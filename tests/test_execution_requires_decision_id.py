from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.execution.engine_stub import ExecutionEngineStub
from app.execution.oms_stub import create_order_intent
from app.execution.runner import run_execution_if_allowed
from app.models.decision import DecisionV1
from app.models.execution_plan import ExecutionPlanV1
from app.models.execution_report_v1 import ExecutionReportV1
from app.models.risk_verdict import RiskVerdictV1


def _decision(with_id: bool):
    dec = DecisionV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        signal_preview_id=uuid4(),
        trade_allowed=True,
        risk_modifier=1.0,
        flags=[],
    )
    if with_id:
        dec.id = uuid4()
    return dec


def _verdict(decision, allowed=True):
    return RiskVerdictV1(
        ts_utc=decision.ts_utc,
        symbol=decision.symbol,
        decision_id=decision.id or uuid4(),
        signal_preview_id=decision.signal_preview_id,
        trade_allowed=allowed,
        risk_modifier=1.0,
        flags=[],
        commentary=None,
    )


def test_engine_stub_requires_decision_id():
    engine = ExecutionEngineStub()
    dec = _decision(with_id=False)
    verdict = _verdict(dec)
    with pytest.raises(ValueError):
        engine.plan(verdict, dec)


def test_runner_requires_decision_id():
    dec = _decision(with_id=False)
    verdict = _verdict(dec)

    class DummyExecEngine:
        def plan(self, v, d):
            return ExecutionPlanV1(
                decision_id=d.id or uuid4(),
                signal_preview_id=d.signal_preview_id,
                symbol=d.symbol,
                action="OPEN",
                notes=None,
            )

    class DummyRepo:
        def insert_report(self, rep):
            return str(uuid4())

    with pytest.raises(ValueError):
        run_execution_if_allowed(verdict, dec, DummyExecEngine(), DummyRepo())  # type: ignore[arg-type]


def test_create_order_intent_requires_decision_id():
    dec = _decision(with_id=False)
    verdict = _verdict(dec)
    report = ExecutionReportV1(
        ts_utc=verdict.ts_utc,
        decision_id=verdict.decision_id,
        signal_preview_id=verdict.signal_preview_id,
        status="PLANNED",
        reason_flags=[],
        details={"action": "OPEN"},
    )
    with pytest.raises(ValueError):
        create_order_intent(plan_action="OPEN", execution_report=report, decision=dec, preview=None)  # type: ignore[arg-type]


def test_create_order_intent_uses_decision_id_when_present():
    dec = _decision(with_id=True)
    verdict = _verdict(dec)
    report = ExecutionReportV1(
        ts_utc=verdict.ts_utc,
        decision_id=verdict.decision_id,
        signal_preview_id=verdict.signal_preview_id,
        status="PLANNED",
        reason_flags=[],
        details={"action": "OPEN"},
    )
    # Provide minimal preview stub to satisfy side calculation
    class PreviewStub:
        direction = type("Dir", (), {"value": "long"})  # not used; _side_from_preview expects enum
    preview = type("Obj", (), {"direction": type("Dir", (), {"value": "long"})(), "symbol": dec.symbol, "ts_utc": dec.ts_utc})()
    intent = create_order_intent(plan_action="OPEN", execution_report=report, decision=dec, preview=preview)  # type: ignore[arg-type]
    assert intent.decision_id == dec.id
