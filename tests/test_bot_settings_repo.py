from uuid import uuid4

from app.models.bot_settings import BotSettings
from app.storage.bot_settings_repo import BotSettingsRepo


class DummyResult:
    def __init__(self, data):
        self.data = data


class ErrorClient:
    def table(self, name):
        raise RuntimeError("db down")


class DummyTable:
    def __init__(self, data):
        self.data = data

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        return DummyResult(self.data)


class DummyDB:
    def __init__(self, data):
        self.client = self
        self._data = data

    def table(self, name):
        return DummyTable(self._data)


def test_bot_settings_repo_returns_safe_on_db_error():
    repo = BotSettingsRepo(db=type("Obj", (), {"client": ErrorClient()})())  # type: ignore
    owner_id = uuid4()
    s = repo.get(owner_user_id=owner_id)
    assert s.trading_enabled is False
    assert s.owner_user_id == owner_id


def test_bot_settings_repo_returns_safe_on_validation_error():
    bad_payload = [{"owner_user_id": str(uuid4()), "symbols": [123]}]
    repo = BotSettingsRepo(db=DummyDB(bad_payload))  # type: ignore
    s = repo.get(owner_user_id=uuid4())
    assert s.trading_enabled is False


def test_bot_settings_repo_parses_symbols():
    owner = uuid4()
    payload = [
        {
            "owner_user_id": str(owner),
            "trading_enabled": True,
            "mode": "paper",
            "symbols": ["eurusd", "GBPUSD"],
        }
    ]
    repo = BotSettingsRepo(db=DummyDB(payload))  # type: ignore
    s = repo.get(owner_user_id=owner)
    assert s.trading_enabled is True
    assert s.symbols == ["EURUSD", "GBPUSD"]
