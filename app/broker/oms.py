"""
IBKR Order Management System (OMS)

Handles order lifecycle:
- Place orders
- Track order status
- Handle fills
- Cancel/modify orders
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.models.order_intent import OrderIntentV1


class OrderStatus(str, Enum):
    """IBKR order statuses mapped to internal statuses."""
    PENDING = "PENDING"           # Created, not yet submitted
    SUBMITTED = "SUBMITTED"       # Sent to broker
    ACCEPTED = "ACCEPTED"         # Accepted by broker
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


class OrderType(str, Enum):
    MARKET = "MKT"
    LIMIT = "LMT"
    STOP = "STP"
    STOP_LIMIT = "STP_LMT"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class IBKROrderRequest(BaseModel):
    """Request to place an order with IBKR."""
    model_config = ConfigDict(extra="forbid")
    
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    tif: str = "GTC"  # Time in force: GTC, DAY, IOC, etc.
    
    # Linking to control plane
    signal_preview_id: Optional[UUID] = None
    decision_id: Optional[UUID] = None
    order_intent_id: Optional[UUID] = None
    
    def to_ib_order(self, ib_module: Any) -> Any:
        """Convert to ib_insync Order object."""
        order_kwargs = {
            "action": self.side.value,
            "totalQuantity": self.quantity,
            "orderType": self.order_type.value,
            "tif": self.tif,
        }
        if self.order_type == OrderType.LIMIT and self.limit_price:
            order_kwargs["lmtPrice"] = self.limit_price
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and self.stop_price:
            order_kwargs["auxPrice"] = self.stop_price
        if self.order_type == OrderType.STOP_LIMIT and self.limit_price:
            order_kwargs["lmtPrice"] = self.limit_price
            
        return ib_module.Order(**order_kwargs)


class IBKROrderState(BaseModel):
    """Current state of an IBKR order."""
    model_config = ConfigDict(extra="forbid")
    
    request_id: UUID
    ib_order_id: Optional[int] = None
    ib_perm_id: Optional[int] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    avg_fill_price: Optional[float] = None
    last_fill_time: Optional[datetime] = None
    commission: float = 0.0
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IBKRFill(BaseModel):
    """Individual fill event."""
    model_config = ConfigDict(extra="forbid")
    
    request_id: UUID
    ib_order_id: int
    ib_exec_id: str
    fill_time: datetime
    quantity: float
    price: float
    commission: float = 0.0
    
    
class IBKROrderCallback:
    """Callbacks for order events."""
    
    def on_order_status(self, state: IBKROrderState) -> None:
        """Called when order status changes."""
        pass
    
    def on_fill(self, fill: IBKRFill) -> None:
        """Called when order is (partially) filled."""
        pass
    
    def on_error(self, request_id: UUID, error: str) -> None:
        """Called on order error."""
        pass


class IBKROMS:
    """
    IBKR Order Management System.
    
    Wraps ib_insync for order placement and tracking.
    """
    
    def __init__(
        self,
        ib: Any,
        callback: Optional[IBKROrderCallback] = None,
        default_account: Optional[str] = None,
    ) -> None:
        self.ib = ib
        self.callback = callback or IBKROrderCallback()
        self.default_account = default_account
        
        # Track active orders
        self._orders: Dict[UUID, IBKROrderState] = {}
        self._ib_to_request: Dict[int, UUID] = {}  # ib_order_id -> request_id
        
        # Register event handlers
        self._register_handlers()
    
    def _register_handlers(self) -> None:
        """Register ib_insync event handlers."""
        if hasattr(self.ib, "orderStatusEvent"):
            self.ib.orderStatusEvent += self._on_order_status
        if hasattr(self.ib, "execDetailsEvent"):
            self.ib.execDetailsEvent += self._on_exec_details
        if hasattr(self.ib, "errorEvent"):
            self.ib.errorEvent += self._on_error
    
    def _unregister_handlers(self) -> None:
        """Unregister event handlers."""
        if hasattr(self.ib, "orderStatusEvent"):
            self.ib.orderStatusEvent -= self._on_order_status
        if hasattr(self.ib, "execDetailsEvent"):
            self.ib.execDetailsEvent -= self._on_exec_details
        if hasattr(self.ib, "errorEvent"):
            self.ib.errorEvent -= self._on_error
    
    def _map_status(self, ib_status: str) -> OrderStatus:
        """Map IBKR status string to OrderStatus enum."""
        mapping = {
            "PendingSubmit": OrderStatus.PENDING,
            "PendingCancel": OrderStatus.PENDING,
            "PreSubmitted": OrderStatus.SUBMITTED,
            "Submitted": OrderStatus.SUBMITTED,
            "ApiPending": OrderStatus.PENDING,
            "ApiCancelled": OrderStatus.CANCELLED,
            "Cancelled": OrderStatus.CANCELLED,
            "Filled": OrderStatus.FILLED,
            "Inactive": OrderStatus.ERROR,
        }
        return mapping.get(ib_status, OrderStatus.PENDING)
    
    def _on_order_status(self, trade: Any) -> None:
        """Handle order status event from ib_insync."""
        ib_order_id = getattr(trade.order, "orderId", None)
        if ib_order_id is None:
            return
            
        request_id = self._ib_to_request.get(ib_order_id)
        if request_id is None:
            return
            
        state = self._orders.get(request_id)
        if state is None:
            return
        
        order_status = getattr(trade, "orderStatus", None)
        if order_status:
            state.status = self._map_status(order_status.status)
            state.filled_quantity = getattr(order_status, "filled", 0.0)
            state.avg_fill_price = getattr(order_status, "avgFillPrice", None)
            state.updated_at = datetime.now(timezone.utc)
            
            self.callback.on_order_status(state)
    
    def _on_exec_details(self, trade: Any, fill: Any) -> None:
        """Handle execution/fill event."""
        ib_order_id = getattr(trade.order, "orderId", None)
        if ib_order_id is None:
            return
            
        request_id = self._ib_to_request.get(ib_order_id)
        if request_id is None:
            return
        
        exec_id = getattr(fill.execution, "execId", "")
        fill_time = getattr(fill.execution, "time", datetime.now(timezone.utc))
        quantity = getattr(fill.execution, "shares", 0.0)
        price = getattr(fill.execution, "price", 0.0)
        commission = getattr(fill.commissionReport, "commission", 0.0) if fill.commissionReport else 0.0
        
        ibkr_fill = IBKRFill(
            request_id=request_id,
            ib_order_id=ib_order_id,
            ib_exec_id=exec_id,
            fill_time=fill_time,
            quantity=quantity,
            price=price,
            commission=commission,
        )
        
        # Update state
        state = self._orders.get(request_id)
        if state:
            state.commission += commission
            state.last_fill_time = fill_time
            state.updated_at = datetime.now(timezone.utc)
        
        self.callback.on_fill(ibkr_fill)
    
    def _on_error(self, reqId: int, errorCode: int, errorString: str, contract: Any) -> None:
        """Handle error event."""
        # Map reqId to request_id if possible
        request_id = self._ib_to_request.get(reqId)
        if request_id and request_id in self._orders:
            state = self._orders[request_id]
            state.status = OrderStatus.ERROR
            state.error_message = f"{errorCode}: {errorString}"
            state.updated_at = datetime.now(timezone.utc)
            
            self.callback.on_error(request_id, state.error_message)
    
    def create_forex_contract(self, symbol: str) -> Any:
        """Create a Forex contract for the given symbol pair."""
        # Symbol format: EURUSD -> EUR.USD
        if len(symbol) == 6:
            base = symbol[:3]
            quote = symbol[3:]
        else:
            base = symbol
            quote = "USD"
        
        from ib_insync import Forex
        contract = Forex(pair=f"{base}{quote}")
        
        # Qualify the contract
        qualified = self.ib.qualifyContracts(contract)
        if qualified:
            return qualified[0]
        return contract
    
    def place_order(self, request: IBKROrderRequest) -> IBKROrderState:
        """
        Place an order with IBKR.
        
        Returns the initial order state (will be updated via callbacks).
        """
        # Create initial state
        state = IBKROrderState(request_id=request.id)
        self._orders[request.id] = state
        
        try:
            # Create contract
            contract = self.create_forex_contract(request.symbol)
            
            # Create order
            from ib_insync import Order
            order = request.to_ib_order(type("IB", (), {"Order": Order}))
            
            # Place order
            trade = self.ib.placeOrder(contract, order)
            
            # Store mapping
            ib_order_id = trade.order.orderId
            state.ib_order_id = ib_order_id
            state.ib_perm_id = getattr(trade.order, "permId", None)
            state.status = OrderStatus.SUBMITTED
            state.updated_at = datetime.now(timezone.utc)
            
            self._ib_to_request[ib_order_id] = request.id
            
            self.callback.on_order_status(state)
            
        except Exception as exc:
            state.status = OrderStatus.ERROR
            state.error_message = str(exc)
            state.updated_at = datetime.now(timezone.utc)
            self.callback.on_error(request.id, state.error_message)
        
        return state
    
    def cancel_order(self, request_id: UUID) -> bool:
        """Cancel an order by request ID."""
        state = self._orders.get(request_id)
        if state is None:
            return False
        
        if state.ib_order_id is None:
            return False
        
        if state.status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
            return False
        
        try:
            # Find the trade object
            for trade in self.ib.trades():
                if trade.order.orderId == state.ib_order_id:
                    self.ib.cancelOrder(trade.order)
                    return True
        except Exception:
            pass
        
        return False
    
    def get_order_state(self, request_id: UUID) -> Optional[IBKROrderState]:
        """Get current order state."""
        return self._orders.get(request_id)
    
    def get_active_orders(self) -> List[IBKROrderState]:
        """Get all orders that are not in terminal state."""
        terminal = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.ERROR}
        return [s for s in self._orders.values() if s.status not in terminal]
    
    def shutdown(self) -> None:
        """Cleanup handlers."""
        self._unregister_handlers()
