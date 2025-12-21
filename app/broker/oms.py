"""
IBKR Order Management System (OMS)

Handles order lifecycle:
- Place orders (Market, Limit, Stop)
- Bracket orders (Main + Stop Loss + Take Profit)
- Track order status
- Handle fills
- Cancel/modify orders
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
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
    
    # Stop Loss / Take Profit
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    
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


class IBKRBracketOrderRequest(BaseModel):
    """Request to place a bracket order (Main + SL + TP)."""
    model_config = ConfigDict(extra="forbid")
    
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    side: OrderSide
    quantity: float
    
    # Main order
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    
    # Stop Loss (required for bracket)
    stop_loss_price: float
    
    # Take Profit (required for bracket)
    take_profit_price: float
    
    tif: str = "GTC"
    
    # Linking
    signal_preview_id: Optional[UUID] = None
    decision_id: Optional[UUID] = None


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
    
    # For bracket orders
    is_bracket: bool = False
    stop_loss_order_id: Optional[int] = None
    take_profit_order_id: Optional[int] = None


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
    Supports:
    - Single orders (Market, Limit, Stop)
    - Bracket orders (Main + Stop Loss + Take Profit)
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
        
        # Track bracket order relationships
        self._bracket_children: Dict[int, UUID] = {}  # child_order_id -> parent_request_id
        
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
        
        # Check if this is a main order or bracket child
        request_id = self._ib_to_request.get(ib_order_id)
        if request_id is None:
            # Check if it's a bracket child order
            request_id = self._bracket_children.get(ib_order_id)
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
            request_id = self._bracket_children.get(ib_order_id)
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
        if request_id is None:
            request_id = self._bracket_children.get(reqId)
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
    
    def place_bracket_order(self, request: IBKRBracketOrderRequest) -> IBKROrderState:
        """
        Place a bracket order with IBKR.
        
        Creates three linked orders:
        1. Main order (parent) - Market or Limit
        2. Stop Loss order - triggered if price moves against position
        3. Take Profit order - triggered if price moves in favor
        
        SL and TP are OCA (One-Cancels-All) - when one fills, the other is cancelled.
        """
        state = IBKROrderState(
            request_id=request.id,
            is_bracket=True,
        )
        self._orders[request.id] = state
        
        try:
            from ib_insync import Order
            
            # Create contract
            contract = self.create_forex_contract(request.symbol)
            
            # Determine opposite action for SL/TP orders
            opposite_action = "SELL" if request.side == OrderSide.BUY else "BUY"
            
            # Create bracket orders using ib_insync helper
            bracket = self.ib.bracketOrder(
                action=request.side.value,
                quantity=request.quantity,
                limitPrice=request.limit_price if request.order_type == OrderType.LIMIT else request.take_profit_price,
                takeProfitPrice=request.take_profit_price,
                stopLossPrice=request.stop_loss_price,
            )
            
            # Unpack bracket orders
            parent_order, take_profit_order, stop_loss_order = bracket
            
            # Set order type for parent
            if request.order_type == OrderType.MARKET:
                parent_order.orderType = "MKT"
                parent_order.lmtPrice = 0
            
            # Set TIF
            parent_order.tif = request.tif
            take_profit_order.tif = request.tif
            stop_loss_order.tif = request.tif
            
            # Place all orders
            parent_trade = self.ib.placeOrder(contract, parent_order)
            tp_trade = self.ib.placeOrder(contract, take_profit_order)
            sl_trade = self.ib.placeOrder(contract, stop_loss_order)
            
            # Store IDs
            state.ib_order_id = parent_trade.order.orderId
            state.ib_perm_id = getattr(parent_trade.order, "permId", None)
            state.take_profit_order_id = tp_trade.order.orderId
            state.stop_loss_order_id = sl_trade.order.orderId
            state.status = OrderStatus.SUBMITTED
            state.updated_at = datetime.now(timezone.utc)
            
            # Register mappings
            self._ib_to_request[state.ib_order_id] = request.id
            self._bracket_children[state.take_profit_order_id] = request.id
            self._bracket_children[state.stop_loss_order_id] = request.id
            
            self.callback.on_order_status(state)
            
        except Exception as exc:
            state.status = OrderStatus.ERROR
            state.error_message = str(exc)
            state.updated_at = datetime.now(timezone.utc)
            self.callback.on_error(request.id, state.error_message)
        
        return state
    
    def place_order_with_sl_tp(
        self,
        request: IBKROrderRequest,
        current_price: Optional[float] = None,
    ) -> IBKROrderState:
        """
        Place order with optional SL/TP.
        
        If both stop_loss_price and take_profit_price are provided,
        creates a bracket order. Otherwise creates a simple order.
        
        Args:
            request: Order request with optional SL/TP prices
            current_price: Current market price (used to calculate SL/TP if needed)
        """
        # If both SL and TP are set, use bracket order
        if request.stop_loss_price is not None and request.take_profit_price is not None:
            bracket_request = IBKRBracketOrderRequest(
                id=request.id,
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                order_type=request.order_type,
                limit_price=request.limit_price,
                stop_loss_price=request.stop_loss_price,
                take_profit_price=request.take_profit_price,
                tif=request.tif,
                signal_preview_id=request.signal_preview_id,
                decision_id=request.decision_id,
            )
            return self.place_bracket_order(bracket_request)
        
        # Otherwise place simple order
        return self.place_order(request)
    
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
                    
                    # Also cancel SL/TP if bracket
                    if state.is_bracket:
                        self._cancel_bracket_children(state)
                    
                    return True
        except Exception:
            pass
        
        return False
    
    def _cancel_bracket_children(self, state: IBKROrderState) -> None:
        """Cancel SL/TP orders for a bracket."""
        for order_id in [state.stop_loss_order_id, state.take_profit_order_id]:
            if order_id:
                try:
                    for trade in self.ib.trades():
                        if trade.order.orderId == order_id:
                            self.ib.cancelOrder(trade.order)
                            break
                except Exception:
                    pass
    
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
