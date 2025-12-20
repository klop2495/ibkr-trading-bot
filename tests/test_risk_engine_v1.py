from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models.bot_settings import BotSettings
from app.models.decision import DecisionV1
from app.risk.engine_v1 import RiskEngineV1, FLAG_TRADING_DISABLED, FLAG_RISK_ERROR


def _settings_enabled():
    return BotSettings(
        owner_user_id=uuid4(),
        trading_enabled=True,
        symbols=["EURUSD"],
    )


def _settings_disabled():
    return BotSettings(
        owner_user_id=uuid4(),
        trading_enabled=False,
        symbols=["EURUSD"],
    )


def _decision(trade_allowed=True, flags=None):
    dec = DecisionV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        signal_preview_id=uuid4(),
        trade_allowed=trade_allowed,
        risk_modifier=1.0,
        flags=flags or [],
    )
    dec.id = uuid4()
    return dec


def test_trading_disabled_blocks():
    engine = RiskEngineV1()
    verdict = engine.evaluate(_decision(), _settings_disabled())
    assert verdict.trade_allowed is False
    assert FLAG_TRADING_DISABLED in verdict.flags


def test_decision_not_allowed_preserves_flags():
    engine = RiskEngineV1()
    dec = _decision(trade_allowed=False, flags=["X"])
    verdict = engine.evaluate(dec, _settings_enabled())
    assert verdict.trade_allowed is False
    assert "X" in verdict.flags


def test_fail_safe_sets_risk_error():
    engine = RiskEngineV1()

    class BadSettings(BotSettings):
        @property
        def trading_enabled(self):
            raise RuntimeError("boom")

    verdict = engine.evaluate(_decision(), BadSettings(owner_user_id=uuid4(), symbols=[]))  # type: ignore[arg-type]
    assert verdict.trade_allowed is False
    assert FLAG_RISK_ERROR in verdict.flags


def test_requires_decision_id():
    engine = RiskEngineV1()
    dec = _decision()
    dec.id = None
    with pytest.raises(ValueError):
        engine.evaluate(dec, _settings_enabled())
