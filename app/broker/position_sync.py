"""
Position Sync Service.

Synchronizes positions between IB Gateway (source of truth) and our database.
Runs:
- On bot startup
- On IB Gateway reconnect  
- Periodically (configurable interval)

This ensures our DB always reflects actual broker state, even if callbacks
were missed due to disconnections or restarts.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class SyncAction(str, Enum):
    """Actions taken during sync."""
    NONE = "none"
    CLOSED_PHANTOM = "closed_phantom"      # DB OPEN -> CLOSED (position gone from broker)
    UPDATED_PRICE = "updated_price"        # Updated current price
    CREATED_RECORD = "created_record"      # Created DB record for broker position
    QUANTITY_ADJUSTED = "quantity_adjusted" # Adjusted quantity to match broker


@dataclass
class SyncResult:
    """Result of position sync."""
    symbol: str
    action: SyncAction
    db_trade_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass
class SyncReport:
    """Full sync report."""
    timestamp: datetime
    success: bool
    broker_connected: bool
    broker_positions_count: int
    db_open_trades_count: int
    actions_taken: List[SyncResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    duration_ms: int = 0
    
    @property
    def summary(self) -> str:
        actions = len([a for a in self.actions_taken if a.action != SyncAction.NONE])
        return (
            f"PositionSync: {actions} actions, "
            f"broker={self.broker_positions_count} positions, "
            f"db={self.db_open_trades_count} open trades"
        )


# Standard FX pairs we trade - only these should be considered for sync
TRADABLE_FX_PAIRS = {
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY", "NZDJPY",
    "EURAUD", "GBPAUD",
}


class PositionSyncService:
    """
    Synchronizes positions between broker and database.
    
    Key principles:
    - Broker is ALWAYS the source of truth
    - DB is for history/analytics, not current state
    - On any discrepancy, trust broker
    
    Usage:
        sync_service = PositionSyncService(db, ib)
        report = sync_service.sync()
        if report.actions_taken:
            logger.info(f"Sync completed: {report.summary}")
    """
    
    def __init__(
        self,
        db,  # SupabaseDB instance
        ib=None,  # ib_insync IB instance (optional, can be set later)
        auto_close_phantoms: bool = True,
        log_callback: Optional[callable] = None,
        tradable_pairs: Optional[Set[str]] = None,
    ):
        """
        Initialize sync service.
        
        Args:
            db: SupabaseDB instance
            ib: ib_insync IB instance (can be None, set via set_ib())
            auto_close_phantoms: Automatically close phantom trades in DB
            log_callback: Optional callback for logging events
            tradable_pairs: Set of FX pairs to sync (default: TRADABLE_FX_PAIRS)
        """
        self.db = db
        self.ib = ib
        self.auto_close_phantoms = auto_close_phantoms
        self.log_callback = log_callback
        self.tradable_pairs = tradable_pairs or TRADABLE_FX_PAIRS
        self._last_sync: Optional[datetime] = None
    
    def set_ib(self, ib) -> None:
        """Set or update IB connection."""
        self.ib = ib
    
    def sync(self) -> SyncReport:
        """
        Perform full position sync.
        
        Returns:
            SyncReport with all actions taken.
        """
        start_time = datetime.now(timezone.utc)
        report = SyncReport(
            timestamp=start_time,
            success=False,
            broker_connected=False,
            broker_positions_count=0,
            db_open_trades_count=0,
        )
        
        try:
            # Check IB connection
            if self.ib is None or not self.ib.isConnected():
                report.errors.append("IB Gateway not connected")
                self._log("POSITION_SYNC_SKIPPED", "warn", "IB Gateway not connected")
                return report
            
            report.broker_connected = True
            
            # Get positions from broker
            broker_positions = self._get_broker_positions()
            report.broker_positions_count = len(broker_positions)
            
            # Get open trades from DB
            db_trades = self._get_db_open_trades()
            report.db_open_trades_count = len(db_trades)
            
            logger.info(
                f"[PositionSync] Broker: {len(broker_positions)} positions, "
                f"DB: {len(db_trades)} open trades"
            )
            
            # Sync logic
            self._sync_positions(broker_positions, db_trades, report)
            
            report.success = True
            self._last_sync = datetime.now(timezone.utc)
            
        except Exception as e:
            logger.error(f"[PositionSync] Error: {e}", exc_info=True)
            report.errors.append(str(e))
        
        # Calculate duration
        report.duration_ms = int(
            (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        )
        
        self._log(
            "POSITION_SYNC_COMPLETED",
            "info" if report.success else "error",
            report.summary,
            {
                "success": report.success,
                "actions_count": len(report.actions_taken),
                "duration_ms": report.duration_ms,
            }
        )
        
        return report
    
    def _get_broker_positions(self) -> Dict[str, Dict[str, Any]]:
        """
        Get current FX positions from broker.
        
        For FX, positions appear as CashBalance in accountSummary.
        Only returns positions for tradable FX pairs.
        
        Returns:
            Dict mapping normalized symbol to position info.
        """
        positions = {}
        
        try:
            # Also check ib.positions() for explicit FX positions
            # This is more reliable than CashBalance for actual traded positions
            for pos in self.ib.positions():
                contract = pos.contract
                if hasattr(contract, 'symbol') and hasattr(contract, 'currency'):
                    symbol = f"{contract.symbol}{contract.currency}"
                    normalized = self._normalize_symbol(symbol)
                    
                    # Only include tradable pairs
                    if normalized in self.tradable_pairs and abs(pos.position) > 0:
                        positions[normalized] = {
                            "quantity": float(pos.position),
                            "avg_cost": float(pos.avgCost) if pos.avgCost else None,
                            "side": "BUY" if pos.position > 0 else "SELL",
                            "raw_symbol": symbol,
                        }
                        logger.debug(f"[PositionSync] Found position: {normalized} qty={pos.position}")
                        
        except Exception as e:
            logger.error(f"[PositionSync] Error getting broker positions: {e}")
        
        return positions
    
    def _get_db_open_trades(self) -> List[Dict[str, Any]]:
        """Get all OPEN trades from database."""
        try:
            result = self.db.client.table("trades_history").select(
                "id, symbol, side, quantity, entry_price, status, ib_order_id, created_at"
            ).in_("status", ["OPEN", "SUBMITTED", "PENDING"]).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"[PositionSync] Error getting DB trades: {e}")
            return []
    
    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize symbol for comparison."""
        return symbol.replace(".", "").replace("/", "").replace(" ", "").upper()
    
    def _sync_positions(
        self,
        broker_positions: Dict[str, Dict[str, Any]],
        db_trades: List[Dict[str, Any]],
        report: SyncReport,
    ) -> None:
        """
        Core sync logic.
        
        Rules:
        1. If trade is OPEN in DB but no position at broker -> close as phantom
        2. If position at broker but no OPEN trade in DB -> log warning (manual intervention needed)
        3. If both exist but quantities differ -> log warning
        """
        
        # Build DB lookup by normalized symbol
        db_by_symbol: Dict[str, Dict[str, Any]] = {}
        for trade in db_trades:
            symbol = self._normalize_symbol(trade.get("symbol", ""))
            if symbol:
                # If multiple trades for same symbol, keep the most recent
                existing = db_by_symbol.get(symbol)
                if not existing or trade.get("created_at", "") > existing.get("created_at", ""):
                    db_by_symbol[symbol] = trade
        
        broker_symbols = set(broker_positions.keys())
        db_symbols = set(db_by_symbol.keys())
        
        # 1. Find phantom trades (in DB but not at broker)
        phantom_symbols = db_symbols - broker_symbols
        for symbol in phantom_symbols:
            trade = db_by_symbol[symbol]
            trade_id = trade.get("id")
            
            logger.warning(
                f"[PositionSync] PHANTOM: {symbol} is OPEN in DB but not at broker. "
                f"Trade ID: {trade_id}"
            )
            
            if self.auto_close_phantoms and trade_id:
                success = self._close_phantom_trade(trade)
                action = SyncAction.CLOSED_PHANTOM if success else SyncAction.NONE
            else:
                action = SyncAction.NONE
            
            report.actions_taken.append(SyncResult(
                symbol=symbol,
                action=action,
                db_trade_id=trade_id,
                message=f"Position closed at broker, DB trade marked as closed" if action == SyncAction.CLOSED_PHANTOM else "Phantom detected, manual review needed",
                details={
                    "db_side": trade.get("side"),
                    "db_quantity": trade.get("quantity"),
                    "db_entry": trade.get("entry_price"),
                }
            ))
        
        # 2. Find orphan positions (at broker but not in DB)
        orphan_symbols = broker_symbols - db_symbols
        for symbol in orphan_symbols:
            pos = broker_positions[symbol]
            
            logger.warning(
                f"[PositionSync] ORPHAN: {symbol} exists at broker but no OPEN trade in DB. "
                f"Qty={pos.get('quantity')}"
            )
            
            report.actions_taken.append(SyncResult(
                symbol=symbol,
                action=SyncAction.NONE,  # Don't auto-create, needs manual review
                message="Position at broker without DB record - manual review needed",
                details={
                    "broker_quantity": pos.get("quantity"),
                    "broker_side": pos.get("side"),
                }
            ))
        
        # 3. Check matched positions for quantity mismatches
        matched_symbols = db_symbols & broker_symbols
        for symbol in matched_symbols:
            trade = db_by_symbol[symbol]
            pos = broker_positions[symbol]
            
            db_qty = abs(float(trade.get("quantity") or 0))
            broker_qty = abs(float(pos.get("quantity") or 0))
            
            # Allow small tolerance (1%)
            if db_qty > 0 and abs(db_qty - broker_qty) / db_qty > 0.01:
                logger.warning(
                    f"[PositionSync] MISMATCH: {symbol} quantity differs. "
                    f"DB={db_qty}, Broker={broker_qty}"
                )
                
                report.actions_taken.append(SyncResult(
                    symbol=symbol,
                    action=SyncAction.NONE,
                    db_trade_id=trade.get("id"),
                    message=f"Quantity mismatch: DB={db_qty} vs Broker={broker_qty}",
                    details={
                        "db_quantity": db_qty,
                        "broker_quantity": broker_qty,
                    }
                ))
            else:
                # Position matches - no action needed
                logger.debug(f"[PositionSync] OK: {symbol} matches")
    
    def _close_phantom_trade(self, trade: Dict[str, Any]) -> bool:
        """
        Mark a phantom trade as CLOSED in database.
        
        Since position no longer exists at broker, we don't know the exact
        exit price or time. We mark it as RECONCILED.
        """
        try:
            trade_id = trade.get("id")
            if not trade_id:
                return False
            
            # Try to get last known price for the symbol
            # For now, we leave exit_price as NULL - can be filled manually later
            
            self.db.client.table("trades_history").update({
                "status": "CLOSED",
                "close_reason": "SYNC_PHANTOM",
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", trade_id).execute()
            
            logger.info(f"[PositionSync] Closed phantom trade {trade_id}")
            
            self._log(
                "PHANTOM_TRADE_CLOSED",
                "warn",
                f"Phantom trade {trade_id} ({trade.get('symbol')}) closed by sync",
                {
                    "trade_id": trade_id,
                    "symbol": trade.get("symbol"),
                    "side": trade.get("side"),
                    "quantity": trade.get("quantity"),
                }
            )
            
            return True
            
        except Exception as e:
            logger.error(f"[PositionSync] Failed to close phantom trade: {e}")
            return False
    
    def _log(
        self,
        event_type: str,
        severity: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log event via callback if provided."""
        if self.log_callback:
            try:
                self.log_callback(event_type, severity, message, data or {})
            except Exception:
                pass
    
    @property
    def last_sync_time(self) -> Optional[datetime]:
        """Get timestamp of last successful sync."""
        return self._last_sync


def create_position_sync_service(db, ib=None) -> PositionSyncService:
    """
    Factory function to create PositionSyncService.
    
    Args:
        db: SupabaseDB instance
        ib: ib_insync IB instance (optional)
    
    Returns:
        Configured PositionSyncService
    """
    return PositionSyncService(
        db=db,
        ib=ib,
        auto_close_phantoms=True,
    )
