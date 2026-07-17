# Codex Project: BingX USDT-M Perpetual Futures Demo Bot

## Goal

Adapt the existing `ibkr-trading-bot` architecture into a safe BingX USDT-M perpetual futures demo-trading system.

The project must target **BingX perpetual futures demo/VST only**. Do not implement live trading in this phase.

## Core principle

Preserve the strongest parts of the current project:

- typed Pydantic pipeline;
- append-only event and execution logs;
- decision → risk → execution separation;
- order/position reconciliation;
- server-side protective orders;
- deterministic risk controls;
- test coverage and auditability.

Remove IBKR-specific execution assumptions behind an adapter boundary.

## Required trading flow

```text
BingX market data
    -> deterministic candidate generator
    -> optional single GPT validator
    -> deterministic risk engine
    -> BingX demo execution adapter
    -> reconciliation of orders, fills, positions, SL/TP and PnL
```

GPT must never be allowed to:

- place orders directly;
- calculate unrestricted position size;
- change leverage;
- remove or widen a stop;
- bypass daily loss, drawdown or exposure limits;
- switch from demo to live mode.

## Scope for phase 1

### Instruments

- BTC-USDT perpetual
- ETH-USDT perpetual
- SOL-USDT perpetual

The adapter must normalize BingX symbols internally.

### Account and position mode

- USDT-M perpetual futures;
- isolated margin only;
- one-way mode initially;
- LONG and SHORT supported;
- maximum leverage: 2x;
- demo/VST environment only.

### Initial risk defaults

- risk per trade: 0.25%;
- maximum risk per trade allowed by configuration: 0.50%;
- maximum simultaneous positions: 2;
- daily realized + unrealized loss limit: 2%;
- maximum account drawdown: 5%;
- no averaging down;
- no martingale;
- no position increase after entry in phase 1;
- every position must have an exchange-side stop-loss;
- take-profit is optional but, when used, must be exchange-side;
- default minimum reward/risk: 1.5;
- stop may only move toward break-even/profit, never away from entry.

## Safety invariants

1. The application must refuse to start when the environment is not explicitly `demo`.
2. There must be no `LIVE=true` or similar one-variable switch capable of enabling real trading.
3. Live base URLs must not be present in the executable phase-1 adapter.
4. API credentials must come only from environment variables.
5. Withdrawal permissions are never required.
6. An entry order is not considered protected until the stop order is confirmed by BingX.
7. If protection cannot be confirmed, the bot must immediately attempt to flatten the position.
8. Local state must never be treated as the source of truth over the exchange.
9. On startup, reconciliation must run before signal generation is enabled.
10. Unknown exchange positions or orders must stop new trading and raise a critical alert.
11. Every order request must use an idempotent client order ID.
12. All quantities and prices must be rounded using exchange contract metadata.
13. Reduce-only must be used for exits and protective orders where supported.
14. All timestamps must be stored in UTC.
15. All financial calculations must use `Decimal`, not binary floats.

## Suggested architecture

```text
app/
  brokers/
    base.py
    bingx/
      client.py
      auth.py
      models.py
      mapper.py
      market_data.py
      execution.py
      reconciliation.py
      exceptions.py
  models/
    market.py
    candidate.py
    decision.py
    risk.py
    execution.py
    position.py
  strategies/
    base.py
    ema_rsi_atr.py
  validators/
    base.py
    deterministic.py
    gpt_validator.py
  risk/
    engine.py
    sizing.py
    limits.py
  services/
    trading_cycle.py
    protection.py
    reconciliation.py
  storage/
  api/
  tests/
```

Use existing project locations when they already provide equivalent abstractions. Do not duplicate working infrastructure unnecessarily.

## BingX adapter requirements

Implement a narrow interface for:

- server time synchronization;
- contract metadata;
- mark price and last price;
- OHLCV candles;
- funding rate and next funding time;
- account equity and available margin;
- open positions;
- open orders;
- recent fills/trades;
- leverage setup;
- isolated margin setup;
- market and limit entry orders;
- stop-market protective orders;
- take-profit-market orders;
- cancel order;
- cancel all symbol orders;
- reduce-only close;
- position reconciliation.

Do not guess BingX endpoint paths or request signatures. Use current official BingX API documentation as the source of truth and document every endpoint used.

## Data models

At minimum create typed models for:

- `ContractSpec`
- `MarketSnapshot`
- `FundingSnapshot`
- `TradeCandidate`
- `ValidationVerdict`
- `RiskDecision`
- `OrderIntent`
- `BrokerOrder`
- `Fill`
- `PositionSnapshot`
- `ProtectionStatus`
- `ReconciliationReport`

Enums must be used for:

- LONG / SHORT;
- BUY / SELL;
- ENTRY / EXIT / STOP_LOSS / TAKE_PROFIT;
- NEW / PARTIALLY_FILLED / FILLED / CANCELED / REJECTED / UNKNOWN;
- DEMO environment;
- ISOLATED margin;
- ONE_WAY position mode.

## Deterministic strategy for baseline

Implement one simple baseline strategy only:

- timeframe: 15m;
- trend filter: EMA 200;
- entry trigger: EMA 20/50 alignment plus RSI confirmation;
- ATR-based stop;
- fixed minimum reward/risk;
- signals only on fully closed candles;
- no signal if funding, spread, volatility or data-quality checks fail.

The baseline is for system validation, not a claim of profitability.

## GPT validator

The GPT validator is optional and must be disabled by default.

It receives a compact structured payload containing:

- symbol and side;
- strategy reason;
- current price and volatility;
- higher-timeframe trend summary;
- funding rate;
- recent volume anomaly;
- position and portfolio exposure;
- deterministic risk result;
- limited recent-news summary when available.

It returns a strict Pydantic model:

```python
class ValidationVerdict(BaseModel):
    action: Literal["ALLOW", "BLOCK", "REDUCE"]
    risk_multiplier: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    reasons: list[str]
    model: str
    prompt_version: str
```

Rules:

- malformed output means `BLOCK`;
- timeout means deterministic policy decides, configurable as BLOCK by default;
- `REDUCE` can only reduce size;
- GPT cannot convert LONG to SHORT or SHORT to LONG;
- GPT cannot create a trade candidate;
- all prompts and responses must be logged with secrets removed.

## Reconciliation behavior

At startup and on every cycle:

1. Fetch exchange positions, open orders and recent fills.
2. Compare with local order intents and position records.
3. Repair known delayed states.
4. Mark missing protective orders as critical.
5. Attempt to restore protection.
6. Flatten a position when protection cannot be restored.
7. Stop new entries while unresolved discrepancies exist.
8. Produce a typed reconciliation report.

## Database changes

Reuse existing append-only event patterns where possible.

Add or adapt tables for:

- market snapshots;
- trade candidates;
- validator verdicts;
- risk decisions;
- order intents;
- broker orders;
- fills;
- positions;
- protection state;
- reconciliation reports;
- account equity snapshots;
- daily risk state.

Never store API secrets in the database.

## Configuration

Create `.env.example` with placeholders only:

```env
TRADING_ENV=demo
BINGX_API_KEY=
BINGX_API_SECRET=
BINGX_DEMO_BASE_URL=
SYMBOLS=BTC-USDT,ETH-USDT,SOL-USDT
TIMEFRAME=15m
MAX_LEVERAGE=2
RISK_PER_TRADE=0.0025
MAX_RISK_PER_TRADE=0.005
MAX_POSITIONS=2
DAILY_LOSS_LIMIT=0.02
MAX_DRAWDOWN=0.05
GPT_VALIDATOR_ENABLED=false
OPENAI_API_KEY=
```

Validate all configuration at startup with Pydantic settings.

## Tests required

### Unit tests

- BingX signing and timestamp handling;
- contract rounding;
- position sizing;
- leverage cap;
- isolated margin invariant;
- stop cannot widen;
- LONG and SHORT PnL math;
- funding cost accounting;
- daily loss lock;
- drawdown lock;
- GPT malformed/timeout behavior;
- idempotent order IDs;
- mapping of all order statuses.

### Integration tests with mocked HTTP

- successful entry and protection;
- entry fill followed by failed stop creation and emergency flatten;
- partial fills;
- duplicate request retry;
- stale timestamp and time resynchronization;
- rate limit response and backoff;
- reconciliation after restart;
- unknown exchange position;
- exchange order missing locally;
- local order missing on exchange.

### End-to-end demo smoke test

Provide a command that:

- validates demo environment;
- fetches contract metadata;
- fetches account equity;
- fetches candles;
- creates no order by default;
- optionally submits and immediately closes a minimum-size demo position only when an explicit CLI confirmation flag is provided.

## CLI commands

Provide commands similar to:

```bash
python -m app.cli check-config
python -m app.cli ping-bingx
python -m app.cli reconcile
python -m app.cli account-status
python -m app.cli run-once --no-trade
python -m app.cli demo-smoke-order --symbol BTC-USDT --confirm-demo-order
python -m app.main
```

## Observability

Log structured events for:

- cycle start/end;
- market-data freshness;
- candidate creation;
- GPT verdict;
- risk decision;
- order request and response;
- fill;
- protection confirmation;
- reconciliation discrepancy;
- emergency flatten;
- risk lock activation.

Expose health information without exposing credentials or raw signed requests.

## Documentation deliverables

Create:

- `docs/BINGX_DEMO_SETUP.md`
- `docs/BINGX_API_ENDPOINTS.md`
- `docs/TRADING_PIPELINE.md`
- `docs/RISK_RULES.md`
- `docs/RECONCILIATION.md`
- `docs/GPT_VALIDATOR.md`
- `docs/DEMO_RUNBOOK.md`

The setup guide must explain how the user creates a BingX demo API key without requesting withdrawal permission.

## Implementation phases

### Phase 0 — Audit and plan

- locate reusable IBKR-independent modules;
- identify all IBKR coupling;
- produce a concise migration map;
- do not delete IBKR code yet.

### Phase 1 — BingX read-only adapter

- authentication;
- time sync;
- metadata;
- market data;
- account, orders, fills and positions;
- tests.

### Phase 2 — Demo execution

- isolated margin;
- leverage capped at 2x;
- entry and reduce-only exit;
- idempotency;
- protective SL/TP;
- emergency flatten;
- tests.

### Phase 3 — Reconciliation and risk

- startup reconciliation;
- continuous reconciliation;
- daily loss and drawdown locks;
- account equity snapshots;
- tests.

### Phase 4 — Baseline strategy

- closed-candle deterministic candidate generator;
- no GPT;
- paper/demo metrics.

### Phase 5 — Single GPT validator

- strict schema;
- disabled by default;
- A/B telemetry comparing baseline versus GPT-filtered decisions.

### Phase 6 — Dashboard/API adaptation

- display BingX demo account status;
- positions, orders, fills, SL/TP status;
- discrepancies and risk locks;
- clearly mark all screens as DEMO.

## Acceptance criteria

The project is accepted only when:

- all tests pass;
- the process cannot connect to a live trading endpoint;
- startup reconciliation completes before trading;
- every opened demo position receives a confirmed exchange-side stop;
- failure to protect causes emergency flatten;
- no trade exceeds configured risk or leverage;
- restart does not lose position/order state;
- GPT can only block or reduce deterministic candidates;
- A/B logs allow comparison of GPT and non-GPT decisions;
- documentation is sufficient for another developer to run the demo safely.

## Codex working instructions

1. Start by reading the complete repository and existing architecture documents.
2. Produce `docs/BINGX_MIGRATION_MAP.md` before implementation.
3. Work in small commits, one phase per pull request when practical.
4. Do not remove existing IBKR functionality unless explicitly required.
5. Prefer adapters and interfaces over broad rewrites.
6. Never commit secrets, real account identifiers, server IPs or API keys.
7. Do not claim an endpoint works without an automated or documented demo verification.
8. Do not enable GPT until the deterministic demo pipeline is stable.
9. At the end of each phase, update the changelog and run the full test suite.
10. Stop and report any ambiguity in official BingX demo API behavior rather than guessing.
