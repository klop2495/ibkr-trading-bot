alter table public.bot_settings
  add column if not exists signals_params jsonb not null default '{}'::jsonb;
