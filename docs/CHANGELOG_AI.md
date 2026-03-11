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

## [2026-03-10] Session: Project audit + P0/P1 fixes

- **Задача:** Full project audit, buglist, critical fixes
- **Файлы изменены:** app/main.py (Supabase .not_() syntax fix), app/models/forecast.py (unused imports), app/models/parallel_decision.py (unused import), app/models/signal_preview.py (unused import), app/notifications/telegram.py (unused vars, f-string)
- **Файлы НЕ тронуты:** forecast engine, indicators, lifecycle, verifier, market_data
- **Что сделано:**
  - P0-1: Fixed _refresh_strategy_guards crash (.not_() → .not_.is_()) — бот перезапускался каждые ~3 мин
  - P0-2: Closed USDCHF zombie trade (SQL manual cleanup)
  - P0-4: Cleaned risk_events 571K → 28K (drop trigger, delete, restore trigger)
  - P1-1: Updated Alt4 ENV: hours 9,19 orig / 15,20,21,22 inv / +GBPUSD exclude
  - P1-3: Cleaned 2145 mock outcomes from parallel_decisions (pre Feb 25)
  - 11 ruff lint errors fixed
  - Created Project_Audit_Plan.md (7 blocks) and Project_Audit_Buglist.md (19 issues)
- **Результат:** All P0 issues resolved. P1-1, P1-3 done. Bot running stable.
- **Deploy:** commit fix(P0-1) + commit fix(lint) + VPS rebuild
- **Следующий шаг:** P1-5 (unverified forecasts), P2 items (dead code, split main.py)

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

## [2026-03-11] Session: Documentation sync + local test-runner recovery

- **Задача:** Привести документацию к фактическому состоянию и восстановить локальный запуск тестов
- **Файлы изменены:** README.md, requirements.txt, docs/CURRENT_STATE.md, app/execution/oms_stub.py, app/execution/engine_stub.py, app/reconciliation/engine_stub.py, app/signals/engine.py
- **Файлы НЕ тронуты:** forecast engine, market_data, execution бизнес-логика
- **Что сделано:**
  - Синхронизированы pinned-версии `supabase/httpx/postgrest` в README с `requirements.txt`
  - Обновлены команды Quick Start на `python3` для совместимости локальной среды
  - Добавлен `pytest` в `requirements.txt` для воспроизводимого запуска тестов
  - Обновлён `docs/CURRENT_STATE.md` по актуальным локальным commit SHA и дате проверки
- **Результат:** docs aligned; добавлены backward-compatible shim imports для legacy тестов
- **Deploy:** не деплоилось
- **Следующий шаг:** при необходимости выполнить полный прогон pytest в изолированном `.venv` окружении

## [2026-03-11] Session: Targeted behavior fixes for remaining pytest failures

- **Задача:** Закрыть оставшиеся behavioral-фейлы без изменения тестов
- **Файлы изменены:** app/broker/state_service.py, app/agents/parallel_runner.py
- **Файлы НЕ тронуты:** forecast engine, market_data, execution core
- **Что сделано:**
  - Hardened `BrokerStateService` against non-deterministic repo return types (MagicMock/truthy objects):
    - safe parse for healed rows
    - strict orphan existence checks
    - stricter recent orphan row shape validation
  - Скорректирован `hybrid_signal` в `ParallelDecisionRunner` для shadow path (`llm disabled` + `active_strategy=rules`) через явный сигнал из preview
- **Результат:** `python3 -m pytest -q` → `459 passed, 7 skipped`
- **Deploy:** не деплоилось
- **Следующий шаг:** при необходимости закоммитить изменения отдельным commit
