#!/usr/bin/env python3
"""
Test script for FX Funds Guard.

Tests the funds checking and auto-reduction logic.
"""

import os
import sys

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_funds_guard():
    """Test FX Funds Guard with live IB connection."""
    from ib_insync import IB
    from app.broker.fx_funds_guard import FXFundsGuard, FundsPolicy
    
    ib = IB()
    ib.connect(
        host=os.getenv("IBKR_HOST", "ib-gateway"),
        port=int(os.getenv("IBKR_PORT", "4004")),
        clientId=250,
        timeout=15,
    )
    
    print("Connected to IB Gateway")
    
    # Create funds guard
    guard = FXFundsGuard(ib=ib, policy=FundsPolicy.AUTO_REDUCE)
    
    # Get current balances
    print("\n=== Cash Balances ===")
    for currency in ["USD", "EUR", "GBP", "AUD", "CHF"]:
        balance = guard.get_cash_balance(currency)
        print(f"  {currency}: {balance:,.2f}")
    
    # Test scenarios
    test_cases = [
        ("EURUSD", "BUY", 100000),   # Large BUY - likely needs reduction
        ("EURUSD", "BUY", 10000),    # Small BUY - should work
        ("EURUSD", "SELL", 100000),  # Large SELL - should work (we have EUR)
        ("GBPUSD", "BUY", 50000),    # Medium BUY
        ("USDJPY", "BUY", 50000),    # BUY with base=USD
    ]
    
    print("\n=== Funds Check Results ===")
    for symbol, side, qty in test_cases:
        result = guard.check_funds(symbol, side, qty)
        status = "✅" if result.can_trade else "❌"
        adjusted = f" -> {result.adjusted_qty}" if result.was_adjusted else ""
        print(f"  {status} {symbol} {side} {qty}{adjusted}: {result.reason}")
        print(f"      Need {result.currency_needed}: {result.required_cash:,.2f}, Have: {result.available_cash:,.2f}")
    
    ib.disconnect()
    print("\nDone!")


def test_order_with_funds_check():
    """Test placing order with funds check through OMS."""
    from ib_insync import IB
    from app.broker.oms import IBKROMS, IBKROrderRequest, OrderSide
    
    ib = IB()
    ib.connect(
        host=os.getenv("IBKR_HOST", "ib-gateway"),
        port=int(os.getenv("IBKR_PORT", "4004")),
        clientId=251,
        timeout=15,
    )
    
    print("Connected to IB Gateway")
    
    # Create OMS with funds guard
    oms = IBKROMS(ib=ib, enable_funds_guard=True)
    
    # Log callback
    events = []
    def log_event(event_type, severity, message, data):
        events.append({"type": event_type, "message": message})
        print(f"  EVENT: {event_type} - {message}")
    
    # Test order that might need reduction
    print("\n=== Test Order with Funds Check ===")
    request = IBKROrderRequest(
        symbol="EURUSD",
        side=OrderSide.BUY,
        quantity=100000,  # Large order
    )
    
    state, details = oms.place_order_with_funds_check(
        request=request,
        log_callback=log_event,
    )
    
    print(f"\nOrder Result:")
    print(f"  Status: {state.status}")
    print(f"  Original Qty: {details.get('original_qty')}")
    print(f"  Adjusted Qty: {details.get('adjusted_qty')}")
    print(f"  Reason: {details.get('reason')}")
    
    # Wait for fill
    if state.status.value not in ["REJECTED", "ERROR"]:
        print("\nWaiting for fill...")
        for i in range(10):
            ib.sleep(1)
            current_state = oms.get_order_state(request.id)
            if current_state:
                print(f"  {i}: {current_state.status}")
                if current_state.status.value == "FILLED":
                    print(f"  FILLED @ {current_state.avg_fill_price}")
                    break
    
    # Show events
    print(f"\nEvents logged: {len(events)}")
    for e in events:
        print(f"  - {e['type']}: {e['message']}")
    
    oms.shutdown()
    ib.disconnect()
    print("\nDone!")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test FX Funds Guard")
    parser.add_argument("--order", action="store_true", help="Test order placement")
    args = parser.parse_args()
    
    if args.order:
        test_order_with_funds_check()
    else:
        test_funds_guard()
