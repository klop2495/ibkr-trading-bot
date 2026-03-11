# Forecast System — Technical Documentation

**Project:** IBKR Trading Bot  
**Author:** System documentation, compiled 2026-02-25  
**Status:** Live (paper trading), active development

---

## 1. Concept & Purpose

The Forecast System is a **read-only directional prediction engine** within the IBKR Trading Bot. It predicts whether each currency pair's price will go UP or DOWN across four time horizons (30m, 1h, 4h, 24h), using a **weighted indicator voting** mechanism applied to OHLC bar data from Interactive Brokers.

The system is intentionally decoupled from trade execution. It serves two functions:

1. **Directional Gate** — can block trades that oppose the forecast direction (strength > 0.6)
2. **Signal Generation** — produces binary UP/DOWN signals for monitoring, Telegram alerts, and future live trading

The forecast is not a price target. It answers one question: *"Will the price be higher or lower than now after N minutes?"*

### Alt Strategies (H30)

- `Alt2`: ma!=pv + ADX>=30 (+ eligibility marker)
- `Alt3 (legacy)`: stricter legacy variant (momentum confirms ma)
- `Alt3 (main=v2)`: independent strict weighted variant (primary working Alt3)
- `Alt4`: hour-based original/inverted mode
- `Alt5`: anti-trend strategy with configurable source (`ALT5_SOURCE_STRATEGY=alt3v2|original`) and env filters:
  - `ALT5_ENABLED`
  - `ALT5_ALLOWED_HOURS` (UTC CSV)
  - `ALT5_MIN_ADX`


## 2. Architecture Overview

```
MarketDataService (IB Gateway)
        │
        ▼
┌─────────────────────┐     ┌──────────────────────┐
│   ForecastEngine     │────▶│   ForecastResult      │
│   (engine.py)        │     │   (models/forecast.py) │
│                      │     │                        │
│  • 7 indicator votes │     │  • 4 horizon forecasts │
│  • Weighted agg.     │     │  • Advanced metadata   │
│  • Metadata calc.    │     │    (ADX, BBW, Squeeze) │
└─────────────────────┘     └──────────┬───────────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
          ┌─────────────┐   ┌─────────────────┐  ┌──────────────┐
          │  Supabase DB │   │ AdaptiveForecast │  │  Telegram    │
          │  (storage)   │   │ Gate (gate.py)   │  │  Notifier    │
          │              │   │                  │  │              │
          │ price_       │   │ • Rolling acc.   │  │ • Quality    │
          │ forecasts    │   │ • Quality filter │  │   filtered   │
          │ table        │   │ • Hours filter   │  │ • Per-signal │
          └──────┬───────┘   │ • Adv. filters   │  │   messages   │
                 │           └──────────────────┘  └──────────────┘
                 ▼
          ┌─────────────┐
          │  Verifier    │
          │ (verifier.py)│
          │              │
          │ • Checks     │
          │   actual vs  │
          │   predicted  │
          │ • Historical │
          │   bar lookup │
          └─────────────┘
```

### Key Files

| File | Purpose |
|------|---------|
| `app/forecast/engine.py` | Core forecast computation, indicator voting |
| `app/forecast/indicators_vote.py` | Individual indicator functions (+1/−1/0 voters) |
| `app/forecast/gate.py` | Adaptive gate: rolling accuracy, quality filter, hours, advanced filters |
| `app/forecast/verifier.py` | Post-hoc verification: compares forecast vs actual price |
| `app/models/forecast.py` | Pydantic data models (ForecastResult, ForecastHorizon) |
| `app/notifications/telegram.py` | Telegram alerts for quality-filtered signals |

### Frontend Pages

| Page | URL | Purpose |
|------|-----|---------|
| Forecast Gate | `/admin/forecast-gate` | Adaptive gate status, blocked/watch/active pairs |
| Forecast History | `/admin/forecasts` | Historical forecasts with verification results |
| Binary Signals | `/admin/binary-signals` | Quality-filtered UP/DOWN signals with live status |


## 3. Data Flow

### 3.1 Signal Generation (every 60 seconds)

1. `main.py` loop calls `ForecastEngine.compute_all(market_data_service, symbols)`
2. Engine requests OHLC bars from in-memory cache for M15, H1, H4 timeframes
3. For each symbol × horizon, collects 7 weighted indicator votes
4. `aggregate_weighted_votes()` produces direction, confidence, strength, aligned count
5. Engine also computes **advanced metadata**: ADX, BB Width, BB Squeeze, MTF conflict
6. `ForecastResult.to_db_row()` flattens to Supabase `price_forecasts` table
7. Gate receives forecasts via `update_forecasts_batch()`
8. TelegramNotifier checks quality filter and sends alerts for new/changed signals

### 3.2 Verification (every 60 seconds)

1. `ForecastVerifier.verify_pending()` queries unverified forecasts older than 30 min
2. For each horizon that has elapsed, looks up historical close price from `market_snapshots`
3. Compares predicted direction with actual price movement
4. Updates `h30_correct`, `h30_actual_price`, etc. in database
5. Marks row as `verified_at` when all horizons checked (or after 4h force-timeout)

### 3.3 Gate Check (on trade attempt)

1. `AdaptiveForecastGate.check(symbol, trade_direction)` runs 4 checks in order:
   - **Rolling accuracy**: is this pair's historical accuracy above threshold?
   - **Quality filter**: does current forecast meet MEDIUM confidence + aligned ≥ 4?
   - **Hours filter**: is current UTC hour in allowed trading hours?
   - **Direction alignment**: does trade direction match forecast?
2. Returns `(allowed: bool, reason: Optional[str])`


## 4. Indicator Voting System

### 4.1 Current Configuration (as deployed 2026-02-25)

The H30 horizon uses **M15 bars** with the following 7 voters:

| # | Indicator | Function | Logic | Weight |
|---|-----------|----------|-------|--------|
| 1 | MA Cross (inverted) | `vote_ma_cross_inverted()` | SMA(20) > SMA(50) → **DOWN** | 1.0 |
| 2 | RSI Trend | `vote_rsi_trend()` | RSI>50 rising → UP; RSI<50 falling → DOWN | 1.0 |
| 3 | RSI Momentum (inverted) | `vote_rsi_momentum()` | RSI>70 → **UP** (momentum); RSI<30 → **DOWN** | 1.0 |
| 4 | Price vs MA | `vote_price_vs_ma()` | Close > SMA(20) → UP | 1.0 |
| 5 | Momentum (ROC) | `vote_momentum()` | Close > Close[6 bars ago] → UP | 1.0 |
| 6 | ATR Trend | `vote_atr_trend()` | ATR expanding + trend → confirms direction | 1.0 |
| 7 | Secondary MA (inverted) | `vote_ma_cross_inverted()` on H1 | Only for H60+ horizons (not H30) | 1.0 |

**For H30 specifically**: Vote 7 is absent (no secondary TF), so 6 votes maximum.

### 4.2 Aggregation

`aggregate_weighted_votes()` computes:
- **weighted_sum** = Σ(vote × weight) for non-neutral votes
- **strength** = |weighted_sum| / total_active_weight
- **direction** = UP if weighted_sum > 0, else DOWN
- **aligned** = count of active votes agreeing with direction
- **confidence**: strength ≥ 0.7 → HIGH, ≥ 0.4 → MEDIUM, < 0.4 → LOW

With all weights = 1.0, this is equivalent to simple majority voting.

### 4.3 Horizon Configuration

| Horizon | Primary TF | Secondary TF | Momentum Lookback | MA Fast/Slow |
|---------|-----------|-------------|-------------------|-------------|
| 30 min | M15 | None | 6 bars | 20/50 |
| 60 min | H1 | M15 | 10 bars | 20/50 |
| 4 hours | H4 | H1 | 10 bars | 20/50 |
| 24 hours | H4 | H1 | 24 bars | 50/200 |

### 4.4 Known Issues with Current Indicators

**MA Cross Inverted (Vote 1)** systematically votes against the trend. In a steady uptrend, it votes DOWN. This was intentionally inverted based on Feb 23 empirical analysis showing raw `vote_ma_cross` was contrarian (−28% delta: accuracy *drops* when it agrees with forecast). However, inverting it creates a structural conflict: in a clean trend, votes 1 (inverted MA) and 4 (price_vs_ma) cancel each other out, reducing aligned count.

**RSI Momentum (Vote 3)** fires very rarely (RSI >70 or <30 on M15 is uncommon). When it does fire, it represents extreme conditions where the inverted logic (momentum continuation vs mean-reversion) may not always hold.

**RSI Trend (Vote 2)** returns NEUTRAL most of the time (requires RSI >50 AND rising, or <50 AND falling). Low participation rate.

**Practical consequence**: In most cases, the effective voting is dominated by 3 indicators: momentum (ROC), price_vs_ma, and one of the MA cross variants. ATR Trend adds a 4th when volatility is expanding. This means aligned count rarely exceeds 4-5/6.


## 5. Quality Filtering & Gate System

### 5.1 Quality Filter (Empirical, 2026-02-24)

Based on analysis of ~1000 verified H30 forecasts:

| Filter | Accuracy | Notes |
|--------|----------|-------|
| No filter (all forecasts) | ~48% | Worse than coin flip |
| MEDIUM confidence only | ~55% | HIGH is a trap (lagging consensus) |
| MEDIUM + aligned ≥ 4 | ~64% | Current production filter |
| MEDIUM + aligned ≥ 4 + BBW ≥ 0.002 | ~74% | Proposed (pending deployment) |
| MEDIUM + aligned ≥ 5 + BBW ≥ 0.002 | ~82% | "Turbo mode" (fewer signals) |

**Why HIGH confidence is worse than MEDIUM**: When most indicators agree (HIGH), it means the move has already happened. The MA cross inverted starts agreeing with trend-following indicators only at trend exhaustion. HIGH confidence = consensus = reversal risk.

### 5.2 Adaptive Rolling Accuracy Gate

The gate tracks rolling accuracy per currency pair over the last 50 verified forecasts:
- **Block** if accuracy < 55%
- **Unblock** if accuracy ≥ 60% (hysteresis prevents toggling)
- **Minimum 15 samples** before deciding

As of 2026-02-25: 9 blocked pairs, 2 watch, 5 active.

### 5.3 Advanced Filters (Metadata, Log-Only)

Computed by `engine.py` on every forecast, stored in DB, but NOT blocking by default:

| Filter | Column | Computation | Blocking? |
|--------|--------|-------------|-----------|
| ADX | `adx_value` | ADX(14) on M15 | No (env: `GATE_ADX_ENABLED`) |
| BB Width | `bb_width` | Bollinger Band width / SMA, M15(20,2.0) | No (env: `GATE_BBW_ENABLED`) |
| BB Squeeze | `bb_squeeze` | BB inside Keltner Channel | No (env: `GATE_SQUEEZE_ENABLED`) |
| MTF Conflict | `mtf_conflict` | H4 direction opposes H30 (when H4 ADX>25) | No (env: `GATE_MTF_VETO_ENABLED`) |
| Spread | `spread_pips` | Current broker spread | No (env: `GATE_SPREAD_ENABLED`) |

Each can be activated independently via environment variable.

### 5.4 Trading Hours Filter

| Period | UTC Hours | Rationale |
|--------|-----------|-----------|
| Morning (London open) | 08-11 | 78% accuracy empirically |
| Late session | 17-19 | 86% accuracy (small sample) |
| **Excluded**: NY open | 13-16 | 44% accuracy — news-driven reversals |
| **Excluded**: After NYSE close | 20-23 | Low volatility, unreliable |


## 6. BB Width — The Strongest Filter

### 6.1 Discovery (2026-02-25)

Analysis of 61 quality-filtered (MEDIUM + aligned ≥ 4) verified H30 signals revealed BB Width as the strongest single predictor of forecast accuracy:

| BBW Range | Accuracy | Interpretation |
|-----------|----------|----------------|
| < 0.002 | 45% (10/22) | **Worse than random** — ranging, no trend |
| 0.002–0.003 | 69% (11/16) | Moderate volatility, indicators start working |
| 0.003–0.005 | 71% (12/17) | Good volatility, clear trends |
| > 0.005 | 100% (6/6) | High volatility, strong moves |

**Filter effect**: BBW ≥ 0.002 improves accuracy from 64% → 74.4% (Wilson LB from 51.4% → 58.9%).

### 6.2 Why It Works

BB Width measures normalized volatility (band width / SMA). When BBW is low (< 0.002), the market is in a tight range — all trend-following indicators (momentum, price_vs_ma, MA cross) generate noise because there's no trend to follow. The BBW filter acts as a **regime detector**: it doesn't predict direction, it predicts whether direction-predicting indicators will work.

### 6.3 Comparison with Other Filters

| Filter | Accuracy | Wilson LB | Volume Impact |
|--------|----------|-----------|---------------|
| BBW ≥ 0.002 | 74.4% | 58.9% | Removes 22 signals (36%) |
| Aligned ≥ 5 | 75.0% | — | Removes 41 signals (67%) |
| Squeeze = False | 65.5% | — | Removes 3 signals (5%) |
| MTF conflict = False | 65.5% | — | Removes 6 signals (10%) |
| ADX buckets | Non-monotonic | — | Unreliable |

BBW ≥ 0.002 provides the best accuracy/volume tradeoff.

### 6.4 BBW Ratio (Expansion Detection) — Under Testing

BBW_ratio = current_BBW / average_BBW_over_last_10_readings. Measures whether volatility is expanding or contracting relative to recent history.

Preliminary results (34 enriched signals, Feb 25):

| Ratio Range | Accuracy | Interpretation |
|-------------|----------|----------------|
| < 0.7 (contracting) | 17% (1/6) | Strong contraction = indicators fail |
| 0.7–0.9 (mild contraction) | 62% (5/8) | Moderate |
| 0.9–1.1 (stable) | 80% (4/5) | Stable volatility = best |
| 1.1–1.3 (expanding) | 83% (5/6) | Expansion = trending, good |
| 1.3–1.6 (strong expand) | 38% (3/8) | Too much expansion = reversal risk? |
| 1.6+ (explosion) | 0% (0/1) | Extreme = unreliable |

**Tentative finding**: Stable-to-moderate expansion (ratio 0.9–1.3) is the sweet spot. Both strong contraction and strong expansion degrade accuracy. Needs 7-day validation with larger sample.


## 7. Change History

### Phase 1: Initial System (2026-02-21)

Created forecast system with 6 indicators, all weight=1, simple majority voting:

1. `vote_ma_cross` — MA crossover (trend-following)
2. `vote_rsi_trend` — RSI direction
3. `vote_rsi_extreme` — RSI mean-reversion (oversold → UP, overbought → DOWN)
4. `vote_price_vs_ma` — Price vs SMA
5. `vote_momentum` — Rate of Change
6. `vote_atr_trend` — ATR expansion confirms trend

Baseline accuracy: ~48% overall, ~61% on H30 with non-neutral forecasts.

### Phase 2: Empirical Analysis & Inversion (2026-02-23)

Analysis of ~318 verified H30 forecasts revealed:
- `vote_ma_cross`: **CONTRARIAN** (−28% delta) — accuracy drops when it agrees
- `vote_rsi_extreme`: **CONTRARIAN** (−88% delta) — momentum, not mean-reversion
- `vote_momentum`: Only reliable predictor (+18% delta, 85% accuracy when aligned)

**Changes deployed:**
- Added `vote_ma_cross_inverted()` — negates raw MA cross
- Added `vote_rsi_momentum()` — negates raw RSI extreme
- Added `aggregate_weighted_votes()` — supports (vote, weight) tuples
- Weights changed: momentum=2.0, atr_trend=0.5, secondary_ma=0.5

### Phase 3: Overfitting & Rollback (2026-02-23 evening → 2026-02-24)

Custom weights caused immediate regression:
- Overall accuracy: 53% → 35%
- HIGH confidence: 50% → **17%**
- Root cause: weight optimization on 318 samples = overfitting

**Rollback**: All weights restored to 1.0. Inverted functions remained active. Accuracy recovered to ~48% within hours.

**Lesson learned**: Gate-based filtering (which signals to trust) is more effective than weight tuning (how to combine indicators) on small samples.

### Phase 4: Quality Filter (2026-02-24)

Instead of changing indicators, applied post-hoc filtering:
- MEDIUM confidence + aligned ≥ 4 = **86% accuracy** on 73 quality samples
- Adaptive rolling accuracy gate with hysteresis (block<55%, unblock≥60%)
- Trading hours initially set to 8-11, 13-16 UTC

### Phase 5: Advanced Filters & Notifications (2026-02-25)

- Added ADX, BB Width, BB Squeeze, MTF conflict as metadata (log-only, not blocking)
- Telegram notifications: separate message per signal with colored arrows
- Daily report: session-end summary with accuracy breakdown
- Binary Signals UI page with live filtering

### Phase 6: Trading Hours Optimization (2026-02-25)

Empirical analysis of 286 verified H30 forecasts by UTC hour:
- 08-11 UTC: **78% accuracy** ✅
- 13-16 UTC: **44% accuracy** ❌ (NY open = unpredictable)
- 17-19 UTC: **86% accuracy** ✅ (small sample)

Hours changed from 8-11+13-16 to **8-11+17-19 UTC**.

### Phase 7: BB Width Discovery (2026-02-25, current)

BBW ≥ 0.002 identified as strongest quality filter. BBW_ratio under testing. Next: 7-day validation and potential deployment as blocking filter.


## 8. Database Schema

### `price_forecasts` table

| Column | Type | Description |
|--------|------|-------------|
| id | serial | Primary key |
| ts_utc | timestamptz | Forecast generation time |
| symbol | text | Currency pair (e.g., EURUSD) |
| base_price | float | Close price at forecast time |
| data_quality | text | "ok" / "unknown" |
| flags | text[] | Error/info flags |
| all_aligned | bool | All horizons same direction |
| dominant_direction | text | Most common direction |
| h30_direction | text | up/down/neutral |
| h30_confidence | text | low/medium/high |
| h30_strength | float | 0.0–1.0 |
| h30_aligned | int | Indicators agreeing |
| h30_total | int | Total indicators that voted |
| h30_correct | bool | NULL until verified |
| h30_actual | text | Actual direction |
| h30_actual_price | float | Price at horizon end |
| *(same for h60, h240, h1440)* | | |
| verified_at | timestamptz | When verification completed |
| adx_value | float | ADX(14) on M15 |
| bb_width | float | Bollinger Band width (normalized) |
| bb_squeeze | bool | BB inside Keltner |
| mtf_conflict | bool | H4 opposes H30 |
| mtf_h4_direction | text | H4 trend direction |
| spread_pips | float | Broker spread (if available) |

### `market_snapshots` table

Used for historical price lookups during verification. Contains M15/H1/H4 bars with `close`, `atr`, `rsi`, `ma_fast`, `ma_slow`, `spread`. **Does not contain OHLC** (only close), which limits some retrospective analyses.


## 9. Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FORECAST_GATE_ENABLED` | 1 | Enable/disable gate |
| `FORECAST_GATE_WINDOW` | 50 | Rolling accuracy window |
| `FORECAST_GATE_BLOCK_BELOW` | 0.55 | Block threshold |
| `FORECAST_GATE_UNBLOCK_ABOVE` | 0.60 | Unblock threshold |
| `FORECAST_GATE_MIN_SAMPLES` | 15 | Min samples before decision |
| `FORECAST_GATE_REFRESH` | 300 | Refresh interval (seconds) |
| `FORECAST_GATE_QUALITY_FILTER` | 1 | Enable confidence+aligned filter |
| `FORECAST_GATE_MIN_ALIGNED` | 4 | Minimum aligned indicators |
| `FORECAST_GATE_REQUIRED_CONFIDENCE` | medium | Required confidence level |
| `FORECAST_GATE_HOURS_FILTER` | 1 | Enable hours filter |
| `FORECAST_GATE_TRADING_HOURS` | 08,09,10,11,17,18,19 | Allowed UTC hours |
| `GATE_ADX_ENABLED` | 0 | ADX blocking (log-only default) |
| `GATE_SQUEEZE_ENABLED` | 0 | Squeeze blocking |
| `GATE_MTF_VETO_ENABLED` | 0 | MTF conflict blocking |
| `GATE_SPREAD_ENABLED` | 0 | Spread blocking |


## 10. Lessons Learned

### What Works
1. **Post-hoc filtering > weight tuning**: Quality filters (confidence, aligned count, BB Width) improve accuracy without overfitting risk
2. **BB Width as regime filter**: Low volatility = indicators fail, regardless of their individual accuracy
3. **MEDIUM confidence sweet spot**: HIGH confidence signals lag the market (consensus at reversal)
4. **Trading hours matter**: Session-specific accuracy varies 44%–86%
5. **Adaptive gate with hysteresis**: Prevents toggling at threshold boundaries

### What Doesn't Work
1. **Weight optimization on small samples**: 318 samples → overfitting → 17% accuracy on HIGH confidence
2. **ADX as filter**: Non-monotonic relationship with accuracy (ADX 15-20 = 75%, ADX 20-25 = 50%, ADX 25-35 = 70%)
3. **MTF conflict filter**: Removes equal numbers of correct and incorrect signals (no net benefit)
4. **All-horizons-aligned filter**: Too restrictive, removes too many good signals

### Open Questions
1. Should inverted indicators (ma_cross_inv, rsi_momentum) be reverted to standard versions?
2. Is BBW ≥ 0.002 stable over 7 days, or is it a single-day artifact?
3. Would removing ma_cross entirely (instead of inverting) improve accuracy?
4. Should individual indicator votes be logged to DB for per-indicator accuracy tracking?
5. Is BBW_ratio (expansion/contraction) additive to absolute BBW threshold?


## 11. Proposed Next Steps

### Short-term (validated by data)
- Deploy BBW ≥ 0.002 as blocking filter (pending 7-day validation)
- Deploy squeeze=False as blocking filter (small benefit, low cost)
- Run 7-day BBW_ratio analysis with cross-day history to resolve cold-start

### Medium-term (needs more data)
- Add individual vote logging to DB (per-indicator accuracy tracking)
- Test removing/replacing ma_cross_inverted with a different indicator
- Evaluate if trading hours filter is redundant when BBW filter is active
- Consider per-pair BBW thresholds (JPY pairs may have different absolute levels)

### Long-term (strategic)
- Transition from paper to live trading with MEDIUM+BBW quality filter
- Build automated weekly reports comparing filter configurations
- Explore ML-based indicator selection (requires 30+ days of data)
