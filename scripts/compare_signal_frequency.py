#!/usr/bin/env python3
"""
Compare historical signal frequency on available market_snapshots data.

Important limitation:
- market_snapshots does not persist historical OHLC
- therefore this replay uses snapshot-based signal generation only
- structural continuation scoring is only evaluated via regime/fallback path

Usage:
  python scripts/compare_signal_frequency.py --hours 720
  python scripts/compare_signal_frequency.py --hours 168 --symbols EURUSD GBPUSD
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.bot_settings import DEFAULT_SYMBOLS
from app.models.signals_params import SignalsParams
from app.models.snapshot import MarketSnapshot
from app.signals.engine_v1 import SignalEngineV1
from app.storage.bot_settings_repo import BotSettingsRepo
from app.storage.db import SupabaseDB


@dataclass
class ReplayStats:
    previews: int = 0
    setup_candidates: int = 0
    entry_triggered: int = 0
    long_entries: int = 0
    short_entries: int = 0
    high_confidence: int = 0
    normal_confidence: int = 0
    low_confidence: int = 0

    def add_preview(self, preview) -> None:
        self.previews += 1
        if preview.setup_present and str(preview.setup_type) != "SetupType.NO_TRADE":
            self.setup_candidates += 1
        if preview.entry_triggered:
            self.entry_triggered += 1
            direction = str(preview.direction.value if hasattr(preview.direction, "value") else preview.direction).lower()
            if direction == "long":
                self.long_entries += 1
            elif direction == "short":
                self.short_entries += 1
        confidence = str(preview.confidence.value if hasattr(preview.confidence, "value") else preview.confidence).lower()
        if confidence == "high":
            self.high_confidence += 1
        elif confidence == "normal":
            self.normal_confidence += 1
        else:
            self.low_confidence += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "previews": self.previews,
            "setup_candidates": self.setup_candidates,
            "entry_triggered": self.entry_triggered,
            "long_entries": self.long_entries,
            "short_entries": self.short_entries,
            "high_confidence": self.high_confidence,
            "normal_confidence": self.normal_confidence,
            "low_confidence": self.low_confidence,
            "setup_rate_pct": _pct(self.setup_candidates, self.previews),
            "entry_rate_pct": _pct(self.entry_triggered, self.previews),
        }


def _pct(part: int, total: int) -> float:
    if not total:
        return 0.0
    return round((part / total) * 100.0, 1)


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_settings(db: SupabaseDB) -> tuple[SignalsParams, list[str]]:
    owner = os.getenv("BOT_OWNER_USER_ID", "").strip()
    if owner:
        settings = BotSettingsRepo(db).get(owner)
        params = getattr(settings, "signals_params", None)
        symbols = list(getattr(settings, "symbols", []) or [])
        if params and params.is_configured():
            return params, symbols or list(DEFAULT_SYMBOLS)

    rows = (
        db.client.table("bot_settings")
        .select("signals_params,symbols")
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise RuntimeError("bot_settings row not found")
    row = rows[0]
    params = SignalsParams.model_validate(row.get("signals_params") or {})
    if not params.is_configured():
        raise RuntimeError("signals_params are not configured in bot_settings")
    symbols = row.get("symbols") or []
    return params, symbols or list(DEFAULT_SYMBOLS)


def _build_old_params(current: SignalsParams) -> SignalsParams:
    payload = current.model_dump()
    payload["scoring"]["min_regime_score"] = 0.0
    payload["scoring"]["min_setup_score"] = 0.0
    payload["scoring"]["min_entry_score"] = 0.0
    payload["scoring"]["high_confidence_score"] = 70.0
    return SignalsParams.model_validate(payload)


def _fetch_snapshot_rows(db: SupabaseDB, *, since_iso: str, symbols: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    chunk_size = 5
    select_cols = "symbol,timeframe,ts,close,atr,rsi,ma_fast,ma_slow,spread,data_quality"
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i : i + chunk_size]
        res = (
            db.client.table("market_snapshots")
            .select(select_cols)
            .in_("symbol", chunk)
            .in_("timeframe", ["M15", "H1", "H4"])
            .gte("ts", since_iso)
            .order("ts", desc=False)
            .limit(50000)
            .execute()
        )
        rows.extend(res.data or [])
    return rows


def _index_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    out: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        timeframe = str(row.get("timeframe") or "").upper()
        if not symbol or timeframe not in {"M15", "H1", "H4"}:
            continue
        out[symbol][timeframe].append(row)
    return out


def _row_to_snapshot(row: dict[str, Any]) -> MarketSnapshot:
    return MarketSnapshot(
        schema_version=1,
        timestamp=_parse_ts(str(row["ts"])),
        symbol=str(row["symbol"]).upper(),
        timeframe=str(row["timeframe"]).upper(),
        close=float(row["close"]),
        atr=float(row["atr"]),
        rsi=float(row["rsi"]),
        ma_fast=float(row["ma_fast"]),
        ma_slow=float(row["ma_slow"]),
        spread=float(row["spread"]),
        data_quality=str(row.get("data_quality") or "ok").lower(),
    )


def _latest_at_or_before(rows: list[dict[str, Any]], target_ts: datetime) -> dict[str, Any] | None:
    candidate = None
    for row in rows:
        row_ts = _parse_ts(str(row["ts"]))
        if row_ts <= target_ts:
            candidate = row
        else:
            break
    return candidate


def _replay_symbol(symbol: str, tf_rows: dict[str, list[dict[str, Any]]], old_engine: SignalEngineV1, new_engine: SignalEngineV1):
    old_stats = ReplayStats()
    new_stats = ReplayStats()

    m15_rows = tf_rows.get("M15", [])
    h1_rows = tf_rows.get("H1", [])
    h4_rows = tf_rows.get("H4", [])
    if not m15_rows or not h1_rows or not h4_rows:
        return old_stats, new_stats

    for m15 in m15_rows:
        ts = _parse_ts(str(m15["ts"]))
        h1 = _latest_at_or_before(h1_rows, ts)
        h4 = _latest_at_or_before(h4_rows, ts)
        if h1 is None or h4 is None:
            continue

        snapshots = {
            "M15": _row_to_snapshot(m15),
            "H1": _row_to_snapshot(h1),
            "H4": _row_to_snapshot(h4),
        }
        old_preview = old_engine.compute_preview_for_symbol(symbol, snapshots, warmup_ready=True)
        new_preview = new_engine.compute_preview_for_symbol(symbol, snapshots, warmup_ready=True)
        old_stats.add_preview(old_preview)
        new_stats.add_preview(new_preview)

    return old_stats, new_stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=720)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--output", type=str, default="")
    args = parser.parse_args()

    db = SupabaseDB()
    if getattr(db, "disabled", False):
        raise RuntimeError("Supabase client not configured")

    current_params, configured_symbols = _load_settings(db)
    symbols = [s.upper() for s in (args.symbols or configured_symbols or list(DEFAULT_SYMBOLS))]
    since_dt = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    since_iso = since_dt.isoformat()

    old_engine = SignalEngineV1(_build_old_params(current_params))
    new_engine = SignalEngineV1(deepcopy(current_params))

    rows = _fetch_snapshot_rows(db, since_iso=since_iso, symbols=symbols)
    indexed = _index_rows(rows)

    by_symbol: dict[str, Any] = {}
    aggregate_old = ReplayStats()
    aggregate_new = ReplayStats()

    for symbol in symbols:
        old_stats, new_stats = _replay_symbol(symbol, indexed.get(symbol, {}), old_engine, new_engine)
        by_symbol[symbol] = {
            "old_baseline": old_stats.to_dict(),
            "new_scoring": new_stats.to_dict(),
            "delta": {
                "entry_triggered": new_stats.entry_triggered - old_stats.entry_triggered,
                "entry_triggered_pct_points": round(
                    _pct(new_stats.entry_triggered, new_stats.previews) - _pct(old_stats.entry_triggered, old_stats.previews), 1
                ),
            },
        }
        aggregate_old.previews += old_stats.previews
        aggregate_old.setup_candidates += old_stats.setup_candidates
        aggregate_old.entry_triggered += old_stats.entry_triggered
        aggregate_old.long_entries += old_stats.long_entries
        aggregate_old.short_entries += old_stats.short_entries
        aggregate_old.high_confidence += old_stats.high_confidence
        aggregate_old.normal_confidence += old_stats.normal_confidence
        aggregate_old.low_confidence += old_stats.low_confidence

        aggregate_new.previews += new_stats.previews
        aggregate_new.setup_candidates += new_stats.setup_candidates
        aggregate_new.entry_triggered += new_stats.entry_triggered
        aggregate_new.long_entries += new_stats.long_entries
        aggregate_new.short_entries += new_stats.short_entries
        aggregate_new.high_confidence += new_stats.high_confidence
        aggregate_new.normal_confidence += new_stats.normal_confidence
        aggregate_new.low_confidence += new_stats.low_confidence

    result = {
        "since": since_iso,
        "symbols": symbols,
        "limitation": "Historical structural replay is unavailable because market_snapshots does not persist OHLC. This comparison uses snapshot-based replay only.",
        "aggregate": {
            "old_baseline": aggregate_old.to_dict(),
            "new_scoring": aggregate_new.to_dict(),
            "delta": {
                "entry_triggered": aggregate_new.entry_triggered - aggregate_old.entry_triggered,
                "entry_rate_pct_points": round(
                    _pct(aggregate_new.entry_triggered, aggregate_new.previews)
                    - _pct(aggregate_old.entry_triggered, aggregate_old.previews),
                    1,
                ),
            },
        },
        "by_symbol": by_symbol,
    }

    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
