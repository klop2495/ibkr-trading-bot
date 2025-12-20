import app.main as main_mod


def test_backfill_handles_oversized_batch_return(monkeypatch):
    def fake_process(**kwargs):
        # returns more than expected items to trigger unpack handling
        return (1, None, 0, 0, False, 1, "extra")

    monkeypatch.setattr(main_mod, "process_pending_previews", fake_process)

    processed_total, fetched_total, scanned_total, sample_ids, stop_reason, elapsed_ms, error_message, slow, next_batch_size, fetch_ms_total, persist_ms_total, early_break, stable_fast, _cursor = main_mod.run_backfill_tick(
        client=None,
        params=None,
        settings=None,
        agents_aggregator=None,
        risk_engine=None,
        decisions_repo=None,
        risk_verdicts_repo=None,
        risk_events_repo=None,
        batch_size=5,
        backfill_max_per_tick=5,
        backfill_max_seconds=1.0,
        enabled=True,
        adaptive=False,
        stable_fast_ticks=0,
    )

    assert stop_reason == "ERROR"
    assert error_message == "BACKFILL_CONTRACT_VIOLATION"
    assert processed_total in {0, 1}
    assert next_batch_size >= 1
    assert elapsed_ms >= 0


def test_backfill_contract_missing_key(monkeypatch):
    def fake_process(**kwargs):
        # missing "fetched"
        return {"processed": 1, "sample": None, "fetch_ms": 0, "persist_ms": 0, "early_break": False, "scanned": 1}

    monkeypatch.setattr(main_mod, "process_pending_previews", fake_process)

    processed_total, fetched_total, scanned_total, sample_ids, stop_reason, elapsed_ms, error_message, slow, next_batch_size, fetch_ms_total, persist_ms_total, early_break, stable_fast, _cursor = main_mod.run_backfill_tick(
        client=None,
        params=None,
        settings=None,
        agents_aggregator=None,
        risk_engine=None,
        decisions_repo=None,
        risk_verdicts_repo=None,
        risk_events_repo=None,
        batch_size=5,
        backfill_max_per_tick=5,
        backfill_max_seconds=1.0,
        enabled=True,
        adaptive=False,
        stable_fast_ticks=0,
    )

    assert stop_reason == "ERROR"
    assert error_message == "BACKFILL_CONTRACT_VIOLATION"
    assert processed_total == 0
    assert fetched_total == 0
