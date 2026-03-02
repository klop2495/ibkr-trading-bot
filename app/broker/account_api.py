"""
Broker Account API.

Provides real-time account data from IB Gateway for the frontend dashboard.
"""

import os
import logging
import asyncio
from datetime import datetime, timezone
from threading import Lock, Thread
from typing import Optional, Dict, Any, List
from queue import Queue

logger = logging.getLogger(__name__)


class BrokerAccountAPI:
    """
    Fetches real account data from IB Gateway.
    """

    def __init__(self):
        self._last_fetch: Optional[datetime] = None
        self._cached_data: Optional[Dict[str, Any]] = None
        self._cache_ttl_seconds = 5
        self._lock = Lock()

    def get_account_data(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Get comprehensive account data from IB Gateway."""
        now = datetime.now(timezone.utc)

        # Check cache
        if not force_refresh and (
            self._cached_data
            and self._last_fetch
            and (now - self._last_fetch).total_seconds() < self._cache_ttl_seconds
        ):
            return self._cached_data

        with self._lock:
            # Double-check after lock
            if not force_refresh and (
                self._cached_data
                and self._last_fetch
                and (now - self._last_fetch).total_seconds() < self._cache_ttl_seconds
            ):
                return self._cached_data

            if force_refresh:
                self._cached_data = None
                self._last_fetch = None

            result = self._fetch_sync()

            if result["account"]["connected"]:
                self._cached_data = result
                self._last_fetch = now
            elif force_refresh:
                self._cached_data = None
                self._last_fetch = None

            return result

    def _fetch_sync(self) -> Dict[str, Any]:
        """Fetch from IB using a separate thread with its own event loop."""
        result_queue: Queue = Queue()

        def worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                from ib_insync import IB

                host = os.getenv("IB_GATEWAY_HOST", "127.0.0.1")
                port = int(os.getenv("IB_GATEWAY_PORT", "4004"))
                client_id = int(os.getenv("IB_CLIENT_ID_BROKER_API", "160"))

                ib = IB()

                try:
                    loop.run_until_complete(
                        ib.connectAsync(host, port, clientId=client_id, timeout=10, readonly=True)
                    )

                    if not ib.isConnected():
                        result_queue.put(self._get_disconnected_response())
                        return

                    logger.info(f"BrokerAccountAPI: Connected to {host}:{port}")

                    account_summary = ib.accountSummary()
                    positions = ib.positions()
                    orders = ib.openOrders()
                    executions = ib.reqExecutions()

                    account_values = self._parse_account_summary(account_summary)
                    parsed_positions = self._parse_positions(positions)
                    parsed_orders = self._parse_orders(orders)
                    parsed_executions = self._parse_executions(executions)

                    now = datetime.now(timezone.utc)

                    result_queue.put({
                        "account": {
                            "accountId": account_values.get("account_id", "Unknown"),
                            "accountType": account_values.get("account_type", "MARGIN"),
                            "currency": account_values.get("currency", "USD"),
                            "equity": account_values.get("net_liquidation", 0),
                            "availableFunds": account_values.get("available_funds", 0),
                            "buyingPower": account_values.get("buying_power", 0),
                            "marginUsed": account_values.get("margin_used", 0),
                            "marginAvailable": account_values.get("margin_available", 0),
                            "unrealizedPnl": account_values.get("unrealized_pnl", 0),
                            "dailyPnl": account_values.get("daily_pnl", 0),
                            "leverage": account_values.get("leverage", 0),
                            "connected": True,
                            "lastUpdate": now.isoformat(),
                        },
                        "connection": {
                            "ibGateway": "connected",
                            "dataFeed": "live",
                            "tradingEnabled": True,
                            "mode": "paper" if "DU" in account_values.get("account_id", "") else "live",
                            "lastHeartbeat": now.isoformat(),
                        },
                        "positions": parsed_positions,
                        "orders": parsed_orders,
                        "executions": parsed_executions,
                        "openPositions": len(parsed_positions),
                        "pendingOrders": len(parsed_orders),
                    })

                except Exception as e:
                    logger.error(f"BrokerAccountAPI IB error: {e}")
                    result_queue.put(self._get_disconnected_response())
                finally:
                    try:
                        if ib.isConnected():
                            ib.disconnect()
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"BrokerAccountAPI worker error: {e}")
                result_queue.put(self._get_disconnected_response())
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        thread = Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=15)

        if result_queue.empty():
            logger.error("BrokerAccountAPI: Timeout waiting for IB data")
            return self._get_disconnected_response()

        return result_queue.get()

    def _parse_positions(self, positions) -> List[Dict[str, Any]]:
        result = []
        for p in positions:
            contract = getattr(p, "contract", None)
            if contract:
                result.append({
                    "symbol": getattr(contract, "symbol", ""),
                    "secType": getattr(contract, "secType", ""),
                    "currency": getattr(contract, "currency", ""),
                    "position": float(getattr(p, "position", 0) or 0),
                    "avgCost": float(getattr(p, "avgCost", 0) or 0),
                })
        return result

    def _parse_orders(self, orders) -> List[Dict[str, Any]]:
        result = []
        for trade in orders:
            contract = trade.contract if hasattr(trade, "contract") else None
            order = trade.order if hasattr(trade, "order") else trade
            status = ""
            if hasattr(trade, "orderStatus") and trade.orderStatus:
                status = getattr(trade.orderStatus, "status", "Unknown")
            result.append({
                "orderId": getattr(order, "orderId", 0),
                "parentId": getattr(order, "parentId", None),
                "symbol": getattr(contract, "symbol", "") if contract else "",
                "action": getattr(order, "action", ""),
                "quantity": float(getattr(order, "totalQuantity", 0) or 0),
                "orderType": getattr(order, "orderType", ""),
                "auxPrice": getattr(order, "auxPrice", None),
                "lmtPrice": getattr(order, "lmtPrice", None),
                "status": status,
            })
        return result

    def _parse_executions(self, executions: List[Any]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for fill in executions or []:
            contract = getattr(fill, "contract", None)
            exec_obj = getattr(fill, "execution", None) or fill
            if contract and getattr(contract, "secType", "") == "CASH":
                symbol = f"{getattr(contract, 'symbol', '')}{getattr(contract, 'currency', '')}".upper()
            else:
                symbol = getattr(contract, "symbol", "") if contract else ""

            exec_time = getattr(exec_obj, "time", None)
            if isinstance(exec_time, datetime):
                if exec_time.tzinfo is None:
                    exec_time = exec_time.replace(tzinfo=timezone.utc)
                exec_time_iso = exec_time.astimezone(timezone.utc).isoformat()
            else:
                exec_time_iso = str(exec_time) if exec_time is not None else None

            result.append({
                "execId": str(getattr(exec_obj, "execId", "") or ""),
                "orderId": getattr(exec_obj, "orderId", None),
                "permId": getattr(exec_obj, "permId", None),
                "symbol": symbol or "UNKNOWN",
                "secType": getattr(contract, "secType", "") if contract else "",
                "side": str(getattr(exec_obj, "side", "") or ""),
                "shares": float(getattr(exec_obj, "shares", 0) or 0),
                "price": float(getattr(exec_obj, "price", 0) or 0),
                "avgPrice": float(getattr(exec_obj, "avgPrice", 0) or 0),
                "currency": getattr(contract, "currency", "") if contract else "",
                "time": exec_time_iso,
            })

        result.sort(key=lambda x: x.get("time") or "", reverse=True)
        return result[:20]

    def _parse_account_summary(self, summary) -> Dict[str, Any]:
        values = {}

        for item in summary:
            tag = getattr(item, "tag", "")
            value = getattr(item, "value", "")
            currency = getattr(item, "currency", "")
            account = getattr(item, "account", "")

            if account and "account_id" not in values:
                values["account_id"] = account
                if account.startswith("DU"):
                    values["account_type"] = "PAPER"
                elif account.startswith("U"):
                    values["account_type"] = "LIVE"
                else:
                    values["account_type"] = "MARGIN"

            try:
                num_value = float(value)
            except (ValueError, TypeError):
                num_value = 0

            if "currency" not in values and currency:
                values["currency"] = currency
            effective_currency = values.get("currency")
            if not effective_currency or currency == effective_currency or currency in ("BASE", ""):
                if tag == "NetLiquidation":
                    values["net_liquidation"] = num_value
                elif tag == "AvailableFunds":
                    values["available_funds"] = num_value
                elif tag == "BuyingPower":
                    values["buying_power"] = num_value
                elif tag == "MaintMarginReq":
                    values["margin_used"] = num_value
                elif tag == "ExcessLiquidity":
                    values["margin_available"] = num_value
                elif tag == "GrossPositionValue":
                    values["gross_position"] = num_value
                elif tag == "RealizedPnL":
                    values["daily_pnl"] = num_value
                elif tag == "UnrealizedPnL":
                    values["unrealized_pnl"] = num_value
                elif tag == "Leverage-S":
                    values["leverage"] = num_value

        if "leverage" not in values and values.get("net_liquidation", 0) > 0:
            gross = values.get("gross_position", 0)
            equity = values.get("net_liquidation", 1)
            values["leverage"] = round(gross / equity, 2) if equity > 0 else 0

        return values

    def _get_disconnected_response(self) -> Dict[str, Any]:
        return {
            "account": {
                "accountId": "NOT_CONNECTED",
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
            "executions": [],
            "openPositions": 0,
            "pendingOrders": 0,
        }


_broker_api: Optional[BrokerAccountAPI] = None


def get_broker_api() -> BrokerAccountAPI:
    global _broker_api
    if _broker_api is None:
        _broker_api = BrokerAccountAPI()
    return _broker_api
