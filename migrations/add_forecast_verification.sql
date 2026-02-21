-- Add verification columns to price_forecasts table
-- These columns track whether predictions were correct after the horizon elapsed.

-- Per-horizon actual direction (filled by verification job)
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h30_actual TEXT;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h60_actual TEXT;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h240_actual TEXT;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h1440_actual TEXT;

-- Per-horizon correctness (true = prediction matched actual)
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h30_correct BOOLEAN;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h60_correct BOOLEAN;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h240_correct BOOLEAN;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h1440_correct BOOLEAN;

-- Per-horizon actual price at verification time
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h30_actual_price REAL;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h60_actual_price REAL;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h240_actual_price REAL;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h1440_actual_price REAL;

-- Base price at forecast time (close price when forecast was made)
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS base_price REAL;

-- When verification was last run for this row
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;

-- Index for finding unverified forecasts efficiently
CREATE INDEX IF NOT EXISTS idx_forecasts_unverified
    ON price_forecasts(verified_at, ts_utc DESC)
    WHERE verified_at IS NULL;
