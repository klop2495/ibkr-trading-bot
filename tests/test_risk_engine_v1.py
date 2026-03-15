from datetime import datetime, timezone
from uuid import uuid4

import pytest
from unittest.mock import MagicMock

from app.models.bot_settings import BotSettings
from app.models.decision import DecisionV1
from app.risk.engine_v1 import (
    RiskEngineV1,
    FLAG_ACTIVE_SYMBOL_POSITION,
    FLAG_DAILY_LOSS_LIMIT,
    FLAG_EXPOSURE_LIMIT,
    FLAG_MAX_DAILY_TRADES_PORTFOLIO,
    FLAG_MAX_DAILY_TRADES_SYMBOL,
    FLAG_MAX_OPEN_POSITIONS,
    FLAG_MARGIN_UTILIZATION,
    FLAG_TRADING_DISABLED,
    FLAG_RISK_ERROR,
)


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


def test_blocks_when_max_open_positions_reached():
    trades_repo = MagicMock()
    trades_repo.count_active_trades.side_effect = [2]
    settings = BotSettings(
        owner_user_id=uuid4(),
        trading_enabled=True,
        symbols=["EURUSD"],
        max_open_positions=2,
    )
    engine = RiskEngineV1(trades_history_repo=trades_repo)
    verdict = engine.evaluate(_decision(), settings)
    assert verdict.trade_allowed is False
    assert FLAG_MAX_OPEN_POSITIONS in verdict.flags
    assert verdict.commentary == "risk_blocked:max_open_positions=2"


def test_blocks_when_symbol_already_has_active_trade():
    trades_repo = MagicMock()
    trades_repo.count_active_trades.side_effect = [0, 1]
    engine = RiskEngineV1(trades_history_repo=trades_repo)
    verdict = engine.evaluate(_decision(), _settings_enabled())
    assert verdict.trade_allowed is False
    assert FLAG_ACTIVE_SYMBOL_POSITION in verdict.flags
    assert verdict.commentary == "risk_blocked:active_symbol_trade=EURUSD"


def test_blocks_when_daily_portfolio_limit_reached():
    trades_repo = MagicMock()
    trades_repo.count_active_trades.side_effect = [0, 0]
    trades_repo.count_trades_opened_since.side_effect = [2]
    settings = BotSettings(
        owner_user_id=uuid4(),
        trading_enabled=True,
        symbols=["EURUSD"],
        max_open_positions=5,
        max_trades_per_day_portfolio=2,
    )
    engine = RiskEngineV1(trades_history_repo=trades_repo)
    verdict = engine.evaluate(_decision(), settings)
    assert verdict.trade_allowed is False
    assert FLAG_MAX_DAILY_TRADES_PORTFOLIO in verdict.flags
    assert verdict.commentary == "risk_blocked:max_trades_per_day_portfolio=2"


def test_blocks_when_daily_symbol_limit_reached():
    trades_repo = MagicMock()
    trades_repo.count_active_trades.side_effect = [0, 0]
    trades_repo.count_trades_opened_since.side_effect = [0, 1]
    settings = BotSettings(
        owner_user_id=uuid4(),
        trading_enabled=True,
        symbols=["EURUSD"],
        max_open_positions=5,
        max_trades_per_day_portfolio=2,
        max_trades_per_day_per_symbol=1,
    )
    engine = RiskEngineV1(trades_history_repo=trades_repo)
    verdict = engine.evaluate(_decision(), settings)
    assert verdict.trade_allowed is False
    assert FLAG_MAX_DAILY_TRADES_SYMBOL in verdict.flags
    assert verdict.commentary == "risk_blocked:max_trades_per_day_per_symbol=EURUSD:1"


def test_blocks_when_daily_loss_limit_reached():
    trades_repo = MagicMock()
    trades_repo.count_active_trades.side_effect = [0, 0]
    trades_repo.count_trades_opened_since.side_effect = [0, 0]
    trades_repo.sum_closed_pnl_since.return_value = -160.0
    broker_state_service = MagicMock()
    broker_state_service.get_state.return_value = MagicMock(net_liquidation=10000.0, available_funds=9000.0)
    settings = BotSettings(
        owner_user_id=uuid4(),
        trading_enabled=True,
        symbols=["EURUSD"],
        max_open_positions=5,
        max_trades_per_day_portfolio=5,
        max_trades_per_day_per_symbol=5,
        daily_loss_limit=0.015,
    )
    engine = RiskEngineV1(trades_history_repo=trades_repo, broker_state_service=broker_state_service)
    verdict = engine.evaluate(_decision(), settings)
    assert verdict.trade_allowed is False
    assert FLAG_DAILY_LOSS_LIMIT in verdict.flags
    assert verdict.commentary == "risk_blocked:daily_loss_limit=0.015"


def test_blocks_when_exposure_limit_reached():
    trades_repo = MagicMock()
    trades_repo.count_active_trades.side_effect = [0, 0]
    trades_repo.count_trades_opened_since.side_effect = [0, 0]
    trades_repo.sum_closed_pnl_since.return_value = 0.0
    trades_repo.get_active_trades_full.return_value = [
        {"quantity": 3000, "entry_price": 2.0},
        {"quantity": 3000, "entry_price": 2.0},
    ]
    broker_state_service = MagicMock()
    broker_state_service.get_state.return_value = MagicMock(net_liquidation=5000.0, available_funds=4000.0)
    settings = BotSettings(
        owner_user_id=uuid4(),
        trading_enabled=True,
        symbols=["EURUSD"],
        max_open_positions=5,
        max_trades_per_day_portfolio=5,
        max_trades_per_day_per_symbol=5,
        max_effective_leverage=2.0,
    )
    engine = RiskEngineV1(trades_history_repo=trades_repo, broker_state_service=broker_state_service)
    verdict = engine.evaluate(_decision(), settings)
    assert verdict.trade_allowed is False
    assert FLAG_EXPOSURE_LIMIT in verdict.flags
    assert verdict.commentary == "risk_blocked:max_effective_leverage=2.0"


def test_blocks_when_margin_utilization_limit_reached():
    trades_repo = MagicMock()
    trades_repo.count_active_trades.side_effect = [0, 0]
    trades_repo.count_trades_opened_since.side_effect = [0, 0]
    trades_repo.sum_closed_pnl_since.return_value = 0.0
    trades_repo.get_active_trades_full.return_value = []
    broker_state_service = MagicMock()
    broker_state_service.get_state.return_value = MagicMock(net_liquidation=10000.0, available_funds=6000.0)
    settings = BotSettings(
        owner_user_id=uuid4(),
        trading_enabled=True,
        symbols=["EURUSD"],
        max_open_positions=5,
        max_trades_per_day_portfolio=5,
        max_trades_per_day_per_symbol=5,
        max_margin_utilization=0.35,
    )
    engine = RiskEngineV1(trades_history_repo=trades_repo, broker_state_service=broker_state_service)
    verdict = engine.evaluate(_decision(), settings)
    assert verdict.trade_allowed is False
    assert FLAG_MARGIN_UTILIZATION in verdict.flags
    assert verdict.commentary == "risk_blocked:max_margin_utilization=0.35"
