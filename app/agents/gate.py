from typing import Optional

from app.agents.orchestrator import AgentsOrchestrator
from app.agents.schemas import AgentDecision, AgentRequest


def evaluate_agents(
    agent_request: AgentRequest,
    orchestrator: Optional[AgentsOrchestrator],
    agents_enabled: bool = True,
) -> AgentDecision:
    """
    Single integration point for decision layer.

    - If agents are disabled or orchestrator is None: allow trading with neutral risk modifier and flag.
    - If enabled: delegate to orchestrator (which applies fail-safe/fallback rules).
    """
    if not agents_enabled or orchestrator is None:
        return AgentDecision(
            decision_id=agent_request.decision_id,
            trade_allowed=True,
            risk_modifier=1.0,
            flags=["agents_disabled"],
            comment="Agents gate disabled",
            per_agent={},
        )

    return orchestrator.analyze(agent_request)
