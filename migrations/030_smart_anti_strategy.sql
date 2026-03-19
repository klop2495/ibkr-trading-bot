-- Alt3 v3: Smart ANTI Strategy
-- Replaces legacy Alt3 with market-condition based logic
-- Rule: h30_direction == mtf_h4_direction AND aligned <= 3 → trade AGAINST
-- Exception: EURUSD trades WITH direction (74% accuracy)
-- Backtest: 78.2% accuracy on 444 samples (14 days)

-- Add trade_eligible column for Alt3 (direction and correct columns already exist)
ALTER TABLE price_forecasts
ADD COLUMN IF NOT EXISTS h30_alt3_trade_eligible BOOLEAN;

-- Index for filtering eligible signals
CREATE INDEX IF NOT EXISTS idx_forecasts_alt3_eligible 
ON price_forecasts (h30_alt3_trade_eligible, h30_alt3_direction) 
WHERE h30_alt3_trade_eligible = true;

COMMENT ON COLUMN price_forecasts.h30_alt3_trade_eligible IS 'Alt3 v3: True when Smart ANTI conditions met (h30==h4, aligned<=3, bb>=0.002)';
