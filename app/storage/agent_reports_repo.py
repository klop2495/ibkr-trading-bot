from datetime import datetime, timezone

from app.models.agent_report import AgentReport
from app.storage.repositories import BaseRepo


class AgentReportsRepo(BaseRepo):
    table_name = "agent_reports"

    def insert(
        self,
        report: AgentReport,
        ts_utc: datetime,
        scope: str,
        symbol: str | None,
    ) -> dict:
        if ts_utc.tzinfo is None:
            raise ValueError("ts_utc must be timezone-aware (UTC)")
        ts_utc = ts_utc.astimezone(timezone.utc)

        payload = {
            "schema_version": getattr(report, "schema_version", 1),
            "ts": ts_utc.isoformat(),
            "scope": scope,
            "symbol": symbol,
            "signal_preview_id": str(report.signal_preview_id) if report.signal_preview_id else None,
            "trade_allowed": report.trade_allowed,
            "risk_modifier": report.risk_modifier,
            "flags": report.flags,
            "comment": report.comment,
        }
        res = self.db.client.table(self.table_name).insert(payload).execute()
        return {"count": len(res.data or []), "data": res.data}
