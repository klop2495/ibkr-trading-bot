-- Alt5: inversion of legacy Alt3 with env-based filters (hours + ADX).

ALTER TABLE price_forecasts
ADD COLUMN IF NOT EXISTS h30_alt5_direction text;

ALTER TABLE price_forecasts
ADD COLUMN IF NOT EXISTS h30_alt5_correct boolean;

COMMENT ON COLUMN price_forecasts.h30_alt5_direction
IS 'Alt5 direction: inversion of legacy Alt3 after env filters';

COMMENT ON COLUMN price_forecasts.h30_alt5_correct
IS 'Verification result for Alt5 signal';
