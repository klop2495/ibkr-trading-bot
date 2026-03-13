#!/usr/bin/env python3
"""
Research backtest for 5 forecast strategy candidates on current-week data.

The script does not modify production logic. It reads:
- price_forecasts: feature snapshot + H30 realized outcome
- market_snapshots: S5 windows for microstructure filters, M15 windows for
  currency-strength overlay

Strategy candidates:
1. regime_switch_alt4
2. trend_pullback_confirmation
3. squeeze_breakout
4. exhaustion_mean_reversion
5. currency_strength_overlay

Usage:
    python scripts/backtest_strategy_candidates_weekly.py
    python scripts/backtest_strategy_candidates_weekly.py --start 2026-03-09T00:00:00+00:00
    python scripts/backtest_strategy_candidates_weekly.py --start 2026-03-09T00:00:00+00:00 --details
"""

from __future__ import annotations

import argparse
import os
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.storage.db import SupabaseDB


ACTIVE_SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
    "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY", "NZDJPY",
    "EURGBP", "EURAUD", "EURCHF",
]


@dataclass
class RowFeature:
    ts_utc: datetime
    symbol: str
    base_price: float
    actual_price: Optional[float]
    actual_dir: str
    h30_direction: Optional[str]
    adx_value: Optional[float]
    bb_width: Optional[float]
    bb_squeeze: Optional[bool]
    mtf_conflict: Optional[bool]
    mtf_h4_direction: Optional[str]
    votes: Dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest 5 strategy candidates on weekly forecast data")
    parser.add_argument("--start", default=None, help="ISO UTC start, default = Monday 00:00 UTC of current week")
    parser.add_argument("--end", default=None, help="ISO UTC end, default = now")
    parser.add_argument("--details", action="store_true", help="Print sample rows per strategy")
    parser.add_argument("--limit", type=int, default=20000, help="Safety cap for forecast rows")
    return parser.parse_args()


def iso_to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def monday_utc_start(now_utc: datetime) -> datetime:
    return (now_utc - timedelta(days=now_utc.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def fetch_paginated(query_builder: Any, page: int = 1000, hard_cap: int = 100000) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    offset = 0
    while offset < hard_cap:
        part = query_builder.range(offset, offset + page - 1).execute().data or []
        if not part:
            break
        rows.extend(part)
        offset += page
        if len(part) < page:
            break
    return rows


def load_forecasts(db: SupabaseDB, start: datetime, end: datetime, limit: int) -> List[RowFeature]:
    raw = fetch_paginated(
        db.client.table("price_forecasts")
        .select(
            "ts_utc,symbol,base_price,h30_actual,h30_actual_price,"
            "h30_direction,adx_value,bb_width,bb_squeeze,mtf_conflict,mtf_h4_direction,h30_votes_json"
        )
        .gte("ts_utc", start.isoformat())
        .lte("ts_utc", end.isoformat())
        .not_.is_("h30_direction", "null")
        .not_.is_("base_price", "null"),
        hard_cap=limit,
    )
    out: List[RowFeature] = []
    for r in raw:
        actual_dir = str(r.get("h30_actual") or "neutral").lower()
        if actual_dir not in {"up", "down", "neutral"}:
            actual_dir = "neutral"
        out.append(
            RowFeature(
                ts_utc=iso_to_dt(r["ts_utc"]),
                symbol=r["symbol"],
                base_price=float(r["base_price"]),
                actual_price=float(r["h30_actual_price"]) if r.get("h30_actual_price") is not None else None,
                actual_dir=actual_dir,
                h30_direction=(str(r.get("h30_direction")).lower() if r.get("h30_direction") else None),
                adx_value=float(r["adx_value"]) if r.get("adx_value") is not None else None,
                bb_width=float(r["bb_width"]) if r.get("bb_width") is not None else None,
                bb_squeeze=r.get("bb_squeeze"),
                mtf_conflict=r.get("mtf_conflict"),
                mtf_h4_direction=(str(r.get("mtf_h4_direction")).lower() if r.get("mtf_h4_direction") else None),
                votes=(r.get("h30_votes_json") or {}),
            )
        )
    return out


def load_m15_snapshots(db: SupabaseDB, start: datetime, end: datetime, symbols: Sequence[str]) -> Dict[str, List[Tuple[datetime, float]]]:
    out: Dict[str, List[Tuple[datetime, float]]] = {}
    for symbol in symbols:
        raw = fetch_paginated(
            db.client.table("market_snapshots")
            .select("ts,close")
            .eq("symbol", symbol)
            .eq("timeframe", "M15")
            .gte("ts", start.isoformat())
            .lte("ts", end.isoformat()),
            hard_cap=20000,
        )
        points = [(iso_to_dt(r["ts"]), float(r["close"])) for r in raw if r.get("close") is not None]
        points.sort(key=lambda x: x[0])
        out[symbol] = points
    return out


class S5WindowCache:
    def __init__(self, db: SupabaseDB) -> None:
        self._db = db
        self._cache: Dict[Tuple[str, str, int, int], List[Tuple[datetime, float]]] = {}

    def get(self, symbol: str, ts_utc: datetime, lookback_sec: int, lookahead_sec: int = 0) -> List[Tuple[datetime, float]]:
        key = (symbol, ts_utc.replace(second=0, microsecond=0).isoformat(), lookback_sec, lookahead_sec)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        start = ts_utc - timedelta(seconds=lookback_sec)
        end = ts_utc + timedelta(seconds=lookahead_sec)
        raw = (
            self._db.client.table("market_snapshots")
            .select("ts,close")
            .eq("symbol", symbol)
            .eq("timeframe", "S5")
            .gte("ts", start.isoformat())
            .lte("ts", end.isoformat())
            .order("ts")
            .limit(500)
            .execute()
            .data
            or []
        )
        points = [(iso_to_dt(r["ts"]), float(r["close"])) for r in raw if r.get("close") is not None]
        self._cache[key] = points
        return points


def pct_change(a: float, b: float) -> float:
    if a == 0:
        return 0.0
    return (b - a) / a


def pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001


def actual_direction(row: RowFeature) -> str:
    if row.actual_dir in {"up", "down"}:
        return row.actual_dir
    if row.actual_price is None:
        return "neutral"
    if row.actual_price > row.base_price:
        return "up"
    if row.actual_price < row.base_price:
        return "down"
    return "neutral"


def direction_correct(predicted: Optional[str], row: RowFeature) -> Optional[bool]:
    if predicted not in {"up", "down"}:
        return None
    actual = actual_direction(row)
    if actual not in {"up", "down"}:
        return False
    return predicted == actual


def micro_pullback_confirm(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> bool:
    if len(s5) < 6 or row.h30_direction not in {"up", "down"}:
        return False
    closes = [x[1] for x in s5]
    final_close = closes[-1]
    prior_60 = closes[:-1]
    ps = pip_size(row.symbol)
    if row.h30_direction == "up":
        dipped = (final_close - min(prior_60)) >= 1.0 * ps
        recovered = final_close > closes[-3]
        return dipped and recovered
    spiked = (max(prior_60) - final_close) >= 1.0 * ps
    recovered = final_close < closes[-3]
    return spiked and recovered


def squeeze_breakout_confirm(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> bool:
    if len(s5) < 12 or row.h30_direction not in {"up", "down"}:
        return False
    closes = [x[1] for x in s5]
    early = closes[:-3]
    final_close = closes[-1]
    ps = pip_size(row.symbol)
    if row.h30_direction == "up":
        return final_close >= max(early) + 0.5 * ps
    return final_close <= min(early) - 0.5 * ps


def squeeze_breakout_confirm_relaxed_1(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> bool:
    if len(s5) < 10 or row.h30_direction not in {"up", "down"}:
        return False
    closes = [x[1] for x in s5]
    early = closes[:-2]
    final_close = closes[-1]
    ps = pip_size(row.symbol)
    if row.h30_direction == "up":
        return final_close >= max(early) + 0.2 * ps
    return final_close <= min(early) - 0.2 * ps


def squeeze_breakout_confirm_relaxed_2(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> bool:
    if len(s5) < 8 or row.h30_direction not in {"up", "down"}:
        return False
    closes = [x[1] for x in s5]
    ps = pip_size(row.symbol)
    start = closes[0]
    end = closes[-1]
    net = end - start
    last3 = closes[-3:]
    if row.h30_direction == "up":
        return net >= 0.3 * ps and last3[-1] >= last3[0]
    return net <= -0.3 * ps and last3[-1] <= last3[0]


def squeeze_breakout_confirm_strict_s5(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> bool:
    if len(s5) < 8 or row.h30_direction not in {"up", "down"}:
        return False
    closes = [x[1] for x in s5]
    ps = pip_size(row.symbol)
    net = closes[-1] - closes[0]
    last3 = closes[-3:]
    if row.h30_direction == "up":
        return (
            net >= 0.5 * ps
            and last3[0] <= last3[1] <= last3[2]
            and closes[-1] >= max(closes[:-3])
        )
    return (
        net <= -0.5 * ps
        and last3[0] >= last3[1] >= last3[2]
        and closes[-1] <= min(closes[:-3])
    )


def squeeze_breakout_confirm_strict_s5_v2(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> bool:
    if len(s5) < 10 or row.h30_direction not in {"up", "down"}:
        return False
    closes = [x[1] for x in s5]
    ps = pip_size(row.symbol)
    net = closes[-1] - closes[0]
    last4 = closes[-4:]
    body = max(closes) - min(closes)
    if row.h30_direction == "up":
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


def squeeze_breakout_confirm_strict_s5_v3(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> bool:
    if len(s5) < 12 or row.h30_direction not in {"up", "down"}:
        return False
    closes = [x[1] for x in s5]
    ps = pip_size(row.symbol)
    net = closes[-1] - closes[0]
    last4 = closes[-4:]
    pullback = max(closes[-8:-4]) - min(closes[-8:-4])
    if row.h30_direction == "up":
        return (
            net >= 1.0 * ps
            and last4[0] <= last4[1] <= last4[2] <= last4[3]
            and closes[-1] >= max(closes[:-4])
            and pullback <= 0.6 * ps
        )
    return (
        net <= -1.0 * ps
        and last4[0] >= last4[1] >= last4[2] >= last4[3]
        and closes[-1] <= min(closes[:-4])
        and pullback <= 0.6 * ps
    )


def exhaustion_reversal_direction(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> Optional[str]:
    if len(s5) < 24:
        return None
    closes = [x[1] for x in s5]
    start = closes[0]
    end = closes[-1]
    ps = pip_size(row.symbol)
    move = end - start
    if abs(move) < 2.0 * ps:
        return None
    return "down" if move > 0 else "up"


def nearest_price_at_or_before(points: Sequence[Tuple[datetime, float]], ts: datetime) -> Optional[float]:
    if not points:
        return None
    times = [p[0] for p in points]
    idx = bisect_right(times, ts) - 1
    if idx < 0:
        return None
    return points[idx][1]


def currency_strength_alignment(
    row: RowFeature,
    m15: Dict[str, List[Tuple[datetime, float]]],
    lookback_minutes: int = 60,
) -> Optional[str]:
    now_prices: Dict[str, float] = {}
    prev_prices: Dict[str, float] = {}
    prev_ts = row.ts_utc - timedelta(minutes=lookback_minutes)
    for symbol in ACTIVE_SYMBOLS:
        current = nearest_price_at_or_before(m15.get(symbol, []), row.ts_utc)
        previous = nearest_price_at_or_before(m15.get(symbol, []), prev_ts)
        if current is None or previous is None:
            continue
        now_prices[symbol] = current
        prev_prices[symbol] = previous
    if len(now_prices) < 6:
        return None

    scores: Dict[str, List[float]] = defaultdict(list)
    for pair, current in now_prices.items():
        previous = prev_prices.get(pair)
        if previous in (None, 0):
            continue
        base, quote = pair[:3], pair[3:]
        change = pct_change(previous, current)
        scores[base].append(change)
        scores[quote].append(-change)
    if not scores:
        return None
    base_ccy, quote_ccy = row.symbol[:3], row.symbol[3:]
    if base_ccy not in scores or quote_ccy not in scores:
        return None
    base_strength = sum(scores[base_ccy]) / len(scores[base_ccy])
    quote_strength = sum(scores[quote_ccy]) / len(scores[quote_ccy])
    delta = base_strength - quote_strength
    if abs(delta) < 0.00035:
        return None
    return "up" if delta > 0 else "down"


def strategy_regime_switch_alt4(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> Optional[str]:
    if row.h30_direction not in {"up", "down"}:
        return None
    adx = row.adx_value
    squeeze = bool(row.bb_squeeze)
    conflict = bool(row.mtf_conflict)
    if adx is not None and adx >= 20 and not squeeze and not conflict:
        return row.h30_direction
    if (adx is not None and adx < 15) or squeeze:
        return "down" if row.h30_direction == "up" else "up"
    return None


def strategy_trend_pullback_confirmation(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> Optional[str]:
    if row.h30_direction not in {"up", "down"}:
        return None
    if row.adx_value is None or row.adx_value < 20:
        return None
    if row.mtf_conflict:
        return None
    if not micro_pullback_confirm(row, s5):
        return None
    return row.h30_direction


def strategy_squeeze_breakout(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> Optional[str]:
    if row.h30_direction not in {"up", "down"}:
        return None
    if not bool(row.bb_squeeze):
        return None
    if not squeeze_breakout_confirm(row, s5):
        return None
    return row.h30_direction


def strategy_squeeze_breakout_relaxed_1(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> Optional[str]:
    if row.h30_direction not in {"up", "down"}:
        return None
    width_ok = row.bb_width is not None and row.bb_width <= 0.0065
    if not (bool(row.bb_squeeze) or width_ok):
        return None
    if not squeeze_breakout_confirm_relaxed_1(row, s5):
        return None
    return row.h30_direction


def strategy_squeeze_breakout_relaxed_2(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> Optional[str]:
    if row.h30_direction not in {"up", "down"}:
        return None
    width_ok = row.bb_width is not None and row.bb_width <= 0.008
    if not (bool(row.bb_squeeze) or width_ok):
        return None
    if not squeeze_breakout_confirm_relaxed_2(row, s5):
        return None
    return row.h30_direction


def strategy_squeeze_breakout_relaxed_2_adx20(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> Optional[str]:
    if row.adx_value is None or row.adx_value < 20:
        return None
    return strategy_squeeze_breakout_relaxed_2(row, s5)


def strategy_squeeze_breakout_relaxed_2_adx20_no_mtf_conflict(
    row: RowFeature, s5: Sequence[Tuple[datetime, float]]
) -> Optional[str]:
    if row.mtf_conflict:
        return None
    return strategy_squeeze_breakout_relaxed_2_adx20(row, s5)


def strategy_squeeze_breakout_relaxed_2_strict_s5(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> Optional[str]:
    if row.h30_direction not in {"up", "down"}:
        return None
    width_ok = row.bb_width is not None and row.bb_width <= 0.008
    if not (bool(row.bb_squeeze) or width_ok):
        return None
    if not squeeze_breakout_confirm_strict_s5(row, s5):
        return None
    return row.h30_direction


def strategy_squeeze_breakout_relaxed_2_strict_s5_v2(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> Optional[str]:
    if row.h30_direction not in {"up", "down"}:
        return None
    width_ok = row.bb_width is not None and row.bb_width <= 0.008
    if not (bool(row.bb_squeeze) or width_ok):
        return None
    if not squeeze_breakout_confirm_strict_s5_v2(row, s5):
        return None
    return row.h30_direction


def strategy_squeeze_breakout_relaxed_2_strict_s5_v3(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> Optional[str]:
    if row.h30_direction not in {"up", "down"}:
        return None
    width_ok = row.bb_width is not None and row.bb_width <= 0.007
    if not (bool(row.bb_squeeze) or width_ok):
        return None
    if not squeeze_breakout_confirm_strict_s5_v3(row, s5):
        return None
    return row.h30_direction


def strategy_exhaustion_mean_reversion(row: RowFeature, s5: Sequence[Tuple[datetime, float]]) -> Optional[str]:
    if row.adx_value is not None and row.adx_value >= 20:
        return None
    return exhaustion_reversal_direction(row, s5)


def strategy_currency_strength_overlay(row: RowFeature, s5: Sequence[Tuple[datetime, float]], m15: Dict[str, List[Tuple[datetime, float]]]) -> Optional[str]:
    if row.h30_direction not in {"up", "down"}:
        return None
    overlay = currency_strength_alignment(row, m15)
    if overlay is None:
        return None
    if overlay != row.h30_direction:
        return None
    if row.mtf_conflict:
        return None
    return row.h30_direction


def summarize(name: str, rows: Sequence[Tuple[RowFeature, Optional[str], Optional[bool]]], details: bool) -> None:
    traded = [(r, pred, ok) for r, pred, ok in rows if pred in {"up", "down"} and ok is not None]
    n = len(traded)
    ok_n = sum(1 for _, _, ok in traded if bool(ok))
    acc = 100.0 * ok_n / n if n else 0.0
    pnl = ok_n * 80 - (n - ok_n) * 100
    print(f"{name:28s}: {ok_n}/{n} acc={acc:.1f}% pnl={pnl:+d}")
    if details and traded:
        for row, pred, ok in traded[:12]:
            print(
                f"  {row.ts_utc.isoformat()} {row.symbol:7s} pred={pred:4s} "
                f"actual={actual_direction(row):7s} ok={ok} adx={row.adx_value} "
                f"bb_squeeze={row.bb_squeeze} mtf_conflict={row.mtf_conflict}"
            )


def summarize_working_hours_breakdown(
    name: str,
    rows: Sequence[Tuple[RowFeature, Optional[str], Optional[bool]]],
    hour_from: int = 9,
    hour_to_exclusive: int = 20,
) -> None:
    traded = [
        (r, pred, ok)
        for r, pred, ok in rows
        if pred in {"up", "down"}
        and ok is not None
        and hour_from <= r.ts_utc.hour < hour_to_exclusive
    ]
    n = len(traded)
    ok_n = sum(1 for _, _, ok in traded if bool(ok))
    acc = 100.0 * ok_n / n if n else 0.0
    pnl = ok_n * 80 - (n - ok_n) * 100
    print(f"\n=== {name} | working_hours_utc={hour_from:02d}:00-{hour_to_exclusive - 1:02d}:59 ===")
    print(f"ALL: {ok_n}/{n} acc={acc:.1f}% pnl={pnl:+d}")

    by_day: Dict[str, Dict[str, int]] = defaultdict(lambda: {"n": 0, "ok": 0})
    by_symbol: Dict[str, Dict[str, int]] = defaultdict(lambda: {"n": 0, "ok": 0})
    by_hour: Dict[int, Dict[str, int]] = defaultdict(lambda: {"n": 0, "ok": 0})

    for row, _, ok in traded:
        day_key = row.ts_utc.strftime("%Y-%m-%d")
        by_day[day_key]["n"] += 1
        by_day[day_key]["ok"] += int(bool(ok))

        by_symbol[row.symbol]["n"] += 1
        by_symbol[row.symbol]["ok"] += int(bool(ok))

        by_hour[row.ts_utc.hour]["n"] += 1
        by_hour[row.ts_utc.hour]["ok"] += int(bool(ok))

    print("BY_DAY")
    for day in sorted(by_day):
        s = by_day[day]
        print(f"{day} | {s['ok']}/{s['n']} acc={100.0 * s['ok'] / s['n']:.1f}%")

    print("BY_HOUR")
    for hour in sorted(by_hour):
        s = by_hour[hour]
        print(f"{hour:02d} | {s['ok']}/{s['n']} acc={100.0 * s['ok'] / s['n']:.1f}%")

    print("BY_SYMBOL")
    for symbol in sorted(by_symbol, key=lambda x: (-by_symbol[x]["n"], x)):
        s = by_symbol[symbol]
        print(f"{symbol:7s} | {s['ok']}/{s['n']} acc={100.0 * s['ok'] / s['n']:.1f}%")


def main() -> int:
    args = parse_args()
    now = datetime.now(timezone.utc)
    start = iso_to_dt(args.start) if args.start else monday_utc_start(now)
    end = iso_to_dt(args.end) if args.end else now

    db = SupabaseDB()
    if db.disabled or not db.ping():
        print("ERROR: Supabase is not reachable")
        return 1

    forecasts = load_forecasts(db, start, end, args.limit)
    if not forecasts:
        print(f"No forecast rows found for period {start.isoformat()} -> {end.isoformat()}")
        return 0

    m15 = load_m15_snapshots(db, start - timedelta(hours=2), end, ACTIVE_SYMBOLS)
    s5_cache = S5WindowCache(db)

    print(f"PERIOD={start.isoformat()} -> {end.isoformat()}")
    print(f"forecast_rows={len(forecasts)}")

    results: Dict[str, List[Tuple[RowFeature, Optional[str], Optional[bool]]]] = {
        "regime_switch_alt4": [],
        "trend_pullback_confirmation": [],
        "squeeze_breakout": [],
        "squeeze_breakout_relaxed_1": [],
        "squeeze_breakout_relaxed_2": [],
        "squeeze_breakout_relaxed_2_adx20": [],
        "squeeze_breakout_relaxed_2_adx20_no_mtf_conflict": [],
        "squeeze_breakout_relaxed_2_strict_s5": [],
        "squeeze_breakout_relaxed_2_strict_s5_v2": [],
        "squeeze_breakout_relaxed_2_strict_s5_v3": [],
        "exhaustion_mean_reversion": [],
        "currency_strength_overlay": [],
    }

    for row in forecasts:
        s5_short = s5_cache.get(row.symbol, row.ts_utc, lookback_sec=180, lookahead_sec=0)
        runs = {
            "regime_switch_alt4": strategy_regime_switch_alt4(row, s5_short),
            "trend_pullback_confirmation": strategy_trend_pullback_confirmation(row, s5_short),
            "squeeze_breakout": strategy_squeeze_breakout(row, s5_short),
            "squeeze_breakout_relaxed_1": strategy_squeeze_breakout_relaxed_1(row, s5_short),
            "squeeze_breakout_relaxed_2": strategy_squeeze_breakout_relaxed_2(row, s5_short),
            "squeeze_breakout_relaxed_2_adx20": strategy_squeeze_breakout_relaxed_2_adx20(row, s5_short),
            "squeeze_breakout_relaxed_2_adx20_no_mtf_conflict": strategy_squeeze_breakout_relaxed_2_adx20_no_mtf_conflict(row, s5_short),
            "squeeze_breakout_relaxed_2_strict_s5": strategy_squeeze_breakout_relaxed_2_strict_s5(row, s5_short),
            "squeeze_breakout_relaxed_2_strict_s5_v2": strategy_squeeze_breakout_relaxed_2_strict_s5_v2(row, s5_short),
            "squeeze_breakout_relaxed_2_strict_s5_v3": strategy_squeeze_breakout_relaxed_2_strict_s5_v3(row, s5_short),
            "exhaustion_mean_reversion": strategy_exhaustion_mean_reversion(row, s5_short),
            "currency_strength_overlay": strategy_currency_strength_overlay(row, s5_short, m15),
        }
        for key, predicted in runs.items():
            results[key].append((row, predicted, direction_correct(predicted, row)))

    print("\n=== WEEKLY STRATEGY CANDIDATES ===")
    for name, rows in results.items():
        summarize(name, rows, args.details)

    summarize_working_hours_breakdown("squeeze_breakout_relaxed_2", results["squeeze_breakout_relaxed_2"])
    summarize_working_hours_breakdown("squeeze_breakout_relaxed_2_strict_s5", results["squeeze_breakout_relaxed_2_strict_s5"])
    summarize_working_hours_breakdown("squeeze_breakout_relaxed_2_strict_s5_v2", results["squeeze_breakout_relaxed_2_strict_s5_v2"])
    summarize_working_hours_breakdown("squeeze_breakout_relaxed_2_strict_s5_v3", results["squeeze_breakout_relaxed_2_strict_s5_v3"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
