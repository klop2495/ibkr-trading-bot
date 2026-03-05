# TASK-D Implementation Report (2026-03-05)

## Документ
- Основание: `TZ_TaskD_Inverted_Rules_Strategy.md`
- Дата фиксации: 2026-03-05
- Контур: execution/control plane (без изменений forecast-layer логики генерации)

## Цель апдейта
- Перевести принятие решений на инвертированную rules-логику.
- Убрать зависимость исполнения от LLM/GPT контуров.
- Добавить контроль качества исполнения через outcome verifier + accuracy guard.
- Зафиксировать операционные блокеры и диагностику на VPS.

## Что реализовано

### 1) Strategy/agents слой
- Добавлен и используется флаг `RULES_INVERT_SIGNAL`.
- Rules-сигнал формируется в инвертированной логике (в контуре Task D).
- В раннере зафиксированы маркеры:
  - `RULES_INVERT_SIGNAL`
  - `entry_triggered` (как обязательное условие actionability)
  - `llm_contour_enabled` (контур LLM отключаемый флагом).

### 2) Execution accuracy слой
- Добавлен `app/execution/outcome_verifier.py`.
- В `main.py` интегрированы:
  - периодическая верификация исходов (`execution_outcome_verify ...`)
  - rolling accuracy guard (`execution_guard blocked=...`).
- Параметры через env:
  - `EXECUTION_ACCURACY_GUARD_ENABLED`
  - `EXECUTION_MIN_ACCURACY`
  - `EXECUTION_MIN_SAMPLES`
  - `EXECUTION_ACCURACY_WINDOW_H`
  - `EXECUTION_VERIFY_INTERVAL_S`
  - `EXECUTION_VERIFY_HORIZON_MIN`
  - `EXECUTION_VERIFY_BATCH`.

### 3) Execution mode
- Зафиксирован режим исполнения:
  - `EXECUTION_STRATEGY=rules`
  - `ACTIVE_STRATEGY=rules`
- На VPS подтверждены runtime значения:
  - `EXECUTION_ENABLED=1`
  - `EXECUTION_DRY_RUN=0`
  - `BOT_MODE=paper`.

### 4) UI/операционный слой
- В ходе Task D и последующей стабилизации:
  - удалены/скрыты GPT-зависимые элементы в части control-plane UI (по проверкам на VPS),
  - выполнена диагностика расхождения статуса торговли UI vs DB.
- Важно: индикатор на `/admin/broker` берет `connection.tradingEnabled` из `/api/broker`, а блок `TRADING_DISABLED` формируется по данным decision/risk-контура с учётом `bot_settings` и времени записи.

## Что проверено на VPS

### A) Источник блокировки сделок
- Подтверждено: `parallel_decisions` содержит `executed_signal=LONG/SHORT`.
- При этом сделки не открывались из-за двух последовательных блокеров:
  1. `execution_guard blocked=1` (низкая rolling accuracy),
  2. `EXECUTION_BLOCKED` в `risk_events` с причиной:
     - `adaptive_gate_blocked:<symbol> rolling_acc=<...>%` (Forecast Gate).

### B) Проверка `TRADING_DISABLED`
- Исторические `risk_verdicts` действительно содержали `TRADING_DISABLED`.
- После обновления `bot_settings.trading_enabled=true` новые строки с `TRADING_DISABLED` не появлялись.
- Проблема далее была не в `trading_enabled`, а в execution блокерах выше.

### C) Outcome verifier
- Верификация и guard логируются в runtime.
- Для старых данных выявлялся noise в снапшотах, добавлен operational lookback/guard подход.

## Временные операционные изменения (диагностика)
- Для проверки прохождения сделки временно ставились:
  - `EXECUTION_ACCURACY_GUARD_ENABLED=0`
  - `FORECAST_GATE_ENABLED=0`
- Это диагностический режим для подтверждения pipeline, не целевой production baseline.

## Сопутствующая документация
- `docs/CHANGELOG_DEV.md` — запись о диагностике 2026-03-05.
- `docs/EXECUTION_BLOCKERS_2026-03-05.md` — runbook по блокерам и rollback.

## Итог
- Апдейт Task D реализован в execution/control контуре и задокументирован.
- Ключевой эффект: решения формируются rules-first в инвертированном режиме, LLM-контур отключаем.
- Текущее поведение исполнения определяется не только `trade_allowed`, но и post-gates:
  - execution accuracy guard,
  - forecast gate (adaptive rolling accuracy).
- Для production возврата к строгому режиму требуется включить обратно guard/gate и откалибровать пороги.
