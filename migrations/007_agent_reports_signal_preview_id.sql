alter table if exists agent_reports
  add column if not exists signal_preview_id uuid;

create index if not exists idx_agent_reports_signal_preview_id
  on agent_reports(signal_preview_id);

alter table if exists agent_reports
  add constraint fk_agent_reports_signal_preview
  foreign key (signal_preview_id) references signal_previews(id);
