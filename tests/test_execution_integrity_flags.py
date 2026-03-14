from app.execution.lifecycle import infer_integrity_flags


def test_infer_integrity_flags_marks_dirty_reconcile_close() -> None:
    flags = infer_integrity_flags(
        symbol="GBPJPY",
        status="CLOSED",
        close_reason="BROKER_FLAT",
        close_source="broker_reconcile",
        entry_price=210.875,
        exit_price=211.185,
        pnl=-78.17,
        pnl_pips=-31.0,
    )
    assert "RECONCILE_CLOSE" in flags


def test_infer_integrity_flags_marks_corrupted_jpy_entry_scale() -> None:
    flags = infer_integrity_flags(
        symbol="CADJPY",
        status="CLOSED",
        close_reason="SL_HIT",
        close_source="broker_bracket",
        entry_price=1.3341,
        exit_price=113.235,
        pnl=-28425.47,
        pnl_pips=-11190.09,
    )
    assert "SUSPECT_ENTRY_PRICE_SCALE" in flags
    assert "EXTREME_PNL_PIPS" in flags


def test_infer_integrity_flags_marks_incomplete_manual_cleanup() -> None:
    flags = infer_integrity_flags(
        symbol="GBPUSD",
        status="CLOSED",
        close_reason="MANUAL_CLEANUP",
        close_source="broker",
        entry_price=1.34210,
        exit_price=None,
        pnl=None,
        pnl_pips=None,
    )
    assert "MANUAL_CLEANUP_CLOSE" in flags
    assert "MISSING_EXIT_PRICE" in flags
    assert "MISSING_PNL" in flags
