-- Control-plane order intents (append-only, idempotent)
create table if not exists control_order_intents (
  id uuid primary key default gen_random_uuid(),
  intent_version int not null default 1,
  ts_utc timestamptz not null,
  symbol text not null,
  execution_report_id uuid not null references control_execution_reports(id),
  decision_id uuid not null references control_decisions(id),
  signal_preview_id uuid not null references signal_previews(id),
  intent_type text not null,
  side text not null,
  status text not null default 'CREATED',
  flags jsonb not null default '[]'::jsonb,
  details jsonb null,
  created_at timestamptz not null default now(),
  unique (execution_report_id, intent_version)
);

-- Append-only trigger
do $$
begin
  if to_regclass('public.control_order_intents') is not null then
    execute 'drop trigger if exists trg_control_order_intents_deny_ud on public.control_order_intents';
    execute 'create trigger trg_control_order_intents_deny_ud before update or delete on public.control_order_intents for each row execute function public.deny_update_delete()';
  end if;
end $$;

-- RLS
alter table control_order_intents enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'control_order_intents' and policyname = 'control_order_intents_service_role_select'
  ) then
    execute 'create policy control_order_intents_service_role_select on control_order_intents for select using (auth.role() = ''service_role'')';
  end if;
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'control_order_intents' and policyname = 'control_order_intents_service_role_insert'
  ) then
    execute 'create policy control_order_intents_service_role_insert on control_order_intents for insert with check (auth.role() = ''service_role'')';
  end if;
end $$;

-- Helpful indexes
create index if not exists idx_control_order_intents_execution_report_id on control_order_intents(execution_report_id);
create index if not exists idx_control_order_intents_decision_id on control_order_intents(decision_id);
create index if not exists idx_control_order_intents_signal_preview_id on control_order_intents(signal_preview_id);
