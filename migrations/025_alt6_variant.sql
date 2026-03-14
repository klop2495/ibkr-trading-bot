ALTER TABLE price_forecasts
ADD COLUMN IF NOT EXISTS h30_alt6_direction text,
ADD COLUMN IF NOT EXISTS h30_alt6_correct boolean,
ADD COLUMN IF NOT EXISTS h30_alt6_trade_eligible boolean DEFAULT false;

COMMENT ON COLUMN price_forecasts.h30_alt6_direction
IS 'Alt6 direction: strict_s5_v2_extension_veto';

COMMENT ON COLUMN price_forecasts.h30_alt6_correct
IS 'Whether Alt6 direction matched realized H30 outcome';

COMMENT ON COLUMN price_forecasts.h30_alt6_trade_eligible
IS 'Alt6 runtime eligibility flag for notifications and manual execution';
