# CHANGELOG

Все изменения в проектах ibkr-trading-bot и ibkr-trading-fronend.

## [2024-12-20] — Audit & Stabilization Release

### Фаза 1 — Unblock Deployment

#### Backend (ibkr-trading-bot)

- **requirements.txt**: Убран дубль `supabase`, зафиксированы версии:
  - `supabase==2.6.0`
  - `httpx==0.26.0`  
  - `postgrest==0.13.0`

- **app/storage/db.py**: Добавлен graceful fallback при отсутствии env:
  - Создан `_NullSupabaseClient` stub-класс
  - При отсутствии `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` логируем в stderr
  - Приложение запускается для диагностики, но операции с БД выбрасывают `RuntimeError`

- **app/storage/repositories.py**: Исправлен паттерн insert:
  - Все repos используют `.insert().execute()` вместо `.insert().select()`
  - При конфликте 23505 (unique violation) — fallback select по уникальным полям
  - Применено к: `ExecutionReportsRepo`, `OrderIntentsRepo`, `BrokerRequestsRepo`, `ReconciliationReportsRepo`

#### Frontend (ibkr-trading-fronend)

- **app/admin/control/ControlDashboard.tsx**:
  - Добавлены типы: `PreviewRow`, `ChainPayload`, `DecisionRow`, `VerdictRow`
  - `formatDate()` — null guard + `isNaN` проверка
  - `renderValue()` — JSON.stringify для объектов (нет `[object Object]`)
  - `WARNING_TEXT` — RU локализация кодов предупреждений

- **app/api/admin/control/*/route.ts**:
  - Возвращают 401/403/500 (не 200 на ошибки)
  - Добавлен `correlation_id` для трейсинга
  - Типизация payloads

- **Удалён `.eslintignore`** — ignores теперь в `eslint.config.mjs`

### Фаза 2 — Stabilize

#### Backend

- **app/risk/engine_v1.py**: 
  - `ValueError("decision.id is required")` вместо генерации фейкового UUID

- **app/main.py**: 
  - Унифицирован ключ телеметрии `signal_preview_id` (убран `preview_id`)

#### Frontend

- **app/login/page.tsx**: 
  - Исправлен паттерн hooks (нет проблемного setState в useEffect)

### Фаза 3 — Harden

#### A) CI/CD (GitHub Actions)

- **Backend** `.github/workflows/ci.yml`:
  - Python 3.11
  - Install dependencies + ruff + pytest
  - Lint with ruff (continue-on-error)
  - Type check with mypy (continue-on-error)
  - Run pytest tests (130 tests)
  - Compile check

- **Frontend** `.github/workflows/ci.yml`:
  - Node.js 20
  - npm ci
  - npm run lint
  - npx tsc --noEmit
  - npm test (vitest)
  - npm run build

#### B) Frontend Tests

- **package.json**: Добавлены vitest + @vitejs/plugin-react
- **vitest.config.ts**: Конфигурация тестового фреймворка
- **__tests__/api-contracts.test.ts** (16 tests):
  - Контракты ответов API (401, 403, 500)
  - Структура previews/chain/meta endpoints
  - Warning codes validation
- **__tests__/ui-smoke.test.ts** (33 tests):
  - `formatDate()` — null/invalid handling
  - `formatNumber()` — null/NaN handling
  - `renderDirection()` — LONG/SHORT/FLAT → RU
  - `renderSetup()` — setup types → RU
  - `renderValue()` — objects/arrays/primitives
  - Edge cases с null fields

---

## [2024-12-20] — Phase D: IBKR Trading Logic

### Новые модули

#### broker/oms.py — Order Management System
- `IBKROrderRequest` — модель запроса на размещение ордера
- `IBKROrderState` — состояние ордера (tracking)
- `IBKRFill` — модель исполнения (fill)
- `IBKROMS` — OMS с event handlers:
  - `place_order()` — размещение ордера через ib_insync
  - `cancel_order()` — отмена ордера
  - `get_order_state()` — получение состояния
  - `get_active_orders()` — список активных ордеров
  - Event callbacks: `on_order_status`, `on_fill`, `on_error`

#### broker/connection_manager.py — Connection Lifecycle
- `ConnectionConfig` — конфигурация подключения (host, port, client_id)
- `ConnectionStats` — статистика соединения
- `IBKRConnectionManager`:
  - Auto-reconnect с exponential backoff
  - Health monitoring thread
  - Thread-safe state management

#### pm/position_sizer.py — Position Sizing
- `PositionSizerConfig` — лимиты риска и позиций
- `PositionSizeResult` — результат расчёта
- `PositionSizer`:
  - `calculate()` — расчёт по stop loss в pips
  - `calculate_from_prices()` — расчёт по entry/stop ценам
  - Поддержка JPY пар (pip = 0.01)
  - Risk modifier от control plane
  - Lot step rounding

#### execution/ibkr_engine.py — Execution Engine
- `IBKRExecutionEngine`:
  - Интеграция с OMS и Position Sizer
  - `plan()` — создание execution plan
  - `execute()` — реальное исполнение через IBKR
  - Логирование в risk_events

### Тесты (34 новых)

- `tests/test_position_sizer.py` — 11 тестов
- `tests/test_ibkr_oms.py` — 13 тестов  
- `tests/test_connection_manager.py` — 10 тестов

---

## [2024-12-20] — Task #1: Execution Integration

### Новые модули

#### execution/service.py — ExecutionService
- `ExecutionMode` — режимы: DISABLED, DRY_RUN, PAPER, LIVE
- `ExecutionResult` — результат выполнения
- `ExecutionService`:
  - `execute()` — исполнение на основе decision + verdict
  - `_determine_mode()` — автоматический выбор режима из env
  - `_determine_side()` — определение BUY/SELL из flags/commentary
  - `_calculate_position_size()` — интеграция с PositionSizer
  - Dry-run mode для тестирования без IBKR

### Изменения в main.py

- Импорт и инициализация `ExecutionService`
- Новая функция `run_execution_tick()` — обработка pending verdicts
- Интеграция в main loop после backfill

### Новые env переменные

| Переменная | Default | Описание |
|------------|---------|----------|
| `EXECUTION_ENABLED` | 0 | Включить execution |
| `EXECUTION_DRY_RUN` | 0 | Только логировать |
| `DEFAULT_EQUITY` | 10000 | Equity для dry-run |
| `EXECUTION_TICK_LIMIT` | 10 | Макс verdicts за tick |

### Тесты (24 новых)

- `tests/test_execution_service.py` — 24 теста

---

## Метрики после изменений

| Проект | Тесты | Lint | Build |
|--------|-------|------|-------|
| Backend | 188 passed | ✅ | N/A |
| Frontend | 49 passed | ✅ | 5.9s |

## Известные warnings (не критичные)

- **pyiceberg/storage3**: Pydantic v2 deprecation warnings — сторонние пакеты
- **Vite CJS**: "The CJS build of Vite's Node API is deprecated" — не влияет на работу
