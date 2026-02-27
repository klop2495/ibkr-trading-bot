# Dev Changelog (append-only)

## 2025-12-26 — Role-based ClientIds + Paper/Live UI + REJECTED Fix

### Role-based ClientIds (Error 326 Fix)
- **Problem**: IB Gateway Error 326 "clientId already in use" — multiple bot components fighting for same clientId
- **Solution**: Dedicated clientId per role:
  - `IB_CLIENT_ID_MAIN=151` — main runtime loop
  - `IB_CLIENT_ID_MARKETDATA=152` — market data fetcher
  - `IBKR_EXECUTION_CLIENT_ID=153` — order execution
  - `IB_CLIENT_ID_EQUITY=154` — equity updates
- **Files changed**:
  - `app/main.py` — use role-based clientIds, ownership flag for IBKRFetcher
  - `app/broker/ibkr_client.py` — don't disconnect if not owner
  - `app/execution/service.py` — use dedicated execution clientId
  - `.env` — added all clientId variables
- **Result**: No more Error 326, each component has its own connection

### REJECTED Status Update Fix
- **Problem**: When order rejected (leverage/position limit), trade record stayed PENDING forever
- **Solution**: Update trade status to REJECTED when `ExecutionResult.executed=False`
- **File**: `app/execution/service.py` lines 1082-1095 — added status update on rejection

### Frontend: Paper/Live Trading Accounts
- **New feature**: Two tabs for Paper and Live accounts
- **Changes**:
  - Renamed "Executions" tab to "Paper Account"
  - Renamed "Simulation" tab to "Live Account" 
  - Yellow accent color (`#ca8a04`) for Live tab
  - Filter only OPEN/CLOSED trades (hide SUBMITTED, REJECTED, PENDING)
  - Added Qty column to trades table
  - Unrealized P/L displayed for open positions
  - API filter by `mode` parameter (`?mode=paper` or `?mode=live`)
  - No fallback to risk_events when specific mode requested
- **Files changed**:
  - `app/admin/executions/page.tsx` — complete rewrite with Paper/Live tabs
  - `app/api/admin/executions/route.ts` — added mode filter, fixed fallback logic
  - `app/api/admin/broker/route.ts` — added await to getServerSupabaseClient
  - `app/api/admin/performance/route.ts` — added await to getServerSupabaseClient

### trades_history Cleanup
- Deleted phantom PENDING/SUBMITTED/REJECTED records
- Created records matching actual IB positions:
  - USDCHF SELL 23,400 @ 0.7883
  - NZDUSD BUY 39,400 @ 0.5838
- Verified positions match IB Gateway via `ib.positions()`

### Verified Working
- ✅ Error 326 eliminated
- ✅ Paper tab shows 2 open positions with Qty and P/L
- ✅ Live tab empty (ready for future live account)
- ✅ Positions confirmed in IB Gateway
- ✅ REJECTED trades no longer accumulate

### Known Issue (Next Session)
- Only TechnicalAgent generates signals — other agents may be disabled or erroring

---

## 2025-12-26 — FX Funds Guard + Fix IBKR Order Execution
- Summary: Fixed orders going Inactive due to insufficient currency balance. IB doesn't allow FX spot orders that create negative balance in quote/base currency. Added FXFundsGuard to pre-check available cash and auto-adjust order size.
- Root cause analysis:
  1. `EXECUTION_DRY_RUN=1` was set — orders were only simulated, not sent to IB
  2. `IBKR_CLIENT_ID` conflict — multiple connections with same clientId caused "already in use" errors
  3. **Main issue**: IB rejects FX orders that would create negative currency balance without explicit error (order becomes `Inactive`)
  - Example: BUY EURUSD requires USD. If USD balance < required amount → Inactive
  - This explained why bracket orders were SUBMITTED then immediately CANCELLED/ERROR
- Solution:
  1. Set `EXECUTION_DRY_RUN=0` and unique `IBKR_CLIENT_ID=151`
  2. Created `app/broker/fx_funds_guard.py` with `FXFundsGuard` class:
     - Pre-checks CashBalance in required currency before placing order
     - For BUY base/quote: needs quote currency (BUY EURUSD needs USD)
     - For SELL base/quote: needs base currency (SELL EURUSD needs EUR)
     - AUTO_REDUCE policy: reduces qty to max affordable
     - SKIP policy: rejects order if insufficient funds
  3. Integrated into OMS via `place_order_with_funds_check()` method
  4. Logs events: EXECUTION_PRECHECK_FUNDS, EXECUTION_SIZE_ADJUSTED, EXECUTION_SKIPPED_INSUFFICIENT_FUNDS
- Configuration (env variables):
  - `FX_FUNDS_BUFFER=0.02` — 2% safety buffer
  - `FX_QTY_STEP=100` — round qty to nearest 100
  - `FX_MIN_IDEALPRO=20000` — IB minimum for IDEALPRO routing
  - `FX_ALLOW_ODD_LOTS=true` — allow below minimum (warning 399)
  - `FX_FUNDS_POLICY=auto_reduce` — policy: auto_reduce or skip
- Files:
  - `app/broker/fx_funds_guard.py` — FXFundsGuard class
  - `app/broker/oms.py` — integrated funds check, added `place_order_with_funds_check()`
  - `scripts/test_fx_funds_guard.py` — test script
  - `.env.example` — added FX_FUNDS_* variables
- Verification:
  - Manual test: BUY EURUSD 15000 → Filled @ 1.1776 ✅
  - Manual test: SELL EURUSD 15000 → Filled ✅
  - Orders now execute when sufficient funds available
- Related fixes:
  - Cleared old DRY_RUN trades from trades_history (were blocking max_positions limit)
  - TWS API settings: verified "Read-Only API" is OFF, "Enable ActiveX and Socket Clients" is ON

## 2025-12-23 — IB Gateway Connection Fix (Docker Networking)
- Summary: Fixed IB Gateway API connection issue. Bot was failing to connect with `CancelledError` due to incorrect port configuration and Docker network isolation.
- Root cause: 
  - IB Gateway Docker image (gnzsnz/ib-gateway) uses socat proxy: API_PORT=4002 (internal), SOCAT_PORT=4004 (external proxy)
  - Bot was trying to connect to wrong ports (4001, 4004) instead of 4002
  - Docker `network_mode: host` doesn't work properly; need shared Docker network
- Solution:
  1. Use shared Docker network `ib-gateway_default` instead of `network_mode: host`
  2. Connect to `ib-gateway:4002` (container name + correct API port)
  3. Set Socket port = 4002 in IB Gateway VNC settings (Configure → Settings → API → Settings)
  4. Ensure "Allow connections from localhost only" is UNCHECKED
- Configuration:
  - `docker-compose.yml`: Added `networks: [default, ib-gateway_default]` with external network
  - `.env`: `IB_GATEWAY_HOST=ib-gateway`, `IB_GATEWAY_PORT=4002`, `IBKR_HOST=ib-gateway`, `IBKR_PORT=4002`
- Files: `docker-compose.yml`, `.env`, `deploy/ib-gateway/README.md`
- Result: `Phase 6: Signal generation ENABLED mode=IBKR` — real market data loading successfully
- Documentation: Created `docs/IBKR_Gateway_Connection_Guide.md` with full troubleshooting guide

## 2025-12-22 — Frontend Signal Strength Page + Dashboard Improvements
- Summary: Created Signal Strength page with signal analysis, filtering, sorting. Improved main dashboard with better error handling, system status display, and loading states. Added navigation link in Sidebar.
- Files (frontend): `app/admin/signal-strength/page.tsx`, `app/page.tsx`, `app/components/Sidebar.tsx`
- Features:
  - Signal Strength page: visual strength bars, direction/R:R display, quality badges, filtering by direction, sorting by strength/R:R/symbol
  - Dashboard: real-time system status (mode, trading pairs, backend status), proper error display, auto-refresh every 60s
  - Navigation: added 💪 Signal Strength link to sidebar
- Pending: verify all pages load correctly after VPS deploy

## 2025-12-18 — Stage 4.1 — Signals params in bot_settings
- Summary: Added signals_params jsonb column and strict Pydantic contracts with configuration gating; repo roundtrips jsonb safely; runtime logs SIGNALS_RULES_NOT_SPECIFIED when config missing; added migration and validation tests.
- Files: `migrations/005_bot_settings_signals_params.sql`, `app/models/signals_params.py`, `app/models/bot_settings.py`, `app/storage/bot_settings_repo.py`, `app/signals/engine.py`, `tests/test_bot_settings.py`, `tests/test_bot_settings_repo.py`, `tests/test_signals_params.py`, `tests/test_signals_rules_warning.py`, `app/main.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-18 — Bot settings control-plane hardening
- Summary: Hardened bot settings polling gate, strict patch validation, and ensured env-only startup; added/verified bot settings tests.
- Files: `app/main.py`, `app/models/bot_settings.py`, `app/storage/bot_settings_repo.py`, `tests/test_bot_settings.py`, `tests/test_bot_settings_repo.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-18 — Stage 3 — Market Data Layer (bars + QA + warm-up + feature snapshots)
- Summary: Added market data indicators, QA, warm-up readiness, IBKR fetcher, buffers, and service orchestration with tests and warmup settings.
- Files: `app/market_data/*`, `app/models/bot_settings.py`, `app/storage/bot_settings_repo.py`, `migrations/004_bot_settings_warmup.sql`, `tests/test_market_data_*`, `docs/CHANGELOG_DEV.md`

## 2025-12-18 — Stage 3 — Market Data runtime wiring (env-gated)
- Summary: Added env-gated wiring in main loop for MarketDataService using IBKRClient; uses BotSettings symbols/warmup; fail-safe if disabled/misconfigured; no trading.
- Files: `app/main.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-18 — Stage 4 — Signals (deterministic, no persistence yet)
- Summary: Added deterministic signals contracts/engine with safe defaults; persistence disabled until rules specified; logs `SIGNALS_RULES_NOT_SPECIFIED`; tests updated; main loop only logs once, no DB writes.
- Files: `app/signals/models.py`, `app/signals/engine.py`, `tests/test_signals_engine.py`, `tests/test_signals_rules_warning.py`, `app/main.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-18 — Stage 2 — IBKR Connect Layer
- Summary: Added IBKR Pydantic contracts, ib_insync client wrapper for connect/read-only account summary and positions snapshots, contract tests, and changelog update.
- Files: `app/models/ibkr.py`, `app/broker/ibkr_client.py`, `tests/test_ibkr_client_contract.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-17 — Bot settings control-plane alignment
- Summary: Aligned BotSettings model with DB constraints, added safe defaults, tightened repo with fail-safe get/update and UUID parsing in main loop, plus tests.
- Files: `app/models/bot_settings.py`, `app/storage/bot_settings_repo.py`, `app/main.py`, `tests/test_bot_settings.py`, `tests/test_bot_settings_repo.py`

## 2025-12-16 — Step 6: persist agent_reports from decision precheck + tests
- Summary: Added agent report repo, reporting helper, precheck persist hook with fail-safe fallback, and tests for repo payload and persist behavior.
- Files: `app/storage/agent_reports_repo.py`, `app/storage/repositories.py`, `app/agents/reporting.py`, `app/decision/agents_precheck.py`, `tests/test_agent_reports_repo_contract.py`, `tests/test_decision_agents_precheck_persist.py`, `docs/agents_spec.md`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Fix: circuit breaker cooldown + strict precheck validation
- Summary: Fixed circuit breaker cooldown to allow retries after window and enforced strict per-symbol input validation in decision precheck.
- Files: `app/agents/state.py`, `app/decision/agents_precheck.py`, `tests/test_decision_agents_precheck.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Step 5: decision pre-check hook for agents + tests
- Summary: Added decision-layer agents precheck builder and gate invocation, with validation tests and docs update.
- Files: `app/decision/agents_precheck.py`, `tests/test_decision_agents_precheck.py`, `docs/agents_spec.md`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Step 4: agents gate + config + tests
- Summary: Added agents gate with env-driven config and fail-safe defaults, plus tests and documentation updates.
- Files: `app/agents/config.py`, `app/agents/gate.py`, `tests/test_agents_gate.py`, `docs/agents_spec.md`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Step 3: orchestrator + circuit breaker + tests
- Summary: Implemented agents orchestrator with deterministic aggregation, fallback policy, circuit breaker state, and unit tests covering success, timeout, error, and cooldown flows.
- Files: `app/agents/orchestrator.py`, `app/agents/state.py`, `tests/test_agents_orchestrator.py`, `docs/agents_spec.md`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Step 2: OpenAI Responses client (stdlib) + tests
- Summary: Added stdlib-based OpenAI Responses client with structured outputs validation and tests for success, schema errors, HTTP errors, and timeouts.
- Files: `app/agents/errors.py`, `app/agents/openai_client.py`, `tests/test_agents_openai_client.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Step 1: agent schemas + tests
- Summary: Added strict Pydantic agent contracts with extra-forbid and validation tests for bounds, universe/timeframes, and serialization.
- Files: `app/agents/__init__.py`, `app/agents/schemas.py`, `tests/test_agents_schemas.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Add agents gate hook
- Summary: Added decision-layer gate entry point with disabled fallback, docs update, and tests for enabled/disabled paths.
- Files: `app/agents/gate.py`, `tests/test_agents_gate.py`, `docs/agents_spec.md`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Add agent reporting and risk event hooks
- Summary: Added agent report insert-only repo, orchestrator hooks for agent reports and risk events (timeouts, CB on/off, trade blocks), with unit tests for reporting and logging.
- Files: `app/storage/agent_reports_repo.py`, `app/agents/orchestrator.py`, `tests/test_agent_reports_repo.py`, `tests/test_agents_orchestrator.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Add agents orchestrator with circuit breaker
- Summary: Implemented deterministic agents orchestrator with fallbacks, circuit breaker state, and tests covering aggregation, timeouts, and safe-mode behavior.
- Files: `app/agents/orchestrator.py`, `app/agents/state.py`, `tests/test_agents_orchestrator.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Add OpenAI agent client interface and errors
- Summary: Added agent client protocol with stdlib OpenAI client, agent errors, contract tests, and documented fallback behavior.
- Files: `app/agents/client.py`, `app/agents/errors.py`, `tests/test_agents_client_contract.py`, `docs/agents_spec.md`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Add agent schemas and tests
- Summary: Added Pydantic agent request/response/decision schemas and unit tests for bounds and serialization.
- Files: `app/agents/schemas.py`, `tests/test_agents_schemas.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Add agents spec scaffold and dev changelog
- Summary: Documented agent layer contracts, aggregation, and fail-safe fallbacks; added dev changelog.
- Files: `docs/agents_spec.md`, `docs/CHANGELOG_DEV.md`

---

## 2026-02-28 — Signal Lifecycle Manager: дедупликация + cooldown + blacklist hours

### Задача
Alt2 стратегия (ma≠pv + ADX≥30 + top-8 symbols) генерирует до 17 дублирующих сигналов для одной и той же пары в одном тренде (пример: CHFJPY 27 фев — 17 "down" подряд). Кроме того, accuracy Alt2 значительно падает в часы перехода торговых сессий (Asian→London, London→NY, NY lunch).

### Цель
1. **Дедупликация** — один активный сигнал на пару, пока не верифицирован
2. **Cooldown с эскалацией** — после ошибочных прогнозов нарастающая пауза
3. **Blacklist hours** — блокировка сигналов в часы с плохой accuracy
4. **UI визуализация** — отображение статуса lifecycle в таблице истории

### Решение: Signal Lifecycle Manager (подход A + C)

**Архитектура:**
- In-memory state manager, интегрированный в main loop (Phase 8 forecast tick)
- Lifecycle: NEW → PENDING (30 мин верификация) → VERIFIED ✓/✗ → COOLDOWN (если ошибка)

**Cooldown escalation (подход C):**
- 1-я ошибка: 30 мин cooldown (пропуск 1 цикла M15)
- 2-я подряд: 60 мин cooldown
- 3+ подряд: блок до смены UTC часа (session reset)
- Любой верный прогноз → сброс streak

**Blacklist hours:** {06, 08, 13, 14, 18} UTC — session transitions где Alt2 показывает худшую accuracy (по бэктесту 27 фев).

**Интеграция в main.py:**
- Lifecycle filter вызывается ПЕРЕД `forecast_repo.insert_batch()` — blocked сигналы получают `alt2_direction=None`
- Verification feedback: после `forecast_verifier.verify_pending()` результаты отправляются в lifecycle для обновления cooldown streak
- Dashboard API `/api/signal-lifecycle` для мониторинга (в отдельном контейнере — пока не shared)

**Frontend (history page):**
- `HourBadge` — 🟢 OK / 🔴 BL для каждого UTC часа
- `SignalStatusBadge` — ⏳ PENDING / ✅ VERIFIED ✓ / ❌ VERIFIED ✗ / ⌛ EXPIRED / ⏸ COOLDOWN
- Blacklisted rows dimmed (opacity 0.75, red tint)
- Summary stats: Active Hours accuracy vs Blacklist Hours accuracy
- API route enrichment: `hour_utc`, `hour_status`, `signal_status` для каждой записи

### Файлы

**Backend (ibkr-trading-bot):**
- `app/forecast/signal_lifecycle.py` — NEW: lifecycle manager (273 строки)
- `app/main.py` — import + init + lifecycle filter в forecast tick + verification feedback
- `app/dashboard.py` — `/api/signal-lifecycle` и `/api/signal-lifecycle/{symbol}` endpoints
- `docs/LIFECYCLE_INTEGRATION.py` — NEW: integration guide

**Frontend (ibkr-trading-fronend):**
- `app/admin/forecasts/history/page.tsx` — HourBadge, SignalStatusBadge, Hour column, Active/Blacklist stats
- `app/api/admin/forecasts/history/route.ts` — enrichment: hour_utc, hour_status, signal_status

### Конфигурация (env vars)
```
SIGNAL_LIFECYCLE_ENABLED=1
SIGNAL_COOLDOWN_1=30        # минуты, 1-я ошибка
SIGNAL_COOLDOWN_2=60        # минуты, 2-я подряд
SIGNAL_COOLDOWN_3=session   # блок до смены часа
SIGNAL_BLACKLIST_HOURS=06,08,13,14,18
```

### Предполагаемые результаты
- Устранение 80-90% дублирующих сигналов (с 17 до 1-2 на тренд)
- Повышение effective accuracy за счёт блокировки blacklist hours (исторически ~40% vs ~55% в active hours)
- Автоматическая адаптация к "плохим" периодам для отдельных пар через cooldown escalation
- Визуальный контроль lifecycle в UI для ручного мониторинга и тюнинга параметров

### Следующие шаги
- Мониторинг 3-7 дней: оценить реальное снижение дубликатов и impact на accuracy
- Тюнинг cooldown параметров при необходимости
- Рассмотреть shared state (Redis/Supabase) для lifecycle endpoint в dashboard контейнере
