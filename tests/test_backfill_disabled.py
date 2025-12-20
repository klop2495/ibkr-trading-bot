import app.main as main_mod


def test_backfill_disabled_skips(monkeypatch):
    def fake_process(**kwargs):
        assert False, "process should not be called when disabled"

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
        batch_size=1,
        backfill_max_per_tick=10,
        backfill_max_seconds=1.0,
        enabled=False,
        adaptive=True,
        stable_fast_ticks=0,
    )

    assert processed_total == 0
    assert sample_ids is None
    assert stop_reason == "DISABLED"
    assert error_message is None
    assert slow is False
    assert next_batch_size == 1
    assert fetch_ms_total == 0
    assert persist_ms_total == 0
    assert early_break is False
    assert stable_fast == 0
