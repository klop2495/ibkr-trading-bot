import app.main as main_mod


def test_downshift_uses_throughput(monkeypatch):
    def fake_process(**kwargs):
        return {"processed": 3, "sample": None, "fetch_ms": 0, "persist_ms": 0, "early_break": False, "fetched": 3, "scanned": 3}

    times = [0.0, 0.0, 1.7, 1.7]  # elapsed_ms ~1700 > budget 1500

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
        batch_size=10,
        backfill_max_per_tick=50,
        backfill_max_seconds=1.5,
        enabled=True,
        adaptive=True,
        stable_fast_ticks=0,
    )
    assert stop_reason in {"MAX_SECONDS", "MAX_PER_TICK", "DRAINED_BATCH", "RECENT_DRAINED", "NO_PENDING"}
    assert next_batch_size <= 10
    assert next_batch_size >= 3


def test_no_pending_stop_reason(monkeypatch):
    def fake_process(**kwargs):
        return {"processed": 0, "sample": None, "fetch_ms": 0, "persist_ms": 0, "early_break": False, "fetched": 0, "scanned": 0}

    monkeypatch.setattr(main_mod, "process_pending_previews", fake_process)
    monkeypatch.setattr(main_mod.time, "perf_counter", lambda: 0.0)

    processed_total, _, _, _, stop_reason, _, _, _, _, _, _, _, _, _ = main_mod.run_backfill_tick(
        client=None,
        params=None,
        settings=None,
        agents_aggregator=None,
        risk_engine=None,
        decisions_repo=None,
        risk_verdicts_repo=None,
        risk_events_repo=None,
        batch_size=5,
        backfill_max_per_tick=10,
        backfill_max_seconds=1.0,
        enabled=True,
        adaptive=False,
        stable_fast_ticks=0,
    )
    assert processed_total == 0
    assert stop_reason in {"NO_PENDING", "ERROR"}


def test_upshift_requires_three_fast_ticks(monkeypatch):
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

    t = [0.0]

    def fake_monotonic():
        t[0] += 0.1
        return t[0]

    monkeypatch.setattr(main_mod, "process_pending_previews", fake_process)
    monkeypatch.setattr(main_mod.time, "perf_counter", fake_monotonic)

    stable_fast = 0
    batch_size = 5
    for i in range(3):
            processed_total, _, _, _, stop_reason, elapsed_ms, _, _, next_batch_size, _, _, _, stable_fast, _cursor = main_mod.run_backfill_tick(
                client=None,
                params=None,
                settings=None,
                agents_aggregator=None,
                risk_engine=None,
                decisions_repo=None,
                risk_verdicts_repo=None,
                risk_events_repo=None,
                batch_size=batch_size,
                backfill_max_per_tick=5,
                backfill_max_seconds=10.0,
                enabled=True,
                adaptive=True,
                stable_fast_ticks=stable_fast,
            )
            batch_size = next_batch_size
            assert stop_reason in {"MAX_PER_TICK", "NO_PENDING", "MAX_SECONDS"}
            assert elapsed_ms <= 10000
    assert batch_size >= 7  # should have increased after 3 fast ticks
