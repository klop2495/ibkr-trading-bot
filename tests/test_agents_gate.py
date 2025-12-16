from datetime import datetime, timezone

from app.agents.gate import evaluate_agents
from app.agents.schemas import AgentDecision, AgentRequest, AgentResponse


class MockOrchestrator:
    def __init__(self, decision: AgentDecision):
        self.decision = decision
        self.calls = 0

    def analyze(self, agent_request: AgentRequest) -> AgentDecision:
        self.calls += 1
        return self.decision


def make_request():
    return AgentRequest(
        decision_id="d1",
        ts=datetime.now(timezone.utc),
        symbols=["EURUSD"],
        timeframes=["M15"],
    )


def test_disabled_gate_returns_allow():
    req = make_request()
    decision = evaluate_agents(req, orchestrator=None, agents_enabled=False)
    assert decision.trade_allowed is True
    assert decision.risk_modifier == 1.0
    assert decision.flags == ["agents_disabled"]


def test_enabled_gate_calls_orchestrator():
    req = make_request()
    mock_decision = AgentDecision(
        decision_id=req.decision_id,
        trade_allowed=False,
        risk_modifier=0.8,
        flags=["MOCK"],
        comment=None,
        per_agent={"x": AgentResponse(decision_id=req.decision_id, trade_allowed=False, risk_modifier=0.8, flags=["MOCK"])},
    )
    orchestrator = MockOrchestrator(mock_decision)
    decision = evaluate_agents(req, orchestrator=orchestrator, agents_enabled=True)
    assert orchestrator.calls == 1
    assert decision.trade_allowed is False
    assert decision.risk_modifier == 0.8
    assert decision.flags == ["MOCK"]
