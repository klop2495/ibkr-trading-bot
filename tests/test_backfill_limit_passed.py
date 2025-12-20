import app.main as main_mod


def test_limit_matches_batch_size(monkeypatch):
    seen_limits = []

    def fake_process(*, limit, **kwargs):
        seen_limits.append(limit)
        return {"processed": 0, "sample": None, "fetch_ms": 0, "persist_ms": 0, "early_break": False, "fetched": 0, "scanned": 0}

    monkeypatch.setattr(main_mod, "process_pending_previews", fake_process)
    monkeypatch.setattr(main_mod.time, "perf_counter", lambda: 0.0)

    main_mod.run_backfill_tick(
        client=None,
        params=None,
        settings=None,
        agents_aggregator=None,
        risk_engine=None,
        decisions_repo=None,
        risk_verdicts_repo=None,
        risk_events_repo=None,
        batch_size=37,
        backfill_max_per_tick=10,
        backfill_max_seconds=1.0,
        enabled=True,
        adaptive=False,
        stable_fast_ticks=0,
    )
    assert seen_limits[0] == 10
    assert len(seen_limits) >= 1
