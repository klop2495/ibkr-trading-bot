#!/usr/bin/env python3
"""
Test script to create a test signal_preview with entry_triggered=True
and verify that execution layer processes it correctly.

Usage:
    docker exec ibkr-trading-bot python3 /app/scripts/test_execution_signal.py

This will:
1. Create a test signal_preview with entry_triggered=True
2. Wait for control_decision and risk_verdict to be created (by backfill tick)
3. Check if execution_tick picks it up
4. Clean up test data
"""

import os
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

# Add app to path
sys.path.insert(0, "/app")

from app.storage.db import SupabaseDB
from app.models.signal_preview import (
    Confidence,
    DataQuality,
    Direction,
    SetupType,
    SignalPreviewV1,
    SpreadQuality,
)


def main():
    print("=" * 60)
    print("TEST EXECUTION SIGNAL")
    print("=" * 60)
    
    # Config
    TEST_SYMBOL = os.getenv("TEST_SYMBOL", "EURUSD")
    TEST_DIRECTION = os.getenv("TEST_DIRECTION", "long")  # long or short
    TEST_SL_PIPS = float(os.getenv("TEST_SL_PIPS", "20"))
    TEST_TP_PIPS = float(os.getenv("TEST_TP_PIPS", "40"))
    DRY_RUN = os.getenv("TEST_DRY_RUN", "1") == "1"  # Just insert, don't wait
    
    print(f"Symbol: {TEST_SYMBOL}")
    print(f"Direction: {TEST_DIRECTION}")
    print(f"SL/TP: {TEST_SL_PIPS}/{TEST_TP_PIPS} pips")
    print(f"Dry run: {DRY_RUN}")
    print()
    
    db = SupabaseDB()
    
    # Create test signal_preview
    ts_utc = datetime.now(timezone.utc)
    direction = Direction.LONG if TEST_DIRECTION == "long" else Direction.SHORT
    
    preview = SignalPreviewV1(
        ts_utc=ts_utc,
        symbol=TEST_SYMBOL,
        timeframe_trigger="M15",
        setup_type=SetupType.SWING_CONTINUATION,
        direction=direction,
        setup_present=True,
        entry_triggered=True,  # KEY: This enables execution
        confidence=Confidence.HIGH,
        rr=2.0,
        data_quality=DataQuality.OK,
        spread_quality=SpreadQuality.OK,
        flags=["TEST_SIGNAL", "STRUCTURAL_SL_TP"],
        sl_distance_pips=TEST_SL_PIPS,
        tp_distance_pips=TEST_TP_PIPS,
    )
    
    # Insert into signal_previews
    print("Inserting test signal_preview...")
    try:
        payload = {
            "ts_utc": preview.ts_utc.isoformat(),
            "symbol": preview.symbol,
            "timeframe_trigger": preview.timeframe_trigger,
            "setup_type": preview.setup_type.value,
            "direction": preview.direction.value,
            "setup_present": preview.setup_present,
            "entry_triggered": preview.entry_triggered,
            "confidence": preview.confidence.value,
            "rr": preview.rr,
            "data_quality": preview.data_quality.value,
            "spread_quality": preview.spread_quality.value,
            "flags": preview.flags,
            "sl_distance_pips": preview.sl_distance_pips,
            "tp_distance_pips": preview.tp_distance_pips,
        }
        
        result = db.client.table("signal_previews").insert(payload).execute()
        
        if not result.data:
            print("ERROR: Failed to insert signal_preview")
            return 1
        
        preview_id = result.data[0].get("id")
        print(f"✅ Created signal_preview: {preview_id}")
        print(f"   entry_triggered: True")
        print(f"   direction: {direction.value}")
        print(f"   sl_distance_pips: {TEST_SL_PIPS}")
        print(f"   tp_distance_pips: {TEST_TP_PIPS}")
        
    except Exception as e:
        print(f"ERROR inserting signal_preview: {e}")
        return 1
    
    if DRY_RUN:
        print()
        print("=" * 60)
        print("DRY RUN - Signal created. The backfill tick should pick it up.")
        print("Check logs with: docker logs ibkr-trading-bot --tail 50")
        print("=" * 60)
        return 0
    
    # Wait for control_decision
    print()
    print("Waiting for control_decision to be created (max 60s)...")
    decision_id = None
    for i in range(12):
        time.sleep(5)
        res = db.client.table("control_decisions").select("id").eq("signal_preview_id", preview_id).execute()
        if res.data:
            decision_id = res.data[0].get("id")
            print(f"✅ control_decision created: {decision_id}")
            break
        print(f"   Waiting... ({(i+1)*5}s)")
    
    if not decision_id:
        print("⚠️ control_decision not created within 60s")
        print("   Check if backfill is running")
        return 1
    
    # Wait for risk_verdict
    print()
    print("Waiting for risk_verdict to be created (max 30s)...")
    verdict_id = None
    for i in range(6):
        time.sleep(5)
        res = db.client.table("risk_verdicts").select("id, trade_allowed").eq("decision_id", decision_id).execute()
        if res.data:
            verdict_id = res.data[0].get("id")
            trade_allowed = res.data[0].get("trade_allowed")
            print(f"✅ risk_verdict created: {verdict_id}")
            print(f"   trade_allowed: {trade_allowed}")
            break
        print(f"   Waiting... ({(i+1)*5}s)")
    
    if not verdict_id:
        print("⚠️ risk_verdict not created within 30s")
        return 1
    
    # Check if execution happened
    print()
    print("Checking for execution events...")
    time.sleep(10)  # Wait for execution tick
    
    exec_res = db.client.table("risk_events").select("event_type, message, data").eq("data->>decision_id", decision_id).execute()
    if exec_res.data:
        for event in exec_res.data:
            print(f"✅ {event.get('event_type')}: {event.get('message')}")
    else:
        print("⚠️ No execution events found")
        print("   Check execution_enabled and execution_tick in logs")
    
    # Check trades_history
    trades_res = db.client.table("trades_history").select("id, symbol, side, status, entry_price").eq("decision_id", decision_id).execute()
    if trades_res.data:
        for trade in trades_res.data:
            print(f"✅ Trade created: {trade.get('id')}")
            print(f"   Status: {trade.get('status')}")
            print(f"   Side: {trade.get('side')}")
    else:
        print("⚠️ No trade created")
    
    print()
    print("=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
