Экспорт ENV (пример, подставьте свои значения):

```
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="your-service-role-key"
export BOT_OWNER_USER_ID="00000000-0000-0000-0000-000000000000"
```

Запуск state capture:

```
python scripts/state_capture_control_plane.py
```

SQL проверки (в Supabase SQL Editor или psql):

```
select count(*) from public.signal_previews;
select count(*) from public.control_decisions;
select p.id as preview_id, d.id as decision_id, v.id as verdict_id from public.signal_previews p join public.control_decisions d on d.signal_preview_id=p.id join public.risk_verdicts v on v.decision_id=d.id order by p.ts_utc desc limit 10;
```
