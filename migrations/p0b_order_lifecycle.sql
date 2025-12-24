-- P0-B: Add columns for order lifecycle tracking
-- Run this in Supabase SQL Editor

-- Add ib_order_id to link trade to IB Gateway order
ALTER TABLE trades_history 
ADD COLUMN IF NOT EXISTS ib_order_id INTEGER;

-- Add error_message for rejected/failed orders
ALTER TABLE trades_history 
ADD COLUMN IF NOT EXISTS error_message TEXT;

-- Add updated_at for tracking status changes
ALTER TABLE trades_history 
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- Create index on ib_order_id for fast lookups in callbacks
CREATE INDEX IF NOT EXISTS idx_trades_history_ib_order_id 
ON trades_history(ib_order_id) 
WHERE ib_order_id IS NOT NULL;

-- Update status column to support new statuses
-- PENDING, SUBMITTED, OPEN, CLOSED, CANCELLED, REJECTED
COMMENT ON COLUMN trades_history.status IS 'P0-B lifecycle: PENDING -> SUBMITTED -> OPEN/CANCELLED/REJECTED -> CLOSED';
