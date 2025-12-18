-- Control-plane decisions (append-only, idempotent)
create table if not exists control_decisions (
  id uuid primary key default gen_random_uuid(),
  decision_version int not null default 1,
  ts_utc timestamptz not null,
  symbol text not null,
  signal_preview_id uuid not null references signal_previews(id),
  trade_allowed boolean not null,
  risk_modifier numeric not null,
  flags jsonb not null default '[]'::jsonb,
  commentary text null,
  engine_version int not null default 1,
  agents_version int not null default 1,
  created_at timestamptz not null default now(),
  unique (signal_preview_id, decision_version)
);

-- Append-only trigger
do $$
begin
  if to_regclass('public.control_decisions') is not null then
    execute 'drop trigger if exists trg_control_decisions_deny_ud on public.control_decisions';
    execute 'create trigger trg_control_decisions_deny_ud before update or delete on public.control_decisions for each row execute function public.deny_update_delete()';
  end if;
end $$;

-- RLS
alter table control_decisions enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'control_decisions' and policyname = 'control_decisions_service_role_select'
  ) then
    execute 'create policy control_decisions_service_role_select on control_decisions for select using (auth.role() = ''service_role'')';
  end if;
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'control_decisions' and policyname = 'control_decisions_service_role_insert'
  ) then
    execute 'create policy control_decisions_service_role_insert on control_decisions for insert with check (auth.role() = ''service_role'')';
  end if;
end $$;

-- Helpful indexes
create index if not exists idx_control_decisions_signal_preview_id on control_decisions(signal_preview_id);
