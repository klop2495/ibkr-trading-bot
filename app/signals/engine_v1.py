from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from app.models.signal_preview import (
    Confidence,
    DataQuality,
    Direction,
    SetupType,
    SignalPreviewV1,
    SpreadQuality,
)
from app.models.signals_params import SignalsParams
from app.models.snapshot import MarketSnapshot
from app.signals.structure_v2 import build_continuation_setup

# Flag constants (stable keys)
FLAG_DATA_WARMUP_NOT_READY = "DATA_WARMUP_NOT_READY"
FLAG_DATA_GAP = "DATA_GAP"
FLAG_DATA_DUP = "DATA_DUP"
FLAG_DATA_STALE = "DATA_STALE"
FLAG_SPREAD_WIDE = "SPREAD_WIDE"
FLAG_SPREAD_UNKNOWN = "SPREAD_UNKNOWN"
FLAG_REGIME_H4_NEUTRAL = "REGIME_H4_NEUTRAL"
FLAG_REGIME_UNKNOWN = "REGIME_UNKNOWN"
FLAG_TF_MISMATCH_H4_H1 = "TF_MISMATCH_H4_H1"
FLAG_SETUP_NOT_FOUND = "SETUP_NOT_FOUND"
FLAG_SETUP_INVALIDATED = "SETUP_INVALIDATED"
FLAG_ENTRY_NOT_TRIGGERED = "ENTRY_NOT_TRIGGERED"
FLAG_SWING_NOT_DETECTED = "SWING_NOT_DETECTED"
FLAG_STRUCTURAL_SL_TP = "STRUCTURAL_SL_TP"
FLAG_REGIME_SCORE_WEAK = "REGIME_SCORE_WEAK"
FLAG_IMPULSE_SCORE_WEAK = "IMPULSE_SCORE_WEAK"
FLAG_PULLBACK_SCORE_WEAK = "PULLBACK_SCORE_WEAK"
FLAG_TRIGGER_SCORE_WEAK = "TRIGGER_SCORE_WEAK"
FLAG_CONTINUATION_SCORE_LOW = "CONTINUATION_SCORE_LOW"
FLAG_LEGACY_CONFIRM_FALLBACK = "LEGACY_CONFIRM_FALLBACK"
FLAG_FALLBACK_SL_FROM_SETTINGS = "FALLBACK_SL_FROM_SETTINGS"
FLAG_FALLBACK_TP_FROM_SETTINGS = "FALLBACK_TP_FROM_SETTINGS"
FLAG_SIGNALS_RULES_NOT_SPECIFIED = "SIGNALS_RULES_NOT_SPECIFIED"
FLAG_SIGNALS_PARAMS_INVALID = "SIGNALS_PARAMS_INVALID"


class SignalEngineV1:
    def __init__(self, params: SignalsParams):
        self.params = params

    def _base_preview(
        self,
        symbol: str,
        ts: datetime,
        setup: SetupType,
        direction: Direction,
        data_quality: DataQuality,
        spread_quality: SpreadQuality,
        flags: List[str],
        confidence: Confidence = Confidence.NORMAL,
    ) -> SignalPreviewV1:
        rr = self.params.rr.high_conf_rr if confidence == Confidence.HIGH else self.params.rr.base_rr or 0.0
        return SignalPreviewV1(
            ts_utc=ts,
            symbol=symbol,
            setup_type=setup,
            direction=direction,
            setup_present=False,
            entry_triggered=False,
            confidence=confidence,
            rr=rr,
            data_quality=data_quality,
            spread_quality=spread_quality,
            flags=flags,
        )

    def _ensure_ts(self, ts: datetime | None) -> datetime:
        if ts is None:
            ts = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts

    @staticmethod
    def _pip_size(symbol: str) -> float:
        if symbol and symbol.upper().endswith("JPY"):
            return 0.01
        return 0.0001

    def _direction_from_ma(self, snap: MarketSnapshot | None) -> Direction:
        if snap is None:
            return Direction.FLAT
        if snap.ma_fast > snap.ma_slow:
            return Direction.LONG
        if snap.ma_fast < snap.ma_slow:
            return Direction.SHORT
        return Direction.FLAT

    def _data_quality_from_snap(self, snap: MarketSnapshot | None) -> DataQuality:
        if snap is None:
            return DataQuality.UNKNOWN
        val = getattr(snap, "data_quality", None)
        if isinstance(val, str):
            lowered = val.lower()
            if lowered in DataQuality._value2member_map_:
                return DataQuality(lowered)  # type: ignore[arg-type]
        if isinstance(val, DataQuality):
            return val
        return DataQuality.OK

    def _spread_quality_from_snap(self, snap: MarketSnapshot | None) -> SpreadQuality:
        if snap is None:
            return SpreadQuality.UNKNOWN
        wide_threshold = self.params.spread.wide_spread_pips
        spread_val = getattr(snap, "spread", None)
        if spread_val is None:
            return SpreadQuality.UNKNOWN
        if wide_threshold is None:
            return SpreadQuality.UNKNOWN
        return SpreadQuality.WIDE if spread_val > wide_threshold else SpreadQuality.OK

    def _m15_confirm(self, symbol: str, snap_m15: MarketSnapshot | None, direction: Direction) -> bool:
        if snap_m15 is None:
            return False
        rsi_val = getattr(snap_m15, "rsi", None)
        close_val = getattr(snap_m15, "close", None)
        sma_val = getattr(snap_m15, "ma_fast", None)
        if rsi_val is None or close_val is None or sma_val is None:
            return False
        if close_val <= 0 or sma_val <= 0:
            return False

        atr_val = getattr(snap_m15, "atr", None)
        atr_mult = self.params.m15_confirm.pullback_atr_mult
        band = None
        if atr_val is not None and atr_val > 0 and atr_mult is not None:
            band = atr_val * atr_mult
        if band is None:
            max_pips = self.params.m15_confirm.pullback_max_pips
            if max_pips is not None:
                band = max_pips * self._pip_size(symbol)

        near_ma = False
        if band is not None and band > 0:
            near_ma = abs(close_val - sma_val) <= band
        else:
            near_ma = close_val >= sma_val if direction == Direction.LONG else close_val <= sma_val

        rsi_overbought = self.params.m15_confirm.rsi_overbought or 70.0
        rsi_oversold = self.params.m15_confirm.rsi_oversold or 30.0

        if direction == Direction.LONG:
            rsi_min = self.params.m15_confirm.rsi_long_min or 0
            return rsi_val >= rsi_min and rsi_val <= rsi_overbought and near_ma
        if direction == Direction.SHORT:
            rsi_max = self.params.m15_confirm.rsi_short_max or 100
            return rsi_val <= rsi_max and rsi_val >= rsi_oversold and near_ma
        return False

    def _compute_confidence(
        self,
        continuation_score: float,
        m15_confirm: bool,
    ) -> Confidence:
        if continuation_score <= 0:
            return Confidence.LOW
        if continuation_score >= self.params.scoring.high_confidence_score and m15_confirm:
            return Confidence.HIGH
        if continuation_score >= self.params.scoring.min_entry_score:
            return Confidence.NORMAL
        return Confidence.LOW

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _score_regime(self, direction: Direction, snap_h4: MarketSnapshot | None, snap_h1: MarketSnapshot | None) -> float:
        if direction == Direction.FLAT or snap_h4 is None or snap_h1 is None:
            return 0.0

        score = 0.0
        cap = self.params.scoring.regime_separation_atr_cap
        for snap in (snap_h4, snap_h1):
            atr = getattr(snap, "atr", 0.0) or 0.0
            ma_fast = getattr(snap, "ma_fast", 0.0) or 0.0
            ma_slow = getattr(snap, "ma_slow", 0.0) or 0.0
            close = getattr(snap, "close", 0.0) or 0.0
            pip_value = self._pip_size(getattr(snap, "symbol", ""))
            separation = abs(ma_fast - ma_slow)
            separation_pips = separation / pip_value if pip_value > 0 else 0.0
            score += self._clamp(separation_pips / 20.0, 0.0, 1.0) * 5.0
            if atr > 0:
                separation_ratio = separation / atr
                score += self._clamp(separation_ratio / cap, 0.0, 1.0) * 3.0
            if direction == Direction.LONG and close >= ma_fast:
                score += 4.5
            elif direction == Direction.SHORT and close <= ma_fast:
                score += 4.5
        return round(min(score, 25.0), 1)

    def _structural_bars_from_ohlc(self, ohlc: Optional[dict]) -> list[dict]:
        if not ohlc:
            return []
        opens = ohlc.get("opens") or []
        highs = ohlc.get("highs") or []
        lows = ohlc.get("lows") or []
        closes = ohlc.get("closes") or []
        count = min(len(opens), len(highs), len(lows), len(closes))
        bars: list[dict] = []
        for i in range(count):
            bars.append(
                {
                    "index": i,
                    "open": opens[i],
                    "high": highs[i],
                    "low": lows[i],
                    "close": closes[i],
                }
            )
        return bars

    def _compute_structural_setup(
        self,
        *,
        symbol: str,
        direction: Direction,
        ohlc_m15: Optional[dict],
        rr: float,
    ):
        bars = self._structural_bars_from_ohlc(ohlc_m15)
        if not bars:
            return None
        structure = self.params.structure
        lookback = structure.swing_lookback_bars or 0
        min_separation = structure.swing_min_separation_bars or 0
        min_sl = structure.min_sl_pips or 0.0
        max_sl = structure.max_sl_pips or 0.0
        if lookback < 1 or min_separation < 1 or min_sl <= 0 or max_sl <= 0:
            return None
        return build_continuation_setup(
            symbol=symbol,
            bars=bars,
            direction="long" if direction == Direction.LONG else "short",
            lookback=lookback,
            min_separation=min_separation,
            sl_buffer_pips=structure.sl_buffer_pips or 0.0,
            min_sl_pips=min_sl,
            max_sl_pips=max_sl,
            rr=rr,
        )

    def _within_trade_hours(self, ts: datetime) -> bool:
        if not self.params.filters.trade_hours_utc:
            return True
        current = ts.time()
        for window in self.params.filters.trade_hours_utc:
            if not isinstance(window, str) or "-" not in window:
                continue
            start_str, end_str = window.split("-", 1)
            try:
                start_hour, start_min = [int(x) for x in start_str.split(":")]
                end_hour, end_min = [int(x) for x in end_str.split(":")]
                start_time = datetime(ts.year, ts.month, ts.day, start_hour, start_min, tzinfo=ts.tzinfo).time()
                end_time = datetime(ts.year, ts.month, ts.day, end_hour, end_min, tzinfo=ts.tzinfo).time()
            except Exception:
                continue
            if start_time <= current <= end_time:
                return True
        return False

    def compute_preview_for_symbol(
        self,
        symbol: str,
        snapshots: Dict[str, MarketSnapshot],
        warmup_ready: bool = True,
        ohlc_by_timeframe: Optional[Dict[str, dict]] = None,
    ) -> SignalPreviewV1:
        ts = self._ensure_ts(next(iter(snapshots.values())).timestamp if snapshots else None)

        if not self.params.is_configured():
            return SignalPreviewV1(
                ts_utc=ts,
                symbol=symbol,
                setup_type=SetupType.NO_TRADE,
                direction=Direction.FLAT,
                setup_present=False,
                entry_triggered=False,
                confidence=Confidence.LOW,
                rr=self.params.rr.base_rr or 0.0,
                data_quality=DataQuality.UNKNOWN,
                spread_quality=SpreadQuality.UNKNOWN,
                flags=[FLAG_SIGNALS_RULES_NOT_SPECIFIED],
            )

        snap_h4 = snapshots.get("H4")
        snap_h1 = snapshots.get("H1")
        snap_m15 = snapshots.get("M15")

        data_quality = self._data_quality_from_snap(snap_m15 or snap_h1 or snap_h4)
        spread_quality = self._spread_quality_from_snap(snap_m15 or snap_h1 or snap_h4)

        # Global gates
        if self.params.gates.require_warmup and not warmup_ready:
            return self._base_preview(
                symbol,
                ts,
                SetupType.NO_TRADE,
                Direction.FLAT,
                DataQuality.UNKNOWN,
                SpreadQuality.UNKNOWN,
                [FLAG_DATA_WARMUP_NOT_READY],
                confidence=Confidence.LOW,
            )

        if not self._within_trade_hours(ts):
            return self._base_preview(
                symbol,
                ts,
                SetupType.NO_TRADE,
                Direction.FLAT,
                data_quality,
                spread_quality,
                [FLAG_SETUP_NOT_FOUND],
                confidence=Confidence.LOW,
            )

        if data_quality in (DataQuality.GAP, DataQuality.DUP, DataQuality.STALE, DataQuality.UNKNOWN) and self.params.gates.require_data_ok:
            flags = []
            if data_quality == DataQuality.GAP:
                flags.append(FLAG_DATA_GAP)
            elif data_quality == DataQuality.DUP:
                flags.append(FLAG_DATA_DUP)
            elif data_quality == DataQuality.STALE:
                flags.append(FLAG_DATA_STALE)
            else:
                flags.append(FLAG_SIGNALS_PARAMS_INVALID)
            return self._base_preview(
                symbol,
                ts,
                SetupType.NO_TRADE,
                Direction.FLAT,
                data_quality,
                spread_quality,
                flags,
                confidence=Confidence.LOW,
            )

        if spread_quality == SpreadQuality.WIDE and self.params.gates.require_spread_ok:
            return self._base_preview(
                symbol,
                ts,
                SetupType.NO_TRADE,
                Direction.FLAT,
                data_quality,
                spread_quality,
                [FLAG_SPREAD_WIDE],
                confidence=Confidence.LOW,
            )
        if spread_quality == SpreadQuality.UNKNOWN and self.params.gates.require_spread_ok:
            return self._base_preview(
                symbol,
                ts,
                SetupType.NO_TRADE,
                Direction.FLAT,
                data_quality,
                spread_quality,
                [FLAG_SPREAD_UNKNOWN],
                confidence=Confidence.LOW,
            )

        dir_h4 = self._direction_from_ma(snap_h4)
        dir_h1 = self._direction_from_ma(snap_h1)
        if self.params.gates.disallow_h4_neutral and dir_h4 == Direction.FLAT:
            return self._base_preview(
                symbol,
                ts,
                SetupType.NO_TRADE,
                Direction.FLAT,
                data_quality,
                spread_quality,
                [FLAG_REGIME_H4_NEUTRAL],
                confidence=Confidence.LOW,
            )

        flags: List[str] = []
        setup_type = SetupType.NO_TRADE
        setup_present = False
        entry_triggered = False

        if dir_h4 == Direction.FLAT or dir_h1 == Direction.FLAT:
            flags.append(FLAG_SWING_NOT_DETECTED)
            return SignalPreviewV1(
                ts_utc=ts,
                symbol=symbol,
                setup_type=SetupType.NO_TRADE,
                direction=Direction.FLAT,
                setup_present=False,
                entry_triggered=False,
                confidence=Confidence.LOW,
                rr=self.params.rr.base_rr or 0.0,
                data_quality=data_quality,
                spread_quality=spread_quality,
                flags=flags,
            )

        if dir_h4 != dir_h1:
            flags.append(FLAG_TF_MISMATCH_H4_H1)
            return SignalPreviewV1(
                ts_utc=ts,
                symbol=symbol,
                setup_type=SetupType.NO_TRADE,
                direction=Direction.FLAT,
                setup_present=False,
                entry_triggered=False,
                confidence=Confidence.LOW,
                rr=self.params.rr.base_rr or 0.0,
                data_quality=data_quality,
                spread_quality=spread_quality,
                flags=flags,
            )

        direction = dir_h4
        regime_score = self._score_regime(direction, snap_h4, snap_h1)
        if regime_score < self.params.scoring.min_regime_score:
            flags.append(FLAG_REGIME_SCORE_WEAK)
            return SignalPreviewV1(
                ts_utc=ts,
                symbol=symbol,
                setup_type=SetupType.NO_TRADE,
                direction=Direction.FLAT,
                setup_present=False,
                entry_triggered=False,
                confidence=Confidence.LOW,
                rr=self.params.rr.base_rr or 0.0,
                data_quality=data_quality,
                spread_quality=spread_quality,
                flags=flags,
            )

        confidence = Confidence.NORMAL
        rr = self.params.rr.base_rr or 0.0
        structural = self._compute_structural_setup(
            symbol=symbol,
            direction=dir_h4,
            ohlc_m15=(ohlc_by_timeframe or {}).get("M15"),
            rr=rr,
        )

        if structural is not None:
            setup_type = SetupType.SWING_CONTINUATION
            setup_present = structural.setup_present
            entry_triggered = structural.entry_triggered
            if structural.invalidated:
                flags.append(FLAG_SETUP_INVALIDATED)
            elif not structural.setup_present:
                if structural.reason in ("insufficient_bars", "insufficient_swings", "trend_structure_not_found"):
                    flags.append(FLAG_SWING_NOT_DETECTED)
                elif structural.reason == "pullback_out_of_range":
                    flags.append(FLAG_SETUP_NOT_FOUND)
                elif structural.reason == "sl_out_of_range":
                    flags.append(FLAG_SETUP_INVALIDATED)
            elif not structural.entry_triggered:
                flags.append(FLAG_ENTRY_NOT_TRIGGERED)
            if structural.stop_distance_pips is not None:
                flags.append(FLAG_STRUCTURAL_SL_TP)

            momentum_confirm = self._m15_confirm(symbol, snap_m15, dir_h4)
            impulse_score = float(structural.impulse_score or 0.0)
            pullback_score = float(structural.pullback_score or 0.0)
            trigger_score = float(structural.trigger_score or 0.0)
            setup_score = regime_score + impulse_score + pullback_score
            continuation_score = setup_score + trigger_score

            if impulse_score < 10.0:
                flags.append(FLAG_IMPULSE_SCORE_WEAK)
            if pullback_score < 10.0:
                flags.append(FLAG_PULLBACK_SCORE_WEAK)
            if structural.entry_triggered and trigger_score < 8.0:
                flags.append(FLAG_TRIGGER_SCORE_WEAK)

            if structural.setup_present and setup_score < self.params.scoring.min_setup_score:
                flags.append(FLAG_CONTINUATION_SCORE_LOW)
                return SignalPreviewV1(
                    ts_utc=ts,
                    symbol=symbol,
                    setup_type=SetupType.NO_TRADE,
                    direction=Direction.FLAT,
                    setup_present=False,
                    entry_triggered=False,
                    confidence=Confidence.LOW,
                    rr=self.params.rr.base_rr or 0.0,
                    data_quality=data_quality,
                    spread_quality=spread_quality,
                    flags=flags,
                )

            if structural.entry_triggered and continuation_score < self.params.scoring.min_entry_score:
                flags.append(FLAG_CONTINUATION_SCORE_LOW)
                entry_triggered = False
                flags.append(FLAG_ENTRY_NOT_TRIGGERED)

            confidence = self._compute_confidence(continuation_score, momentum_confirm and entry_triggered)
            rr = self.params.rr.high_conf_rr if confidence == Confidence.HIGH else self.params.rr.base_rr or 0.0

            return SignalPreviewV1(
                ts_utc=ts,
                symbol=symbol,
                setup_type=setup_type if setup_present else SetupType.NO_TRADE,
                direction=direction if setup_present else Direction.FLAT,
                setup_present=setup_present,
                entry_triggered=entry_triggered,
                confidence=confidence,
                rr=rr,
                data_quality=data_quality,
                spread_quality=spread_quality,
                flags=flags,
                sl_distance_pips=structural.stop_distance_pips,
                tp_distance_pips=structural.take_profit_pips if structural.entry_triggered else None,
            )

        # Fallback: legacy surrogate continuation only when structural history is unavailable.
        setup_type = SetupType.SWING_CONTINUATION
        setup_present = True
        flags.append(FLAG_LEGACY_CONFIRM_FALLBACK)

        confirm = self._m15_confirm(symbol, snap_m15, dir_h4)
        if confirm:
            entry_triggered = True
        else:
            flags.append(FLAG_ENTRY_NOT_TRIGGERED)

        confidence = self._compute_confidence(regime_score + (60.0 if confirm else 0.0), confirm)
        rr = self.params.rr.high_conf_rr if confidence == Confidence.HIGH else self.params.rr.base_rr or 0.0

        if not setup_present:
            direction = Direction.FLAT

        return SignalPreviewV1(
            ts_utc=ts,
            symbol=symbol,
            setup_type=setup_type,
            direction=direction,
            setup_present=setup_present,
            entry_triggered=entry_triggered,
            confidence=confidence,
            rr=rr,
            data_quality=data_quality,
            spread_quality=spread_quality,
            flags=flags,
        )

    def compute_previews(
        self,
        snapshots: Iterable[MarketSnapshot],
        warmup_ready: bool = True,
        ohlc_by_symbol: Optional[Dict[str, Dict[str, dict]]] = None,
    ) -> List[SignalPreviewV1]:
        by_symbol: Dict[str, Dict[str, MarketSnapshot]] = {}
        for snap in snapshots:
            sym = snap.symbol.upper()
            by_symbol.setdefault(sym, {})
            by_symbol[sym][snap.timeframe] = snap
        previews: List[SignalPreviewV1] = []
        for sym, tf_snaps in by_symbol.items():
            previews.append(
                self.compute_preview_for_symbol(
                    sym,
                    tf_snaps,
                    warmup_ready=warmup_ready,
                    ohlc_by_timeframe=(ohlc_by_symbol or {}).get(sym),
                )
            )
        return previews
