from datetime import datetime, timezone
from uuid import UUID

from app.models.signal_preview import (
    Confidence,
    DataQuality,
    Direction,
    SetupType,
    SignalPreviewV1,
    SpreadQuality,
)
from app.storage.repositories import SignalPreviewsRepo


class DummyResult:
    def __init__(self, data):
        self.data = data


class DummyTable:
    def __init__(self):
        self.insert_called = False
        self.payload = None
        self.select_called = False

    def insert(self, payload):
        self.insert_called = True
        self.payload = payload
        return self

    def execute(self):
        # simulate insert returning no rows
        return DummyResult([])

    # Select path should not be used in insert flow; if reached, track it
    def select(self, *args, **kwargs):
        self.select_called = True
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self


class DummyClient:
    def __init__(self):
        self.table_obj = DummyTable()

    def table(self, name):
        assert name == "signal_previews"
        return self.table_obj


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
        flags=["TEST"],
    )


def test_signal_previews_repo_inserts_without_select_on_insert():
    client = DummyClient()
    repo = SignalPreviewsRepo(db=type("Obj", (), {"client": client})())  # type: ignore
    preview = _preview()

    preview_id = repo.insert_preview_return_id(preview)

    assert client.table_obj.insert_called is True
    assert client.table_obj.select_called is False
    assert "id" in client.table_obj.payload
    assert UUID(preview_id)
    assert preview_id == client.table_obj.payload["id"]
