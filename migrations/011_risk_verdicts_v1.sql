-- Risk verdicts linked to control decisions (append-only, idempotent)
do $$
begin
  if to_regclass('public.risk_verdicts') is null then
    execute $create$
      create table risk_verdicts (
        id uuid primary key default gen_random_uuid(),
        risk_version int not null default 1,
        ts_utc timestamptz not null,
        symbol text not null,
        decision_id uuid not null references control_decisions(id),
        signal_preview_id uuid not null references signal_previews(id),
        trade_allowed boolean not null,
        risk_modifier numeric not null,
        flags jsonb not null default '[]'::jsonb,
        commentary text null,
        created_at timestamptz not null default now(),
        unique (decision_id, risk_version)
      );
    $create$;
  else
    -- Adjust FK to control_decisions if table already existed
    begin
      alter table risk_verdicts drop constraint if exists risk_verdicts_decision_id_fkey;
    exception when undefined_object then
      null;
    end;
    alter table risk_verdicts
      add constraint risk_verdicts_decision_id_fkey foreign key (decision_id) references control_decisions(id);
  end if;
end $$;

-- Append-only trigger
do $$
begin
  if to_regclass('public.risk_verdicts') is not null then
    execute 'drop trigger if exists trg_risk_verdicts_deny_ud on public.risk_verdicts';
    execute 'create trigger trg_risk_verdicts_deny_ud before update or delete on public.risk_verdicts for each row execute function public.deny_update_delete()';
  end if;
end $$;

-- RLS
alter table risk_verdicts enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'risk_verdicts' and policyname = 'risk_verdicts_service_role_select'
  ) then
    execute 'create policy risk_verdicts_service_role_select on risk_verdicts for select using (auth.role() = ''service_role'')';
  end if;
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'risk_verdicts' and policyname = 'risk_verdicts_service_role_insert'
  ) then
    execute 'create policy risk_verdicts_service_role_insert on risk_verdicts for insert with check (auth.role() = ''service_role'')';
  end if;
end $$;

-- Helpful indexes
create index if not exists idx_risk_verdicts_decision_id on risk_verdicts(decision_id);
create index if not exists idx_risk_verdicts_signal_preview_id on risk_verdicts(signal_preview_id);
