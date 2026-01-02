from types import SimpleNamespace
from unittest.mock import MagicMock

from app.broker.state_service import BrokerStateService


class MockIB:
    def __init__(self):
        self._connected = True
        self._positions = []
        self._open_orders = []
        self._open_trades = []
        self._account_summary = []

    def isConnected(self):
        return self._connected

    def positions(self):
        return self._positions

    def openOrders(self):
        return self._open_orders

    def openTrades(self):
        return self._open_trades

    def accountSummary(self):
        return self._account_summary


def test_sync_blocks_on_missing_conid():
    ib = MockIB()
    ib._positions = [
        SimpleNamespace(
            contract=SimpleNamespace(secType="CFD", conId=None, localSymbol="EUR.USD"),
            position=10000,
            avgCost=1.0,
            unrealizedPNL=0.0,
        )
    ]
    trades_repo = MagicMock()
    risk_repo = MagicMock()
    bot_settings_repo = MagicMock()
    service = BrokerStateService(
        ib=ib,
        trades_history_repo=trades_repo,
        risk_events_repo=risk_repo,
        bot_settings_repo=bot_settings_repo,
        owner_user_id="owner",
    )
    result = service.sync_with_db()
    assert "missing_conId" in result.errors
    assert bot_settings_repo.update.called
    assert any(call.kwargs.get("event_type") == "MISSING_CONID" for call in risk_repo.insert.call_args_list)
    trades_repo.get_active_trades_full.assert_not_called()


def test_sync_blocks_on_unexpected_sectype():
    ib = MockIB()
    ib._positions = [
        SimpleNamespace(
            contract=SimpleNamespace(secType="CASH", conId=123, localSymbol="EUR.USD"),
            position=10000,
            avgCost=1.0,
            unrealizedPNL=0.0,
        )
    ]
    trades_repo = MagicMock()
    risk_repo = MagicMock()
    bot_settings_repo = MagicMock()
    service = BrokerStateService(
        ib=ib,
        trades_history_repo=trades_repo,
        risk_events_repo=risk_repo,
        bot_settings_repo=bot_settings_repo,
        owner_user_id="owner",
    )
    result = service.sync_with_db()
    assert "unexpected_sectype" in result.errors
    assert bot_settings_repo.update.called
    assert any(call.kwargs.get("event_type") == "UNEXPECTED_SECTYPE" for call in risk_repo.insert.call_args_list)
    trades_repo.get_active_trades_full.assert_not_called()


def test_sync_blocks_on_missing_trade_instrument_key():
    ib = MockIB()
    ib._positions = [
        SimpleNamespace(
            contract=SimpleNamespace(secType="CFD", conId=321, localSymbol="EUR.USD"),
            position=10000,
            avgCost=1.0,
            unrealizedPNL=0.0,
        )
    ]
    trades_repo = MagicMock()
    trades_repo.get_active_trades_full.return_value = [
        {"id": "t1", "symbol": "EURUSD", "status": "OPEN", "meta": {}},
    ]
    risk_repo = MagicMock()
    bot_settings_repo = MagicMock()
    service = BrokerStateService(
        ib=ib,
        trades_history_repo=trades_repo,
        risk_events_repo=risk_repo,
        bot_settings_repo=bot_settings_repo,
        owner_user_id="owner",
    )
    result = service.sync_with_db()
    assert "missing_instrument_key" in result.errors
    assert bot_settings_repo.update.called
    assert any(call.kwargs.get("event_type") == "MISSING_INSTRUMENT_KEY" for call in risk_repo.insert.call_args_list)
