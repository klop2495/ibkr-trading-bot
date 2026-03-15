from app.dashboard import _normalize_execution_block_reason, _normalize_risk_block_reason


def test_normalize_risk_block_reason():
    assert _normalize_risk_block_reason("risk_blocked:max_open_positions=2") == "max_open_positions=2"
    assert _normalize_risk_block_reason("trading disabled") == "trading_disabled"
    assert _normalize_risk_block_reason("some other comment") is None


def test_normalize_execution_block_reason():
    assert _normalize_execution_block_reason(
        "EXECUTION_BLOCKED",
        "Trade blocked by forecast gate for EURUSD",
        {"reason": "forecast_gate"},
    ) == "forecast_gate"
    assert _normalize_execution_block_reason(
        "EXECUTION_ABORTED",
        "Failed to persist trade row before order placement for EURUSD",
        {},
    ) == "trade_row_not_created"
    assert _normalize_execution_block_reason(
        "EXECUTION_BLOCKED",
        "Non-structural SL/TP fallback blocked for EURUSD",
        {},
    ) == "non_structural_sl_tp"
