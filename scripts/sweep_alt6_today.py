#!/usr/bin/env python3
"""
Sweep ALT6 thresholds on today's forecasts and compare emitted/rejected counts.

Usage:
  python scripts/sweep_alt6_today.py
  python scripts/sweep_alt6_today.py --since 2026-03-16T13:00:00+00:00
  python scripts/sweep_alt6_today.py --symbols EURUSD USDJPY
  python scripts/sweep_alt6_today.py --age-secs 20,30,40 --distance-pips 1.0,1.4,1.8 --extension-pips 1.4,1.8
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


def _parse_int_csv(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


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


def _fetch_rows(db: SupabaseDB, symbols: list[str], since: datetime) -> list[dict[str, Any]]:
    query = (
        db.client.table("price_forecasts")
        .select("symbol,ts_utc,h30_direction,h30_confidence,h30_strength,h30_aligned,h30_total,bb_width,bb_squeeze")
        .gte("ts_utc", since.isoformat())
        .order("ts_utc", desc=False)
        .limit(5000)
    )
    if symbols:
        query = query.in_("symbol", symbols)
    return query.execute().data or []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--age-secs", default="20,30,40,50")
    parser.add_argument("--distance-pips", default="1.0,1.4,1.8,2.2")
    parser.add_argument("--extension-pips", default="1.4,1.8,2.2")
    parser.add_argument("--since", default="")
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    db = SupabaseDB()
    if getattr(db, "disabled", False):
        raise RuntimeError("Supabase client not configured")

    symbols = [s.upper() for s in (args.symbols or [])]
    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    since = _parse_ts(args.since) if args.since else start_of_day
    rows = _fetch_rows(db, symbols, since)
    prepared: list[tuple[dict[str, Any], ForecastResult, Any]] = []
    for row in rows:
        fc = _build_forecast(row)
        s5 = alt6.load_s5_window(db.client, symbol=fc.symbol, ts_utc=fc.ts_utc)
        prepared.append((row, fc, s5))

    combos: list[dict[str, Any]] = []
    orig_age = alt6.DEFAULT_MAX_BREAKOUT_AGE_SEC
    orig_dist = alt6.DEFAULT_MAX_BREAKOUT_DISTANCE_PIPS
    orig_ext = alt6.DEFAULT_MAX_EXTENSION_PIPS
    try:
        for age in _parse_int_csv(args.age_secs):
            for distance in _parse_float_csv(args.distance_pips):
                for extension in _parse_float_csv(args.extension_pips):
                    alt6.DEFAULT_MAX_BREAKOUT_AGE_SEC = age
                    alt6.DEFAULT_MAX_BREAKOUT_DISTANCE_PIPS = distance
                    alt6.DEFAULT_MAX_EXTENSION_PIPS = extension
                    emitted = 0
                    rejected = 0
                    undecided = 0
                    reason_counts: Counter[str] = Counter()
                    emitted_symbols: Counter[str] = Counter()
                    for _, fc, s5 in prepared:
                        decision = alt6.compute_alt6_signal(fc, s5)
                        if decision.direction is not None and decision.trade_eligible:
                            emitted += 1
                            emitted_symbols[fc.symbol] += 1
                        elif decision.trade_eligible is False:
                            rejected += 1
                            reason_counts[str(decision.reject_reason or "UNKNOWN")] += 1
                        else:
                            undecided += 1
                    combos.append(
                        {
                            "age_sec": age,
                            "distance_pips": distance,
                            "extension_pips": extension,
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
        alt6.DEFAULT_MAX_BREAKOUT_AGE_SEC = orig_age
        alt6.DEFAULT_MAX_BREAKOUT_DISTANCE_PIPS = orig_dist
        alt6.DEFAULT_MAX_EXTENSION_PIPS = orig_ext

    combos_sorted = sorted(
        combos,
        key=lambda x: (x["emitted_signals"], -x["rejected_candidates"], x["distance_pips"], x["age_sec"]),
        reverse=True,
    )
    payload = {
        "since": since.isoformat(),
        "rows_scanned": len(prepared),
        "symbols": symbols or sorted({str(r.get("symbol") or "") for r, _, _ in prepared}),
        "current_defaults": {
            "age_sec": orig_age,
            "distance_pips": orig_dist,
            "extension_pips": orig_ext,
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
