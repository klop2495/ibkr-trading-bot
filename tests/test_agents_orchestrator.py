from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from app.agents.errors import AgentHTTPError, AgentSchemaError, AgentTimeout
from app.agents.openai_client import OpenAIResponsesClient
from app.agents.orchestrator import AgentsOrchestrator
from app.agents.schemas import AgentDecision, AgentRequest, AgentResponse, FeatureBins, PerSymbolAgentState, PortfolioAgentState, SignalSummary
from app.agents.state import CircuitBreakerState


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


def test_success_resets_breaker(monkeypatch):
    req = make_request()
    mock_client = mock.Mock(spec=OpenAIResponsesClient)
    mock_client.analyze.return_value = AgentResponse(trade_allowed=True, risk_modifier=0.9, flags=["ok"], comment="ok")
    state = CircuitBreakerState()
    state.failures = 2
    orch = AgentsOrchestrator(client=mock_client, state=state)

    decision = orch.analyze(req)

    assert isinstance(decision, AgentDecision)
    assert decision.response.trade_allowed is True
    assert state.failures == 0
    mock_client.analyze.assert_called_once()


def test_timeout_fallback_records_failure(monkeypatch):
    req = make_request()
    mock_client = mock.Mock(spec=OpenAIResponsesClient)
    mock_client.analyze.side_effect = AgentTimeout("timeout")
    state = CircuitBreakerState()
    orch = AgentsOrchestrator(client=mock_client, cb_failure_threshold=2, state=state)

    decision = orch.analyze(req)

    assert decision.response.trade_allowed is False
    assert decision.response.risk_modifier == 0.5
    assert decision.timed_out is True
    assert state.failures == 1
    assert "agent_timeout" in decision.response.flags


def test_error_fallback_with_agent_error_flag(monkeypatch):
    req = make_request()
    mock_client = mock.Mock(spec=OpenAIResponsesClient)
    mock_client.analyze.side_effect = AgentSchemaError("bad schema")
    orch = AgentsOrchestrator(client=mock_client)

    decision = orch.analyze(req)

    assert decision.response.trade_allowed is False
    assert "agent_error" in decision.response.flags
    assert decision.fallback_used is True
    assert decision.error == "AgentSchemaError"


def test_circuit_breaker_opens_and_blocks_until_cooldown():
    req = make_request()
    mock_client = mock.Mock(spec=OpenAIResponsesClient)
    mock_client.analyze.side_effect = AgentHTTPError("http", status_code=500)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    times = [start, start + timedelta(seconds=1), start + timedelta(hours=2)]

    def now_fn():
        return times.pop(0)

    state = CircuitBreakerState()
    orch = AgentsOrchestrator(
        client=mock_client,
        cb_failure_threshold=1,
        cb_cooldown_sec=3600,
        now_func=now_fn,
        state=state,
    )

    # First call triggers failure and opens breaker
    decision1 = orch.analyze(req)
    assert decision1.response.trade_allowed is False
    assert "agent_error" in decision1.response.flags
    assert state.opened_at_utc is not None

    # Second call should be blocked by breaker
    decision2 = orch.analyze(req)
    assert decision2.response.trade_allowed is False
    assert "agent_circuit_breaker" in decision2.response.flags
    assert decision2.fallback_used is True
    # Third call after cooldown elapsed should attempt again (and fail)
    decision3 = orch.analyze(req)
    assert "agent_error" in decision3.response.flags
    assert state.opened_at_utc is not None
