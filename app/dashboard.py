"""
Dashboard API for LLM Agents Monitoring.

Phase 5: Real-time monitoring of parallel decisions and LLM agents.
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.storage.db import SupabaseDB
from app.broker.account_api import get_broker_api

logger = logging.getLogger(__name__)

app = FastAPI(
    title="IBKR Trading Bot Dashboard",
    description="LLM Agents Monitoring Dashboard",
    version="1.0.0",
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pydantic models for responses
class ParallelDecisionSummary(BaseModel):
    id: str
    ts_utc: str
    symbol: str
    rules_signal: str
    rules_confidence: str
    gpt_signal: str
    gpt_score: float
    hybrid_signal: str
    executed_strategy: str
    executed_signal: str
    gpt_consensus: bool
    cache_hits: int


class AgentStats(BaseModel):
    total_decisions: int
    by_strategy: Dict[str, int]
    by_signal: Dict[str, int]
    consensus_rate: float
    avg_cache_hits: float
    last_hour_count: int


class SignalComparison(BaseModel):
    symbol: str
    rules_long: int
    rules_short: int
    rules_hold: int
    gpt_long: int
    gpt_short: int
    gpt_hold: int
    agreement_rate: float


class CloseTradeRequest(BaseModel):
    trade_id: str


class CloseTradeResponse(BaseModel):
    status: str
    trade_id: str
    symbol: str
    closed_quantity: float
    exit_price: Optional[float]
    message: Optional[str] = None


# Database connection
_db: Optional[SupabaseDB] = None


def get_db() -> SupabaseDB:
    global _db
    if _db is None:
        _db = SupabaseDB()
    return _db


@app.get("/", response_class=HTMLResponse)
async def dashboard_html():
    """Serve the dashboard HTML."""
    return DASHBOARD_HTML


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    db = get_db()
    return {
        "status": "ok",
        "supabase": db.ping(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/broker")
async def get_broker_data():
    """
    Get real-time broker account data from IB Gateway.
    
    Returns account summary, positions, orders, and connection status.
    Used by the frontend /admin/broker page.
    """
    try:
        broker_api = get_broker_api()
        data = broker_api.get_account_data()
        
        # Merge with bot_settings for tradingEnabled and mode
        db = get_db()
        settings_result = db.client.table("bot_settings").select("trading_enabled, mode").order("updated_at", desc=True).limit(1).execute()
        settings_rows = getattr(settings_result, "data", []) or []
        
        if settings_rows:
            settings = settings_rows[0]
            data["connection"]["tradingEnabled"] = settings.get("trading_enabled", False)
            # Keep IB-detected mode if connected, otherwise use settings
            if not data["account"]["connected"]:
                data["connection"]["mode"] = settings.get("mode", "dry_run")
        
        return data
    except Exception as e:
        return {
            "error": str(e),
            "account": {
                "accountId": "ERROR",
                "accountType": "UNKNOWN",
                "currency": "USD",
                "equity": 0,
                "availableFunds": 0,
                "buyingPower": 0,
                "marginUsed": 0,
                "marginAvailable": 0,
                "unrealizedPnl": 0,
                "dailyPnl": 0,
                "leverage": 0,
                "connected": False,
                "lastUpdate": None,
            },
            "connection": {
                "ibGateway": "disconnected",
                "dataFeed": "unknown",
                "tradingEnabled": False,
                "mode": "unknown",
                "lastHeartbeat": None,
            },
            "positions": [],
            "orders": [],
            "openPositions": 0,
            "pendingOrders": 0,
        }


@app.post("/api/broker/reconnect")
async def reconnect_broker():
    """Force IB Gateway reconnect attempt for the dashboard."""
    broker_api = get_broker_api()
    data = broker_api.get_account_data(force_refresh=True)
    connected = bool(data.get("account", {}).get("connected"))
    return {
        "status": "connected" if connected else "disconnected",
        "data": data,
    }


def _normalize_symbol(symbol: str) -> str:
    return symbol.replace(".", "").replace("/", "").replace(" ", "").upper()


def _match_position_symbol(contract, symbol: str) -> bool:
    base = getattr(contract, "symbol", "") or ""
    quote = getattr(contract, "currency", "") or ""
    combined = f"{base}{quote}".upper()
    return combined == _normalize_symbol(symbol)


def _pip_size(symbol: str) -> float:
    return 0.01 if symbol.upper().endswith("JPY") else 0.0001


def _close_trade_sync(trade: dict) -> dict:
    """
    Close a forex position via IB Gateway.
    
    Uses separate thread with its own event loop (same pattern as BrokerAccountAPI).
    
    IMPORTANT: First checks if position exists at broker. If not, returns
    success with no_position flag so DB can be updated without opening new position.
    """
    from queue import Queue
    from threading import Thread
    import asyncio
    
    result_queue: Queue = Queue()
    
    def worker():
        # Create fresh event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            from ib_insync import IB, Forex, MarketOrder
            
            host = os.getenv("IB_GATEWAY_HOST", "127.0.0.1")
            port = int(os.getenv("IB_GATEWAY_PORT", "4004"))
            client_id = int(os.getenv("IB_CLIENT_ID_MANUAL_CLOSE", "161"))
            
            # Docker container may need different host
            if host in ("127.0.0.1", "localhost"):
                alt_host = os.getenv("IBKR_HOST")
                if alt_host and alt_host not in ("127.0.0.1", "localhost"):
                    host = alt_host
            
            logger.info(f"[ClosePosition] Connecting to {host}:{port} clientId={client_id}")
            
            ib = IB()
            
            try:
                # Connect using event loop
                loop.run_until_complete(
                    ib.connectAsync(host, port, clientId=client_id, timeout=15, readonly=False)
                )
                
                if not ib.isConnected():
                    logger.error("[ClosePosition] Failed to connect")
                    result_queue.put({"ok": False, "error": "ib_not_connected"})
                    return
                
                logger.info("[ClosePosition] Connected to IB Gateway")
                
                symbol = trade.get("symbol") or ""
                normalized = symbol.replace(".", "").replace("/", "").replace(" ", "").upper()
                trade_side = (trade.get("side") or "BUY").upper()
                trade_qty = float(trade.get("quantity") or 0)
                
                if trade_qty <= 0:
                    result_queue.put({"ok": False, "error": "invalid_quantity"})
                    return
                
                # ============ CHECK IF POSITION EXISTS AT BROKER ============
                # For FX, check cash balances
                position_exists = False
                broker_qty = 0.0
                
                # Extract base currency from symbol (e.g., USDCHF -> USD)
                base_ccy = normalized[:3] if len(normalized) >= 6 else normalized
                
                for v in ib.accountSummary():
                    if v.tag == "CashBalance" and v.currency == base_ccy:
                        try:
                            broker_qty = abs(float(v.value))
                            if broker_qty > 100:  # Significant position
                                position_exists = True
                                logger.info(f"[ClosePosition] Found {base_ccy} balance: {v.value}")
                        except:
                            pass
                        break
                
                if not position_exists:
                    logger.warning(f"[ClosePosition] No position found at broker for {symbol}. "
                                  f"Position was likely closed by SL/TP.")
                    # Return success with flag - position already closed
                    result_queue.put({
                        "ok": True, 
                        "exit_price": None, 
                        "closed_qty": 0,
                        "no_position": True,
                        "message": "Position already closed at broker (SL/TP)"
                    })
                    return
                # ============ END POSITION CHECK ============
                
                # Determine close direction
                if trade_side in ("BUY", "LONG"):
                    position = trade_qty
                else:
                    position = -trade_qty
                
                logger.info(f"[ClosePosition] Trade: {symbol} side={trade_side} qty={trade_qty}")
                
                # Cancel bracket orders first
                parent_id = trade.get("ib_order_id")
                if parent_id:
                    logger.info(f"[ClosePosition] Cancelling bracket orders for parent={parent_id}")
                    for open_trade in ib.openTrades():
                        order = getattr(open_trade, "order", None)
                        if order and getattr(order, "parentId", None) == parent_id:
                            try:
                                ib.cancelOrder(order)
                                logger.info(f"[ClosePosition] Cancelled order {getattr(order, 'orderId', '?')}")
                            except Exception as e:
                                logger.warning(f"[ClosePosition] Cancel failed: {e}")
                    ib.sleep(0.5)
                
                action = "SELL" if position > 0 else "BUY"
                qty = abs(position)
                
                contract = Forex(pair=normalized)
                try:
                    ib.qualifyContracts(contract)
                    logger.info(f"[ClosePosition] Contract qualified: {contract}")
                except Exception as e:
                    logger.warning(f"[ClosePosition] qualifyContracts: {e}")
                
                order = MarketOrder(action, qty)
                order.tif = "GTC"
                
                logger.info(f"[ClosePosition] Placing {action} {qty} {normalized}")
                ib_trade = ib.placeOrder(contract, order)
                
                # Wait for fill
                timeout_s = 15
                waited = 0.0
                while not ib_trade.isDone() and waited < timeout_s:
                    ib.sleep(0.5)
                    waited += 0.5
                
                exit_price = None
                status = getattr(ib_trade, "orderStatus", None)
                if status:
                    exit_price = getattr(status, "avgFillPrice", None)
                    logger.info(f"[ClosePosition] Order status: {getattr(status, 'status', '?')} price={exit_price}")
                
                if exit_price in (None, 0):
                    fills = getattr(ib_trade, "fills", []) or []
                    if fills:
                        execution = getattr(fills[-1], "execution", None)
                        if execution:
                            exit_price = getattr(execution, "price", None)
                
                logger.info(f"[ClosePosition] Done: exit_price={exit_price} qty={qty}")
                result_queue.put({"ok": True, "exit_price": exit_price, "closed_qty": qty})
                
            except Exception as e:
                logger.error(f"[ClosePosition] IB error: {e}")
                result_queue.put({"ok": False, "error": str(e)})
            finally:
                try:
                    if ib.isConnected():
                        ib.disconnect()
                except:
                    pass
        except Exception as e:
            logger.error(f"[ClosePosition] Worker error: {e}")
            result_queue.put({"ok": False, "error": str(e)})
        finally:
            try:
                loop.close()
            except:
                pass
    
    # Run in separate thread
    logger.info(f"[ClosePosition] Starting thread for trade {trade.get('id')}")
    thread = Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=30)
    
    if result_queue.empty():
        logger.error("[ClosePosition] Timeout waiting for result")
        return {"ok": False, "error": "timeout"}
    
    return result_queue.get()


@app.post("/api/broker/close", response_model=CloseTradeResponse)
def close_trade(req: CloseTradeRequest):
    """Close an open trade position via IB Gateway."""
    db = get_db()
    trade_res = (
        db.client.table("trades_history")
        .select("*")
        .eq("id", req.trade_id)
        .limit(1)
        .execute()
    )
    trade_rows = getattr(trade_res, "data", None) or []
    if not trade_rows:
        raise HTTPException(status_code=404, detail="trade_not_found")
    trade = trade_rows[0]
    if trade.get("status") != "OPEN":
        raise HTTPException(status_code=400, detail="trade_not_open")

    # Close via IB Gateway (runs in separate thread with own event loop)
    result = _close_trade_sync(trade)
    
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error") or "close_failed")

    # Handle case where position was already closed at broker (SL/TP hit)
    if result.get("no_position"):
        logger.info(f"Trade {req.trade_id} position not found at broker - marking as closed")
        try:
            db.client.table("trades_history").update({
                "status": "CLOSED",
                "close_reason": "RECONCILED_PHANTOM",
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", req.trade_id).execute()
        except Exception as exc:
            logger.error(f"Failed to update phantom trade: {exc}")
        
        return CloseTradeResponse(
            status="closed",
            trade_id=req.trade_id,
            symbol=trade.get("symbol") or "",
            closed_quantity=0,
            exit_price=None,
            message="Position was already closed at broker (SL/TP)",
        )

    exit_price = result.get("exit_price")
    qty = float(result.get("closed_qty") or trade.get("quantity") or 0)
    entry = trade.get("entry_price")
    side = (trade.get("side") or "BUY").upper()
    pnl = None
    pnl_pips = None
    if entry is not None and exit_price is not None and qty:
        if side == "BUY":
            pnl = (exit_price - entry) * qty
        else:
            pnl = (entry - exit_price) * qty
        pip = _pip_size(trade.get("symbol") or "")
        if pip > 0:
            pnl_pips = (exit_price - entry) / pip
            if side == "SELL":
                pnl_pips = -pnl_pips

    try:
        db.client.table("trades_history").update({
            "status": "CLOSED",
            "exit_price": exit_price,
            "close_reason": "MANUAL",
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "pnl": pnl,
            "pnl_pips": pnl_pips,
        }).eq("id", req.trade_id).execute()
    except Exception as exc:
        logger.error(f"Failed to update trade close: {exc}")

    return CloseTradeResponse(
        status="closed",
        trade_id=req.trade_id,
        symbol=trade.get("symbol") or "",
        closed_quantity=qty,
        exit_price=exit_price,
        message="ok",
    )


# ============== Reconciliation API ==============

class ReconciliationResponse(BaseModel):
    """Response from reconciliation endpoint."""
    timestamp: str
    broker_connected: bool
    db_open_trades: int
    broker_positions: int
    phantom_trades: List[Dict[str, Any]]
    orphan_positions: List[Dict[str, Any]]
    quantity_mismatches: List[Dict[str, Any]]
    matched: List[str]
    errors: List[str]
    summary: str
    has_issues: bool


@app.post("/api/broker/reconcile")
def run_reconciliation(auto_close: bool = True):
    """
    Run position reconciliation between broker and database.
    
    Compares positions at IB Gateway with OPEN trades in trades_history.
    
    Detects:
    - Phantom trades: OPEN in DB but no position at broker (SL/TP triggered)
    - Orphan positions: Position at broker but no record in DB
    - Quantity mismatches: Position size differs
    
    Args:
        auto_close: If True, automatically mark phantom trades as CLOSED
    
    Returns:
        ReconciliationReport with findings and actions taken.
    """
    try:
        from app.reconciliation.position_reconciler import PositionReconciler
        
        db = get_db()
        reconciler = PositionReconciler(
            db=db,
            auto_close_phantoms=auto_close,
            auto_create_orphans=False,
        )
        
        report = reconciler.run()
        
        return {
            "timestamp": report.timestamp.isoformat(),
            "broker_connected": report.broker_connected,
            "db_open_trades": report.db_open_trades,
            "broker_positions": report.broker_positions,
            "phantom_trades": [
                {
                    "symbol": r.symbol,
                    "action": r.action.value,
                    "db_trade_id": r.db_trade_id,
                    "db_quantity": r.db_quantity,
                    "db_side": r.db_side,
                    "message": r.message,
                }
                for r in report.phantom_trades
            ],
            "orphan_positions": [
                {
                    "symbol": r.symbol,
                    "action": r.action.value,
                    "broker_quantity": r.broker_quantity,
                    "message": r.message,
                }
                for r in report.orphan_positions
            ],
            "quantity_mismatches": [
                {
                    "symbol": r.symbol,
                    "db_trade_id": r.db_trade_id,
                    "db_quantity": r.db_quantity,
                    "broker_quantity": r.broker_quantity,
                    "message": r.message,
                }
                for r in report.quantity_mismatches
            ],
            "matched": report.matched,
            "errors": report.errors,
            "summary": report.summary,
            "has_issues": report.has_issues,
        }
    except Exception as e:
        logger.error(f"Reconciliation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/broker/reconcile/status")
def get_reconciliation_status():
    """
    Quick check for position mismatches without running full reconciliation.
    
    Returns count of OPEN trades in DB for comparison.
    """
    db = get_db()
    try:
        result = db.client.table("trades_history").select("id, symbol, side, quantity").eq("status", "OPEN").execute()
        trades = result.data or []
        return {
            "db_open_trades": len(trades),
            "trades": [
                {
                    "id": t.get("id"),
                    "symbol": t.get("symbol"),
                    "side": t.get("side"),
                    "quantity": t.get("quantity"),
                }
                for t in trades
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/parallel-decisions", response_model=List[ParallelDecisionSummary])
async def get_parallel_decisions(
    limit: int = Query(50, ge=1, le=500),
    symbol: Optional[str] = None,
    hours: int = Query(168, ge=1, le=720),
):
    """Get recent parallel decisions."""
    db = get_db()
    
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    
    query = (
        db.client.table("parallel_decisions")
        .select("*")
        .gte("ts_utc", since.isoformat())
        .order("ts_utc", desc=True)
        .limit(limit)
    )
    
    if symbol:
        query = query.eq("symbol", symbol.upper())
    
    result = query.execute()
    rows = getattr(result, "data", []) or []
    
    return [
        ParallelDecisionSummary(
            id=str(row.get("id", "")),
            ts_utc=row.get("ts_utc", ""),
            symbol=row.get("symbol", ""),
            rules_signal=row.get("rules_signal", "HOLD"),
            rules_confidence=row.get("rules_confidence", "low"),
            gpt_signal=row.get("gpt_signal", "HOLD"),
            gpt_score=row.get("gpt_score", 0.0) or 0.0,
            hybrid_signal=row.get("hybrid_signal", "HOLD"),
            executed_strategy=row.get("executed_strategy", "rules"),
            executed_signal=row.get("executed_signal", "HOLD"),
            gpt_consensus=row.get("gpt_consensus", False) or False,
            cache_hits=row.get("cache_hits", 0) or 0,
        )
        for row in rows
    ]


@app.get("/api/stats", response_model=AgentStats)
async def get_agent_stats(hours: int = Query(168, ge=1, le=720)):
    """Get aggregated statistics for LLM agents."""
    db = get_db()
    
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    last_hour = datetime.now(timezone.utc) - timedelta(hours=1)
    
    # Get all decisions in time range
    result = (
        db.client.table("parallel_decisions")
        .select("executed_strategy, executed_signal, gpt_consensus, cache_hits, ts_utc")
        .gte("ts_utc", since.isoformat())
        .execute()
    )
    rows = getattr(result, "data", []) or []
    
    if not rows:
        return AgentStats(
            total_decisions=0,
            by_strategy={"rules": 0, "gpt": 0, "hybrid": 0},
            by_signal={"LONG": 0, "SHORT": 0, "HOLD": 0},
            consensus_rate=0.0,
            avg_cache_hits=0.0,
            last_hour_count=0,
        )
    
    # Calculate stats
    by_strategy = {"rules": 0, "gpt": 0, "hybrid": 0}
    by_signal = {"LONG": 0, "SHORT": 0, "HOLD": 0}
    consensus_count = 0
    total_cache_hits = 0
    last_hour_count = 0
    
    for row in rows:
        strategy = row.get("executed_strategy", "rules")
        signal = row.get("executed_signal", "HOLD")
        consensus = row.get("gpt_consensus", False)
        cache = row.get("cache_hits", 0) or 0
        ts = row.get("ts_utc", "")
        
        by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
        by_signal[signal] = by_signal.get(signal, 0) + 1
        if consensus:
            consensus_count += 1
        total_cache_hits += cache
        
        if ts and ts >= last_hour.isoformat():
            last_hour_count += 1
    
    total = len(rows)
    
    return AgentStats(
        total_decisions=total,
        by_strategy=by_strategy,
        by_signal=by_signal,
        consensus_rate=round(consensus_count / total, 3) if total > 0 else 0.0,
        avg_cache_hits=round(total_cache_hits / total, 2) if total > 0 else 0.0,
        last_hour_count=last_hour_count,
    )


@app.get("/api/comparison", response_model=List[SignalComparison])
async def get_signal_comparison(hours: int = Query(168, ge=1, le=720)):
    """Compare rules vs GPT signals by symbol."""
    db = get_db()
    
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    
    result = (
        db.client.table("parallel_decisions")
        .select("symbol, rules_signal, gpt_signal")
        .gte("ts_utc", since.isoformat())
        .execute()
    )
    rows = getattr(result, "data", []) or []
    
    # Aggregate by symbol
    symbols: Dict[str, Dict[str, int]] = {}
    
    for row in rows:
        symbol = row.get("symbol", "UNKNOWN")
        rules = row.get("rules_signal", "HOLD")
        gpt = row.get("gpt_signal", "HOLD")
        
        if symbol not in symbols:
            symbols[symbol] = {
                "rules_LONG": 0, "rules_SHORT": 0, "rules_HOLD": 0,
                "gpt_LONG": 0, "gpt_SHORT": 0, "gpt_HOLD": 0,
                "agreements": 0, "total": 0,
            }
        
        symbols[symbol][f"rules_{rules}"] += 1
        symbols[symbol][f"gpt_{gpt}"] += 1
        symbols[symbol]["total"] += 1
        if rules == gpt:
            symbols[symbol]["agreements"] += 1
    
    return [
        SignalComparison(
            symbol=sym,
            rules_long=data["rules_LONG"],
            rules_short=data["rules_SHORT"],
            rules_hold=data["rules_HOLD"],
            gpt_long=data["gpt_LONG"],
            gpt_short=data["gpt_SHORT"],
            gpt_hold=data["gpt_HOLD"],
            agreement_rate=round(data["agreements"] / data["total"], 3) if data["total"] > 0 else 0.0,
        )
        for sym, data in sorted(symbols.items())
    ]


@app.get("/api/agent-details/{decision_id}")
async def get_agent_details(decision_id: str):
    """Get detailed agent breakdown for a specific decision."""
    db = get_db()
    
    result = (
        db.client.table("parallel_decisions")
        .select("*")
        .eq("id", decision_id)
        .single()
        .execute()
    )
    
    row = getattr(result, "data", None)
    if not row:
        raise HTTPException(status_code=404, detail="Decision not found")
    
    return {
        "id": str(row.get("id", "")),
        "ts_utc": row.get("ts_utc", ""),
        "symbol": row.get("symbol", ""),
        "rules": {
            "signal": row.get("rules_signal", "HOLD"),
            "confidence": row.get("rules_confidence", "low"),
            "flags": row.get("rules_flags", []),
        },
        "gpt": {
            "signal": row.get("gpt_signal", "HOLD"),
            "score": row.get("gpt_score", 0.0),
            "consensus": row.get("gpt_consensus", False),
            "consensus_count": row.get("gpt_consensus_count", 0),
            "agent_details": row.get("gpt_agent_details", []),
        },
        "hybrid": {
            "signal": row.get("hybrid_signal", "HOLD"),
            "score": row.get("hybrid_score", 0.0),
        },
        "execution": {
            "strategy": row.get("executed_strategy", "rules"),
            "signal": row.get("executed_signal", "HOLD"),
        },
        "safety": {
            "source_health": row.get("source_health", {}),
            "budget_status": row.get("budget_status", "OK"),
            "cache_hits": row.get("cache_hits", 0),
            "validation_failures": row.get("validation_failures", 0),
        },
    }


# Dashboard HTML
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IBKR Trading Bot - LLM Agents Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
    <style>
        .signal-long { color: #10b981; font-weight: bold; }
        .signal-short { color: #ef4444; font-weight: bold; }
        .signal-hold { color: #6b7280; }
        .confidence-high { background: #dcfce7; }
        .confidence-normal { background: #fef3c7; }
        .confidence-low { background: #f3f4f6; }
    </style>
</head>
<body class="bg-gray-100 min-h-screen">
    <div id="app" class="container mx-auto px-4 py-8">
        <!-- Header -->
        <div class="bg-white rounded-lg shadow-md p-6 mb-6">
            <div class="flex justify-between items-center">
                <h1 class="text-2xl font-bold text-gray-800">🤖 LLM Agents Dashboard</h1>
                <div class="flex items-center gap-4">
                    <span class="text-sm text-gray-500">Last updated: {{ lastUpdate }}</span>
                    <button @click="refresh" class="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600">
                        Refresh
                    </button>
                </div>
            </div>
        </div>

        <!-- Stats Cards -->
        <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
            <div class="bg-white rounded-lg shadow-md p-6">
                <h3 class="text-sm text-gray-500 uppercase">Total Decisions (7d)</h3>
                <p class="text-3xl font-bold text-gray-800">{{ stats.total_decisions }}</p>
            </div>
            <div class="bg-white rounded-lg shadow-md p-6">
                <h3 class="text-sm text-gray-500 uppercase">Last Hour</h3>
                <p class="text-3xl font-bold text-blue-600">{{ stats.last_hour_count }}</p>
            </div>
            <div class="bg-white rounded-lg shadow-md p-6">
                <h3 class="text-sm text-gray-500 uppercase">GPT Consensus Rate</h3>
                <p class="text-3xl font-bold text-green-600">{{ (stats.consensus_rate * 100).toFixed(1) }}%</p>
            </div>
            <div class="bg-white rounded-lg shadow-md p-6">
                <h3 class="text-sm text-gray-500 uppercase">Avg Cache Hits</h3>
                <p class="text-3xl font-bold text-purple-600">{{ stats.avg_cache_hits.toFixed(1) }}</p>
            </div>
        </div>

        <!-- Signal Distribution -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
            <div class="bg-white rounded-lg shadow-md p-6">
                <h2 class="text-lg font-semibold mb-4">📊 Signal Distribution</h2>
                <div class="flex gap-4">
                    <div class="flex-1 text-center p-4 bg-green-50 rounded">
                        <p class="text-2xl font-bold text-green-600">{{ stats.by_signal?.LONG || 0 }}</p>
                        <p class="text-sm text-gray-500">LONG</p>
                    </div>
                    <div class="flex-1 text-center p-4 bg-red-50 rounded">
                        <p class="text-2xl font-bold text-red-600">{{ stats.by_signal?.SHORT || 0 }}</p>
                        <p class="text-sm text-gray-500">SHORT</p>
                    </div>
                    <div class="flex-1 text-center p-4 bg-gray-50 rounded">
                        <p class="text-2xl font-bold text-gray-600">{{ stats.by_signal?.HOLD || 0 }}</p>
                        <p class="text-sm text-gray-500">HOLD</p>
                    </div>
                </div>
            </div>
            <div class="bg-white rounded-lg shadow-md p-6">
                <h2 class="text-lg font-semibold mb-4">🎯 Strategy Usage</h2>
                <div class="flex gap-4">
                    <div class="flex-1 text-center p-4 bg-blue-50 rounded">
                        <p class="text-2xl font-bold text-blue-600">{{ stats.by_strategy?.rules || 0 }}</p>
                        <p class="text-sm text-gray-500">Rules</p>
                    </div>
                    <div class="flex-1 text-center p-4 bg-purple-50 rounded">
                        <p class="text-2xl font-bold text-purple-600">{{ stats.by_strategy?.gpt || 0 }}</p>
                        <p class="text-sm text-gray-500">GPT</p>
                    </div>
                    <div class="flex-1 text-center p-4 bg-indigo-50 rounded">
                        <p class="text-2xl font-bold text-indigo-600">{{ stats.by_strategy?.hybrid || 0 }}</p>
                        <p class="text-sm text-gray-500">Hybrid</p>
                    </div>
                </div>
            </div>
        </div>

        <!-- Symbol Comparison -->
        <div class="bg-white rounded-lg shadow-md p-6 mb-6">
            <h2 class="text-lg font-semibold mb-4">⚖️ Rules vs GPT Comparison</h2>
            <div class="overflow-x-auto">
                <table class="w-full text-sm">
                    <thead class="bg-gray-50">
                        <tr>
                            <th class="px-4 py-2 text-left">Symbol</th>
                            <th class="px-4 py-2 text-center" colspan="3">Rules</th>
                            <th class="px-4 py-2 text-center" colspan="3">GPT</th>
                            <th class="px-4 py-2 text-center">Agreement</th>
                        </tr>
                        <tr class="text-xs text-gray-500">
                            <th></th>
                            <th class="px-2">L</th>
                            <th class="px-2">S</th>
                            <th class="px-2">H</th>
                            <th class="px-2">L</th>
                            <th class="px-2">S</th>
                            <th class="px-2">H</th>
                            <th></th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="c in comparison" :key="c.symbol" class="border-t hover:bg-gray-50">
                            <td class="px-4 py-2 font-medium">{{ c.symbol }}</td>
                            <td class="px-2 py-2 text-center text-green-600">{{ c.rules_long }}</td>
                            <td class="px-2 py-2 text-center text-red-600">{{ c.rules_short }}</td>
                            <td class="px-2 py-2 text-center text-gray-500">{{ c.rules_hold }}</td>
                            <td class="px-2 py-2 text-center text-green-600">{{ c.gpt_long }}</td>
                            <td class="px-2 py-2 text-center text-red-600">{{ c.gpt_short }}</td>
                            <td class="px-2 py-2 text-center text-gray-500">{{ c.gpt_hold }}</td>
                            <td class="px-4 py-2 text-center">
                                <span :class="c.agreement_rate > 0.7 ? 'text-green-600' : c.agreement_rate > 0.4 ? 'text-yellow-600' : 'text-red-600'">
                                    {{ (c.agreement_rate * 100).toFixed(0) }}%
                                </span>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Recent Decisions -->
        <div class="bg-white rounded-lg shadow-md p-6">
            <h2 class="text-lg font-semibold mb-4">📋 Recent Decisions</h2>
            <div class="overflow-x-auto">
                <table class="w-full text-sm">
                    <thead class="bg-gray-50">
                        <tr>
                            <th class="px-4 py-2 text-left">Time</th>
                            <th class="px-4 py-2 text-left">Symbol</th>
                            <th class="px-4 py-2 text-center">Rules</th>
                            <th class="px-4 py-2 text-center">GPT</th>
                            <th class="px-4 py-2 text-center">Hybrid</th>
                            <th class="px-4 py-2 text-center">Executed</th>
                            <th class="px-4 py-2 text-center">Consensus</th>
                            <th class="px-4 py-2 text-center">Cache</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="d in decisions" :key="d.id" class="border-t hover:bg-gray-50 cursor-pointer" @click="showDetails(d.id)">
                            <td class="px-4 py-2 text-gray-500">{{ formatTime(d.ts_utc) }}</td>
                            <td class="px-4 py-2 font-medium">{{ d.symbol }}</td>
                            <td class="px-4 py-2 text-center">
                                <span :class="'signal-' + d.rules_signal.toLowerCase()">{{ d.rules_signal }}</span>
                                <span class="text-xs text-gray-400 ml-1">{{ d.rules_confidence }}</span>
                            </td>
                            <td class="px-4 py-2 text-center">
                                <span :class="'signal-' + d.gpt_signal.toLowerCase()">{{ d.gpt_signal }}</span>
                                <span class="text-xs text-gray-400 ml-1">({{ d.gpt_score.toFixed(2) }})</span>
                            </td>
                            <td class="px-4 py-2 text-center">
                                <span :class="'signal-' + d.hybrid_signal.toLowerCase()">{{ d.hybrid_signal }}</span>
                            </td>
                            <td class="px-4 py-2 text-center">
                                <span class="px-2 py-1 rounded text-xs" :class="d.executed_strategy === 'hybrid' ? 'bg-indigo-100' : d.executed_strategy === 'gpt' ? 'bg-purple-100' : 'bg-blue-100'">
                                    {{ d.executed_strategy }}: {{ d.executed_signal }}
                                </span>
                            </td>
                            <td class="px-4 py-2 text-center">
                                <span v-if="d.gpt_consensus" class="text-green-500">✓</span>
                                <span v-else class="text-gray-300">-</span>
                            </td>
                            <td class="px-4 py-2 text-center text-gray-500">{{ d.cache_hits }}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Modal for Details -->
        <div v-if="selectedDecision" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="selectedDecision = null">
            <div class="bg-white rounded-lg shadow-xl p-6 max-w-2xl w-full mx-4 max-h-[80vh] overflow-y-auto">
                <div class="flex justify-between items-center mb-4">
                    <h3 class="text-lg font-semibold">Decision Details</h3>
                    <button @click="selectedDecision = null" class="text-gray-500 hover:text-gray-700">✕</button>
                </div>
                <pre class="bg-gray-50 p-4 rounded text-xs overflow-auto">{{ JSON.stringify(selectedDecision, null, 2) }}</pre>
            </div>
        </div>
    </div>

    <script>
        const { createApp, ref, onMounted } = Vue;

        createApp({
            setup() {
                const stats = ref({ total_decisions: 0, by_strategy: {}, by_signal: {}, consensus_rate: 0, avg_cache_hits: 0, last_hour_count: 0 });
                const decisions = ref([]);
                const comparison = ref([]);
                const lastUpdate = ref('');
                const selectedDecision = ref(null);

                const refresh = async () => {
                    try {
                        const [statsRes, decisionsRes, comparisonRes] = await Promise.all([
                            fetch('/api/stats'),
                            fetch('/api/parallel-decisions?limit=50'),
                            fetch('/api/comparison'),
                        ]);
                        stats.value = await statsRes.json();
                        decisions.value = await decisionsRes.json();
                        comparison.value = await comparisonRes.json();
                        lastUpdate.value = new Date().toLocaleTimeString();
                    } catch (e) {
                        console.error('Refresh failed:', e);
                    }
                };

                const formatTime = (ts) => {
                    if (!ts) return '';
                    return new Date(ts).toLocaleString();
                };

                const showDetails = async (id) => {
                    try {
                        const res = await fetch(`/api/agent-details/${id}`);
                        selectedDecision.value = await res.json();
                    } catch (e) {
                        console.error('Failed to load details:', e);
                    }
                };

                onMounted(() => {
                    refresh();
                    setInterval(refresh, 30000); // Auto-refresh every 30s
                });

                return { stats, decisions, comparison, lastUpdate, selectedDecision, refresh, formatTime, showDetails };
            }
        }).mount('#app');
    </script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
