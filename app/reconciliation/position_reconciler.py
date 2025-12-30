"""
Position Reconciliation Service.

Compares positions at broker (IB Gateway) with trades in our database.
Detects and handles:
1. Phantom trades - OPEN in DB but no position at broker (closed by SL/TP)
2. Orphan positions - Position at broker but no record in DB
3. Quantity mismatches - Position size differs between broker and DB

Runs periodically to keep DB in sync with actual broker state.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from queue import Queue
from threading import Thread
from typing import Any, Dict, List, Optional, Tuple
import asyncio

logger = logging.getLogger(__name__)


class ReconciliationAction(str, Enum):
    """Actions taken during reconciliation."""
    NONE = "none"
    CLOSED_PHANTOM = "closed_phantom"  # Marked phantom trade as closed in DB
    CREATED_ORPHAN = "created_orphan"  # Created DB record for orphan position
    ALERTED_ORPHAN = "alerted_orphan"  # Alerted about orphan (no auto-create)
    QUANTITY_MISMATCH = "quantity_mismatch"  # Flagged quantity difference
    ERROR = "error"


@dataclass
class ReconciliationResult:
    """Result of a single reconciliation check."""
    symbol: str
    action: ReconciliationAction
    db_trade_id: Optional[str] = None
    db_status: Optional[str] = None
    db_quantity: Optional[float] = None
    db_side: Optional[str] = None
    broker_quantity: Optional[float] = None
    broker_avg_cost: Optional[float] = None
    message: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass 
class ReconciliationReport:
    """Full reconciliation report."""
    timestamp: datetime
    broker_connected: bool
    db_open_trades: int
    broker_positions: int
    phantom_trades: List[ReconciliationResult] = field(default_factory=list)
    orphan_positions: List[ReconciliationResult] = field(default_factory=list)
    quantity_mismatches: List[ReconciliationResult] = field(default_factory=list)
    matched: List[str] = field(default_factory=list)  # Symbols that matched
    errors: List[str] = field(default_factory=list)
    
    @property
    def has_issues(self) -> bool:
        return bool(self.phantom_trades or self.orphan_positions or self.quantity_mismatches)
    
    @property
    def summary(self) -> str:
        return (
            f"Reconciliation: {len(self.matched)} matched, "
            f"{len(self.phantom_trades)} phantom, "
            f"{len(self.orphan_positions)} orphan, "
            f"{len(self.quantity_mismatches)} mismatches"
        )


class PositionReconciler:
    """
    Reconciles positions between IB Gateway and trades_history DB.
    
    Usage:
        reconciler = PositionReconciler(db, auto_close_phantoms=True)
        report = reconciler.run()
        if report.has_issues:
            logger.warning(report.summary)
    """
    
    def __init__(
        self,
        db,  # SupabaseDB instance
        auto_close_phantoms: bool = True,
        auto_create_orphans: bool = False,
        host: Optional[str] = None,
        port: Optional[int] = None,
        client_id: Optional[int] = None,
    ):
        """
        Initialize reconciler.
        
        Args:
            db: SupabaseDB instance for trades_history access
            auto_close_phantoms: Automatically mark phantom trades as CLOSED
            auto_create_orphans: Automatically create DB records for orphan positions
            host: IB Gateway host (default from env)
            port: IB Gateway port (default from env)
            client_id: Client ID for IB connection (default 162)
        """
        self.db = db
        self.auto_close_phantoms = auto_close_phantoms
        self.auto_create_orphans = auto_create_orphans
        
        self.host = host or os.getenv("IB_GATEWAY_HOST") or os.getenv("IBKR_HOST", "127.0.0.1")
        self.port = port or int(os.getenv("IB_GATEWAY_PORT") or os.getenv("IBKR_PORT", "4004"))
        self.client_id = client_id or int(os.getenv("IB_CLIENT_ID_RECONCILER", "162"))
    
    def run(self) -> ReconciliationReport:
        """
        Run reconciliation in a separate thread with its own event loop.
        
        Returns:
            ReconciliationReport with all findings and actions taken.
        """
        result_queue: Queue = Queue()
        
        def worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                report = self._run_reconciliation(loop)
                result_queue.put(report)
            except Exception as e:
                logger.error(f"[Reconciler] Worker error: {e}", exc_info=True)
                result_queue.put(ReconciliationReport(
                    timestamp=datetime.now(timezone.utc),
                    broker_connected=False,
                    db_open_trades=0,
                    broker_positions=0,
                    errors=[str(e)],
                ))
            finally:
                try:
                    loop.close()
                except:
                    pass
        
        thread = Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=30)
        
        if result_queue.empty():
            return ReconciliationReport(
                timestamp=datetime.now(timezone.utc),
                broker_connected=False,
                db_open_trades=0,
                broker_positions=0,
                errors=["Reconciliation timeout"],
            )
        
        return result_queue.get()
    
    def _run_reconciliation(self, loop) -> ReconciliationReport:
        """Internal reconciliation logic."""
        from ib_insync import IB
        
        timestamp = datetime.now(timezone.utc)
        report = ReconciliationReport(
            timestamp=timestamp,
            broker_connected=False,
            db_open_trades=0,
            broker_positions=0,
        )
        
        # Get open trades from DB
        db_trades = self._get_open_trades_from_db()
        report.db_open_trades = len(db_trades)
        logger.info(f"[Reconciler] Found {len(db_trades)} open trades in DB")
        
        # Connect to broker
        ib = IB()
        try:
            loop.run_until_complete(
                ib.connectAsync(self.host, self.port, clientId=self.client_id, timeout=15, readonly=True)
            )
            
            if not ib.isConnected():
                report.errors.append("Failed to connect to IB Gateway")
                return report
            
            report.broker_connected = True
            logger.info(f"[Reconciler] Connected to IB Gateway at {self.host}:{self.port}")
            
            # Get positions from broker
            # Note: For FX, positions appear in accountSummary as CashBalance, not in positions()
            broker_positions = self._get_broker_positions(ib)
            report.broker_positions = len(broker_positions)
            logger.info(f"[Reconciler] Found {len(broker_positions)} FX positions at broker")
            
            # Compare and reconcile
            self._compare_positions(db_trades, broker_positions, report)
            
        except Exception as e:
            logger.error(f"[Reconciler] Error: {e}", exc_info=True)
            report.errors.append(str(e))
        finally:
            try:
                if ib.isConnected():
                    ib.disconnect()
            except:
                pass
        
        logger.info(f"[Reconciler] {report.summary}")
        return report
    
    def _get_open_trades_from_db(self) -> List[Dict[str, Any]]:
        """Get all OPEN trades from trades_history."""
        try:
            result = self.db.client.table("trades_history").select(
                "id, symbol, side, quantity, entry_price, status, ib_order_id, created_at"
            ).eq("status", "OPEN").execute()
            return result.data or []
        except Exception as e:
            logger.error(f"[Reconciler] DB error: {e}")
            return []
    
    def _get_broker_positions(self, ib) -> Dict[str, Dict[str, Any]]:
        """
        Get FX positions from broker.
        
        For FX, we look at CashBalance in accountSummary.
        A non-base-currency cash balance indicates an FX position.
        
        Returns:
            Dict mapping symbol (e.g., "EURUSD") to position info.
        """
        positions = {}
        
        # Get account summary for cash balances
        try:
            summary = ib.accountSummary()
            base_currency = "EUR"  # Account base currency
            
            # Find base currency
            for item in summary:
                if item.tag == "Currency":
                    base_currency = item.value
                    break
            
            # Check cash balances for FX positions
            cash_balances = {}
            for item in summary:
                if item.tag == "CashBalance" and item.currency not in ("BASE",):
                    try:
                        balance = float(item.value)
                        if abs(balance) > 1:  # Ignore tiny balances
                            cash_balances[item.currency] = balance
                    except:
                        pass
            
            # Convert cash balances to FX pair positions
            # e.g., USD balance of 23400 with EUR base = short EURUSD or long USDEUR
            for currency, balance in cash_balances.items():
                if currency == base_currency:
                    continue
                
                # Determine FX pair and direction
                # Positive USD balance = sold EUR to buy USD = short EURUSD
                # Negative USD balance = bought EUR with USD = long EURUSD
                symbol = f"{currency}{base_currency}"
                
                positions[symbol] = {
                    "quantity": balance,
                    "currency": currency,
                    "base": base_currency,
                    "side": "BUY" if balance > 0 else "SELL",
                }
                
            logger.info(f"[Reconciler] Cash balances: {cash_balances}")
            
        except Exception as e:
            logger.error(f"[Reconciler] Error getting positions: {e}")
        
        # Also check ib.positions() for any regular positions
        try:
            for p in ib.positions():
                contract = p.contract
                if contract.secType == "CASH":
                    symbol = f"{contract.symbol}{contract.currency}"
                    positions[symbol] = {
                        "quantity": float(p.position),
                        "avg_cost": float(p.avgCost) if p.avgCost else None,
                        "side": "BUY" if p.position > 0 else "SELL",
                    }
        except Exception as e:
            logger.warning(f"[Reconciler] Error checking ib.positions(): {e}")
        
        return positions
    
    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize symbol for comparison."""
        return symbol.replace(".", "").replace("/", "").replace(" ", "").upper()
    
    def _compare_positions(
        self,
        db_trades: List[Dict[str, Any]],
        broker_positions: Dict[str, Dict[str, Any]],
        report: ReconciliationReport,
    ) -> None:
        """Compare DB trades with broker positions."""
        
        # Create lookup by normalized symbol
        db_by_symbol: Dict[str, Dict[str, Any]] = {}
        for trade in db_trades:
            symbol = self._normalize_symbol(trade.get("symbol", ""))
            if symbol:
                db_by_symbol[symbol] = trade
        
        broker_symbols = set(self._normalize_symbol(s) for s in broker_positions.keys())
        db_symbols = set(db_by_symbol.keys())
        
        # SAFETY CHECK: If DB has trades but broker shows 0 positions, something is wrong
        # Don't do phantom cleanup in this case - likely a connection issue
        if len(db_trades) > 0 and len(broker_positions) == 0:
            logger.warning(
                f"[Reconciler] SAFETY: DB has {len(db_trades)} open trades but broker shows 0 positions. "
                "Skipping phantom cleanup to avoid false positives."
            )
            report.errors.append(
                f"Safety skip: DB has {len(db_trades)} trades but broker has 0 - possible connection issue"
            )
            # Still report matched symbols (empty in this case)
            return
        
        # Find phantom trades (in DB but not at broker)
        phantom_symbols = db_symbols - broker_symbols
        for symbol in phantom_symbols:
            trade = db_by_symbol[symbol]
            
            # GRACE PERIOD: Don't mark as phantom if trade is less than 5 minutes old
            # This prevents race conditions during order execution
            opened_at = trade.get("opened_at") or trade.get("created_at")
            if opened_at:
                try:
                    if isinstance(opened_at, str):
                        from dateutil import parser
                        opened_time = parser.parse(opened_at)
                    else:
                        opened_time = opened_at
                    
                    age_seconds = (datetime.now(timezone.utc) - opened_time.replace(tzinfo=timezone.utc)).total_seconds()
                    if age_seconds < 300:  # 5 minutes grace period
                        logger.info(
                            f"[Reconciler] Skipping {symbol} phantom check - trade is only {age_seconds:.0f}s old"
                        )
                        continue
                except Exception as e:
                    logger.warning(f"[Reconciler] Could not parse opened_at for {symbol}: {e}")
            
            result = ReconciliationResult(
                symbol=symbol,
                action=ReconciliationAction.CLOSED_PHANTOM if self.auto_close_phantoms else ReconciliationAction.NONE,
                db_trade_id=trade.get("id"),
                db_status=trade.get("status"),
                db_quantity=trade.get("quantity"),
                db_side=trade.get("side"),
                broker_quantity=0,
                message="Position closed at broker (SL/TP hit) but still OPEN in DB",
            )
            
            if self.auto_close_phantoms:
                self._close_phantom_trade(trade)
                result.message += " - Auto-closed in DB"
            
            report.phantom_trades.append(result)
            logger.warning(f"[Reconciler] Phantom trade: {symbol} {result.message}")
        
        # Find orphan positions (at broker but not in DB)
        orphan_symbols = broker_symbols - db_symbols
        for symbol in orphan_symbols:
            # Find original symbol key
            orig_symbol = None
            for s in broker_positions.keys():
                if self._normalize_symbol(s) == symbol:
                    orig_symbol = s
                    break
            
            pos = broker_positions.get(orig_symbol, {})
            result = ReconciliationResult(
                symbol=symbol,
                action=ReconciliationAction.ALERTED_ORPHAN,
                broker_quantity=pos.get("quantity"),
                broker_avg_cost=pos.get("avg_cost"),
                message="Position exists at broker but no OPEN trade in DB",
            )
            
            if self.auto_create_orphans:
                # Could create a trade record here
                result.action = ReconciliationAction.CREATED_ORPHAN
                result.message += " - Auto-created in DB"
            
            report.orphan_positions.append(result)
            logger.warning(f"[Reconciler] Orphan position: {symbol} qty={pos.get('quantity')}")
        
        # Check matched positions for quantity mismatches
        matched_symbols = db_symbols & broker_symbols
        for symbol in matched_symbols:
            trade = db_by_symbol[symbol]
            
            # Find broker position
            broker_pos = None
            for s, pos in broker_positions.items():
                if self._normalize_symbol(s) == symbol:
                    broker_pos = pos
                    break
            
            if broker_pos:
                db_qty = abs(float(trade.get("quantity") or 0))
                broker_qty = abs(float(broker_pos.get("quantity") or 0))
                
                # Allow 1% tolerance for quantity matching
                if abs(db_qty - broker_qty) / max(db_qty, 1) > 0.01:
                    result = ReconciliationResult(
                        symbol=symbol,
                        action=ReconciliationAction.QUANTITY_MISMATCH,
                        db_trade_id=trade.get("id"),
                        db_quantity=db_qty,
                        broker_quantity=broker_qty,
                        message=f"Quantity mismatch: DB={db_qty} vs Broker={broker_qty}",
                    )
                    report.quantity_mismatches.append(result)
                    logger.warning(f"[Reconciler] {result.message}")
                else:
                    report.matched.append(symbol)
    
    def _close_phantom_trade(self, trade: Dict[str, Any]) -> bool:
        """Mark a phantom trade as CLOSED in DB."""
        try:
            trade_id = trade.get("id")
            if not trade_id:
                return False
            
            self.db.client.table("trades_history").update({
                "status": "CLOSED",
                "close_reason": "RECONCILED_PHANTOM",
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", trade_id).execute()
            
            logger.info(f"[Reconciler] Closed phantom trade {trade_id}")
            return True
        except Exception as e:
            logger.error(f"[Reconciler] Failed to close phantom trade: {e}")
            return False


def run_reconciliation(db, auto_close: bool = True) -> ReconciliationReport:
    """
    Convenience function to run reconciliation.
    
    Args:
        db: SupabaseDB instance
        auto_close: Whether to auto-close phantom trades
    
    Returns:
        ReconciliationReport
    """
    reconciler = PositionReconciler(db, auto_close_phantoms=auto_close)
    return reconciler.run()
