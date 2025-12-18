from datetime import datetime, timezone
from uuid import uuid4

from app.execution.runner import run_execution_if_allowed
from app.models.decision import DecisionV1
from app.models.risk_verdict import RiskVerdictV1


class StubEngine:
    def __init__(self):
        self.called = 0

    def plan(self, verdict, decision):
        self.called += 1
        return type(
            "Plan",
            (),
            {
                "action": "OPEN",
                "decision_id": decision.id or decision.signal_preview_id,
                "signal_preview_id": decision.signal_preview_id,
                "symbol": decision.symbol,
            },
        )()


class StubRepo:
    def __init__(self):
        self.called = 0
        self.last = None

    def insert_report(self, rep):
        self.called += 1
        self.last = rep
        return "exec-id"


def _decision():
    return DecisionV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        signal_preview_id=uuid4(),
        trade_allowed=True,
        risk_modifier=1.0,
        flags=[],
    )


def _verdict(allowed: bool):
    return RiskVerdictV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        decision_id=uuid4(),
        signal_preview_id=uuid4(),
        trade_allowed=allowed,
        risk_modifier=1.0,
        flags=[],
    )


def test_execution_not_called_when_blocked():
    engine = StubEngine()
    repo = StubRepo()
    res = run_execution_if_allowed(_verdict(False), _decision(), engine, repo)
    assert res is None
    assert engine.called == 0
    assert repo.called == 0


def test_execution_persists_when_allowed():
    engine = StubEngine()
    repo = StubRepo()
    decision = _decision()
    decision.id = uuid4()
    res = run_execution_if_allowed(_verdict(True), decision, engine, repo)
    assert res is not None
    assert engine.called == 1
    assert repo.called == 1
    assert repo.last.status == "PLANNED"
