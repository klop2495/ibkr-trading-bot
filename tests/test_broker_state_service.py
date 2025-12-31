import pytest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.broker.oms import IBKROMS, IBKROrderCallback
from app.broker.state_service import BrokerStateService


class MockIB:
    def __init__(self):
        self._connected = True
        self._positions = []
        self._open_orders = []
        self._open_trades = []
        self._account_summary = []
        self._executions = []

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

    def reqExecutions(self):
        return self._executions


class MockEvent:
    def __init__(self):
        self._handlers = []

    def __iadd__(self, handler):
        self._handlers.append(handler)
        return self

    def __isub__(self, handler):
        if handler in self._handlers:
            self._handlers.remove(handler)
        return self


class MockIBEvents(MockIB):
    def __init__(self):
        super().__init__()
        self.orderStatusEvent = MockEvent()
        self.execDetailsEvent = MockEvent()
        self.errorEvent = MockEvent()


class RecordingCallback(IBKROrderCallback):
    def __init__(self):
        self.status_events = []
        self.fill_events = []

    def on_order_status(self, state):
        self.status_events.append(state)

    def on_fill(self, fill):
        self.fill_events.append(fill)


def _fx_contract(base: str, quote: str):
    return SimpleNamespace(secType="CASH", symbol=base, currency=quote)


def test_can_open_position_blocks_when_disconnected():
    ib = MockIB()
    ib._connected = False
    service = BrokerStateService(ib=ib)
    ok, reason = service.can_open_position("EURUSD", "BUY", 10000)
    assert ok is False
    assert reason == "broker_disconnected"


def test_sync_creates_orphan_position_record():
    ib = MockIB()
    position = SimpleNamespace(
        contract=_fx_contract("EUR", "USD"),
        position=25000,
        avgCost=1.1,
        unrealizedPNL=12.5,
    )
    ib._positions = [position]
    trades_repo = MagicMock()
    trades_repo.get_active_trades_full.return_value = []
    trades_repo.create_trade.return_value = "trade1"
    service = BrokerStateService(ib=ib, trades_history_repo=trades_repo)
    result = service.sync_with_db()
    assert "EURUSD" in result.positions_opened
    trades_repo.create_trade.assert_called_once()


def test_sync_closes_trade_with_sl_hit():
    ib = MockIB()
    trade = {
        "id": "trade1",
        "symbol": "EURUSD",
        "side": "BUY",
        "stop_loss": 1.0950,
        "take_profit": 1.1100,
        "opened_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
    }
    trades_repo = MagicMock()
    trades_repo.get_active_trades_full.return_value = [trade]
    trades_repo.close_trade.return_value = None
    exec_obj = SimpleNamespace(
        time=datetime.now(timezone.utc),
        side="SELL",
        price=1.0950,
        orderId=999,
    )
    fill = SimpleNamespace(contract=_fx_contract("EUR", "USD"), execution=exec_obj)
    ib._executions = [fill]
    service = BrokerStateService(ib=ib, trades_history_repo=trades_repo)
    result = service.sync_with_db()
    assert "EURUSD" in result.positions_closed
    trades_repo.close_trade.assert_called_once()
    assert trades_repo.close_trade.call_args.kwargs["close_reason"] == "SL_HIT"


def test_orphan_orders_logged_without_cancel():
    ib = MockIB()
    order = SimpleNamespace(
        orderId=123,
        action="SELL",
        totalQuantity=10000,
        orderType="STP",
        lmtPrice=None,
        auxPrice=1.1,
        parentId=10,
    )
    ib._open_orders = [order]
    trade = SimpleNamespace(
        order=order,
        contract=_fx_contract("EUR", "USD"),
        orderStatus=SimpleNamespace(status="Submitted"),
    )
    ib._open_trades = [trade]
    trades_repo = MagicMock()
    trades_repo.get_active_trades_full.return_value = []
    risk_events_repo = MagicMock()
    service = BrokerStateService(
        ib=ib,
        trades_history_repo=trades_repo,
        risk_events_repo=risk_events_repo,
    )
    result = service.sync_with_db()
    assert any("orphan_order" in msg for msg in result.mismatches)
    assert risk_events_repo.insert.called


def test_oms_duplicate_ib_order_id_disables_trading():
    ib = MockIBEvents()
    trades_repo = MagicMock()
    trades_repo.get_active_trades_with_ib_order_id.return_value = [
        {"id": "a", "ib_order_id": 10, "status": "OPEN"},
        {"id": "b", "ib_order_id": 10, "status": "OPEN"},
    ]
    disable_cb = MagicMock()
    IBKROMS(
        ib=ib,
        callback=RecordingCallback(),
        trades_history_repo=trades_repo,
        disable_trading_callback=disable_cb,
    )
    disable_cb.assert_called_once()


def test_oms_fallback_lookup_by_ib_order_id():
    ib = MockIBEvents()
    trades_repo = MagicMock()
    trades_repo.get_active_trades_with_ib_order_id.return_value = []
    trades_repo.get_trade_by_ib_order_id.return_value = {"id": "t1", "ib_order_id": 55, "status": "SUBMITTED"}
    callback = RecordingCallback()
    oms = IBKROMS(
        ib=ib,
        callback=callback,
        trades_history_repo=trades_repo,
    )

    order = SimpleNamespace(orderId=55, parentId=None)
    order_status = SimpleNamespace(status="Submitted", filled=0.0, avgFillPrice=0.0)
    trade = SimpleNamespace(order=order, orderStatus=order_status)

    oms._on_order_status(trade)
    assert len(callback.status_events) == 1
    assert callback.status_events[0].ib_order_id == 55


def test_oms_fill_after_restart_uses_db_fallback():
    ib = MockIBEvents()
    trades_repo = MagicMock()
    trades_repo.get_active_trades_with_ib_order_id.return_value = []
    trades_repo.get_trade_by_ib_order_id.return_value = {"id": "t1", "ib_order_id": 77, "status": "OPEN"}
    callback = RecordingCallback()
    oms = IBKROMS(
        ib=ib,
        callback=callback,
        trades_history_repo=trades_repo,
    )

    order = SimpleNamespace(orderId=77, parentId=None)
    contract = _fx_contract("EUR", "USD")
    execution = SimpleNamespace(execId="x1", time=datetime.now(timezone.utc), shares=1000, price=1.1, orderId=77)
    fill = SimpleNamespace(execution=execution, commissionReport=None)
    trade = SimpleNamespace(order=order, contract=contract)

    oms._on_exec_details(trade, fill)
    assert len(callback.fill_events) == 1
    assert callback.fill_events[0].ib_order_id == 77
