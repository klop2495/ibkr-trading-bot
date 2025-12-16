# Agents Layer — v1

Source of truth: follows `docs/spec_v1.md` §11 (Agents) and `docs/AI_RULES.md`.

## Scope and invariants
- Agents provide qualitative gating only; they **never compute numeric values** (prices, SL/TP, pips, lots, margin, PnL, leverage).
- Scale-in remains **forbidden** in v1; agent output must not enable add-to-position.
- Agents are not a single point of failure: any error/timeout falls back to conservative defaults (fail-safe).
- Idempotency is preserved upstream via `decision_id`; agents must not create new identifiers.

## Contracts

### AgentRequest (Pydantic)
- `decision_id: str` — required for traceability; supplied by caller.
- `ts: datetime` (UTC) — request timestamp.
- `symbols: list[str]` — exactly the v1 set (`EURUSD`, `GBPUSD`, `USDJPY`, `USDCHF`, `AUDUSD`, `USDCAD`, `NZDUSD`).
- `timeframes: list[str]` — expected `["M15", "H1", "H4"]`.
- `context_flags: list[str]` — categorical markers (e.g., `NEWS_WINDOW`, `DATA_GAP`, `RISK_OFF`) without numeric payloads.
- `features_summary: dict[str, str]` — pre-binned/categorical feature views per symbol/TF (e.g., `EURUSD:H1=RSI_HIGH`, `GBPUSD:H4=MA_BEARISH`); no raw numbers.
- `position_state: dict[str, str]` — per symbol flat/long/short only; no sizes or exposure numbers.

### AgentResponse (Pydantic)
- `decision_id: str` — must echo the request for idempotency.
- `trade_allowed: bool` — if any uncertainty/error → `false`.
- `risk_modifier: float` — bounded **0.5..1.0**; defaults to **1.0** on success; may downgrade to **0.8** under volatility fallback.
- `flags: list[str]` — categorical tags only (e.g., `NEWS_RISK`, `VOLATILITY_FALLBACK`, `DATA_QUALITY_TIMEOUT`).
- `comment: str` — short human-readable rationale; no numeric calculations.

## Orchestrator aggregation (multi-agent)
- `trade_allowed` — **any `false` blocks** the combined result.
- `risk_modifier` — take the **minimum** across agent responses (default 1.0 when agent absent/ok).
- `flags` — **union** of all flags (deduplicated).
- Aggregation is idempotent by `decision_id`; reruns with the same `decision_id` must not re-enter.

## Timeouts and fallbacks
- `news` or `data_quality` timeout → `trade_allowed=false`, add flag (`NEWS_TIMEOUT` or `DATA_QUALITY_TIMEOUT`), comment notes timeout.
- `volatility` timeout → `trade_allowed=true`, `risk_modifier=0.8`, add `VOLATILITY_FALLBACK` flag, comment notes fallback.
- Any other agent error/timeout → conservative default (`trade_allowed=false`, flag `AGENT_ERROR`).
- Timeouts are enforced per agent; orchestrator must not block waiting indefinitely.

## Circuit breaker and resilience
- Agents are **not SPOF**: orchestrator must continue with remaining agents and apply fallbacks.
- Circuit breaker trips when repeated agent failures/timeouts exceed policy → switch to safe mode (`trade_allowed=false`, flag `AGENT_CIRCUIT_BREAKER`) until reset window elapses.
- Orchestrator must log all agent outcomes with `decision_id` and `ts`; append-only semantics respected by storage adapters.
- OpenAI client is implemented via stdlib HTTP; missing configuration or network errors raise `AgentCallError`/`AgentTimeout` for orchestrator fallback (safe mode).

### Circuit breaker behavior (orchestrator)
- Threshold: after N consecutive agent failures (configurable; default 3) breaker opens.
- Cooldown: breaker stays open for a configured window; during this time agent calls are skipped and `trade_allowed=false`, `risk_modifier=0.5`, flag `agent_circuit_breaker`.
- On timeout: fallback `trade_allowed=false`, `risk_modifier=0.5`, flag `agent_timeout`, `timed_out=true`.
- On other agent errors/refusals: fallback `trade_allowed=false`, `risk_modifier=0.5`, flag `agent_error`.

## Integration (gate)
- Use `evaluate_agents(agent_request, orchestrator, agents_enabled)` as the single entry for the decision layer.
- If `agents_enabled` is false or orchestrator is `None`, gate returns `trade_allowed=true`, `risk_modifier=1.0`, and flag `agents_disabled`.
- If enabled, the orchestrator handles aggregation, fallbacks, and circuit breaker per rules above.

## Prohibited behaviors
- No numeric computations by LLM/agents (prices, SL/TP, lots, pips, margin, leverage, PnL).
- No scale-in enabling signals; if existing position detected, upstream risk layer blocks per spec.
- No raw dicts inside pipeline logic; only Pydantic models for requests/responses (serialization allowed at boundaries).
