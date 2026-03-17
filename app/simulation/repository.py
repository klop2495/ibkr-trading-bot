"""
SimulationEngine Repository - Database operations for sim_* tables
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.simulation.models import (
    SimTrade, SimFill, SimEquityPoint, SimEvent,
    SimTradeStatus, SimCloseReason, SimSide, SimEventType
)
from app.storage.db import SupabaseDB


class SimTradesRepo:
    """Repository for sim_trades table"""
    table = "sim_trades"
    
    def __init__(self, db: SupabaseDB):
        self.db = db
    
    def insert(self, trade: SimTrade) -> str:
        """Insert a new simulated trade"""
        payload = {
            "id": str(trade.id),
            "decision_id": trade.decision_id,
            "signal_preview_id": trade.signal_preview_id,
            "verdict_id": trade.verdict_id,
            "symbol": trade.symbol,
            "side": trade.side.value if isinstance(trade.side, SimSide) else trade.side,
            "quantity": trade.quantity,
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "stop_loss": trade.stop_loss,
            "take_profit": trade.take_profit,
            "current_price": trade.current_price,
            "pnl": trade.pnl,
            "pnl_pips": trade.pnl_pips,
            "unrealized_pnl": trade.unrealized_pnl,
            "status": trade.status.value if isinstance(trade.status, SimTradeStatus) else trade.status,
            "close_reason": trade.close_reason.value if trade.close_reason else None,
            "block_reason": trade.block_reason.value if trade.block_reason else None,
            "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
            "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
            "equity_at_entry": trade.equity_at_entry,
            "risk_cash": trade.risk_cash,
            "sl_pips": trade.sl_pips,
            "tp_pips": trade.tp_pips,
            "notional": trade.notional,
            "risk_modifier": trade.risk_modifier,
        }
        
        self.db.client.table(self.table).insert(payload).execute()
        return str(trade.id)
    
    def update_status(
        self,
        trade_id: str,
        status: SimTradeStatus,
        exit_price: Optional[float] = None,
        pnl: Optional[float] = None,
        pnl_pips: Optional[float] = None,
        close_reason: Optional[SimCloseReason] = None,
        closed_at: Optional[datetime] = None,
    ) -> bool:
        """Update trade status (for closing)"""
        payload: Dict[str, Any] = {
            "status": status.value,
        }
        
        if exit_price is not None:
            payload["exit_price"] = exit_price
        if pnl is not None:
            payload["pnl"] = pnl
        if pnl_pips is not None:
            payload["pnl_pips"] = pnl_pips
        if close_reason is not None:
            payload["close_reason"] = close_reason.value
        if closed_at is not None:
            payload["closed_at"] = closed_at.isoformat()
        
        res = self.db.client.table(self.table).update(payload).eq("id", trade_id).execute()
        return len(res.data) > 0 if res.data else False
    
    def update_current_price(self, trade_id: str, current_price: float, unrealized_pnl: float) -> bool:
        """Update current price and unrealized P&L"""
        payload = {
            "current_price": current_price,
            "unrealized_pnl": unrealized_pnl,
        }
        res = self.db.client.table(self.table).update(payload).eq("id", trade_id).execute()
        return len(res.data) > 0 if res.data else False
    
    def get_open_trades(self) -> List[Dict[str, Any]]:
        """Get all open simulated trades"""
        res = (
            self.db.client.table(self.table)
            .select("*")
            .eq("status", SimTradeStatus.OPEN.value)
            .execute()
        )
        return res.data or []
    
    def get_by_decision_id(self, decision_id: str) -> Optional[Dict[str, Any]]:
        """Check if trade already exists for this decision"""
        res = (
            self.db.client.table(self.table)
            .select("id, status")
            .eq("decision_id", decision_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    
    def get_open_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get open trade for symbol (for 1-position-per-symbol rule)"""
        res = (
            self.db.client.table(self.table)
            .select("*")
            .eq("symbol", symbol)
            .eq("status", SimTradeStatus.OPEN.value)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    
    def count_open_trades(self) -> int:
        """Count total open positions"""
        res = (
            self.db.client.table(self.table)
            .select("id", count="exact")
            .eq("status", SimTradeStatus.OPEN.value)
            .execute()
        )
        return res.count or 0


class SimFillsRepo:
    """Repository for sim_fills table"""
    table = "sim_fills"
    
    def __init__(self, db: SupabaseDB):
        self.db = db
    
    def insert(self, fill: SimFill) -> str:
        """Insert a fill record"""
        payload = {
            "id": str(fill.id),
            "trade_id": str(fill.trade_id),
            "fill_type": fill.fill_type,
            "symbol": fill.symbol,
            "side": fill.side.value if isinstance(fill.side, SimSide) else fill.side,
            "quantity": fill.quantity,
            "price": fill.price,
            "bid": fill.bid,
            "ask": fill.ask,
            "spread": fill.spread,
            "timestamp": fill.timestamp.isoformat(),
        }
        self.db.client.table(self.table).insert(payload).execute()
        return str(fill.id)


class SimEquityCurveRepo:
    """Repository for sim_equity_curve table"""
    table = "sim_equity_curve"
    
    def __init__(self, db: SupabaseDB):
        self.db = db
    
    def insert(self, point: SimEquityPoint) -> str:
        """Insert equity curve point"""
        payload = {
            "id": str(point.id),
            "timestamp": point.timestamp.isoformat(),
            "baseline_equity": point.baseline_equity,
            "sim_equity": point.sim_equity,
            "closed_pnl": point.closed_pnl,
            "open_pnl": point.open_pnl,
            "total_exposure": point.total_exposure,
            "num_open_positions": point.num_open_positions,
            "equity_source": point.equity_source,
        }
        self.db.client.table(self.table).insert(payload).execute()
        return str(point.id)
    
    def get_latest(self) -> Optional[Dict[str, Any]]:
        """Get latest equity point"""
        res = (
            self.db.client.table(self.table)
            .select("*")
            .order("timestamp", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None


class SimEventsRepo:
    """Repository for sim_events table"""
    table = "sim_events"
    
    def __init__(self, db: SupabaseDB):
        self.db = db
    
    def insert(self, event: SimEvent) -> str:
        """Insert simulation event"""
        payload = {
            "id": str(event.id),
            "timestamp": event.timestamp.isoformat(),
            "event_type": event.event_type.value if isinstance(event.event_type, SimEventType) else event.event_type,
            "severity": event.severity,
            "symbol": event.symbol,
            "trade_id": event.trade_id,
            "decision_id": event.decision_id,
            "message": event.message,
            "data": event.data,
        }
        self.db.client.table(self.table).insert(payload).execute()
        return str(event.id)
    
    def log(
        self,
        event_type: SimEventType,
        message: str,
        severity: str = "INFO",
        symbol: Optional[str] = None,
        trade_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        data: Optional[dict] = None,
    ) -> str:
        """Convenience method to log an event"""
        event = SimEvent(
            event_type=event_type,
            severity=severity,
            symbol=symbol,
            trade_id=trade_id,
            decision_id=decision_id,
            message=message,
            data=data or {},
        )
        return self.insert(event)
