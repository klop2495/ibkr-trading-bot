-- =========================================================
-- Supabase schema v1 for agent-fx-bot
-- =========================================================

-- Extensions
create extension if not exists "pgcrypto";

-- =========================================================
-- ENUMS
-- =========================================================
do $$ begin
  create type timeframe_t as enum ('M15','H1','H4');
exception when duplicate_object then null; end $$;

do $$ begin
  create type raw_signal_t as enum ('long','short','flat');
exception when duplicate_object then null; end $$;

do $$ begin
  create type decision_action_t as enum ('open','close','hold');
exception when duplicate_object then null; end $$;

do $$ begin
  create type decision_direction_t as enum ('buy','sell');
exception when duplicate_object then null; end $$;

do $$ begin
  create type exec_status_t as enum (
    'accepted','filled','rejected','cancelled','sl_failed','tp_failed'
  );
exception when duplicate_object then null; end $$;

-- =========================================================
-- CORE DATA TABLES (APPEND ONLY)
-- =========================================================

create table if not exists market_snapshots (
  id bigserial primary key,
  schema_version int not null default 1,
  ts timestamptz not null,
  symbol text not null,
  timeframe timeframe_t not null,
  close double precision not null,
  atr double precision not null,
  rsi double precision not null,
  ma_fast double precision not null,
  ma_slow double precision not null,
  spread double precision not null,
  created_at timestamptz not null default now()
);

create index if not exists idx_market_snapshots_symbol_tf_ts
  on market_snapshots(symbol, timeframe, ts desc);

-- ---------------------------------------------------------

create table if not exists signals (
  id bigserial primary key,
  schema_version int not null default 1,
  ts timestamptz not null default now(),
  symbol text not null,
  raw_signal raw_signal_t not null,
  entry_triggered boolean not null,
  sl_pips double precision not null,
  tp_pips double precision not null,
  confidence double precision not null,
  created_at timestamptz not null default now()
);

create index if not exists idx_signals_symbol_ts
  on signals(symbol, ts desc);

-- ---------------------------------------------------------

create table if not exists agent_reports (
  id bigserial primary key,
  schema_version int not null default 1,
  ts timestamptz not null default now(),
  scope text not null default 'portfolio',
  symbol text null,
  trade_allowed boolean not null,
  risk_modifier double precision not null,
  flags jsonb not null default '[]'::jsonb,
  comment text null,
  created_at timestamptz not null default now()
);

create index if not exists idx_agent_reports_ts
  on agent_reports(ts desc);

-- ---------------------------------------------------------

create table if not exists decisions (
  decision_id uuid primary key default gen_random_uuid(),
  schema_version int not null default 1,
  ts timestamptz not null default now(),
  symbol text not null,
  action decision_action_t not null,
  direction decision_direction_t null,
  volume double precision null,
  sl_price double precision null,
  tp_price double precision null,
  reason_codes jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_decisions_symbol_ts
  on decisions(symbol, ts desc);

-- ---------------------------------------------------------

create table if not exists execution_reports (
  id bigserial primary key,
  schema_version int not null default 1,
  ts timestamptz not null default now(),
  decision_id uuid not null references decisions(decision_id) on delete cascade,
  order_id text null,
  status exec_status_t not null,
  message text null,
  created_at timestamptz not null default now()
);

create index if not exists idx_exec_reports_decision_ts
  on execution_reports(decision_id, ts desc);

-- =========================================================
-- TRADING STATE TABLES
-- =========================================================

create table if not exists orders (
  id bigserial primary key,
  ts timestamptz not null default now(),
  decision_id uuid null references decisions(decision_id) on delete set null,
  broker_order_id text not null,
  symbol text not null,
  direction decision_direction_t null,
  order_type text not null,
  qty double precision not null,
  price double precision null,
  status text not null,
  meta jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create unique index if not exists ux_orders_broker_order_id
  on orders(broker_order_id);

-- ---------------------------------------------------------

create table if not exists fills (
  id bigserial primary key,
  ts timestamptz not null default now(),
  broker_order_id text not null,
  symbol text not null,
  direction decision_direction_t null,
  fill_qty double precision not null,
  fill_price double precision not null,
  commission double precision null,
  liquidity text null,
  meta jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------

create table if not exists positions (
  symbol text primary key,
  updated_at timestamptz not null default now(),
  side decision_direction_t null,
  qty double precision not null default 0,
  avg_price double precision null,
  sl_price double precision null,
  tp_price double precision null,
  source text not null default 'bot',
  meta jsonb not null default '{}'::jsonb
);

-- ---------------------------------------------------------

create table if not exists risk_events (
  id bigserial primary key,
  ts timestamptz not null default now(),
  event_type text not null,
  severity text not null default 'info',
  symbol text null,
  message text null,
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- =========================================================
-- USER PROFILES + RLS
-- =========================================================

create table if not exists user_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  is_admin boolean not null default false,
  created_at timestamptz not null default now()
);

alter table user_profiles enable row level security;

create policy "profiles_read_own"
on user_profiles for select
to authenticated
using (user_id = auth.uid());

-- ---------------------------------------------------------
-- ADMIN HELPER
-- ---------------------------------------------------------

create or replace function is_admin()
returns boolean
language sql stable
as $$
  select exists (
    select 1 from user_profiles
    where user_id = auth.uid() and is_admin = true
  );
$$;

-- =========================================================
-- RLS FOR LOG TABLES (READ-ONLY FOR ADMIN)
-- =========================================================

alter table market_snapshots enable row level security;
create policy "snapshots_admin_read"
on market_snapshots for select
to authenticated
using (is_admin());

alter table signals enable row level security;
create policy "signals_admin_read"
on signals for select
to authenticated
using (is_admin());

alter table agent_reports enable row level security;
create policy "agent_reports_admin_read"
on agent_reports for select
to authenticated
using (is_admin());

alter table decisions enable row level security;
create policy "decisions_admin_read"
on decisions for select
to authenticated
using (is_admin());

alter table execution_reports enable row level security;
create policy "execution_reports_admin_read"
on execution_reports for select
to authenticated
using (is_admin());

alter table orders enable row level security;
create policy "orders_admin_read"
on orders for select
to authenticated
using (is_admin());

alter table fills enable row level security;
create policy "fills_admin_read"
on fills for select
to authenticated
using (is_admin());

alter table positions enable row level security;
create policy "positions_admin_read"
on positions for select
to authenticated
using (is_admin());

alter table risk_events enable row level security;
create policy "risk_events_admin_read"
on risk_events for select
to authenticated
using (is_admin());

-- =========================================================
-- END
-- =========================================================
