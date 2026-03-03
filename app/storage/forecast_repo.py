"""
Forecast Repository — Supabase CRUD for price_forecasts table.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from app.models.forecast import ForecastResult
from app.storage.db import SupabaseDB


class ForecastRepo:
    table = "price_forecasts"

    def __init__(self, db: SupabaseDB) -> None:
        self.db = db

    def insert(self, forecast: ForecastResult) -> dict:
        """Insert a single forecast."""
        payload = forecast.to_db_row()
        try:
            res = self.db.client.table(self.table).insert(payload).execute()
            return {"count": len(res.data or []), "data": res.data}
        except Exception as exc:
            # Log but don't crash — forecast is non-critical
            return {"count": 0, "error": str(exc)}

    def insert_batch(self, forecasts: List[ForecastResult]) -> dict:
        """Insert multiple forecasts, skipping duplicates where direction hasn't changed."""
        if not forecasts:
            return {"count": 0}

        # Dedup: check latest forecast per symbol, skip if identical directions
        symbols = list({f.symbol for f in forecasts})
        existing: Dict[str, Any] = {}
        try:
            res = (
                self.db.client.table(self.table)
                .select(
                    "symbol, h30_direction, h60_direction, h240_direction, h1440_direction, "
                    "h30_alt2_direction, h30_alt2_trade_eligible, h30_alt3_direction"
                )
                .in_("symbol", symbols)
                .order("ts_utc", desc=True)
                .limit(len(symbols) * 2)
                .execute()
            )
            for row in (res.data or []):
                sym = row.get("symbol")
                if sym and sym not in existing:
                    existing[sym] = (
                        row.get("h30_direction"),
                        row.get("h60_direction"),
                        row.get("h240_direction"),
                        row.get("h1440_direction"),
                        row.get("h30_alt2_direction"),
                        row.get("h30_alt2_trade_eligible"),
                        row.get("h30_alt3_direction"),
                    )
        except Exception:
            pass  # If lookup fails, insert all

        filtered: list = []
        for f in forecasts:
            prev = existing.get(f.symbol)
            if prev:
                current = tuple(
                    (h.direction.value if h else None)
                    for h_min in [30, 60, 240, 1440]
                    for h in [f.horizon(h_min)]
                ) + (f.h30_alt2_direction, f.h30_alt2_trade_eligible, f.h30_alt3_direction)
                if current == prev:
                    continue  # Skip — identical directions + alt signals
            filtered.append(f)

        if not filtered:
            return {"count": 0, "skipped": len(forecasts)}

        payloads = [f.to_db_row() for f in filtered]
        try:
            res = self.db.client.table(self.table).insert(payloads).execute()
            return {"count": len(res.data or []), "skipped": len(forecasts) - len(filtered)}
        except Exception as exc:
            return {"count": 0, "error": str(exc)}

    def get_latest(self, symbol: str, limit: int = 1) -> list:
        """Get latest forecasts for a symbol."""
        res = (
            self.db.client.table(self.table)
            .select("*")
            .eq("symbol", symbol.upper())
            .order("ts_utc", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []

    def get_all_latest(self) -> list:
        """
        Get the most recent forecast for each symbol.
        Fetches recent rows and deduplicates by symbol.
        """
        res = (
            self.db.client.table(self.table)
            .select("*")
            .order("ts_utc", desc=True)
            .limit(200)
            .execute()
        )
        seen: set = set()
        result: list = []
        for row in res.data or []:
            sym = row.get("symbol")
            if sym and sym not in seen:
                seen.add(sym)
                result.append(row)
        return result

    def get_history(self, symbol: str, hours: int = 24) -> list:
        """Get forecast history for a symbol within time range."""
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        res = (
            self.db.client.table(self.table)
            .select("*")
            .eq("symbol", symbol.upper())
            .gte("ts_utc", since)
            .order("ts_utc", desc=True)
            .limit(500)
            .execute()
        )
        return res.data or []

    def get_history_all(
        self,
        *,
        symbol: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> dict:
        """
        Get forecast history with optional filters.
        Returns {rows, total} for pagination.
        """
        query = self.db.client.table(self.table).select("*", count="exact")
        if symbol:
            query = query.eq("symbol", symbol.upper())
        if since:
            query = query.gte("ts_utc", since)
        if until:
            query = query.lte("ts_utc", until)
        query = query.order("ts_utc", desc=True).range(offset, offset + limit - 1)
        res = query.execute()
        return {
            "rows": res.data or [],
            "total": res.count if hasattr(res, "count") and res.count is not None else len(res.data or []),
        }

    def get_accuracy_stats(
        self,
        *,
        symbol: Optional[str] = None,
        hours: int = 168,
    ) -> dict:
        """
        Compute accuracy stats from verified forecasts.
        Returns accuracy per horizon and overall.
        """
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        query = (
            self.db.client.table(self.table)
            .select(
                "h30_correct, h60_correct, h240_correct, h1440_correct, "
                "h30_direction, h60_direction, h240_direction, h1440_direction"
            )
            .gte("ts_utc", since)
            .not_.is_("verified_at", "null")
        )
        if symbol:
            query = query.eq("symbol", symbol.upper())
        res = query.limit(2000).execute()
        rows = res.data or []

        stats: Dict[str, Dict[str, int]] = {}
        for prefix in ("h30", "h60", "h240", "h1440"):
            correct_key = f"{prefix}_correct"
            dir_key = f"{prefix}_direction"
            total = 0
            correct = 0
            for row in rows:
                d = row.get(dir_key)
                c = row.get(correct_key)
                if d and d != "neutral" and c is not None:
                    total += 1
                    if c:
                        correct += 1
            stats[prefix] = {
                "total": total,
                "correct": correct,
                "accuracy": round(correct / total, 4) if total > 0 else 0.0,
            }

        all_total = sum(s["total"] for s in stats.values())
        all_correct = sum(s["correct"] for s in stats.values())
        stats["overall"] = {
            "total": all_total,
            "correct": all_correct,
            "accuracy": round(all_correct / all_total, 4) if all_total > 0 else 0.0,
        }
        return stats
