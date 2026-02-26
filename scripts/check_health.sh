#!/bin/bash
# Health check after IB Gateway reconnection
# Usage: bash scripts/check_health.sh

echo "=== IBKR TRADING BOT HEALTH CHECK ==="
echo "Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

# 1. IB Gateway connection
echo "--- 1. IB Gateway Connection ---"
docker logs --since 2m ibkr-trading-bot 2>&1 | grep -E "ibkr_marketdata_connected|IB Gateway connected|ib_connection_failed|ib_untrusted|not connected" | tail -5
echo ""

# 2. Signal generation
echo "--- 2. Signal Generation ---"
docker logs --since 5m ibkr-trading-bot 2>&1 | grep "signal_gen" | tail -3
echo ""

# 3. Forecast writing
echo "--- 3. Latest Forecasts in DB ---"
docker exec ibkr-trading-bot python3 -c "
from app.storage.db import SupabaseDB
from datetime import datetime, timezone
db = SupabaseDB()
res = db.client.table('price_forecasts').select('ts_utc, symbol, h30_direction, h30_votes_json').order('ts_utc', desc=True).limit(3).execute()
now = datetime.now(timezone.utc)
for r in (res.data or []):
    ts = r['ts_utc'][:19]
    sym = r['symbol']
    d = r['h30_direction']
    has_votes = 'YES' if r.get('h30_votes_json') else 'NO'
    print(f'  {ts} {sym:8s} dir={d:7s} votes={has_votes}')
" 2>&1
echo ""

# 4. Errors
echo "--- 4. Recent Errors ---"
docker logs --since 5m ibkr-trading-bot 2>&1 | grep -iE "error|traceback|exception" | grep -v "errors=0" | tail -5
ERR_COUNT=$(docker logs --since 5m ibkr-trading-bot 2>&1 | grep -iE "error|traceback|exception" | grep -v "errors=0" | wc -l)
if [ "$ERR_COUNT" -eq 0 ]; then
    echo "  No errors in last 5 minutes ✅"
fi
echo ""

# 5. Verifier
echo "--- 5. Forecast Verifier ---"
docker logs --since 10m ibkr-trading-bot 2>&1 | grep "forecast_verified" | tail -3
echo ""

echo "=== DONE ==="
