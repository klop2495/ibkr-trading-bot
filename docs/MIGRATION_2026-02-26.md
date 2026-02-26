# Migration Context — IBKR Trading Bot Forecast System
**Date:** 2026-02-26 ~12:00 UTC
**Purpose:** Context transfer to next Claude Desktop project chat
**Full system docs:** `docs/FORECAST_SYSTEM.md`

---

## Infrastructure

| Component | Location | Container |
|-----------|----------|-----------|
| VPS | 65.108.83.67 (Hetzner) | — |
| Backend | `/root/ibkr-trading-bot/` | `ibkr-trading-bot` |
| Frontend | `/root/ibkr-trading-frontend/` | `ibkr-dashboard` + PM2 |
| IB Gateway | `/root/ib-gateway/` | `ib-gateway` (port 4004, paper) |
| VNC | port 5900 | — |
| Local backend (MCP) | `/Users/oleggnikishin/AI PROJECTS/Forex trading bot/ibkr-trading-bot/` | — |
| Local frontend (MCP) | `/Users/oleggnikishin/AI PROJECTS/Forex trading bot/ibkr-trading-fronend/` | — |

### Restart sequence (after IB Gateway disconnect)
```bash
cd /root/ib-gateway && docker compose restart
sleep 30
cd /root/ibkr-trading-bot && docker compose restart
```

---

## What Was Done Feb 26

### 1. Created `docs/FORECAST_SYSTEM.md`
Comprehensive documentation: architecture, all 7 indicators with descriptions, quality filters, empirical results, change history (Feb 21-25), BB Width discovery, lessons learned.

### 2. Added individual vote logging (`h30_votes_json`)

**DB migration (executed):**
```sql
ALTER TABLE price_forecasts ADD COLUMN IF NOT EXISTS h30_votes_json jsonb;
```

**Code changes:**
- `app/models/forecast.py` — added `h30_votes_json: Optional[dict] = None`
  - **BUG FIXED:** file had duplicate field definition (Optional[dict] + Optional[str]). Pydantic used second (str), rejected dict. Removed str duplicate via `sed` on server.
- `app/forecast/engine.py` — `_compute_horizon()` now builds `named_votes: Dict[str, int]` per indicator, stored for H30 via `self._last_votes`. `_compute_symbol()` captures it and passes to `ForecastResult(h30_votes_json=...)`.

**Format stored in DB:**
```json
{"ma_cross_inv": -1, "rsi_trend": 0, "rsi_momentum": 0, "price_vs_ma": 1, "momentum": 1, "atr_trend": 0}
```
Values: +1=UP, -1=DOWN, 0=NEUTRAL

**Status:** ✅ Working. Logging since 09:45 UTC Feb 26.

### 3. Disabled hours filter for statistics collection
```bash
# Added to /root/ibkr-trading-bot/.env:
FORECAST_GATE_HOURS_FILTER=0
```
⚠️ **RE-ENABLE before live trading:** change to `FORECAST_GATE_HOURS_FILTER=1`

### 4. Created utility scripts
- `scripts/check_health.sh` — post-restart health check
- `scripts/report_indicators.sh [hours]` — indicator accuracy report from votes data

---

## Current Production Indicator Config

Engine: 7 voters for H30 horizon, M15 bars, all weights = 1.0

| # | Key | Function | Participation | Delta | Verdict |
|---|-----|----------|--------------|-------|---------|
| 1 | ma_cross_inv | Inverted SMA(20)>SMA(50) | 100% | +2% | WEAK — almost no signal |
| 2 | rsi_trend | RSI>50 rising→UP | 52% | −33%* | Often neutral |
| 3 | rsi_momentum | Inverted RSI extreme (>70/<30) | 37% | n/a | Rarely fires |
| 4 | price_vs_ma | Close>SMA(20)→UP | 100% | **+11%** | **BEST predictor** |
| 5 | momentum | Close>Close[6]→UP | 100% | **−8% to −18%** | **CONTRARIAN** |
| 6 | atr_trend | ATR expanding + trend | 15% | n/a | Almost always neutral |
| 7 | secondary_ma | Inverted MA cross on H1 | H60+ only | n/a | Not in H30 |

*Small sample on delta values. Need 150+ verified votes rows.

**Key finding:** `price_vs_ma` is only consistently good indicator. `momentum` behaves as contrarian signal — when it agrees with forecast, accuracy is LOWER than when it opposes. Needs confirmation on larger sample.

---

## Advanced Filter Results (1000 verified samples)

### Individual filters
| Filter | Accuracy | N | Verdict |
|--------|----------|---|---------|
| No filter | 45.2% | 1000 | Below random |
| MEDIUM + aligned≥4 | 49.0% | 192 | Barely random |
| ADX 0-15 (weak trend) | 60% | 47 | Interesting |
| ADX 35+ (strong trend) | 38% | 119 | Toxic — late entry |
| BBW < 0.002 | ~43% | mixed | Low volatility = noise |
| BBW ≥ 0.002 | 50%+ | mixed | Sufficient volatility |
| BBW 0.0015-0.002 | 38% | 106 | **Toxic zone confirmed** |
| MTF conflict=True | 37% | 92 | H4 disagrees = bad |
| MTF conflict=False | 49% | 447 | Better |

### Combo filters (on quality base MED+al≥4)
| Combo | Accuracy | Wilson LB | N |
|-------|----------|-----------|---|
| Baseline | 49% | 42% | 192 |
| +BBW≥0.002 | 59% | 47% | 73 |
| +MTF=False | 49% | 41% | 161 |
| **BBW≥0.002 + MTF=False** | **68%** | **55%** | **56** |
| ALL filters | 68% | 55% | 56 |

**Best combo: MEDIUM + aligned≥4 + BBW≥0.002 + MTF_conflict=False → 68%**

⚠️ n=56 is small. Need 150+ to confirm. Wilson LB 55% is promising but not conclusive.

---

## Database State

| Column | Since | Rows |
|--------|-------|------|
| price_forecasts total | Feb 21 | ~13,300 |
| h30_correct (verified) | Feb 21 | ~6,080 |
| bb_width, bb_squeeze, adx_value, mtf_conflict | Feb 25 | ~560 |
| h30_votes_json | **Feb 26 09:45** | ~50+ growing |

**Missing columns in DB (exist in Pydantic model only):**
- `vol_regime_blocked` — will cause insert error if populated
- `vol_regime_reason` — same

---

## Known Issues

1. **Inverted indicators active in production** — `vote_ma_cross_inverted` and `vote_rsi_momentum` are inverted. User believed they were reverted but they weren't. Decision pending.

2. **Duplicate field bug (FIXED)** — `forecast.py` had `h30_votes_json` defined twice. Fixed on server via sed, fixed locally via MCP edit. **Verify local file is clean:**
   ```bash
   grep "h30_votes_json" app/models/forecast.py
   # Should show exactly 2 lines: field definition + to_db_row usage
   ```

3. **vol_regime_blocked/reason** — in model but not in DB table. If engine populates them → Supabase insert will fail. Need either DB migration or remove from model.

4. **BBW data only since Feb 25** — cannot do 7-day retrospective analysis.

---

## Pending Tasks (Priority)

### P1: Accumulate votes statistics
- Hours filter disabled, collecting 24/7
- Target: 150+ verified rows with h30_votes_json
- ETA: ~24-48 hours
- Check: `bash scripts/report_indicators.sh`

### P2: Validate BBW+MTF filter combo
- Once n≥150, confirm 68% holds
- If confirmed → implement as blocking filter in `gate.py`

### P3: Decide indicator changes based on votes data
- Invert or remove momentum?
- Remove ma_cross_inv (useless)?
- Replace rsi_momentum/rsi_trend (too often neutral)?

### P4: BBW_ratio 7-day analysis
- Sweet spot: ratio 0.9-1.3 (preliminary 1-day data)
- Need cross-day BBW data accumulation

### P5: Re-enable hours filter before live trading
- `FORECAST_GATE_HOURS_FILTER=1` in .env

---

## Quick Commands

```bash
# Health check after restart
bash scripts/check_health.sh

# Indicator accuracy report
bash scripts/report_indicators.sh        # last 24h
bash scripts/report_indicators.sh 48     # last 48h

# Check votes are logging
docker exec ibkr-trading-bot python3 -c "
from app.storage.db import SupabaseDB
db = SupabaseDB()
r = db.client.table('price_forecasts').select('ts_utc,symbol,h30_votes_json').not_.is_('h30_votes_json','null').order('ts_utc',desc=True).limit(3).execute()
for x in (r.data or []): print(x['ts_utc'][:19], x['symbol'], x['h30_votes_json'])
"

# Count votes rows
docker exec ibkr-trading-bot python3 -c "
from app.storage.db import SupabaseDB
db = SupabaseDB()
r = db.client.table('price_forecasts').select('id', count='exact').not_.is_('h30_votes_json','null').execute()
print(f'Rows with votes: {r.count}')
"
```

## Key Files

| File | What |
|------|------|
| `app/forecast/engine.py` | Forecast computation + votes logging |
| `app/forecast/indicators_vote.py` | 7 indicator vote functions |
| `app/forecast/gate.py` | Adaptive gate, quality/hours/advanced filters |
| `app/forecast/verifier.py` | Post-hoc H30 verification |
| `app/models/forecast.py` | Pydantic models (ForecastResult, ForecastHorizon) |
| `app/notifications/telegram.py` | Signal alerts + daily report |
| `docs/FORECAST_SYSTEM.md` | Full system documentation |
| `scripts/check_health.sh` | Post-restart health check |
| `scripts/report_indicators.sh` | Indicator accuracy report |
