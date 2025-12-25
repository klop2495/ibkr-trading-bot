"""
SimulationEngine - Main simulation engine for shadow trading

This engine:
1. Reads verdicts with trade_allowed=True from Supabase
2. Calculates position size based on real IBKR equity
3. Opens simulated trades
4. Monitors prices and closes on SL/TP
5. Tracks equity curve and P&L

IMPORTANT: This engine does NOT:
- Execute real orders
- Write to trading bot tables (trades_history, execution_reports, control_*)
- Modify any trading bot state
"""
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from app.simulation.models import (
    SimTrade, SimFill, SimEquityPoint, SimEvent,
    SimTradeStatus, SimCloseReason, SimSide, SimEventType, BlockReason,
    FOREX_PIP_SIZES, DEFAULT_PIP_SIZE
)
from app.simulation.repository import (
    SimTradesRepo, SimFillsRepo, SimEquityCurveRepo, SimEventsRepo
)
from app.simulation.price_feed import PriceFeed, PriceQuote
from app.simulation.position_sizer import PositionSizer, MinSizePolicy
from app.simulation.pnl_calculator import PnLCalculator
from app.storage.db import SupabaseDB


class SimulationEngine:
    """
    Shadow Trading Simulation Engine v1
    
    Runs parallel to trading bot, simulates trades on real market data
    without executing actual orders.
    """
    
    def __init__(
        self,
        db: SupabaseDB,
        ib_client: Optional[Any] = None,
        # Risk parameters
        risk_per_trade: float = 0.01,  # 1% risk per trade
        max_open_positions: int = 5,
        max_effective_leverage: float = 10.0,
        # Sizing
        min_size_policy: str = MinSizePolicy.ROUND_UP,
        # Timing
        price_check_interval: int = 10,  # Check SL/TP every N seconds
        equity_update_interval: int = 60,  # Update equity every N seconds
        verdict_scan_interval: int = 30,  # Scan for new verdicts every N seconds
        # Lookback
        verdict_lookback_hours: int = 24,  # Only consider verdicts from last N hours
    ):
        self.db = db
        self.ib = ib_client
        
        # Risk parameters
        self.risk_per_trade = risk_per_trade
        self.max_open_positions = max_open_positions
        self.max_effective_leverage = max_effective_leverage
        
        # Intervals
        self.price_check_interval = price_check_interval
        self.equity_update_interval = equity_update_interval
        self.verdict_scan_interval = verdict_scan_interval
        self.verdict_lookback_hours = verdict_lookback_hours
        
        # Components
        self.price_feed = PriceFeed(
            ib_client=ib_client,
            db_client=db.client if db else None,
        )
        self.position_sizer = PositionSizer(min_size_policy=min_size_policy)
        self.pnl_calc = PnLCalculator()
        
        # Repositories
        self.trades_repo = SimTradesRepo(db)
        self.fills_repo = SimFillsRepo(db)
        self.equity_repo = SimEquityCurveRepo(db)
        self.events_repo = SimEventsRepo(db)
        
        # State
        self.baseline_equity: float = 0.0
        self.sim_equity: float = 0.0
        self.closed_pnl: float = 0.0
        self.open_positions: Dict[str, SimTrade] = {}  # trade_id -> trade
        
        # Tracking
        self.last_price_check: float = 0.0
        self.last_equity_update: float = 0.0
        self.last_verdict_scan: float = 0.0
        self.processed_verdict_ids: set = set()
        
        # Running flag
        self.running: bool = False
    
    def start(self):
        """Start the simulation engine"""
        self.running = True
        
        # Log start
        self.events_repo.log(
            SimEventType.ENGINE_START,
            "SimulationEngine started",
            data={"risk_per_trade": self.risk_per_trade, "max_positions": self.max_open_positions}
        )
        print("SimulationEngine: Started")
        
        # Initialize equity
        self._update_baseline_equity()
        
        # Load existing open positions
        self._load_open_positions()
        
        print(f"SimulationEngine: Baseline equity={self.baseline_equity}, Open positions={len(self.open_positions)}")
    
    def stop(self):
        """Stop the simulation engine"""
        self.running = False
        self.events_repo.log(SimEventType.ENGINE_STOP, "SimulationEngine stopped")
        print("SimulationEngine: Stopped")
    
    def tick(self):
        """
        Main tick function - call this in the main loop.
        Performs time-based checks and actions.
        """
        now = time.time()
        
        # 1. Check for new verdicts to process
        if now - self.last_verdict_scan >= self.verdict_scan_interval:
            self._scan_verdicts()
            self.last_verdict_scan = now
        
        # 2. Check SL/TP for open positions
        if now - self.last_price_check >= self.price_check_interval:
            self._check_open_positions()
            self.last_price_check = now
        
        # 3. Update equity baseline
        if now - self.last_equity_update >= self.equity_update_interval:
            self._update_baseline_equity()
            self._record_equity_point()
            self.last_equity_update = now
    
    # ==================== EQUITY ====================
    
    def _update_baseline_equity(self):
        """Fetch baseline equity from IBKR"""
        equity = self._fetch_ibkr_equity()
        
        if equity is not None and equity > 0:
            self.baseline_equity = equity
            self.sim_equity = self.baseline_equity + self.closed_pnl + self._calculate_open_pnl()
            
            self.events_repo.log(
                SimEventType.EQUITY_BASELINE,
                f"Equity baseline updated: {equity}",
                data={"baseline": equity, "sim_equity": self.sim_equity, "source": "IBKR"}
            )
            print(f"SimulationEngine: Equity baseline={equity:.2f}, Sim equity={self.sim_equity:.2f}")
        else:
            # Fallback to env default
            default_equity = float(os.getenv("DEFAULT_EQUITY", "10000"))
            if self.baseline_equity == 0:
                self.baseline_equity = default_equity
                self.sim_equity = default_equity
                print(f"SimulationEngine: Using default equity={default_equity}")
    
    def _fetch_ibkr_equity(self) -> Optional[float]:
        """Fetch NetLiquidation from IBKR"""
        if self.ib is None or not self.ib.isConnected():
            return None
        
        try:
            account_values = self.ib.accountSummary()
            for av in account_values:
                if av.tag == 'NetLiquidation':
                    return float(av.value)
            return None
        except Exception as e:
            print(f"SimulationEngine: Failed to fetch IBKR equity: {e}")
            return None
    
    def _calculate_open_pnl(self) -> float:
        """Calculate total unrealized P&L from open positions"""
        total_pnl = 0.0
        
        for trade in self.open_positions.values():
            if trade.unrealized_pnl is not None:
                total_pnl += trade.unrealized_pnl
        
        return total_pnl
    
    def _record_equity_point(self):
        """Record a point on the equity curve"""
        open_pnl = self._calculate_open_pnl()
        total_exposure = sum(
            t.notional or 0 for t in self.open_positions.values()
        )
        
        point = SimEquityPoint(
            baseline_equity=self.baseline_equity,
            sim_equity=self.sim_equity,
            closed_pnl=self.closed_pnl,
            open_pnl=open_pnl,
            total_exposure=total_exposure,
            num_open_positions=len(self.open_positions),
            equity_source="IBKR" if self.ib else "DEFAULT",
        )
        
        self.equity_repo.insert(point)
    
    # ==================== VERDICT SCANNING ====================
    
    def _scan_verdicts(self):
        """Scan for new tradeable verdicts"""
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=self.verdict_lookback_hours)).isoformat()
            
            # Get verdicts with trade_allowed=True
            res = (
                self.db.client.table("risk_verdicts")
                .select("id, decision_id, signal_preview_id, trade_allowed, risk_modifier, flags, created_at")
                .eq("trade_allowed", True)
                .gte("created_at", cutoff)
                .order("created_at", desc=True)
                .limit(50)
                .execute()
            )
            
            verdicts = res.data or []
            
            for verdict in verdicts:
                verdict_id = verdict.get("id")
                
                # Skip if already processed
                if verdict_id in self.processed_verdict_ids:
                    continue
                
                # Check if trade already exists for this decision
                decision_id = verdict.get("decision_id")
                if decision_id:
                    existing = self.trades_repo.get_by_decision_id(decision_id)
                    if existing:
                        self.processed_verdict_ids.add(verdict_id)
                        continue
                
                # Process this verdict
                self._process_verdict(verdict)
                self.processed_verdict_ids.add(verdict_id)
        
        except Exception as e:
            print(f"SimulationEngine: Error scanning verdicts: {e}")
            self.events_repo.log(
                SimEventType.ERROR,
                f"Verdict scan error: {e}",
                severity="ERROR"
            )
    
    def _process_verdict(self, verdict: Dict[str, Any]):
        """Process a single verdict - check admission rules and open trade if valid"""
        decision_id = verdict.get("decision_id")
        signal_preview_id = verdict.get("signal_preview_id")
        risk_modifier = verdict.get("risk_modifier", 1.0)
        
        # Fetch decision details
        decision = self._fetch_decision(decision_id)
        if not decision:
            return
        
        symbol = decision.get("symbol")
        if not symbol:
            return
        
        # Fetch signal preview for SL/TP and direction
        preview = self._fetch_signal_preview(signal_preview_id)
        if not preview:
            return
        
        # Check entry_triggered
        if not preview.get("entry_triggered", False):
            self._block_trade(verdict, BlockReason.ENTRY_NOT_TRIGGERED, symbol)
            return
        
        # Get direction
        direction = preview.get("direction")
        if direction not in ("LONG", "SHORT", "BUY", "SELL"):
            return
        
        side = SimSide.BUY if direction in ("LONG", "BUY") else SimSide.SELL
        
        # Get SL/TP pips
        sl_pips = preview.get("sl_distance_pips") or 20.0
        tp_pips = preview.get("tp_distance_pips") or 40.0
        
        # Admission checks
        block_reason = self._check_admission(symbol)
        if block_reason:
            self._block_trade(verdict, block_reason, symbol)
            return
        
        # Get current price
        quote = self.price_feed.get_quote(symbol)
        if not quote:
            self._block_trade(verdict, BlockReason.NO_PRICE_DATA, symbol)
            return
        
        # Calculate position size
        qty, risk_cash, size_block = self.position_sizer.calculate_size(
            symbol=symbol,
            equity=self.sim_equity,
            risk_per_trade=self.risk_per_trade,
            sl_pips=sl_pips,
            risk_modifier=risk_modifier,
            current_price=quote.mid,
        )
        
        if size_block:
            self._block_trade(verdict, size_block, symbol)
            return
        
        # Calculate entry price
        entry_price = quote.ask if side == SimSide.BUY else quote.bid
        
        # Calculate SL/TP prices
        sl_price, tp_price = self.pnl_calc.calculate_sl_tp_prices(
            symbol, side, entry_price, sl_pips, tp_pips
        )
        
        # Calculate notional for leverage check
        notional = self.position_sizer.calculate_notional(symbol, qty, entry_price)
        total_exposure = sum(t.notional or 0 for t in self.open_positions.values()) + notional
        
        allowed, current_leverage = self.position_sizer.check_leverage(
            total_exposure, self.sim_equity, self.max_effective_leverage
        )
        if not allowed:
            self._block_trade(verdict, BlockReason.MAX_LEVERAGE_EXCEEDED, symbol, 
                            data={"leverage": current_leverage, "max": self.max_effective_leverage})
            return
        
        # Open the trade
        self._open_trade(
            verdict=verdict,
            decision=decision,
            preview=preview,
            symbol=symbol,
            side=side,
            quantity=qty,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            risk_cash=risk_cash,
            notional=notional,
            risk_modifier=risk_modifier,
            quote=quote,
        )
    
    def _check_admission(self, symbol: str) -> Optional[BlockReason]:
        """Check if a new trade is allowed"""
        # Check max positions
        if len(self.open_positions) >= self.max_open_positions:
            return BlockReason.MAX_POSITIONS_REACHED
        
        # Check 1 position per symbol
        existing = self.trades_repo.get_open_by_symbol(symbol)
        if existing:
            return BlockReason.SYMBOL_ALREADY_OPEN
        
        return None
    
    def _block_trade(
        self, 
        verdict: Dict, 
        reason: BlockReason, 
        symbol: str,
        data: Optional[dict] = None
    ):
        """Record a blocked trade"""
        trade = SimTrade(
            decision_id=verdict.get("decision_id"),
            signal_preview_id=verdict.get("signal_preview_id"),
            verdict_id=verdict.get("id"),
            symbol=symbol,
            status=SimTradeStatus.BLOCKED,
            block_reason=reason,
            equity_at_entry=self.sim_equity,
        )
        self.trades_repo.insert(trade)
        
        self.events_repo.log(
            SimEventType.TRADE_BLOCKED,
            f"Trade blocked: {reason.value}",
            symbol=symbol,
            decision_id=verdict.get("decision_id"),
            data={"reason": reason.value, **(data or {})}
        )
        print(f"SimulationEngine: Trade BLOCKED symbol={symbol} reason={reason.value}")
    
    def _open_trade(
        self,
        verdict: Dict,
        decision: Dict,
        preview: Dict,
        symbol: str,
        side: SimSide,
        quantity: float,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        sl_pips: float,
        tp_pips: float,
        risk_cash: float,
        notional: float,
        risk_modifier: float,
        quote: PriceQuote,
    ):
        """Open a simulated trade"""
        now = datetime.now(timezone.utc)
        trade_id = uuid4()
        
        trade = SimTrade(
            id=trade_id,
            decision_id=verdict.get("decision_id"),
            signal_preview_id=verdict.get("signal_preview_id"),
            verdict_id=verdict.get("id"),
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=sl_price,
            take_profit=tp_price,
            current_price=quote.mid,
            status=SimTradeStatus.OPEN,
            opened_at=now,
            equity_at_entry=self.sim_equity,
            risk_cash=risk_cash,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            notional=notional,
            risk_modifier=risk_modifier,
            unrealized_pnl=0.0,
        )
        
        # Save to DB
        self.trades_repo.insert(trade)
        
        # Record fill
        fill = SimFill(
            trade_id=trade_id,
            fill_type="ENTRY",
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=entry_price,
            bid=quote.bid,
            ask=quote.ask,
            spread=quote.spread,
        )
        self.fills_repo.insert(fill)
        
        # Add to open positions
        self.open_positions[str(trade_id)] = trade
        
        # Log event
        self.events_repo.log(
            SimEventType.TRADE_OPEN,
            f"Trade opened: {side.value} {symbol} qty={quantity} @ {entry_price}",
            symbol=symbol,
            trade_id=str(trade_id),
            decision_id=verdict.get("decision_id"),
            data={
                "side": side.value,
                "quantity": quantity,
                "entry_price": entry_price,
                "sl": sl_price,
                "tp": tp_price,
                "notional": notional,
            }
        )
        
        print(f"SimulationEngine: Trade OPEN {side.value} {symbol} qty={quantity:.0f} @ {entry_price:.5f} SL={sl_price:.5f} TP={tp_price:.5f}")
    
    # ==================== POSITION MONITORING ====================
    
    def _load_open_positions(self):
        """Load existing open positions from DB"""
        try:
            rows = self.trades_repo.get_open_trades()
            for row in rows:
                trade = self._row_to_trade(row)
                self.open_positions[str(trade.id)] = trade
            print(f"SimulationEngine: Loaded {len(rows)} open positions")
        except Exception as e:
            print(f"SimulationEngine: Error loading positions: {e}")
    
    def _row_to_trade(self, row: Dict) -> SimTrade:
        """Convert DB row to SimTrade"""
        return SimTrade(
            id=UUID(row["id"]),
            decision_id=row.get("decision_id"),
            signal_preview_id=row.get("signal_preview_id"),
            verdict_id=row.get("verdict_id"),
            symbol=row.get("symbol", ""),
            side=SimSide(row.get("side", "BUY")),
            quantity=row.get("quantity", 0),
            entry_price=row.get("entry_price"),
            exit_price=row.get("exit_price"),
            stop_loss=row.get("stop_loss"),
            take_profit=row.get("take_profit"),
            current_price=row.get("current_price"),
            pnl=row.get("pnl"),
            pnl_pips=row.get("pnl_pips"),
            unrealized_pnl=row.get("unrealized_pnl"),
            status=SimTradeStatus(row.get("status", "OPEN")),
            close_reason=SimCloseReason(row["close_reason"]) if row.get("close_reason") else None,
            opened_at=datetime.fromisoformat(row["opened_at"]) if row.get("opened_at") else None,
            closed_at=datetime.fromisoformat(row["closed_at"]) if row.get("closed_at") else None,
            equity_at_entry=row.get("equity_at_entry"),
            risk_cash=row.get("risk_cash"),
            sl_pips=row.get("sl_pips"),
            tp_pips=row.get("tp_pips"),
            notional=row.get("notional"),
            risk_modifier=row.get("risk_modifier", 1.0),
        )
    
    def _check_open_positions(self):
        """Check all open positions for SL/TP hits and update P&L"""
        if not self.open_positions:
            return
        
        # Get quotes for all symbols
        symbols = list(set(t.symbol for t in self.open_positions.values()))
        quotes = self.price_feed.get_quotes(symbols)
        
        positions_to_close = []
        
        for trade_id, trade in self.open_positions.items():
            quote = quotes.get(trade.symbol)
            if not quote:
                continue
            
            # Update unrealized P&L
            unrealized_pips, unrealized_cash = self.pnl_calc.calculate_unrealized_pnl(
                trade.symbol,
                trade.side,
                trade.entry_price,
                quote.bid,
                quote.ask,
                trade.quantity,
            )
            
            trade.current_price = quote.mid
            trade.unrealized_pnl = unrealized_cash
            
            # Update in DB
            self.trades_repo.update_current_price(trade_id, quote.mid, unrealized_cash)
            
            # Check SL
            if self.pnl_calc.check_sl_hit(trade.side, trade.stop_loss, quote.bid, quote.ask):
                positions_to_close.append((trade_id, trade, quote, SimCloseReason.SL_HIT))
                continue
            
            # Check TP
            if self.pnl_calc.check_tp_hit(trade.side, trade.take_profit, quote.bid, quote.ask):
                positions_to_close.append((trade_id, trade, quote, SimCloseReason.TP_HIT))
                continue
        
        # Close triggered positions
        for trade_id, trade, quote, reason in positions_to_close:
            self._close_trade(trade_id, trade, quote, reason)
    
    def _close_trade(
        self,
        trade_id: str,
        trade: SimTrade,
        quote: PriceQuote,
        reason: SimCloseReason,
    ):
        """Close a simulated trade"""
        now = datetime.now(timezone.utc)
        
        # Get exit price
        exit_price = self.pnl_calc.get_exit_price_for_close(trade.side, quote.bid, quote.ask)
        
        # Calculate final P&L
        pnl_pips, pnl_cash = self.pnl_calc.calculate_pnl(
            trade.symbol,
            trade.side,
            trade.entry_price,
            exit_price,
            trade.quantity,
        )
        
        # Update trade
        self.trades_repo.update_status(
            trade_id=trade_id,
            status=SimTradeStatus.CLOSED,
            exit_price=exit_price,
            pnl=pnl_cash,
            pnl_pips=pnl_pips,
            close_reason=reason,
            closed_at=now,
        )
        
        # Record exit fill
        fill = SimFill(
            trade_id=UUID(trade_id),
            fill_type="EXIT",
            symbol=trade.symbol,
            side=trade.side,
            quantity=trade.quantity,
            price=exit_price,
            bid=quote.bid,
            ask=quote.ask,
            spread=quote.spread,
        )
        self.fills_repo.insert(fill)
        
        # Update closed P&L
        self.closed_pnl += pnl_cash
        self.sim_equity = self.baseline_equity + self.closed_pnl + self._calculate_open_pnl()
        
        # Remove from open positions
        if trade_id in self.open_positions:
            del self.open_positions[trade_id]
        
        # Log event
        event_type = SimEventType.TRADE_CLOSE_TP if reason == SimCloseReason.TP_HIT else SimEventType.TRADE_CLOSE_SL
        self.events_repo.log(
            event_type,
            f"Trade closed by {reason.value}: {trade.side.value} {trade.symbol} P&L={pnl_cash:.2f}",
            symbol=trade.symbol,
            trade_id=trade_id,
            data={
                "exit_price": exit_price,
                "pnl": pnl_cash,
                "pnl_pips": pnl_pips,
                "reason": reason.value,
            }
        )
        
        pnl_str = f"+${pnl_cash:.2f}" if pnl_cash >= 0 else f"-${abs(pnl_cash):.2f}"
        print(f"SimulationEngine: Trade CLOSED {reason.value} {trade.symbol} @ {exit_price:.5f} P&L={pnl_str} ({pnl_pips:.1f} pips)")
    
    # ==================== DATA FETCHING ====================
    
    def _fetch_decision(self, decision_id: str) -> Optional[Dict]:
        """Fetch decision from control_decisions"""
        if not decision_id:
            return None
        try:
            res = (
                self.db.client.table("control_decisions")
                .select("id, symbol, signal_preview_id, trade_allowed, risk_modifier")
                .eq("id", decision_id)
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None
        except Exception:
            return None
    
    def _fetch_signal_preview(self, preview_id: str) -> Optional[Dict]:
        """Fetch signal preview"""
        if not preview_id:
            return None
        try:
            res = (
                self.db.client.table("signal_previews")
                .select("id, symbol, direction, sl_distance_pips, tp_distance_pips, entry_triggered, spread_quality")
                .eq("id", preview_id)
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None
        except Exception:
            return None
