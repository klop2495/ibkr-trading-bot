"""
Forecast Engine — computes price direction forecasts for multiple horizons.

Uses existing MarketDataService bars cache. No additional IB Gateway requests.
Read-only module: never influences trade execution.

Indicator weights (based on empirical analysis 2026-02-23):
- momentum: STRONG predictor (85% accuracy), weight=2.0
- price_vs_ma: GOOD predictor (60%), weight=1.0
- ma_cross: CONTRARIAN — inverted, weight=1.0
- rsi_momentum: CONTRARIAN RSI extreme inverted (momentum, not mean-reversion), weight=1.0
- rsi_trend: rarely fires but decent when it does, weight=1.0
- atr_trend: kept for horizons with OHLC data, weight=0.5
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.models.forecast import (
    FORECAST_HORIZONS,
    ForecastConfidence,
    ForecastDirection,
    ForecastHorizon,
    ForecastResult,
)
from app.forecast.indicators_vote import (
    aggregate_votes,
    aggregate_weighted_votes,
    vote_atr_trend,
    vote_ma_cross,
    vote_ma_cross_inverted,
    vote_momentum,
    vote_price_vs_ma,
    vote_rsi_extreme,
    vote_rsi_momentum,
    vote_rsi_trend,
)

logger = logging.getLogger(__name__)

# Mapping: horizon_minutes → (primary_timeframe, secondary_timeframe, momentum_lookback)
HORIZON_CONFIG = {
    30: {"primary_tf": "M15", "secondary_tf": None, "momentum_lookback": 6, "ma_fast": 20, "ma_slow": 50},
    60: {"primary_tf": "H1", "secondary_tf": "M15", "momentum_lookback": 10, "ma_fast": 20, "ma_slow": 50},
    240: {"primary_tf": "H4", "secondary_tf": "H1", "momentum_lookback": 10, "ma_fast": 20, "ma_slow": 50},
    1440: {"primary_tf": "H4", "secondary_tf": "H1", "momentum_lookback": 24, "ma_fast": 50, "ma_slow": 200},
}

# Indicator weights — empirically derived from 407 verified forecasts (2026-02-23)
# ma_cross_inv: 64% accuracy, 100% active rate — best global predictor
# price_vs_ma: 58% accuracy, 100% active — stable second
# momentum: 51% globally, contrarian for JPY pairs (13-21%) — reduced
# rsi_trend: 50%, only 14% active — minimal contribution
# rsi_momentum: 53%, only 16% active — minimal contribution
WEIGHTS = {
    "ma_cross_inv": 2.0,   # BEST: 64% accuracy, always fires, inverted
    "price_vs_ma": 1.0,    # GOOD: 58% accuracy, always fires
    "momentum": 0.5,       # WEAK globally (51%), contrarian for JPY pairs
    "rsi_momentum": 0.5,   # MARGINAL: 53%, rarely fires (16%)
    "rsi_trend": 0.5,      # MARGINAL: 50%, rarely fires (14%)
    "atr_trend": 0.5,      # Supplementary, needs OHLC
    "secondary_ma": 0.5,   # Secondary TF confirmation, inverted
}

# Minimum bars needed for reliable indicator calculation
MIN_BARS_REQUIRED = 50


class ForecastEngine:
    """
    Computes multi-horizon price direction forecasts using weighted indicator voting.

    Usage:
        engine = ForecastEngine()
        forecasts = engine.compute_all(market_data_service, symbols)
    """

    def __init__(self):
        self._warn_logged: Dict[str, bool] = {}

    def compute_all(
        self,
        market_data_service: Any,
        symbols: List[str],
    ) -> List[ForecastResult]:
        """Compute forecasts for all symbols."""
        results: List[ForecastResult] = []
        ts = datetime.now(timezone.utc)

        for symbol in symbols:
            try:
                result = self._compute_symbol(symbol, ts, market_data_service)
                results.append(result)
            except Exception as exc:
                key = f"forecast_error_{symbol}"
                if not self._warn_logged.get(key):
                    logger.warning(f"forecast_error symbol={symbol} error={exc}")
                    self._warn_logged[key] = True
                results.append(self._empty_forecast(symbol, ts, flags=[f"ERROR:{type(exc).__name__}"]))

        return results

    def _compute_symbol(
        self,
        symbol: str,
        ts: datetime,
        mds: Any,
    ) -> ForecastResult:
        """Compute forecast for a single symbol across all horizons."""
        horizons: List[ForecastHorizon] = []
        flags: List[str] = []
        data_quality = "ok"

        # Collect bars from cache for all timeframes
        bars_cache: Dict[str, Dict[str, List[float]]] = {}
        for tf in ("M15", "H1", "H4"):
            ohlc = mds.get_ohlc(symbol, tf, n_bars=250)
            if ohlc and ohlc.get("closes"):
                bars_cache[tf] = ohlc
            else:
                bars_cache[tf] = {"opens": [], "highs": [], "lows": [], "closes": []}

        # Check data availability
        available_tfs = [tf for tf, d in bars_cache.items() if len(d.get("closes", [])) >= MIN_BARS_REQUIRED]
        if not available_tfs:
            flags.append("INSUFFICIENT_DATA")
            data_quality = "unknown"
            return self._empty_forecast(symbol, ts, data_quality=data_quality, flags=flags)

        # Capture base price (latest close from primary timeframe)
        base_price: Optional[float] = None
        for tf in ("M15", "H1", "H4"):
            closes = bars_cache.get(tf, {}).get("closes", [])
            if closes:
                base_price = closes[-1]
                break

        for horizon_minutes in FORECAST_HORIZONS:
            h = self._compute_horizon(symbol, horizon_minutes, bars_cache, flags)
            horizons.append(h)

        return ForecastResult(
            ts_utc=ts,
            symbol=symbol,
            horizons=horizons,
            data_quality=data_quality,
            flags=flags,
            base_price=base_price,
        )

    def _compute_horizon(
        self,
        symbol: str,
        horizon_minutes: int,
        bars_cache: Dict[str, Dict[str, List[float]]],
        flags: List[str],
    ) -> ForecastHorizon:
        """Compute a single-horizon forecast using weighted indicator voting."""
        config = HORIZON_CONFIG[horizon_minutes]
        primary_tf = config["primary_tf"]
        secondary_tf = config["secondary_tf"]
        momentum_lookback = config["momentum_lookback"]
        ma_fast_period = config["ma_fast"]
        ma_slow_period = config["ma_slow"]

        # Get primary data
        primary = bars_cache.get(primary_tf, {})
        closes = primary.get("closes", [])
        highs = primary.get("highs", [])
        lows = primary.get("lows", [])

        if len(closes) < MIN_BARS_REQUIRED:
            # Try secondary timeframe
            if secondary_tf:
                secondary = bars_cache.get(secondary_tf, {})
                closes = secondary.get("closes", [])
                highs = secondary.get("highs", [])
                lows = secondary.get("lows", [])

        if len(closes) < 15:
            flags.append(f"NO_DATA_{primary_tf}_H{horizon_minutes}")
            return ForecastHorizon(
                horizon_minutes=horizon_minutes,
                direction=ForecastDirection.NEUTRAL,
                confidence=ForecastConfidence.LOW,
                strength=0.0,
                indicators_aligned=0,
                indicators_total=0,
            )

        # Collect weighted votes: (vote, weight)
        weighted_votes: List[Tuple[int, float]] = []

        # Vote 1: INVERTED MA Cross (contrarian signal)
        weighted_votes.append((
            vote_ma_cross_inverted(closes, ma_fast_period, ma_slow_period),
            WEIGHTS["ma_cross_inv"],
        ))

        # Vote 2: RSI Trend (standard — decent when it fires)
        weighted_votes.append((
            vote_rsi_trend(closes),
            WEIGHTS["rsi_trend"],
        ))

        # Vote 3: INVERTED RSI Extreme → RSI Momentum (for short horizons)
        if horizon_minutes <= 240:
            weighted_votes.append((
                vote_rsi_momentum(closes),
                WEIGHTS["rsi_momentum"],
            ))

        # Vote 4: Price vs MA (best single predictor)
        weighted_votes.append((
            vote_price_vs_ma(closes, ma_fast_period),
            WEIGHTS["price_vs_ma"],
        ))

        # Vote 5: Momentum — STRONGEST predictor, double weight
        weighted_votes.append((
            vote_momentum(closes, momentum_lookback),
            WEIGHTS["momentum"],
        ))

        # Vote 6: ATR Trend (supplementary, lower weight)
        if len(highs) >= 20 and len(lows) >= 20:
            from app.market_data.indicators import sma as calc_sma
            ma_f = calc_sma(closes, ma_fast_period)
            ma_s = calc_sma(closes, ma_slow_period)
            weighted_votes.append((
                vote_atr_trend(highs, lows, closes, ma_f, ma_s),
                WEIGHTS["atr_trend"],
            ))

        # Vote 7: Secondary TF MA cross (inverted, reduced weight)
        if secondary_tf:
            sec_data = bars_cache.get(secondary_tf, {})
            sec_closes = sec_data.get("closes", [])
            if len(sec_closes) >= ma_slow_period:
                weighted_votes.append((
                    vote_ma_cross_inverted(sec_closes, ma_fast_period, ma_slow_period),
                    WEIGHTS["secondary_ma"],
                ))

        # Aggregate with weights
        direction_str, confidence_str, strength, aligned, total = aggregate_weighted_votes(weighted_votes)

        return ForecastHorizon(
            horizon_minutes=horizon_minutes,
            direction=ForecastDirection(direction_str),
            confidence=ForecastConfidence(confidence_str),
            strength=strength,
            indicators_aligned=aligned,
            indicators_total=total,
        )

    @staticmethod
    def _empty_forecast(
        symbol: str,
        ts: datetime,
        data_quality: str = "unknown",
        flags: Optional[List[str]] = None,
    ) -> ForecastResult:
        """Generate a neutral forecast when data is unavailable."""
        return ForecastResult(
            ts_utc=ts,
            symbol=symbol,
            horizons=[
                ForecastHorizon(
                    horizon_minutes=h,
                    direction=ForecastDirection.NEUTRAL,
                    confidence=ForecastConfidence.LOW,
                    strength=0.0,
                    indicators_aligned=0,
                    indicators_total=0,
                )
                for h in FORECAST_HORIZONS
            ],
            data_quality=data_quality,
            flags=flags or [],
        )
