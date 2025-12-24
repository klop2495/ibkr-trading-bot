"""
IB Gateway Diagnostics Script

P0-A: Diagnose order rejection/cancellation reasons.

Usage:
    python -m app.broker.ib_diagnostics [--symbol AUDUSD] [--quantity 35000]
    
Or via docker:
    docker exec ibkr-trading-bot python -m app.broker.ib_diagnostics
"""

import os
import sys
import time
import argparse
from datetime import datetime, timezone
from typing import Any, Optional

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class IBDiagnostics:
    """
    Comprehensive IB Gateway diagnostics.
    
    Logs all events:
    - errorEvent (code, message, reqId)
    - orderStatusEvent (status, filled, remaining)
    - execDetailsEvent (fills)
    - newOrderEvent
    - orderModifyEvent
    - cancelOrderEvent
    """
    
    def __init__(self, ib: Any, verbose: bool = True):
        self.ib = ib
        self.verbose = verbose
        self.events: list[dict] = []
        self.errors: list[dict] = []
        self._register_handlers()
    
    def _register_handlers(self) -> None:
        """Register all IB event handlers for diagnostics."""
        self.ib.errorEvent += self._on_error
        self.ib.orderStatusEvent += self._on_order_status
        self.ib.execDetailsEvent += self._on_exec_details
        self.ib.newOrderEvent += self._on_new_order
        self.ib.orderModifyEvent += self._on_order_modify
        self.ib.cancelOrderEvent += self._on_cancel_order
    
    def _unregister_handlers(self) -> None:
        """Unregister handlers."""
        self.ib.errorEvent -= self._on_error
        self.ib.orderStatusEvent -= self._on_order_status
        self.ib.execDetailsEvent -= self._on_exec_details
        self.ib.newOrderEvent -= self._on_new_order
        self.ib.orderModifyEvent -= self._on_order_modify
        self.ib.cancelOrderEvent -= self._on_cancel_order
    
    def _log(self, event_type: str, data: dict) -> None:
        """Log event."""
        ts = datetime.now(timezone.utc).isoformat()
        event = {"ts": ts, "type": event_type, **data}
        self.events.append(event)
        if self.verbose:
            print(f"[{ts}] {event_type}: {data}")
    
    def _on_error(self, reqId: int, errorCode: int, errorString: str, contract: Any) -> None:
        """Handle error event - CRITICAL for understanding rejects."""
        contract_str = ""
        if contract:
            contract_str = f"{getattr(contract, 'symbol', '')} {getattr(contract, 'secType', '')}"
        
        error_data = {
            "reqId": reqId,
            "errorCode": errorCode,
            "errorString": errorString,
            "contract": contract_str,
        }
        self.errors.append(error_data)
        self._log("ERROR", error_data)
        
        # Highlight critical errors
        if errorCode in (103, 104, 109, 110, 135, 136, 161, 200, 201, 202, 203, 399, 10147):
            print(f"\n{'='*60}")
            print(f"CRITICAL ERROR CODE {errorCode}")
            print(f"  Message: {errorString}")
            print(f"  ReqId: {reqId}")
            print(f"  Contract: {contract_str}")
            print(self._explain_error_code(errorCode))
            print(f"{'='*60}\n")
    
    def _explain_error_code(self, code: int) -> str:
        """Explain common IB error codes."""
        explanations = {
            103: "  -> Duplicate order ID. Solution: Get new reqId.",
            104: "  -> Cannot modify a filled order.",
            109: "  -> Price out of range.",
            110: "  -> Price does not conform to minimum price variation.",
            135: "  -> Cannot find order with ID. Parent order not found for bracket.",
            136: "  -> Cannot cancel order - already cancelled/filled.",
            161: "  -> Cancel attempted when order is not in cancellable state.",
            200: "  -> No security definition found. Contract not valid.",
            201: "  -> Order rejected. Check message for specific reason.",
            202: "  -> Order cancelled. Often means: insufficient margin, invalid qty, or market closed.",
            203: "  -> Market is closed or order not allowed at this time.",
            399: "  -> Order size below IDEALPRO minimum. Will route as odd lot (may be rejected).",
            10147: "  -> Order could not be transmitted. Check connection and permissions.",
        }
        return explanations.get(code, "  -> Unknown error code. Check IB documentation.")
    
    def _on_order_status(self, trade: Any) -> None:
        """Handle order status change."""
        order = trade.order
        status = trade.orderStatus
        
        data = {
            "orderId": order.orderId,
            "permId": order.permId,
            "status": status.status,
            "filled": status.filled,
            "remaining": status.remaining,
            "avgFillPrice": status.avgFillPrice,
            "lastFillPrice": status.lastFillPrice,
            "whyHeld": status.whyHeld if hasattr(status, 'whyHeld') else "",
            "action": order.action,
            "totalQuantity": order.totalQuantity,
            "orderType": order.orderType,
        }
        self._log("ORDER_STATUS", data)
        
        # Highlight important statuses
        if status.status in ("Inactive", "Cancelled", "ApiCancelled"):
            print(f"\n*** ORDER {status.status.upper()} ***")
            print(f"    OrderId: {order.orderId}")
            print(f"    Action: {order.action} {order.totalQuantity}")
            print(f"    WhyHeld: {status.whyHeld if hasattr(status, 'whyHeld') else 'N/A'}")
            print(f"    Check ERROR events above for reason!\n")
    
    def _on_exec_details(self, trade: Any, fill: Any) -> None:
        """Handle fill event."""
        data = {
            "orderId": trade.order.orderId,
            "execId": fill.execution.execId,
            "time": str(fill.execution.time),
            "shares": fill.execution.shares,
            "price": fill.execution.price,
            "side": fill.execution.side,
            "commission": fill.commissionReport.commission if fill.commissionReport else 0,
        }
        self._log("FILL", data)
    
    def _on_new_order(self, trade: Any) -> None:
        """Handle new order event."""
        order = trade.order
        data = {
            "orderId": order.orderId,
            "action": order.action,
            "totalQuantity": order.totalQuantity,
            "orderType": order.orderType,
            "lmtPrice": getattr(order, 'lmtPrice', None),
            "auxPrice": getattr(order, 'auxPrice', None),
            "parentId": getattr(order, 'parentId', 0),
            "transmit": getattr(order, 'transmit', True),
        }
        self._log("NEW_ORDER", data)
    
    def _on_order_modify(self, trade: Any) -> None:
        """Handle order modify event."""
        order = trade.order
        data = {
            "orderId": order.orderId,
            "action": order.action,
            "totalQuantity": order.totalQuantity,
        }
        self._log("ORDER_MODIFY", data)
    
    def _on_cancel_order(self, trade: Any) -> None:
        """Handle cancel order event."""
        order = trade.order
        data = {
            "orderId": order.orderId,
            "action": order.action,
            "totalQuantity": order.totalQuantity,
        }
        self._log("ORDER_CANCEL", data)
    
    def get_account_info(self) -> dict:
        """Get account information."""
        print("\n=== ACCOUNT INFO ===")
        
        # Account summary
        account_values = self.ib.accountSummary()
        important_tags = ['NetLiquidation', 'EquityWithLoanValue', 'BuyingPower', 
                         'TotalCashValue', 'AvailableFunds', 'MaintMarginReq']
        
        account_data = {}
        for av in account_values:
            if av.tag in important_tags:
                account_data[av.tag] = f"{av.value} {av.currency}"
                print(f"  {av.tag}: {av.value} {av.currency}")
        
        # Positions
        print("\n=== POSITIONS ===")
        positions = self.ib.positions()
        if positions:
            for p in positions:
                print(f"  {p.contract.symbol}{p.contract.currency}: {p.position} @ {p.avgCost}")
        else:
            print("  No open positions")
        
        # Open orders
        print("\n=== OPEN ORDERS ===")
        open_orders = self.ib.openOrders()
        if open_orders:
            for o in open_orders:
                print(f"  {o.orderId}: {o.action} {o.totalQuantity} {o.orderType}")
        else:
            print("  No open orders")
        
        return account_data
    
    def test_contract_qualification(self, symbol: str) -> bool:
        """Test if contract can be qualified."""
        print(f"\n=== QUALIFYING CONTRACT: {symbol} ===")
        
        from ib_insync import Forex
        
        # Parse symbol
        if len(symbol) == 6:
            base = symbol[:3]
            quote = symbol[3:]
            pair = f"{base}{quote}"
        else:
            pair = symbol
        
        contract = Forex(pair=pair)
        print(f"  Raw contract: {contract}")
        
        try:
            qualified = self.ib.qualifyContracts(contract)
            if qualified:
                c = qualified[0]
                print(f"  Qualified: conId={c.conId}, symbol={c.symbol}, "
                      f"currency={c.currency}, exchange={c.exchange}")
                return True
            else:
                print(f"  FAILED: Contract could not be qualified!")
                return False
        except Exception as e:
            print(f"  ERROR: {e}")
            return False
    
    def test_simple_market_order(
        self,
        symbol: str = "AUDUSD",
        quantity: float = 35000,
        side: str = "BUY",
        wait_seconds: int = 10,
    ) -> dict:
        """
        Test a simple market order and capture all events.
        
        Returns dict with results and all captured events/errors.
        """
        print(f"\n{'='*60}")
        print(f"TESTING SIMPLE MARKET ORDER")
        print(f"  Symbol: {symbol}")
        print(f"  Side: {side}")
        print(f"  Quantity: {quantity}")
        print(f"{'='*60}")
        
        from ib_insync import Forex, MarketOrder
        
        # Clear previous events
        self.events = []
        self.errors = []
        
        # Qualify contract
        if not self.test_contract_qualification(symbol):
            return {"success": False, "reason": "contract_not_qualified", "events": self.events, "errors": self.errors}
        
        # Create contract and order
        if len(symbol) == 6:
            pair = f"{symbol[:3]}{symbol[3:]}"
        else:
            pair = symbol
        
        contract = Forex(pair=pair)
        qualified = self.ib.qualifyContracts(contract)
        if qualified:
            contract = qualified[0]
        
        order = MarketOrder(side, quantity)
        
        print(f"\n=== PLACING ORDER ===")
        print(f"  Contract: {contract}")
        print(f"  Order: {order}")
        
        # Place order
        trade = self.ib.placeOrder(contract, order)
        order_id = trade.order.orderId
        print(f"  OrderId: {order_id}")
        
        # Wait and capture events
        print(f"\n=== WAITING {wait_seconds}s FOR EVENTS ===")
        for i in range(wait_seconds):
            self.ib.sleep(1)
            print(f"  ... {i+1}s (status: {trade.orderStatus.status})")
            
            # Check if terminal state
            if trade.orderStatus.status in ("Filled", "Cancelled", "Inactive"):
                break
        
        # Final status
        print(f"\n=== FINAL STATUS ===")
        print(f"  Status: {trade.orderStatus.status}")
        print(f"  Filled: {trade.orderStatus.filled}")
        print(f"  Remaining: {trade.orderStatus.remaining}")
        print(f"  AvgFillPrice: {trade.orderStatus.avgFillPrice}")
        
        # Summary
        print(f"\n=== EVENT SUMMARY ===")
        print(f"  Total events: {len(self.events)}")
        print(f"  Errors: {len(self.errors)}")
        
        if self.errors:
            print(f"\n=== ERRORS CAPTURED ===")
            for err in self.errors:
                print(f"  Code {err['errorCode']}: {err['errorString']}")
        
        return {
            "success": trade.orderStatus.status == "Filled",
            "final_status": trade.orderStatus.status,
            "order_id": order_id,
            "filled": trade.orderStatus.filled,
            "avg_price": trade.orderStatus.avgFillPrice,
            "events": self.events,
            "errors": self.errors,
        }
    
    def test_bracket_order(
        self,
        symbol: str = "AUDUSD",
        quantity: float = 35000,
        side: str = "BUY",
        sl_distance_pips: float = 20,
        tp_distance_pips: float = 40,
        wait_seconds: int = 15,
    ) -> dict:
        """
        Test a bracket order (parent + SL + TP).
        """
        print(f"\n{'='*60}")
        print(f"TESTING BRACKET ORDER")
        print(f"  Symbol: {symbol}")
        print(f"  Side: {side}")
        print(f"  Quantity: {quantity}")
        print(f"  SL: {sl_distance_pips} pips, TP: {tp_distance_pips} pips")
        print(f"{'='*60}")
        
        from ib_insync import Forex, Order
        
        # Clear events
        self.events = []
        self.errors = []
        
        # Qualify contract
        if not self.test_contract_qualification(symbol):
            return {"success": False, "reason": "contract_not_qualified"}
        
        # Get current price
        if len(symbol) == 6:
            pair = f"{symbol[:3]}{symbol[3:]}"
        else:
            pair = symbol
        
        contract = Forex(pair=pair)
        qualified = self.ib.qualifyContracts(contract)
        if qualified:
            contract = qualified[0]
        
        # Request market data for price
        self.ib.reqMktData(contract, '', False, False)
        self.ib.sleep(2)
        ticker = self.ib.ticker(contract)
        
        if ticker.midpoint():
            current_price = ticker.midpoint()
        elif ticker.last:
            current_price = ticker.last
        else:
            print("  Cannot get current price!")
            return {"success": False, "reason": "no_price"}
        
        print(f"  Current price: {current_price}")
        
        # Calculate SL/TP prices
        pip_value = 0.01 if "JPY" in symbol.upper() else 0.0001
        
        if side == "BUY":
            sl_price = round(current_price - (sl_distance_pips * pip_value), 5)
            tp_price = round(current_price + (tp_distance_pips * pip_value), 5)
        else:
            sl_price = round(current_price + (sl_distance_pips * pip_value), 5)
            tp_price = round(current_price - (tp_distance_pips * pip_value), 5)
        
        print(f"  SL price: {sl_price}")
        print(f"  TP price: {tp_price}")
        
        # Get order IDs
        parent_id = self.ib.client.getReqId()
        tp_id = self.ib.client.getReqId()
        sl_id = self.ib.client.getReqId()
        
        print(f"\n=== ORDER IDs ===")
        print(f"  Parent: {parent_id}")
        print(f"  TP: {tp_id}")
        print(f"  SL: {sl_id}")
        
        # Create orders
        opposite = "SELL" if side == "BUY" else "BUY"
        
        parent_order = Order(
            orderId=parent_id,
            action=side,
            totalQuantity=quantity,
            orderType="MKT",
            tif="GTC",
            transmit=False,
        )
        
        tp_order = Order(
            orderId=tp_id,
            action=opposite,
            totalQuantity=quantity,
            orderType="LMT",
            lmtPrice=tp_price,
            tif="GTC",
            parentId=parent_id,
            transmit=False,
        )
        
        sl_order = Order(
            orderId=sl_id,
            action=opposite,
            totalQuantity=quantity,
            orderType="STP",
            auxPrice=sl_price,
            tif="GTC",
            parentId=parent_id,
            transmit=True,  # This sends the entire bracket
        )
        
        print(f"\n=== PLACING BRACKET ===")
        
        # Place in sequence
        parent_trade = self.ib.placeOrder(contract, parent_order)
        print(f"  Parent placed: {parent_trade.order.orderId}")
        
        tp_trade = self.ib.placeOrder(contract, tp_order)
        print(f"  TP placed: {tp_trade.order.orderId}")
        
        sl_trade = self.ib.placeOrder(contract, sl_order)
        print(f"  SL placed (transmit=True): {sl_trade.order.orderId}")
        
        # Wait
        print(f"\n=== WAITING {wait_seconds}s ===")
        for i in range(wait_seconds):
            self.ib.sleep(1)
            print(f"  ... {i+1}s | Parent: {parent_trade.orderStatus.status} | "
                  f"TP: {tp_trade.orderStatus.status} | SL: {sl_trade.orderStatus.status}")
            
            if parent_trade.orderStatus.status in ("Filled", "Cancelled", "Inactive"):
                break
        
        # Final
        print(f"\n=== FINAL STATUS ===")
        print(f"  Parent: {parent_trade.orderStatus.status} (filled: {parent_trade.orderStatus.filled})")
        print(f"  TP: {tp_trade.orderStatus.status}")
        print(f"  SL: {sl_trade.orderStatus.status}")
        
        if self.errors:
            print(f"\n=== ERRORS ===")
            for err in self.errors:
                print(f"  [{err['reqId']}] Code {err['errorCode']}: {err['errorString']}")
        
        return {
            "success": parent_trade.orderStatus.status == "Filled",
            "parent_status": parent_trade.orderStatus.status,
            "tp_status": tp_trade.orderStatus.status,
            "sl_status": sl_trade.orderStatus.status,
            "errors": self.errors,
        }
    
    def shutdown(self) -> None:
        """Cleanup."""
        self._unregister_handlers()


def main():
    parser = argparse.ArgumentParser(description="IB Gateway Diagnostics")
    parser.add_argument("--host", default="ib-gateway", help="IB Gateway host")
    parser.add_argument("--port", type=int, default=4004, help="IB Gateway port")
    parser.add_argument("--client-id", type=int, default=200, help="Client ID for diagnostics")
    parser.add_argument("--symbol", default="AUDUSD", help="Symbol to test")
    parser.add_argument("--quantity", type=float, default=35000, help="Order quantity")
    parser.add_argument("--side", default="BUY", choices=["BUY", "SELL"], help="Order side")
    parser.add_argument("--test", default="info", choices=["info", "simple", "bracket"], 
                       help="Test type: info (account only), simple (market order), bracket")
    parser.add_argument("--wait", type=int, default=10, help="Seconds to wait for order events")
    
    args = parser.parse_args()
    
    from ib_insync import IB
    
    print(f"\n{'='*60}")
    print("IB GATEWAY DIAGNOSTICS")
    print(f"{'='*60}")
    print(f"Host: {args.host}")
    print(f"Port: {args.port}")
    print(f"ClientId: {args.client_id}")
    
    # Connect
    ib = IB()
    try:
        ib.connect(args.host, args.port, clientId=args.client_id, timeout=30)
        print(f"Connected: {ib.isConnected()}")
    except Exception as e:
        print(f"CONNECTION FAILED: {e}")
        sys.exit(1)
    
    # Create diagnostics
    diag = IBDiagnostics(ib, verbose=True)
    
    try:
        # Always get account info
        diag.get_account_info()
        
        if args.test == "simple":
            result = diag.test_simple_market_order(
                symbol=args.symbol,
                quantity=args.quantity,
                side=args.side,
                wait_seconds=args.wait,
            )
            print(f"\n=== RESULT ===")
            print(f"  Success: {result['success']}")
            print(f"  Status: {result.get('final_status', 'N/A')}")
            
        elif args.test == "bracket":
            result = diag.test_bracket_order(
                symbol=args.symbol,
                quantity=args.quantity,
                side=args.side,
                wait_seconds=args.wait,
            )
            print(f"\n=== RESULT ===")
            print(f"  Success: {result['success']}")
            
    finally:
        diag.shutdown()
        ib.disconnect()
        print("\nDisconnected.")


if __name__ == "__main__":
    main()
