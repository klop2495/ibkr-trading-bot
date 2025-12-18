# Dev Changelog (append-only)

## 2025-12-18 — Stage 4.1 — Signals params in bot_settings
- Summary: Added signals_params jsonb column and strict Pydantic contracts with configuration gating; repo roundtrips jsonb safely; runtime logs SIGNALS_RULES_NOT_SPECIFIED when config missing; added migration and validation tests.
- Files: `migrations/005_bot_settings_signals_params.sql`, `app/models/signals_params.py`, `app/models/bot_settings.py`, `app/storage/bot_settings_repo.py`, `app/signals/engine.py`, `tests/test_bot_settings.py`, `tests/test_bot_settings_repo.py`, `tests/test_signals_params.py`, `tests/test_signals_rules_warning.py`, `app/main.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-18 — Bot settings control-plane hardening
- Summary: Hardened bot settings polling gate, strict patch validation, and ensured env-only startup; added/verified bot settings tests.
- Files: `app/main.py`, `app/models/bot_settings.py`, `app/storage/bot_settings_repo.py`, `tests/test_bot_settings.py`, `tests/test_bot_settings_repo.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-18 — Stage 3 — Market Data Layer (bars + QA + warm-up + feature snapshots)
- Summary: Added market data indicators, QA, warm-up readiness, IBKR fetcher, buffers, and service orchestration with tests and warmup settings.
- Files: `app/market_data/*`, `app/models/bot_settings.py`, `app/storage/bot_settings_repo.py`, `migrations/004_bot_settings_warmup.sql`, `tests/test_market_data_*`, `docs/CHANGELOG_DEV.md`

## 2025-12-18 — Stage 3 — Market Data runtime wiring (env-gated)
- Summary: Added env-gated wiring in main loop for MarketDataService using IBKRClient; uses BotSettings symbols/warmup; fail-safe if disabled/misconfigured; no trading.
- Files: `app/main.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-18 — Stage 4 — Signals (deterministic, no persistence yet)
- Summary: Added deterministic signals contracts/engine with safe defaults; persistence disabled until rules specified; logs `SIGNALS_RULES_NOT_SPECIFIED`; tests updated; main loop only logs once, no DB writes.
- Files: `app/signals/models.py`, `app/signals/engine.py`, `tests/test_signals_engine.py`, `tests/test_signals_rules_warning.py`, `app/main.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-18 — Stage 2 — IBKR Connect Layer
- Summary: Added IBKR Pydantic contracts, ib_insync client wrapper for connect/read-only account summary and positions snapshots, contract tests, and changelog update.
- Files: `app/models/ibkr.py`, `app/broker/ibkr_client.py`, `tests/test_ibkr_client_contract.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-17 — Bot settings control-plane alignment
- Summary: Aligned BotSettings model with DB constraints, added safe defaults, tightened repo with fail-safe get/update and UUID parsing in main loop, plus tests.
- Files: `app/models/bot_settings.py`, `app/storage/bot_settings_repo.py`, `app/main.py`, `tests/test_bot_settings.py`, `tests/test_bot_settings_repo.py`

## 2025-12-16 — Step 6: persist agent_reports from decision precheck + tests
- Summary: Added agent report repo, reporting helper, precheck persist hook with fail-safe fallback, and tests for repo payload and persist behavior.
- Files: `app/storage/agent_reports_repo.py`, `app/storage/repositories.py`, `app/agents/reporting.py`, `app/decision/agents_precheck.py`, `tests/test_agent_reports_repo_contract.py`, `tests/test_decision_agents_precheck_persist.py`, `docs/agents_spec.md`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Fix: circuit breaker cooldown + strict precheck validation
- Summary: Fixed circuit breaker cooldown to allow retries after window and enforced strict per-symbol input validation in decision precheck.
- Files: `app/agents/state.py`, `app/decision/agents_precheck.py`, `tests/test_decision_agents_precheck.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Step 5: decision pre-check hook for agents + tests
- Summary: Added decision-layer agents precheck builder and gate invocation, with validation tests and docs update.
- Files: `app/decision/agents_precheck.py`, `tests/test_decision_agents_precheck.py`, `docs/agents_spec.md`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Step 4: agents gate + config + tests
- Summary: Added agents gate with env-driven config and fail-safe defaults, plus tests and documentation updates.
- Files: `app/agents/config.py`, `app/agents/gate.py`, `tests/test_agents_gate.py`, `docs/agents_spec.md`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Step 3: orchestrator + circuit breaker + tests
- Summary: Implemented agents orchestrator with deterministic aggregation, fallback policy, circuit breaker state, and unit tests covering success, timeout, error, and cooldown flows.
- Files: `app/agents/orchestrator.py`, `app/agents/state.py`, `tests/test_agents_orchestrator.py`, `docs/agents_spec.md`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Step 2: OpenAI Responses client (stdlib) + tests
- Summary: Added stdlib-based OpenAI Responses client with structured outputs validation and tests for success, schema errors, HTTP errors, and timeouts.
- Files: `app/agents/errors.py`, `app/agents/openai_client.py`, `tests/test_agents_openai_client.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Step 1: agent schemas + tests
- Summary: Added strict Pydantic agent contracts with extra-forbid and validation tests for bounds, universe/timeframes, and serialization.
- Files: `app/agents/__init__.py`, `app/agents/schemas.py`, `tests/test_agents_schemas.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Add agents gate hook
- Summary: Added decision-layer gate entry point with disabled fallback, docs update, and tests for enabled/disabled paths.
- Files: `app/agents/gate.py`, `tests/test_agents_gate.py`, `docs/agents_spec.md`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Add agent reporting and risk event hooks
- Summary: Added agent report insert-only repo, orchestrator hooks for agent reports and risk events (timeouts, CB on/off, trade blocks), with unit tests for reporting and logging.
- Files: `app/storage/agent_reports_repo.py`, `app/agents/orchestrator.py`, `tests/test_agent_reports_repo.py`, `tests/test_agents_orchestrator.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Add agents orchestrator with circuit breaker
- Summary: Implemented deterministic agents orchestrator with fallbacks, circuit breaker state, and tests covering aggregation, timeouts, and safe-mode behavior.
- Files: `app/agents/orchestrator.py`, `app/agents/state.py`, `tests/test_agents_orchestrator.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Add OpenAI agent client interface and errors
- Summary: Added agent client protocol with stdlib OpenAI client, agent errors, contract tests, and documented fallback behavior.
- Files: `app/agents/client.py`, `app/agents/errors.py`, `tests/test_agents_client_contract.py`, `docs/agents_spec.md`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Add agent schemas and tests
- Summary: Added Pydantic agent request/response/decision schemas and unit tests for bounds and serialization.
- Files: `app/agents/schemas.py`, `tests/test_agents_schemas.py`, `docs/CHANGELOG_DEV.md`

## 2025-12-16 — Add agents spec scaffold and dev changelog
- Summary: Documented agent layer contracts, aggregation, and fail-safe fallbacks; added dev changelog.
- Files: `docs/agents_spec.md`, `docs/CHANGELOG_DEV.md`
