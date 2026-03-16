"""
Alt6 forecast strategy: strict_s5_v2_extension_veto.

This strategy is evaluated after base forecasts are produced because it needs
recent S5 snapshots from the database.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence, Tuple

from app.models.forecast import ForecastResult

logger = logging.getLogger(__name__)


def pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol.upper() else 0.0001


def _iso_to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _h30_direction(forecast: ForecastResult) -> Optional[str]:
    horizon = forecast.horizon(30)
    if horizon is None:
        return None
    return horizon.direction.value


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
    if direction == "up":
        return (
            net >= 0.8 * ps
            and last4[0] <= last4[1] <= last4[2] <= last4[3]
            and closes[-1] >= max(closes[:-4])
            and body >= 1.0 * ps
        )
    return (
        net <= -0.8 * ps
        and last4[0] >= last4[1] >= last4[2] >= last4[3]
        and closes[-1] <= min(closes[:-4])
        and body >= 1.0 * ps
    )


def extension_veto(
    forecast: ForecastResult,
    s5: Sequence[Tuple[datetime, float]],
    *,
    max_extension_pips: float = 1.8,
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


def compute_alt6_signal(
    forecast: ForecastResult, s5: Sequence[Tuple[datetime, float]]
) -> Tuple[Optional[str], Optional[bool]]:
    direction = _h30_direction(forecast)
    if direction not in {"up", "down"}:
        return None, None
    width_ok = forecast.bb_width is not None and forecast.bb_width <= 0.008
    if not (bool(forecast.bb_squeeze) or width_ok):
        return None, None
    if not squeeze_breakout_confirm_strict_s5_v2(forecast, s5):
        return None, None
    if extension_veto(forecast, s5):
        return None, None
    return direction, True


def enrich_forecasts_with_alt6(db_client: any, forecasts: Iterable[ForecastResult]) -> None:
    for forecast in forecasts:
        try:
            s5 = load_s5_window(db_client, symbol=forecast.symbol, ts_utc=forecast.ts_utc)
            direction, eligible = compute_alt6_signal(forecast, s5)
            forecast.h30_alt6_direction = direction
            forecast.h30_alt6_trade_eligible = eligible
        except Exception as exc:
            logger.warning("alt6_enrich_failed symbol=%s error=%s", forecast.symbol, exc)
            forecast.h30_alt6_direction = None
            forecast.h30_alt6_trade_eligible = None
