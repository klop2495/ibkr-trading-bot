-- Smart ANTI Strategy columns
-- Rule: h30_direction == mtf_h4_direction AND aligned <= 3 → trade AGAINST
-- Exception: EURUSD trades WITH direction (74% accuracy)
-- Backtest: 78.2% accuracy on 444 samples (14 days)

-- Add Smart ANTI columns to price_forecasts
ALTER TABLE price_forecasts
ADD COLUMN IF NOT EXISTS h30_smart_anti_direction TEXT,
ADD COLUMN IF NOT EXISTS h30_smart_anti_correct BOOLEAN,
ADD COLUMN IF NOT EXISTS h30_smart_anti_eligible BOOLEAN;

-- Index for analysis
CREATE INDEX IF NOT EXISTS idx_forecasts_smart_anti 
ON price_forecasts (h30_smart_anti_eligible, h30_smart_anti_direction) 
WHERE h30_smart_anti_direction IS NOT NULL;

COMMENT ON COLUMN price_forecasts.h30_smart_anti_direction IS 'Smart ANTI direction: inverted h30 when h30==h4 and aligned<=3 (EURUSD follows h30)';
COMMENT ON COLUMN price_forecasts.h30_smart_anti_correct IS 'Whether Smart ANTI prediction was correct (filled by outcome checker)';
COMMENT ON COLUMN price_forecasts.h30_smart_anti_eligible IS 'True when all Smart ANTI conditions are met';
