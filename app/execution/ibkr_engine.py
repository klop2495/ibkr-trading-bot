"""
IBKR Execution Engine

Replaces ExecutionEngineStub with real IBKR order execution.
Integrates:
- Position Sizer
- OMS (Order Management System)
- Control plane decisions
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.broker.oms import (
    IBKROMS,
    IBKROrderCallback,
    IBKROrderRequest,
    IBKROrderState,
    IBKRFill,
    OrderSide,
    OrderType,
)
from app.models.decision import DecisionV1
from app.models.execution_plan import ExecutionPlanV1
from app.models.risk_verdict import RiskVerdictV1
from app.pm.position_sizer import PositionSizer
from app.storage.repositories import RiskEventsRepo


class ExecutionEngineCallback(IBKROrderCallback):
    """Callbacks for execution events with logging."""
    
    def __init__(self, risk_events_repo: Optional[RiskEventsRepo] = None):
        self.risk_events_repo = risk_events_repo
    
    def on_order_status(self, state: IBKROrderState) -> None:
        """Log order status changes."""
        if self.risk_events_repo:
            self.risk_events_repo.insert(
                event_type="IBKR_ORDER_STATUS",
                severity="info",
                message=f"Order {state.request_id} status: {state.status.value}",
                data={
                    "request_id": str(state.request_id),
                    "ib_order_id": state.ib_order_id,
                    "status": state.status.value,
                    "filled_quantity": state.filled_quantity,
                    "avg_fill_price": state.avg_fill_price,
                },
            )
    
    def on_fill(self, fill: IBKRFill) -> None:
        """Log fills."""
        if self.risk_events_repo:
            self.risk_events_repo.insert(
                event_type="IBKR_ORDER_FILL",
                severity="info",
                message=f"Order {fill.request_id} filled: {fill.quantity} @ {fill.price}",
                data={
                    "request_id": str(fill.request_id),
                    "ib_order_id": fill.ib_order_id,
                    "exec_id": fill.ib_exec_id,
                    "quantity": fill.quantity,
                    "price": fill.price,
                    "commission": fill.commission,
                },
            )
    
    def on_error(self, request_id: UUID, error: str) -> None:
        """Log errors."""
        if self.risk_events_repo:
            self.risk_events_repo.insert(
                event_type="IBKR_ORDER_ERROR",
                severity="error",
                message=f"Order {request_id} error: {error}",
                data={
                    "request_id": str(request_id),
                    "error": error,
                },
            )


class IBKRExecutionEngine:
    """
    Real IBKR execution engine.
    
    Workflow:
    1. Receive verdict + decision
    2. Calculate position size
    3. Create order request
    4. Submit to OMS
    5. Return execution plan
    """
    
    def __init__(
        self,
        oms: IBKROMS,
        position_sizer: Optional[PositionSizer] = None,
        risk_events_repo: Optional[RiskEventsRepo] = None,
        account_equity: float = 10000.0,  # Default for testing
    ):
        self.oms = oms
        self.position_sizer = position_sizer or PositionSizer()
        self.risk_events_repo = risk_events_repo
        self.account_equity = account_equity
    
    def update_equity(self, equity: float) -> None:
        """Update account equity for position sizing."""
        self.account_equity = equity
    
    def plan(
        self,
        verdict: RiskVerdictV1,
        decision: DecisionV1,
        stop_loss_pips: float = 20.0,
        entry_price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> Optional[ExecutionPlanV1]:
        """
        Create execution plan and optionally place order.
        
        Args:
            verdict: Risk verdict from control plane
            decision: Decision from control plane
            stop_loss_pips: Stop loss distance in pips (used if prices not provided)
            entry_price: Entry price (optional)
            stop_price: Stop loss price (optional)
            
        Returns:
            ExecutionPlanV1 if order should be placed, None otherwise
        """
        if not verdict.trade_allowed:
            self._log_event(
                "EXECUTION_BLOCKED",
                "warn",
                f"Trade blocked for {decision.symbol}",
                {"decision_id": str(decision.id), "reason": "risk_not_allowed"},
            )
            return None
        
        if not decision.id:
            raise ValueError("decision.id is required")
        
        # Calculate position size
        if entry_price and stop_price:
            size_result = self.position_sizer.calculate_from_prices(
                equity=self.account_equity,
                entry_price=entry_price,
                stop_price=stop_price,
                symbol=decision.symbol,
                risk_modifier=verdict.risk_modifier,
            )
        else:
            size_result = self.position_sizer.calculate(
                equity=self.account_equity,
                stop_loss_pips=stop_loss_pips,
                symbol=decision.symbol,
                risk_modifier=verdict.risk_modifier,
            )
        
        if size_result.units <= 0:
            self._log_event(
                "EXECUTION_SKIPPED",
                "warn",
                f"Position size too small for {decision.symbol}",
                {
                    "decision_id": str(decision.id),
                    "reason": size_result.reason or "zero_size",
                    "equity": self.account_equity,
                    "risk_modifier": verdict.risk_modifier,
                },
            )
            return None
        
        # Determine side from decision
        side = self._determine_side(decision)
        if side is None:
            self._log_event(
                "EXECUTION_SKIPPED",
                "warn",
                f"Cannot determine side for {decision.symbol}",
                {"decision_id": str(decision.id)},
            )
            return None
        
        ts = verdict.ts_utc or decision.ts_utc or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        
        return ExecutionPlanV1(
            decision_id=decision.id,
            signal_preview_id=decision.signal_preview_id,
            symbol=decision.symbol,
            action="OPEN",
            notes=f"size={size_result.units}, risk={size_result.risk_percent_actual:.2f}%",
        )
    
    def execute(
        self,
        verdict: RiskVerdictV1,
        decision: DecisionV1,
        stop_loss_pips: float = 20.0,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
    ) -> Optional[IBKROrderState]:
        """
        Execute order through IBKR.
        
        Args:
            verdict: Risk verdict
            decision: Decision
            stop_loss_pips: Stop loss in pips for position sizing
            order_type: Order type (MARKET, LIMIT, etc.)
            limit_price: Limit price (for LIMIT orders)
            
        Returns:
            IBKROrderState if order was placed, None otherwise
        """
        plan = self.plan(verdict, decision, stop_loss_pips)
        if plan is None:
            return None
        
        # Calculate position size again for order
        size_result = self.position_sizer.calculate(
            equity=self.account_equity,
            stop_loss_pips=stop_loss_pips,
            symbol=decision.symbol,
            risk_modifier=verdict.risk_modifier,
        )
        
        if size_result.units <= 0:
            return None
        
        # Determine side
        side = self._determine_side(decision)
        if side is None:
            return None
        
        # Create order request
        request = IBKROrderRequest(
            symbol=decision.symbol,
            side=side,
            quantity=size_result.units,
            order_type=order_type,
            limit_price=limit_price if order_type == OrderType.LIMIT else None,
            signal_preview_id=decision.signal_preview_id,
            decision_id=decision.id,
        )
        
        self._log_event(
            "IBKR_ORDER_SUBMIT",
            "info",
            f"Submitting {side.value} order for {decision.symbol}",
            {
                "request_id": str(request.id),
                "decision_id": str(decision.id),
                "symbol": decision.symbol,
                "side": side.value,
                "quantity": size_result.units,
                "order_type": order_type.value,
            },
        )
        
        # Place order
        state = self.oms.place_order(request)
        
        return state
    
    def _determine_side(self, decision: DecisionV1) -> Optional[OrderSide]:
        """Determine order side from decision."""
        # Check flags for direction
        flags = decision.flags or []
        
        for flag in flags:
            flag_upper = flag.upper()
            if "LONG" in flag_upper or "BUY" in flag_upper:
                return OrderSide.BUY
            if "SHORT" in flag_upper or "SELL" in flag_upper:
                return OrderSide.SELL
        
        # Check commentary
        if decision.commentary:
            commentary_upper = decision.commentary.upper()
            if "LONG" in commentary_upper or "BUY" in commentary_upper:
                return OrderSide.BUY
            if "SHORT" in commentary_upper or "SELL" in commentary_upper:
                return OrderSide.SELL
        
        # Cannot determine
        return None
    
    def _log_event(
        self,
        event_type: str,
        severity: str,
        message: str,
        data: Optional[dict] = None,
    ) -> None:
        """Log event to risk_events."""
        if self.risk_events_repo:
            self.risk_events_repo.insert(
                event_type=event_type,
                severity=severity,
                message=message,
                data=data or {},
            )
