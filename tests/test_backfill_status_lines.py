import app.main as main_mod


class DummyRiskEventsRepo:
    def __init__(self):
        self.calls = []

    def insert(self, **kwargs):
        self.calls.append(kwargs)


def test_backfill_status_disabled(monkeypatch):
    monkeypatch.setenv("CONTROL_PLANE_LOG_LEVEL", "INFO")
    processed_total, fetched_total, scanned_total, sample_ids, stop_reason, elapsed_ms, error_message, slow, next_batch_size, fetch_ms_total, persist_ms_total, early_break, stable_fast, _cursor = main_mod.run_backfill_tick(
        client=None,
        params=None,
        settings=None,
        agents_aggregator=None,
        risk_engine=None,
        decisions_repo=None,
        risk_verdicts_repo=None,
        risk_events_repo=None,
        batch_size=1,
        backfill_max_per_tick=10,
        backfill_max_seconds=1.0,
        enabled=False,
        adaptive=True,
        stable_fast_ticks=0,
    )
    msg = main_mod.format_backfill_status(
        log_level="INFO",
        processed_total=processed_total,
        fetched_total=fetched_total,
        scanned_total=scanned_total,
        elapsed_ms=elapsed_ms,
        stop_reason=stop_reason,
        sample_ids=sample_ids,
        error_message=error_message,
        slow=slow,
        batch_size=next_batch_size,
        fetch_ms=fetch_ms_total,
        persist_ms=persist_ms_total,
        early_break=early_break,
        stable_fast_ticks=stable_fast,
    )
    assert stop_reason == "DISABLED"
    assert "stop_reason=DISABLED" in msg


def test_backfill_status_no_pending(monkeypatch):
    def fake_process(**kwargs):
        return {"processed": 0, "sample": None, "fetch_ms": 0, "persist_ms": 0, "early_break": False, "fetched": 0, "scanned": 0}

    monkeypatch.setattr(main_mod, "process_pending_previews", fake_process)
    processed_total, fetched_total, scanned_total, sample_ids, stop_reason, elapsed_ms, error_message, slow, next_batch_size, fetch_ms_total, persist_ms_total, early_break, stable_fast, _cursor = main_mod.run_backfill_tick(
        client=None,
        params=None,
        settings=None,
        agents_aggregator=None,
        risk_engine=None,
        decisions_repo=None,
        risk_verdicts_repo=None,
        risk_events_repo=DummyRiskEventsRepo(),
        batch_size=1,
        backfill_max_per_tick=10,
        backfill_max_seconds=1.0,
        enabled=True,
        adaptive=True,
        stable_fast_ticks=0,
    )
    msg = main_mod.format_backfill_status(
        log_level="INFO",
        processed_total=processed_total,
        fetched_total=fetched_total,
        scanned_total=scanned_total,
        elapsed_ms=elapsed_ms,
        stop_reason=stop_reason,
        sample_ids=sample_ids,
        error_message=error_message,
        slow=slow,
        batch_size=next_batch_size,
        fetch_ms=fetch_ms_total,
        persist_ms=persist_ms_total,
        early_break=early_break,
        stable_fast_ticks=stable_fast,
    )
    assert processed_total == 0
    assert stop_reason == "NO_PENDING"
    assert "stop_reason=NO_PENDING" in msg


def test_backfill_status_error(monkeypatch):
    events_repo = DummyRiskEventsRepo()

    def fake_process(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(main_mod, "process_pending_previews", fake_process)
    processed_total, fetched_total, scanned_total, sample_ids, stop_reason, elapsed_ms, error_message, slow, next_batch_size, fetch_ms_total, persist_ms_total, early_break, stable_fast, _cursor = main_mod.run_backfill_tick(
        client=None,
        params=None,
        settings=None,
        agents_aggregator=None,
        risk_engine=None,
        decisions_repo=None,
        risk_verdicts_repo=None,
        risk_events_repo=events_repo,
        batch_size=1,
        backfill_max_per_tick=10,
        backfill_max_seconds=1.0,
        enabled=True,
        adaptive=True,
        stable_fast_ticks=0,
    )
    msg = main_mod.format_backfill_status(
        log_level="INFO",
        processed_total=processed_total,
        fetched_total=fetched_total,
        scanned_total=scanned_total,
        elapsed_ms=elapsed_ms,
        stop_reason=stop_reason,
        sample_ids=sample_ids,
        error_message=error_message,
        slow=slow,
        batch_size=next_batch_size,
        fetch_ms=fetch_ms_total,
        persist_ms=persist_ms_total,
        early_break=early_break,
        stable_fast_ticks=stable_fast,
    )
    assert stop_reason == "ERROR"
    assert error_message == "boom"
    assert "stop_reason=ERROR" in msg
    assert "error=boom" in msg
    assert events_repo.calls
