#!/usr/bin/env python3
"""
SimulationEngine - Standalone entrypoint

Run as separate container/process alongside trading bot.
Does NOT execute real orders, only simulates trades.

Usage:
    python -m app.simulation.main

Environment variables:
    - SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY: Database connection
    - IB_GATEWAY_HOST, IB_GATEWAY_PORT, IB_CLIENT_ID_SIM: IBKR connection
    - SIM_RISK_PER_TRADE: Risk per trade (default 0.01 = 1%)
    - SIM_MAX_OPEN_POSITIONS: Max simultaneous positions (default 5)
    - SIM_MAX_LEVERAGE: Max effective leverage (default 10)
    - SIM_PRICE_CHECK_INTERVAL: Seconds between SL/TP checks (default 10)
    - SIM_VERDICT_SCAN_INTERVAL: Seconds between verdict scans (default 30)
    - SIM_MIN_SIZE_POLICY: ROUND_UP or BLOCK (default ROUND_UP)
"""
import os
import sys
import time
import signal
from datetime import datetime

from app.storage.db import SupabaseDB
from app.simulation.engine import SimulationEngine
from app.simulation.position_sizer import MinSizePolicy


def create_ib_connection():
    """Create IB Gateway connection for simulation (read-only data)"""
    ib_host = os.getenv("IB_GATEWAY_HOST", "ib-gateway")
    ib_port = int(os.getenv("IB_GATEWAY_PORT", "4004"))
    # Use different client ID to avoid conflicts with trading bot
    ib_client_id = int(os.getenv("IB_CLIENT_ID_SIM", "200"))
    
    try:
        from ib_insync import IB
        
        print(f"SimulationEngine: Connecting to IB Gateway {ib_host}:{ib_port} clientId={ib_client_id}")
        ib = IB()
        ib.RequestTimeout = 60
        ib.connect(ib_host, ib_port, clientId=ib_client_id, timeout=60)
        print("SimulationEngine: IB Gateway connected")
        return ib
    except Exception as e:
        print(f"SimulationEngine: IB Gateway connection failed: {e}")
        print("SimulationEngine: Running without IB (will use DB fallback for prices)")
        return None


def main():
    print("=" * 60)
    print("SimulationEngine v1 - Shadow Trading Simulator")
    print("=" * 60)
    print(f"Started at: {datetime.now().isoformat()}")
    print()
    
    # Initialize database
    db = SupabaseDB()
    if not db.ping():
        print("ERROR: Cannot connect to Supabase")
        sys.exit(1)
    print("SimulationEngine: Supabase connected")
    
    # Initialize IB connection (optional)
    ib = create_ib_connection()
    
    # Read configuration from environment
    config = {
        "risk_per_trade": float(os.getenv("SIM_RISK_PER_TRADE", "0.01")),
        "max_open_positions": int(os.getenv("SIM_MAX_OPEN_POSITIONS", "5")),
        "max_effective_leverage": float(os.getenv("SIM_MAX_LEVERAGE", "10")),
        "price_check_interval": int(os.getenv("SIM_PRICE_CHECK_INTERVAL", "10")),
        "equity_update_interval": int(os.getenv("SIM_EQUITY_UPDATE_INTERVAL", "60")),
        "verdict_scan_interval": int(os.getenv("SIM_VERDICT_SCAN_INTERVAL", "30")),
        "verdict_lookback_hours": int(os.getenv("SIM_VERDICT_LOOKBACK_HOURS", "24")),
        "min_size_policy": os.getenv("SIM_MIN_SIZE_POLICY", MinSizePolicy.ROUND_UP),
    }
    
    print("Configuration:")
    for key, value in config.items():
        print(f"  {key}: {value}")
    print()
    
    # Create engine
    engine = SimulationEngine(
        db=db,
        ib_client=ib,
        **config,
    )
    
    # Handle shutdown signals
    def shutdown_handler(signum, frame):
        print("\nSimulationEngine: Shutdown signal received")
        engine.stop()
        if ib and ib.isConnected():
            ib.disconnect()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    
    # Start engine
    engine.start()
    
    # Main loop
    print("\nSimulationEngine: Entering main loop...")
    print("-" * 60)
    
    tick_count = 0
    while engine.running:
        try:
            engine.tick()
            tick_count += 1
            
            # Status log every 60 ticks (roughly every minute with default intervals)
            if tick_count % 60 == 0:
                open_count = len(engine.open_positions)
                print(f"SimulationEngine: tick={tick_count} equity={engine.sim_equity:.2f} open_positions={open_count} closed_pnl={engine.closed_pnl:.2f}")
            
            time.sleep(1)  # Main loop interval
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"SimulationEngine: Error in main loop: {e}")
            time.sleep(5)
    
    # Cleanup
    engine.stop()
    if ib and ib.isConnected():
        ib.disconnect()
    
    print("SimulationEngine: Exited")


if __name__ == "__main__":
    main()
