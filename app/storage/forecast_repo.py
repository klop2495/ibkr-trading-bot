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
        """Insert multiple forecasts in one call."""
        if not forecasts:
            return {"count": 0}
        payloads = [f.to_db_row() for f in forecasts]
        try:
            res = self.db.client.table(self.table).insert(payloads).execute()
            return {"count": len(res.data or []), "data": res.data}
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
