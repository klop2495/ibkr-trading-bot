from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models.risk_verdict import RiskVerdictV1
from app.storage.repositories import RiskVerdictsRepo


class DupThenSelect:
    def insert(self, payload):
        raise Exception("duplicate key value violates unique constraint")

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        return type("Obj", (), {"data": [{"id": "existing-id"}]})()


class BadInsert:
    def insert(self, payload):
        raise Exception("connection refused")


def _verdict():
    return RiskVerdictV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        decision_id=uuid4(),
        signal_preview_id=uuid4(),
        trade_allowed=True,
        risk_modifier=1.0,
        flags=[],
    )


def test_risk_verdict_repo_idempotent_on_duplicate():
    repo = RiskVerdictsRepo(db=type("Obj", (), {"client": type("Obj", (), {"table": lambda self, _: DupThenSelect()})()})())  # type: ignore
    verdict_id = repo.insert_verdict(_verdict())
    assert verdict_id == "existing-id"


def test_risk_verdict_repo_raises_on_non_unique():
    repo = RiskVerdictsRepo(db=type("Obj", (), {"client": type("Obj", (), {"table": lambda self, _: BadInsert()})()})())  # type: ignore
    with pytest.raises(Exception):
        repo.insert_verdict(_verdict())
