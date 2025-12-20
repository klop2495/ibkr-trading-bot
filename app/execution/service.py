"""
Execution Service

Unified service for order execution with:
- Dry-run mode (logs but doesn't execute)
- Paper trading mode
- Live trading mode
- Integration with OMS, Position Sizer, Connection Manager
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from app.broker.connection_manager import (
    ConnectionConfig,
    ConnectionState,
    IBKRConnectionManager,
)
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
from app.models.bot_settings import BotSettings
from app.models.decision import DecisionV1
from app.models.risk_verdict import RiskVerdictV1
from app.pm.position_sizer import PositionSizer, PositionSizerConfig, PositionSizeResult
from app.storage.repositories import RiskEventsRepo


logger = logging.getLogger(__name__)


class ExecutionMode(str, Enum):
    """Execution mode."""
    DISABLED = "disabled"   # No execution at all
    DRY_RUN = "dry_run"     # Log what would be done, no orders
    PAPER = "paper"         # Paper trading via IBKR
    LIVE = "live"           # Live trading via IBKR


@dataclass
class ExecutionResult:
    """Result of execution attempt."""
    executed: bool
    mode: ExecutionMode
    order_id: Optional[UUID] = None
    ib_order_id: Optional[int] = None
    symbol: str = ""
    side: Optional[OrderSide] = None
    quantity: float = 0.0
    status: Optional[OrderStatus] = None
    reason: Optional[str] = None
    dry_run_log: Optional[str] = None


class ExecutionServiceCallback(IBKROrderCallback):
    """Callbacks for execution events with logging to risk_events."""
    
    def __init__(self, risk_events_repo: Optional[RiskEventsRepo] = None):
        self.risk_events_repo = risk_events_repo
    
    def on_order_status(self, state: IBKROrderState) -> None:
        logger.info(f"Order {state.request_id} status: {state.status.value}")
        if self.risk_events_repo:
            self.risk_events_repo.insert(
                event_type="EXECUTION_ORDER_STATUS",
                severity="info",
                message=f"Order status: {state.status.value}",
                data={
                    "request_id": str(state.request_id),
                    "ib_order_id": state.ib_order_id,
                    "status": state.status.value,
                    "filled_quantity": state.filled_quantity,
                    "avg_fill_price": state.avg_fill_price,
                },
            )
    
    def on_fill(self, fill: IBKRFill) -> None:
        logger.info(f"Order {fill.request_id} filled: {fill.quantity} @ {fill.price}")
        if self.risk_events_repo:
            self.risk_events_repo.insert(
                event_type="EXECUTION_ORDER_FILL",
                severity="info",
                message=f"Order filled: {fill.quantity} @ {fill.price}",
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
        logger.error(f"Order {request_id} error: {error}")
        if self.risk_events_repo:
            self.risk_events_repo.insert(
                event_type="EXECUTION_ORDER_ERROR",
                severity="error",
                message=f"Order error: {error}",
                data={
                    "request_id": str(request_id),
                    "error": error,
                },
            )


class ExecutionService:
    """
    Unified execution service.
    
    Modes:
    - DISABLED: No execution, returns immediately
    - DRY_RUN: Calculates everything, logs, but doesn't place orders
    - PAPER: Places orders via IBKR paper account
    - LIVE: Places orders via IBKR live account (requires explicit enable)
    """
    
    # Default equity for dry-run mode
    DEFAULT_EQUITY = 10000.0
    
    def __init__(
        self,
        risk_events_repo: Optional[RiskEventsRepo] = None,
        connection_config: Optional[ConnectionConfig] = None,
    ):
        self.risk_events_repo = risk_events_repo
        self.connection_config = connection_config
        
        # Components (lazy init)
        self._connection_manager: Optional[IBKRConnectionManager] = None
        self._oms: Optional[IBKROMS] = None
        self._position_sizer: Optional[PositionSizer] = None
        self._callback: Optional[ExecutionServiceCallback] = None
        
        # State
        self._equity: float = self.DEFAULT_EQUITY
        self._mode: ExecutionMode = ExecutionMode.DISABLED
        self._initialized: bool = False
    
    def _determine_mode(self, settings: BotSettings) -> ExecutionMode:
        """Determine execution mode from env and settings."""
        # Check env first
        if os.getenv("EXECUTION_ENABLED") != "1":
            return ExecutionMode.DISABLED
        
        # Check if dry-run mode
        if os.getenv("EXECUTION_DRY_RUN") == "1":
            return ExecutionMode.DRY_RUN
        
        # Check IBKR enabled
        if os.getenv("IBKR_ENABLED") != "1":
            return ExecutionMode.DRY_RUN
        
        # Check settings
        if not getattr(settings, "trading_enabled", False):
            return ExecutionMode.DISABLED
        
        mode = getattr(settings, "mode", "paper")
        
        if mode == "live":
            if os.getenv("ALLOW_LIVE_EXECUTION") == "1":
                return ExecutionMode.LIVE
            else:
                logger.warning("Live mode requested but ALLOW_LIVE_EXECUTION != 1, using paper")
                return ExecutionMode.PAPER
        
        return ExecutionMode.PAPER
    
    def _init_components(self) -> bool:
        """Initialize IBKR components if needed."""
        if self._initialized:
            return True
        
        if self._mode in (ExecutionMode.DISABLED, ExecutionMode.DRY_RUN):
            self._initialized = True
            return True
        
        try:
            # Position sizer
            sizer_config = PositionSizerConfig(
                max_risk_per_trade_pct=1.0,
                min_risk_per_trade_pct=0.1,
                max_position_size=100000.0,
                min_position_size=1000.0,
            )
            self._position_sizer = PositionSizer(sizer_config)
            
            # Callback
            self._callback = ExecutionServiceCallback(self.risk_events_repo)
            
            # Connection manager
            config = self.connection_config or ConnectionConfig(
                host=os.getenv("IBKR_HOST", "127.0.0.1"),
                port=int(os.getenv("IBKR_PORT", "7497")),
                client_id=int(os.getenv("IBKR_CLIENT_ID", "1")),
                readonly=False,
            )
            self._connection_manager = IBKRConnectionManager(
                config=config,
                callback=None,  # We handle connection events separately
            )
            
            # Try to connect
            if not self._connection_manager.connect():
                logger.error("Failed to connect to IBKR")
                self._log_event("EXECUTION_INIT_FAILED", "error", "Failed to connect to IBKR")
                return False
            
            # OMS
            self._oms = IBKROMS(
                ib=self._connection_manager.ib,
                callback=self._callback,
            )
            
            self._initialized = True
            self._log_event("EXECUTION_INITIALIZED", "info", f"Execution service initialized in {self._mode.value} mode")
            return True
            
        except Exception as e:
            logger.exception("Failed to initialize execution components")
            self._log_event("EXECUTION_INIT_FAILED", "error", str(e))
            return False
    
    def _ensure_position_sizer(self) -> PositionSizer:
        """Get or create position sizer."""
        if self._position_sizer is None:
            self._position_sizer = PositionSizer()
        return self._position_sizer
    
    def update_equity(self, equity: float) -> None:
        """Update account equity for position sizing."""
        self._equity = equity
        logger.info(f"Equity updated: {equity}")
    
    def get_equity(self) -> float:
        """Get current equity."""
        return self._equity
    
    def _determine_side(self, decision: DecisionV1, verdict: RiskVerdictV1) -> Optional[OrderSide]:
        """Determine order side from decision."""
        flags = decision.flags or []
        
        # Check flags
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
        
        return None
    
    def _calculate_position_size(
        self,
        symbol: str,
        risk_modifier: float,
        stop_loss_pips: float = 20.0,
    ) -> PositionSizeResult:
        """Calculate position size."""
        sizer = self._ensure_position_sizer()
        return sizer.calculate(
            equity=self._equity,
            stop_loss_pips=stop_loss_pips,
            symbol=symbol,
            risk_modifier=risk_modifier,
        )
    
    def execute(
        self,
        decision: DecisionV1,
        verdict: RiskVerdictV1,
        settings: BotSettings,
        stop_loss_pips: float = 20.0,
        order_type: OrderType = OrderType.MARKET,
    ) -> ExecutionResult:
        """
        Execute trade based on decision and verdict.
        
        Returns ExecutionResult with details of what was done.
        """
        # Determine mode
        self._mode = self._determine_mode(settings)
        
        # Check if execution is allowed
        if self._mode == ExecutionMode.DISABLED:
            return ExecutionResult(
                executed=False,
                mode=self._mode,
                reason="execution_disabled",
            )
        
        # Check verdict allows trade
        if not verdict.trade_allowed:
            self._log_event(
                "EXECUTION_BLOCKED",
                "info",
                f"Trade blocked by risk for {decision.symbol}",
                {"decision_id": str(decision.id), "symbol": decision.symbol},
            )
            return ExecutionResult(
                executed=False,
                mode=self._mode,
                symbol=decision.symbol,
                reason="risk_not_allowed",
            )
        
        # Determine side
        side = self._determine_side(decision, verdict)
        if side is None:
            self._log_event(
                "EXECUTION_SKIPPED",
                "warn",
                f"Cannot determine side for {decision.symbol}",
                {"decision_id": str(decision.id)},
            )
            return ExecutionResult(
                executed=False,
                mode=self._mode,
                symbol=decision.symbol,
                reason="cannot_determine_side",
            )
        
        # Calculate position size
        size_result = self._calculate_position_size(
            symbol=decision.symbol,
            risk_modifier=verdict.risk_modifier,
            stop_loss_pips=stop_loss_pips,
        )
        
        if size_result.units <= 0:
            self._log_event(
                "EXECUTION_SKIPPED",
                "warn",
                f"Position size too small for {decision.symbol}",
                {
                    "decision_id": str(decision.id),
                    "reason": size_result.reason or "zero_size",
                    "equity": self._equity,
                },
            )
            return ExecutionResult(
                executed=False,
                mode=self._mode,
                symbol=decision.symbol,
                side=side,
                quantity=0,
                reason=size_result.reason or "position_too_small",
            )
        
        # DRY RUN mode — log but don't execute
        if self._mode == ExecutionMode.DRY_RUN:
            dry_run_log = (
                f"DRY_RUN: Would place {side.value} {size_result.units} {decision.symbol} "
                f"(risk={size_result.risk_percent_actual:.2f}%, equity={self._equity})"
            )
            logger.info(dry_run_log)
            self._log_event(
                "EXECUTION_DRY_RUN",
                "info",
                dry_run_log,
                {
                    "decision_id": str(decision.id),
                    "symbol": decision.symbol,
                    "side": side.value,
                    "quantity": size_result.units,
                    "risk_pct": size_result.risk_percent_actual,
                },
            )
            return ExecutionResult(
                executed=True,  # "executed" in dry-run sense
                mode=self._mode,
                order_id=uuid4(),
                symbol=decision.symbol,
                side=side,
                quantity=size_result.units,
                status=OrderStatus.FILLED,  # Simulated
                dry_run_log=dry_run_log,
            )
        
        # PAPER or LIVE mode — actually place order
        if not self._init_components():
            return ExecutionResult(
                executed=False,
                mode=self._mode,
                symbol=decision.symbol,
                reason="ibkr_not_connected",
            )
        
        if self._oms is None:
            return ExecutionResult(
                executed=False,
                mode=self._mode,
                symbol=decision.symbol,
                reason="oms_not_initialized",
            )
        
        # Create order request
        request = IBKROrderRequest(
            symbol=decision.symbol,
            side=side,
            quantity=size_result.units,
            order_type=order_type,
            signal_preview_id=decision.signal_preview_id,
            decision_id=decision.id,
        )
        
        self._log_event(
            "EXECUTION_SUBMIT",
            "info",
            f"Submitting {side.value} {size_result.units} {decision.symbol}",
            {
                "request_id": str(request.id),
                "decision_id": str(decision.id),
                "symbol": decision.symbol,
                "side": side.value,
                "quantity": size_result.units,
                "mode": self._mode.value,
            },
        )
        
        # Place order
        try:
            state = self._oms.place_order(request)
            return ExecutionResult(
                executed=True,
                mode=self._mode,
                order_id=request.id,
                ib_order_id=state.ib_order_id,
                symbol=decision.symbol,
                side=side,
                quantity=size_result.units,
                status=state.status,
            )
        except Exception as e:
            logger.exception(f"Order placement failed: {e}")
            self._log_event(
                "EXECUTION_FAILED",
                "error",
                f"Order placement failed: {e}",
                {"request_id": str(request.id), "error": str(e)},
            )
            return ExecutionResult(
                executed=False,
                mode=self._mode,
                order_id=request.id,
                symbol=decision.symbol,
                side=side,
                quantity=size_result.units,
                reason=str(e),
            )
    
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
    
    def shutdown(self) -> None:
        """Cleanup resources."""
        if self._oms:
            self._oms.shutdown()
        if self._connection_manager:
            self._connection_manager.disconnect()
        self._initialized = False
        logger.info("Execution service shutdown")
