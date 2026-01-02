# Spec v1 — Agent FX Bot (IBKR + Supabase)

Дата: 2025-12-16  
Статус: **заморожено для реализации v1 (MVP → paper)**

---

## 1) Цель и область (Scope)

**Цель:** построить производственную FX‑систему для IBKR (через `ib_insync`) с предсказуемой, детерминированной логикой и строгим риск‑контуром.

**Архитектура (фиксировано):**  
`market data → features → signals → (agents) → decision → risk → execution(OMS) → reconciliation → storage → observability/UI`

**Non-goals v1 (не делаем):**
- Scale-in / add-to-position (доливка) — **запрещено** (см. §6.2).
- Мульти‑стратегии/портфели — нет (один owner → один профиль).
- Кроссы без USD, экзотика, крипто, CFD и т.п. — вне v1.
- “Оптимизация стратегии” — вне v1 (сначала безопасность/контур/исполнение).

**Addendum (CFD-only режим):** для брокерского контура используется только CFD FX, ключ инструмента строго `secType:conId`, сопоставление с БД — через `meta.instrument_key`. Любой `secType != CFD` или отсутствие `conId` переводит систему в safe-mode без модификаций БД.

---

## 2) Инварианты (не нарушать)

### 2.1 LLM / GPT-агенты
- **LLM не считает числа**: не работает с ценами, SL/TP, лотами, pip‑значениями, маржой и т.п.
- LLM возвращает только:
  - `trade_allowed: bool`
  - `risk_modifier: float` (диапазон 0.5..1.0)
  - `flags: list[str]`
  - `comment: str`

### 2.2 Контракты данных
- Внутри пайплайна (signals/risk/decision/execution/recon/pm) — **только Pydantic‑модели**.
- “Сырые dict/JSON” допускаются **только на границах**:
  - storage/network/broker adapters (сериализация/десериализация).

### 2.3 Fail-safe
- Любая ошибка в агенте/данных/сверке/исполнении → **trade_allowed = false** (запрет новых входов).
- Если SL rejected/unknown → **позиция немедленно закрывается** (strict workflow, §9.3).

### 2.4 Append-only + Idempotency
- Append‑only логи: snapshots/signals/agent_reports/decisions/execution_reports/orders/fills/risk_events — **не изменяются**.
- Idempotency: **каждое торговое решение имеет `decision_id`**, повторное исполнение одного `decision_id` запрещено.

---

## 3) Инструменты (торгуемые пары)

**FX pairs (v1, фиксировано):**
- `EURUSD` — базовый высоколиквидный инструмент.
- `GBPUSD` — выше волатильность, чувствительность к UK/US новостям.
- `USDJPY` — иные pip/тик‑конвенции, чувствителен к ставкам и risk‑режимам.
- `USDCHF` — CHF как safe-haven, возможны резкие движения в risk-off.
- `AUDUSD` — commodity/risk-on профиль.
- `USDCAD` — часто корреляция с нефтью/commodity‑фактором.
- `NZDUSD` — risk-on, ниже ликвидность, чем AUDUSD.

**Дополнения (не v1):** кроссы без USD, экзотика, расширение списка — только после paper‑валидации.

---

## 4) Таймфреймы и торговая частота

**TF (фиксировано):** `M15 / H1 / H4`

**Лимиты частоты (фиксировано):**
- `max_trades_per_day_portfolio = 2`
- `max_trades_per_day_per_symbol = 1`

**Определение “входа” (для лимитов):**
- `Entry = переход по символу из flat → non-flat` (новая позиция).
- Scale-in отсутствует (см. §6.2), поэтому “доливки” не считаются (их просто нет).

---

## 5) Риск‑профиль v1 (процентный; сумма аккаунта не константа)

Все лимиты задаются **в процентах от текущей equity** (Net Liquidation / Account Equity из IBKR, база для вычислений).

**Risk profile v1 (фиксировано):**
- `risk_per_trade = 0.5%`
- `max_open_positions = 3`
- `max_usd_side_positions = 2` (USD-long или USD-short одновременно не более 2)
- `daily_loss_limit = 1.5%`
- `loss_streak_breaker = 3` → пауза **24h** (no new entries)
- `max_effective_leverage = 2.0×`
- `max_margin_utilization = 35%`

**Дополнительное правило (обязательное для реализации):**
- `worst_case_risk_open ≤ daily_loss_limit` (сумма рисков по всем открытым позициям не превышает 1.5% equity).

---

## 6) Позиции и Position Management (PM)

### 6.1 Зачем нужен PM
Между `risk/decision` и `execution/recon` должен быть явный слой сопровождения позиции, чтобы логика не “расползалась”.

### 6.2 Запрет scale-in (фиксировано)
**Scale-in полностью запрещён в v1.**  
Если по символу уже есть позиция (long/short), то любые новые entry‑intents по этому символу:
- **блокируются** (`trade_allowed=false` на символ),
- логируются как `risk_event(SCALE_IN_BLOCKED)`.

### 6.3 PM: ответственность (v1)
PM **не считает числа**, он формирует только **intents/actions**:
- реакция на partial fills (например: `REQUEST_REPLACE_PROTECTION`)
- реакция на `SL_REJECTED` (freeze + close)
- реакция на recon mismatch (minor/critical)
- сигнал “нужно ли модифицировать защиту” (как intent, без уровней)

---

## 7) Данные, warm-up и Data QA

### 7.1 Market Data
- Источник: IBKR через `ib_insync`.
- Сбор: исторические бары + регулярное обновление.
- Нормализация времени: **UTC**.

### 7.2 Warm-up (фиксировано)
Торговля запрещена, пока не накоплено минимум `N` баров **по всем символам и всем TF**.  
`N` — параметр в `bot_settings` (например 300, уточняется при реализации).

### 7.3 Data QA (обязательное)
- проверки дырок/дубликатов баров
- журнал проблем данных → `risk_events(DATA_GAP | DATA_DUP | DATA_STALE)`

### 7.4 Backpressure (фиксировано)
Если данные приходят быстрее обработки:
- политика: **drop old / keep last** на `(symbol, timeframe)` для market data событий.

---

## 8) Features layer (детерминированно)

Вычисления строго детерминированно, без LLM:

- ATR(14) — H1/H4
- RSI(14) — H1/H4
- MA(50), MA(200) — H1/H4
- spread (bid/ask) как фильтр

Выход: `FeatureSnapshot` на каждый символ/TF (Pydantic).

---

## 9) Signals / Decision / Risk / Execution (OMS)

### 9.1 Signals v1 (общая рамка)
Сигнал строится в логике:
- **H4**: фильтр направления/режима
- **H1**: сетап
- **M15**: триггер входа

Выход Signal Builder (Pydantic):
- `raw_signal: long | short | flat`
- `entry_triggered: bool`
- `sl_distance_pips` / `tp_distance_pips` — **детерминированно** (не LLM)
- `signal_confidence: 0..1`

### 9.2 Risk engine v1 (детерминированно)
- position sizing от риска и SL
- контроль effective leverage
- контроль margin utilization (по данным IBKR)
- USD-side exposure:
  - классификация каждой позиции как USD-long / USD-short
  - ограничение `max_usd_side_positions = 2`
- лимиты:
  - `max_open_positions`
  - `max_trades_per_day_portfolio`
  - `max_trades_per_day_per_symbol`
  - `daily_loss_limit`
  - `loss_streak_breaker`
  - `worst_case_risk_open`

### 9.3 Execution safety (OMS) — обязательный workflow
**Idempotency:** все намерения → `decision_id` (UUID), повтор запрещён.

**Bracket workflow “защищённая позиция”:**
1) выставить entry order
2) дождаться fill (включая partial fills)
3) выставить SL
4) если SL rejected/unknown → **немедленно close/reduce**
5) выставить TP после SL
6) TP rejected → warning (позиция остаётся под SL)

Обработка:
- partial fills
- rejected
- cancelled
- rate limiting: очередь торговых действий, ограничение X ордеров/мин (параметр)

---

## 10) Reconciliation и recovery (обязательный контур)

**Source of truth:** IBKR positions/fills/orders.

**Периодичность сверки:** каждые `recon_interval_sec` (параметр в `bot_settings`, v1: 60–300 сек).

**Реакции:**
- позиция в IBKR, которой “нет в боте” → alert + **safe mode** (no new entries)
- ордер без привязки к decision_id → cancel/alert по политике
- критический сценарий “filled, SL rejected/unknown” → fail-safe (close)

**Recovery после рестарта:**
- загрузить состояние из БД
- свериться с брокером
- продолжить сопровождение или перейти в safe mode

---

## 11) Agents (опционально в MVP, интерфейс обязателен)

`AgentClient.analyze(snapshot)`:
- вход: агрегированный snapshot по 7 парам (один запрос)
- выход: `trade_allowed`, `risk_modifier`, `flags[]`, `comment`
- таймаут 2–5 сек
- fallback по типам:
  - news-risk timeout → запрет новых входов
  - anomaly timeout → “no anomaly”
  - regime timeout → conservative mode

---

## 12) Observability / Health

`/health` JSON:
- `broker_connection`
- `data_feed`
- `last_bar_time` per TF
- `last_decision_at`
- `positions_synced`

Метрики:
- latency data→decision→execution
- reject rate
- slippage (как факт исполнения)

Логи: JSON structured logs, correlation id = `decision_id`.

---

## 13) Хранилище, доступ и управление

**Storage:** Supabase Postgres (production).  
UI: Next.js (Vercel) — **read-only логи** и управление через `bot_settings`, без доступа к runtime.

**RLS/security (фиксировано):**
- сервисный контур пишет через `service_role`
- UI доступ по admin‑политикам (`user_profiles.is_admin` + `is_admin()`)

**Append-only enforcement (фиксировано):**
- для лог‑таблиц включить DB triggers “deny update/delete”
- исключения: `positions`, `bot_settings`

---

## 14) Критерии готовности MVP (paper)

В paper режиме:
- сбор баров M15/H1/H4 по 7 парам стабильный
- есть feature snapshots
- есть signals (пусть простые)
- есть decisions/risk gating
- execution ставит ордера и соблюдает workflow
- **0 позиций без SL**
- reconciliation работает и логирует
- лимиты риска реально блокируют входы (max positions, USD-side, daily loss, loss streak, trades/day)
