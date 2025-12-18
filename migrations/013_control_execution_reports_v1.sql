-- Control-plane execution reports (append-only, idempotent)
create table if not exists control_execution_reports (
  id uuid primary key default gen_random_uuid(),
  execution_version int not null default 1,
  ts_utc timestamptz not null,
  decision_id uuid not null references control_decisions(id),
  signal_preview_id uuid not null references signal_previews(id),
  status text not null,
  reason_flags jsonb not null default '[]'::jsonb,
  details jsonb null,
  created_at timestamptz not null default now(),
  unique (decision_id, execution_version)
);

-- Append-only trigger
do $$
begin
  if to_regclass('public.control_execution_reports') is not null then
    execute 'drop trigger if exists trg_control_execution_reports_deny_ud on public.control_execution_reports';
    execute 'create trigger trg_control_execution_reports_deny_ud before update or delete on public.control_execution_reports for each row execute function public.deny_update_delete()';
  end if;
end $$;

-- RLS
alter table control_execution_reports enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'control_execution_reports' and policyname = 'control_execution_reports_service_role_select'
  ) then
    execute 'create policy control_execution_reports_service_role_select on control_execution_reports for select using (auth.role() = ''service_role'')';
  end if;
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'control_execution_reports' and policyname = 'control_execution_reports_service_role_insert'
  ) then
    execute 'create policy control_execution_reports_service_role_insert on control_execution_reports for insert with check (auth.role() = ''service_role'')';
  end if;
end $$;

-- Helpful indexes
create index if not exists idx_control_execution_reports_decision_id on control_execution_reports(decision_id);
create index if not exists idx_control_execution_reports_signal_preview_id on control_execution_reports(signal_preview_id);
