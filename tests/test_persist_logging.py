from uuid import uuid4

from app.main import persist_control_decision_and_verdict


class DummyRepo:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.insert_calls = 0

    def insert_decision(self, *_args, **_kwargs):
        self.insert_calls += 1
        if self.should_fail:
            raise RuntimeError("fail decision")
        return str(uuid4())

    def insert_verdict(self, *_args, **_kwargs):
        self.insert_calls += 1
        if self.should_fail:
            raise RuntimeError("fail verdict")
        return str(uuid4())


class DummyRiskEventsRepo:
    def __init__(self):
        self.events = []

    def insert(self, **kwargs):
        self.events.append(kwargs)
        return kwargs


def _preview():
    return type(
        "Preview",
        (),
        {
            "ts_utc": None,
            "symbol": "EURUSD",
            "flags": [],
            "setup_type": type("Obj", (), {"value": "NO_TRADE"})(),
            "direction": type("Obj", (), {"value": "flat"})(),
        },
    )()


def test_persist_logs_on_decision_exception():
    decisions_repo = DummyRepo(should_fail=True)
    verdicts_repo = DummyRepo()
    risk_events = DummyRiskEventsRepo()
    preview_id = uuid4()

    decision_id, verdict_id, decision, verdict = persist_control_decision_and_verdict(
        preview=_preview(),
        preview_id=preview_id,
        params=None,
        settings=type("S", (), {"trading_enabled": True})(),
        agents_aggregator=None,
        risk_engine=type("R", (), {"evaluate": lambda *args, **kwargs: None})(),
        decisions_repo=decisions_repo,
        risk_verdicts_repo=verdicts_repo,
        risk_events_repo=risk_events,
    )

    assert decision_id is None
    assert verdict_id is None
    assert len(risk_events.events) == 1
    event = risk_events.events[0]
    assert event["event_type"] == "CONTROL_DECISION_PERSIST"
    assert "symbol" not in event or event["symbol"] is None  # symbol goes into data
    assert "symbol" in event.get("data", {})
    assert "signal_preview_id" in event.get("data", {}) or "preview_id" in event.get("data", {})
