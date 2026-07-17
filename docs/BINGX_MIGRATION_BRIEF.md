# BingX Migration Brief

## Decision

The active target is BingX USDT-M perpetual futures in demo/VST mode.

IBKR is no longer the target broker because the account has been closed. The current repository remains valuable as an architecture source for typed decisions, risk controls, execution state, reconciliation and audit logs.

## Keep

- typed Pydantic models;
- append-only event history;
- order intent and broker report separation;
- protection lifecycle;
- reconciliation before trading;
- deterministic risk engine;
- structured logging and tests;
- API/dashboard patterns that do not depend on IBKR.

## Replace or isolate

- IB Gateway connection;
- IB contract models;
- IB order and execution status mapping;
- stock/session assumptions;
- IB-specific bracket order handling;
- IB account and position retrieval.

## New target behavior

- USDT-M perpetual futures;
- LONG and SHORT;
- isolated margin;
- one-way mode initially;
- leverage capped at 2x;
- exchange-side SL mandatory;
- reduce-only exits;
- funding and liquidation-risk awareness;
- demo/VST only;
- single optional GPT validator after deterministic strategy and before risk/execution.

## Codex entry point

Read `CODEX_TASK_BINGX_PERPETUAL.md` and implement Phase 0 first. Do not begin order placement until the read-only BingX demo adapter and its tests are complete.
