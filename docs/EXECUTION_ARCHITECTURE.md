# Execution Architecture

## Scope
Execution contour for Forex auto-trading, excluding forecast generation logic.

## Lifecycle
1. Signal/decision layer produces a trade intent.
2. Risk/execution gates decide whether the intent may be executed.
3. ExecutionService/OMS submits the order to IBKR.
4. `trades_history` stores the trade lifecycle.
5. Broker sync/reconciliation aligns local state with IBKR state.
6. Admin surfaces expose executions, broker state, and manual close controls.

## Core Components

### Signal-to-order
- `app/main.py`
- `app/execution/service.py`
- `app/broker/oms.py`
- `app/broker/fx_funds_guard.py`

Responsibilities:
- transform trade intent into executable order parameters
- enforce execution pre-checks
- prevent duplicate or conflicting trades
- block execution on funds/risk/broker constraints

### Order placement
- `app/execution/service.py`
- `app/broker/oms.py`

Responsibilities:
- place orders with dedicated IB clientId
- persist initial trade rows
- transition trade rows through terminal states
- log execution-related events

### Broker sync / reconciliation
- `app/broker/state_service.py`
- `app/dashboard.py` reconciliation endpoints

Responsibilities:
- compare broker positions/orders to local DB
- recover from restart / partial state loss
- determine close reason when possible
- mark broker-flat local trades as closed

### Persistence
- `trades_history`
- `control_broker_requests`
- `risk_events`

### Admin control surface
- frontend `/admin/executions`
- frontend `/api/admin/executions`
- frontend `/api/admin/executions/close`
- backend `/api/broker`
- backend `/api/broker/close`

## Source Of Truth Matrix

### Open/closed trade state
- primary: `trades_history`
- authority override: broker reconciliation when IBKR state contradicts local DB

### Live bracket presence (SL/TP active)
- primary: broker open orders

### Current open-trade price for admin UI
- primary: `market_snapshots` price ladder
- preferred order: `S5 -> M1 -> M15`

### Manual close action
- trigger: frontend `/api/admin/executions/close`
- authority: backend `/api/broker/close`
- audit trail: `risk_events` (`MANUAL_CLOSE_REQUEST`, `MANUAL_CLOSE_SUCCESS`)

## Execution API Principles
- admin routes must require authenticated owner session
- state-changing routes must verify same-origin browser request
- frontend routes should remain thin orchestration shells
- broker actions must be auditable

## Current Hardening Baseline
- `/api/admin/executions` requires authenticated owner session
- `/api/admin/executions/close` requires authenticated owner session
- manual close route checks request origin
- manual close actor is forwarded to backend and logged
- open-trade current price uses freshest available snapshot source with freshness metadata

## Planned Next Steps
1. formalize execution state machine and stale-order policy
2. split executions API into smaller endpoints (`list`, `stats`, `live-open`, `close`)
3. normalize `trades_history` with completion/integrity metadata
4. add execution health metrics and dashboards
