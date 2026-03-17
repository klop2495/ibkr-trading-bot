"""
Alt6 forecast strategy: strict_s5_v2_extension_veto.

This strategy is evaluated after base forecasts are produced because it needs
recent S5 snapshots from the database.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence, Tuple

from app.models.forecast import ForecastResult

logger = logging.getLogger(__name__)


DEFAULT_MAX_EXTENSION_PIPS = float(os.getenv("ALT6_MAX_EXTENSION_PIPS", "1.4"))
DEFAULT_MAX_BREAKOUT_AGE_SEC = int(os.getenv("ALT6_MAX_BREAKOUT_AGE_SEC", "90"))
DEFAULT_MAX_BREAKOUT_DISTANCE_PIPS = float(os.getenv("ALT6_MAX_BREAKOUT_DISTANCE_PIPS", "1.4"))
DEFAULT_BREAKOUT_NET_PIPS = float(os.getenv("ALT6_BREAKOUT_NET_PIPS", "0.4"))
DEFAULT_BREAKOUT_BODY_PIPS = float(os.getenv("ALT6_BREAKOUT_BODY_PIPS", "1.0"))
DEFAULT_BREAKOUT_MONOTONIC_MODE = str(os.getenv("ALT6_BREAKOUT_MONOTONIC_MODE", "relaxed")).strip().lower() or "relaxed"
DEFAULT_SOFT_NET_PIPS = float(os.getenv("ALT6_SOFT_NET_PIPS", "0.2"))
DEFAULT_SOFT_BODY_PIPS = float(os.getenv("ALT6_SOFT_BODY_PIPS", "0.6"))
DEFAULT_SOFT_BREAK_MARGIN_PIPS = float(os.getenv("ALT6_SOFT_BREAK_MARGIN_PIPS", "0.2"))
DEFAULT_MIN_ADX = float(os.getenv("ALT6_MIN_ADX", "0"))
DEFAULT_MTF_VETO = str(os.getenv("ALT6_MTF_VETO", "false")).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Alt6Decision:
    direction: Optional[str]
    trade_eligible: bool
    reject_reason: Optional[str]
    stage: str
    candidate: bool
    structure_passed: bool
    timing_passed: bool
    quality_passed: bool
    quality_flags: Tuple[str, ...] = ()


def pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol.upper() else 0.0001


def _iso_to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _h30_direction(forecast: ForecastResult) -> Optional[str]:
    horizon = forecast.horizon(30)
    if horizon is None:
        return None
    return horizon.direction.value


def _append_reject_flag(forecast: ForecastResult, reason: Optional[str]) -> None:
    if not reason:
        return
    flag = f"ALT6_REJECT:{reason}"
    if flag not in forecast.flags:
        forecast.flags.append(flag)


def _append_stage_flag(forecast: ForecastResult, stage: str) -> None:
    flag = f"ALT6_STAGE:{stage}"
    if flag not in forecast.flags:
        forecast.flags.append(flag)


def _breakout_metrics(
    forecast: ForecastResult,
    s5: Sequence[Tuple[datetime, float]],
) -> Tuple[Optional[float], Optional[float]]:
    direction = _h30_direction(forecast)
    if len(s5) < 10 or direction not in {"up", "down"}:
        return None, None
    closes = [x[1] for x in s5]
    times = [x[0] for x in s5]
    breakout_ts: Optional[datetime] = None
    breakout_anchor: Optional[float] = None
    if direction == "up":
        for idx in range(4, len(closes)):
            prior_anchor = max(closes[:idx])
            if closes[idx] > prior_anchor:
                breakout_ts = times[idx]
                breakout_anchor = prior_anchor
        if breakout_ts is None or breakout_anchor is None:
            return None, None
        distance_pips = (closes[-1] - breakout_anchor) / pip_size(forecast.symbol)
    else:
        for idx in range(4, len(closes)):
            prior_anchor = min(closes[:idx])
            if closes[idx] < prior_anchor:
                breakout_ts = times[idx]
                breakout_anchor = prior_anchor
        if breakout_ts is None or breakout_anchor is None:
            return None, None
        distance_pips = (breakout_anchor - closes[-1]) / pip_size(forecast.symbol)
    if breakout_ts is None or breakout_anchor is None:
        return None, None
    age_sec = max(0.0, (forecast.ts_utc - breakout_ts).total_seconds())
    return age_sec, max(0.0, distance_pips)


def load_s5_window(
    db_client: any,
    *,
    symbol: str,
    ts_utc: datetime,
    lookback_sec: int = 180,
    limit: int = 500,
) -> List[Tuple[datetime, float]]:
    start = ts_utc - timedelta(seconds=lookback_sec)
    raw = (
        db_client.table("market_snapshots")
        .select("ts,close")
        .eq("symbol", symbol.upper())
        .eq("timeframe", "S5")
        .gte("ts", start.isoformat())
        .lte("ts", ts_utc.isoformat())
        .order("ts")
        .limit(limit)
        .execute()
        .data
        or []
    )
    out: List[Tuple[datetime, float]] = []
    for row in raw:
        close = row.get("close")
        ts_raw = row.get("ts")
        if close is None or not ts_raw:
            continue
        out.append((_iso_to_dt(ts_raw), float(close)))
    return out


def squeeze_breakout_confirm_strict_s5_v2(
    forecast: ForecastResult, s5: Sequence[Tuple[datetime, float]]
) -> bool:
    direction = _h30_direction(forecast)
    if len(s5) < 10 or direction not in {"up", "down"}:
        return False
    closes = [x[1] for x in s5]
    ps = pip_size(forecast.symbol)
    net = closes[-1] - closes[0]
    last4 = closes[-4:]
    body = max(closes) - min(closes)
    monotonic_mode = DEFAULT_BREAKOUT_MONOTONIC_MODE
    if direction == "up":
        monotonic_ok = (
            last4[0] <= last4[1] <= last4[2] <= last4[3]
            if monotonic_mode == "strict"
            else sum(1 for a, b in zip(last4, last4[1:]) if b >= a) >= 2
        )
        return (
            net >= DEFAULT_BREAKOUT_NET_PIPS * ps
            and monotonic_ok
            and closes[-1] >= max(closes[:-4])
            and body >= DEFAULT_BREAKOUT_BODY_PIPS * ps
        )
    monotonic_ok = (
        last4[0] >= last4[1] >= last4[2] >= last4[3]
        if monotonic_mode == "strict"
        else sum(1 for a, b in zip(last4, last4[1:]) if b <= a) >= 2
    )
    return (
        net <= -DEFAULT_BREAKOUT_NET_PIPS * ps
        and monotonic_ok
        and closes[-1] <= min(closes[:-4])
        and body >= DEFAULT_BREAKOUT_BODY_PIPS * ps
    )


def squeeze_breakout_confirm_soft_s5_v4(
    forecast: ForecastResult, s5: Sequence[Tuple[datetime, float]]
) -> bool:
    direction = _h30_direction(forecast)
    if len(s5) < 8 or direction not in {"up", "down"}:
        return False
    closes = [x[1] for x in s5]
    ps = pip_size(forecast.symbol)
    recent = closes[-4:]
    history = closes[:-4]
    if not history:
        return False
    net = closes[-1] - closes[0]
    body = max(closes) - min(closes)
    margin = DEFAULT_SOFT_BREAK_MARGIN_PIPS * ps
    if direction == "up":
        directional_steps = sum(1 for a, b in zip(recent, recent[1:]) if b >= a)
        anchor = max(history)
        breakout_touch = max(recent) >= (anchor - margin)
        return (
            net >= DEFAULT_SOFT_NET_PIPS * ps
            and directional_steps >= 2
            and breakout_touch
            and closes[-1] >= (anchor - margin)
            and body >= DEFAULT_SOFT_BODY_PIPS * ps
        )
    directional_steps = sum(1 for a, b in zip(recent, recent[1:]) if b <= a)
    anchor = min(history)
    breakout_touch = min(recent) <= (anchor + margin)
    return (
        net <= -DEFAULT_SOFT_NET_PIPS * ps
        and directional_steps >= 2
        and breakout_touch
        and closes[-1] <= (anchor + margin)
        and body >= DEFAULT_SOFT_BODY_PIPS * ps
    )


def extension_veto(
    forecast: ForecastResult,
    s5: Sequence[Tuple[datetime, float]],
    *,
    max_extension_pips: float = DEFAULT_MAX_EXTENSION_PIPS,
) -> bool:
    direction = _h30_direction(forecast)
    if len(s5) < 8 or direction not in {"up", "down"}:
        return False
    closes = [x[1] for x in s5]
    ps = pip_size(forecast.symbol)
    extension = (closes[-1] - closes[0]) / ps
    if direction == "up":
        return extension > max_extension_pips
    return extension < -max_extension_pips


def _candidate_stage(forecast: ForecastResult) -> Tuple[Optional[str], Optional[str]]:
    direction = _h30_direction(forecast)
    if direction not in {"up", "down"}:
        return None, "NO_H30_DIRECTION"
    return direction, None


def _structure_stage(
    forecast: ForecastResult,
    s5: Sequence[Tuple[datetime, float]],
) -> Optional[str]:
    width_ok = forecast.bb_width is not None and forecast.bb_width <= 0.008
    if not (bool(forecast.bb_squeeze) or width_ok):
        return "NO_SQUEEZE_CONTEXT"
    if not squeeze_breakout_confirm_soft_s5_v4(forecast, s5):
        return "STRUCTURE_SOFT_FAIL"
    return None


def _timing_stage(
    forecast: ForecastResult,
    s5: Sequence[Tuple[datetime, float]],
) -> Optional[str]:
    breakout_age_sec, breakout_distance_pips = _breakout_metrics(forecast, s5)
    if breakout_age_sec is None or breakout_distance_pips is None:
        return "BREAKOUT_CONTEXT_MISSING"
    if breakout_age_sec > DEFAULT_MAX_BREAKOUT_AGE_SEC:
        return "BREAKOUT_TOO_OLD"
    if breakout_distance_pips > DEFAULT_MAX_BREAKOUT_DISTANCE_PIPS:
        return "TOO_FAR_FROM_BREAKOUT"
    if extension_veto(forecast, s5):
        return "EXTENSION_VETO"
    return None


def _quality_stage(
    forecast: ForecastResult,
    s5: Sequence[Tuple[datetime, float]],
) -> Tuple[Optional[str], Tuple[str, ...]]:
    quality_flags: list[str] = []
    if squeeze_breakout_confirm_strict_s5_v2(forecast, s5):
        quality_flags.append("ALT6_QUALITY:STRICT_BREAKOUT_OK")
    else:
        quality_flags.append("ALT6_QUALITY:STRICT_BREAKOUT_WEAK")
    if forecast.adx_value is not None and forecast.adx_value < DEFAULT_MIN_ADX:
        return "LOW_ADX", tuple(quality_flags)
    if DEFAULT_MTF_VETO and bool(forecast.mtf_conflict):
        return "MTF_CONFLICT", tuple(quality_flags)
    return None, tuple(quality_flags)


def compute_alt6_signal(
    forecast: ForecastResult, s5: Sequence[Tuple[datetime, float]]
) -> Alt6Decision:
    direction, reason = _candidate_stage(forecast)
    if reason:
        return Alt6Decision(
            direction=None,
            trade_eligible=False,
            reject_reason=reason,
            stage="candidate",
            candidate=False,
            structure_passed=False,
            timing_passed=False,
            quality_passed=False,
        )
    reason = _structure_stage(forecast, s5)
    if reason:
        return Alt6Decision(
            direction=None,
            trade_eligible=False,
            reject_reason=reason,
            stage="structure",
            candidate=True,
            structure_passed=False,
            timing_passed=False,
            quality_passed=False,
        )
    reason = _timing_stage(forecast, s5)
    if reason:
        return Alt6Decision(
            direction=None,
            trade_eligible=False,
            reject_reason=reason,
            stage="timing",
            candidate=True,
            structure_passed=True,
            timing_passed=False,
            quality_passed=False,
        )
    reason, quality_flags = _quality_stage(forecast, s5)
    if reason:
        return Alt6Decision(
            direction=None,
            trade_eligible=False,
            reject_reason=reason,
            stage="quality",
            candidate=True,
            structure_passed=True,
            timing_passed=True,
            quality_passed=False,
            quality_flags=quality_flags,
        )
    return Alt6Decision(
        direction=direction,
        trade_eligible=True,
        reject_reason=None,
        stage="executable",
        candidate=True,
        structure_passed=True,
        timing_passed=True,
        quality_passed=True,
        quality_flags=quality_flags,
    )


def enrich_forecasts_with_alt6(db_client: any, forecasts: Iterable[ForecastResult]) -> None:
    for forecast in forecasts:
        try:
            s5 = load_s5_window(db_client, symbol=forecast.symbol, ts_utc=forecast.ts_utc)
            decision = compute_alt6_signal(forecast, s5)
            forecast.h30_alt6_direction = decision.direction
            forecast.h30_alt6_trade_eligible = decision.trade_eligible
            if decision.candidate:
                _append_stage_flag(forecast, "CANDIDATE")
            if decision.structure_passed:
                _append_stage_flag(forecast, "STRUCTURE")
            if decision.timing_passed:
                _append_stage_flag(forecast, "TIMING")
            if decision.quality_passed:
                _append_stage_flag(forecast, "QUALITY")
            if decision.direction and decision.trade_eligible:
                _append_stage_flag(forecast, "EXECUTABLE")
            for flag in decision.quality_flags:
                if flag not in forecast.flags:
                    forecast.flags.append(flag)
            _append_reject_flag(forecast, decision.reject_reason)
        except Exception as exc:
            logger.warning("alt6_enrich_failed symbol=%s error=%s", forecast.symbol, exc)
            forecast.h30_alt6_direction = None
            forecast.h30_alt6_trade_eligible = False
            _append_reject_flag(forecast, "ENRICH_FAILED")
