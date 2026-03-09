-- Alt5 trade eligibility marker (for UI recommended split and analytics).

ALTER TABLE price_forecasts
ADD COLUMN IF NOT EXISTS h30_alt5_trade_eligible boolean DEFAULT false;

COMMENT ON COLUMN price_forecasts.h30_alt5_trade_eligible
IS 'Alt5 execution/recommendation eligibility after env filters and lifecycle';
