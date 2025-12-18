from datetime import datetime, timezone

import pytest

from app.models.signal_preview import (
    Confidence,
    DataQuality,
    Direction,
    SetupType,
    SignalPreviewV1,
    SpreadQuality,
)
from app.storage.repositories import SignalPreviewsRepo


class DummyExecute:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.calls = 0

    def select(self, *args, **kwargs):
        return self

    def execute(self):
        self.calls += 1
        if self.should_fail:
            raise Exception("duplicate key value violates unique constraint")
        return type("Obj", (), {"data": [{"id": "new-id"}]})()


class DummyTable:
    def __init__(self, executor: DummyExecute):
        self.executor = executor
        self.last_payload = None
        self._selector = None

    def insert(self, payload):
        self.last_payload = payload
        return self.executor

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self


class DummyDB:
    def __init__(self, executor: DummyExecute):
        self.client = self
        self.executor = executor
        self.select_calls = 0

    def table(self, name):
        return DummyTable(self.executor)


class DuplicateThenSelect:
    """
    Simulates unique violation on insert then returns an id on select.
    """

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


def _preview():
    return SignalPreviewV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        timeframe_trigger="M15",
        setup_type=SetupType.SWING_CONTINUATION,
        direction=Direction.LONG,
        setup_present=True,
        entry_triggered=True,
        confidence=Confidence.NORMAL,
        rr=2.0,
        data_quality=DataQuality.OK,
        spread_quality=SpreadQuality.OK,
        flags=[],
    )


def test_insert_preview_happy_path():
    execu = DummyExecute()
    repo = SignalPreviewsRepo(db=DummyDB(execu))  # type: ignore
    repo.insert_preview(_preview())
    assert execu.calls == 1


def test_insert_preview_idempotent_on_duplicate():
    dup = DuplicateThenSelect()
    repo = SignalPreviewsRepo(db=type("Obj", (), {"client": type("Obj", (), {"table": lambda self, _: dup})()})())  # type: ignore
    preview_id = repo.insert_preview_return_id(_preview())
    assert preview_id == "existing-id"
