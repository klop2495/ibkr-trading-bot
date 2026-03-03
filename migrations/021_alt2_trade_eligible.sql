-- Alt2 soft execution filter marker.
-- Keep all Alt2 signals in DB for analysis; trade only eligible subset.

ALTER TABLE price_forecasts
ADD COLUMN IF NOT EXISTS h30_alt2_trade_eligible boolean;

COMMENT ON COLUMN price_forecasts.h30_alt2_trade_eligible
IS 'Soft execution filter for Alt2: true=trade-eligible, false=info-only';

