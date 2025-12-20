-- Counts
select count(*) as signal_previews from public.signal_previews;
select count(*) as control_decisions from public.control_decisions;
select count(*) as risk_verdicts from public.risk_verdicts;
select event_type, count(*) from public.risk_events group by event_type order by event_type;

-- Latest chain (preview -> decision -> verdict)
select p.id as preview_id, d.id as decision_id, v.id as verdict_id, p.ts_utc
from public.signal_previews p
left join public.control_decisions d on d.signal_preview_id = p.id
left join public.risk_verdicts v on v.decision_id = d.id
order by p.ts_utc desc
limit 10;

-- Coverage
select count(*) as previews_without_decision
from public.signal_previews p
left join public.control_decisions d on d.signal_preview_id = p.id
where d.id is null;

select count(*) as decisions_without_verdict
from public.control_decisions d
left join public.risk_verdicts v on v.decision_id = d.id
where v.id is null;

-- FK sanity
select count(*) as decisions_missing_preview_fk from public.control_decisions where signal_preview_id is null;
select count(*) as verdicts_missing_decision_fk from public.risk_verdicts where decision_id is null;

-- Recent risk events for control-plane persists
select ts, event_type, severity, message, data
from public.risk_events
where event_type in ('CONTROL_DECISION_PERSIST','RISK_VERDICT_PERSIST','SIGNAL_PREVIEW_PERSIST')
order by ts desc
limit 20;
