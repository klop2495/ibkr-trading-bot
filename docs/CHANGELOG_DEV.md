# Dev Changelog (append-only)

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
