# Deploy A/B Alt Scoring — 2026-02-26

## What changed
- **engine.py**: Computes `h30_alt_direction` + `h30_alt_strength` using ALT_WEIGHTS
  - `ma_cross_inv`: 1.0 (keep), `price_vs_ma`: -1.0 (invert), `momentum`: -1.0 (invert), `atr_trend`: -1.0 (invert)
  - `rsi_trend`, `rsi_momentum`: 0.0 (dropped — too often neutral)
- **forecast.py**: Added fields `h30_alt_direction`, `h30_alt_strength` to model + `to_db_row()`
- **verifier.py**: Verifies `h30_alt_correct` alongside `h30_correct`
- **indicators_vote.py**: Fixed `aggregate_weighted_votes` to handle negative weights (abs for total_weight, effective contribution for aligned count)

## Files changed (local)
```
app/forecast/engine.py
app/forecast/indicators_vote.py
app/forecast/verifier.py
app/models/forecast.py
```

## Deploy steps

### 1. DB migration (run FIRST)
```sql
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h30_alt_direction text;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h30_alt_strength float;
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h30_alt_correct boolean;
```

### 2. Push code
```bash
cd /Users/oleggnikishin/AI\ PROJECTS/Forex\ trading\ bot/ibkr-trading-bot
git add -A && git commit -m "feat: A/B alt scoring with inverted contrarian weights" && git push
```

### 3. Deploy on VPS
```bash
ssh root@65.108.83.67
cd /root/ibkr-trading-bot
git pull
docker compose restart
sleep 30
docker logs --tail 50 ibkr-trading-bot | grep -E "alt_direction|AdaptiveForecastGate"
```

### 4. Verify in 35 minutes
```bash
docker exec ibkr-trading-bot python3 -c "
from app.storage.db import SupabaseDB
db = SupabaseDB()
r = db.client.table('price_forecasts').select('ts_utc,symbol,h30_direction,h30_alt_direction,h30_alt_strength').not_.is_('h30_alt_direction','null').order('ts_utc',desc=True).limit(5).execute()
for x in (r.data or []): print(x['ts_utc'][:19], x['symbol'], f\"orig={x['h30_direction']} alt={x['h30_alt_direction']} str={x['h30_alt_strength']}\")
"
```

### 5. After 30+ min — check verification
```bash
docker exec ibkr-trading-bot python3 -c "
from app.storage.db import SupabaseDB
db = SupabaseDB()
# Compare original vs alt accuracy
r = db.client.table('price_forecasts').select('h30_correct,h30_alt_correct').not_.is_('h30_alt_correct','null').execute()
rows = r.data or []
n = len(rows)
orig_c = sum(1 for x in rows if x['h30_correct'])
alt_c = sum(1 for x in rows if x['h30_alt_correct'])
print(f'Verified: {n}')
print(f'Original: {orig_c}/{n} = {orig_c/n*100:.1f}%' if n else 'No data')
print(f'Alt:      {alt_c}/{n} = {alt_c/n*100:.1f}%' if n else 'No data')
"
```
