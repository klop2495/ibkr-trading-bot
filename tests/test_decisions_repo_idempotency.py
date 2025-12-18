from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models.decision import DecisionV1
from app.storage.repositories import DecisionsRepo


class DummyExecute:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def select(self, *args, **kwargs):
        return self

    def execute(self):
        self.calls += 1
        if self.fail:
            raise Exception("duplicate key value violates unique constraint")
        return type("Obj", (), {"data": [{"id": "new-id"}]})()

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self


class DummyTable:
    def __init__(self, executor: DummyExecute):
        self.executor = executor

    def insert(self, payload):
        return self.executor

    def select(self, *args, **kwargs):
        return self.executor


class DummyDB:
    def __init__(self, executor: DummyExecute):
        self.client = self
        self.executor = executor

    def table(self, name):
        return DummyTable(self.executor)


def _decision():
    return DecisionV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        signal_preview_id=uuid4(),
        trade_allowed=True,
        risk_modifier=1.0,
        flags=[],
    )


def test_decision_repo_inserts_and_returns_id():
    executor = DummyExecute(fail=False)
    repo = DecisionsRepo(db=DummyDB(executor))  # type: ignore
    dec_id = repo.insert_decision(_decision())
    assert dec_id == "new-id"


def test_decision_repo_idempotent_on_duplicate():
    class DupThenSelectTable:
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
            return type("Obj", (), {"data": [{"id": "existing-id"}]})()

    repo = DecisionsRepo(db=type("Obj", (), {"client": type("Obj", (), {"table": lambda self, _: DupThenSelectTable()})()})())  # type: ignore
    dec_id = repo.insert_decision(_decision())
    assert dec_id == "existing-id"


def test_decision_repo_raises_on_non_unique_error():
    class BadInsert:
        def insert(self, payload):
            raise Exception("connection refused")

    repo = DecisionsRepo(db=type("Obj", (), {"client": type("Obj", (), {"table": lambda self, _: BadInsert()})()})())  # type: ignore
    with pytest.raises(Exception):
        repo.insert_decision(_decision())
