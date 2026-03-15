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
    assert _normalize_execution_block_reason(
        "EXECUTION_BLOCKED",
        "Broker has pending orders for USDCHF",
        {},
    ) == "broker_has_open_orders"
    assert _normalize_execution_block_reason(
        "EXECUTION_BLOCKED",
        "Broker already has position in EURUSD",
        {},
    ) == "broker_has_position"
    assert _normalize_execution_block_reason(
        "EXECUTION_BLOCKED",
        "Duplicate decision acf62b9b-5fde-42ca-b58d-d90c6a9a49e4 - already executed",
        {},
    ) == "duplicate_decision"
    assert _normalize_execution_block_reason(
        "EXECUTION_SKIPPED",
        "Position size too small for EURUSD",
        {},
    ) == "position_too_small"
    assert _normalize_execution_block_reason(
        "EXECUTION_BLOCKED",
        "Trade blocked by forecast gate for EURUSD: hours_filter:current_hour=16 allowed=[8, 9, 10]",
        {},
    ) == "hours_filter"
