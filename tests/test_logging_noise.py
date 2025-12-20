import os

import app.main as main_mod


def test_log_backfill_summary_no_sample_info_in_info(monkeypatch):
    # INFO level should print summary without sample ids
    monkeypatch.setenv("CONTROL_PLANE_LOG_LEVEL", "INFO")
    processed_total = 5
    sample_ids = ("d1", "v1", "p1")
    log_level = (os.getenv("CONTROL_PLANE_LOG_LEVEL") or "INFO").upper()
    msg = main_mod.format_backfill_status(
        log_level=log_level,
        processed_total=processed_total,
        fetched_total=5,
        scanned_total=5,
        elapsed_ms=3,
        stop_reason="NO_PENDING",
        sample_ids=sample_ids,
    )
    assert "sample_decision" not in msg
    assert msg.startswith("control_plane_backfill fetched=5 scanned=5 processed=5")
