# Execution Failure Modes

## Purpose
Define the main failure classes in the execution contour and the expected handling strategy.

## Failure Classes

### 1. Pre-check block
Examples:
- insufficient funds
- duplicate/open position conflict
- execution gate block
- broker unavailable

Expected handling:
- do not submit order
- persist blocked reason
- emit risk/execution event

### 2. Broker submit failure
Examples:
- IBKR request rejected
- network/API error during submit
- invalid contract/order params

Expected handling:
- no silent pending rows
- trade should end in deterministic failure state
- log rejection/error with broker context

### 3. Stale pending/submitted state
Examples:
- local trade row remains pending without broker acknowledgment
- bracket leg missing for too long

Expected handling:
- watchdog/escalation policy
- transition to terminal or investigated state
- visible in admin health panel

### 4. Broker/DB divergence
Examples:
- DB says OPEN, broker flat
- broker has position, DB missing trade
- orphan order / orphan position

Expected handling:
- reconciliation detects mismatch
- local row is healed or closed
- divergence is logged for audit

### 5. Partial recovery after restart
Examples:
- ib_order_id mapping lost
- fill appears after process restart
- trade restored from broker state

Expected handling:
- recover linkages from broker data and local history
- mark recovered state explicitly when needed

### 6. Manual operator intervention
Examples:
- manual close from admin UI

Expected handling:
- require authenticated owner
- require same-origin request
- log actor, trade_id, symbol, mode, result

### 7. Monitoring freshness degradation
Examples:
- no `S5` available
- fallback to `M1` or `M15`
- stale prices on executions page

Expected handling:
- expose `price_source`, `price_ts`, `price_age_sec`
- keep UI explicit about data freshness

## Priority Matrix

### P0
- unauthorized admin read/write access
- unaudited manual close

### P1
- stale pending/submitted trades
- ambiguous terminal states
- broker/DB divergence without explicit recovery trace

### P2
- stale current price / unrealized PnL on admin executions page

## Operational Signals To Add
- stale_open_orders
- stale_submitted_trades
- broker_db_mismatches
- sync_lag_seconds
- rejected_ratio
- manual_intervention_count
