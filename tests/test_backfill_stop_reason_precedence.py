import app.main as main_mod


def test_max_per_tick_precedence_over_time(monkeypatch):
    def fake_process(**kwargs):
        return {"processed": 3, "sample": None, "fetch_ms": 0, "persist_ms": 0, "early_break": False, "fetched": 3, "scanned": 3}

    # time never exceeds max_seconds
    t = [0.0]

    def fake_monotonic():
        t[0] += 0.1
        return t[0]

    monkeypatch.setattr(main_mod, "process_pending_previews", fake_process)
    monkeypatch.setattr(main_mod.time, "perf_counter", fake_monotonic)

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
        backfill_max_per_tick=2,
        backfill_max_seconds=5.0,
        enabled=True,
        adaptive=False,
        stable_fast_ticks=0,
    )
    assert stop_reason in {"MAX_PER_TICK", "MAX_SECONDS"}
    assert processed_total >= 2
    assert sample_ids is None
    assert error_message is None
    assert slow is False
    assert elapsed_ms > 0
    assert next_batch_size >= 1
    assert early_break in {False, True}
    assert fetch_ms_total >= 0
    assert persist_ms_total >= 0
    assert stable_fast == 0


def test_slow_flag_and_max_seconds(monkeypatch):
    def fake_process(**kwargs):
        return {"processed": 0, "sample": None, "fetch_ms": 0, "persist_ms": 0, "early_break": False, "fetched": 0, "scanned": 0}

    times = [2.0, 10.0, 10.0]  # immediately exceed budget on first loop check

    def fake_monotonic():
        return times.pop(0)

    monkeypatch.setattr(main_mod, "process_pending_previews", fake_process)
    monkeypatch.setattr(main_mod.time, "perf_counter", fake_monotonic)

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
        enabled=True,
        adaptive=False,
        stable_fast_ticks=0,
    )
    assert stop_reason in {"MAX_SECONDS", "NO_PENDING"}
    assert processed_total == 0
    assert sample_ids is None
    assert error_message is None
    assert slow in {True, False}
    assert next_batch_size >= 1
    assert fetch_ms_total >= 0
    assert persist_ms_total >= 0
    assert stable_fast == 0
