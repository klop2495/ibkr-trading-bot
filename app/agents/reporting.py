from datetime import datetime

from app.agents.schemas import AgentDecision, AgentRequest
from app.models.agent_report import AgentReport


def build_agent_report(
    decision_id: str,
    ts_utc: datetime,
    request: AgentRequest,
    decision: AgentDecision,
) -> AgentReport:
    comment = (decision.response.comment or "")[:240]
    return AgentReport(
        trade_allowed=decision.response.trade_allowed,
        risk_modifier=decision.response.risk_modifier,
        flags=decision.response.flags,
        comment=comment or None,
    )
