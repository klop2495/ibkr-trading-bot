-- 002_bot_settings_fix.sql
-- Align existing public.bot_settings to v1 risk profile defaults and policies.
-- Keeps current structure: owner_user_id (per-user row), symbols jsonb, updated_at.

-- 1) Defaults (match Risk Profile v1)
alter table public.bot_settings
  alter column trading_enabled set default false;

alter table public.bot_settings
  alter column mode set default 'paper';

alter table public.bot_settings
  alter column risk_per_trade set default 0.005;

alter table public.bot_settings
  alter column max_open_positions set default 3;

alter table public.bot_settings
  alter column max_trades_per_day_portfolio set default 2;

alter table public.bot_settings
  alter column max_trades_per_day_per_symbol set default 1;

alter table public.bot_settings
  alter column max_usd_side_positions set default 2;

alter table public.bot_settings
  alter column daily_loss_limit set default 0.015;

alter table public.bot_settings
  alter column loss_streak_breaker set default 3;

alter table public.bot_settings
  alter column breaker_pause_hours set default 24;

alter table public.bot_settings
  alter column max_effective_leverage set default 2.0;

alter table public.bot_settings
  alter column max_margin_utilization set default 0.35;

-- symbols is jsonb: default list of majors
alter table public.bot_settings
  alter column symbols set default '["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD"]'::jsonb;

-- 2) Constraints (safety)
do $$ begin
  alter table public.bot_settings
    add constraint bot_settings_mode_chk check (mode in ('paper','live'));
exception when duplicate_object then null; end $$;

do $$ begin
  alter table public.bot_settings
    add constraint bot_settings_risk_per_trade_chk check (risk_per_trade >= 0 and risk_per_trade <= 0.05);
exception when duplicate_object then null; end $$;

do $$ begin
  alter table public.bot_settings
    add constraint bot_settings_max_open_positions_chk check (max_open_positions >= 0 and max_open_positions <= 20);
exception when duplicate_object then null; end $$;

do $$ begin
  alter table public.bot_settings
    add constraint bot_settings_max_trades_portfolio_chk check (max_trades_per_day_portfolio >= 0 and max_trades_per_day_portfolio <= 50);
exception when duplicate_object then null; end $$;

do $$ begin
  alter table public.bot_settings
    add constraint bot_settings_max_trades_symbol_chk check (max_trades_per_day_per_symbol >= 0 and max_trades_per_day_per_symbol <= 20);
exception when duplicate_object then null; end $$;

do $$ begin
  alter table public.bot_settings
    add constraint bot_settings_max_usd_side_chk check (max_usd_side_positions >= 0 and max_usd_side_positions <= 20);
exception when duplicate_object then null; end $$;

do $$ begin
  alter table public.bot_settings
    add constraint bot_settings_daily_loss_limit_chk check (daily_loss_limit >= 0 and daily_loss_limit <= 0.5);
exception when duplicate_object then null; end $$;

do $$ begin
  alter table public.bot_settings
    add constraint bot_settings_loss_streak_breaker_chk check (loss_streak_breaker >= 0 and loss_streak_breaker <= 20);
exception when duplicate_object then null; end $$;

do $$ begin
  alter table public.bot_settings
    add constraint bot_settings_breaker_pause_hours_chk check (breaker_pause_hours >= 0 and breaker_pause_hours <= 168);
exception when duplicate_object then null; end $$;

do $$ begin
  alter table public.bot_settings
    add constraint bot_settings_max_effective_leverage_chk check (max_effective_leverage >= 0 and max_effective_leverage <= 50);
exception when duplicate_object then null; end $$;

do $$ begin
  alter table public.bot_settings
    add constraint bot_settings_max_margin_utilization_chk check (max_margin_utilization >= 0 and max_margin_utilization <= 1);
exception when duplicate_object then null; end $$;

-- 3) updated_at trigger
create or replace function public.set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_bot_settings_updated_at on public.bot_settings;
create trigger trg_bot_settings_updated_at
before update on public.bot_settings
for each row execute function public.set_updated_at();

-- 4) RLS policies:
-- Keep owner-based access + allow admin read/write.
alter table public.bot_settings enable row level security;

-- Owner: select/insert/update own row
drop policy if exists settings_owner_select on public.bot_settings;
create policy settings_owner_select
on public.bot_settings for select
to authenticated
using (owner_user_id = auth.uid());

drop policy if exists settings_owner_insert on public.bot_settings;
create policy settings_owner_insert
on public.bot_settings for insert
to authenticated
with check (owner_user_id = auth.uid());

drop policy if exists settings_owner_update on public.bot_settings;
create policy settings_owner_update
on public.bot_settings for update
to authenticated
using (owner_user_id = auth.uid())
with check (owner_user_id = auth.uid());

-- Admin: select/update any row (use your existing public.is_admin())
drop policy if exists settings_admin_select on public.bot_settings;
create policy settings_admin_select
on public.bot_settings for select
to authenticated
using (public.is_admin());

drop policy if exists settings_admin_update on public.bot_settings;
create policy settings_admin_update
on public.bot_settings for update
to authenticated
using (public.is_admin())
with check (public.is_admin());

-- No delete policy -> delete denied

