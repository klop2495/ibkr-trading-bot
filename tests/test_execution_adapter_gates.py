import os
from datetime import datetime, timezone
from uuid import uuid4

from app.execution.gates import is_execution_enabled
from app.execution.adapters.null_adapter import NullExecutionAdapter
from app.execution.adapters.ibkr_adapter import IBKRExecutionAdapter
from app.execution.oms_stub import create_order_intent
from app.models.decision import DecisionV1
from app.models.execution_report_v1 import ExecutionReportV1
from app.models.signal_preview import SignalPreviewV1, SetupType, Direction, Confidence, DataQuality, SpreadQuality
from app.models.order_intent import OrderIntentV1
from app.models.broker_request import BrokerRequestV1
from app.storage.repositories import BrokerRequestsRepo


def _preview(direction=Direction.LONG):
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


def _decision(trading_enabled=True, mode="paper"):
    dec = DecisionV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        signal_preview_id=uuid4(),
        trade_allowed=True,
        risk_modifier=1.0,
        flags=[],
    )
    dec.id = uuid4()
    dec._mode_override = mode
    return dec


class DummySettings:
    def __init__(self, trading_enabled=True, mode="paper"):
        self.trading_enabled = trading_enabled
        self.mode = mode


class DummyBrokerRepo:
    def __init__(self):
        self.called = 0
        self.last = None

    def insert_request(self, req: BrokerRequestV1):
        self.called += 1
        self.last = req
        return "broker-id"


def test_is_execution_enabled_false_on_env_missing():
    env = {}
    settings = DummySettings(trading_enabled=True, mode="paper")
    assert is_execution_enabled(settings, env) is False


def test_is_execution_enabled_true_for_paper_when_env_allows():
    env = {"IBKR_ENABLED": "1", "EXECUTION_ENABLED": "1"}
    settings = DummySettings(trading_enabled=True, mode="paper")
    assert is_execution_enabled(settings, env) is True


def test_adapter_not_called_when_disabled():
    env = {"IBKR_ENABLED": "0", "EXECUTION_ENABLED": "0"}
    settings = DummySettings(trading_enabled=True, mode="paper")
    enabled = is_execution_enabled(settings, env)
    assert enabled is False
    repo = DummyBrokerRepo()
    decision = _decision()
    exec_report = ExecutionReportV1(
        ts_utc=datetime.now(timezone.utc),
        decision_id=decision.id,
        signal_preview_id=decision.signal_preview_id,
        status="PLANNED",
        reason_flags=[],
        details={"action": "OPEN"},
    )
    intent = create_order_intent(
        plan_action="OPEN", execution_report=exec_report, decision=decision, preview=_preview()
    )
    adapter = NullExecutionAdapter()
    prepared = adapter.prepare(intent)
    br = BrokerRequestV1(
        ts_utc=exec_report.ts_utc,
        order_intent_id=intent.id,
        decision_id=decision.id,
        signal_preview_id=decision.signal_preview_id,
        execution_report_id=exec_report.id,
        status="SKIPPED",
        flags=["EXECUTION_DISABLED"],
        payload=prepared.model_dump(),
    )
    repo.insert_request(br)
    assert repo.called == 1
    assert repo.last.status == "SKIPPED"


def test_adapter_called_when_enabled():
    env = {"IBKR_ENABLED": "1", "EXECUTION_ENABLED": "1"}
    settings = DummySettings(trading_enabled=True, mode="paper")
    assert is_execution_enabled(settings, env) is True
    repo = DummyBrokerRepo()
    decision = _decision()
    exec_report = ExecutionReportV1(
        ts_utc=datetime.now(timezone.utc),
        decision_id=decision.id,
        signal_preview_id=decision.signal_preview_id,
        status="PLANNED",
        reason_flags=[],
        details={"action": "OPEN"},
    )
    intent = create_order_intent(
        plan_action="OPEN", execution_report=exec_report, decision=decision, preview=_preview(Direction.SHORT)
    )
    adapter = IBKRExecutionAdapter()
    prepared = adapter.prepare(intent)
    br = BrokerRequestV1(
        ts_utc=exec_report.ts_utc,
        order_intent_id=intent.id,
        decision_id=decision.id,
        signal_preview_id=decision.signal_preview_id,
        execution_report_id=exec_report.id,
        status="PREPARED",
        flags=[],
        payload=prepared.model_dump(),
    )
    repo.insert_request(br)
    assert repo.called == 1
    assert repo.last.status == "PREPARED"
