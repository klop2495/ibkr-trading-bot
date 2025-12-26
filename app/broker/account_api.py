"""
Broker Account API.

Provides real-time account data from IB Gateway for the frontend dashboard.
Uses nest_asyncio to handle event loop conflicts with FastAPI.
"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import nest_asyncio
nest_asyncio.apply()

from ib_insync import IB, util

logger = logging.getLogger(__name__)


class BrokerAccountAPI:
    """
    Fetches real account data from IB Gateway.
    
    Used by the /api/broker endpoint for frontend dashboard.
    """
    
    def __init__(self):
        self._last_fetch: Optional[datetime] = None
        self._cached_data: Optional[Dict[str, Any]] = None
        self._cache_ttl_seconds = 5
    
    def get_account_data(self) -> Dict[str, Any]:
        """
        Get comprehensive account data from IB Gateway.
        
        Returns:
            Dict with account info, positions, orders, and connection status.
        """
        now = datetime.now(timezone.utc)
        
        # Check cache
        if (self._cached_data and self._last_fetch and 
            (now - self._last_fetch).total_seconds() < self._cache_ttl_seconds):
            return self._cached_data
        
        # Connect, fetch, disconnect pattern (avoids async issues)
        ib = IB()
        connected = False
        
        try:
            host = os.getenv("IB_GATEWAY_HOST", "127.0.0.1")
            port = int(os.getenv("IB_GATEWAY_PORT", "4004"))
            client_id = int(os.getenv("IB_CLIENT_ID_BROKER_API", "160"))
            
            # Use util.run for sync execution
            ib.connect(host, port, clientId=client_id, timeout=10, readonly=True)
            connected = ib.isConnected()
            
            if not connected:
                logger.warning("BrokerAccountAPI: Connection returned but not connected")
                return self._get_disconnected_response()
            
            logger.info(f"BrokerAccountAPI: Connected to IB Gateway {host}:{port}")
            
            # Fetch all data
            account_summary = ib.accountSummary()
            positions = ib.positions()
            orders = ib.openOrders()
            
            # Parse account values
            account_values = self._parse_account_summary(account_summary)
            
            # Parse positions
            parsed_positions = []
            for p in positions:
                contract = getattr(p, "contract", None)
                if contract:
                    parsed_positions.append({
                        "symbol": getattr(contract, "symbol", ""),
                        "secType": getattr(contract, "secType", ""),
                        "currency": getattr(contract, "currency", ""),
                        "position": float(getattr(p, "position", 0) or 0),
                        "avgCost": float(getattr(p, "avgCost", 0) or 0),
                    })
            
            # Parse orders
            parsed_orders = []
            for trade in orders:
                contract = trade.contract if hasattr(trade, 'contract') else None
                order = trade.order if hasattr(trade, 'order') else trade
                parsed_orders.append({
                    "orderId": getattr(order, "orderId", 0),
                    "symbol": getattr(contract, "symbol", "") if contract else "",
                    "action": getattr(order, "action", ""),
                    "quantity": float(getattr(order, "totalQuantity", 0) or 0),
                    "orderType": getattr(order, "orderType", ""),
                    "status": getattr(trade, "orderStatus", {}).status if hasattr(trade, "orderStatus") else "Unknown",
                })
            
            result = {
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
                "openPositions": len(parsed_positions),
                "pendingOrders": len(parsed_orders),
            }
            
            self._cached_data = result
            self._last_fetch = now
            
            return result
            
        except Exception as e:
            logger.error(f"BrokerAccountAPI: Error: {e}")
            return self._get_disconnected_response()
        finally:
            if connected:
                try:
                    ib.disconnect()
                except:
                    pass
    
    def _parse_account_summary(self, summary: List[Any]) -> Dict[str, Any]:
        """Parse account summary values into a dict."""
        values = {}
        
        for item in summary:
            tag = getattr(item, "tag", "")
            value = getattr(item, "value", "")
            currency = getattr(item, "currency", "")
            account = getattr(item, "account", "")
            
            # Store account ID
            if account and "account_id" not in values:
                values["account_id"] = account
                if account.startswith("DU"):
                    values["account_type"] = "PAPER"
                elif account.startswith("U"):
                    values["account_type"] = "LIVE"
                else:
                    values["account_type"] = "MARGIN"
            
            # Parse numeric values
            try:
                num_value = float(value)
            except (ValueError, TypeError):
                num_value = 0
            
            # Map IB tags to our fields (USD only)
            if currency == "USD" or currency == "":
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
        
        # Calculate leverage if not provided
        if "leverage" not in values and values.get("net_liquidation", 0) > 0:
            gross = values.get("gross_position", 0)
            equity = values.get("net_liquidation", 1)
            values["leverage"] = round(gross / equity, 2) if equity > 0 else 0
        
        values["currency"] = "USD"
        
        return values
    
    def _get_disconnected_response(self) -> Dict[str, Any]:
        """Return response when IB is not connected."""
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
            "openPositions": 0,
            "pendingOrders": 0,
        }


# Singleton instance
_broker_api: Optional[BrokerAccountAPI] = None


def get_broker_api() -> BrokerAccountAPI:
    """Get or create the broker API singleton."""
    global _broker_api
    if _broker_api is None:
        _broker_api = BrokerAccountAPI()
    return _broker_api
