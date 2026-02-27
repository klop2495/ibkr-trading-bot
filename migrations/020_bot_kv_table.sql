-- bot_kv: simple key-value store for cross-container state sharing
-- Used by Signal Lifecycle Manager to persist state from trading-bot → dashboard

CREATE TABLE IF NOT EXISTS bot_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_bot_kv_updated ON bot_kv (updated_at DESC);

-- RLS: allow service role full access
ALTER TABLE bot_kv ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON bot_kv
    FOR ALL
    USING (true)
    WITH CHECK (true);
