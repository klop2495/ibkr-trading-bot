-- Alt3-v2: independent strict forecast variant.
-- Keeps legacy h30_alt3_* fields untouched for backward compatibility.

ALTER TABLE price_forecasts
ADD COLUMN IF NOT EXISTS h30_alt3v2_direction text;

ALTER TABLE price_forecasts
ADD COLUMN IF NOT EXISTS h30_alt3v2_correct boolean;

ALTER TABLE price_forecasts
ADD COLUMN IF NOT EXISTS h30_alt3v2_trade_eligible boolean;

ALTER TABLE price_forecasts
ADD COLUMN IF NOT EXISTS h30_alt3v2_score double precision;

ALTER TABLE price_forecasts
ADD COLUMN IF NOT EXISTS h30_alt3v2_meta_json jsonb;

COMMENT ON COLUMN price_forecasts.h30_alt3v2_direction
IS 'Alt3-v2 independent variant direction';

COMMENT ON COLUMN price_forecasts.h30_alt3v2_correct
IS 'Verification result for Alt3-v2 signal';

COMMENT ON COLUMN price_forecasts.h30_alt3v2_trade_eligible
IS 'Execution eligibility marker for Alt3-v2';

COMMENT ON COLUMN price_forecasts.h30_alt3v2_score
IS 'Raw Alt3-v2 weighted score';

COMMENT ON COLUMN price_forecasts.h30_alt3v2_meta_json
IS 'Alt3-v2 score components for diagnostics';
