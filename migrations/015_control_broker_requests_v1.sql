-- Control-plane broker requests (prepared/disabled, append-only, idempotent)
create table if not exists control_broker_requests (
  id uuid primary key default gen_random_uuid(),
  request_version int not null default 1,
  ts_utc timestamptz not null,
  order_intent_id uuid not null references control_order_intents(id),
  decision_id uuid not null references control_decisions(id),
  signal_preview_id uuid not null references signal_previews(id),
  status text not null,
  flags jsonb not null default '[]'::jsonb,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (order_intent_id, request_version)
);

-- Append-only trigger
do $$
begin
  if to_regclass('public.control_broker_requests') is not null then
    execute 'drop trigger if exists trg_control_broker_requests_deny_ud on public.control_broker_requests';
    execute 'create trigger trg_control_broker_requests_deny_ud before update or delete on public.control_broker_requests for each row execute function public.deny_update_delete()';
  end if;
end $$;

-- RLS
alter table control_broker_requests enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'control_broker_requests' and policyname = 'control_broker_requests_service_role_select'
  ) then
    execute 'create policy control_broker_requests_service_role_select on control_broker_requests for select using (auth.role() = ''service_role'')';
  end if;
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'control_broker_requests' and policyname = 'control_broker_requests_service_role_insert'
  ) then
    execute 'create policy control_broker_requests_service_role_insert on control_broker_requests for insert with check (auth.role() = ''service_role'')';
  end if;
end $$;

-- Helpful indexes
create index if not exists idx_control_broker_requests_intent_id on control_broker_requests(order_intent_id);
create index if not exists idx_control_broker_requests_decision_id on control_broker_requests(decision_id);
create index if not exists idx_control_broker_requests_preview_id on control_broker_requests(signal_preview_id);
