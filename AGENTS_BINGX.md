# Agent instructions: BingX perpetual demo migration

These instructions apply to work on branch `codex/bingx-perpetual-demo`.

1. Read `CODEX_TASK_BINGX_PERPETUAL.md` completely before changing code.
2. Implement Phase 0 first and create `docs/BINGX_MIGRATION_MAP.md`.
3. Preserve existing IBKR functionality behind interfaces; do not delete it during the first migration phases.
4. Use only official current BingX API documentation for endpoint details.
5. The executable BingX adapter must support demo/VST only.
6. Never commit credentials, account identifiers, IP addresses or signed requests.
7. Use Pydantic models inside the pipeline and `Decimal` for financial calculations.
8. A position without confirmed exchange-side protection is an emergency condition.
9. GPT is optional, disabled by default, and can only BLOCK or REDUCE a deterministic candidate.
10. Run and report tests after every phase. Do not describe untested code as working.
