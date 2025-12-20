import time

import app.main as main_mod


def test_backfill_stops_on_max_seconds(monkeypatch):
    calls = []

    def fake_process(*, limit, **kwargs):
        calls.append(limit)
        return {"processed": 1, "sample": ("d1", "v1", "p1"), "fetch_ms": 0, "persist_ms": 0, "early_break": False, "fetched": 1, "scanned": 1}

    monkeypatch.setattr(main_mod, "process_pending_previews", fake_process)

    # Simulate time moving slowly then jumping over the limit
    t0 = [0.0]

    def fake_time():
        # first two calls -> small increments, then exceed
        t0[0] += 0.6
        return t0[0]

    monkeypatch.setattr(main_mod.time, "perf_counter", fake_time)

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

    assert stop_reason in {"MAX_SECONDS", "MAX_PER_TICK"}
    assert processed_total >= 1
    assert sample_ids == ("d1", "v1", "p1")
    assert error_message is None
    assert elapsed_ms >= 1000
    assert fetch_ms_total >= 0
    assert persist_ms_total >= 0
