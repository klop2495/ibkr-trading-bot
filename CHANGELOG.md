# CHANGELOG

Все изменения в проектах ibkr-trading-bot и ibkr-trading-fronend.

## [2025-12-23] — Quorum Voting v2 + IB Gateway Connection Fix

### 🎯 Quorum Voting v2

#### Проблема
Предыдущая система требовала unanimous voting — любой агент с HOLD блокировал торговлю, что делало веса агентов бессмысленными.

#### Решение
Внедрена система взвешенного голосования с настраиваемыми порогами:

| Параметр | Значение | Описание |
|----------|----------|----------|
| `QUORUM_THRESHOLD_WITH_ENTRY` | 0.60 | Порог при entry_triggered=True |
| `QUORUM_THRESHOLD_NO_ENTRY` | 0.75 | Строже при entry_triggered=False |
| `MIN_ACTIVE_WEIGHT` | 0.50 | Минимум участия для кворума |
| `RISK_MOD_CAP_NO_ENTRY` | 0.70 | Ограничение risk_modifier без entry |

#### Логика голосования
```python
# Vote mapping
LONG/SHORT → ALLOW (вес × confidence)
HOLD + confidence >= MEDIUM → BLOCK
HOLD + confidence == LOW → ABSTAIN (не участвует)

# Расчёт
approval_ratio = allow_score / (allow_score + block_score)
trade_allowed = approval_ratio >= threshold AND active_weight >= 0.50
```

#### Изменённые файлы
- `app/agents/config.py` — новые константы
- `app/models/confidence.py` — `confidence_to_float()`
- `app/agents/llm/aggregator.py` — полная переработка логики
- `app/agents/runner.py` — `is_candidate_valid()`, убран hard gate entry_triggered
- `app/agents/parallel_runner.py` — передача entry_triggered и candidate_valid
- `tests/test_quorum_voting.py` — 23 новых теста

### 🔌 IB Gateway Connection Fix

#### Проблема
После деплоя Quorum Voting бот не подключался к IB Gateway:
```
API connection failed: TimeoutError()
Warning: IB Gateway connection failed, falling back to mock data
```

#### Причины
1. **Неправильный `.env` на VPS** — содержал только 4 строки вместо полного конфига
2. **Неверный SUPABASE_URL** — `traddingbot.supabase.co` вместо `kimuxfiaoyyswdwkubve.supabase.co`
3. **Неверный OpenAI API Key** — устаревший ключ
4. **Неправильный порт IB Gateway** — использовался 4002 вместо 4004

#### Решение

**1. Правильный `.env` для VPS:**
```bash
# Supabase
SUPABASE_URL=https://kimuxfiaoyyswdwkubve.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...

# Bot
BOT_OWNER_USER_ID=fb7e03c2-aef5-4215-acd0-47902df9c721
BOT_MODE=paper

# IB Gateway Connection (КРИТИЧНО!)
IB_GATEWAY_HOST=ib-gateway      # Имя контейнера в Docker network
IB_GATEWAY_PORT=4004            # Gateway Paper Trading порт
IB_CLIENT_ID=10

IBKR_HOST=ib-gateway
IBKR_PORT=4004
IBKR_CLIENT_ID=20
IBKR_ENABLED=1

# OpenAI
OPENAI_API_KEY=sk-proj-...

# Phase 6
SIGNAL_GEN_ENABLED=1
SIGNAL_GEN_MOCK=0
SIGNAL_GEN_INTERVAL=60
```

**2. Архитектура портов IB Gateway:**

| Порт | Назначение |
|------|------------|
| 4001 | TWS Live |
| 4002 | TWS Paper / Gateway внутренний |
| 4003 | Gateway Live |
| **4004** | **Gateway Paper (используем)** |

В Docker образе `ghcr.io/gnzsnz/ib-gateway`:
- IB Gateway слушает на 4002 внутри
- socat проксирует 4004 → 4002
- **Подключаться нужно на 4004!**

**3. Docker Compose Network:**
```yaml
services:
  trading-bot:
    networks:
      - default
      - ib-gateway_default  # Подключение к сети IB Gateway

networks:
  ib-gateway_default:
    external: true
```

#### Проверка подключения
```bash
# Проверить socket
docker exec ibkr-trading-bot python -c "
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
result = sock.connect_ex(('ib-gateway', 4004))
print('OPEN' if result == 0 else 'CLOSED')
"

# Проверить ib_insync
docker exec ibkr-trading-bot python -c "
from ib_insync import IB
ib = IB()
ib.connect('ib-gateway', 4004, clientId=10, timeout=30)
print('Connected:', ib.isConnected())
ib.disconnect()
"
```

#### Успешное подключение в логах
```
Supabase ping: True
Phase 5: Parallel agents ENABLED mode=LLM strategy=hybrid client=OpenAI
Phase 1: Data sources ENABLED mode=REAL
Phase 6: Signal generation ENABLED mode=IBKR symbols=7 interval=60s
```

В IB Gateway GUI:
- ✅ Interactive Brokers API Server: connected
- ✅ Market Data Farm: ON (cashfarm, usfarm)
- ✅ Historical Data Farm: ON (cashhmds, ushmds)
- ✅ API Client: 1 connected (Client 10)

### ⚠️ Важные уроки

1. **`.env` не синхронизируется через git** — нужно вручную обновлять на VPS
2. **Имя переменной важно** — код ожидает `SUPABASE_SERVICE_ROLE_KEY`, не `SUPABASE_KEY`
3. **docker-compose restart не перечитывает `.env`** — нужен `docker-compose down && up`
4. **Порт 4004, не 4002** — из-за socat proxy в Docker образе

### 📊 Тесты

| Файл | Тестов | Статус |
|------|--------|--------|
| test_quorum_voting.py | 23 | ✅ |
| test_llm_agents.py | 30 | ✅ |
| **Всего** | **85** | ✅ |

---

## [2025-12-20] — Production Deployment to Hetzner VPS

### 🚀 VPS Deployment

**Сервер:** `65.108.83.67` (Ubuntu 24.04, CX23, Helsinki)

#### Docker Setup
- Установлен Docker 28.2.2 + docker-compose
- Создан `Dockerfile` (Python 3.11-slim)
- Создан `docker-compose.yml` с healthcheck и logging
- Контейнер: `ibkr-trading-bot`

#### Dockerfile
```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y gcc
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "app.main"]
```

#### docker-compose.yml
- Healthcheck: `pgrep -f python`
- Restart: `unless-stopped`
- Logging: 10MB max, 3 files
- Env file: `.env`

#### Environment Variables
```
SUPABASE_URL=https://kimuxfiaoyyswdwkubve.supabase.co
SUPABASE_SERVICE_ROLE_KEY=***
BOT_OWNER_USER_ID=fb7e03c2-aef5-4215-acd0-47902df9c721
EXECUTION_MODE=DRY_RUN
BOT_MODE=paper
TRADING_ENABLED=false
CONTROL_PLANE_IDLE_BACKOFF_ENABLED=1
```

### 🔧 Bug Fixes

#### requirements.txt
- Удалена жёсткая версия `postgrest==0.13.0` (конфликт с supabase 2.6.0)
- Supabase сам подтягивает `postgrest>=0.14`

### 📊 Bot Status
```
✅ Supabase ping: True
✅ ExecutionService initialized mode=disabled
✅ bot_settings found_row=True symbols_count=1
✅ control_plane_backfill: FULLY_DRAINED
✅ Idle backoff: 10s → 60s
```

### 🛠️ Полезные команды

```bash
# SSH
ssh root@65.108.83.67

# Логи
docker logs -f ibkr-trading-bot

# Перезапуск
cd /root/ibkr-trading-bot
docker-compose restart

# Полная пересборка
docker-compose down
docker-compose up -d --build

# Статус
docker ps
```

---

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
