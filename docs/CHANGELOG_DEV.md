# Dev Changelog (append-only)

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
