# Event Priorities & Sequencing

## Priority order (lower number = higher priority)
1. SAFETY: SL rejected / protection invalid / critical recon mismatch
2. RECON: reconciliation mismatch detection & corrective workflows
3. EXECUTION_FEEDBACK: fills / partial fills / rejects
4. RISK_GATES: trade_allowed=false, circuit breakers, data quality fail
5. NEW_DECISION: new decision intent
6. MARKET_DATA: bars/snapshots

## Mutex rule
Processing is serialized per symbol using a mutex. High-priority events preempt normal flow via the priority queue.

## Backpressure policy
For market data: keep only the latest event per (symbol, timeframe). Older market data is dropped ("drop old / keep last").
