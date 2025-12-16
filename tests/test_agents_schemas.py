import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.agents.schemas import AgentDecision, AgentRequest, AgentResponse


def test_agent_response_risk_modifier_bounds():
    assert AgentResponse(decision_id="d1", trade_allowed=True, risk_modifier=0.5)
    assert AgentResponse(decision_id="d2", trade_allowed=False, risk_modifier=1.0)
    with pytest.raises(ValidationError):
        AgentResponse(decision_id="d3", trade_allowed=True, risk_modifier=0.4)
    with pytest.raises(ValidationError):
        AgentResponse(decision_id="d4", trade_allowed=True, risk_modifier=1.1)


def test_agent_request_minimal_fields_defaults():
    req = AgentRequest(
        decision_id="abc",
        ts=datetime.utcnow(),
        symbols=["EURUSD"],
        timeframes=["M15"],
    )
    assert req.context_flags == []
    assert req.features_summary == {}
    assert req.position_state == {}


def test_agent_decision_serializable():
    decision = AgentDecision(
        decision_id="abc",
        trade_allowed=True,
        risk_modifier=0.9,
        flags=["NEWS_WINDOW"],
        comment="ok",
        per_agent={
            "news": AgentResponse(
                decision_id="abc", trade_allowed=False, risk_modifier=1.0, flags=["NEWS_TIMEOUT"]
            )
        },
    )
    dumped = decision.model_dump(mode="json")
    json_str = json.dumps(dumped)
    assert "NEWS_TIMEOUT" in json_str
    assert dumped["per_agent"]["news"]["decision_id"] == "abc"
