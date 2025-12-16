from datetime import datetime, timezone
from unittest import mock

import pytest
from pydantic import ValidationError

from app.agents.schemas import AgentDecision, AgentResponse
from app.decision.agents_precheck import agents_precheck, build_agent_request_from_inputs


def make_inputs():
    per_symbol_inputs = {
        "EURUSD": {
            "signal_summary": {"direction": "long", "setup_present": True, "entry_triggered": False},
            "feature_bins": {"volatility": "normal", "trend": "up", "momentum": "neutral"},
            "data_quality": "ok",
            "spread_quality": "ok",
        }
    }
    portfolio_inputs = {
        "open_positions_count": 0,
        "usd_side_bias": "neutral",
        "daily_trade_count_portfolio": 0,
        "daily_trade_count_per_symbol": {"EURUSD": 0},
    }
    return per_symbol_inputs, portfolio_inputs


def test_build_agent_request_from_inputs_valid():
    per_symbol_inputs, portfolio_inputs = make_inputs()
    req = build_agent_request_from_inputs(
        ts_utc=datetime.now(timezone.utc),
        universe=["EURUSD"],
        timeframes=["M15", "H1", "H4"],
        per_symbol_inputs=per_symbol_inputs,
        portfolio_inputs=portfolio_inputs,
    )
    assert req.universe == ["EURUSD"]
    assert req.timeframes == ["M15", "H1", "H4"]
    assert req.per_symbol[0].symbol == "EURUSD"
    assert req.portfolio.open_positions_count == 0


def test_agents_precheck_calls_gate(monkeypatch):
    per_symbol_inputs, portfolio_inputs = make_inputs()
    decision = AgentDecision(
        response=AgentResponse(trade_allowed=True, risk_modifier=1.0, flags=["ok"], comment="ok"),
        per_agent=None,
        timed_out=False,
        fallback_used=False,
        error=None,
    )
    mock_gate = mock.Mock(return_value=decision)
    monkeypatch.setattr("app.decision.agents_precheck.evaluate_agents", mock_gate)

    result = agents_precheck(
        ts_utc=datetime.now(timezone.utc),
        universe=["EURUSD"],
        per_symbol_inputs=per_symbol_inputs,
        portfolio_inputs=portfolio_inputs,
        config=None,
    )
    assert result == decision
    assert mock_gate.called


def test_invalid_symbol_raises_validation_error():
    per_symbol_inputs, portfolio_inputs = make_inputs()
    with pytest.raises(ValidationError):
        build_agent_request_from_inputs(
            ts_utc=datetime.now(timezone.utc),
            universe=["FOO"],  # invalid
            timeframes=["M15", "H1", "H4"],
            per_symbol_inputs=per_symbol_inputs,
            portfolio_inputs=portfolio_inputs,
        )


def test_unexpected_key_in_per_symbol_inputs_fails():
    per_symbol_inputs, portfolio_inputs = make_inputs()
    per_symbol_inputs["EURUSD"]["unexpected"] = "value"
    with pytest.raises(ValidationError):
        build_agent_request_from_inputs(
            ts_utc=datetime.now(timezone.utc),
            universe=["EURUSD"],
            timeframes=["M15", "H1", "H4"],
            per_symbol_inputs=per_symbol_inputs,
            portfolio_inputs=portfolio_inputs,
        )
