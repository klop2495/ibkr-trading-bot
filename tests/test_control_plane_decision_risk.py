from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.main import persist_control_decision_and_verdict
from app.models.signal_preview import (
    Confidence,
    DataQuality,
    Direction,
    SetupType,
    SignalPreviewV1,
    SpreadQuality,
)
from app.risk.engine_v1 import RiskEngineV1


class DummyDecisionsRepo:
    def __init__(self):
        self.saved = []
        self._id = str(uuid4())

    def insert_decision(self, decision):
        self.saved.append(decision)
        return self._id


class DummyRiskVerdictsRepo:
    def __init__(self):
        self.saved = []
        self._id = str(uuid4())

    def insert_verdict(self, verdict):
        self.saved.append(verdict)
        return self._id


class DummyRiskEventsRepo:
    def __init__(self):
        self.events = []

    def insert(self, **kwargs):
        self.events.append(kwargs)
        return kwargs


class DummyAggregator:
    def run(self, preview, params, signal_preview_id=None):
        return {
            "trade_allowed": True,
            "risk_modifier": 1.5,
            "flags": ["AGG_FLAG"],
            "commentary": "ok",
        }


class DummyParams:
    def is_configured(self):
        return False


def _preview():
    return SignalPreviewV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        timeframe_trigger="M15",
        setup_type=SetupType.NO_TRADE,
        direction=Direction.FLAT,
        setup_present=False,
        entry_triggered=False,
        confidence=Confidence.LOW,
        rr=0.0,
        data_quality=DataQuality.OK,
        spread_quality=SpreadQuality.OK,
        flags=["TEST_FLAG"],
    )


def test_decision_and_verdict_persist_for_no_trade_preview():
    decisions_repo = DummyDecisionsRepo()
    verdicts_repo = DummyRiskVerdictsRepo()
    events_repo = DummyRiskEventsRepo()
    preview = _preview()
    preview_id = uuid4()

    decision_id, verdict_id, decision, verdict = persist_control_decision_and_verdict(
        preview=preview,
        preview_id=preview_id,
        params=DummyParams(),
        settings=SimpleNamespace(trading_enabled=True),
        agents_aggregator=DummyAggregator(),
        risk_engine=RiskEngineV1(),
        decisions_repo=decisions_repo,
        risk_verdicts_repo=verdicts_repo,
        risk_events_repo=events_repo,
    )

    assert decision_id == decisions_repo._id
    assert verdict_id == verdicts_repo._id
    assert decision is decisions_repo.saved[0]
    assert verdict is verdicts_repo.saved[0]
    assert decision.trade_allowed is False  # forced hold for NO_TRADE/FLAT
    assert verdict.trade_allowed is False
    assert "TEST_FLAG" in decision.flags
    assert events_repo.events == []
    assert str(preview_id) != decision_id
    assert str(verdict.decision_id) == decision_id
    assert decision.id is not None
    assert decision.id == decision_id


def test_decision_persist_error_logs_risk_event():
    class FailingDecisionsRepo(DummyDecisionsRepo):
        def insert_decision(self, decision):
            raise RuntimeError("fail-decision")

    decisions_repo = FailingDecisionsRepo()
    verdicts_repo = DummyRiskVerdictsRepo()
    events_repo = DummyRiskEventsRepo()
    preview = _preview()
    preview_id = uuid4()

    decision_id, verdict_id, decision, verdict = persist_control_decision_and_verdict(
        preview=preview,
        preview_id=preview_id,
        params=None,
        settings=SimpleNamespace(trading_enabled=True),
        agents_aggregator=DummyAggregator(),
        risk_engine=RiskEngineV1(),
        decisions_repo=decisions_repo,
        risk_verdicts_repo=verdicts_repo,
        risk_events_repo=events_repo,
    )

    assert decision_id is None
    assert verdict_id is None
    assert decision is not None
    assert verdict is None
    assert len(events_repo.events) == 1
    assert events_repo.events[0]["event_type"] == "CONTROL_DECISION_PERSIST"
