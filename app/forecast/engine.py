"""
Forecast Engine — computes price direction forecasts for multiple horizons.

Uses existing MarketDataService bars cache. No additional IB Gateway requests.
Read-only module: never influences trade execution.

All indicator weights set to 1.0 (default). Empirical weight optimization on 407
samples (2026-02-23) led to overfitting: HIGH confidence accuracy dropped 50%→17%.
Gate-based pair filtering is more effective than weight tuning.
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
from app.market_data.indicators import (
    adx as calc_adx,
    bollinger_bands as calc_bb,
    is_squeeze as calc_squeeze,
)

logger = logging.getLogger(__name__)

# Mapping: horizon_minutes → (primary_timeframe, secondary_timeframe, momentum_lookback)
HORIZON_CONFIG = {
    30: {"primary_tf": "M15", "secondary_tf": None, "momentum_lookback": 6, "ma_fast": 20, "ma_slow": 50},
    60: {"primary_tf": "H1", "secondary_tf": "M15", "momentum_lookback": 10, "ma_fast": 20, "ma_slow": 50},
    240: {"primary_tf": "H4", "secondary_tf": "H1", "momentum_lookback": 10, "ma_fast": 20, "ma_slow": 50},
    1440: {"primary_tf": "H4", "secondary_tf": "H1", "momentum_lookback": 24, "ma_fast": 50, "ma_slow": 200},
}

# Indicator weights — all set to 1.0 (default)
# Empirical weight optimization (2026-02-23) proved to be overfitting:
# - Custom weights: accuracy dropped 53% → 35%, HIGH conf 50% → 17%
# - Rollback to 1.0: accuracy recovering to ~48%+ within hours
# Lesson: gate-based pair filtering > weight tuning on small samples
WEIGHTS = {
    "ma_cross_inv": 1.0,
    "price_vs_ma": 1.0,
    "momentum": 1.0,
    "rsi_momentum": 1.0,
    "rsi_trend": 1.0,
    "atr_trend": 1.0,
    "secondary_ma": 1.0,
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

        h30_votes_json = None
        h30_alt_direction = None
        h30_alt_strength = None
        for horizon_minutes in FORECAST_HORIZONS:
            self._last_votes = None
            h = self._compute_horizon(symbol, horizon_minutes, bars_cache, flags)
            horizons.append(h)
            if horizon_minutes == 30 and self._last_votes:
                h30_votes_json = self._last_votes
                # A/B test: compute alternative direction with inverted contrarian weights
                h30_alt_direction, h30_alt_strength = self._compute_alt_scoring(self._last_votes)

        # === Advanced filter metadata (log-only, not blocking) ===

        # ADX on M15 (primary timeframe for H30)
        adx_value = None
        m15 = bars_cache.get("M15", {})
        m15_h = m15.get("highs", [])
        m15_l = m15.get("lows", [])
        m15_c = m15.get("closes", [])
        if len(m15_c) >= 30 and len(m15_h) == len(m15_c) and len(m15_l) == len(m15_c):
            adx_value = calc_adx(m15_h, m15_l, m15_c, period=14)

        # Bollinger Bands width + squeeze on M15
        bb_width = None
        bb_squeeze = None
        if len(m15_c) >= 20:
            bb = calc_bb(m15_c, period=20, std_mult=2.0)
            if bb:
                bb_width = bb["width"]
            if len(m15_h) >= 20 and len(m15_l) >= 20:
                bb_squeeze = calc_squeeze(m15_h, m15_l, m15_c)

        # MTF conflict: H4 direction vs H30 direction
        mtf_conflict = None
        mtf_h4_direction = None
        h30_horizon = next((h for h in horizons if h.horizon_minutes == 30), None)
        h4_data = bars_cache.get("H4", {})
        h4_closes = h4_data.get("closes", [])
        if h30_horizon and h30_horizon.direction.value != "neutral" and len(h4_closes) >= 50:
            # Determine H4 trend via price vs MA(20) + momentum
            from app.forecast.indicators_vote import vote_price_vs_ma, vote_momentum
            h4_trend_ma = vote_price_vs_ma(h4_closes, 20)
            h4_trend_mom = vote_momentum(h4_closes, 10)
            h4_net = h4_trend_ma + h4_trend_mom
            if h4_net > 0:
                mtf_h4_direction = "up"
            elif h4_net < 0:
                mtf_h4_direction = "down"
            else:
                mtf_h4_direction = "neutral"

            # Conflict = H4 strong direction opposite to H30
            if mtf_h4_direction != "neutral" and h30_horizon.direction.value != mtf_h4_direction:
                # Check if H4 has strong ADX to confirm real conflict
                h4_h = h4_data.get("highs", [])
                h4_l = h4_data.get("lows", [])
                h4_adx = None
                if len(h4_h) >= 30 and len(h4_l) >= 30:
                    h4_adx = calc_adx(h4_h, h4_l, h4_closes, period=14)
                mtf_conflict = h4_adx is not None and h4_adx > 25  # Only flag if H4 ADX is strong
            else:
                mtf_conflict = False

        return ForecastResult(
            ts_utc=ts,
            symbol=symbol,
            horizons=horizons,
            data_quality=data_quality,
            flags=flags,
            base_price=base_price,
            adx_value=adx_value,
            bb_width=bb_width,
            bb_squeeze=bb_squeeze,
            mtf_conflict=mtf_conflict,
            mtf_h4_direction=mtf_h4_direction,
            h30_votes_json=h30_votes_json,
            h30_alt_direction=h30_alt_direction,
            h30_alt_strength=h30_alt_strength,
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

        # Collect weighted votes: (vote, weight) + named votes dict for audit
        weighted_votes: List[Tuple[int, float]] = []
        named_votes: Dict[str, int] = {}

        # Vote 1: INVERTED MA Cross (contrarian signal)
        v = vote_ma_cross_inverted(closes, ma_fast_period, ma_slow_period)
        weighted_votes.append((v, WEIGHTS["ma_cross_inv"]))
        named_votes["ma_cross_inv"] = v

        # Vote 2: RSI Trend (standard — decent when it fires)
        v = vote_rsi_trend(closes)
        weighted_votes.append((v, WEIGHTS["rsi_trend"]))
        named_votes["rsi_trend"] = v

        # Vote 3: INVERTED RSI Extreme → RSI Momentum (for short horizons)
        if horizon_minutes <= 240:
            v = vote_rsi_momentum(closes)
            weighted_votes.append((v, WEIGHTS["rsi_momentum"]))
            named_votes["rsi_momentum"] = v

        # Vote 4: Price vs MA (best single predictor)
        v = vote_price_vs_ma(closes, ma_fast_period)
        weighted_votes.append((v, WEIGHTS["price_vs_ma"]))
        named_votes["price_vs_ma"] = v

        # Vote 5: Momentum — STRONGEST predictor, double weight
        v = vote_momentum(closes, momentum_lookback)
        weighted_votes.append((v, WEIGHTS["momentum"]))
        named_votes["momentum"] = v

        # Vote 6: ATR Trend (supplementary, lower weight)
        if len(highs) >= 20 and len(lows) >= 20:
            from app.market_data.indicators import sma as calc_sma
            ma_f = calc_sma(closes, ma_fast_period)
            ma_s = calc_sma(closes, ma_slow_period)
            v = vote_atr_trend(highs, lows, closes, ma_f, ma_s)
            weighted_votes.append((v, WEIGHTS["atr_trend"]))
            named_votes["atr_trend"] = v

        # Vote 7: Secondary TF MA cross (inverted, reduced weight)
        if secondary_tf:
            sec_data = bars_cache.get(secondary_tf, {})
            sec_closes = sec_data.get("closes", [])
            if len(sec_closes) >= ma_slow_period:
                v = vote_ma_cross_inverted(sec_closes, ma_fast_period, ma_slow_period)
                weighted_votes.append((v, WEIGHTS["secondary_ma"]))
                named_votes["secondary_ma"] = v

        # Aggregate with weights
        direction_str, confidence_str, strength, aligned, total = aggregate_weighted_votes(weighted_votes)

        # Store named votes for H30 audit trail
        self._last_votes = named_votes if horizon_minutes == 30 else getattr(self, '_last_votes', None)

        return ForecastHorizon(
            horizon_minutes=horizon_minutes,
            direction=ForecastDirection(direction_str),
            confidence=ForecastConfidence(confidence_str),
            strength=strength,
            indicators_aligned=aligned,
            indicators_total=total,
        )

    # A/B alternative weights: invert contrarians (price_vs_ma, momentum, atr_trend)
    ALT_WEIGHTS = {
        "ma_cross_inv": 1.0,      # KEEP: +23.1% delta (good predictor)
        "rsi_trend": 0.0,         # DROP: weak, often neutral
        "rsi_momentum": 0.0,      # DROP: 14% participation, no data
        "price_vs_ma": -1.0,      # INVERT: -15.2% delta (contrarian)
        "momentum": -1.0,         # INVERT: -8.5% delta (contrarian)
        "atr_trend": -1.0,        # INVERT: -15.2% delta (contrarian)
        "secondary_ma": 0.0,      # Not in H30
    }

    def _compute_alt_scoring(self, votes: Dict[str, int]) -> Tuple[Optional[str], Optional[float]]:
        """Compute alternative direction using inverted contrarian weights (A/B test)."""
        weighted = []
        for ind, vote in votes.items():
            w = self.ALT_WEIGHTS.get(ind, 0.0)
            if w != 0.0 and vote != 0:
                weighted.append((vote, w))
        if not weighted:
            return None, None
        direction, _, strength, _, _ = aggregate_weighted_votes(weighted)
        return direction, strength

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
