from datetime import datetime, timezone
from uuid import uuid4

from app.execution.oms_stub import create_order_intent
from app.models.decision import DecisionV1
from app.models.execution_report_v1 import ExecutionReportV1
from app.models.order_intent import OrderIntentV1
from app.models.signal_preview import SignalPreviewV1, SetupType, Direction, Confidence, DataQuality, SpreadQuality, TimeframeTrigger
from app.storage.repositories import OrderIntentsRepo


def _preview(direction: Direction):
    return SignalPreviewV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        setup_type=SetupType.SWING_CONTINUATION,
        direction=direction,
        setup_present=True,
        entry_triggered=True,
        confidence=Confidence.NORMAL,
        rr=2.0,
        data_quality=DataQuality.OK,
        spread_quality=SpreadQuality.OK,
        flags=[],
    )


def _decision():
    return DecisionV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        signal_preview_id=uuid4(),
        trade_allowed=True,
        risk_modifier=1.0,
        flags=[],
    )


class DummyRepo:
    def __init__(self, fail=False):
        self.fail = fail
        self.called = 0

    def insert_intent(self, intent: OrderIntentV1):
        self.called += 1
        if self.fail:
            raise Exception("duplicate key value violates unique constraint")
        return "intent-id"


def test_create_order_intent_side_mapping():
    decision = _decision()
    decision.id = uuid4()
    exec_report = ExecutionReportV1(
        ts_utc=datetime.now(timezone.utc),
        decision_id=decision.id,
        signal_preview_id=decision.signal_preview_id,
        status="PLANNED",
        reason_flags=[],
        details={"action": "OPEN"},
    )
    intent = create_order_intent(
        plan_action="OPEN",
        execution_report=exec_report,
        decision=decision,
        preview=_preview(Direction.LONG),
    )
    assert intent.side == "buy"
    assert intent.intent_type == "OPEN_MARKET"


def test_order_intent_repo_idempotent_on_duplicate():
    class DupThenSelect:
        def __init__(self):
            self.insert_called = False

        def insert(self, payload):
            self.insert_called = True
            raise Exception("duplicate key value violates unique constraint")

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def execute(self):
            return type("Obj", (), {"data": [{"id": "existing-intent"}]})()

    repo = OrderIntentsRepo(db=type("Obj", (), {"client": type("Obj", (), {"table": lambda self, _: DupThenSelect()})()})())  # type: ignore
    decision = _decision()
    decision.id = uuid4()
    exec_report = ExecutionReportV1(
        ts_utc=datetime.now(timezone.utc),
        decision_id=decision.id,
        signal_preview_id=decision.signal_preview_id,
        status="PLANNED",
        reason_flags=[],
        details={"action": "OPEN"},
    )
    intent = create_order_intent(
        plan_action="OPEN",
        execution_report=exec_report,
        decision=decision,
        preview=_preview(Direction.SHORT),
    )
    intent_id = repo.insert_intent(intent)
    assert intent_id == "existing-intent"
