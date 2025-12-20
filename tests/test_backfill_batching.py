import app.main as main_mod

def test_backfill_batches_respect_max(monkeypatch):
    calls = []

    def fake_process(*, limit, **kwargs):
        calls.append(limit)
        if len(calls) == 1:
            return {"processed": 3, "sample": ("d1", "v1", "p1"), "fetch_ms": 0, "persist_ms": 0, "early_break": False, "fetched": 3, "scanned": 3}
        if len(calls) == 2:
            return {"processed": 3, "sample": ("d2", "v2", "p2"), "fetch_ms": 0, "persist_ms": 0, "early_break": False, "fetched": 3, "scanned": 3}
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
        risk_events_repo=None,
        batch_size=3,
        backfill_max_per_tick=5,
        backfill_max_seconds=10.0,
        enabled=True,
        adaptive=False,
        stable_fast_ticks=0,
    )
    assert processed_total == 6  # stopped after exceeding max_per_tick
    assert sample_ids == ("d1", "v1", "p1")
    assert stop_reason in {"MAX_PER_TICK", "MAX_SECONDS"}
    assert len(calls) == 2
    assert error_message is None
    assert slow is False
    assert fetch_ms_total >= 0
    assert persist_ms_total >= 0
    assert early_break in {False, True}
