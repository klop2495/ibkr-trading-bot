# Reconciliation Workflow

## Source of truth
IBKR positions/fills are the ultimate truth.

## Schedule
- Periodic: every RECON_INTERVAL_SEC (default 5s)
- Also: on-demand after critical execution events (e.g., partial fill, reject)

## Outputs
Reconciliation produces a `ReconMismatchEvent` when local vs broker state diverge.

## Reactions
- MINOR mismatch:
  - log risk_event (warning)
  - request recon refresh / corrective checks
- CRITICAL mismatch:
  - freeze trading for symbol (or portfolio)
  - initiate immediate close (fail-safe)
  - log risk_event (critical)
