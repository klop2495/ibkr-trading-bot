-- Ensure exactly one deny_update_delete trigger per append-only table
do $$
begin
  -- signal_previews
  execute 'drop trigger if exists trg_signal_previews_deny_ud on public.signal_previews';
  execute 'create trigger trg_signal_previews_deny_ud before update or delete on public.signal_previews for each row execute function public.deny_update_delete()';

  -- agent_reports
  execute 'drop trigger if exists trg_agent_reports_deny_ud on public.agent_reports';
  execute 'create trigger trg_agent_reports_deny_ud before update or delete on public.agent_reports for each row execute function public.deny_update_delete()';

  -- control_decisions
  execute 'drop trigger if exists trg_control_decisions_deny_ud on public.control_decisions';
  execute 'create trigger trg_control_decisions_deny_ud before update or delete on public.control_decisions for each row execute function public.deny_update_delete()';

  -- risk_verdicts
  execute 'drop trigger if exists trg_risk_verdicts_deny_ud on public.risk_verdicts';
  execute 'create trigger trg_risk_verdicts_deny_ud before update or delete on public.risk_verdicts for each row execute function public.deny_update_delete()';
end $$;
