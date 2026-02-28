import pytest
from uuid import uuid4

from pydantic import ValidationError

from app.models.bot_settings import BotSettings, SignalsParams


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


def test_signals_params_configured_and_forbid_extra():
    cfg = {
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
        "spread": {"wide_spread_pips": 0.5},
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
    }
    params = SignalsParams.model_validate(cfg)
    assert params.is_configured() is True
    with pytest.raises(ValidationError):
        SignalsParams.model_validate({**cfg, "extra": True})  # type: ignore[arg-type]


def test_default_symbols_constant():
    from app.models.bot_settings import DEFAULT_SYMBOLS
    base_symbols = [
        "EURUSD",
        "GBPUSD",
        "USDJPY",
        "USDCHF",
        "AUDUSD",
        "USDCAD",
        "NZDUSD",
    ]
    assert DEFAULT_SYMBOLS[:7] == base_symbols
    assert len(DEFAULT_SYMBOLS) >= 7
