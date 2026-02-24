-- Add advanced filter metadata columns to price_forecasts table
-- These columns store indicator data for Phase 2 filtering analysis
-- All nullable — existing rows will have NULL values

ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS adx_value real;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS bb_width real;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS bb_squeeze boolean;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS mtf_conflict boolean;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS mtf_h4_direction text;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS spread_pips real;

-- Comments for documentation
COMMENT ON COLUMN price_forecasts.adx_value IS 'ADX(14) on M15 — trend strength 0-100. <20=flat, >25=trending';
COMMENT ON COLUMN price_forecasts.bb_width IS 'Bollinger Band width (normalized). Lower = tighter range';
COMMENT ON COLUMN price_forecasts.bb_squeeze IS 'True if BB inside Keltner Channel — squeeze/low volatility';
COMMENT ON COLUMN price_forecasts.mtf_conflict IS 'True if H4 strong trend opposes H30 signal direction';
COMMENT ON COLUMN price_forecasts.mtf_h4_direction IS 'H4 timeframe dominant direction (up/down/neutral)';
COMMENT ON COLUMN price_forecasts.spread_pips IS 'Current spread in pips from broker (if available)';
