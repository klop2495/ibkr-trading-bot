# STATE_CAPTURE_CONTROL_PLANE
- timestamp: 2025-12-19T20:37:27.105517+00:00
- supabase_url: https://kimuxfiaoyyswdwkubve.supabase.co
- owner_user_id: fb7e03c2-aef5-4215-acd0-47902df9c721

## Counts
- signal_previews_total: 946
- control_decisions_total: 515
- risk_verdicts_total: 515
- risk_events_total: 1140

## Coverage
- previews_without_decision: 431
- decisions_without_verdict: 0
- verdicts_without_decision: 0

## Freshness
- signal_previews_latest_ts: None
- control_decisions_latest_ts: None
- risk_verdicts_latest_ts: None

## FK checks
- decisions_missing_preview: 0
- verdicts_missing_decision: 0
- verdict_preview_mismatch: 0
- decisions_missing_preview_fk: 0
- verdicts_missing_decision_fk: 0

## Sanity
- decisions_missing_preview_fk: 0
- verdicts_missing_decision_fk: 0

## Top errors
- 2025-12-19T20:09:59.894281+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:09:59.824184+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:09:49.415348+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:09:49.34071+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:09:39.094763+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:09:39.020729+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:09:28.687491+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:09:28.62415+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:09:18.173439+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:09:18.100642+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:09:07.699546+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:09:07.625107+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:08:57.328063+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:08:57.258097+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:08:46.954348+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:08:46.882863+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:08:36.668862+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:08:36.598214+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:08:26.314058+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given
- 2025-12-19T20:08:26.22807+00:00 | CONTROL_DECISION_PERSIST | ERROR | fetch pending previews failed | BaseSelectRequestBuilder.order() takes 2 positional arguments but 3 were given

## Warnings
- none

## Smoke
- not run

## Next actions
- Проверить persist decision: есть превью без решения.