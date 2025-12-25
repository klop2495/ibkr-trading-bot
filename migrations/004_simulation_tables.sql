-- SimulationEngine Tables Migration
-- These tables are SEPARATE from trading bot tables
-- SimulationEngine only writes to sim_* tables

-- ============================================
-- sim_trades - Simulated trade records
-- ============================================
CREATE TABLE IF NOT EXISTS sim_trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- References to bot data (read-only)
    decision_id TEXT,
    signal_preview_id TEXT,
    verdict_id TEXT,
    
    -- Trade details
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity NUMERIC NOT NULL DEFAULT 0,
    
    -- Prices
    entry_price NUMERIC,
    exit_price NUMERIC,
    stop_loss NUMERIC,
    take_profit NUMERIC,
    current_price NUMERIC,
    
    -- P&L
    pnl NUMERIC,
    pnl_pips NUMERIC,
    unrealized_pnl NUMERIC,
    
    -- Status
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'OPEN', 'CLOSED', 'BLOCKED')),
    close_reason TEXT CHECK (close_reason IN ('TP_HIT', 'SL_HIT', 'MANUAL', 'EXPIRED')),
    block_reason TEXT,
    
    -- Timestamps
    opened_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Risk parameters at entry
    equity_at_entry NUMERIC,
    risk_cash NUMERIC,
    sl_pips NUMERIC,
    tp_pips NUMERIC,
    notional NUMERIC,
    risk_modifier NUMERIC DEFAULT 1.0
);

-- Indexes for sim_trades
CREATE INDEX IF NOT EXISTS idx_sim_trades_status ON sim_trades(status);
CREATE INDEX IF NOT EXISTS idx_sim_trades_symbol ON sim_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_sim_trades_decision_id ON sim_trades(decision_id);
CREATE INDEX IF NOT EXISTS idx_sim_trades_created_at ON sim_trades(created_at DESC);

-- ============================================
-- sim_fills - Simulated fill/execution records
-- ============================================
CREATE TABLE IF NOT EXISTS sim_fills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_id UUID NOT NULL REFERENCES sim_trades(id),
    
    fill_type TEXT NOT NULL CHECK (fill_type IN ('ENTRY', 'EXIT')),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity NUMERIC NOT NULL,
    price NUMERIC NOT NULL,
    
    -- Market data at fill
    bid NUMERIC,
    ask NUMERIC,
    spread NUMERIC,
    
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sim_fills_trade_id ON sim_fills(trade_id);
CREATE INDEX IF NOT EXISTS idx_sim_fills_timestamp ON sim_fills(timestamp DESC);

-- ============================================
-- sim_equity_curve - Equity tracking over time
-- ============================================
CREATE TABLE IF NOT EXISTS sim_equity_curve (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    
    -- Equity components
    baseline_equity NUMERIC NOT NULL DEFAULT 0,  -- From IBKR
    sim_equity NUMERIC NOT NULL DEFAULT 0,       -- Simulated equity
    
    closed_pnl NUMERIC DEFAULT 0,    -- Realized P&L
    open_pnl NUMERIC DEFAULT 0,      -- Unrealized P&L (mark-to-market)
    
    -- Exposure
    total_exposure NUMERIC DEFAULT 0,
    num_open_positions INTEGER DEFAULT 0,
    
    -- Source
    equity_source TEXT DEFAULT 'IBKR'
);

CREATE INDEX IF NOT EXISTS idx_sim_equity_curve_timestamp ON sim_equity_curve(timestamp DESC);

-- ============================================
-- sim_events - Simulation events for logging
-- ============================================
CREATE TABLE IF NOT EXISTS sim_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    
    event_type TEXT NOT NULL,
    severity TEXT DEFAULT 'INFO' CHECK (severity IN ('INFO', 'WARN', 'ERROR')),
    
    -- Context
    symbol TEXT,
    trade_id TEXT,
    decision_id TEXT,
    
    -- Details
    message TEXT,
    data JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_sim_events_timestamp ON sim_events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_sim_events_event_type ON sim_events(event_type);
CREATE INDEX IF NOT EXISTS idx_sim_events_symbol ON sim_events(symbol);

-- ============================================
-- Enable RLS (Row Level Security) if needed
-- ============================================
-- ALTER TABLE sim_trades ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE sim_fills ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE sim_equity_curve ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE sim_events ENABLE ROW LEVEL SECURITY;

-- ============================================
-- Comments
-- ============================================
COMMENT ON TABLE sim_trades IS 'Simulated trades from SimulationEngine - separate from real trades';
COMMENT ON TABLE sim_fills IS 'Simulated fills/executions';
COMMENT ON TABLE sim_equity_curve IS 'Equity curve tracking for simulation';
COMMENT ON TABLE sim_events IS 'Simulation engine events and diagnostics';
