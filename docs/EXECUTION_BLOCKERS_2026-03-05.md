# Execution Blockers Runbook (2026-03-05)

## Scope
- Diagnose why `parallel_decisions.executed_signal` exists but no new rows appear in `orders` / `trades_history`.
- Record verified blockers and temporary runtime overrides used during diagnostics.

## Verified timeline
- `risk_verdicts` with `TRADING_DISABLED` existed in older rows.
- Latest `bot_settings.trading_enabled` switched to `true`.
- New decisions still did not open trades.
- `risk_events` showed repeated:
  - `event_type=EXECUTION_BLOCKED`
  - `message=Trade blocked by forecast gate ...`
  - `reason=adaptive_gate_blocked:<symbol> rolling_acc=<...>%`

## Confirmed blockers
1. `EXECUTION_ACCURACY_GUARD` could block execution (`execution_guard blocked=1 ...`).
2. `ForecastGate` blocked symbols by rolling accuracy (`adaptive_gate_blocked`).

## Runtime flags used for diagnostics
- `EXECUTION_ENABLED=1`
- `EXECUTION_DRY_RUN=0`
- `BOT_MODE=paper`
- `EXECUTION_STRATEGY=rules`
- `ACTIVE_STRATEGY=rules`
- `EXECUTION_ACCURACY_GUARD_ENABLED=0` (temporary)
- `FORECAST_GATE_ENABLED=0` (temporary)

## Quick checks

### 1) Effective runtime env inside container
```bash
docker exec ibkr-trading-bot sh -lc 'env | egrep "EXECUTION_ENABLED|EXECUTION_DRY_RUN|BOT_MODE|ACTIVE_STRATEGY|EXECUTION_STRATEGY|EXECUTION_ACCURACY_GUARD_ENABLED|FORECAST_GATE_ENABLED"'
```

### 2) Decisions vs trades in last 20 minutes
```bash
docker exec ibkr-trading-bot python3 -c "
import os, json
from datetime import datetime, timezone, timedelta
from supabase import create_client
sb=create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])
since=(datetime.now(timezone.utc)-timedelta(minutes=20)).isoformat()
pd=(sb.table('parallel_decisions').select('ts_utc,symbol,executed_signal').gte('ts_utc',since).order('ts_utc',desc=True).limit(300).execute().data or [])
th=(sb.table('trades_history').select('opened_at,symbol,status,error_message').gte('opened_at',since).order('opened_at',desc=True).limit(100).execute().data or [])
print('parallel_total=',len(pd))
print('parallel_non_hold=',sum(1 for x in pd if (x.get('executed_signal') or 'HOLD')!='HOLD'))
print('trades_last_20m=',len(th))
print(json.dumps(th[:10], ensure_ascii=False, indent=2))
"
```

### 3) Block reason from risk_events
```bash
docker exec ibkr-trading-bot python3 -c "
import os, json
from datetime import datetime, timezone, timedelta
from supabase import create_client
sb=create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])
since=(datetime.now(timezone.utc)-timedelta(minutes=40)).isoformat()
rows=(sb.table('risk_events').select('created_at,event_type,severity,message,data').gte('created_at',since).order('created_at',desc=True).limit(500).execute().data or [])
for r in rows[:100]:
    if (r.get('event_type') or '').startswith('EXECUTION'):
        print(json.dumps(r, ensure_ascii=False))
"
```

## Rollback to strict mode (after diagnostics)
```bash
cd /root/ibkr-trading-bot
if grep -q '^EXECUTION_ACCURACY_GUARD_ENABLED=' .env; then sed -i 's/^EXECUTION_ACCURACY_GUARD_ENABLED=.*/EXECUTION_ACCURACY_GUARD_ENABLED=1/' .env; else echo 'EXECUTION_ACCURACY_GUARD_ENABLED=1' >> .env; fi
if grep -q '^FORECAST_GATE_ENABLED=' .env; then sed -i 's/^FORECAST_GATE_ENABLED=.*/FORECAST_GATE_ENABLED=1/' .env; else echo 'FORECAST_GATE_ENABLED=1' >> .env; fi
docker compose up -d trading-bot
```

## Important
- `TRADING_DISABLED` in old rows should be interpreted with row timestamp.
- Always compare blocker timestamps with the exact moment config was changed.
