#!/usr/bin/env python3
"""
Compare ALT6 rows for today's session before/after a deployment cutover.

Usage:
  python scripts/compare_alt6_today.py --cutover 2026-03-16T12:00:00+00:00
  python scripts/compare_alt6_today.py --cutover 2026-03-16T12:00:00+00:00 --symbols EURUSD USDJPY
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.storage.db import SupabaseDB


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bucket_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    emitted = [r for r in rows if r.get("h30_alt6_direction") is not None]
    rejected = [r for r in rows if r.get("h30_alt6_direction") is None and r.get("h30_alt6_trade_eligible") is False]
    undecided = [r for r in rows if r.get("h30_alt6_direction") is None and r.get("h30_alt6_trade_eligible") is None]

    emitted_by_symbol = Counter(str(r.get("symbol") or "") for r in emitted)
    rejected_by_symbol = Counter(str(r.get("symbol") or "") for r in rejected)

    return {
        "total_rows": len(rows),
        "emitted_signals": len(emitted),
        "rejected_candidates": len(rejected),
        "undecided_rows": len(undecided),
        "emitted_by_symbol": dict(emitted_by_symbol),
        "rejected_by_symbol": dict(rejected_by_symbol),
        "examples_emitted": emitted[:10],
        "examples_rejected": rejected[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutover", required=True, help="UTC timestamp separating old/new behavior")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    db = SupabaseDB()
    if getattr(db, "disabled", False):
        raise RuntimeError("Supabase client not configured")

    now = datetime.now(timezone.utc)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cutover = _parse_ts(args.cutover)
    symbols = [s.upper() for s in (args.symbols or [])]

    query = (
        db.client.table("price_forecasts")
        .select("symbol,ts_utc,h30_direction,h30_alt6_direction,h30_alt6_trade_eligible,flags")
        .gte("ts_utc", start_of_day.isoformat())
        .lte("ts_utc", now.isoformat())
        .order("ts_utc", desc=False)
        .limit(5000)
    )
    if symbols:
        query = query.in_("symbol", symbols)

    rows = query.execute().data or []
    pre = [r for r in rows if _parse_ts(str(r["ts_utc"])) < cutover]
    post = [r for r in rows if _parse_ts(str(r["ts_utc"])) >= cutover]

    result = {
        "start_of_day": start_of_day.isoformat(),
        "cutover": cutover.isoformat(),
        "symbols": symbols or sorted({str(r.get("symbol") or "") for r in rows}),
        "pre_cutover": _bucket_stats(pre),
        "post_cutover": _bucket_stats(post),
        "delta": {
            "emitted_signals": _bucket_stats(post)["emitted_signals"] - _bucket_stats(pre)["emitted_signals"],
            "rejected_candidates": _bucket_stats(post)["rejected_candidates"] - _bucket_stats(pre)["rejected_candidates"],
        },
    }

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
