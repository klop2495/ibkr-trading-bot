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

import threading

from app.storage.db import SupabaseDB
from app.broker.account_api import get_broker_api

# Optimizer state (in-memory, runs as background thread)
_optimizer_state: Dict[str, Any] = {
    "status": "idle",  # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "params": None,
    "results": None,
    "error": None,
    "progress": 0,
    "total_combos": 0,
}

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
    """Force IB Gateway reconnect — account API check + trading bot restart."""
    # 1. Check IB Gateway via account API (clientId 160)
    broker_api = get_broker_api()
    data = broker_api.get_account_data(force_refresh=True)
    account_connected = bool(data.get("account", {}).get("connected"))

    # 2. Restart trading bot container via Docker socket API
    bot_restarted = False
    restart_error = None
    try:
        import urllib.request
        req = urllib.request.Request(
            "http+unix:///var/run/docker.sock/containers/ibkr-trading-bot/restart",
            method="POST",
        )
        # Use raw socket since urllib doesn't support unix sockets
        import socket as _socket
        import http.client

        class DockerConnection(http.client.HTTPConnection):
            def connect(self):
                self.sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
                self.sock.connect("/var/run/docker.sock")
                self.sock.settimeout(60)

        conn = DockerConnection("localhost")
        conn.request("POST", "/containers/ibkr-trading-bot/restart?t=10")
        resp = conn.getresponse()
        if resp.status == 204:
            bot_restarted = True
            logger.info("Trading bot restarted successfully via Docker API")
        else:
            restart_error = f"Docker API returned {resp.status}: {resp.read().decode()}"
            logger.error(f"Bot restart failed: {restart_error}")
        conn.close()
    except FileNotFoundError:
        restart_error = "Docker socket not available"
        logger.error(restart_error)
    except Exception as e:
        restart_error = str(e)
        logger.error(f"Bot restart error: {e}")

    connected = account_connected
    return {
        "status": "connected" if connected else "disconnected",
        "account_connected": account_connected,
        "bot_restarted": bot_restarted,
        "restart_error": restart_error,
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
    Legacy reconciliation endpoint (deprecated).
    """
    raise HTTPException(
        status_code=410,
        detail="PositionReconciler is deprecated. Use BrokerStateService sync.",
    )


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


# ============== Forecast API ==============


@app.get("/api/forecasts")
async def get_forecasts():
    """Get latest price direction forecasts for all symbols."""
    db = get_db()
    try:
        from app.storage.forecast_repo import ForecastRepo
        repo = ForecastRepo(db)
        return {"forecasts": repo.get_all_latest()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/forecasts/{symbol}")
async def get_forecast_by_symbol(symbol: str, limit: int = Query(10, ge=1, le=100)):
    """Get latest forecasts for a specific symbol."""
    db = get_db()
    try:
        from app.storage.forecast_repo import ForecastRepo
        repo = ForecastRepo(db)
        return {"forecasts": repo.get_latest(symbol.upper(), limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/forecasts/{symbol}/history")
async def get_forecast_history(symbol: str, hours: int = Query(24, ge=1, le=168)):
    """Get forecast history for a symbol within time range."""
    db = get_db()
    try:
        from app.storage.forecast_repo import ForecastRepo
        repo = ForecastRepo(db)
        return {"forecasts": repo.get_history(symbol.upper(), hours=hours)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/forecasts-history")
async def get_forecasts_history(
    symbol: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    recommended_only: bool = Query(False, alias="recommendedOnly"),
):
    """Get paginated forecast history with filters."""
    db = get_db()
    try:
        from app.storage.forecast_repo import ForecastRepo
        repo = ForecastRepo(db)
        result = repo.get_history_all(
            symbol=symbol,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
        rows = result.get("rows") or []
        from app.forecast.recommended_windows import classify_utc_timestamp, get_recommended_window_labels
        from app.forecast.original_mode import classify_with_effective

        lifecycle_symbols = {}
        try:
            if _signal_lifecycle_manager is not None:
                lifecycle_symbols = _signal_lifecycle_manager.get_status().get("symbols", {}) or {}
            else:
                from app.forecast.signal_lifecycle import SignalLifecycleManager
                data = SignalLifecycleManager.load_from_supabase(_get_supabase_client()) or {}
                lifecycle_symbols = data.get("symbols", {}) or {}
        except Exception:
            lifecycle_symbols = {}

        enriched_rows = []
        for row in rows:
            in_window, matched_window, hour_utc, minute_utc = classify_utc_timestamp(str(row.get("ts_utc") or ""))
            has_alt2 = row.get("h30_alt2_direction") is not None
            has_alt3 = row.get("h30_alt3v2_direction") is not None or row.get("h30_alt3_direction") is not None
            has_alt4 = row.get("h30_alt4_direction") is not None

            lifecycle_alt2 = lifecycle_symbols.get(f"{row.get('symbol')}#alt2") or lifecycle_symbols.get(row.get("symbol"))
            lifecycle_alt3 = lifecycle_symbols.get(f"{row.get('symbol')}#alt3v2") or lifecycle_symbols.get(f"{row.get('symbol')}#alt3")
            lifecycle_alt4 = lifecycle_symbols.get(f"{row.get('symbol')}#alt4")
            lifecycle_status_alt2 = str((lifecycle_alt2 or {}).get("status") or "").lower()
            lifecycle_status_alt3 = str((lifecycle_alt3 or {}).get("status") or "").lower()
            lifecycle_status_alt4 = str((lifecycle_alt4 or {}).get("status") or "").lower()
            lifecycle_blocked_alt2 = lifecycle_status_alt2 in {"pending", "cooldown", "blacklisted"}
            lifecycle_blocked_alt3 = lifecycle_status_alt3 in {"pending", "cooldown", "blacklisted"}
            lifecycle_blocked_alt4 = lifecycle_status_alt4 in {"pending", "cooldown", "blacklisted"}

            alt2_trade_eligible = bool(row.get("h30_alt2_trade_eligible"))
            alt4_trade_eligible = bool(row.get("h30_alt4_trade_eligible"))
            recommended_alt2 = bool(in_window and has_alt2 and alt2_trade_eligible and not lifecycle_blocked_alt2)
            recommended_alt3 = bool(in_window and has_alt3 and not lifecycle_blocked_alt3)
            recommended_alt4 = bool(in_window and has_alt4 and alt4_trade_eligible and not lifecycle_blocked_alt4)
            recommended = bool(recommended_alt2 or recommended_alt3 or recommended_alt4)
            orig_badge, orig_effective = classify_with_effective(row.get("h30_direction"), hour_utc)
            row2 = {
                **row,
                "hour_utc": hour_utc,
                "hour_minute_utc": minute_utc,
                "matched_window_utc": matched_window,
                "recommended_window": recommended,
                "recommended_alt2": recommended_alt2,
                "recommended_alt3": recommended_alt3,
                "recommended_alt4": recommended_alt4,
                "window_status": "recommended" if recommended else "info_only",
                "h30_original_mode": orig_badge,
                "h30_original_effective_direction": orig_effective,
            }
            enriched_rows.append(row2)

        if recommended_only:
            enriched_rows = [r for r in enriched_rows if r.get("recommended_window")]

        def _acc(correct: int, total: int):
            return (correct / total) if total > 0 else None

        alt2_all = [r for r in enriched_rows if r.get("h30_alt2_direction") is not None]
        alt2_all_verified = [r for r in alt2_all if r.get("h30_alt2_correct") is not None]
        alt2_all_correct = sum(1 for r in alt2_all_verified if bool(r.get("h30_alt2_correct")))

        alt2_eligible = [r for r in alt2_all if bool(r.get("h30_alt2_trade_eligible"))]
        alt2_eligible_verified = [r for r in alt2_eligible if r.get("h30_alt2_correct") is not None]
        alt2_eligible_correct = sum(1 for r in alt2_eligible_verified if bool(r.get("h30_alt2_correct")))
        alt3_all = [r for r in enriched_rows if (r.get("h30_alt3v2_direction") is not None or r.get("h30_alt3_direction") is not None)]
        alt3_all_verified = [r for r in alt3_all if (r.get("h30_alt3v2_correct") is not None or r.get("h30_alt3_correct") is not None)]
        alt3_all_correct = sum(
            1
            for r in alt3_all_verified
            if bool(r.get("h30_alt3v2_correct")) or bool(r.get("h30_alt3_correct"))
        )

        result["alt2_stats"] = {
            "all": {
                "total": len(alt2_all),
                "verified": len(alt2_all_verified),
                "correct": alt2_all_correct,
                "accuracy": _acc(alt2_all_correct, len(alt2_all_verified)),
            },
            "trade_eligible": {
                "total": len(alt2_eligible),
                "verified": len(alt2_eligible_verified),
                "correct": alt2_eligible_correct,
                "accuracy": _acc(alt2_eligible_correct, len(alt2_eligible_verified)),
            },
        }
        result["alt3_stats"] = {
            "all": {
                "total": len(alt3_all),
                "verified": len(alt3_all_verified),
                "correct": alt3_all_correct,
                "accuracy": _acc(alt3_all_correct, len(alt3_all_verified)),
            },
        }
        alt4_all = [r for r in enriched_rows if r.get("h30_alt4_direction") is not None]
        alt4_all_verified = [r for r in alt4_all if r.get("h30_alt4_correct") is not None]
        alt4_all_correct = sum(1 for r in alt4_all_verified if bool(r.get("h30_alt4_correct")))
        alt4_eligible = [r for r in alt4_all if bool(r.get("h30_alt4_trade_eligible"))]
        alt4_eligible_verified = [r for r in alt4_eligible if r.get("h30_alt4_correct") is not None]
        alt4_eligible_correct = sum(1 for r in alt4_eligible_verified if bool(r.get("h30_alt4_correct")))
        result["alt4_stats"] = {
            "all": {
                "total": len(alt4_all),
                "verified": len(alt4_all_verified),
                "correct": alt4_all_correct,
                "accuracy": _acc(alt4_all_correct, len(alt4_all_verified)),
            },
            "trade_eligible": {
                "total": len(alt4_eligible),
                "verified": len(alt4_eligible_verified),
                "correct": alt4_eligible_correct,
                "accuracy": _acc(alt4_eligible_correct, len(alt4_eligible_verified)),
            },
        }
        result["rows"] = enriched_rows
        result["total"] = len(enriched_rows) if recommended_only else result.get("total", len(enriched_rows))
        result["recommended_windows_utc"] = get_recommended_window_labels()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/forecasts-accuracy")
async def get_forecasts_accuracy(
    symbol: Optional[str] = None,
    hours: int = Query(168, ge=1, le=720),
):
    """Get forecast accuracy statistics."""
    db = get_db()
    try:
        from app.storage.forecast_repo import ForecastRepo
        repo = ForecastRepo(db)
        return repo.get_accuracy_stats(symbol=symbol, hours=hours)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


# ── Forecast Optimizer API ────────────────────────────────────────────


def _run_optimizer_thread(days: int, horizon: Optional[int], symbol: Optional[str], quick: bool):
    """Background thread to run parameter optimization."""
    global _optimizer_state
    try:
        _optimizer_state["status"] = "running"
        _optimizer_state["started_at"] = datetime.now(timezone.utc).isoformat()
        _optimizer_state["error"] = None
        _optimizer_state["results"] = None

        import itertools
        import time as _time
        from app.models.forecast import FORECAST_HORIZONS
        from app.market_data.indicators import rsi as calc_rsi, sma as calc_sma

        QUICK_GRID = {
            "ma_fast": [10, 20, 30],
            "ma_slow": [50, 100, 200],
            "momentum_lookback": [5, 10, 20],
            "rsi_period": [14],
            "rsi_lookback": [3],
            "min_ratio": [0.2],
        }
        FULL_GRID = {
            "ma_fast": [8, 12, 20, 30, 50],
            "ma_slow": [30, 50, 100, 150, 200],
            "momentum_lookback": [3, 6, 10, 15, 24],
            "rsi_period": [7, 10, 14, 21],
            "rsi_lookback": [2, 3, 5],
            "min_ratio": [0.15, 0.2, 0.3],
        }
        HORIZON_TF = {
            30: ("M15", None),
            60: ("H1", "M15"),
            240: ("H4", "H1"),
            1440: ("H4", "H1"),
        }
        MIN_BARS = 30

        grid = QUICK_GRID if quick else FULL_GRID
        keys = sorted(grid.keys())
        combos = []
        for vals in itertools.product(*(grid[k] for k in keys)):
            c = dict(zip(keys, vals))
            if c["ma_fast"] >= c["ma_slow"]:
                continue
            combos.append(c)

        horizons = [horizon] if horizon else FORECAST_HORIZONS
        from app.models.bot_settings import DEFAULT_SYMBOLS
        symbols = [symbol.upper()] if symbol else DEFAULT_SYMBOLS

        _optimizer_state["total_combos"] = len(combos) * len(horizons)

        db = get_db()
        until = datetime.now(timezone.utc)
        since = until - timedelta(days=days + 2)
        backtest_start = until - timedelta(days=days)

        # Fetch data
        tf_mins = {"M15": 15, "H1": 60, "H4": 240}
        data = {}
        for sym in symbols:
            data[sym] = {}
            for tf, interval in tf_mins.items():
                all_rows = []
                cursor = since.isoformat()
                for _ in range(20):
                    res = (
                        db.client.table("market_snapshots")
                        .select("ts, close")
                        .eq("symbol", sym)
                        .eq("timeframe", tf)
                        .gte("ts", cursor)
                        .lte("ts", until.isoformat())
                        .order("ts", desc=False)
                        .limit(1000)
                        .execute()
                    )
                    rows = res.data or []
                    if not rows:
                        break
                    all_rows.extend(rows)
                    if len(rows) < 1000:
                        break
                    cursor = rows[-1]["ts"]
                seen = {}
                for r in all_rows:
                    if r.get("close") is None:
                        continue
                    dt = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
                    epoch = int(dt.timestamp())
                    rounded = epoch - (epoch % (interval * 60))
                    key = datetime.fromtimestamp(rounded, tz=timezone.utc).isoformat()
                    seen[key] = {"ts": key, "close": float(r["close"])}
                data[sym][tf] = sorted(seen.values(), key=lambda x: x["ts"])

        # Inline voter functions
        def v_ma_cross(closes, fp, sp):
            if len(closes) < sp: return 0
            f = calc_sma(closes, fp); s = calc_sma(closes, sp)
            if f is None or s is None: return 0
            return 1 if f > s else (-1 if f < s else 0)

        def v_rsi_trend(closes, per=14, lb=3):
            if len(closes) < per + 1 + lb: return 0
            now = calc_rsi(closes, per); prev = calc_rsi(closes[:-lb], per)
            if now is None or prev is None: return 0
            if now > 50 and now > prev: return 1
            if now < 50 and now < prev: return -1
            return 0

        def v_rsi_extreme(closes, per=14):
            if len(closes) < per + 1: return 0
            v = calc_rsi(closes, per)
            if v is None: return 0
            if v <= 30: return 1
            if v >= 70: return -1
            return 0

        def v_price_vs_ma(closes, mp):
            if len(closes) < mp: return 0
            ma = calc_sma(closes, mp)
            if ma is None or ma == 0: return 0
            return 1 if closes[-1] > ma else (-1 if closes[-1] < ma else 0)

        def v_momentum(closes, lb):
            if len(closes) < lb + 1: return 0
            return 1 if closes[-1] > closes[-(lb + 1)] else (-1 if closes[-1] < closes[-(lb + 1)] else 0)

        # Run grid search
        all_results = {}
        progress = 0
        for h in horizons:
            primary_tf, secondary_tf = HORIZON_TF[h]
            h_results = []
            for params in combos:
                total = 0; correct = 0
                for sym in symbols:
                    m15_bars = data.get(sym, {}).get("M15", [])
                    if len(m15_bars) < MIN_BARS + 10: continue
                    for i in range(MIN_BARS, len(m15_bars)):
                        bar = m15_bars[i]
                        bar_dt = datetime.fromisoformat(bar["ts"].replace("Z", "+00:00"))
                        if bar_dt < backtest_start: continue
                        if i % 4 != 0: continue
                        # build closes
                        if primary_tf == "M15":
                            closes = [b["close"] for b in m15_bars[:i]]
                        else:
                            cts = m15_bars[i - 1]["ts"] if i > 0 else ""
                            tf_bars = data.get(sym, {}).get(primary_tf, [])
                            closes = [b["close"] for b in tf_bars if b["ts"] <= cts]
                        if len(closes) < MIN_BARS and secondary_tf:
                            cts = m15_bars[i - 1]["ts"] if i > 0 else ""
                            tf_bars = data.get(sym, {}).get(secondary_tf, [])
                            closes = [b["close"] for b in tf_bars if b["ts"] <= cts]
                        if len(closes) < 15: continue
                        votes = []
                        votes.append(v_ma_cross(closes, params["ma_fast"], params["ma_slow"]))
                        votes.append(v_rsi_trend(closes, params["rsi_period"], params["rsi_lookback"]))
                        if h <= 240: votes.append(v_rsi_extreme(closes, params["rsi_period"]))
                        votes.append(v_price_vs_ma(closes, params["ma_fast"]))
                        votes.append(v_momentum(closes, params["momentum_lookback"]))
                        if secondary_tf:
                            cts2 = m15_bars[i - 1]["ts"] if i > 0 else ""
                            sc = [b["close"] for b in data.get(sym, {}).get(secondary_tf, []) if b["ts"] <= cts2]
                            if len(sc) >= params["ma_slow"]:
                                votes.append(v_ma_cross(sc, params["ma_fast"], params["ma_slow"]))
                        t = len(votes); ups = sum(1 for v in votes if v > 0); downs = sum(1 for v in votes if v < 0)
                        net = ups - downs; ratio = abs(net) / t if t else 0
                        if ratio < params["min_ratio"]: continue
                        direction = "up" if net > 0 else "down"
                        bars_ahead = h // 15; target_idx = i + bars_ahead
                        if target_idx >= len(m15_bars): continue
                        actual_price = m15_bars[target_idx]["close"]; base_price = closes[-1]
                        pc = actual_price - base_price
                        if abs(pc) < 1e-6: ad = "neutral"
                        else: ad = "up" if pc > 0 else "down"
                        if direction == ad: correct += 1
                        total += 1
                acc = correct / total if total > 0 else 0
                h_results.append({**params, "total": total, "correct": correct, "accuracy": acc})
                progress += 1
                _optimizer_state["progress"] = progress

            h_results.sort(key=lambda r: (r["accuracy"], r["total"]), reverse=True)

            # Current defaults for comparison
            cur = {"ma_fast": 20, "ma_slow": 50, "momentum_lookback": 10, "rsi_period": 14, "rsi_lookback": 3, "min_ratio": 0.2}
            if h == 1440: cur.update(ma_fast=50, ma_slow=200, momentum_lookback=24)
            cur_match = [r for r in h_results if r["ma_fast"] == cur["ma_fast"] and r["ma_slow"] == cur["ma_slow"]]
            cur_acc = cur_match[0]["accuracy"] if cur_match else 0

            all_results[str(h)] = {
                "top": h_results[:20],
                "current_accuracy": cur_acc,
                "current_params": cur,
                "total_combos": len(combos),
            }

        _optimizer_state["results"] = all_results
        _optimizer_state["status"] = "done"
        _optimizer_state["finished_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        _optimizer_state["status"] = "error"
        _optimizer_state["error"] = str(e)
        _optimizer_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        logger.exception(f"Optimizer failed: {e}")


class OptimizerRequest(BaseModel):
    days: int = 30
    horizon: Optional[int] = None
    symbol: Optional[str] = None
    quick: bool = True


@app.post("/api/optimizer/run")
async def run_optimizer(req: OptimizerRequest):
    """Start a parameter optimization run in the background."""
    global _optimizer_state
    if _optimizer_state["status"] == "running":
        return {"error": "Optimizer already running", "status": "running"}
    _optimizer_state = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "params": {"days": req.days, "horizon": req.horizon, "symbol": req.symbol, "quick": req.quick},
        "results": None,
        "error": None,
        "progress": 0,
        "total_combos": 0,
    }
    t = threading.Thread(
        target=_run_optimizer_thread,
        args=(req.days, req.horizon, req.symbol, req.quick),
        daemon=True,
    )
    t.start()
    return {"status": "started", "params": _optimizer_state["params"]}


@app.get("/api/optimizer/status")
async def optimizer_status():
    """Get current optimizer status and results."""
    return _optimizer_state


# ========== Adaptive Forecast Gate Status ==========

_forecast_gate_instance = None
_market_data_fetcher = None


def set_forecast_gate(gate):
    """Set the forecast gate instance from main.py."""
    global _forecast_gate_instance
    _forecast_gate_instance = gate


def set_market_data_fetcher(fetcher):
    """Set the IBKRFetcher instance from main.py for reconnect support."""
    global _market_data_fetcher
    _market_data_fetcher = fetcher


@app.get("/api/forecast-gate/status")
async def forecast_gate_status():
    """Get adaptive forecast gate status - rolling accuracy per pair."""
    if _forecast_gate_instance is None:
        # Fallback: create temporary gate to read from DB
        try:
            from app.forecast.gate import AdaptiveForecastGate
            db = SupabaseDB()
            gate = AdaptiveForecastGate(db=db)
            gate._refresh_accuracy()
            return gate.get_status()
        except Exception as e:
            return {"error": str(e), "enabled": False}
    
    # Force refresh if stale
    _forecast_gate_instance.refresh_if_needed()
    return _forecast_gate_instance.get_status()


# ========== Binary Signals API ==========

@app.get("/api/binary-signals")
async def binary_signals():
    """
    Quality-filtered forecast signals for binary options.
    
    Returns all pairs with H30 and H60 horizon data, each checked against
    the quality filter (confidence=MEDIUM, aligned>=4, trading hours).
    
    Empirical basis: 1000-sample analysis (2026-02-24)
    - MEDIUM + aligned>=4 = 86% accuracy on H30
    - Trading hours 08-11, 20-23 UTC = best performance
    """
    db = SupabaseDB()
    now = datetime.now(timezone.utc)
    current_hour = now.hour
    
    # Quality filter config (from gate or env defaults)
    gate = _forecast_gate_instance
    min_aligned = int(os.getenv("FORECAST_GATE_MIN_ALIGNED", "4"))
    required_confidence = os.getenv("FORECAST_GATE_REQUIRED_CONFIDENCE", "medium").lower()
    hours_filter_enabled = os.getenv("FORECAST_GATE_HOURS_FILTER", "1") == "1"
    default_hours = "08,09,10,11,20,21,22,23"
    trading_hours_str = os.getenv("FORECAST_GATE_TRADING_HOURS", default_hours)
    trading_hours = set()
    for part in trading_hours_str.split(","):
        part = part.strip()
        if part.isdigit():
            h = int(part)
            if 0 <= h <= 23:
                trading_hours.add(h)
    
    if gate:
        min_aligned = gate._min_aligned
        required_confidence = gate._required_confidence
        hours_filter_enabled = gate._hours_filter_enabled
        trading_hours = gate._trading_hours
    
    in_trading_hours = not hours_filter_enabled or current_hour in trading_hours
    
    # Get latest forecasts from DB (last 30 min to cover recent cycle)
    try:
        cutoff = (now - timedelta(minutes=60)).isoformat()
        res = db.client.table("price_forecasts").select(
            "symbol, ts_utc, base_price, "
            "h30_direction, h30_confidence, h30_strength, h30_aligned, h30_total, "
            "h60_direction, h60_confidence, h60_strength, h60_aligned, h60_total, "
            "dominant_direction, all_aligned"
        ).gte("ts_utc", cutoff).order("ts_utc", desc=True).limit(200).execute()
        rows = res.data or []
    except Exception as e:
        return {"error": str(e), "pairs": [], "summary": {}}
    
    # Deduplicate: latest forecast per symbol
    latest_by_symbol: Dict[str, Dict] = {}
    for row in rows:
        sym = row.get("symbol")
        if sym and sym not in latest_by_symbol:
            latest_by_symbol[sym] = row
    
    def _check_horizon(row: Dict, prefix: str) -> Dict:
        """Check quality filter for one horizon."""
        direction = row.get(f"{prefix}_direction") or "neutral"
        confidence = (row.get(f"{prefix}_confidence") or "low").lower()
        strength = float(row.get(f"{prefix}_strength") or 0)
        aligned_count = int(row.get(f"{prefix}_aligned") or 0)
        total = int(row.get(f"{prefix}_total") or 0)
        
        reasons = []
        if direction == "neutral":
            reasons.append("neutral")
        if confidence != required_confidence:
            reasons.append(f"conf={confidence}")
        if aligned_count < min_aligned:
            reasons.append(f"aligned={aligned_count}<{min_aligned}")
        if hours_filter_enabled and current_hour not in trading_hours:
            reasons.append(f"hour={current_hour}")
        
        passed = len(reasons) == 0
        
        return {
            "direction": direction,
            "confidence": confidence,
            "strength": round(strength, 3),
            "aligned": aligned_count,
            "total": total,
            "passed": passed,
            "reasons": reasons,
        }
    
    # Build response for each pair
    pairs = []
    passed_h30 = 0
    passed_h60 = 0
    
    for sym, row in sorted(latest_by_symbol.items()):
        ts_str = row.get("ts_utc", "")
        try:
            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age_seconds = int((now - ts_dt).total_seconds())
        except Exception:
            age_seconds = -1
        
        h30 = _check_horizon(row, "h30")
        h60 = _check_horizon(row, "h60")
        
        if h30["passed"]:
            passed_h30 += 1
        if h60["passed"]:
            passed_h60 += 1
        
        pairs.append({
            "symbol": sym,
            "h30": h30,
            "h60": h60,
            "base_price": row.get("base_price"),
            "dominant_direction": row.get("dominant_direction"),
            "all_aligned": row.get("all_aligned"),
            "timestamp": ts_str,
            "age_seconds": age_seconds,
        })
    
    # Sort: passed signals first, then by aligned desc
    pairs.sort(key=lambda p: (
        -(1 if p["h30"]["passed"] or p["h60"]["passed"] else 0),
        -(p["h30"]["aligned"] + p["h60"]["aligned"]),
    ))
    
    # Get accuracy stats for verified forecasts (last 48h)
    accuracy_stats = {}
    try:
        acc_cutoff = (now - timedelta(hours=48)).isoformat()
        acc_res = db.client.table("price_forecasts").select(
            "symbol, h30_correct, h30_confidence, h30_aligned, "
            "h60_correct, h60_confidence, h60_aligned"
        ).not_.is_("verified_at", "null").gte("ts_utc", acc_cutoff).execute()
        acc_rows = acc_res.data or []
        
        # Global accuracy for passed-filter forecasts
        h30_passed_correct = 0
        h30_passed_total = 0
        h60_passed_correct = 0
        h60_passed_total = 0
        
        for r in acc_rows:
            h30_conf = (r.get("h30_confidence") or "").lower()
            h30_al = int(r.get("h30_aligned") or 0)
            if h30_conf == required_confidence and h30_al >= min_aligned:
                if r.get("h30_correct") is not None:
                    h30_passed_total += 1
                    if r["h30_correct"]:
                        h30_passed_correct += 1
            
            h60_conf = (r.get("h60_confidence") or "").lower()
            h60_al = int(r.get("h60_aligned") or 0)
            if h60_conf == required_confidence and h60_al >= min_aligned:
                if r.get("h60_correct") is not None:
                    h60_passed_total += 1
                    if r["h60_correct"]:
                        h60_passed_correct += 1
        
        accuracy_stats = {
            "h30": {
                "correct": h30_passed_correct,
                "total": h30_passed_total,
                "accuracy": round(h30_passed_correct / h30_passed_total, 3) if h30_passed_total > 0 else None,
            },
            "h60": {
                "correct": h60_passed_correct,
                "total": h60_passed_total,
                "accuracy": round(h60_passed_correct / h60_passed_total, 3) if h60_passed_total > 0 else None,
            },
        }
    except Exception:
        pass
    
    return {
        "pairs": pairs,
        "summary": {
            "total_pairs": len(pairs),
            "passed_h30": passed_h30,
            "passed_h60": passed_h60,
            "current_hour_utc": current_hour,
            "in_trading_hours": in_trading_hours,
            "trading_hours_utc": sorted(trading_hours),
            "updated_at": now.isoformat(),
        },
        "filter_config": {
            "required_confidence": required_confidence,
            "min_aligned": min_aligned,
            "hours_filter_enabled": hours_filter_enabled,
            "trading_hours_utc": sorted(trading_hours),
        },
        "accuracy_48h": accuracy_stats,
    }


# ── Signal Lifecycle Status ────────────────────────────────────────────

# Global reference to lifecycle manager (set from main.py or startup)
_signal_lifecycle_manager = None

# Lazy Supabase client for dashboard container
_dashboard_supabase = None
def _get_supabase_client():
    global _dashboard_supabase
    if _dashboard_supabase is None:
        _dashboard_supabase = SupabaseDB().client
    return _dashboard_supabase


def set_signal_lifecycle_manager(manager):
    global _signal_lifecycle_manager
    _signal_lifecycle_manager = manager


@app.get("/api/signal-lifecycle")
def signal_lifecycle_status():
    """Get signal lifecycle status — dedup, cooldown, blacklist hours.
    Reads from in-memory manager if available, otherwise from Supabase."""
    from app.forecast.recommended_windows import get_recommended_window_labels
    if _signal_lifecycle_manager is not None:
        data = _signal_lifecycle_manager.get_status()
        data["recommended_windows_utc"] = get_recommended_window_labels()
        return data
    # Fallback: read persisted state from Supabase (dashboard runs in separate container)
    try:
        from app.forecast.signal_lifecycle import SignalLifecycleManager
        data = SignalLifecycleManager.load_from_supabase(_get_supabase_client())
        if data:
            data["recommended_windows_utc"] = get_recommended_window_labels()
            return data
    except Exception as exc:
        pass
    return {"enabled": False, "message": "lifecycle state not available"}


@app.get("/api/signal-lifecycle/{symbol}")
def signal_lifecycle_symbol(symbol: str):
    """Get lifecycle status for a specific symbol."""
    sym = symbol.upper()
    if _signal_lifecycle_manager is not None:
        return {"symbol": sym, "status": _signal_lifecycle_manager.get_signal_status(sym)}
    # Fallback: read from Supabase
    try:
        from app.forecast.signal_lifecycle import SignalLifecycleManager
        data = SignalLifecycleManager.load_from_supabase(_get_supabase_client())
        if data and "symbols" in data:
            sym_data = data["symbols"].get(sym)
            if sym_data:
                return {"symbol": sym, **sym_data}
    except Exception:
        pass
    return {"symbol": sym, "status": "unknown"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
