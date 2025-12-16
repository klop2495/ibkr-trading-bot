-- Add warmup bars minimum to bot_settings for market data readiness
alter table public.bot_settings add column if not exists warmup_bars_min int not null default 300;
