-- Phase 8: Price Direction Forecasts
-- Multi-horizon directional predictions (30m, 1h, 4h, 24h)
-- Read-only module — does NOT influence trade execution.

CREATE TABLE IF NOT EXISTS price_forecasts (
    id                UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at        TIMESTAMPTZ DEFAULT now(),
    ts_utc            TIMESTAMPTZ NOT NULL,
    symbol            TEXT NOT NULL,

    -- 30-minute horizon
    h30_direction     TEXT,       -- up / down / neutral
    h30_confidence    TEXT,       -- low / medium / high
    h30_strength      REAL,       -- 0.0 – 1.0
    h30_aligned       INT,        -- indicators aligned
    h30_total         INT,        -- indicators total

    -- 60-minute horizon
    h60_direction     TEXT,
    h60_confidence    TEXT,
    h60_strength      REAL,
    h60_aligned       INT,
    h60_total         INT,

    -- 240-minute (4h) horizon
    h240_direction    TEXT,
    h240_confidence   TEXT,
    h240_strength     REAL,
    h240_aligned      INT,
    h240_total        INT,

    -- 1440-minute (24h) horizon
    h1440_direction   TEXT,
    h1440_confidence  TEXT,
    h1440_strength    REAL,
    h1440_aligned     INT,
    h1440_total       INT,

    -- Aggregated fields
    all_aligned        BOOLEAN DEFAULT false,
    dominant_direction TEXT,       -- up / down / neutral

    data_quality      TEXT DEFAULT 'ok',
    flags             JSONB DEFAULT '[]'
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_forecasts_symbol_ts ON price_forecasts(symbol, ts_utc DESC);
CREATE INDEX IF NOT EXISTS idx_forecasts_ts ON price_forecasts(ts_utc DESC);
CREATE INDEX IF NOT EXISTS idx_forecasts_aligned ON price_forecasts(all_aligned, ts_utc DESC);
