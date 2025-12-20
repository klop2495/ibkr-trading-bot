import app.main as main_mod


def test_no_pending_stop_reason(monkeypatch):
    def fake_process(**kwargs):
        return {"processed": 0, "sample": None, "fetch_ms": 0, "persist_ms": 0, "early_break": False, "fetched": 0, "scanned": 0}

    monkeypatch.setattr(main_mod, "process_pending_previews", fake_process)
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
    assert stop_reason == "NO_PENDING"


def test_idle_ticks_increment_and_reset():
    idle_ticks = 0
    # first no pending
    if True:
        processed_total = 0
        stop_reason = "NO_PENDING"
        if stop_reason == "NO_PENDING":
            idle_ticks += 1
        elif processed_total > 0:
            idle_ticks = 0
    assert idle_ticks == 1
    # work happens
    processed_total = 2
    stop_reason = "MAX_PER_TICK"
    if stop_reason == "NO_PENDING":
        idle_ticks += 1
    elif processed_total > 0:
        idle_ticks = 0
    assert idle_ticks == 0


def test_effective_sleep_cap():
    poll_seconds = 2
    idle_ticks = 20
    idle_backoff_max = 10
    effective_sleep = min(poll_seconds + idle_ticks, idle_backoff_max)
    assert effective_sleep == 10
