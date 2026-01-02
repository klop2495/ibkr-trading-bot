"""
IBKR Order Management System (OMS)

Handles order lifecycle:
- Place orders (Market, Limit, Stop)
- Bracket orders (Main + Stop Loss + Take Profit)
- Track order status
- Handle fills
- Cancel/modify orders
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

import threading

from pydantic import BaseModel, ConfigDict, Field

from app.models.order_intent import OrderIntentV1
from app.broker.contracts import create_cfd_fx_contract
from app.broker.keys import instrument_key
from app.storage.repositories import RiskEventsRepo, TradesHistoryRepo

# FX Funds Guard for pre-checking available currency
try:
    from app.broker.fx_funds_guard import FXFundsGuard, FundsCheckResult
    FX_FUNDS_GUARD_AVAILABLE = True
except ImportError:
    FX_FUNDS_GUARD_AVAILABLE = False


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
    TRAIL = "TRAIL"           # Trailing Stop
    TRAIL_LIMIT = "TRAIL_LIMIT"  # Trailing Stop Limit


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
    
    # Trailing Stop settings
    trailing_stop_enabled: bool = False
    trailing_stop_distance: Optional[float] = None  # In price units (not pips)
    trailing_stop_distance_pips: Optional[float] = None  # In pips (will be converted)
    
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
    instrument_key: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # For bracket orders
    is_bracket: bool = False
    stop_loss_order_id: Optional[int] = None
    take_profit_order_id: Optional[int] = None
    
    # For trailing stop
    is_trailing_stop: bool = False
    trailing_stop_order_id: Optional[int] = None
    trailing_stop_distance: Optional[float] = None

    # Last update info (helps distinguish bracket child events)
    last_update_order_id: Optional[int] = None
    last_update_order_role: Optional[str] = None  # PARENT, SL, TP
    last_update_order_status: Optional[OrderStatus] = None


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
    order_role: Optional[str] = None  # PARENT, SL, TP
    parent_ib_order_id: Optional[int] = None
    
    
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
        enable_funds_guard: bool = True,
        trades_history_repo: Optional[TradesHistoryRepo] = None,
        risk_events_repo: Optional[RiskEventsRepo] = None,
        disable_trading_callback: Optional[Callable[[str], None]] = None,
        sync_lock: Optional[threading.RLock] = None,
        broker_state_service: Optional[Any] = None,
    ) -> None:
        self.ib = ib
        self.callback = callback or IBKROrderCallback()
        self.default_account = default_account
        self._trades_history_repo = trades_history_repo
        self._risk_events_repo = risk_events_repo
        self._disable_trading_callback = disable_trading_callback
        self._sync_lock = sync_lock
        self._broker_state_service = broker_state_service
        
        # Track active orders
        self._orders: Dict[UUID, IBKROrderState] = {}
        self._ib_to_request: Dict[int, UUID] = {}  # ib_order_id -> request_id
        
        # Track bracket order relationships
        self._bracket_children: Dict[int, UUID] = {}  # child_order_id -> parent_request_id
        self._bracket_child_types: Dict[int, str] = {}  # child_order_id -> SL/TP
        
        # FX Funds Guard - pre-check available currency before placing orders
        self._funds_guard: Optional[FXFundsGuard] = None
        if enable_funds_guard and FX_FUNDS_GUARD_AVAILABLE:
            try:
                self._funds_guard = FXFundsGuard(ib=ib, account=default_account)
            except Exception as e:
                print(f"Warning: Failed to initialize FXFundsGuard: {e}")
        
        # Register event handlers
        self._register_handlers()

        # Restore order mappings from DB on startup
        self.restore_order_mapping_from_db()
    
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

    def set_sync_lock(self, sync_lock: Optional[threading.RLock]) -> None:
        """Attach a sync lock to block event processing during broker sync."""
        self._sync_lock = sync_lock

    @contextmanager
    def _sync_guard(self):
        if self._sync_lock:
            self._sync_lock.acquire()
            try:
                yield
            finally:
                self._sync_lock.release()
        else:
            yield

    def _invalidate_broker_cache(self) -> None:
        if not self._broker_state_service:
            return
        try:
            self._broker_state_service.invalidate_cache()
        except Exception:
            pass

    def _log_critical(self, message: str, data: Optional[dict] = None) -> None:
        if not self._risk_events_repo:
            return
        try:
            self._risk_events_repo.insert(
                event_type="BROKER_OMS_CRITICAL",
                severity="CRITICAL",
                message=message,
                data=data or {},
            )
        except Exception:
            pass

    def _disable_trading(self, reason: str) -> None:
        if not self._disable_trading_callback:
            return
        try:
            self._disable_trading_callback(reason)
        except Exception:
            pass

    def restore_order_mapping_from_db(self) -> None:
        """Restore ib_order_id -> request_id mapping from trades_history."""
        if not self._trades_history_repo:
            return

        trades = []
        try:
            trades = self._trades_history_repo.get_active_trades_with_ib_order_id()
        except Exception:
            return

        seen: Dict[int, dict] = {}
        duplicates: Dict[int, List[str]] = {}
        for trade in trades:
            ib_order_id = trade.get("ib_order_id")
            if ib_order_id is None:
                continue
            if ib_order_id in seen:
                duplicates.setdefault(int(ib_order_id), []).append(str(trade.get("id")))
                continue
            seen[int(ib_order_id)] = trade

        if duplicates:
            self._log_critical(
                "Duplicate ib_order_id detected in trades_history",
                {"duplicates": duplicates},
            )
            self._disable_trading("duplicate_ib_order_id")
            return

        for ib_order_id, trade in seen.items():
            if ib_order_id in self._ib_to_request:
                continue
            request_id = uuid4()
            self._ib_to_request[int(ib_order_id)] = request_id
            state = IBKROrderState(request_id=request_id, ib_order_id=int(ib_order_id))
            trade_status = str(trade.get("status") or "").upper()
            if trade_status in ("OPEN",):
                state.status = OrderStatus.FILLED
            elif trade_status in ("SUBMITTED",):
                state.status = OrderStatus.SUBMITTED
            elif trade_status in ("PENDING",):
                state.status = OrderStatus.PENDING
            else:
                state.status = OrderStatus.PENDING
            self._orders[request_id] = state

    def _resolve_request_id(self, ib_order_id: int, trade: Optional[Any] = None) -> Optional[UUID]:
        request_id = self._ib_to_request.get(ib_order_id)
        if request_id:
            return request_id
        if ib_order_id in self._bracket_children:
            return self._bracket_children.get(ib_order_id)
        if trade:
            parent_id = getattr(getattr(trade, "order", None), "parentId", None)
            if parent_id and parent_id in self._ib_to_request:
                return self._ib_to_request.get(parent_id)
        if self._trades_history_repo:
            try:
                trade_row = self._trades_history_repo.get_trade_by_ib_order_id(ib_order_id)
            except Exception:
                trade_row = None
            if trade_row:
                request_id = uuid4()
                self._ib_to_request[ib_order_id] = request_id
                self._orders[request_id] = IBKROrderState(request_id=request_id, ib_order_id=ib_order_id)
                return request_id
        return None
    
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
        with self._sync_guard():
            self._invalidate_broker_cache()
            ib_order_id = getattr(trade.order, "orderId", None)
            if ib_order_id is None:
                return

            request_id = self._resolve_request_id(int(ib_order_id), trade=trade)
            if request_id is None:
                return

            state = self._orders.get(request_id)
            if state is None:
                state = IBKROrderState(request_id=request_id, ib_order_id=int(ib_order_id))
                self._orders[request_id] = state

            role = None
            if ib_order_id == state.ib_order_id:
                role = "PARENT"
            elif ib_order_id in self._bracket_children:
                role = self._bracket_child_types.get(ib_order_id, "CHILD")
            elif getattr(trade.order, "parentId", None):
                role = "CHILD"

            order_status = getattr(trade, "orderStatus", None)
            if order_status:
                mapped_status = self._map_status(order_status.status)
                state.last_update_order_id = ib_order_id
                state.last_update_order_role = role
                state.last_update_order_status = mapped_status
                state.updated_at = datetime.now(timezone.utc)

                if role == "PARENT" or role is None:
                    state.status = mapped_status
                    state.filled_quantity = getattr(order_status, "filled", 0.0)
                    state.avg_fill_price = getattr(order_status, "avgFillPrice", None)

                self.callback.on_order_status(state)
    
    def _on_exec_details(self, trade: Any, fill: Any) -> None:
        """Handle execution/fill event."""
        with self._sync_guard():
            self._invalidate_broker_cache()
            ib_order_id = getattr(trade.order, "orderId", None)
            if ib_order_id is None:
                return

            role = None
            parent_ib_order_id = None

            request_id = self._resolve_request_id(int(ib_order_id), trade=trade)
            if request_id is None:
                return

            if ib_order_id in self._bracket_children:
                role = self._bracket_child_types.get(ib_order_id, "CHILD")
            elif getattr(trade.order, "parentId", None):
                role = "CHILD"
            else:
                role = "PARENT"

            exec_id = getattr(fill.execution, "execId", "")
            fill_time = getattr(fill.execution, "time", datetime.now(timezone.utc))
            quantity = getattr(fill.execution, "shares", 0.0)
            price = getattr(fill.execution, "price", 0.0)
            commission = getattr(fill.commissionReport, "commission", 0.0) if fill.commissionReport else 0.0

            # Map child fill to parent order id (used for trade close)
            state = self._orders.get(request_id)
            if state and state.ib_order_id:
                parent_ib_order_id = state.ib_order_id

            ibkr_fill = IBKRFill(
                request_id=request_id,
                ib_order_id=int(ib_order_id),
                ib_exec_id=exec_id,
                fill_time=fill_time,
                quantity=quantity,
                price=price,
                commission=commission,
                order_role=role,
                parent_ib_order_id=parent_ib_order_id,
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
        with self._sync_guard():
            self._invalidate_broker_cache()
            # Map reqId to request_id if possible
            request_id = self._ib_to_request.get(reqId)
            if request_id is None:
                request_id = self._bracket_children.get(reqId)
            if request_id is None:
                request_id = self._resolve_request_id(reqId)
            if request_id and request_id in self._orders:
                state = self._orders[request_id]
                state.status = OrderStatus.ERROR
                state.error_message = f"{errorCode}: {errorString}"
                state.updated_at = datetime.now(timezone.utc)

                self.callback.on_error(request_id, state.error_message)
    
    def create_forex_contract(self, symbol: str) -> Any:
        """CFD-only mode: CASH Forex contracts are запрещены."""
        self._log_critical(
            "CASH forex contract requested in CFD-only mode",
            {"symbol": symbol},
        )
        self._disable_trading("cfd_only_forex_contract_requested")
        raise RuntimeError("cfd_only_forex_contract_forbidden")

    def create_cfd_fx_contract(self, symbol: str) -> Any:
        """Create a CFD FX contract for the given symbol pair."""
        return create_cfd_fx_contract(self.ib, symbol)
    
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
            contract = self.create_cfd_fx_contract(request.symbol)
            state.instrument_key = instrument_key(contract)
            
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
        
        IMPORTANT: Orders must be placed in correct sequence with transmit flags:
        - Parent: transmit=False (hold until children ready)
        - TP child: transmit=False (hold)
        - SL child: transmit=True (sends entire bracket group)
        """
        state = IBKROrderState(
            request_id=request.id,
            is_bracket=True,
        )
        self._orders[request.id] = state
        
        try:
            from ib_insync import Order
            
            # Create contract
            contract = self.create_cfd_fx_contract(request.symbol)
            state.instrument_key = instrument_key(contract)
            
            # Determine opposite action for SL/TP orders
            opposite_action = "SELL" if request.side == OrderSide.BUY else "BUY"
            
            # Get next order IDs from IBKR
            parent_id = self.ib.client.getReqId()
            tp_id = self.ib.client.getReqId()
            sl_id = self.ib.client.getReqId()
            
            # Create PARENT order (Market or Limit)
            parent_order = Order(
                orderId=parent_id,
                action=request.side.value,
                totalQuantity=request.quantity,
                orderType="MKT" if request.order_type == OrderType.MARKET else "LMT",
                tif=request.tif,
                transmit=False,  # Don't transmit yet - wait for children
            )
            if request.order_type == OrderType.LIMIT and request.limit_price:
                parent_order.lmtPrice = request.limit_price
            
            # Create TAKE PROFIT order (Limit)
            tp_order = Order(
                orderId=tp_id,
                action=opposite_action,
                totalQuantity=request.quantity,
                orderType="LMT",
                lmtPrice=request.take_profit_price,
                tif=request.tif,
                parentId=parent_id,  # Link to parent
                transmit=False,  # Don't transmit yet
            )
            
            # Create STOP LOSS order (Stop)
            sl_order = Order(
                orderId=sl_id,
                action=opposite_action,
                totalQuantity=request.quantity,
                orderType="STP",
                auxPrice=request.stop_loss_price,
                tif=request.tif,
                parentId=parent_id,  # Link to parent
                transmit=True,  # Transmit entire bracket group NOW
            )
            
            # Place orders in sequence - IBKR requires this order
            # Parent first, then children. Last order with transmit=True sends all.
            parent_trade = self.ib.placeOrder(contract, parent_order)
            tp_trade = self.ib.placeOrder(contract, tp_order)
            sl_trade = self.ib.placeOrder(contract, sl_order)
            
            # Store IDs
            state.ib_order_id = parent_id
            state.ib_perm_id = getattr(parent_trade.order, "permId", None)
            state.take_profit_order_id = tp_id
            state.stop_loss_order_id = sl_id
            state.status = OrderStatus.SUBMITTED
            state.updated_at = datetime.now(timezone.utc)
            
            # Register mappings
            self._ib_to_request[parent_id] = request.id
            self._bracket_children[tp_id] = request.id
            self._bracket_children[sl_id] = request.id
            self._bracket_child_types[tp_id] = "TP"
            self._bracket_child_types[sl_id] = "SL"
            
            print(f"BRACKET_ORDER submitted parent={parent_id} tp={tp_id} sl={sl_id} symbol={request.symbol} qty={request.quantity}")
            
            self.callback.on_order_status(state)
            
        except Exception as exc:
            state.status = OrderStatus.ERROR
            state.error_message = str(exc)
            state.updated_at = datetime.now(timezone.utc)
            print(f"BRACKET_ORDER error: {exc}")
            self.callback.on_error(request.id, state.error_message)
        
        return state
    
    def place_trailing_stop_order(
        self,
        request: IBKROrderRequest,
        entry_price: float,
    ) -> IBKROrderState:
        """
        Place a trailing stop order after main order is filled.
        
        The trailing stop follows the price by a fixed distance.
        When price moves in favor, the stop moves up (for long) or down (for short).
        When price reverses, the stop stays in place and triggers when hit.
        
        Args:
            request: Order request with trailing_stop_distance or trailing_stop_distance_pips
            entry_price: Entry price to calculate initial stop level
        """
        state = IBKROrderState(
            request_id=request.id,
            is_trailing_stop=True,
        )
        self._orders[request.id] = state
        
        try:
            from ib_insync import Order
            
            # Create contract
            contract = self.create_cfd_fx_contract(request.symbol)
            state.instrument_key = instrument_key(contract)
            
            # Calculate trailing distance in price units
            trailing_distance = request.trailing_stop_distance
            if trailing_distance is None and request.trailing_stop_distance_pips is not None:
                # Convert pips to price units
                pip_value = 0.01 if "JPY" in request.symbol.upper() else 0.0001
                trailing_distance = request.trailing_stop_distance_pips * pip_value
            
            if trailing_distance is None or trailing_distance <= 0:
                raise ValueError("Invalid trailing stop distance")
            
            state.trailing_stop_distance = trailing_distance
            
            # Opposite action for the stop order
            opposite_action = "SELL" if request.side == OrderSide.BUY else "BUY"
            
            # Create trailing stop order
            trailing_order = Order(
                action=opposite_action,
                totalQuantity=request.quantity,
                orderType="TRAIL",
                auxPrice=trailing_distance,  # Trailing amount in price units
                tif=request.tif,
            )
            
            # Place order
            trade = self.ib.placeOrder(contract, trailing_order)
            
            # Store IDs
            state.ib_order_id = trade.order.orderId
            state.trailing_stop_order_id = trade.order.orderId
            state.ib_perm_id = getattr(trade.order, "permId", None)
            state.status = OrderStatus.SUBMITTED
            state.updated_at = datetime.now(timezone.utc)
            
            self._ib_to_request[state.ib_order_id] = request.id
            
            self.callback.on_order_status(state)
            
        except Exception as exc:
            state.status = OrderStatus.ERROR
            state.error_message = str(exc)
            state.updated_at = datetime.now(timezone.utc)
            self.callback.on_error(request.id, state.error_message)
        
        return state
    
    def check_funds(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        log_callback: Optional[callable] = None,
    ) -> Tuple[bool, float, Dict[str, Any]]:
        """
        Check if sufficient funds are available for FX order.
        
        Args:
            symbol: FX pair (e.g., "EURUSD")
            side: "BUY" or "SELL"
            quantity: Desired quantity
            price: Current price (optional)
            log_callback: Optional callback for logging events
        
        Returns:
            Tuple of (can_trade, adjusted_qty, details_dict)
        """
        if not self._funds_guard:
            # No funds guard - allow order as-is
            return True, quantity, {"reason": "funds_guard_disabled"}
        
        return self._funds_guard.precheck_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            log_event_callback=log_callback,
        )
    
    def place_order_with_funds_check(
        self,
        request: IBKROrderRequest,
        current_price: Optional[float] = None,
        log_callback: Optional[callable] = None,
    ) -> Tuple[IBKROrderState, Dict[str, Any]]:
        """
        Place order with pre-check for available funds.
        
        This method checks if sufficient currency is available before placing
        the order. If funds are insufficient, it either adjusts the quantity
        (AUTO_REDUCE policy) or returns an error state (SKIP policy).
        
        Args:
            request: Order request
            current_price: Current market price
            log_callback: Optional callback for logging events
        
        Returns:
            Tuple of (order_state, funds_check_details)
        """
        # Check funds
        can_trade, adjusted_qty, details = self.check_funds(
            symbol=request.symbol,
            side=request.side.value,
            quantity=request.quantity,
            price=current_price,
            log_callback=log_callback,
        )
        
        if not can_trade:
            # Cannot trade - return error state
            state = IBKROrderState(
                request_id=request.id,
                status=OrderStatus.REJECTED,
                error_message=f"Insufficient funds: {details.get('reason', 'unknown')}",
            )
            self._orders[request.id] = state
            return state, details
        
        # Adjust quantity if needed
        if adjusted_qty != request.quantity:
            # Create modified request with adjusted quantity
            request = IBKROrderRequest(
                id=request.id,
                symbol=request.symbol,
                side=request.side,
                quantity=adjusted_qty,
                order_type=request.order_type,
                limit_price=request.limit_price,
                stop_price=request.stop_price,
                tif=request.tif,
                stop_loss_price=request.stop_loss_price,
                take_profit_price=request.take_profit_price,
                trailing_stop_enabled=request.trailing_stop_enabled,
                trailing_stop_distance=request.trailing_stop_distance,
                trailing_stop_distance_pips=request.trailing_stop_distance_pips,
                signal_preview_id=request.signal_preview_id,
                decision_id=request.decision_id,
                order_intent_id=request.order_intent_id,
            )
        
        # Place order with SL/TP
        state = self.place_order_with_sl_tp(request, current_price)
        return state, details
    
    def place_order_with_sl_tp(
        self,
        request: IBKROrderRequest,
        current_price: Optional[float] = None,
    ) -> IBKROrderState:
        """
        Place order with optional SL/TP or Trailing Stop.
        
        Priority:
        1. If trailing_stop_enabled and has distance -> Main order + Trailing Stop
        2. If both SL and TP prices -> Bracket order
        3. Otherwise -> Simple order
        
        Args:
            request: Order request with optional SL/TP prices
            current_price: Current market price (used to calculate SL/TP if needed)
        """
        # If trailing stop is enabled, use trailing stop approach
        if request.trailing_stop_enabled and (
            request.trailing_stop_distance is not None or 
            request.trailing_stop_distance_pips is not None
        ):
            # First place main order, then trailing stop will be placed on fill
            main_state = self.place_order(request)
            
            if main_state.status not in (OrderStatus.ERROR, OrderStatus.REJECTED):
                # Store trailing stop info for when main order fills
                main_state.is_trailing_stop = True
                main_state.trailing_stop_distance = request.trailing_stop_distance
                # Note: In production, you'd want to place trailing stop AFTER main fills
                # For now, we place it immediately
                if current_price:
                    trailing_request = IBKROrderRequest(
                        symbol=request.symbol,
                        side=request.side,
                        quantity=request.quantity,
                        trailing_stop_enabled=True,
                        trailing_stop_distance=request.trailing_stop_distance,
                        trailing_stop_distance_pips=request.trailing_stop_distance_pips,
                        tif=request.tif,
                    )
                    self.place_trailing_stop_order(trailing_request, current_price)
            
            return main_state
        
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
