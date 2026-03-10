# CURRENT_STATE — Текущее состояние системы

> Обновляется после каждого деплоя / значимого изменения.
> AI-разработчик: прочитай ПЕРЕД началом работы.

**Последнее обновление:** 10 March 2026

---

## Контур 1: Forecasts (бинарные опционы)

### Статус: 🟢 РАБОТАЕТ

### Активные стратегии:

| Стратегия | Статус | Текущая оценка | Поток |
|---|---|---|---|
| Alt2 | 🟢 active | меняется по рыночным часам | variable |
| Alt3 | 🟢 active | source для Alt5 | variable |
| Alt4 | 🟢 active | live в работе | variable |
| Alt5 | 🟢 active (deployed) | 120h: 65.71% (23/35), 7d: 71.43% (30/42) | low/medium |

### Alt5 ENV (production)

```bash
ALT5_ENABLED=1
ALT5_SOURCE_STRATEGY=alt3
ALT5_INVERT=1
ALT5_HOURS_UTC=3,9,11,19
ALT5_ALLOWED_HOURS=3,9,11,19
ALT5_MIN_ADX=20
ALT5_MIN_REPEAT_MIN=120
ALT5_EXCLUDE_CONFIDENCE=
```

### Alt5 critical notes

1. Исторические строки Alt5, созданные до фикса, могли иметь `h30_alt5_trade_eligible=false`.
2. Для консистентного historical view выполнен backfill eligibility за 7 дней.
3. После backfill для строк Alt5 в часах стратегии корректно выставляются:
   - `recommended_alt5=true`
   - `window_status="recommended"` (включая 19h)
4. Для Python dashboard endpoint использовать:
   - `/api/forecasts-history`
   - поле ответа: `rows` (не `forecasts`)

### Lifecycle

- Alt2/Alt3v2/Alt4/Alt5 отслеживаются раздельно (`SYMBOL#variant`).
- Alt5 cooldown активен через `ALT5_MIN_REPEAT_MIN`.
- В history API флаг recommended считается по историческим условиям окна, а не по текущему lifecycle status.

---

## Контур 2: Execution (автоматический forex)

### Статус: 🟡 ЧАСТИЧНО РАБОТАЕТ

- TASK-D (Inverted Rules) deployed.
- `EXECUTION_ENABLED=1`, `EXECUTION_DRY_RUN=0`, `BOT_MODE=paper`.
- Execution-часть остается чувствительной к guard/funds/reconciliation условиям.

---

## Инфраструктура

### Статус контейнеров (последняя проверка)

- `ibkr-dashboard`: healthy
- `ibkr-trading-bot`: healthy
- `api/health`: `{"status":"ok","supabase":true}`

### Репозитории на сервере синхронизированы с origin/main

- `/root/ibkr-trading-bot`: `d1d0b2f` (HEAD == origin/main)
- `/root/ibkr-trading-fronend`: `ab5ca18` (HEAD == origin/main)

---

## Pending Tasks

| Task | Priority | Status | Description |
|---|---|---|---|
| TASK-C | P1 | 📋 open | Execution layer hardening / consistency |
| TASK-D | P1 | ✅ deployed | Inverted Rules + GPT cleanup |
| Alt5 stabilization | P1 | ⏳ in progress | накопить больше live-выборку (500+ verified) |
| Forecast docs sync | P2 | ⏳ in progress | поддерживать AGENTS/CURRENT_STATE/CHANGELOG |

---

## Последние деплой-коммиты

- `ibkr-trading-bot`: `2584d48`, `d1d0b2f`
- `ibkr-trading-frontend`: `ab5ca18`

*Обновляй этот файл после каждого деплоя.*
