# agent-fx-bot

Skeleton structure for IBKR trading bot + Supabase backend.

## Engineering rules (pipeline invariants)

- **Inside the pipeline (signals/agents/decision/risk/execution/recon/pm): only Pydantic models.**
- **At boundaries (storage/network/broker adapters): dict/json is allowed** for serialization/deserialization only.
- Event sequencing: `docs/event-priorities.md`
- Reconciliation workflow: `docs/recon-workflow.md`
- Append-only logs enforced by DB triggers (see `migrations/002_append_only_triggers.sql`).
