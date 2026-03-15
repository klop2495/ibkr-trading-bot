# Project Update: USD Equity-Adaptive Model

Date: 2026-03-15
Scope: align signal, risk, and execution logic for a USD-denominated account that sizes from real current equity.

## Operating Principle

The bot must not be hard-coded to a fixed deposit amount.

Correct model:

- account currency: `USD`
- position sizing uses real current account equity
- if equity falls, size falls automatically
- if equity rises, size may rise within limits
- if equity becomes too small for a valid structural setup, the bot skips the trade

`3000 USD` is treated as the user's current starting capital, not as a fixed design assumption.

## Target Operating Model

- Account currency: `USD`
- Execution mode: `paper` first, then `live`
- Instruments: FX CFD only
- Position sizing: true USD-based dynamic risk sizing
- Strategy: deterministic structural swing continuation
- Goal: if a trade reaches final execution gates, it either submits a valid order or fails with a precise operational reason

## Core Invariants

1. Signal logic is market-structure dependent, not deposit dependent.
2. Risk sizing is equity-adaptive.
3. Execution viability is checked on every trade using current equity and broker state.
4. If the account is too small for the current setup, the trade is skipped cleanly.

## Current Problems

### 1. `risk_per_trade` semantics are ambiguous

Current code documents `risk_per_trade` as percent:

- `0.5 = 0.5%`

But existing rows have used values like `0.005`, which means `0.005%`, not `0.5%`.

Impact:

- position sizes collapse
- required minimum equity becomes unrealistically high
- `risk_modifier` can become ineffective due to min/max clamp convergence

### 2. `RiskEngineV1` is not a real risk engine

Today it mostly mirrors:

- `settings.trading_enabled`
- `decision.trade_allowed`

It does not enforce key portfolio risk settings such as:

- `max_trades_per_day_portfolio`
- `max_trades_per_day_per_symbol`
- `daily_loss_limit`
- `loss_streak_breaker`
- `max_margin_utilization`

### 3. Signal engine is simpler than the declared spec

The current signal path is effectively:

- H4 MA alignment
- H1 MA alignment
- M15 RSI + near-MA confirm

It does not implement true structural continuation.

### 4. Execution path mixes CFD orders with spot-style cash checks

OMS submits CFD FX contracts, but `FXFundsGuard` validates balances like spot FX/CASH.

This is conceptually wrong for the active execution mode.

### 5. Position sizing assumes USD account currency by default, but not explicitly enough

The target model is USD-only, but the runtime must enforce that instead of relying on defaults.

## Recommended Solution

## A. Normalize settings semantics

Adopt one invariant:

- `risk_per_trade` is always stored in percent units
- Example:
  - `0.25` means `0.25%`
  - `0.5` means `0.5%`
  - `1.0` means `1.0%`

Required actions:

- keep model semantics as percent
- migrate any mistaken-scale rows
- validate input aggressively

Recommended starting live baseline:

- `risk_per_trade = 0.25` or `0.5`

Recommendation:

- start `live` at `0.25`
- allow `0.5` only after paper validation

## B. Make USD explicit everywhere

Required actions:

- add explicit `account_currency` to execution sizing config flow
- validate that runtime equity is interpreted as USD
- if broker account currency differs, convert before sizing or block startup

Invariant:

- equity, pip value, risk amount, and exposure must use the same currency domain

## C. Replace spot-style funds guard with CFD-compatible margin checks

For FX CFD mode:

- do not require base/quote cash balances like spot FX
- validate using broker margin / buying power / exposure logic

Required actions:

- disable `FXFundsGuard` in CFD mode
- add CFD-specific pre-trade guard using:
  - broker connection health
  - available funds
  - buying power
  - margin utilization
  - max effective leverage

## D. Turn `RiskEngineV1` into a real pre-execution policy engine

`RiskEngineV2` should evaluate:

- `trading_enabled`
- `decision.trade_allowed`
- `max_open_positions`
- per-symbol daily trade cap
- portfolio daily trade cap
- loss streak breaker
- daily loss breaker
- max leverage policy
- max margin utilization policy

Execution service should still keep operational guards:

- broker disconnected
- broker already has position
- duplicate decision
- symbol lock
- OMS exception
- broker-side order rejection

## E. Implement real structural swing continuation

Use actual `structure` params:

- detect confirmed swing highs/lows on M15
- define impulse leg
- detect pullback
- define continuation trigger
- define invalidation
- derive SL from structure
- derive TP from RR

This makes the strategy valid across varying account sizes because:

- the signal is market-derived
- the risk sizing adapts to current equity

## F. Guarantee deterministic final outcome after verdict

Required invariant:

- if a verdict is `ALLOW`, the remaining failures must be strictly operational and explicitly logged

Allowed final failure classes after risk allow:

- `broker_disconnected`
- `broker_has_position`
- `broker_has_open_orders`
- `symbol_locked`
- `forecast_gate_blocked`
- `margin_blocked`
- `trade_row_not_created`
- `oms_error`
- `position_too_small`

Disallowed outcome:

- silent disappearance between `ALLOW` and execution

## Equity-Adaptive Sizing Model

The strategy must work like this:

- take current real equity from account
- compute risk budget:
  - `risk_amount = equity * risk_per_trade_pct / 100`
- compute structural stop from signal
- compute position size from:
  - risk amount
  - stop size
  - pip value

If the resulting trade is too small to execute safely:

- skip trade
- log reason precisely

This is correct behavior.

## Minimum Viable Trade Logic

The bot must not promise to trade at any arbitrarily low equity.

Correct rule:

- bot must be able to operate at any equity level
- but it may skip trades when current equity is too low for the current setup

Examples of valid skip reasons:

- `below_min_position`
- `position_too_small`
- `margin_blocked`

This is not a strategy failure. It is correct risk discipline.

## Broker Validation Required

Before live rollout, these must be verified specifically for FX CFD:

1. minimum accepted quantity
2. quantity step size
3. margin requirement by symbol
4. small-size order acceptance on this account type
5. effect of broker-side leverage and buying power on order acceptance

Important:

- public spot FX/IDEALPRO minimums must not be reused as truth for CFD mode

## Recommended Starting Configuration

This is not fixed to 3000 USD. It is the recommended starting profile for a small account and should scale with equity.

- `account_currency = USD`
- `mode = paper`
- `risk_per_trade = 0.25`
- `max_open_positions = 2`
- `max_trades_per_day_portfolio = 2`
- `max_trades_per_day_per_symbol = 1`
- `daily_loss_limit = 0.02`
- `loss_streak_breaker = 3`
- `breaker_pause_hours = 24`
- `max_effective_leverage = 2.0`
- `max_margin_utilization = 0.30`

After paper validation:

- optionally raise `risk_per_trade` to `0.5`

## Example: Starting Equity 3000 USD

If current equity is `3000 USD` and:

- `risk_per_trade = 0.25%`

Then risk budget per trade is:

- `7.5 USD`

If current equity later falls to `2400 USD`:

- risk budget becomes `6 USD`

If current equity rises to `4200 USD`:

- risk budget becomes `10.5 USD`

Signal logic does not change. Only size changes.

## Implementation Order

### Phase 1. Safety and semantics

- normalize `risk_per_trade`
- enforce explicit `USD`
- add startup validation for currency/risk settings

### Phase 2. Execution consistency

- bypass or remove spot-style `FXFundsGuard` for CFD mode
- add CFD-compatible margin guard
- keep `trade_row_not_created` hard abort

### Phase 3. Risk engine upgrade

- move portfolio/day-loss/day-count logic into risk verdict generation
- keep execution service focused on operational broker state

### Phase 4. Structural signal engine

- implement real swing continuation
- implement structural SL/TP/invalidation

### Phase 5. Observability

- separate reasons in UI:
  - signal blocked
  - decision blocked
  - risk blocked
  - execution blocked
  - broker cancelled

## Concrete File-Level Plan

### Must change

- `app/models/bot_settings.py`
- `app/risk/engine_v1.py`
- `app/execution/service.py`
- `app/broker/fx_funds_guard.py`
- `app/broker/oms.py`
- `app/pm/position_sizer.py`

### Should change

- `app/signals/engine_v1.py`
- `app/main.py`
- frontend funnel analytics for clearer end-state reasons

### Tests to add first

- risk percent semantics migration and validation
- USD account currency sizing contract
- CFD execution with funds guard disabled
- margin block path
- daily loss breaker path
- max trades per day path
- risk allow followed by operational block with explicit reason
- too-small-equity skip path

## Final Recommendation

The correct target model is:

- structural continuation strategy
- USD-only sizing
- equity-adaptive risk
- CFD execution
- no spot-style cash balance guard
- real risk verdict layer

`3000 USD` should influence the starting risk profile, but must not be hard-coded into strategy logic.
