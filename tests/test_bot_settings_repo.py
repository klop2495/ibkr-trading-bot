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
            "signals_params": None,
        }
    ]
    repo = BotSettingsRepo(db=DummyDB(payload))  # type: ignore
    s = repo.get(owner_user_id=owner)
    assert s.trading_enabled is True
    assert s.symbols == ["EURUSD", "GBPUSD"]
    assert s.signals_params.is_configured() is False


def test_bot_settings_repo_parses_signals_params():
    owner = uuid4()
    payload = [
        {
            "owner_user_id": str(owner),
            "trading_enabled": True,
            "mode": "paper",
            "symbols": ["eurusd"],
            "signals_params": {
                "schema_version": 1,
                "enabled": True,
                "rules_version": 1,
                "timeframes": {"h4": "H4", "h1": "H1", "m15": "M15"},
                "gates": {
                    "require_warmup": True,
                    "require_data_ok": True,
                    "require_spread_ok": True,
                    "disallow_h4_neutral": True,
                },
                "spread": {"wide_spread_pips": 0.1},
                "structure": {
                    "swing_lookback_bars": 20,
                    "swing_min_separation_bars": 5,
                    "sl_buffer_mode": "PIPS",
                    "sl_buffer_pips": 5,
                    "min_sl_pips": 5,
                    "max_sl_pips": 50,
                },
                "confidence": {"high_conf_rule": "H4_H1_ALIGN_AND_M15_MOMENTUM_CONFIRM"},
                "rr": {"mode": "contextual", "base_rr": 2.0, "high_conf_rr": 3.0},
                "filters": {"trade_hours_utc": ["06:00-20:00"]},
                "m15_confirm": {"rsi_period": 14, "rsi_long_min": 55, "rsi_short_max": 45, "sma_period": 50},
            },
        }
    ]
    repo = BotSettingsRepo(db=DummyDB(payload))  # type: ignore
    s = repo.get(owner_user_id=owner)
    assert s.signals_params.is_configured() is True
