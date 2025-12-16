from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.models import (
    MarketSnapshot,
    Signal,
    Decision,
    ExecutionReport,
)

from app.storage.db import SupabaseDB


class BaseRepo:
    def __init__(self, db: SupabaseDB) -> None:
        self.db = db


class SnapshotsRepo(BaseRepo):
    table = "market_snapshots"

    def insert(self, snap: MarketSnapshot) -> dict:
        payload = {
            "schema_version": snap.schema_version,
            "ts": snap.timestamp.isoformat(),
            "symbol": snap.symbol,
            "timeframe": snap.timeframe,
            "close": snap.close,
            "atr": snap.atr,
            "rsi": snap.rsi,
            "ma_fast": snap.ma_fast,
            "ma_slow": snap.ma_slow,
            "spread": snap.spread,
        }
        res = self.db.client.table(self.table).insert(payload).execute()
        return {"count": len(res.data or []), "data": res.data}


class SignalsRepo(BaseRepo):
    table = "signals"

    def insert(self, sig: Signal) -> dict:
        payload = {
            "schema_version": sig.schema_version,
            "ts": datetime.utcnow().isoformat(),
            "symbol": sig.symbol,
            "raw_signal": sig.raw_signal,
            "entry_triggered": sig.entry_triggered,
            "sl_pips": sig.sl_pips,
            "tp_pips": sig.tp_pips,
            "confidence": sig.confidence,
        }
        res = self.db.client.table(self.table).insert(payload).execute()
        return {"count": len(res.data or []), "data": res.data}


class DecisionsRepo(BaseRepo):
    table = "decisions"

    def insert(self, dec: Decision) -> dict:
        payload = {
            "decision_id": str(dec.decision_id),
            "schema_version": dec.schema_version,
            "ts": datetime.utcnow().isoformat(),
            "symbol": dec.symbol,
            "action": dec.action,
            "direction": dec.direction,
            "volume": dec.volume,
            "sl_price": dec.sl_price,
            "tp_price": dec.tp_price,
            "reason_codes": dec.reason_codes,
        }
        res = self.db.client.table(self.table).insert(payload).execute()
        return {"count": len(res.data or []), "data": res.data}

    def exists(self, decision_id: str) -> bool:
        """
        Idempotency helper: check if decision_id already exists.
        """
        res = (
            self.db.client.table(self.table)
            .select("decision_id")
            .eq("decision_id", decision_id)
            .limit(1)
            .execute()
        )
        return bool(res.data)


class ExecutionReportsRepo(BaseRepo):
    table = "execution_reports"

    def insert(self, rep: ExecutionReport) -> dict:
        payload = {
            "schema_version": rep.schema_version,
            "ts": datetime.utcnow().isoformat(),
            "decision_id": str(rep.decision_id),
            "order_id": rep.order_id,
            "status": rep.status,
            "message": rep.message,
        }
        res = self.db.client.table(self.table).insert(payload).execute()
        return {"count": len(res.data or []), "data": res.data}


class RiskEventsRepo(BaseRepo):
    table = "risk_events"

    def insert(
        self,
        event_type: str,
        severity: str = "info",
        symbol: Optional[str] = None,
        message: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> dict:
        payload = {
            "ts": datetime.utcnow().isoformat(),
            "event_type": event_type,
            "severity": severity,
            "symbol": symbol,
            "message": message,
            "data": data or {},
        }
        res = self.db.client.table(self.table).insert(payload).execute()
        return {"count": len(res.data or []), "data": res.data}


def make_repos(db: SupabaseDB) -> dict[str, Any]:
    """
    Convenience factory.
    """
    return {
        "snapshots": SnapshotsRepo(db),
        "signals": SignalsRepo(db),
        "decisions": DecisionsRepo(db),
        "execution_reports": ExecutionReportsRepo(db),
        "risk_events": RiskEventsRepo(db),
    }
