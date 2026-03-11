# agent-fx-bot

Skeleton structure for IBKR trading bot + Supabase backend.

## Engineering rules (pipeline invariants)

- **Inside the pipeline (signals/agents/decision/risk/execution/recon/pm): only Pydantic models.**
- **At boundaries (storage/network/broker adapters): dict/json is allowed** for serialization/deserialization only.
- Event sequencing: `docs/event-priorities.md`
- Reconciliation workflow: `docs/recon-workflow.md`
- Append-only logs enforced by DB triggers (see `migrations/003_append_only_triggers.sql`).

---

## Quick Start

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your Supabase credentials

# Test
python3 -m pytest -q
```

---

## Dependencies

Pinned versions для стабильности:

```
supabase==2.14.0
httpx==0.28.1
postgrest==0.19.3
```

При обновлении supabase SDK проверьте совместимость httpx/postgrest.

---

## Graceful Fallback

При отсутствии `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`:

1. Приложение запускается (для диагностики)
2. Логируется причина в stderr
3. Операции с БД выбрасывают `RuntimeError`

См. `OPS.md` для деталей.

---

## CI/CD

GitHub Actions workflow: `.github/workflows/ci.yml`

- Python 3.11
- Lint (ruff)
- Type check (mypy)
- Tests (pytest)

---

## Documentation

- `CHANGELOG.md` — история изменений
- `OPS.md` — операционная документация
- `docs/` — архитектурная документация
