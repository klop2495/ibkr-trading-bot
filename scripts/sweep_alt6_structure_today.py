#!/usr/bin/env python3
"""
Sweep ALT6 scored-structure + timing thresholds on today's forecasts.

Usage:
  python scripts/sweep_alt6_structure_today.py
  python scripts/sweep_alt6_structure_today.py --since 2026-03-17T00:00:00+00:00
  python scripts/sweep_alt6_structure_today.py --soft-net-pips 0.05,0.1,0.15 \
      --soft-body-pips 0.2,0.4,0.6 --soft-break-margin-pips 0.2,0.4,0.6 \
      --structure-min-score 1,2,3 --age-secs 90,120,150
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
from app.models.forecast import ForecastConfidence, ForecastDirection, ForecastHorizon, ForecastResult
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
        adx_value=(float(row["adx_value"]) if row.get("adx_value") is not None else None),
        mtf_conflict=row.get("mtf_conflict"),
        flags=[],
    )


def _fetch_rows(db: SupabaseDB, symbols: list[str], since: datetime) -> list[dict[str, Any]]:
    query = (
        db.client.table("price_forecasts")
        .select("symbol,ts_utc,h30_direction,h30_confidence,h30_strength,h30_aligned,h30_total,bb_width,bb_squeeze,adx_value,mtf_conflict")
        .gte("ts_utc", since.isoformat())
        .order("ts_utc", desc=False)
        .limit(5000)
    )
    if symbols:
        query = query.in_("symbol", symbols)
    return query.execute().data or []


def _make_structure_stage(min_score: int):
    def _stage(forecast: ForecastResult, s5):
        width_ok = forecast.bb_width is not None and forecast.bb_width <= 0.008
        if not (bool(forecast.bb_squeeze) or width_ok):
            return "NO_SQUEEZE_CONTEXT", ()
        passed, score, metrics = alt6.soft_structure_metrics_s5_v43(forecast, s5)
        flags = (f"ALT6_STRUCTURE_SCORE:{score}",) + tuple(f"ALT6_STRUCTURE_{m}" for m in metrics)
        if not passed or score < min_score:
            return "STRUCTURE_SOFT_FAIL", flags
        return None, flags

    return _stage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--since", default="")
    parser.add_argument("--soft-net-pips", default="0.05,0.1,0.15")
    parser.add_argument("--soft-body-pips", default="0.2,0.4,0.6")
    parser.add_argument("--soft-break-margin-pips", default="0.2,0.4,0.6")
    parser.add_argument("--structure-min-score", default="1,2,3")
    parser.add_argument("--age-secs", default="90,120,150")
    parser.add_argument("--distance-pips", default="1.4,1.8,2.2")
    parser.add_argument("--extension-pips", default="1.4,1.8,2.2")
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
    prepared: list[tuple[ForecastResult, Any]] = []
    for row in rows:
        fc = _build_forecast(row)
        s5 = alt6.load_s5_window(db.client, symbol=fc.symbol, ts_utc=fc.ts_utc)
        prepared.append((fc, s5))

    orig_soft_net = alt6.DEFAULT_SOFT_NET_PIPS
    orig_soft_body = alt6.DEFAULT_SOFT_BODY_PIPS
    orig_soft_margin = alt6.DEFAULT_SOFT_BREAK_MARGIN_PIPS
    orig_age = alt6.DEFAULT_MAX_BREAKOUT_AGE_SEC
    orig_dist = alt6.DEFAULT_MAX_BREAKOUT_DISTANCE_PIPS
    orig_ext = alt6.DEFAULT_MAX_EXTENSION_PIPS
    orig_structure_stage = alt6._structure_stage

    combos: list[dict[str, Any]] = []
    try:
        for soft_net in _parse_float_csv(args.soft_net_pips):
            for soft_body in _parse_float_csv(args.soft_body_pips):
                for soft_margin in _parse_float_csv(args.soft_break_margin_pips):
                    for min_score in _parse_int_csv(args.structure_min_score):
                        for age in _parse_int_csv(args.age_secs):
                            for distance in _parse_float_csv(args.distance_pips):
                                for extension in _parse_float_csv(args.extension_pips):
                                    alt6.DEFAULT_SOFT_NET_PIPS = soft_net
                                    alt6.DEFAULT_SOFT_BODY_PIPS = soft_body
                                    alt6.DEFAULT_SOFT_BREAK_MARGIN_PIPS = soft_margin
                                    alt6.DEFAULT_MAX_BREAKOUT_AGE_SEC = age
                                    alt6.DEFAULT_MAX_BREAKOUT_DISTANCE_PIPS = distance
                                    alt6.DEFAULT_MAX_EXTENSION_PIPS = extension
                                    alt6._structure_stage = _make_structure_stage(min_score)

                                    emitted = 0
                                    rejected = 0
                                    undecided = 0
                                    reason_counts: Counter[str] = Counter()
                                    emitted_symbols: Counter[str] = Counter()
                                    for fc, s5 in prepared:
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
                                            "soft_net_pips": soft_net,
                                            "soft_body_pips": soft_body,
                                            "soft_break_margin_pips": soft_margin,
                                            "structure_min_score": min_score,
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
        alt6.DEFAULT_SOFT_NET_PIPS = orig_soft_net
        alt6.DEFAULT_SOFT_BODY_PIPS = orig_soft_body
        alt6.DEFAULT_SOFT_BREAK_MARGIN_PIPS = orig_soft_margin
        alt6.DEFAULT_MAX_BREAKOUT_AGE_SEC = orig_age
        alt6.DEFAULT_MAX_BREAKOUT_DISTANCE_PIPS = orig_dist
        alt6.DEFAULT_MAX_EXTENSION_PIPS = orig_ext
        alt6._structure_stage = orig_structure_stage

    combos_sorted = sorted(
        combos,
        key=lambda x: (x["emitted_signals"], -x["rejected_candidates"], -x["structure_min_score"], -x["soft_net_pips"]),
        reverse=True,
    )
    payload = {
        "since": since.isoformat(),
        "rows_scanned": len(prepared),
        "symbols": symbols or sorted({fc.symbol for fc, _ in prepared}),
        "current_defaults": {
            "soft_net_pips": orig_soft_net,
            "soft_body_pips": orig_soft_body,
            "soft_break_margin_pips": orig_soft_margin,
            "structure_min_score": 2,
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
