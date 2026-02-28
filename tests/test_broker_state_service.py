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


def _fx_contract(base: str, quote: str, con_id: int = 1):
    return SimpleNamespace(
        secType="CFD",
        symbol=base,
        currency=quote,
        localSymbol=f"{base}.{quote}",
        conId=con_id,
    )


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
        contract=_fx_contract("EUR", "USD", con_id=101),
        position=25000,
        avgCost=1.1,
        unrealizedPNL=12.5,
    )
    ib._positions = [position]
    trades_repo = MagicMock()
    trades_repo.get_active_trades_full.return_value = []
    trades_repo.get_latest_orphan_by_instrument_key.return_value = None
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
        "quantity": 10000,
        "entry_price": 1.1000,
        "stop_loss": 1.0950,
        "take_profit": 1.1100,
        "opened_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        "meta": {"instrument_key": "CFD:101"},
    }
    trades_repo = MagicMock()
    trades_repo.get_active_trades_full.return_value = [trade]
    trades_repo.get_latest_orphan_by_instrument_key.return_value = None
    trades_repo.close_trade.return_value = None
    ib._open_trades = [
        SimpleNamespace(
            contract=_fx_contract("EUR", "USD", con_id=101),
            order=SimpleNamespace(orderId=1),
        )
    ]
    exec_obj = SimpleNamespace(
        time=datetime.now(timezone.utc),
        side="SELL",
        price=1.0950,
        orderId=999,
    )
    fill = SimpleNamespace(contract=_fx_contract("EUR", "USD", con_id=101), execution=exec_obj)
    ib._executions = [fill]
    service = BrokerStateService(ib=ib, trades_history_repo=trades_repo)
    result = service.sync_with_db()
    assert "EURUSD" in result.positions_closed
    trades_repo.close_trade.assert_called_once()
    close_kwargs = trades_repo.close_trade.call_args.kwargs
    assert close_kwargs["close_reason"] == "SL_HIT"
    assert close_kwargs["pnl"] == pytest.approx(-50.0)
    assert close_kwargs["pnl_pips"] == pytest.approx(-50.0)


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
        contract=_fx_contract("EUR", "USD", con_id=101),
        orderStatus=SimpleNamespace(status="Submitted"),
    )
    ib._open_trades = [trade]
    trades_repo = MagicMock()
    trades_repo.get_active_trades_full.return_value = []
    trades_repo.get_latest_orphan_by_instrument_key.return_value = None
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
    contract = _fx_contract("EUR", "USD", con_id=101)
    execution = SimpleNamespace(execId="x1", time=datetime.now(timezone.utc), shares=1000, price=1.1, orderId=77)
    fill = SimpleNamespace(execution=execution, commissionReport=None)
    trade = SimpleNamespace(order=order, contract=contract)

    oms._on_exec_details(trade, fill)
    assert len(callback.fill_events) == 1
    assert callback.fill_events[0].ib_order_id == 77


def test_orphan_dedup_skips_recent_orphan():
    ib = MockIB()
    position = SimpleNamespace(
        contract=_fx_contract("EUR", "USD", con_id=101),
        position=10000,
        avgCost=1.05,
        unrealizedPNL=0.0,
    )
    ib._positions = [position]
    trades_repo = MagicMock()
    trades_repo.get_active_trades_full.return_value = []
    trades_repo.get_latest_orphan_by_instrument_key.return_value = {
        "id": "orphan1",
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }
    service = BrokerStateService(ib=ib, trades_history_repo=trades_repo)
    result = service.sync_with_db()
    assert "orphan_recent_exists:CFD:101" in result.mismatches
    trades_repo.create_trade.assert_not_called()
