# Backend Audit Report (Codex Task #7.5.0)

## Executive Summary
- Control-plane persists previews and decisions/verdicts; remaining compatibility risks around Supabase insert patterns and ID handling.
- Service-role envs are required at runtime; missing envs crash early without telemetry.
- Several repos still chain `.insert().select()` which previously broke in this Supabase runtime.
- Decision/Risk logging is inconsistent on ID field names, making diagnostics harder.
- Schema vs models drift (enum casing, numeric bounds) can allow invalid data past Pydantic checks.
- Append-only triggers and RLS exist for core tables, but legacy tables remain alongside new control-plane schema.
- Tests pass locally; no lint/static analysis configured; compileall OK.

## Source Map
- Entry points: `app/main.py` (control-plane loop), scripts/smoke: `scripts/smoke_control_plane.py`.
- Modules:
  - Market data: `app/market_data/*` (fetchers, service, buffer, seed).
  - Signals: `app/signals/*`, `app/models/signal_preview.py`.
  - Agents/decisions: `app/agents/*`, `app/models/decision.py`, `app/agglomerates`.
  - Risk: `app/risk/engine_v1.py`, `app/models/risk_verdict.py`.
  - Execution: `app/execution/*` (runner, engine_stub, oms_stub, adapters, gates).
  - Reconciliation: `app/reconciliation/*`, `app/models/reconciliation_report_v1.py`.
  - Storage/repos: `app/storage/repositories.py`, `app/storage/bot_settings_repo.py`, `app/storage/db.py`.
  - API: `app/api/admin/control/*` (previews/meta/chain).
  - Scripts: `scripts/` utilities, new `scripts/smoke_control_plane.py`.

## Dependencies (from requirements.txt)
- supabase>=2.6.0 (listed twice).
- pydantic>=2.7.
- ib-insync>=0.9.86.
- postgrest/httpx versions not pinned (inherit from supabase-py).

## Schema Summary (from migrations 001–016)
- `market_snapshots` (001): ts/symbol/timeframe enums, append-only index.
- `signals` (001): raw_signal enum, confidence numeric, append-only.
- `agent_reports` (001): flags jsonb, append-only trigger (012).
- `risk_events` (001): event_type/severity, data jsonb, append-only.
- `signal_previews` (006): UUID PK, ts_utc, symbol, setup_type/direction text, flags jsonb, unique (ts_utc,symbol,timeframe_trigger,engine_version), append-only trigger + RLS (service_role insert/select).
- `control_decisions` (010): UUID PK, decision_version, ts_utc, symbol, signal_preview_id FK, risk_modifier numeric, flags jsonb, unique (signal_preview_id, decision_version), append-only trigger + RLS.
- `risk_verdicts` (011): UUID PK, risk_version, ts_utc, decision_id FK, signal_preview_id FK, flags jsonb, unique (decision_id, risk_version), append-only trigger + RLS.
- Control execution chain tables (013–016): execution_reports, order_intents, broker_requests, reconciliation_reports with append-only triggers and service_role policies.
- RLS: enabled on append-only tables with service_role select/insert policies.

## Model vs Schema Drift (examples)
- Enums: DB uses text/enums (e.g., raw_signal_t long/short/flat), but Pydantic models (`SignalPreviewV1.Direction`) are lowercase strings; DB lacks check constraints -> casing drift possible.
- `risk_modifier` in DB is numeric unbounded; models constrain to 0.0–2.0 (DecisionV1/RiskVerdictV1) -> DB can store out-of-range if validation bypassed.
- Legacy tables (`decisions`, `execution_reports`) remain from 001 and are unused by current control-plane models; risk of confusion during queries/backfills.

## Findings (detailed list below)
See section “Findings” and `audit_findings.json` for structured list.

## Commands Run
- `.venv/bin/python -m pytest -q` (pass; 96 tests, existing upstream pydantic warnings).
- `.venv/bin/python -m compileall app` (pass).

## Findings (summary)
1) `.insert().select()` still in execution-related repos (app/storage/repositories.py: ExecutionReportsRepo, OrderIntentsRepo, BrokerRequestsRepo, ReconciliationReportsRepo) — incompatible with current Supabase runtime; risk of attribute errors and missing IDs.
2) Risk engine fabricates UUID when decision.id is missing (app/risk/engine_v1.py) → verdicts may not match persisted decisions.
3) Error telemetry uses mixed keys (`preview_id` vs `signal_preview_id`) in risk_event data (app/main.py around decision persist), complicating diagnostics.
4) Risk_event logging in persist error paths swallows failures silently except stderr prints; no retry/backoff.
5) supabase envs are mandatory; SupabaseDB raises without risk_event (app/storage/db.py) → hard crash, no telemetry.
6) Schema/model drift on enum/text casing (signal_previews.direction/setup_type text vs model enums) allows invalid casing; no DB constraint.
7) Schema/model drift on `risk_modifier` bounds (numeric unbounded in DB vs 0–2 in model) → possible inconsistent data if validations bypassed.
8) Legacy tables (`decisions`, `execution_reports`, etc. from 001) still present; not append-only/RLS-aligned with current control-plane, risk of querying wrong tables.
9) No lint/static analysis configured (ruff/flake8/mypy absent) → potential undiscovered style/typing errors.
10) Tests lack integration coverage for Supabase conflict/idempotency on control_decisions/risk_verdicts; only unit stubs — real conflict paths untested.

## Next Steps (high level)
- Refactor remaining repos to drop `.select()` after insert and add conflict-safe ID lookups (same pattern as signal_previews).
- Enforce consistent telemetry keys and ensure risk_event logging cannot fail silently.
- Add lightweight integration test hitting Supabase (or contract tests) for decision/verdict insert/idempotency.
- Consider adding DB check constraints or enum types for control-plane tables to align with Pydantic expectations.
- Add lint/static tooling configuration and CI hook.

## Self-Audit
- No secrets or env values were recorded.
- Database schema was read only; no migrations executed/altered.
- Business logic unchanged; only audit artifacts added.
