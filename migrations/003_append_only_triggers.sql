create or replace function public.deny_update_delete()
returns trigger as $$
begin
  raise exception 'UPDATE/DELETE is forbidden on append-only table: %', tg_table_name
    using errcode = '45000';
end;
$$ language plpgsql;

do $$
declare
  t text;
  tables text[] := array[
    'market_snapshots',
    'signals',
    'agent_reports',
    'decisions',
    'execution_reports',
    'orders',
    'fills',
    'risk_events'
  ];
begin
  foreach t in array tables loop
    if to_regclass('public.' || t) is not null then
      execute format('drop trigger if exists trg_%I_deny_ud on public.%I', t, t);
      execute format('create trigger trg_%I_deny_ud before update or delete on public.%I for each row execute function public.deny_update_delete()', t, t);
    end if;
  end loop;
end $$;
