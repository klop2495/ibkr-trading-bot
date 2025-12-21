# IBKR Trading Bot — Полная история проекта

> **Проект:** Автоматизированная торговая система для Interactive Brokers
> **Стек:** Python (Backend), Next.js (Frontend), Supabase (DB), Docker
> **Сервер:** Hetzner VPS 65.108.83.67 (Ubuntu 24.04)
> **Последнее обновление:** 2025-12-21

---

# Содержание

1. [Архитектура системы](#архитектура-системы)
2. [Хронология разработки](#хронология-разработки)
   - [Фаза 1: Агенты и схемы](#фаза-1-агенты-и-схемы-2025-12-16)
   - [Фаза 2: IBKR Connect](#фаза-2-ibkr-connect-2025-12-17--18)
   - [Фаза 3: Market Data](#фаза-3-market-data-2025-12-18)
   - [Фаза 4: Signals Engine](#фаза-4-signals-engine-2025-12-18)
   - [Фаза 5: Production Deployment](#фаза-5-production-deployment-2025-12-20)
   - [Фаза 6: UI & Agents Dashboard](#фаза-6-ui--agents-dashboard-2025-12-21)
   - [Фаза 7: Auth & Pages](#фаза-7-auth--new-pages-2025-12-21)
   - [Фаза 8: Trading Settings](#фаза-8-trading-settings-2025-12-21)
3. [Справочник команд](#справочник-команд)
4. [Конфигурация](#конфигурация)

---

# Архитектура системы

```
┌─────────────────────────────────────────────────────────────────┐
│                    VPS Hetzner 65.108.83.67                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │  ibkr-frontend  │  │ ibkr-trading-bot│  │   IB Gateway    │ │
│  │   (PM2/Next)    │  │    (Docker)     │  │    (Docker)     │ │
│  │   Port 3000     │  │   Python 3.11   │  │   Port 4001     │ │
│  └────────┬────────┘  └────────┬────────┘  └───────┬─────────┘ │
│           │                    │                    │           │
│           └────────────────────┼────────────────────┘           │
│                                │                                │
└────────────────────────────────┼────────────────────────────────┘
                                 │
            ┌────────────────────┴────────────────────┐
            ▼                                         ▼
     ┌──────────────┐                         ┌──────────────┐
     │   Supabase   │                         │     IBKR     │
     │   (Cloud)    │                         │   (Paper)    │
     └──────────────┘                         └──────────────┘
```

## Компоненты

| Компонент | Технология | Назначение |
|-----------|------------|------------|
| Backend | Python 3.11, Docker | Торговая логика, агенты, OMS |
| Frontend | Next.js 16, PM2 | UI дашборд, настройки |
| Database | Supabase (PostgreSQL) | Хранение настроек, сигналов, решений |
| Broker | IB Gateway (Docker) | Подключение к Interactive Brokers |

## Ключевые пути на VPS

| Путь | Описание |
|------|----------|
| `/root/ibkr-trading-frontend` | Frontend (Next.js) |
| `/root/ibkr-trading-bot` | Backend (Python/Docker) |
| `/root/ibkr-trading-bot/deploy/ib-gateway` | IB Gateway Docker |

---

# Хронология разработки

---

## Фаза 1: Агенты и схемы (2025-12-16)

### Цель
Создание базовой архитектуры агентной системы с OpenAI интеграцией.

### Выполненные задачи

#### 1.1 Agent Schemas
- Добавлены Pydantic схемы `AgentRequest`, `AgentResponse`, `AgentDecision`
- Strict validation с `extra="forbid"`
- Unit tests для bounds и serialization

**Файлы:**
- `app/agents/__init__.py`
- `app/agents/schemas.py`
- `tests/test_agents_schemas.py`

#### 1.2 OpenAI Client
- Stdlib-based клиент для OpenAI Responses API
- Structured outputs validation
- Tests для success/error/timeout flows

**Файлы:**
- `app/agents/errors.py`
- `app/agents/openai_client.py`
- `tests/test_agents_openai_client.py`

#### 1.3 Orchestrator + Circuit Breaker
- Детерминистическая агрегация решений агентов
- Fallback policy при ошибках
- Circuit breaker state machine
- Cooldown после N ошибок

**Файлы:**
- `app/agents/orchestrator.py`
- `app/agents/state.py`
- `tests/test_agents_orchestrator.py`

#### 1.4 Agents Gate
- Entry point для decision layer
- Env-driven конфигурация (`AGENTS_ENABLED`)
- Fail-safe defaults

**Файлы:**
- `app/agents/config.py`
- `app/agents/gate.py`
- `tests/test_agents_gate.py`

#### 1.5 Decision Pre-check
- Валидация перед вызовом агентов
- Per-symbol input validation
- Persist hook для agent reports

**Файлы:**
- `app/decision/agents_precheck.py`
- `tests/test_decision_agents_precheck.py`

#### 1.6 Agent Reports Repository
- Insert-only репозиторий для отчётов агентов
- Risk events (timeouts, CB on/off, trade blocks)

**Файлы:**
- `app/storage/agent_reports_repo.py`
- `tests/test_agent_reports_repo_contract.py`

---

## Фаза 2: IBKR Connect (2025-12-17 — 18)

### Цель
Интеграция с Interactive Brokers через ib_insync.

### Выполненные задачи

#### 2.1 IBKR Pydantic Contracts
- Модели для account summary, positions
- Type-safe wrappers

**Файлы:**
- `app/models/ibkr.py`

#### 2.2 IBKR Client Wrapper
- Connect/disconnect lifecycle
- Read-only account summary
- Positions snapshots

**Файлы:**
- `app/broker/ibkr_client.py`
- `tests/test_ibkr_client_contract.py`

#### 2.3 Bot Settings Alignment
- Модель `BotSettings` aligned с DB constraints
- Safe defaults
- UUID parsing в main loop

**Файлы:**
- `app/models/bot_settings.py`
- `app/storage/bot_settings_repo.py`
- `tests/test_bot_settings.py`
- `tests/test_bot_settings_repo.py`

---

## Фаза 3: Market Data (2025-12-18)

### Цель
Получение и обработка рыночных данных.

### Выполненные задачи

#### 3.1 Market Data Indicators
- Technical indicators calculation
- QA checks для data quality
- Warm-up readiness logic

#### 3.2 IBKR Fetcher
- Historical bars fetching
- Real-time data buffers

#### 3.3 Service Orchestration
- `MarketDataService` с env-gated wiring
- BotSettings symbols/warmup integration
- Fail-safe если disabled/misconfigured

**Файлы:**
- `app/market_data/*`
- `migrations/004_bot_settings_warmup.sql`
- `tests/test_market_data_*`

---

## Фаза 4: Signals Engine (2025-12-18)

### Цель
Детерминистическая генерация торговых сигналов.

### Выполненные задачи

#### 4.1 Signals Params
- JSONB column в `bot_settings`
- Strict Pydantic contracts
- Configuration gating

**Файлы:**
- `migrations/005_bot_settings_signals_params.sql`
- `app/models/signals_params.py`

#### 4.2 Signals Engine
- Deterministic signal generation
- Safe defaults
- `SIGNALS_RULES_NOT_SPECIFIED` flag когда config missing

**Файлы:**
- `app/signals/models.py`
- `app/signals/engine.py`
- `tests/test_signals_engine.py`
- `tests/test_signals_rules_warning.py`

---

## Фаза 5: Production Deployment (2025-12-20)

### Цель
Деплой системы на production сервер.

### Выполненные задачи

#### 5.1 Hetzner VPS Setup
- **Сервер:** `65.108.83.67` (Ubuntu 24.04, CX23, Helsinki)
- Docker 28.2.2 + docker-compose
- Node.js 20.19.6
- PM2 для process management
- Nginx как reverse proxy

#### 5.2 Backend Docker
- `Dockerfile` (Python 3.11-slim)
- `docker-compose.yml`
- Режим: `EXECUTION_MODE=DRY_RUN`

#### 5.3 Frontend Deployment
- PM2 для Next.js
- Nginx (порт 80 → 3000)
- Автозапуск при перезагрузке

#### 5.4 Supabase Auth Configuration
- Site URL: `http://65.108.83.67`
- Redirect URLs настроены
- Auth callback fix для proxy (X-Forwarded-Host)

#### 5.5 UI Redesign
- Тёмная тема (dark mode)
- Sidebar навигация
- Статус бота (Online/DRY_RUN)

**Новые файлы:**
```
app/components/Sidebar.tsx
app/globals.css (полный редизайн)
Dockerfile
docker-compose.yml
```

#### 5.6 Frontend Stabilization
- Vitest тестовый фреймворк
- 49 tests (API contracts + UI smoke)
- GitHub Actions CI/CD

---

## Фаза 6: UI & Agents Dashboard (2025-12-21)

### Цель
Создание страницы мониторинга агентов и расширение торговых пар.

### Выполненные задачи

#### 6.1 Страница агентов `/admin/agents`

**Новые файлы:**
```
app/admin/agents/page.tsx
app/admin/agents/AgentsDashboard.tsx
app/api/admin/agents/route.ts
app/api/admin/symbols/route.ts
```

**Функционал:**
- Summary карточки (торговые пары, сигналы 24ч, активные)
- Статус агентов (RegimeAgent, QualityAgent) с approval rate
- Карточки символов с силой сигнала (0-100%)
- Модальное окно для настройки торговых пар
- Авто-обновление каждые 30 секунд

**Алгоритм силы сигнала:**
```javascript
strength = 0
strength += min(rr * 20, 40)      // R:R до 40 очков
if confidence == "high": +20
elif confidence == "medium": +10
if direction != "flat": +15
if data_quality == "ok": +10
if spread_quality == "ok": +10
if !all_agents_approved: cap at 30
strength = clamp(0, 100)
```

#### 6.2 Расширение торговых пар

**Файл:** `app/agents/schemas.py`

```python
ALLOWED_SYMBOLS = [
    # Major pairs (7)
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    # JPY crosses (6)
    "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY", "NZDJPY",
    # Other crosses (3)
    "EURGBP", "EURAUD", "EURCHF",
]
```

**Итого:** 16 пар (было 7, добавлено 9)

#### 6.3 Git коммиты

**Backend:**
```
0781f7f feat: expand allowed symbols to 16 pairs
```

**Frontend:**
```
daca830 feat: agents dashboard with signal monitoring
30bfea9 feat: agents dashboard with signal strength monitoring
```

---

## Фаза 7: Auth & New Pages (2025-12-21)

### Цель
Безопасная авторизация и новые страницы мониторинга.

### Выполненные задачи

#### 7.1 Authentication System Overhaul

**Login Page** (`app/login/page.tsx`):
- Email + password authentication (multi-device support)
- Registration functionality removed for security
- Auto-redirect to home after successful login
- Removed magic link flow

**Middleware Protection** (`middleware.ts`):
- Server-side auth middleware using `@supabase/ssr`
- Redirects unauthenticated users to `/login`
- Redirects authenticated users away from `/login` to home
- Protects all routes except auth callbacks and API routes

**Layout Changes:**
- `AppLayout.tsx` component для conditional sidebar rendering
- Login page shows no sidebar (full-screen form)

#### 7.2 Trades Page `/admin/trades`

**Файл:** `app/admin/trades/page.tsx`

**Функционал:**
- Displays `parallel_decisions` table from Supabase
- Stats cards: Total decisions, LONG/SHORT/HOLD counts, consensus rate
- Filters: Symbol dropdown (all 16 pairs), time period (24h/3d/7d/30d)
- Clickable rows open modal with full decision details
- Agent breakdown: TechnicalAgent, MacroAgent, SentimentAgent, CorrelationAgent, RiskAgent
- Signal colors: LONG (green), SHORT (red), HOLD (gray)

#### 7.3 Agents Page `/admin/agents`

**Файл:** `app/admin/agents/page.tsx`

**Функционал:**
- Active strategy display: Rules/GPT/Hybrid
- Hybrid weights visualization: 60% Rules + 40% GPT
- Agent weights breakdown:
  - TechnicalAgent: 25%
  - MacroAgent: 20%
  - SentimentAgent: 15%
  - CorrelationAgent: 15%
  - RiskAgent: 25%
- Stats from database (7 days)

#### 7.4 Sidebar Updates

- User email display in Account section
- Logout button (calls `supabase.auth.signOut()`)
- Updated navigation structure
- Status indicator: "HYBRID Mode"

#### 7.5 PM2 Directory Fix

**Проблема:** PM2 запускался из `/root/ibkr-trading-fronend` (опечатка)

**Решение:**
```bash
pm2 delete ibkr-frontend
cd /root/ibkr-trading-frontend
pm2 start npm --name "ibkr-frontend" -- start
pm2 save
```

#### 7.6 BOT_OWNER_USER_ID Update

**Проблема:** Backend API проверял старый user ID

**Решение:**
1. Создан новый user в Supabase: `8962f4ba-88a2-42c5-904b-ef1d10aae979`
2. Обновлён `.env` на backend и frontend
3. Обновлён `owner_user_id` в `bot_settings`

---

## Фаза 8: Trading Settings (2025-12-21)

### Цель
Настраиваемые параметры торговли: риск, плечо, trailing stop.

### Выполненные задачи

#### 8.1 Backend: BotSettings Model Update

**Файл:** `app/models/bot_settings.py`

**Новые поля:**
```python
# Stop Loss / Take Profit settings
default_sl_pips: float = Field(default=20.0, ge=5.0, le=200.0)
default_tp_pips: float = Field(default=40.0, ge=5.0, le=400.0)

# Trailing Stop settings
trailing_stop_enabled: bool = Field(default=False)
trailing_stop_distance_pips: float = Field(default=15.0, ge=5.0, le=100.0)
trailing_stop_activation_pips: float = Field(default=10.0, ge=0.0, le=100.0)
```

**Существующие поля (уже были):**
```python
risk_per_trade: float = Field(default=0.005, ge=0.0, le=0.05)  # 0.5%
max_effective_leverage: float = Field(default=2.0, ge=0.0, le=50.0)
```

#### 8.2 Backend: OMS Trailing Stop Support

**Файл:** `app/broker/oms.py`

**Новые типы ордеров:**
```python
class OrderType(str, Enum):
    MARKET = "MKT"
    LIMIT = "LMT"
    STOP = "STP"
    STOP_LIMIT = "STP_LMT"
    TRAIL = "TRAIL"              # NEW
    TRAIL_LIMIT = "TRAIL_LIMIT"  # NEW
```

**Новые поля в IBKROrderRequest:**
```python
trailing_stop_enabled: bool = False
trailing_stop_distance: Optional[float] = None
trailing_stop_distance_pips: Optional[float] = None
```

**Новый метод:**
```python
def place_trailing_stop_order(
    self,
    request: IBKROrderRequest,
    entry_price: float,
) -> IBKROrderState:
    """
    Place a trailing stop order after main order is filled.
    
    The trailing stop follows the price by a fixed distance.
    When price moves in favor, the stop moves up (for long) or down (for short).
    When price reverses, the stop stays in place and triggers when hit.
    """
```

**Обновлённый метод `place_order_with_sl_tp`:**
- Priority 1: If trailing_stop_enabled → Main order + Trailing Stop
- Priority 2: If both SL and TP prices → Bracket order
- Priority 3: Otherwise → Simple order

#### 8.3 Database Migration

```sql
ALTER TABLE bot_settings 
ADD COLUMN IF NOT EXISTS default_sl_pips float DEFAULT 20.0,
ADD COLUMN IF NOT EXISTS default_tp_pips float DEFAULT 40.0,
ADD COLUMN IF NOT EXISTS trailing_stop_enabled boolean DEFAULT false,
ADD COLUMN IF NOT EXISTS trailing_stop_distance_pips float DEFAULT 15.0,
ADD COLUMN IF NOT EXISTS trailing_stop_activation_pips float DEFAULT 10.0;
```

#### 8.4 Frontend: Settings Page `/admin/settings`

**Новые файлы:**
```
app/admin/settings/page.tsx
app/api/admin/settings/route.ts
```

**Секции настроек:**

**📊 Управление рисками:**
- Риск на сделку (0% - 5%)
- Максимальное плечо (1x - 50x)
- Макс. открытых позиций (1 - 20)
- Дневной лимит убытков (0% - 50%)

**🎯 Stop Loss / Take Profit:**
- Stop Loss по умолчанию (5 - 200 pips)
- Take Profit по умолчанию (5 - 400 pips)
- Risk:Reward ratio display

**📈 Trailing Stop:**
- Включение/выключение
- Дистанция trailing stop (5 - 100 pips)
- Активация после X pips прибыли (0 - 100 pips)

**Функционал:**
- Валидация ranges на backend
- Upsert для автосоздания записи
- Visual indicators для изменённых полей
- Reset to default button

#### 8.5 Sidebar Update

```typescript
{
  section: "Настройки",
  items: [
    { href: "/admin/settings", icon: "⚙️", label: "Торговля" },  // NEW
    { href: "/admin/signals-params", icon: "📶", label: "Сигналы" },
  ],
}
```

#### 8.6 owner_user_id Fix

**Проблема:** API возвращал 500 "The result contains 0 rows"

**Причина:** В `bot_settings` был старый `owner_user_id`

**Решение:**
```sql
UPDATE bot_settings 
SET owner_user_id = '8962f4ba-88a2-42c5-904b-ef1d10aae979'
WHERE owner_user_id = 'fb7e03c2-aef5-4215-acd0-47902df9c721';
```

#### 8.7 Git коммиты

**Backend:**
```
feat: Trailing stop support in OMS
- Add trailing stop fields to BotSettings model
- Add TRAIL order type to OMS
- Implement place_trailing_stop_order method
- Support trailing_stop_distance_pips in execution
```

**Frontend:**
```
feat: Trading settings page with trailing stop config
- Add /admin/settings page with risk/leverage/SL/TP controls
- Add trailing stop configuration
- API endpoint for settings management
- Update sidebar navigation

fix: Use upsert for settings API
```

---

# Справочник команд

## Деплой Frontend
```bash
ssh root@65.108.83.67
cd /root/ibkr-trading-frontend
git pull origin main
npm run build
pm2 restart ibkr-frontend
```

## Деплой Backend
```bash
ssh root@65.108.83.67
cd /root/ibkr-trading-bot
git pull origin feature/control-risk-exec-v1
docker-compose down && docker-compose up -d --build
```

## Логи
```bash
# Frontend
pm2 logs ibkr-frontend --lines 50

# Backend
docker logs --tail 50 ibkr-trading-bot

# IB Gateway
docker logs --tail 50 ib-gateway
```

## Статус
```bash
pm2 status
docker ps
```

## Тесты (локально)
```bash
# Backend
cd ~/ibkr-trading-bot
.venv/bin/python -m pytest -q

# Frontend
cd ~/ibkr-trading-fronend
npm run lint
npm test
npm run build
```

---

# Конфигурация

## Environment Variables

### Backend (`/root/ibkr-trading-bot/.env`)
```bash
SUPABASE_URL=https://kimuxfiaoyyswdwkubve.supabase.co
SUPABASE_SERVICE_ROLE_KEY=***
BOT_OWNER_USER_ID=8962f4ba-88a2-42c5-904b-ef1d10aae979
EXECUTION_MODE=DRY_RUN
EXECUTION_ENABLED=0
BOT_MODE=paper
TRADING_ENABLED=false
DEFAULT_EQUITY=10000.0
```

### Frontend (`/root/ibkr-trading-frontend/.env.local`)
```bash
NEXT_PUBLIC_SUPABASE_URL=https://kimuxfiaoyyswdwkubve.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=***
SUPABASE_SERVICE_ROLE_KEY=***
BOT_OWNER_USER_ID=8962f4ba-88a2-42c5-904b-ef1d10aae979
```

## Supabase Tables

| Таблица | Назначение |
|---------|------------|
| `bot_settings` | Настройки бота (риск, плечо, символы) |
| `signal_previews` | Превью сигналов |
| `parallel_decisions` | Решения агентов |
| `risk_verdicts` | Вердикты риск-менеджера |
| `agent_reports` | Отчёты агентов |

## URLs

| URL | Страница |
|-----|----------|
| http://65.108.83.67 | Dashboard |
| http://65.108.83.67/admin/trades | Trades |
| http://65.108.83.67/admin/agents | Agents |
| http://65.108.83.67/admin/control | Control Plane |
| http://65.108.83.67/admin/settings | Trading Settings |
| http://65.108.83.67/admin/signals-params | Signal Parameters |
| http://65.108.83.67/login | Login |

---

# Текущий статус системы

| Компонент | Статус | Детали |
|-----------|--------|--------|
| Backend | ✅ Online | Docker, DRY_RUN mode |
| Frontend | ✅ Online | PM2, порт 3000 |
| IB Gateway | ✅ Connected | Paper trading |
| Supabase | ✅ Connected | 16 символов |
| Auth | ✅ Working | Email + password |
| Settings | ✅ Working | Risk/Leverage/SL/TP/Trailing |

**Execution:** DISABLED (`EXECUTION_ENABLED=0`)

---

*Last updated: 2025-12-21T21:30:00Z*
