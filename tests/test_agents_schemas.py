import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.agents.schemas import (
    AgentDecision,
    AgentRequest,
    AgentResponse,
    FeatureBins,
    PerSymbolAgentState,
    PortfolioAgentState,
    SignalSummary,
)


def make_per_symbol(symbol: str = "EURUSD"):
    return PerSymbolAgentState(
        symbol=symbol,
        signal_summary=SignalSummary(direction="long", setup_present=True, entry_triggered=False),
        feature_bins=FeatureBins(volatility="normal", trend="up", momentum="neutral"),
        data_quality="ok",
        spread_quality="ok",
    )


def make_portfolio():
    return PortfolioAgentState(
        open_positions_count=0,
        usd_side_bias="neutral",
        daily_trade_count_portfolio=0,
        daily_trade_count_per_symbol={"EURUSD": 0},
    )


def make_request():
    return AgentRequest(
        ts_utc=datetime.now(timezone.utc),
        universe=["EURUSD"],
        timeframes=["M15"],
        per_symbol=[make_per_symbol()],
        portfolio=make_portfolio(),
        safe_mode=False,
    )


def test_agent_response_risk_modifier_bounds():
    assert AgentResponse(trade_allowed=True, risk_modifier=0.5, flags=[], comment="ok")
    assert AgentResponse(trade_allowed=False, risk_modifier=1.0, flags=["x"], comment="blocked")
    with pytest.raises(ValidationError):
        AgentResponse(trade_allowed=True, risk_modifier=0.4, flags=[], comment="low")
    with pytest.raises(ValidationError):
        AgentResponse(trade_allowed=True, risk_modifier=1.1, flags=[], comment="high")


def test_agent_request_rejects_unknown_symbol_or_timeframe():
    with pytest.raises(ValidationError):
        AgentRequest(
            ts_utc=datetime.now(timezone.utc),
            universe=["EURUSD", "FOO"],
            timeframes=["M15"],
            per_symbol=[make_per_symbol()],
            portfolio=make_portfolio(),
            safe_mode=False,
        )
    with pytest.raises(ValidationError):
        AgentRequest(
            ts_utc=datetime.now(timezone.utc),
            universe=["EURUSD"],
            timeframes=["M5"],  # invalid timeframe
            per_symbol=[make_per_symbol()],
            portfolio=make_portfolio(),
            safe_mode=False,
        )


def test_per_symbol_forbids_extra_fields():
    with pytest.raises(ValidationError):
        PerSymbolAgentState(
            symbol="EURUSD",
            signal_summary=SignalSummary(direction="long", setup_present=True, entry_triggered=False),
            feature_bins=FeatureBins(volatility="normal", trend="up", momentum="neutral"),
            data_quality="ok",
            spread_quality="ok",
            volume=1.0,  # extra numeric field not allowed
        )


def test_agent_decision_serializes_to_json():
    decision = AgentDecision(
        response=AgentResponse(trade_allowed=True, risk_modifier=0.9, flags=["ok"], comment="all good"),
        per_agent={
            "mock": AgentResponse(trade_allowed=True, risk_modifier=1.0, flags=[], comment="mock")
        },
        timed_out=False,
        fallback_used=False,
        error=None,
    )
    dumped = decision.model_dump(mode="json")
    json_str = json.dumps(dumped)
    assert '"risk_modifier": 0.9' in json_str
    assert "per_agent" in dumped
