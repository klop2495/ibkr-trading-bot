from datetime import datetime, timezone
from typing import Dict, Iterable, List

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

    def _m15_confirm(self, snap_m15: MarketSnapshot | None, direction: Direction) -> bool:
        if snap_m15 is None:
            return False
        rsi_val = getattr(snap_m15, "rsi", None)
        close_val = getattr(snap_m15, "close", None)
        sma_val = getattr(snap_m15, "ma_fast", None)
        if rsi_val is None or close_val is None or sma_val is None:
            return False
        if direction == Direction.LONG:
            return (
                rsi_val >= (self.params.m15_confirm.rsi_long_min or 0)
                and close_val > sma_val
            )
        if direction == Direction.SHORT:
            return (
                rsi_val <= (self.params.m15_confirm.rsi_short_max or 0)
                and close_val < sma_val
            )
        return False

    def _compute_confidence(
        self,
        dir_h4: Direction,
        dir_h1: Direction,
        m15_confirm: bool,
    ) -> Confidence:
        if dir_h4 == Direction.FLAT or dir_h1 == Direction.FLAT:
            return Confidence.LOW
        if dir_h4 == dir_h1 and m15_confirm:
            return Confidence.HIGH
        return Confidence.NORMAL

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
        dir_m15 = self._direction_from_ma(snap_m15)

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

        # Minimal deterministic setup detection placeholder
        setup_type = SetupType.SWING_CONTINUATION if dir_h4 == dir_h1 else SetupType.SWING_REVERSAL
        setup_present = True

        confirm = self._m15_confirm(snap_m15, dir_h4 if dir_h4 == dir_h1 else dir_m15)
        if confirm:
            entry_triggered = True
        else:
            flags.append(FLAG_ENTRY_NOT_TRIGGERED)

        confidence = self._compute_confidence(dir_h4, dir_h1, confirm)
        rr = self.params.rr.high_conf_rr if confidence == Confidence.HIGH else self.params.rr.base_rr or 0.0

        direction = dir_h4 if dir_h4 == dir_h1 else dir_m15
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
    ) -> List[SignalPreviewV1]:
        by_symbol: Dict[str, Dict[str, MarketSnapshot]] = {}
        for snap in snapshots:
            sym = snap.symbol.upper()
            by_symbol.setdefault(sym, {})
            by_symbol[sym][snap.timeframe] = snap
        previews: List[SignalPreviewV1] = []
        for sym, tf_snaps in by_symbol.items():
            previews.append(self.compute_preview_for_symbol(sym, tf_snaps, warmup_ready=warmup_ready))
        return previews
