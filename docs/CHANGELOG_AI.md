# CHANGELOG_AI — Журнал изменений AI-разработчика

> Каждая запись = одна сессия / одно изменение.
> Формат: дата, задача, файлы, результат, следующий шаг.

---

## [2026-03-04] Session: Alt4 deployment + lifecycle dedup fix

- **Задача:** Deploy Alt4, fix lifecycle dedup bug
- **Файлы изменены:** app/storage/forecast_repo.py (added inserted_payloads), app/main.py (fallback to inserted_payloads)
- **Файлы НЕ тронуты:** engine.py, signal_lifecycle.py, verifier.py, indicators_vote.py
- **Что сделано:** Fixed insert_batch missing "data" key which broke lifecycle for ALL strategies. Alt4 deployed with cooldown 120min.
- **Результат:** verified — Alt4 tracked 15 symbols, 0 duplicates post-deploy
- **Deploy:** commit a22609e
- **Следующий шаг:** Monitor Alt4 accuracy, TASK-C execution layer fix

## [2026-03-04] Session: Execution layer diagnostics + TZ creation

- **Задача:** Full diagnostic of execution layer (Contour 2)
- **Файлы изменены:** нет (только диагностика)
- **Что сделано:**
  - Identified 2 zombie trades (USDCHF, NZDUSD) — OPEN status with closed_at
  - Identified CFD:XXXXX invalid instrument_key blocking sync_with_db()
  - Identified frontend reads from 'trades' table, backend writes to 'trades_history'
  - Full code review of BrokerStateService, TradesHistoryRepo, dashboard.py
  - Created TZ_TaskC_Execution_Layer_Fix (7 bugs, P0-P3)
  - Created TZ_TaskC_Addendum_v1.1 (root cause analysis)
- **Результат:** TZ documents created
- **Deploy:** нет
- **Следующий шаг:** TASK-C implementation by developer

## [2026-03-04] Session: Rules/GPT accuracy analysis + TASK-D

- **Задача:** Diagnose why Rules engine has 39.3% accuracy
- **Файлы изменены:** нет (только анализ)
- **Что сделано:**
  - Found setup_present used instead of entry_triggered (461/500 unconfirmed signals)
  - Found GPT agents 3/5 non-functional
  - Tested inverted signals: Rules triggered INVERTED = 69.0% H4, 63.6% H8
  - Created TZ_TaskD_Inverted_Rules_Strategy.md (5 phases)
- **Результат:** TZ created, analysis confirmed inversion works
- **Deploy:** нет
- **Следующий шаг:** TASK-D implementation

## [2026-03-05] Session: TASK-D implementation (by developer)

- **Задача:** TASK-D Phases D1-D5
- **Файлы изменены:** parallel_runner.py, outcome_verifier.py (new), main.py, .env, frontend pages
- **Что сделано:** See docs/TASKD_IMPLEMENTATION_REPORT_2026-03-05.md
- **Результат:** Deployed, outcome_verifier working after SQL trigger fix
- **Deploy:** on VPS
- **Следующий шаг:** Fix Supabase trigger for parallel_decisions, accumulate outcome data

## [2026-03-06] Session: Alt4 accuracy analysis + Alt5 strategy

- **Задача:** Analyze Alt4 live performance, investigate ANTI_ALT3
- **Файлы изменены:** нет (только анализ)
- **Что сделано:**
  - Alt4 live: 272 verified, eligible 61.8%, inverted mode 69.0%
  - Found hour 10 original toxic (20%), hour 20 inverted missing from env (78%)
  - Recommended ENV: remove hour 10, add hours 9,19,20
  - ANTI_ALT3 analysis: best combo 74.6% (good_hours + ADX≥20, n=59)
  - Created Strategy_Alt5_ANTI_ALT3.md
- **Результат:** Strategy document created
- **Deploy:** нет
- **Следующий шаг:** Implement Alt5, fix Alt4 ENV hours

## [2026-03-06] Session: AGENTS.md + operational docs

- **Задача:** Create AI developer operating rules
- **Файлы изменены:** AGENTS.md (new), docs/CHANGELOG_AI.md (new), docs/CURRENT_STATE.md (new)
- **Что сделано:** Comprehensive AI developer instructions
- **Результат:** created
- **Deploy:** нет
- **Следующий шаг:** Commit to repo

---

*Append new entries at the bottom.*

## [2026-03-09] Session: Alt5 rollout alignment (backend + frontend)

- **Задача:** Привести реализацию Alt5 к согласованной стратегии ANTI_ALT3 (hours+ADX+lifecycle+UI)
- **Файлы изменены (backend):**
  - app/forecast/engine.py
  - app/models/forecast.py
  - app/storage/forecast_repo.py
  - app/main.py
  - app/forecast/recommended_windows.py
  - app/dashboard.py
  - migrations/024_alt5_trade_eligible.sql
- **Файлы изменены (frontend):**
  - app/api/admin/binary-signals/route.ts
  - app/api/admin/forecasts/history/route.ts
  - lib/recommendedWindows.ts
- **Что сделано:**
  - Добавлены env-фильтры Alt5 (source/invert/hours/exclude-hours/min-ADX/exclude-confidence)
  - Добавлен `h30_alt5_trade_eligible` в модель и запись
  - Добавлен Alt5 lifecycle cooldown (`ALT5_MIN_REPEAT_MIN`), key `SYMBOL#alt5`
  - Исправлена классификация recommended для Alt5 в API history/binary-signals
  - Добавлена миграция `024_alt5_trade_eligible.sql`
- **Результат:** deployed + validated на VPS
- **Deploy commits:**
  - bot: `2584d48`
  - frontend: `ab5ca18`
- **Следующий шаг:** наблюдать live статистику Alt5 и расширять выборку verified

## [2026-03-10] Session: history API consistency fix + data backfill

- **Задача:** Исправить расхождения `window_status`/`recommended_alt5` в исторических строках
- **Файлы изменены:** app/dashboard.py
- **Что сделано:**
  - В `/api/forecasts-history` убрана зависимость historical recommended от текущего lifecycle status
  - Рекомендованность Alt5 считается по историческому timestamp и Alt5-окнам
  - На VPS выполнен backfill `h30_alt5_trade_eligible` за последние 7 дней
- **Результат:**
  - 19h Alt5 строки корректно стали `recommended_alt5=true`, `window_status="recommended"`
- **Deploy commit:** `d1d0b2f`
- **Следующий шаг:** накопление 500+ verified для статистической стабильности Alt5
