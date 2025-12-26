"""
Broker Account API.

Provides real-time account data from IB Gateway for the frontend dashboard.
"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from ib_insync import IB

logger = logging.getLogger(__name__)


class BrokerAccountAPI:
    """
    Fetches real account data from IB Gateway.
    
    Used by the /api/admin/broker frontend endpoint.
    """
    
    def __init__(self):
        self._ib: Optional[IB] = None
        self._last_fetch: Optional[datetime] = None
        self._cached_data: Optional[Dict[str, Any]] = None
        self._cache_ttl_seconds = 5  # Cache for 5 seconds to avoid hammering IB
    
    def _connect(self) -> bool:
        """Connect to IB Gateway if not already connected."""
        if self._ib and self._ib.isConnected():
            return True
        
        try:
            host = os.getenv("IB_GATEWAY_HOST", "127.0.0.1")
            port = int(os.getenv("IB_GATEWAY_PORT", "4004"))
            client_id = int(os.getenv("IB_CLIENT_ID_BROKER_API", "160"))
            
            self._ib = IB()
            self._ib.connect(host, port, clientId=client_id, timeout=10, readonly=True)
            logger.info(f"BrokerAccountAPI connected to IB Gateway: {host}:{port}")
            return True
        except Exception as e:
            logger.warning(f"BrokerAccountAPI: Failed to connect to IB Gateway: {e}")
            self._ib = None
            return False
    
    def _disconnect(self):
        """Disconnect from IB Gateway."""
        if self._ib:
            try:
                self._ib.disconnect()
            except Exception:
                pass
            self._ib = None
    
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
        
        # Try to connect
        connected = self._connect()
        
        if not connected or not self._ib:
            return self._get_disconnected_response()
        
        try:
            # Fetch all data
            account_summary = self._fetch_account_summary()
            positions = self._fetch_positions()
            orders = self._fetch_open_orders()
            
            # Parse account values
            account_values = self._parse_account_summary(account_summary)
            
            # Calculate P&L from positions
            unrealized_pnl = sum(p.get("unrealizedPnl", 0) for p in positions)
            
            result = {
                "account": {
                    "accountId": account_values.get("account_id", "Unknown"),
                    "accountType": account_values.get("account_type", "Unknown"),
                    "currency": account_values.get("currency", "USD"),
                    "equity": account_values.get("net_liquidation", 0),
                    "availableFunds": account_values.get("available_funds", 0),
                    "buyingPower": account_values.get("buying_power", 0),
                    "marginUsed": account_values.get("margin_used", 0),
                    "marginAvailable": account_values.get("margin_available", 0),
                    "unrealizedPnl": unrealized_pnl,
                    "dailyPnl": account_values.get("daily_pnl", 0),
                    "leverage": account_values.get("leverage", 0),
                    "connected": True,
                    "lastUpdate": now.isoformat(),
                },
                "connection": {
                    "ibGateway": "connected",
                    "dataFeed": "live",
                    "tradingEnabled": True,  # Will be overridden by bot_settings
                    "mode": "paper" if "DU" in account_values.get("account_id", "") else "live",
                    "lastHeartbeat": now.isoformat(),
                },
                "positions": positions,
                "orders": orders,
                "openPositions": len(positions),
                "pendingOrders": len(orders),
            }
            
            self._cached_data = result
            self._last_fetch = now
            
            return result
            
        except Exception as e:
            logger.error(f"BrokerAccountAPI: Error fetching data: {e}")
            return self._get_disconnected_response()
    
    def _fetch_account_summary(self) -> List[Any]:
        """Fetch account summary from IB."""
        if not self._ib:
            return []
        try:
            return self._ib.accountSummary()
        except Exception as e:
            logger.error(f"Error fetching account summary: {e}")
            return []
    
    def _fetch_positions(self) -> List[Dict[str, Any]]:
        """Fetch open positions from IB."""
        if not self._ib:
            return []
        try:
            raw_positions = self._ib.positions()
            positions = []
            
            for p in raw_positions:
                contract = getattr(p, "contract", None)
                if not contract:
                    continue
                
                # Get market value if available
                avg_cost = getattr(p, "avgCost", 0) or 0
                position_size = getattr(p, "position", 0) or 0
                
                positions.append({
                    "symbol": getattr(contract, "symbol", ""),
                    "secType": getattr(contract, "secType", ""),
                    "currency": getattr(contract, "currency", ""),
                    "position": float(position_size),
                    "avgCost": float(avg_cost),
                    "marketValue": float(position_size * avg_cost),
                    "unrealizedPnl": 0,  # Would need portfolio data for this
                })
            
            return positions
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []
    
    def _fetch_open_orders(self) -> List[Dict[str, Any]]:
        """Fetch open orders from IB."""
        if not self._ib:
            return []
        try:
            raw_orders = self._ib.openOrders()
            orders = []
            
            for o in raw_orders:
                contract = getattr(o, "contract", None)
                order = getattr(o, "order", None) if hasattr(o, "order") else o
                
                orders.append({
                    "orderId": getattr(order, "orderId", 0),
                    "symbol": getattr(contract, "symbol", "") if contract else "",
                    "action": getattr(order, "action", ""),
                    "quantity": getattr(order, "totalQuantity", 0),
                    "orderType": getattr(order, "orderType", ""),
                    "limitPrice": getattr(order, "lmtPrice", None),
                    "status": getattr(o, "status", "Unknown"),
                })
            
            return orders
        except Exception as e:
            logger.error(f"Error fetching open orders: {e}")
            return []
    
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
                # Determine account type from ID
                if account.startswith("DU"):
                    values["account_type"] = "PAPER"
                elif account.startswith("U"):
                    values["account_type"] = "LIVE"
                else:
                    values["account_type"] = "UNKNOWN"
            
            # Parse numeric values
            try:
                num_value = float(value)
            except (ValueError, TypeError):
                num_value = 0
            
            # Map IB tags to our fields
            if tag == "NetLiquidation" and currency == "USD":
                values["net_liquidation"] = num_value
            elif tag == "AvailableFunds" and currency == "USD":
                values["available_funds"] = num_value
            elif tag == "BuyingPower" and currency == "USD":
                values["buying_power"] = num_value
            elif tag == "MaintMarginReq" and currency == "USD":
                values["margin_used"] = num_value
            elif tag == "ExcessLiquidity" and currency == "USD":
                values["margin_available"] = num_value
            elif tag == "GrossPositionValue" and currency == "USD":
                values["gross_position"] = num_value
            elif tag == "RealizedPnL" and currency == "USD":
                values["daily_pnl"] = num_value
            elif tag == "UnrealizedPnL" and currency == "USD":
                values["unrealized_pnl"] = num_value
            elif tag == "Leverage-S":
                values["leverage"] = num_value
            elif tag == "AccountType":
                if value == "INDIVIDUAL":
                    values["account_type"] = "MARGIN"
                elif value:
                    values["account_type"] = value
        
        # Calculate leverage if not provided
        if "leverage" not in values and values.get("net_liquidation", 0) > 0:
            gross = values.get("gross_position", 0)
            equity = values.get("net_liquidation", 1)
            values["leverage"] = gross / equity if equity > 0 else 0
        
        # Set currency
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
