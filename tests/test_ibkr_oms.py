"""
Tests for IBKR OMS (Order Management System)
"""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from app.broker.oms import (
    IBKROMS,
    IBKROrderCallback,
    IBKROrderRequest,
    IBKROrderState,
    IBKRFill,
    OrderSide,
    OrderStatus,
    OrderType,
)


class MockIB:
    """Mock IB instance for testing."""
    
    def __init__(self):
        self.connected = True
        self._order_id_counter = 1000
        self._trades = []
        
        # Event handlers
        self.orderStatusEvent = MockEvent()
        self.execDetailsEvent = MockEvent()
        self.errorEvent = MockEvent()
    
    def isConnected(self):
        return self.connected
    
    def qualifyContracts(self, contract):
        contract.conId = getattr(contract, "conId", None) or 999
        contract.secType = "CFD"
        return [contract]
    
    def forex(self, pair):
        return MockContract(pair)
    
    def placeOrder(self, contract, order):
        trade = MockTrade(contract, order, self._order_id_counter)
        self._order_id_counter += 1
        self._trades.append(trade)
        return trade
    
    def cancelOrder(self, order):
        pass
    
    def trades(self):
        return self._trades


class MockEvent:
    """Mock event for ib_insync."""
    
    def __init__(self):
        self._handlers = []
    
    def __iadd__(self, handler):
        self._handlers.append(handler)
        return self
    
    def __isub__(self, handler):
        if handler in self._handlers:
            self._handlers.remove(handler)
        return self


class MockContract:
    def __init__(self, symbol):
        self.symbol = symbol
        self.secType = "CFD"
        self.currency = "USD"
        self.exchange = "SMART"
        self.conId = 999


class MockOrder:
    def __init__(self, order_id):
        self.orderId = order_id
        self.permId = order_id + 1000000
        self.action = "BUY"
        self.totalQuantity = 10000
        self.orderType = "MKT"


class MockTrade:
    def __init__(self, contract, order, order_id):
        self.contract = contract
        self.order = MockOrder(order_id)
        self.orderStatus = MockOrderStatus()


class MockOrderStatus:
    def __init__(self):
        self.status = "Submitted"
        self.filled = 0.0
        self.avgFillPrice = 0.0


class MockOrderCallback(IBKROrderCallback):
    """Mock callback that records events."""
    
    def __init__(self):
        self.status_events = []
        self.fill_events = []
        self.error_events = []
    
    def on_order_status(self, state: IBKROrderState) -> None:
        self.status_events.append(state)
    
    def on_fill(self, fill: IBKRFill) -> None:
        self.fill_events.append(fill)
    
    def on_error(self, request_id, error: str) -> None:
        self.error_events.append((request_id, error))


class TestIBKROrderRequest:
    """Tests for order request model."""
    
    def test_create_market_order(self):
        """Test creating a market order request."""
        request = IBKROrderRequest(
            symbol="EURUSD",
            side=OrderSide.BUY,
            quantity=10000,
            order_type=OrderType.MARKET,
        )
        
        assert request.symbol == "EURUSD"
        assert request.side == OrderSide.BUY
        assert request.quantity == 10000
        assert request.order_type == OrderType.MARKET
        assert request.id is not None
    
    def test_create_limit_order(self):
        """Test creating a limit order request."""
        request = IBKROrderRequest(
            symbol="EURUSD",
            side=OrderSide.SELL,
            quantity=5000,
            order_type=OrderType.LIMIT,
            limit_price=1.1050,
        )
        
        assert request.order_type == OrderType.LIMIT
        assert request.limit_price == 1.1050
    
    def test_create_stop_order(self):
        """Test creating a stop order request."""
        request = IBKROrderRequest(
            symbol="USDJPY",
            side=OrderSide.BUY,
            quantity=10000,
            order_type=OrderType.STOP,
            stop_price=151.00,
        )
        
        assert request.order_type == OrderType.STOP
        assert request.stop_price == 151.00


class TestIBKROMS:
    """Tests for OMS."""
    
    def test_place_order(self):
        """Test placing an order."""
        mock_ib = MockIB()
        callback = MockOrderCallback()
        oms = IBKROMS(ib=mock_ib, callback=callback)
        
        request = IBKROrderRequest(
            symbol="EURUSD",
            side=OrderSide.BUY,
            quantity=10000,
        )
        
        state = oms.place_order(request)
        
        assert state is not None
        assert state.request_id == request.id
        assert state.status == OrderStatus.SUBMITTED
        assert state.ib_order_id is not None
        assert len(callback.status_events) == 1
    
    def test_get_order_state(self):
        """Test retrieving order state."""
        mock_ib = MockIB()
        oms = IBKROMS(ib=mock_ib)
        
        request = IBKROrderRequest(
            symbol="EURUSD",
            side=OrderSide.BUY,
            quantity=10000,
        )
        
        oms.place_order(request)
        state = oms.get_order_state(request.id)
        
        assert state is not None
        assert state.request_id == request.id
    
    def test_get_nonexistent_order(self):
        """Test retrieving non-existent order returns None."""
        mock_ib = MockIB()
        oms = IBKROMS(ib=mock_ib)
        
        state = oms.get_order_state(uuid4())
        assert state is None
    
    def test_get_active_orders(self):
        """Test getting active orders."""
        mock_ib = MockIB()
        oms = IBKROMS(ib=mock_ib)
        
        # Place multiple orders
        for _ in range(3):
            request = IBKROrderRequest(
                symbol="EURUSD",
                side=OrderSide.BUY,
                quantity=10000,
            )
            oms.place_order(request)
        
        active = oms.get_active_orders()
        assert len(active) == 3
    
    def test_create_forex_contract_forbidden(self):
        """CFD-only: forex contract creation should fail."""
        mock_ib = MockIB()
        oms = IBKROMS(ib=mock_ib)
        with pytest.raises(RuntimeError, match="cfd_only_forex_contract_forbidden"):
            oms.create_forex_contract("EURUSD")
    
    def test_order_with_control_plane_ids(self):
        """Test order with control plane linking."""
        mock_ib = MockIB()
        oms = IBKROMS(ib=mock_ib)
        
        preview_id = uuid4()
        decision_id = uuid4()
        
        request = IBKROrderRequest(
            symbol="GBPUSD",
            side=OrderSide.SELL,
            quantity=5000,
            signal_preview_id=preview_id,
            decision_id=decision_id,
        )
        
        state = oms.place_order(request)
        
        assert request.signal_preview_id == preview_id
        assert request.decision_id == decision_id


class TestOrderStatus:
    """Tests for order status mapping."""
    
    def test_status_enum_values(self):
        """Test status enum has expected values."""
        assert OrderStatus.PENDING.value == "PENDING"
        assert OrderStatus.SUBMITTED.value == "SUBMITTED"
        assert OrderStatus.FILLED.value == "FILLED"
        assert OrderStatus.CANCELLED.value == "CANCELLED"
        assert OrderStatus.ERROR.value == "ERROR"


class TestOrderSide:
    """Tests for order side enum."""
    
    def test_side_enum_values(self):
        """Test side enum has expected values."""
        assert OrderSide.BUY.value == "BUY"
        assert OrderSide.SELL.value == "SELL"


class TestOrderType:
    """Tests for order type enum."""
    
    def test_type_enum_values(self):
        """Test type enum has expected values."""
        assert OrderType.MARKET.value == "MKT"
        assert OrderType.LIMIT.value == "LMT"
        assert OrderType.STOP.value == "STP"
