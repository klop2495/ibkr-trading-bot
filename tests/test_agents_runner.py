from datetime import datetime, timezone

from app.agents.runner import AgentsAggregator, AgentResult, aggregate_decision
from app.models.signal_preview import Confidence, DataQuality, Direction, SetupType, SignalPreviewV1, SpreadQuality
from app.models.signals_params import SignalsParams
from app.signals.engine_v1 import (
    FLAG_DATA_STALE,
    FLAG_SIGNALS_RULES_NOT_SPECIFIED,
)


def _params_configured():
    return SignalsParams.model_validate(
        {
            "schema_version": 1,
            "enabled": True,
            "rules_version": 1,
            "timeframes": {"h4": "H4", "h1": "H1", "m15": "M15"},
            "gates": {
                "require_warmup": True,
                "require_data_ok": True,
                "require_spread_ok": True,
                "disallow_h4_neutral": True,
            },
            "spread": {"wide_spread_pips": 0.5},
            "structure": {
                "swing_lookback_bars": 20,
                "swing_min_separation_bars": 5,
                "sl_buffer_mode": "PIPS",
                "sl_buffer_pips": 5,
                "min_sl_pips": 5,
                "max_sl_pips": 50,
            },
            "confidence": {"high_conf_rule": "H4_H1_ALIGN_AND_M15_MOMENTUM_CONFIRM"},
            "rr": {"mode": "contextual", "base_rr": 2.0, "high_conf_rr": 3.0},
            "filters": {"trade_hours_utc": ["06:00-20:00"]},
            "m15_confirm": {"rsi_period": 14, "rsi_long_min": 55, "rsi_short_max": 45, "sma_period": 50},
        }
    )


def _preview(flags=None, setup_present=True, entry_triggered=True, direction=Direction.LONG):
    return SignalPreviewV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        setup_type=SetupType.SWING_CONTINUATION,
        direction=direction,
        setup_present=setup_present,
        entry_triggered=entry_triggered,
        confidence=Confidence.NORMAL,
        rr=2.0,
        data_quality=DataQuality.OK,
        spread_quality=SpreadQuality.OK,
        flags=flags or [],
    )


def test_quality_agent_blocks_on_bad_quality():
    params = _params_configured()
    aggregator = AgentsAggregator()
    preview = _preview(flags=[FLAG_DATA_STALE])
    results = aggregator.run(preview, params)
    decision = aggregate_decision(preview, results)
    assert decision["trade_allowed"] is False
    assert FLAG_DATA_STALE in decision["flags"]
    assert FLAG_SIGNALS_RULES_NOT_SPECIFIED not in decision["flags"]


def test_entry_not_triggered_blocks_even_if_agents_allow():
    params = _params_configured()
    aggregator = AgentsAggregator()
    preview = _preview(setup_present=True, entry_triggered=False, direction=Direction.LONG)
    results = aggregator.run(preview, params)
    decision = aggregate_decision(preview, results)
    assert decision["trade_allowed"] is False
    assert FLAG_SIGNALS_RULES_NOT_SPECIFIED not in decision["flags"]


def test_agent_exception_fails_safe():
    class BadAgent:
        name = "BadAgent"
        version = "1.0"

        def run(self, agent_input):
            raise RuntimeError("boom")

    params = _params_configured()
    aggregator = AgentsAggregator(agents=[BadAgent()])
    preview = _preview()
    results = aggregator.run(preview, params)
    assert "BadAgent" in results
    assert results["BadAgent"].trade_allowed is False
    decision = aggregate_decision(preview, results)
    assert decision["trade_allowed"] is False
    assert FLAG_SIGNALS_RULES_NOT_SPECIFIED not in decision["flags"]
