-- Signal previews append-only table
create table if not exists signal_previews (
  id uuid primary key default gen_random_uuid(),
  ts_utc timestamptz not null,
  symbol text not null,
  timeframe_trigger text not null,
  setup_type text not null,
  direction text not null,
  setup_present boolean not null,
  entry_triggered boolean not null,
  confidence text not null,
  rr numeric not null,
  data_quality text not null,
  spread_quality text not null,
  flags jsonb not null default '[]'::jsonb,
  sl_distance_pips numeric null,
  tp_distance_pips numeric null,
  engine_version integer not null default 1,
  created_at timestamptz not null default now(),
  unique (ts_utc, symbol, timeframe_trigger, engine_version)
);

-- Append-only trigger
do $$
declare
  t text := 'signal_previews';
begin
  if to_regclass('public.' || t) is not null then
    execute format('drop trigger if exists trg_%I_deny_ud on public.%I', t, t);
    execute format('create trigger trg_%I_deny_ud before update or delete on public.%I for each row execute function public.deny_update_delete()', t, t);
  end if;
end $$;

-- RLS
alter table signal_previews enable row level security;

-- Policies: service role can insert/select
do $$
begin
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'signal_previews' and policyname = 'signal_previews_service_role_select'
  ) then
    execute 'create policy signal_previews_service_role_select on signal_previews for select using (auth.role() = ''service_role'')';
  end if;

  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'signal_previews' and policyname = 'signal_previews_service_role_insert'
  ) then
    execute 'create policy signal_previews_service_role_insert on signal_previews for insert with check (auth.role() = ''service_role'')';
  end if;
end $$;
