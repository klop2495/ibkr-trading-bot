from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.models import (
    MarketSnapshot,
    Signal,
    ExecutionReport,
)
from app.models.signal_preview import SignalPreviewV1
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

    def insert_decision(self, dec) -> str:
        payload = dec.to_db_row()
        try:
            res = self.db.client.table(self.table).insert(payload).select("id").execute()
            if res.data:
                return res.data[0].get("id")
        except Exception as exc:
            msg = str(exc).lower()
            if "duplicate key" in msg or "unique constraint" in msg:
                pass
            else:
                raise
        res = (
            self.db.client.table(self.table)
            .select("id")
            .eq("signal_preview_id", str(payload["signal_preview_id"]))
            .eq("decision_version", payload["decision_version"])
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("id")
        raise RuntimeError("decision id not found after insert")


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


class SignalPreviewsRepo(BaseRepo):
    table = "signal_previews"

    def insert_preview(self, preview: SignalPreviewV1) -> None:
        _ = self.insert_preview_return_id(preview)

    def insert_preview_return_id(self, preview: SignalPreviewV1):
        payload = preview.to_db_row()
        try:
            res = self.db.client.table(self.table).insert(payload).select("id").execute()
            if res.data:
                return res.data[0].get("id")
        except Exception as exc:
            # Idempotent: unique violation is acceptable
            msg = str(exc).lower()
            if "duplicate key" in msg or "unique constraint" in msg:
                pass
            else:
                raise
        # fetch existing on conflict
        res = (
            self.db.client.table(self.table)
            .select("id")
            .eq("ts_utc", payload["ts_utc"])
            .eq("symbol", payload["symbol"])
            .eq("timeframe_trigger", payload["timeframe_trigger"])
            .eq("engine_version", payload["engine_version"])
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("id")
        raise RuntimeError("signal_preview id not found after insert")


def make_repos(db: SupabaseDB) -> dict[str, Any]:
    """
    Convenience factory.
    """
    repos = {
        "snapshots": SnapshotsRepo(db),
        "signals": SignalsRepo(db),
        "decisions": DecisionsRepo(db),
        "execution_reports": ExecutionReportsRepo(db),
        "risk_events": RiskEventsRepo(db),
    }
    try:
        from app.storage.agent_reports_repo import AgentReportsRepo  # local import to avoid circular

        repos["agent_reports"] = AgentReportsRepo(db)
    except Exception:
        pass
    repos["signal_previews"] = SignalPreviewsRepo(db)
    return repos
