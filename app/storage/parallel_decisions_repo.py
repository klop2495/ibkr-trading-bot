"""
Repository for parallel_decisions table.

Phase 0: Simple insert-only operations for shadow mode logging.
"""

from uuid import uuid4

from app.models.parallel_decision import ParallelDecisionV1
from app.storage.db import SupabaseDB


class ParallelDecisionsRepo:
    """Repository for parallel_decisions table."""
    
    table = "parallel_decisions"

    def __init__(self, db: SupabaseDB) -> None:
        self.db = db

    def _new_id(self) -> str:
        return str(uuid4())

    def _is_unique_violation(self, exc: Exception) -> bool:
        """Check if exception is a unique constraint violation."""
        code = getattr(exc, "code", None) or getattr(exc, "sqlstate", None)
        if code == "23505":
            return True
        for attr in ("args", "detail", "details", "context"):
            val = getattr(exc, attr, None)
            if val and "23505" in str(val):
                return True
            if val and "duplicate key value violates unique constraint" in str(val).lower():
                return True
        msg = str(exc).lower()
        if "duplicate key value violates unique constraint" in msg:
            return True
        return False

    def insert(self, decision: ParallelDecisionV1) -> str:
        """
        Insert a parallel decision record.
        
        Returns:
            str: The UUID of the inserted record.
        """
        payload = decision.to_db_row()
        if "id" not in payload or not payload.get("id"):
            payload["id"] = self._new_id()
        
        try:
            res = self.db.client.table(self.table).insert(payload).execute()
            rows = getattr(res, "data", None) or []
            if rows:
                return rows[0].get("id") or payload["id"]
            return payload["id"]
        except Exception as exc:
            # For shadow mode, we don't want failures to break the main loop
            # Log and return a placeholder ID
            if self._is_unique_violation(exc):
                # Duplicate is acceptable in shadow mode
                return payload["id"]
            # Re-raise other errors
            raise

    def insert_bulk(self, decisions: list[ParallelDecisionV1]) -> list[str]:
        """
        Bulk insert parallel decisions.
        
        Returns:
            list[str]: List of UUIDs for inserted records.
        """
        if not decisions:
            return []
        
        payloads = []
        ids = []
        for dec in decisions:
            row = dec.to_db_row()
            if "id" not in row or not row.get("id"):
                row["id"] = self._new_id()
            ids.append(row["id"])
            payloads.append(row)
        
        try:
            res = self.db.client.table(self.table).insert(payloads).execute()
            rows = getattr(res, "data", None) or []
            if rows and len(rows) == len(payloads):
                return [r.get("id") or ids[i] for i, r in enumerate(rows)]
            return ids
        except Exception:
            # Fallback to individual inserts on bulk failure
            result_ids = []
            for dec in decisions:
                try:
                    result_ids.append(self.insert(dec))
                except Exception:
                    result_ids.append(self._new_id())  # placeholder
            return result_ids

    def get_recent(self, limit: int = 50) -> list[dict]:
        """
        Get recent parallel decisions for dashboard.
        
        Returns:
            list[dict]: List of decision rows.
        """
        try:
            res = (
                self.db.client.table(self.table)
                .select("*")
                .order("ts_utc", desc=True)
                .limit(limit)
                .execute()
            )
            return getattr(res, "data", None) or []
        except Exception:
            return []

    def get_comparison_stats(self, days: int = 7) -> dict:
        """
        Get comparison statistics for dashboard.
        
        This is a simple implementation; Phase 4 will add proper analytics.
        
        Returns:
            dict: Statistics for rules vs gpt vs hybrid.
        """
        try:
            # For now, just count by executed_strategy
            res = (
                self.db.client.table(self.table)
                .select("executed_strategy, rules_signal, gpt_signal, hybrid_signal")
                .order("ts_utc", desc=True)
                .limit(1000)  # Last 1000 decisions
                .execute()
            )
            rows = getattr(res, "data", None) or []
            
            stats = {
                "total": len(rows),
                "rules_signals": 0,
                "gpt_signals": 0,
                "hybrid_signals": 0,
                "agreement_count": 0,
            }
            
            for row in rows:
                if row.get("rules_signal") not in (None, "HOLD"):
                    stats["rules_signals"] += 1
                if row.get("gpt_signal") not in (None, "HOLD"):
                    stats["gpt_signals"] += 1
                if row.get("hybrid_signal") not in (None, "HOLD"):
                    stats["hybrid_signals"] += 1
                # Agreement: rules == gpt
                if row.get("rules_signal") == row.get("gpt_signal"):
                    stats["agreement_count"] += 1
            
            stats["agreement_rate"] = (
                stats["agreement_count"] / stats["total"] 
                if stats["total"] > 0 else 0.0
            )
            
            return stats
        except Exception:
            return {"total": 0, "error": "failed to fetch stats"}
