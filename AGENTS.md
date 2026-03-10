# AGENTS.md — Операционные правила AI-разработчика

> **Этот файл ОБЯЗАТЕЛЕН к прочтению** перед началом любой работы в репозитории.
> AI-разработчик (Claude, Codex, Cursor, любой LLM) должен прочитать этот файл ПЕРВЫМ.

---

## 1. Обязательная процедура входа в контекст

При начале **каждой сессии** (нового чата / нового контекстного окна):

```
ШАГ 1: Прочитать AGENTS.md (этот файл)
ШАГ 2: Прочитать docs/AI_RULES.md (жёсткие ограничения)
ШАГ 3: Прочитать docs/CHANGELOG_AI.md (последние 30 строк — что уже сделано)
ШАГ 4: Прочитать docs/CURRENT_STATE.md (текущее состояние системы)
ШАГ 5: Если есть активное ТЗ — прочитать его перед началом работы
```

**Не начинать работу пока не выполнены шаги 1-4.**

---

## 2. Архитектура: два контура (НЕ ПУТАТЬ)

### Контур 1: Forecasts (бинарные опционы)
- **Цель:** Направленные прогнозы на H30 для РУЧНОЙ торговли опционами
- **Стратегии:** Alt2, Alt3, Alt4, Alt5
- **Output:** Telegram сигнал + dashboard
- **Бот НЕ торгует.** Человек принимает решение сам.
- **Файлы:** `app/forecast/engine.py`, `indicators_vote.py`, `signal_lifecycle.py`, `verifier.py`, `gate.py`

### Контур 2: Execution (автоматический forex)
- **Цель:** Автоматическая торговля CFD через IB Gateway
- **Pipeline:** signal_preview → decision → risk → execution → reconciliation
- **Стратегия:** Inverted Rules (TASK-D), SWING_CONTINUATION/REVERSAL
- **Файлы:** `app/signals/engine_v1.py`, `app/agents/parallel_runner.py`, `app/execution/`, `app/broker/`

> ⚠️ Эти контуры АРХИТЕКТУРНО РАЗДЕЛЕНЫ. Изменения в одном НЕ должны затрагивать другой.

---

## 3. Защищённые файлы (НЕ ТРОГАТЬ без явного разрешения)

### Контур 1 — полностью защищён:
- `app/forecast/engine.py` — генерация Alt2/Alt3/Alt4/Alt5
- `app/forecast/indicators_vote.py` — индикаторы
- `app/forecast/signal_lifecycle.py` — dedup и cooldown
- `app/forecast/verifier.py` — верификация прогнозов
- `app/forecast/gate.py` — adaptive gate
- `app/notifications/telegram.py` — TG alerts

### Market Data — защищён:
- `app/market_data/` — весь каталог
- Таблица `market_snapshots`

### Broker core — защищён:
- `app/broker/keys.py` — instrument_key
- `app/broker/contracts.py` — CFD contract factory

### Принцип: broker = source of truth
- IB Gateway = источник правды по позициям
- Ключ инструмента: `CFD:<conId>`
- `conId` отсутствует/0 → safe-mode

---

## 4. Самопроверка (ОБЯЗАТЕЛЬНО перед каждым ответом)

### 4.1 Перед предложением изменений:

```
□ Прочитал текущий код файла который собираюсь менять
□ Проверил логи/данные на VPS перед диагнозом
□ Не делаю предположений — проверяю факты командами
□ Изменения минимальны — не рефакторю "ради красоты"
□ Не трогаю защищённые файлы
□ Не путаю Контур 1 и Контур 2
```

### 4.2 После каждого изменения:

```
□ Компиляция/lint проверены
□ Тесты запущены (если есть)
□ Записал изменение в docs/CHANGELOG_AI.md
□ Обновил docs/CURRENT_STATE.md если изменилось состояние
□ Дал команду деплоя (если нужен деплой)
□ Проверил результат на VPS после деплоя
```

### 4.3 Acceptance checklist (из AI_RULES.md):

```
□ Не добавлял вычисления чисел в LLM
□ Внутри доменной логики — только Pydantic
□ Не менял БД/RLS/таблицы без разрешения
□ Не нарушил fail-safe
□ Сохранил idempotency по decision_id
□ Не "починил" зависимости без запроса
```

---

## 5. Журналирование изменений

### docs/CHANGELOG_AI.md
После **каждого** изменения добавлять запись:

```markdown
## [YYYY-MM-DD] Session: <краткое описание>
- **Задача:** TASK-X Phase Y
- **Файлы изменены:** file1.py, file2.tsx
- **Файлы НЕ тронуты:** (подтвердить что forecasts layer не тронут)
- **Что сделано:** краткое описание
- **Результат:** verified/pending/failed
- **Deploy:** commit hash / не деплоилось
- **Следующий шаг:** что нужно сделать дальше
```

### docs/CURRENT_STATE.md
Содержит актуальное состояние системы. Обновлять при:
- Изменении ENV настроек
- Деплое нового кода
- Обнаружении багов
- Изменении accuracy/результатов

---

## 6. Работа с задачами (ТЗ)

### Формат ТЗ:
Все ТЗ хранятся как `.md` файлы. Текущие:
- `TZ_TaskC_Execution_Layer_Fix.md` — ревизия execution layer
- `TZ_TaskD_Inverted_Rules_Strategy.md` — инвертированные Rules
- `Strategy_Alt5_ANTI_ALT3.md` — стратегия Alt5

### Перед началом работы по ТЗ:
1. Прочитать ТЗ полностью
2. Написать **отчёт о понимании** (что буду делать, какие файлы, что НЕ трогаю)
3. **Ждать подтверждения** от владельца
4. Только после подтверждения — писать код

### После завершения ТЗ:
1. Записать в CHANGELOG_AI.md
2. Обновить CURRENT_STATE.md
3. Написать отчёт о выполнении

---

## 7. Инфраструктура

### VPS (production)
- **Host:** 65.108.83.67 (Hetzner)
- **Backend:** Docker container `ibkr-trading-bot`
- **Dashboard:** Docker container `ibkr-dashboard` (port 8080)
- **Frontend:** PM2 + Next.js (port 3000)
- **IB Gateway:** Container `ib-gateway` (port 4004 внутри docker network, 4001-4002 наружу)

### Локальная разработка (MCP)
- **Backend:** `/Users/oleggnikishin/AI PROJECTS/Forex trading bot/ibkr-trading-bot/`
- **Frontend:** `/Users/oleggnikishin/AI PROJECTS/Forex trading bot/ibkr-trading-fronend/` (опечатка в имени — так и есть)

### Деплой
```
Локально: edit via MCP → git commit → git push
VPS: ssh → cd /root/ibkr-trading-bot → git pull → docker compose build --no-cache → docker compose up -d
Frontend: cd /root/ibkr-trading-fronend → git pull → pm2 restart all
```

### База данных
- **Supabase** (PostgreSQL, cloud)
- Миграции через Supabase SQL Editor
- **Append-only** для логов (triggers)
- **Mutable:** trades_history, bot_settings, positions

### Ключевые данные
- `market_snapshots` до Feb 2026 — **mock/seed data** (close=1.00465). НЕ использовать для анализа.
- Реальные данные начинаются с **25 Feb 2026**.

---

## 8. Принципы общения с владельцем

1. **Не уходить от задачи.** Если спросили про execution — не предлагать менять forecasts.
2. **Не делать предположений.** Проверять факты командами на VPS.
3. **Краткие диагностические команды.** Владелец предпочитает конкретику.
4. **Корректно различать контуры.** Владелец строго следит за разделением.
5. **Отчёт о понимании ПЕРЕД кодом.** Не начинать реализацию без подтверждения.
6. **Признавать ошибки.** Если пошёл не туда — сказать прямо и вернуться к задаче.

---

## 9. Ссылки на документацию

| Документ | Что содержит |
|---|---|
| `docs/AI_RULES.md` | Жёсткие ограничения (LLM, Pydantic, fail-safe) |
| `docs/spec_v1.md` | Функциональное ТЗ v1 |
| `docs/broker_cfd.md` | CFD-only режим, instrument_key |
| `docs/FORECAST_SYSTEM.md` | Документация Контура 1 |
| `docs/PHASE6_TWO_CONTOUR.md` | Two-Contour Architecture |
| `docs/INTEGRATION_SCHEMA_V2.md` | Полная схема интеграции |
| `docs/signals_rules_v1.md` | Rules engine spec |
| `docs/recon-workflow.md` | Reconciliation |
| `docs/event-priorities.md` | Event sequencing |
| `docs/MIGRATION_2026-02-26.md` | Context transfer |
| `docs/TASKD_IMPLEMENTATION_REPORT_2026-03-05.md` | TASK-D отчёт |
| `docs/CHANGELOG_AI.md` | Журнал AI изменений |
| `docs/CURRENT_STATE.md` | Текущее состояние системы |

---

*Последнее обновление: 10 March 2026*
