"""
Execution Service

Unified service for order execution with:
- Dry-run mode (logs but doesn't execute)
- Paper trading mode
- Live trading mode
- Integration with OMS, Position Sizer, Connection Manager
- Stop-Loss / Take-Profit support
- Phase 7: Performance tracking on trade close
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
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
    IBKRBracketOrderRequest,
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
from app.storage.repositories import RiskEventsRepo, TradesHistoryRepo

# Phase 7: Import performance tracker
try:
    from app.agents.performance_tracker import AgentPerformanceTracker, TradeOutcome
    PERFORMANCE_TRACKER_AVAILABLE = True
except ImportError:
    PERFORMANCE_TRACKER_AVAILABLE = False


logger = logging.getLogger(__name__)


# Pip values for different currency pairs
PIP_VALUES = {
    "DEFAULT": 0.0001,  # Most pairs
    "JPY": 0.01,        # JPY pairs
}


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
    
    # SL/TP info
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    entry_price: Optional[float] = None
    is_bracket: bool = False


@dataclass
class TradeCloseInfo:
    """
    Information about a closed trade for performance tracking.
    
    Phase 7: Used to record trade outcomes.
    """
    trade_id: str
    symbol: str
    direction: str  # "BUY" or "SELL"
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pips: float
    agent_votes: Dict[str, str] = field(default_factory=dict)
    agent_confidences: Dict[str, float] = field(default_factory=dict)
    final_signal: str = "HOLD"
    hold_time_minutes: int = 0


class ExecutionServiceCallback(IBKROrderCallback):
    """
    Callbacks for execution events.
    
    P0-B: Order Lifecycle Management
    - Logs events to risk_events table
    - Updates trades_history status on order state changes
    - Handles FILLED, CANCELLED, REJECTED transitions
    
    Phase 7: Triggers performance tracking on trade close.
    """
    
    def __init__(
        self,
        risk_events_repo: Optional[RiskEventsRepo] = None,
        trades_history_repo: Optional[TradesHistoryRepo] = None,
        on_trade_closed: Optional[callable] = None,  # Phase 7
    ):
        self.risk_events_repo = risk_events_repo
        self.trades_history_repo = trades_history_repo
        self._on_trade_closed = on_trade_closed  # Phase 7: callback
    
    def on_order_status(self, state: IBKROrderState) -> None:
        """Handle order status change - update trades_history accordingly."""
        logger.info(f"Order {state.request_id} status: {state.status.value}")
        
        # Log to risk_events
        if self.risk_events_repo:
            data = {
                "request_id": str(state.request_id),
                "ib_order_id": state.ib_order_id,
                "status": state.status.value,
                "filled_quantity": state.filled_quantity,
                "avg_fill_price": state.avg_fill_price,
            }
            if state.is_bracket:
                data["is_bracket"] = True
                data["stop_loss_order_id"] = state.stop_loss_order_id
                data["take_profit_order_id"] = state.take_profit_order_id
            
            self.risk_events_repo.insert(
                event_type="EXECUTION_ORDER_STATUS",
                severity="info",
                message=f"Order status: {state.status.value}",
                data=data,
            )
        
        # P0-B: Update trades_history status
        if self.trades_history_repo and state.ib_order_id:
            try:
                trade = self.trades_history_repo.get_trade_by_ib_order_id(state.ib_order_id)
                if trade:
                    trade_id = trade.get("id")
                    new_status = self._map_order_status_to_trade_status(state.status)
                    
                    if new_status and new_status != trade.get("status"):
                        update_kwargs = {
                            "trade_id": trade_id,
                            "status": new_status,
                        }
                        
                        # Update entry price on fill
                        if state.status == OrderStatus.FILLED and state.avg_fill_price:
                            update_kwargs["entry_price"] = state.avg_fill_price
                        
                        # Add error message for rejected/error
                        if state.status in (OrderStatus.REJECTED, OrderStatus.ERROR):
                            update_kwargs["error_message"] = state.error_message
                        
                        self.trades_history_repo.update_status(**update_kwargs)
                        logger.info(f"Trade {trade_id} status updated: {trade.get('status')} -> {new_status}")
            except Exception as e:
                logger.error(f"Failed to update trade status: {e}")
    
    def _map_order_status_to_trade_status(self, order_status: OrderStatus) -> Optional[str]:
        """
        Map OMS OrderStatus to trades_history status.
        
        P0-B Lifecycle:
        - PENDING -> PENDING (no change needed)
        - SUBMITTED -> SUBMITTED
        - ACCEPTED -> SUBMITTED (treat as submitted)
        - PARTIALLY_FILLED -> OPEN (position exists)
        - FILLED -> OPEN
        - CANCELLED -> CANCELLED
        - REJECTED -> REJECTED
        - ERROR -> REJECTED
        """
        mapping = {
            OrderStatus.PENDING: "PENDING",
            OrderStatus.SUBMITTED: "SUBMITTED",
            OrderStatus.ACCEPTED: "SUBMITTED",
            OrderStatus.PARTIALLY_FILLED: "OPEN",
            OrderStatus.FILLED: "OPEN",
            OrderStatus.CANCELLED: "CANCELLED",
            OrderStatus.REJECTED: "REJECTED",
            OrderStatus.ERROR: "REJECTED",
        }
        return mapping.get(order_status)
    
    def on_fill(self, fill: IBKRFill) -> None:
        """Handle order fill - update trade with actual fill price."""
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
        
        # P0-B: Update trade entry price with actual fill price
        if self.trades_history_repo:
            try:
                trade = self.trades_history_repo.get_trade_by_ib_order_id(fill.ib_order_id)
                if trade:
                    self.trades_history_repo.update_status(
                        trade_id=trade["id"],
                        status="OPEN",
                        entry_price=fill.price,
                    )
                    logger.info(f"Trade {trade['id']} filled at {fill.price}")
            except Exception as e:
                logger.error(f"Failed to update trade on fill: {e}")
    
    def on_error(self, request_id: UUID, error: str) -> None:
        """Handle order error - mark trade as rejected."""
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
    
    Features:
    - Automatic SL/TP calculation from signal_preview distances
    - Bracket orders for risk management
    - Position sizing based on equity and risk parameters
    - Phase 7: Performance tracking on trade close
    """
    
    # Default equity for dry-run mode
    DEFAULT_EQUITY = 10000.0
    
    def __init__(
        self,
        risk_events_repo: Optional[RiskEventsRepo] = None,
        trades_history_repo: Optional[TradesHistoryRepo] = None,
        connection_config: Optional[ConnectionConfig] = None,
        performance_tracker: Optional['AgentPerformanceTracker'] = None,  # Phase 7
    ):
        self.risk_events_repo = risk_events_repo
        self.trades_history_repo = trades_history_repo
        self.connection_config = connection_config
        
        # Components (lazy init)
        self._connection_manager: Optional[IBKRConnectionManager] = None
        self._oms: Optional[IBKROMS] = None
        self._position_sizer: Optional[PositionSizer] = None
        self._callback: Optional[ExecutionServiceCallback] = None
        
        # Phase 7: Performance tracker
        self._performance_tracker: Optional['AgentPerformanceTracker'] = performance_tracker
        if self._performance_tracker is None and PERFORMANCE_TRACKER_AVAILABLE:
            try:
                self._performance_tracker = AgentPerformanceTracker()
                logger.info("ExecutionService: AgentPerformanceTracker initialized")
            except Exception as e:
                logger.warning(f"ExecutionService: Failed to init performance tracker: {e}")
        
        # State
        self._equity: float = self.DEFAULT_EQUITY
        self._mode: ExecutionMode = ExecutionMode.DISABLED
        self._initialized: bool = False
        
        # Price cache (for SL/TP calculation)
        self._price_cache: dict[str, float] = {}
        
        # Phase 7: Cache agent data for trade outcomes
        self._pending_trade_data: Dict[str, Dict[str, Any]] = {}
    
    @property
    def performance_tracker(self) -> Optional['AgentPerformanceTracker']:
        """Get performance tracker instance."""
        return self._performance_tracker
    
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
            
            # Callback - P0-B: pass trades_history_repo for lifecycle updates
            self._callback = ExecutionServiceCallback(
                risk_events_repo=self.risk_events_repo,
                trades_history_repo=self.trades_history_repo,
                on_trade_closed=self._on_trade_closed_callback,  # Phase 7
            )
            
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
    
    def update_price(self, symbol: str, price: float) -> None:
        """Update price cache for a symbol."""
        self._price_cache[symbol] = price
    
    def get_price(self, symbol: str) -> Optional[float]:
        """Get cached price for a symbol."""
        return self._price_cache.get(symbol)
    
    def _get_pip_value(self, symbol: str) -> float:
        """Get pip value for a symbol."""
        if "JPY" in symbol.upper():
            return PIP_VALUES["JPY"]
        return PIP_VALUES["DEFAULT"]
    
    def _calculate_sl_tp_prices(
        self,
        symbol: str,
        side: OrderSide,
        entry_price: float,
        sl_distance_pips: Optional[float],
        tp_distance_pips: Optional[float],
    ) -> tuple[Optional[float], Optional[float]]:
        """
        Calculate SL and TP prices from pip distances.
        
        Args:
            symbol: Currency pair
            side: BUY or SELL
            entry_price: Entry price
            sl_distance_pips: Stop loss distance in pips
            tp_distance_pips: Take profit distance in pips
        
        Returns:
            Tuple of (stop_loss_price, take_profit_price)
        """
        pip_value = self._get_pip_value(symbol)
        
        sl_price = None
        tp_price = None
        
        if side == OrderSide.BUY:
            # For BUY: SL below entry, TP above entry
            if sl_distance_pips is not None and sl_distance_pips > 0:
                sl_price = entry_price - (sl_distance_pips * pip_value)
            if tp_distance_pips is not None and tp_distance_pips > 0:
                tp_price = entry_price + (tp_distance_pips * pip_value)
        else:
            # For SELL: SL above entry, TP below entry
            if sl_distance_pips is not None and sl_distance_pips > 0:
                sl_price = entry_price + (sl_distance_pips * pip_value)
            if tp_distance_pips is not None and tp_distance_pips > 0:
                tp_price = entry_price - (tp_distance_pips * pip_value)
        
        # Round to appropriate precision
        if sl_price is not None:
            sl_price = round(sl_price, 5 if pip_value == 0.0001 else 3)
        if tp_price is not None:
            tp_price = round(tp_price, 5 if pip_value == 0.0001 else 3)
        
        return sl_price, tp_price
    
    def _determine_side(self, decision: DecisionV1, verdict: RiskVerdictV1, direction: Optional[str] = None) -> Optional[OrderSide]:
        """Determine order side from decision or direction."""
        # First check explicit direction from signal_preview
        if direction:
            direction_upper = direction.upper()
            if direction_upper in ("LONG", "BUY"):
                return OrderSide.BUY
            if direction_upper in ("SHORT", "SELL"):
                return OrderSide.SELL
        
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
    
    def _get_open_trades_count(self) -> int:
        """Get count of currently open trades."""
        if not self.trades_history_repo:
            return 0
        try:
            return self.trades_history_repo.count_open_trades()
        except Exception as e:
            logger.error(f"Failed to get open trades count: {e}")
            return 0
    
    def _get_open_trades_for_symbol(self, symbol: str) -> int:
        """Get count of open trades for a specific symbol."""
        if not self.trades_history_repo:
            return 0
        try:
            trades = self.trades_history_repo.get_open_trades(symbol)
            return len(trades)
        except Exception as e:
            logger.error(f"Failed to get open trades for {symbol}: {e}")
            return 0
    
    def _calculate_total_exposure(self) -> float:
        """Calculate total exposure from all open trades."""
        if not self.trades_history_repo:
            return 0.0
        try:
            trades = self.trades_history_repo.get_all_open_trades()
            total = 0.0
            for trade in trades:
                qty = trade.get('quantity') or 0
                entry = trade.get('entry_price') or 0
                total += qty * entry
            return total
        except Exception as e:
            logger.error(f"Failed to calculate exposure: {e}")
            return 0.0
    
    # ========== Phase 7: Performance Tracking ==========
    
    def record_agent_data_for_trade(
        self,
        trade_id: str,
        agent_votes: Dict[str, str],
        agent_confidences: Dict[str, float],
        final_signal: str,
    ) -> None:
        """
        Phase 7: Store agent data for a pending trade.
        
        Call this after execute() to save agent decisions for later
        performance tracking when the trade closes.
        """
        self._pending_trade_data[trade_id] = {
            "agent_votes": agent_votes,
            "agent_confidences": agent_confidences,
            "final_signal": final_signal,
            "open_time": datetime.now(timezone.utc),
        }
        logger.debug(f"Stored agent data for trade {trade_id}")
    
    def on_trade_closed(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
    ) -> Dict[str, float]:
        """
        Phase 7: Record trade outcome for performance tracking.
        
        Call this when a trade is closed (by SL, TP, or manual).
        
        Args:
            trade_id: Unique trade identifier
            symbol: Trading symbol
            direction: "BUY" or "SELL"
            entry_price: Entry price
            exit_price: Exit price
            pnl: Realized P&L
        
        Returns:
            Dict of weight changes per agent.
        """
        if not self._performance_tracker:
            return {}
        
        # Get stored agent data
        trade_data = self._pending_trade_data.pop(trade_id, {})
        agent_votes = trade_data.get("agent_votes", {})
        agent_confidences = trade_data.get("agent_confidences", {})
        final_signal = trade_data.get("final_signal", "HOLD")
        open_time = trade_data.get("open_time")
        
        # Calculate hold time
        hold_time_minutes = 0
        if open_time:
            hold_time_minutes = int((datetime.now(timezone.utc) - open_time).total_seconds() / 60)
        
        # Calculate pnl in pips
        pip_value = self._get_pip_value(symbol)
        pnl_pips = (exit_price - entry_price) / pip_value
        if direction == "SELL":
            pnl_pips = -pnl_pips
        
        try:
            outcome = TradeOutcome(
                trade_id=trade_id,
                symbol=symbol,
                direction=direction,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl=pnl,
                pnl_pips=pnl_pips,
                agent_votes=agent_votes,
                agent_confidences=agent_confidences,
                final_signal=final_signal,
                hold_time_minutes=hold_time_minutes,
            )
            
            changes = self._performance_tracker.record_outcome(outcome)
            
            self._log_event(
                "TRADE_OUTCOME_RECORDED",
                "info",
                f"Trade {trade_id} closed: pnl={pnl:.2f} pips={pnl_pips:.1f}",
                {
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "pnl": pnl,
                    "pnl_pips": pnl_pips,
                    "weight_changes": changes,
                },
            )
            
            logger.info(f"Performance tracker updated for trade {trade_id}: {changes}")
            return changes
            
        except Exception as e:
            logger.error(f"Failed to record trade outcome: {e}")
            return {}
    
    def _on_trade_closed_callback(self, trade: Dict[str, Any]) -> None:
        """
        Phase 7: Internal callback when trade is closed via broker.
        
        Called by ExecutionServiceCallback when SL/TP is hit.
        """
        trade_id = trade.get("id")
        if not trade_id:
            return
        
        self.on_trade_closed(
            trade_id=str(trade_id),
            symbol=trade.get("symbol", ""),
            direction=trade.get("side", "BUY"),
            entry_price=trade.get("entry_price", 0.0),
            exit_price=trade.get("exit_price", 0.0),
            pnl=trade.get("pnl", 0.0),
        )
    
    def get_performance_report(self) -> Optional[Dict[str, Any]]:
        """
        Phase 7: Get performance report from tracker.
        """
        if not self._performance_tracker:
            return None
        
        try:
            return self._performance_tracker.get_performance_report()
        except Exception as e:
            logger.error(f"Failed to get performance report: {e}")
            return None
    
    def save_performance_state(self) -> Optional[Dict[str, Any]]:
        """
        Phase 7: Save performance tracker state for persistence.
        
        Returns:
            Dict that can be saved to database/file.
        """
        if not self._performance_tracker:
            return None
        
        try:
            return self._performance_tracker.to_dict()
        except Exception as e:
            logger.error(f"Failed to save performance state: {e}")
            return None
    
    def load_performance_state(self, state: Dict[str, Any]) -> bool:
        """
        Phase 7: Load performance tracker state from persistence.
        
        Args:
            state: Dict previously returned by save_performance_state()
        
        Returns:
            True if loaded successfully.
        """
        if not PERFORMANCE_TRACKER_AVAILABLE:
            return False
        
        try:
            self._performance_tracker = AgentPerformanceTracker.from_dict(state)
            logger.info(f"Performance tracker loaded: {len(state.get('outcomes', []))} outcomes")
            return True
        except Exception as e:
            logger.error(f"Failed to load performance state: {e}")
            return False
    
    # ========== End Phase 7 ==========

    def execute(
        self,
        decision: DecisionV1,
        verdict: RiskVerdictV1,
        settings: BotSettings,
        stop_loss_pips: Optional[float] = None,
        take_profit_pips: Optional[float] = None,
        entry_price: Optional[float] = None,
        order_type: OrderType = OrderType.MARKET,
        direction: Optional[str] = None,
        agent_votes: Optional[Dict[str, str]] = None,  # Phase 7
        agent_confidences: Optional[Dict[str, float]] = None,  # Phase 7
        final_signal: Optional[str] = None,  # Phase 7
    ) -> ExecutionResult:
        """
        Execute trade based on decision and verdict.
        
        Args:
            decision: Control plane decision
            verdict: Risk verdict
            settings: Bot settings
            stop_loss_pips: SL distance in pips (overrides signal_preview)
            take_profit_pips: TP distance in pips (overrides signal_preview)
            entry_price: Entry price (uses cached price if not provided)
            order_type: Order type (default: MARKET)
            agent_votes: Phase 7 - agent votes for performance tracking
            agent_confidences: Phase 7 - agent confidences
            final_signal: Phase 7 - final aggregated signal
        
        Returns:
            ExecutionResult with details of what was done.
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
        
        # ========== MONEY MANAGEMENT CHECKS ==========
        
        # 1. Check max concurrent trades
        max_positions = getattr(settings, 'max_open_positions', 3)
        current_open = self._get_open_trades_count()
        if current_open >= max_positions:
            self._log_event(
                "EXECUTION_BLOCKED",
                "warn",
                f"Max open positions reached: {current_open}/{max_positions}",
                {"decision_id": str(decision.id), "symbol": decision.symbol, "current_open": current_open},
            )
            return ExecutionResult(
                executed=False,
                mode=self._mode,
                symbol=decision.symbol,
                reason=f"max_positions_reached:{current_open}/{max_positions}",
            )
        
        # 2. Check if already have position in this symbol
        symbol_positions = self._get_open_trades_for_symbol(decision.symbol)
        if symbol_positions > 0:
            self._log_event(
                "EXECUTION_BLOCKED",
                "info",
                f"Already have open position in {decision.symbol}",
                {"decision_id": str(decision.id), "symbol": decision.symbol, "existing_positions": symbol_positions},
            )
            return ExecutionResult(
                executed=False,
                mode=self._mode,
                symbol=decision.symbol,
                reason=f"already_have_position:{decision.symbol}",
            )
        
        # 3. Check leverage limit
        max_leverage = getattr(settings, 'max_effective_leverage', 5.0)
        current_exposure = self._calculate_total_exposure()
        max_exposure = self._equity * max_leverage
        
        # ========== END MONEY MANAGEMENT CHECKS ==========
        
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
        side = self._determine_side(decision, verdict, direction=direction)
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
        
        # Get SL/TP distances (prefer passed values, fallback to signal_preview)
        sl_pips = stop_loss_pips
        tp_pips = take_profit_pips
        
        # Default SL for position sizing if not provided
        effective_sl_pips = sl_pips if sl_pips is not None else 20.0
        
        # Get entry price early for leverage calculation
        current_price = entry_price or self.get_price(decision.symbol)
        
        # Calculate position size
        size_result = self._calculate_position_size(
            symbol=decision.symbol,
            risk_modifier=verdict.risk_modifier,
            stop_loss_pips=effective_sl_pips,
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
        
        # Check if new position would exceed leverage limit
        estimated_position_value = size_result.units * (current_price or 1.0)
        new_total_exposure = current_exposure + estimated_position_value
        if new_total_exposure > max_exposure:
            self._log_event(
                "EXECUTION_BLOCKED",
                "warn",
                f"Leverage limit exceeded: {new_total_exposure:.0f}/{max_exposure:.0f}",
                {
                    "decision_id": str(decision.id),
                    "symbol": decision.symbol,
                    "current_exposure": current_exposure,
                    "new_position_value": estimated_position_value,
                    "max_exposure": max_exposure,
                    "equity": self._equity,
                    "max_leverage": max_leverage,
                },
            )
            return ExecutionResult(
                executed=False,
                mode=self._mode,
                symbol=decision.symbol,
                side=side,
                quantity=size_result.units,
                reason=f"leverage_exceeded:{new_total_exposure:.0f}/{max_exposure:.0f}",
            )
        
        # Calculate SL/TP prices if we have distances and a price
        sl_price = None
        tp_price = None
        if current_price is not None and (sl_pips is not None or tp_pips is not None):
            sl_price, tp_price = self._calculate_sl_tp_prices(
                symbol=decision.symbol,
                side=side,
                entry_price=current_price,
                sl_distance_pips=sl_pips,
                tp_distance_pips=tp_pips,
            )
        
        is_bracket = sl_price is not None and tp_price is not None
        
        # DRY RUN mode — log but don't execute
        if self._mode == ExecutionMode.DRY_RUN:
            sl_tp_info = ""
            if sl_price is not None or tp_price is not None:
                sl_tp_info = f" SL={sl_price} TP={tp_price}"
            
            dry_run_log = (
                f"DRY_RUN: Would place {side.value} {size_result.units} {decision.symbol} "
                f"(risk={size_result.risk_percent_actual:.2f}%, equity={self._equity}){sl_tp_info}"
            )
            if is_bracket:
                dry_run_log += " [BRACKET ORDER]"
            
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
                    "stop_loss_price": sl_price,
                    "take_profit_price": tp_price,
                    "entry_price": current_price,
                    "is_bracket": is_bracket,
                },
            )
            
            # Record trade in trades_history (even for dry-run)
            trade_id = self._record_trade_open(
                symbol=decision.symbol,
                side=side,
                quantity=size_result.units,
                entry_price=current_price,
                stop_loss=sl_price,
                take_profit=tp_price,
                mode="dry_run",
                signal_preview_id=decision.signal_preview_id,
                decision_id=decision.id,
            )
            
            # Phase 7: Store agent data for performance tracking
            if trade_id and agent_votes:
                self.record_agent_data_for_trade(
                    trade_id=trade_id,
                    agent_votes=agent_votes,
                    agent_confidences=agent_confidences or {},
                    final_signal=final_signal or "HOLD",
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
                stop_loss_price=sl_price,
                take_profit_price=tp_price,
                entry_price=current_price,
                is_bracket=is_bracket,
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
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
            signal_preview_id=decision.signal_preview_id,
            decision_id=decision.id,
        )
        
        self._log_event(
            "EXECUTION_SUBMIT",
            "info",
            f"Submitting {side.value} {size_result.units} {decision.symbol}" + 
            (f" SL={sl_price} TP={tp_price}" if is_bracket else ""),
            {
                "request_id": str(request.id),
                "decision_id": str(decision.id),
                "symbol": decision.symbol,
                "side": side.value,
                "quantity": size_result.units,
                "mode": self._mode.value,
                "stop_loss_price": sl_price,
                "take_profit_price": tp_price,
                "is_bracket": is_bracket,
            },
        )
        
        # P0-B: Create trade record FIRST with PENDING status
        trade_id = self._record_trade_pending(
            symbol=decision.symbol,
            side=side,
            quantity=size_result.units,
            entry_price=current_price,
            stop_loss=sl_price,
            take_profit=tp_price,
            mode=self._mode.value,
            signal_preview_id=decision.signal_preview_id,
            decision_id=decision.id,
        )
        
        # Phase 7: Store agent data for performance tracking
        if trade_id and agent_votes:
            self.record_agent_data_for_trade(
                trade_id=trade_id,
                agent_votes=agent_votes,
                agent_confidences=agent_confidences or {},
                final_signal=final_signal or "HOLD",
            )
        
        # Place order with funds check (with or without SL/TP)
        try:
            state, funds_details = self._oms.place_order_with_funds_check(
                request,
                current_price,
                log_callback=lambda event_type, severity, message, data: self._log_event(event_type, severity, message, data),
            )
            
            # Check if order was rejected due to insufficient funds
            if state.status == OrderStatus.REJECTED:
                logger.warning(f"Order rejected: {state.error_message}")
                return ExecutionResult(
                    executed=False,
                    mode=self._mode,
                    order_id=request.id,
                    symbol=decision.symbol,
                    side=side,
                    quantity=size_result.units,
                    reason=state.error_message or "insufficient_funds",
                )
            
            # Log if quantity was adjusted
            if funds_details.get("was_adjusted"):
                logger.info(
                    f"Order qty adjusted: {funds_details.get('original_qty')} -> {funds_details.get('adjusted_qty')} "
                    f"({funds_details.get('reason')})"
                )
            
            # P0-B: Update trade with ib_order_id and SUBMITTED status
            if trade_id and state.ib_order_id:
                self._update_trade_ib_order_id(trade_id, state.ib_order_id)
            
            return ExecutionResult(
                executed=True,
                mode=self._mode,
                order_id=request.id,
                ib_order_id=state.ib_order_id,
                symbol=decision.symbol,
                side=side,
                quantity=size_result.units,
                status=state.status,
                stop_loss_price=sl_price,
                take_profit_price=tp_price,
                entry_price=current_price,
                is_bracket=state.is_bracket,
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
    
    def _record_trade_pending(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        entry_price: Optional[float],
        stop_loss: Optional[float],
        take_profit: Optional[float],
        mode: str,
        signal_preview_id: Optional[UUID],
        decision_id: Optional[UUID],
        ib_order_id: Optional[int] = None,
    ) -> Optional[str]:
        """
        Record trade in trades_history with PENDING status.
        
        P0-B: Trade starts as PENDING, then gets updated to SUBMITTED/OPEN
        based on IB Gateway callbacks.
        """
        if not self.trades_history_repo:
            return None
        
        # Determine initial status based on mode
        # dry_run trades go straight to OPEN (no broker callback)
        initial_status = "OPEN" if mode == "dry_run" else "PENDING"
        
        try:
            trade_id = self.trades_history_repo.create_trade(
                symbol=symbol,
                side=side.value,
                quantity=quantity,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                mode=mode,
                signal_preview_id=str(signal_preview_id) if signal_preview_id else None,
                decision_id=str(decision_id) if decision_id else None,
                ib_order_id=ib_order_id,
                status=initial_status,
            )
            logger.info(f"Trade recorded in trades_history: {trade_id} status={initial_status}")
            return trade_id
        except Exception as e:
            logger.error(f"Failed to record trade in trades_history: {e}")
            return None
    
    def _update_trade_ib_order_id(self, trade_id: str, ib_order_id: int) -> None:
        """
        Update trade with IB order ID after order is placed.
        
        P0-B: This links the trade to the IB order for callback updates.
        """
        if not self.trades_history_repo:
            return
        
        try:
            self.trades_history_repo.update_status(
                trade_id=trade_id,
                status="SUBMITTED",
                ib_order_id=ib_order_id,
            )
            logger.info(f"Trade {trade_id} updated with ib_order_id={ib_order_id}")
        except Exception as e:
            logger.error(f"Failed to update trade ib_order_id: {e}")

    # Legacy method for backward compatibility
    def _record_trade_open(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        entry_price: Optional[float],
        stop_loss: Optional[float],
        take_profit: Optional[float],
        mode: str,
        signal_preview_id: Optional[UUID],
        decision_id: Optional[UUID],
    ) -> Optional[str]:
        """Record trade opening in trades_history (legacy - use _record_trade_pending)."""
        return self._record_trade_pending(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            mode=mode,
            signal_preview_id=signal_preview_id,
            decision_id=decision_id,
        )
    
    def shutdown(self) -> None:
        """Cleanup resources."""
        if self._oms:
            self._oms.shutdown()
        if self._connection_manager:
            self._connection_manager.disconnect()
        self._initialized = False
        logger.info("Execution service shutdown")
