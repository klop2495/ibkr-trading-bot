#!/usr/bin/env python3
"""
Sweep ALT6 breakout-confirm thresholds on today's forecasts.

This isolates the current dominant blocker: BREAKOUT_NOT_CONFIRMED.

Usage:
  python scripts/sweep_alt6_breakout_today.py
  python scripts/sweep_alt6_breakout_today.py --symbols EURUSD USDJPY
  python scripts/sweep_alt6_breakout_today.py --net-thresholds 0.4,0.6,0.8 --body-thresholds 0.5,0.8,1.0 --monotonic-modes strict,relaxed
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.forecast import alt6
from app.models.forecast import (
    ForecastConfidence,
    ForecastDirection,
    ForecastHorizon,
    ForecastResult,
)
from app.storage.db import SupabaseDB


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_float_csv(raw: str) -> list[float]:
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_str_csv(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def _pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((part / total) * 100.0, 1)


def _build_forecast(row: dict[str, Any]) -> ForecastResult:
    direction = str(row.get("h30_direction") or "neutral").lower()
    confidence = str(row.get("h30_confidence") or "low").lower()
    try:
        fd = ForecastDirection(direction)
    except Exception:
        fd = ForecastDirection.NEUTRAL
    try:
        fc = ForecastConfidence(confidence)
    except Exception:
        fc = ForecastConfidence.LOW
    return ForecastResult(
        ts_utc=_parse_ts(str(row["ts_utc"])),
        symbol=str(row["symbol"]).upper(),
        horizons=[
            ForecastHorizon(
                horizon_minutes=30,
                direction=fd,
                confidence=fc,
                strength=float(row.get("h30_strength") or 0.0),
                indicators_aligned=int(row.get("h30_aligned") or 0),
                indicators_total=int(row.get("h30_total") or 0),
            )
        ],
        bb_width=(float(row["bb_width"]) if row.get("bb_width") is not None else None),
        bb_squeeze=row.get("bb_squeeze"),
        flags=[],
    )


def _fetch_today_rows(db: SupabaseDB, symbols: list[str]) -> list[dict[str, Any]]:
    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    query = (
        db.client.table("price_forecasts")
        .select("symbol,ts_utc,h30_direction,h30_confidence,h30_strength,h30_aligned,h30_total,bb_width,bb_squeeze")
        .gte("ts_utc", start_of_day.isoformat())
        .order("ts_utc", desc=False)
        .limit(5000)
    )
    if symbols:
        query = query.in_("symbol", symbols)
    return query.execute().data or []


def _make_breakout_confirm(net_threshold_pips: float, body_threshold_pips: float, monotonic_mode: str):
    def _confirm(forecast: ForecastResult, s5):
        direction = alt6._h30_direction(forecast)
        if len(s5) < 10 or direction not in {"up", "down"}:
            return False
        closes = [x[1] for x in s5]
        ps = alt6.pip_size(forecast.symbol)
        net = closes[-1] - closes[0]
        last4 = closes[-4:]
        body = max(closes) - min(closes)
        if direction == "up":
            monotonic_ok = (
                last4[0] <= last4[1] <= last4[2] <= last4[3]
                if monotonic_mode == "strict"
                else sum(1 for a, b in zip(last4, last4[1:]) if b >= a) >= 2
            )
            return (
                net >= net_threshold_pips * ps
                and monotonic_ok
                and closes[-1] >= max(closes[:-4])
                and body >= body_threshold_pips * ps
            )
        monotonic_ok = (
            last4[0] >= last4[1] >= last4[2] >= last4[3]
            if monotonic_mode == "strict"
            else sum(1 for a, b in zip(last4, last4[1:]) if b <= a) >= 2
        )
        return (
            net <= -net_threshold_pips * ps
            and monotonic_ok
            and closes[-1] <= min(closes[:-4])
            and body >= body_threshold_pips * ps
        )

    return _confirm


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--net-thresholds", default="0.4,0.6,0.8")
    parser.add_argument("--body-thresholds", default="0.5,0.8,1.0")
    parser.add_argument("--monotonic-modes", default="strict,relaxed")
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    db = SupabaseDB()
    if getattr(db, "disabled", False):
        raise RuntimeError("Supabase client not configured")

    symbols = [s.upper() for s in (args.symbols or [])]
    rows = _fetch_today_rows(db, symbols)
    prepared: list[tuple[ForecastResult, Any]] = []
    for row in rows:
        fc = _build_forecast(row)
        s5 = alt6.load_s5_window(db.client, symbol=fc.symbol, ts_utc=fc.ts_utc)
        prepared.append((fc, s5))

    orig_confirm = alt6.squeeze_breakout_confirm_strict_s5_v2
    combos: list[dict[str, Any]] = []
    try:
        for net_threshold in _parse_float_csv(args.net_thresholds):
            for body_threshold in _parse_float_csv(args.body_thresholds):
                for monotonic_mode in _parse_str_csv(args.monotonic_modes):
                    alt6.squeeze_breakout_confirm_strict_s5_v2 = _make_breakout_confirm(
                        net_threshold, body_threshold, monotonic_mode
                    )
                    emitted = 0
                    rejected = 0
                    undecided = 0
                    reason_counts: Counter[str] = Counter()
                    emitted_symbols: Counter[str] = Counter()
                    for fc, s5 in prepared:
                        direction, eligible, reason = alt6.compute_alt6_signal(fc, s5)
                        if direction is not None and eligible:
                            emitted += 1
                            emitted_symbols[fc.symbol] += 1
                        elif eligible is False:
                            rejected += 1
                            reason_counts[str(reason or "UNKNOWN")] += 1
                        else:
                            undecided += 1
                    combos.append(
                        {
                            "net_threshold_pips": net_threshold,
                            "body_threshold_pips": body_threshold,
                            "monotonic_mode": monotonic_mode,
                            "total_rows": len(prepared),
                            "emitted_signals": emitted,
                            "emitted_rate_pct": _pct(emitted, len(prepared)),
                            "rejected_candidates": rejected,
                            "rejected_rate_pct": _pct(rejected, len(prepared)),
                            "undecided_rows": undecided,
                            "undecided_rate_pct": _pct(undecided, len(prepared)),
                            "top_reject_reasons": dict(reason_counts.most_common(5)),
                            "emitted_by_symbol": dict(emitted_symbols),
                        }
                    )
    finally:
        alt6.squeeze_breakout_confirm_strict_s5_v2 = orig_confirm

    combos_sorted = sorted(
        combos,
        key=lambda x: (x["emitted_signals"], x["monotonic_mode"] == "relaxed", -x["rejected_candidates"]),
        reverse=True,
    )
    payload = {
        "rows_scanned": len(prepared),
        "symbols": symbols or sorted({fc.symbol for fc, _ in prepared}),
        "current_breakout_confirm": {
            "net_threshold_pips": 0.8,
            "body_threshold_pips": 1.0,
            "monotonic_mode": "strict",
        },
        "best_combinations": combos_sorted[: args.top],
    }

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
