from datetime import datetime, timedelta, timezone

import pytest

from app.agents.client import AgentClient
from app.agents.errors import AgentCallError, AgentTimeout
from app.agents.orchestrator import AgentsOrchestrator
from app.agents.schemas import AgentRequest, AgentResponse
from app.agents.state import AgentsCircuitBreakerState
from app.models.agent_report import AgentReport


class FakeAgentReportsRepo:
    def __init__(self):
        self.records = []

    def insert(self, decision_id: str, agent_name: str, report: AgentReport):
        self.records.append((decision_id, agent_name, report))
        return {"count": 1, "data": []}


class FakeRiskEventsRepo:
    def __init__(self):
        self.events = []

    def insert(self, event_type, severity="info", symbol=None, message=None, data=None):
        self.events.append(
            {"event_type": event_type, "severity": severity, "symbol": symbol, "message": message, "data": data}
        )
        return {"count": 1, "data": []}


class StaticAgent(AgentClient):
    def __init__(self, response: AgentResponse):
        self.response = response
        self.calls = 0

    def analyze(self, agent_request: AgentRequest) -> AgentResponse:
        self.calls += 1
        return self.response


class TimeoutAgent(AgentClient):
    def analyze(self, agent_request: AgentRequest) -> AgentResponse:
        raise AgentTimeout("simulated timeout")


class ErrorAgent(AgentClient):
    def __init__(self):
        self.calls = 0

    def analyze(self, agent_request: AgentRequest) -> AgentResponse:
        self.calls += 1
        raise AgentCallError("simulated failure")


def make_request():
    return AgentRequest(
        decision_id="d1",
        ts=datetime.now(timezone.utc),
        symbols=["EURUSD"],
        timeframes=["M15"],
    )


def test_aggregation_logic():
    req = make_request()
    a1 = StaticAgent(
        AgentResponse(decision_id=req.decision_id, trade_allowed=True, risk_modifier=1.0, flags=["A1"])
    )
    a2 = StaticAgent(
        AgentResponse(decision_id=req.decision_id, trade_allowed=False, risk_modifier=0.7, flags=["A2"])
    )
    reports_repo = FakeAgentReportsRepo()
    risk_repo = FakeRiskEventsRepo()
    orch = AgentsOrchestrator(
        agents={"a1": a1, "a2": a2},
        agent_reports_repo=reports_repo,
        risk_events_repo=risk_repo,
    )
    decision = orch.analyze(req)
    assert decision.trade_allowed is False  # any false blocks
    assert decision.risk_modifier == 0.7  # min risk modifier
    assert set(decision.flags) == {"A1", "A2"}
    assert decision.per_agent["a1"].flags == ["A1"]
    assert decision.per_agent["a2"].flags == ["A2"]
    # Reports logged for each agent
    assert len(reports_repo.records) == 2
    assert reports_repo.records[0][0] == req.decision_id
    # Trade blocked logged
    assert any(evt["event_type"] == "AGENT_TRADE_BLOCKED" for evt in risk_repo.events)


def test_timeout_fallback_news_agent():
    req = make_request()
    news_agent = TimeoutAgent()
    risk_repo = FakeRiskEventsRepo()
    orch = AgentsOrchestrator(
        agents={"news": news_agent},
        agent_roles={"news": "news"},
        failure_threshold=2,
        cooldown_seconds=60,
        risk_events_repo=risk_repo,
    )
    decision = orch.analyze(req)
    assert decision.trade_allowed is False
    assert "NEWS_TIMEOUT" in decision.flags
    assert decision.per_agent["news"].trade_allowed is False
    assert any(evt["event_type"] == "AGENT_TIMEOUT" for evt in risk_repo.events)


def test_circuit_breaker_blocks_after_failures():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    times = [start, start + timedelta(seconds=10), start + timedelta(seconds=70)]

    def now_fn():
        return times.pop(0)

    req = make_request()
    failing_agent = ErrorAgent()
    state = AgentsCircuitBreakerState()
    risk_repo = FakeRiskEventsRepo()
    orch = AgentsOrchestrator(
        agents={"fail": failing_agent},
        agent_roles={"fail": "generic"},
        failure_threshold=1,
        cooldown_seconds=30,
        state=state,
        now_fn=now_fn,
        risk_events_repo=risk_repo,
    )

    # First call triggers failure and opens breaker
    decision1 = orch.analyze(req)
    assert decision1.trade_allowed is False
    assert "AGENT_ERROR" in decision1.flags
    assert failing_agent.calls == 1
    assert any(evt["event_type"] == "AGENT_ERROR" for evt in risk_repo.events)
    assert any(evt["event_type"] == "AGENT_CB_ON" for evt in risk_repo.events)

    # Second call should be blocked by circuit breaker (no agent invocation)
    decision2 = orch.analyze(req)
    assert decision2.trade_allowed is False
    assert decision2.flags == ["AGENT_CIRCUIT_BREAKER"]
    assert failing_agent.calls == 1
    assert any(evt["event_type"] == "AGENT_TRADE_BLOCKED" for evt in risk_repo.events)

    # Third call after cooldown resets breaker and attempts again (consumes time list)
    decision3 = orch.analyze(req)
    assert decision3.trade_allowed is False
    assert "AGENT_ERROR" in decision3.flags
    assert failing_agent.calls == 2
    assert any(evt["event_type"] == "AGENT_CB_OFF" for evt in risk_repo.events)
