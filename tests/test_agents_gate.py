import os
from datetime import datetime, timezone
from unittest import mock

from app.agents.config import AgentConfig
from app.agents.gate import evaluate_agents
from app.agents.schemas import AgentDecision, AgentRequest, AgentResponse, FeatureBins, PerSymbolAgentState, PortfolioAgentState, SignalSummary


def make_request():
    return AgentRequest(
        ts_utc=datetime.now(timezone.utc),
        universe=["EURUSD"],
        timeframes=["M15"],
        per_symbol=[
            PerSymbolAgentState(
                symbol="EURUSD",
                signal_summary=SignalSummary(direction="long", setup_present=True, entry_triggered=False),
                feature_bins=FeatureBins(volatility="normal", trend="up", momentum="neutral"),
                data_quality="ok",
                spread_quality="ok",
            )
        ],
        portfolio=PortfolioAgentState(
            open_positions_count=0,
            usd_side_bias="neutral",
            daily_trade_count_portfolio=0,
            daily_trade_count_per_symbol={"EURUSD": 0},
        ),
        safe_mode=False,
    )


def test_disabled_returns_allow(monkeypatch):
    req = make_request()
    decision = evaluate_agents(req, config=AgentConfig(enabled=False))
    assert decision.response.trade_allowed is True
    assert decision.response.risk_modifier == 1.0
    assert decision.response.flags == ["agents_disabled"]
    assert decision.error is None


def test_enabled_missing_key_blocks(monkeypatch):
    req = make_request()
    cfg = AgentConfig(enabled=True, api_key=None)
    decision = evaluate_agents(req, config=cfg)
    assert decision.response.trade_allowed is False
    assert decision.response.risk_modifier == 0.5
    assert "agent_misconfigured" in decision.response.flags
    assert decision.error == "missing_api_key"
    assert decision.fallback_used is True


def test_enabled_uses_orchestrator(monkeypatch):
    req = make_request()
    cfg = AgentConfig(enabled=True, api_key="sk-test")
    mock_orch = mock.Mock()
    expected = AgentDecision(
        response=AgentResponse(trade_allowed=True, risk_modifier=0.9, flags=["ok"], comment="ok"),
        per_agent=None,
        timed_out=False,
        fallback_used=False,
        error=None,
    )
    mock_orch.analyze.return_value = expected

    with mock.patch("app.agents.gate.AgentsOrchestrator", return_value=mock_orch):
        decision = evaluate_agents(req, config=cfg)

    assert decision == expected
    mock_orch.analyze.assert_called_once_with(req)
