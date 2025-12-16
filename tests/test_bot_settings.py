import pytest
from uuid import uuid4

from pydantic import ValidationError

from app.models.bot_settings import BotSettings


def test_bot_settings_forbid_extra():
    with pytest.raises(ValidationError):
        BotSettings(owner_user_id=uuid4(), trading_enabled=False, symbols=[], extra_field=True)  # type: ignore[arg-type]


def test_bot_settings_symbols_normalization():
    s = BotSettings(
        owner_user_id=uuid4(),
        trading_enabled=True,
        symbols=["eurusd", " EURUSD ", "gbpusd", "gbpusd", ""],
    )
    assert s.symbols == ["EURUSD", "GBPUSD"]
