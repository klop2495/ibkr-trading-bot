from datetime import datetime, timezone
from unittest import mock

from app.agents.schemas import AgentDecision, AgentResponse
from app.decision.agents_precheck import agents_precheck_and_persist


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


def make_decision():
    return AgentDecision(
        response=AgentResponse(trade_allowed=True, risk_modifier=1.0, flags=["ok"], comment="ok"),
        per_agent=None,
        timed_out=False,
        fallback_used=False,
        error=None,
    )


def test_agents_precheck_and_persist_success(monkeypatch):
    per_symbol_inputs, portfolio_inputs = make_inputs()
    decision = make_decision()
    mock_gate = mock.Mock(return_value=decision)
    mock_repo = mock.Mock()

    monkeypatch.setattr("app.decision.agents_precheck.evaluate_agents", mock_gate)

    ts = datetime.now(timezone.utc)
    result = agents_precheck_and_persist(
        decision_id="d1",
        ts_utc=ts,
        universe=["EURUSD"],
        per_symbol_inputs=per_symbol_inputs,
        portfolio_inputs=portfolio_inputs,
        config=None,
        agent_reports_repo=mock_repo,
    )

    assert result == decision
    mock_gate.assert_called_once()
    mock_repo.insert.assert_called_once()
    args, kwargs = mock_repo.insert.call_args
    assert "report" in kwargs
    assert kwargs["scope"] == "portfolio"
    assert kwargs["symbol"] is None
    assert kwargs["ts_utc"] == ts


def test_agents_precheck_and_persist_failure_returns_fallback(monkeypatch):
    per_symbol_inputs, portfolio_inputs = make_inputs()
    decision = make_decision()
    mock_gate = mock.Mock(return_value=decision)
    mock_repo = mock.Mock()
    mock_repo.insert.side_effect = Exception("fail")

    monkeypatch.setattr("app.decision.agents_precheck.evaluate_agents", mock_gate)

    result = agents_precheck_and_persist(
        decision_id="d1",
        ts_utc=datetime.now(timezone.utc),
        universe=["EURUSD"],
        per_symbol_inputs=per_symbol_inputs,
        portfolio_inputs=portfolio_inputs,
        config=None,
        agent_reports_repo=mock_repo,
    )

    assert result.response.trade_allowed is False
    assert result.response.risk_modifier == 0.5
    assert "agent_report_persist_failed" in result.response.flags
    assert result.fallback_used is True
    assert result.error == "persist_failed"
