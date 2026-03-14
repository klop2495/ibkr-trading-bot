# CURRENT_STATE — Текущее состояние системы

> Обновляется после каждого деплоя / значимого изменения.
> AI-разработчик: прочитай ПЕРЕД началом работы.

**Последнее обновление:** 13 March 2026

---

## Контур 1: Forecasts (бинарные опционы)

### Статус: 🟢 РАБОТАЕТ

### Активные стратегии:

| Стратегия | Статус | Текущая оценка | Поток |
|---|---|---|---|
| Alt3 (legacy) | ⚫ deprecated | отключен в runtime (оставлен только в исторических данных) | none |
| Alt3 (main=v2) | 🟢 active | основная рабочая версия Alt3 | low/medium |
| Alt4 | 🟢 active | live в работе | variable |
| Alt6 | 🟢 active (candidate adopted) | strategy = `strict_s5_v2_extension_veto` | research->runtime |

### Alt6 ENV (production)

```bash
ALT6_MIN_REPEAT_MIN=45
```

### Alt6 critical notes

1. `Alt6` использует отдельные поля `h30_alt6_*`; старый `Alt5` остается только как historical legacy.
2. Направление строится из `strict_s5_v2_extension_veto` поверх recent `S5` snapshots.
3. Для Python dashboard endpoint использовать:
   - `/api/forecasts-history`
   - поле ответа: `rows` (не `forecasts`)

### Lifecycle

- Alt3v2/Alt4/Alt6 отслеживаются раздельно (`SYMBOL#variant`).
- Базовое правило дедупликации/ожидания: pending + repeat guard (`SIGNAL_MIN_REPEAT_MIN=45`).
- Strategy repeat guard: `ALT3_MIN_REPEAT_MIN=45`, `ALT4_MIN_REPEAT_MIN=45`, `ALT6_MIN_REPEAT_MIN=45`.
- `ALT4` now supports an intrinsic ADX floor via `ALT4_MIN_ADX` (recommended current test value: `20`).
- Cooldown escalation: `30m -> 60m -> session`.
- Для блокировки вне торговых окон используется `SIGNAL_BLACKLIST_SOURCE=recommended_complement`
  (часы берутся как дополнение к `FORECAST_RECOMMENDED_WINDOWS_UTC`).
- В history API флаг recommended считается по историческим условиям окна, а не по текущему lifecycle status.
- Для H30 верификации доступен `FORECAST_VERIFY_H30_STRICT_EXPIRY=1`:
  цена берется как первый `market_snapshots.M15` snapshot на/после `expiry`,
  а не ближайший бар вокруг `expiry`.
- Для более точной H30 экспирации доступен `S5` recorder:
  `FORECAST_VERIFY_S5_ENABLED=1`, `FORECAST_VERIFY_S5_FLUSH_INTERVAL=5`,
  verifier сначала ищет `market_snapshots.timeframe='S5'`, затем fallback на `M15`.

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

### Локальные репозитории синхронизированы с origin/main (проверка 11 March 2026)

- `ibkr-trading-bot`: `153d5b1` (HEAD == origin/main)
- `ibkr-trading-fronend`: `5e24529` (HEAD == origin/main)

---

## Pending Tasks

| Task | Priority | Status | Description |
|---|---|---|---|
| TASK-C | P1 | 📋 open | Execution layer hardening / consistency |
| TASK-D | P1 | ✅ deployed | Inverted Rules + GPT cleanup |
| Alt6 stabilization | P1 | ⏳ in progress | накопить больше live-выборку (500+ verified) |
| Forecast docs sync | P2 | ⏳ in progress | поддерживать AGENTS/CURRENT_STATE/CHANGELOG |

---

## Последние известные коммиты (локально)

- `ibkr-trading-bot`: `bb01ef7`, `153d5b1`
- `ibkr-trading-frontend`: `1cfc28c`, `5e24529`

*Обновляй этот файл после каждого деплоя.*
