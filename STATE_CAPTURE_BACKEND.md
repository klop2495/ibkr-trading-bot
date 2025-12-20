# STATE_CAPTURE_BACKEND (Codex Task #7.6.0)

> Примечание: прямой Supabase доступ в этой среде недоступен (нет сети/ключей для запуска SQL). Ниже приведены команды/запросы, которые нужно выполнить в рабочей среде; результаты в этой сессии помечены как “не выполнено (нет доступа)”.

## A) Состояние таблиц / данные

- bot_settings (для BOT_OWNER_USER_ID):
  - SQL: `select owner_user_id, trading_enabled, symbols, signals_params is not null as has_params from bot_settings where owner_user_id = '<BOT_OWNER_USER_ID>';`
  - Результат: не выполнено (нет доступа).
  - Возможный симптом из логов: found_row=false ⇒ trading_disabled, symbols=[].

- Счётчики записей:
  - `select count(*) from signal_previews;` — не выполнено.
  - `select count(*) from control_decisions;` — не выполнено.
  - `select count(*) from risk_verdicts;` — не выполнено.
  - `select event_type, count(*) from risk_events group by event_type;` — не выполнено.

- Ограничения/FK:
  - `\d+ control_decisions;` ожидаемые: PK id (uuid), FK signal_preview_id → signal_previews(id), unique(signal_preview_id, decision_version).
  - `\d+ risk_verdicts;` ожидаемые: PK id (uuid), FK decision_id → control_decisions(id), FK signal_preview_id → signal_previews(id), unique(decision_id, risk_version).
  - RLS включена, политики service_role select/insert (см. миграции 010, 011).

- Исполнение/режим:
  - Основной цикл: `python -m app.main`; env обязательны: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, BOT_OWNER_USER_ID, BOT_SETTINGS_POLL_SECONDS (опц), IBKR_ENABLED/SEED_SNAPSHOTS/EXECUTION_ENABLED/RECONCILIATION_ENABLED.

## B) Причина found_row=false (кодовый анализ)
- BotSettingsRepo.get возвращает safe defaults при отсутствии строки и логирует risk_event (BOT_SETTINGS_NOT_FOUND). Если BOT_OWNER_USER_ID отсутствует или UUID в строковом виде не совпадает, found_row становится false ⇒ trading_disabled/symbols=[].
- Нет автосоздания строки bot_settings в текущем коде.

## C) Проверка контрактов (код)
- Решения/вердикты сейчас требуют decision.id (risk engine больше не генерирует UUID).
- Репозитории решений/вердиктов/превью генерируют UUID на клиенте перед insert; execution-подрепозитории ещё использовали insert().select() (исправлено в этой задаче).
- Telemetry: все control-plane risk_events должны использовать `data.signal_preview_id` (нормализовано).

## D) Команды для ручного запуска (в бою)
```
-- counts
select count(*) as previews from public.signal_previews;
select count(*) as decisions from public.control_decisions;
select count(*) as verdicts from public.risk_verdicts;
select event_type, count(*) from public.risk_events group by event_type order by 1;

-- последняя цепочка
select p.id as preview_id, d.id as decision_id, v.id as verdict_id
from public.signal_previews p
left join public.control_decisions d on d.signal_preview_id = p.id
left join public.risk_verdicts v on v.decision_id = d.id
order by p.ts_utc desc limit 10;
```

## E) smoke / repro
- Новый скрипт `scripts/smoke_control_plane_chain.py` (см. репо) выполняет:
  1) Получает последний preview.
  2) Вставляет decision (trade_allowed=false, flags=["SMOKE_CHAIN"]).
  3) Запускает risk_engine.evaluate и вставляет verdict.
  4) Печатает IDs; логирует risk_events на ошибках.

## F) Диагностика lint/compile
- `.venv/bin/python -m compileall app` — выполнено, успешно.
- `.venv/bin/python -m pytest -q` — выполнено, 96 passed (только внешние pydantic warnings).

## G) Риски, влияющие на decisions/verdicts=0 (кодовый анализ)
1) RiskEngineV1 ранее генерировал UUID при пустом decision.id → verdict FK не совпадал, записи могли падать/ломать FK. Исправлено: теперь требует decision.id (ValueError).
2) Execution repos использовали insert().select(), несовместимо с текущим supabase-py (бросает AttributeError) → могло блокировать цепочку после вердикта. Исправлено паттерном insert().execute() с client-side UUID.
3) Telemetry ключи mismatch (preview_id vs signal_preview_id) затрудняли диагностику, теперь нормализовано.

## H) Что ещё проверить с доступом к БД
- Запрос на risk_events с event_type in ('CONTROL_DECISION_PERSIST','RISK_VERDICT_PERSIST') чтобы увидеть реальные ошибки persist.
- Проверить триггер deny_update_delete на control_decisions/risk_verdicts: `select tgname from pg_trigger where tgrelid='control_decisions'::regclass;`
- Убедиться, что policies для service_role существуют: `select policyname from pg_policies where tablename in ('control_decisions','risk_verdicts');`
