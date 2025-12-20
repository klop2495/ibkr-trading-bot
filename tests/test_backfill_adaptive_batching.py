import app.main as main_mod


def test_batch_size_decreases_when_slow(monkeypatch):
    def fake_process(**kwargs):
        return {"processed": 1, "sample": None, "fetch_ms": 0, "persist_ms": 0, "early_break": False, "fetched": 1, "scanned": 1}

    times = [0.0, 0.5, 3.5, 3.6]  # elapsed after loop > max_seconds*2

    def fake_monotonic():
        return times.pop(0)

    monkeypatch.setattr(main_mod, "process_pending_previews", fake_process)
    monkeypatch.setattr(main_mod.time, "perf_counter", fake_monotonic)

    _, _, _, _, stop_reason, elapsed_ms, _, _, next_batch_size, _, _, _, _, _ = main_mod.run_backfill_tick(
        client=None,
        params=None,
        settings=None,
        agents_aggregator=None,
        risk_engine=None,
        decisions_repo=None,
        risk_verdicts_repo=None,
        risk_events_repo=None,
        batch_size=100,
        backfill_max_per_tick=10,
        backfill_max_seconds=1.0,
        enabled=True,
        adaptive=True,
        stable_fast_ticks=0,
    )
    assert stop_reason in {"MAX_SECONDS", "MAX_PER_TICK", "DRAINED_BATCH", "RECENT_DRAINED", "NO_PENDING"}
    assert next_batch_size < 100
    assert next_batch_size >= 5


def test_batch_size_increases_when_fast(monkeypatch):
    def fake_process(**kwargs):
        return {
            "processed": kwargs["limit"],
            "sample": None,
            "fetch_ms": 0,
            "persist_ms": 0,
            "early_break": False,
            "fetched": kwargs["limit"],
            "scanned": kwargs["limit"],
        }

    times = [0.0, 0.1, 0.2, 0.3]

    def fake_monotonic():
        return times.pop(0)

    monkeypatch.setattr(main_mod, "process_pending_previews", fake_process)
    monkeypatch.setattr(main_mod.time, "perf_counter", fake_monotonic)

    processed_total, _, _, _, stop_reason, elapsed_ms, _, _, next_batch_size, _, _, _, _, _ = main_mod.run_backfill_tick(
        client=None,
        params=None,
        settings=None,
        agents_aggregator=None,
        risk_engine=None,
        decisions_repo=None,
        risk_verdicts_repo=None,
        risk_events_repo=None,
        batch_size=10,
        backfill_max_per_tick=10,
        backfill_max_seconds=2.0,
        enabled=True,
        adaptive=True,
        stable_fast_ticks=0,
    )
    assert stop_reason == "MAX_PER_TICK"
    assert processed_total >= 10
    assert elapsed_ms < 2000
    assert next_batch_size >= 10
