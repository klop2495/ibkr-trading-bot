from app.execution.lifecycle import (
    append_status_trace,
    infer_close_source,
    infer_completion_status,
    merge_integrity_flags,
    validate_execution_transition,
)


def test_validate_execution_transition_allows_expected_flow():
    validate_execution_transition("PENDING", "SUBMITTED")
    validate_execution_transition("SUBMITTED", "OPEN")
    validate_execution_transition("OPEN", "CLOSED")


def test_validate_execution_transition_rejects_invalid_flow():
    try:
        validate_execution_transition("CLOSED", "OPEN")
    except ValueError as exc:
        assert "invalid execution status transition" in str(exc)
    else:
        raise AssertionError("Expected ValueError for invalid transition")


def test_infer_completion_status():
    assert infer_completion_status("OPEN") == "active"
    assert infer_completion_status("REJECTED") == "rejected"
    assert infer_completion_status("CLOSED", exit_price=1.2, pnl=10.0) == "complete"
    assert infer_completion_status("CLOSED", exit_price=None, pnl=10.0) == "incomplete"


def test_infer_close_source():
    assert infer_close_source("TP_HIT") == "broker_bracket"
    assert infer_close_source("MANUAL") == "manual"
    assert infer_close_source("BROKER_FLAT") == "broker_reconcile"
    assert infer_close_source("OTHER_REASON") == "broker"


def test_status_trace_and_flags():
    trace = append_status_trace([], from_status=None, to_status="PENDING", reason="create")
    trace = append_status_trace(trace, from_status="PENDING", to_status="SUBMITTED", reason="submit")
    assert trace[0]["to"] == "PENDING"
    assert trace[1]["from"] == "PENDING"
    flags = merge_integrity_flags(["STALE_STATUS"], "BROKER_REJECTED", "STALE_STATUS")
    assert flags == ["BROKER_REJECTED", "STALE_STATUS"]
