import pytest
from pydantic import ValidationError

from app.agents.client import AgentClient, OpenAIAgentClient
from app.agents.schemas import AgentRequest, AgentResponse


class MockAgentClient:
    def analyze(self, agent_request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            decision_id=agent_request.decision_id,
            trade_allowed=True,
            risk_modifier=0.9,
            flags=["MOCK_OK"],
            comment="ok",
        )


def test_mock_client_returns_valid_response():
    client: AgentClient = MockAgentClient()
    req = AgentRequest(
        decision_id="abc",
        ts=agent_request_ts(),
        symbols=["EURUSD"],
        timeframes=["M15"],
    )
    resp = client.analyze(req)
    assert isinstance(resp, AgentResponse)
    assert resp.risk_modifier == 0.9
    assert resp.trade_allowed is True


def test_invalid_response_rejected_by_schema():
    with pytest.raises(ValidationError):
        AgentResponse(
            decision_id="abc",
            trade_allowed=True,
            risk_modifier=1.5,  # out of bounds
        )


def test_openai_client_requires_api_key():
    client = OpenAIAgentClient(api_key=None)
    with pytest.raises(Exception):
        client.analyze(
            AgentRequest(
                decision_id="abc",
                ts=agent_request_ts(),
                symbols=["EURUSD"],
                timeframes=["M15"],
            )
        )


def agent_request_ts():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
