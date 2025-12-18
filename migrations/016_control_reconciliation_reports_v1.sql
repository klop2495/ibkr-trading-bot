-- Control-plane reconciliation reports (append-only, idempotent)
create table if not exists control_reconciliation_reports (
  id uuid primary key default gen_random_uuid(),
  broker_request_id uuid not null references control_broker_requests(id),
  order_intent_id uuid not null references control_order_intents(id),
  execution_report_id uuid not null references control_execution_reports(id),
  decision_id uuid not null references control_decisions(id),
  signal_preview_id uuid not null references signal_previews(id),
  ts_utc timestamptz not null,
  recon_version int not null default 1,
  status text not null,
  flags jsonb not null default '[]'::jsonb,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (broker_request_id, recon_version)
);

-- Append-only trigger
do $$
begin
  if to_regclass('public.control_reconciliation_reports') is not null then
    execute 'drop trigger if exists trg_control_reconciliation_reports_deny_ud on public.control_reconciliation_reports';
    execute 'create trigger trg_control_reconciliation_reports_deny_ud before update or delete on public.control_reconciliation_reports for each row execute function public.deny_update_delete()';
  end if;
end $$;

-- RLS
alter table control_reconciliation_reports enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'control_reconciliation_reports' and policyname = 'control_reconciliation_reports_service_role_select'
  ) then
    execute 'create policy control_reconciliation_reports_service_role_select on control_reconciliation_reports for select using (auth.role() = ''service_role'')';
  end if;
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'control_reconciliation_reports' and policyname = 'control_reconciliation_reports_service_role_insert'
  ) then
    execute 'create policy control_reconciliation_reports_service_role_insert on control_reconciliation_reports for insert with check (auth.role() = ''service_role'')';
  end if;
end $$;

-- Helpful indexes
create index if not exists idx_control_recon_req_id on control_reconciliation_reports(broker_request_id);
create index if not exists idx_control_recon_decision_id on control_reconciliation_reports(decision_id);
create index if not exists idx_control_recon_preview_id on control_reconciliation_reports(signal_preview_id);
