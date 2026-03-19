"""
Forecast Engine — computes price direction forecasts for multiple horizons.

Uses existing MarketDataService bars cache. No additional IB Gateway requests.
Read-only module: never influences trade execution.

All indicator weights set to 1.0 (default). Empirical weight optimization on 407
samples (2026-02-23) led to overfitting: HIGH confidence accuracy dropped 50%→17%.
Gate-based pair filtering is more effective than weight tuning.
"""

import logging
import os
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
    aggregate_weighted_votes,
    vote_atr_trend,
    vote_ma_cross_inverted,
    vote_momentum,
    vote_price_vs_ma,
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
        # Alt4 config
        self._alt4_original_hours = self._parse_hour_set(
            os.getenv("ALT4_ORIGINAL_HOURS", "9,10,19"),
            default={9, 10, 19},
        )
        self._alt4_inverted_hours = self._parse_hour_set(
            os.getenv("ALT4_INVERTED_HOURS", "15,20,21,22"),
            default={15, 20, 21, 22},
        )
        self._alt4_exclude_symbols = self._parse_csv_set(
            os.getenv("ALT4_EXCLUDE_SYMBOLS", "CADJPY,EURJPY,CHFJPY,GBPJPY,EURGBP,EURCHF"),
            upper=True,
        )
        self._alt4_eligible_confidence = self._parse_csv_set(
            os.getenv("ALT4_ELIGIBLE_CONFIDENCE", "medium,high"),
            lower=True,
        )
        self._alt4_min_adx = self._parse_float(os.getenv("ALT4_MIN_ADX", "0"), 0.0)

        # Alt5 config: ANTI-h30 strategy (inverts h30_direction)
        self._alt5_enabled = os.getenv("ALT5_ENABLED", "1") == "1"
        self._alt5_hours_utc = self._parse_hour_set_or_none(
            os.getenv("ALT5_HOURS_UTC", "") or os.getenv("ALT5_ALLOWED_HOURS", "")
        )
        self._alt5_min_adx = self._parse_float(os.getenv("ALT5_MIN_ADX", "20"), 20.0)
        self._alt5_exclude_symbols = self._parse_csv_set(
            os.getenv("ALT5_EXCLUDE_SYMBOLS", "USDCAD,CHFJPY"),
            upper=True,
        )

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
                mtf_conflict = h4_adx is not None and h4_adx > 25
            else:
                mtf_conflict = False

        # Alt2 and Alt3(legacy) are deprecated and intentionally disabled.
        _alt2_dir = None
        _alt3_dir = None
        _alt3v2_dir, _alt3v2_eligible, _alt3v2_score, _alt3v2_meta = self._compute_alt3v2_signal(
            symbol, h30_votes_json, adx_value
        )
        
        # Alt3 v3: Smart ANTI strategy
        # Rule: If h30_direction == mtf_h4_direction AND aligned <= 3 → trade AGAINST
        # Exception: EURUSD → trade WITH direction (74% accuracy)
        # Backtest: 78.2% accuracy on 444 samples (14 days)
        _alt3_dir, _alt3_eligible = self._compute_alt3_signal_v3(
            symbol=symbol,
            h30_direction=h30_horizon.direction.value if h30_horizon else None,
            h30_aligned=h30_horizon.indicators_aligned if h30_horizon else 0,
            mtf_h4_direction=mtf_h4_direction,
            bb_width=bb_width,
        )
        _h30_direction = h30_horizon.direction.value if h30_horizon else None
        _h30_confidence = h30_horizon.confidence.value if h30_horizon else None
        _alt4_dir, _alt4_mode, _alt4_eligible = self._compute_alt4_signal(
            symbol=symbol,
            h30_direction=_h30_direction,
            h30_confidence=_h30_confidence,
            adx_value=adx_value,
            ts_utc=ts,
        )

        # Alt5: ANTI-h30 strategy (inverts h30_direction with filters)
        _alt5_dir, _alt5_eligible = self._compute_alt5_signal(
            symbol=symbol,
            h30_direction=_h30_direction,
            adx_value=adx_value,
            ts_utc=ts,
        )

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
            h30_alt2_direction=_alt2_dir,
            h30_alt2_trade_eligible=None,
            # Alt3: Smart ANTI strategy (v3) - replaces legacy Alt3
            h30_alt3_direction=_alt3_dir,
            h30_alt3_trade_eligible=_alt3_eligible,
            # Alt3-v2: legacy variant (kept for comparison)
            h30_alt3v2_direction=_alt3v2_dir,
            h30_alt3v2_trade_eligible=_alt3v2_eligible,
            h30_alt3v2_score=_alt3v2_score,
            h30_alt3v2_meta_json=_alt3v2_meta,
            h30_alt4_direction=_alt4_dir,
            h30_alt4_mode=_alt4_mode,
            h30_alt4_trade_eligible=_alt4_eligible,
            # Alt5: ANTI-h30 direction
            h30_alt5_direction=_alt5_dir,
            h30_alt5_trade_eligible=_alt5_eligible,
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
        "ma_cross_inv": 1.0,
        "rsi_trend": 0.0,
        "rsi_momentum": 0.0,
        "price_vs_ma": -1.0,
        "momentum": -1.0,
        "atr_trend": -1.0,
        "secondary_ma": 0.0,
    }

    # A/B alt2: top 16 symbols where signal is valid
    ALT2_SYMBOLS = {
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
        "AUDUSD", "USDCAD", "NZDUSD", "EURJPY",
        "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY",
        "NZDJPY", "EURGBP", "EURAUD", "EURCHF",
    }

    def _compute_alt2_signal(self, symbol: str, votes: Optional[Dict[str, int]], adx_value: Optional[float]) -> Optional[str]:
        """A/B test 2: ma_cross_inv direction when ma!=pv + ADX>=30 + top8."""
        if not votes or symbol not in self.ALT2_SYMBOLS:
            return None
        ma = votes.get("ma_cross_inv", 0)
        pv = votes.get("price_vs_ma", 0)
        if ma == 0 or pv == 0 or ma == pv:
            return None
        if adx_value is None or adx_value < 30:
            return None
        return "up" if ma == 1 else "down"

    def _compute_alt3_signal(self, symbol: str, votes: Optional[Dict[str, int]], adx_value: Optional[float]) -> Optional[str]:
        """A/B test 3: ma!=pv + ADX>=30 + momentum agrees with ma + top8."""
        if not votes or symbol not in self.ALT2_SYMBOLS:
            return None
        ma = votes.get("ma_cross_inv", 0)
        pv = votes.get("price_vs_ma", 0)
        mom = votes.get("momentum", 0)
        if ma == 0 or pv == 0 or ma == pv:
            return None
        if adx_value is None or adx_value < 30:
            return None
        if mom != ma:
            return None
        return "up" if ma == 1 else "down"

    def _compute_alt3v2_signal(
        self,
        symbol: str,
        votes: Optional[Dict[str, int]],
        adx_value: Optional[float],
    ) -> Tuple[Optional[str], Optional[bool], Optional[float], Optional[Dict[str, float]]]:
        """Independent Alt3-v2 signal."""
        if not votes or symbol not in self.ALT2_SYMBOLS:
            return None, None, None, None
        if adx_value is None or adx_value < 30:
            return None, None, None, None

        ma = int(votes.get("ma_cross_inv", 0) or 0)
        pv = int(votes.get("price_vs_ma", 0) or 0)
        mom = int(votes.get("momentum", 0) or 0)
        rsi = int(votes.get("rsi_trend", 0) or 0)
        atr = int(votes.get("atr_trend", 0) or 0)
        if ma == 0 or pv == 0 or mom == 0:
            return None, None, None, None

        pv_inv = -pv
        score = (
            1.0 * ma +
            0.9 * mom +
            0.8 * pv_inv +
            0.35 * rsi +
            0.25 * atr
        )
        if abs(score) < 1.2:
            return None, None, score, {
                "ma": float(ma),
                "momentum": float(mom),
                "pv_inv": float(pv_inv),
                "rsi_trend": float(rsi),
                "atr_trend": float(atr),
                "score": round(score, 4),
            }

        direction = "up" if score > 0 else "down"
        dir_sign = 1 if direction == "up" else -1
        core_match = sum(1 for x in (ma, mom, pv_inv) if x == dir_sign)
        trade_eligible = bool(core_match >= 2 and abs(score) >= 2.1)
        meta = {
            "ma": float(ma),
            "momentum": float(mom),
            "pv_inv": float(pv_inv),
            "rsi_trend": float(rsi),
            "atr_trend": float(atr),
            "score": round(score, 4),
            "core_match": float(core_match),
        }
        return direction, trade_eligible, score, meta

    def _compute_alt4_signal(
        self,
        symbol: str,
        h30_direction: Optional[str],
        h30_confidence: Optional[str],
        ts_utc: datetime,
        adx_value: Optional[float] = None,
    ) -> Tuple[Optional[str], Optional[str], Optional[bool]]:
        """Alt4 hybrid signal: hour-based original/inverted direction + eligibility."""
        if h30_direction is None or h30_direction == "neutral":
            return None, None, None
        hour = ts_utc.hour
        if hour in self._alt4_original_hours:
            alt4_direction = h30_direction
            mode = "original"
        elif hour in self._alt4_inverted_hours:
            alt4_direction = "down" if h30_direction == "up" else "up"
            mode = "inverted"
        else:
            return None, None, None
        eligible = True
        if symbol.upper() in self._alt4_exclude_symbols:
            eligible = False
        if (h30_confidence or "").lower() not in self._alt4_eligible_confidence:
            eligible = False
        if self._alt4_min_adx > 0:
            if adx_value is None or adx_value < self._alt4_min_adx:
                eligible = False
        return alt4_direction, mode, eligible

    def _compute_alt3_signal_v3(
        self,
        symbol: str,
        h30_direction: Optional[str],
        h30_aligned: int,
        mtf_h4_direction: Optional[str],
        bb_width: Optional[float],
    ) -> Tuple[Optional[str], Optional[bool]]:
        """Alt3 v3: Smart ANTI strategy — 78.2% accuracy on 444 samples.
        
        Replaces legacy Alt3 with market-condition based logic:
        - If h30_direction == mtf_h4_direction AND aligned <= 3 → trade AGAINST h30
        - Exception: EURUSD trades WITH h30_direction (74% accuracy)
        - Filter: bb_width >= 0.002 for sufficient volatility
        
        Returns (direction, eligible):
        - direction: trading direction (inverted for most pairs, original for EURUSD)
        - eligible: True if all conditions met
        """
        # Need valid directions
        if h30_direction is None or h30_direction == "neutral":
            return None, None
        if mtf_h4_direction is None or mtf_h4_direction == "neutral":
            return None, None
        
        # Volatility filter
        if bb_width is None or bb_width < 0.002:
            return None, None
        
        # Core condition: h30 matches H4 AND weak alignment
        if h30_direction != mtf_h4_direction:
            return None, None
        if h30_aligned > 3:
            return None, None
        
        # EURUSD exception: trade WITH direction (74% accuracy)
        if symbol.upper() == "EURUSD":
            return h30_direction, True
        
        # All other pairs: trade AGAINST direction (78% accuracy)
        anti_direction = "down" if h30_direction == "up" else "up"
        return anti_direction, True

    def _compute_alt5_signal(
        self,
        symbol: str,
        h30_direction: Optional[str],
        adx_value: Optional[float],
        ts_utc: datetime,
    ) -> Tuple[Optional[str], Optional[bool]]:
        """Alt5: ANTI-h30 strategy — inverts h30_direction with hour + ADX filters.

        This is independent of Alt3-v2. It simply inverts h30_direction when:
        - ALT5_ENABLED=1
        - Current hour is in ALT5_HOURS_UTC (if set)
        - ADX >= ALT5_MIN_ADX (default 20)
        - Symbol not in ALT5_EXCLUDE_SYMBOLS

        Returns (direction, eligible):
        - direction: inverted h30_direction or None
        - eligible: True if all filters pass
        """
        # Check if Alt5 is enabled
        if not self._alt5_enabled:
            return None, None

        # Need a valid h30_direction to invert
        if h30_direction is None or h30_direction == "neutral":
            return None, None

        # Hour filter (if set)
        hour = ts_utc.hour
        if self._alt5_hours_utc is not None and hour not in self._alt5_hours_utc:
            return None, None

        # ADX filter
        if self._alt5_min_adx > 0:
            if adx_value is None or adx_value < self._alt5_min_adx:
                return None, None

        # Symbol exclusion
        if symbol.upper() in self._alt5_exclude_symbols:
            return None, None

        # Invert h30_direction
        alt5_direction = "down" if h30_direction == "up" else "up"

        # All filters passed = eligible
        return alt5_direction, True

    @staticmethod
    def _compute_alt2_trade_eligible(votes: Optional[Dict[str, int]], alt2_direction: Optional[str]) -> Optional[bool]:
        """Soft execution filter for Alt2."""
        if alt2_direction is None:
            return None
        if not votes:
            return False
        ma = votes.get("ma_cross_inv", 0)
        mom = votes.get("momentum", 0)
        if ma == 0 or mom == 0:
            return False
        return bool(mom == ma)

    @staticmethod
    def _parse_hour_set(raw: str, default: set[int]) -> set[int]:
        vals: set[int] = set()
        for part in (raw or "").split(","):
            p = part.strip()
            if not p:
                continue
            try:
                h = int(p)
                if 0 <= h <= 23:
                    vals.add(h)
            except Exception:
                continue
        return vals or set(default)

    @staticmethod
    def _parse_csv_set(raw: str, *, upper: bool = False, lower: bool = False) -> set[str]:
        vals: set[str] = set()
        for part in (raw or "").split(","):
            p = part.strip()
            if not p:
                continue
            if upper:
                p = p.upper()
            if lower:
                p = p.lower()
            vals.add(p)
        return vals

    @staticmethod
    def _parse_hour_set_or_none(raw: str) -> Optional[set[int]]:
        vals: set[int] = set()
        for part in (raw or "").split(","):
            p = part.strip()
            if not p:
                continue
            try:
                h = int(p)
                if 0 <= h <= 23:
                    vals.add(h)
            except Exception:
                continue
        return vals or None

    @staticmethod
    def _parse_float(raw: str, default: float) -> float:
        try:
            return float(raw)
        except Exception:
            return default

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
