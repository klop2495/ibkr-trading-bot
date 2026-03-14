-- Execution lifecycle metadata for trades_history.
-- Run in Supabase SQL editor before deploying this phase.

ALTER TABLE trades_history
ADD COLUMN IF NOT EXISTS completion_status TEXT,
ADD COLUMN IF NOT EXISTS close_source TEXT,
ADD COLUMN IF NOT EXISTS data_integrity_flags JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS status_trace JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS last_status_at TIMESTAMPTZ DEFAULT NOW();

UPDATE trades_history
SET
  completion_status = CASE
    WHEN status IN ('PENDING', 'SUBMITTED', 'OPEN') THEN 'active'
    WHEN status = 'DRY_RUN' THEN 'dry_run'
    WHEN status = 'EXPIRED' THEN 'expired'
    WHEN status = 'CANCELLED' THEN 'cancelled'
    WHEN status = 'REJECTED' THEN 'rejected'
    WHEN status = 'ORPHAN_POSITION' THEN 'recovered_from_broker'
    WHEN status = 'CLOSED' AND exit_price IS NOT NULL AND pnl IS NOT NULL THEN 'complete'
    WHEN status = 'CLOSED' THEN 'incomplete'
    ELSE COALESCE(completion_status, 'unknown')
  END,
  close_source = CASE
    WHEN close_reason IN ('TP_HIT', 'SL_HIT') THEN 'broker_bracket'
    WHEN close_reason IN ('MANUAL', 'MANUAL_CLOSE') THEN 'manual'
    WHEN close_reason = 'BROKER_FLAT' THEN 'broker_reconcile'
    ELSE close_source
  END,
  data_integrity_flags = COALESCE(data_integrity_flags, '[]'::jsonb),
  status_trace = COALESCE(status_trace, '[]'::jsonb),
  last_status_at = COALESCE(last_status_at, updated_at, created_at, opened_at, NOW())
WHERE completion_status IS NULL
   OR close_source IS NULL
   OR data_integrity_flags IS NULL
   OR status_trace IS NULL
   OR last_status_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_trades_history_completion_status
ON trades_history(completion_status);

CREATE INDEX IF NOT EXISTS idx_trades_history_last_status_at
ON trades_history(last_status_at);
