-- Migration: 018_performance_tracker_state.sql
-- Phase 7: Persistence for AgentPerformanceTracker
-- 
-- This table stores the performance tracker state including:
-- - Dynamic agent weights adjusted based on trade outcomes
-- - Trade outcomes history for weight calculation
-- - Agent performance statistics
--
-- Run this in Supabase SQL Editor or via migration tool

-- Create performance_tracker_state table
CREATE TABLE IF NOT EXISTS performance_tracker_state (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    
    -- Unique key for tracker instance (allows multiple trackers)
    key TEXT UNIQUE NOT NULL DEFAULT 'default',
    
    -- Full tracker state (serialized from AgentPerformanceTracker.to_dict())
    -- Contains: weights, outcomes, agent_stats, config
    state_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    
    -- Current weights extracted for quick queries
    -- Example: {"TechnicalAgent": 0.27, "RiskAgent": 0.23, ...}
    weights JSONB NOT NULL DEFAULT '{}'::jsonb,
    
    -- Summary stats for monitoring
    total_trades INTEGER NOT NULL DEFAULT 0,
    
    -- Timestamps
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for quick lookups by key
CREATE INDEX IF NOT EXISTS idx_performance_tracker_key 
    ON performance_tracker_state(key);

-- Index on updated_at for finding stale states
CREATE INDEX IF NOT EXISTS idx_performance_tracker_updated 
    ON performance_tracker_state(updated_at DESC);

-- Add comment for documentation
COMMENT ON TABLE performance_tracker_state IS 
    'Phase 7: Stores AgentPerformanceTracker state for persistence across restarts. Weights adapt based on trade outcomes.';

COMMENT ON COLUMN performance_tracker_state.key IS 
    'Unique identifier for tracker instance. Default is "default" for main production tracker.';

COMMENT ON COLUMN performance_tracker_state.state_json IS 
    'Full serialized state from AgentPerformanceTracker.to_dict(). Contains weights, outcomes, agent_stats.';

COMMENT ON COLUMN performance_tracker_state.weights IS 
    'Current agent weights extracted for quick queries without deserializing full state.';

-- Trigger to auto-update updated_at
CREATE OR REPLACE FUNCTION update_performance_tracker_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_performance_tracker_updated ON performance_tracker_state;
CREATE TRIGGER trg_performance_tracker_updated
    BEFORE UPDATE ON performance_tracker_state
    FOR EACH ROW
    EXECUTE FUNCTION update_performance_tracker_timestamp();

-- Enable RLS (optional, uncomment if needed)
-- ALTER TABLE performance_tracker_state ENABLE ROW LEVEL SECURITY;

-- Grant permissions (adjust as needed for your setup)
-- GRANT ALL ON performance_tracker_state TO authenticated;
-- GRANT ALL ON performance_tracker_state TO service_role;
