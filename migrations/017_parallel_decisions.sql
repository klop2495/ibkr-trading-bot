-- Phase 0: Parallel decisions shadow table
-- Purpose: Log all strategy decisions (rules, gpt, hybrid) for comparison
-- GPT starts as HOLD stubs, replaced incrementally

create table if not exists parallel_decisions (
    id uuid primary key default gen_random_uuid(),
    ts_utc timestamptz not null,
    symbol text not null,
    
    -- Rules Engine (existing logic)
    rules_signal text,              -- LONG, SHORT, HOLD
    rules_confidence text,          -- low, normal, high (from Confidence enum)
    rules_flags text[] default '{}',
    
    -- GPT Agents (stubs initially, real later)
    gpt_signal text default 'HOLD',
    gpt_score float default 0.0,
    gpt_consensus boolean default false,
    gpt_consensus_count int default 0,
    gpt_agent_details jsonb default '[]'::jsonb,
    
    -- Hybrid (blend of rules + gpt)
    hybrid_signal text,
    hybrid_score float default 0.0,
    
    -- Safety metrics (Phase 2+)
    budget_status text default 'OK',
    cache_hits int default 0,
    source_health jsonb default '{}'::jsonb,
    validation_failures int default 0,
    
    -- Execution tracking
    executed_strategy text,         -- rules, gpt, hybrid, none
    executed_signal text,
    
    -- Outcome (filled after trade closes)
    outcome_pips float,
    outcome_result text,            -- win, loss, breakeven
    
    -- Links
    signal_preview_id uuid references signal_previews(id),
    control_decision_id uuid references control_decisions(id),
    
    created_at timestamptz not null default now()
);

-- Indexes
create index if not exists idx_parallel_decisions_ts on parallel_decisions(ts_utc);
create index if not exists idx_parallel_decisions_symbol on parallel_decisions(symbol);
create index if not exists idx_parallel_decisions_strategy on parallel_decisions(executed_strategy);
create index if not exists idx_parallel_decisions_signal_preview on parallel_decisions(signal_preview_id);

-- Append-only trigger (consistent with other control tables)
do $$
begin
  if to_regclass('public.parallel_decisions') is not null then
    execute 'drop trigger if exists trg_parallel_decisions_deny_ud on public.parallel_decisions';
    execute 'create trigger trg_parallel_decisions_deny_ud before update or delete on public.parallel_decisions for each row execute function public.deny_update_delete()';
  end if;
end $$;

-- RLS
alter table parallel_decisions enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'parallel_decisions' and policyname = 'parallel_decisions_service_role_select'
  ) then
    execute 'create policy parallel_decisions_service_role_select on parallel_decisions for select using (auth.role() = ''service_role'')';
  end if;
  if not exists (
    select 1 from pg_policies where schemaname = 'public' and tablename = 'parallel_decisions' and policyname = 'parallel_decisions_service_role_insert'
  ) then
    execute 'create policy parallel_decisions_service_role_insert on parallel_decisions for insert with check (auth.role() = ''service_role'')';
  end if;
end $$;
