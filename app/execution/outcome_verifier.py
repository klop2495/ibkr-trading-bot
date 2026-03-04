from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.storage.db import SupabaseDB


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def _pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in (symbol or "").upper() else 0.0001


def _contains_append_only_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "deny_update_delete" in msg or "append-only" in msg or "update or delete" in msg


@dataclass
class OutcomeVerifyResult:
    scanned: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    blocked_append_only: bool = False


class ExecutionOutcomeVerifier:
    """
    Fills outcome_result/outcome_pips for parallel_decisions using market_snapshots.
    """

    def __init__(self, db: SupabaseDB):
        self.db = db

    def _fetch_price_before(self, symbol: str, ts_utc: datetime) -> Optional[float]:
        res = (
            self.db.client.table("market_snapshots")
            .select("close")
            .eq("symbol", symbol)
            .eq("timeframe", "M15")
            .lte("ts", ts_utc.isoformat())
            .order("ts", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return None
        price = rows[0].get("close")
        return float(price) if price is not None else None

    def _fetch_price_after(self, symbol: str, ts_utc: datetime) -> Optional[float]:
        res = (
            self.db.client.table("market_snapshots")
            .select("close")
            .eq("symbol", symbol)
            .eq("timeframe", "M15")
            .gte("ts", ts_utc.isoformat())
            .order("ts", desc=False)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return None
        price = rows[0].get("close")
        return float(price) if price is not None else None

    def verify_pending(self, horizon_minutes: int = 240, limit: int = 200) -> OutcomeVerifyResult:
        result = OutcomeVerifyResult()
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=horizon_minutes)
        rows_res = (
            self.db.client.table("parallel_decisions")
            .select("id, ts_utc, symbol, executed_signal, hybrid_signal, rules_signal")
            .is_("outcome_result", "null")
            .in_("executed_signal", ["LONG", "SHORT"])
            .lte("ts_utc", cutoff.isoformat())
            .order("ts_utc", desc=False)
            .limit(limit)
            .execute()
        )
        rows = getattr(rows_res, "data", None) or []
        if not rows:
            return result

        for row in rows:
            result.scanned += 1
            try:
                row_id = row.get("id")
                symbol = str(row.get("symbol") or "")
                ts_utc = _parse_ts(row.get("ts_utc"))
                signal = (
                    (row.get("executed_signal") or row.get("hybrid_signal") or row.get("rules_signal") or "")
                    .upper()
                )
                if not row_id or not symbol or ts_utc is None or signal not in ("LONG", "SHORT"):
                    result.skipped += 1
                    continue

                verify_ts = ts_utc + timedelta(minutes=horizon_minutes)
                entry_price = self._fetch_price_before(symbol, ts_utc)
                exit_price = self._fetch_price_after(symbol, verify_ts)
                if entry_price is None or exit_price is None:
                    result.skipped += 1
                    continue

                delta = exit_price - entry_price
                if signal == "LONG":
                    directional_delta = delta
                else:
                    directional_delta = -delta

                pips = directional_delta / _pip_size(symbol)
                if directional_delta > 0:
                    outcome = "win"
                elif directional_delta < 0:
                    outcome = "loss"
                else:
                    outcome = "breakeven"

                self.db.client.table("parallel_decisions").update(
                    {"outcome_result": outcome, "outcome_pips": float(round(pips, 3))}
                ).eq("id", row_id).execute()
                result.updated += 1
            except Exception as exc:
                result.errors += 1
                if _contains_append_only_error(exc):
                    result.blocked_append_only = True
                    break

        return result


def evaluate_execution_accuracy(
    db: SupabaseDB,
    *,
    window_hours: int,
    min_samples: int,
    min_accuracy: float,
) -> dict:
    """
    Rolling accuracy guard for execution using verified parallel_decisions outcomes.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    res = (
        db.client.table("parallel_decisions")
        .select("outcome_result")
        .gte("ts_utc", since.isoformat())
        .in_("outcome_result", ["win", "loss", "breakeven"])
        .limit(5000)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    n = len(rows)
    wins = sum(1 for r in rows if (r.get("outcome_result") or "").lower() == "win")
    accuracy = (wins / n) if n > 0 else None

    blocked = bool(n >= min_samples and accuracy is not None and accuracy < min_accuracy)
    return {
        "samples": n,
        "wins": wins,
        "accuracy": accuracy,
        "blocked": blocked,
        "window_hours": window_hours,
        "min_samples": min_samples,
        "min_accuracy": min_accuracy,
    }
