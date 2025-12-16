from datetime import datetime

from app.models.agent_report import AgentReport
from app.storage.db import SupabaseDB
from app.storage.repositories import BaseRepo


class AgentReportsRepo(BaseRepo):
    table = "agent_reports"

    def insert(self, decision_id: str, agent_name: str, report: AgentReport) -> dict:
        payload = {
            "decision_id": decision_id,
            "agent_name": agent_name,
            "schema_version": report.schema_version,
            "trade_allowed": report.trade_allowed,
            "risk_modifier": report.risk_modifier,
            "flags": report.flags,
            "comment": report.comment,
            "ts": datetime.utcnow().isoformat(),
        }
        res = self.db.client.table(self.table).insert(payload).execute()
        return {"count": len(res.data or []), "data": res.data}
