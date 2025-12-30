# Operations Guide

## Pinned Dependencies

### Backend (requirements.txt)

```
pydantic>=2.7
python-dotenv>=1.0
supabase==2.14.0
httpx==0.28.1
postgrest==0.19.3
ib-insync>=0.9.86
```

**Почему зафиксированы версии:**
- `supabase==2.14.0` — совместимость с gotrue/httpx proxy API
- `httpx==0.28.1` — поддержка `proxy` параметра (требуется gotrue)
- `postgrest==0.19.3` — совместимость с supabase 2.14.x

**Обновление:** При обновлении supabase SDK убедитесь, что httpx/postgrest совместимы.

---

## Graceful Fallback для Supabase Env

### Поведение при отсутствии env переменных

Если `SUPABASE_URL` или `SUPABASE_SERVICE_ROLE_KEY` не установлены:

1. **Логируется причина в stderr:**
   ```
   [SupabaseDB] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set
   ```

2. **Создаётся stub-клиент** `_NullSupabaseClient`

3. **Приложение запускается** (для диагностики)

4. **При попытке использовать БД** выбрасывается `RuntimeError`:
   ```
   RuntimeError: Supabase client not configured: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set
   ```

### Проверка подключения

```python
from app.storage.db import SupabaseDB

db = SupabaseDB()
if db.disabled:
    print("Supabase not configured")
else:
    if db.ping():
        print("Connected")
    else:
        print("Connection failed")
```

---

## Hybrid Execution (Execution Strategy)

### Env Variables

- `EXECUTION_STRATEGY`: `rules` | `hybrid` | `llm` (default: uses `ACTIVE_STRATEGY` or `rules`)
- `HYBRID_THRESHOLD`: float (default `0.7`) — порог для `hybrid_score`
- `HYBRID_REQUIRE_ENTRY_TRIGGERED`: `0|1` (default `0`) — требовать ли `entry_triggered`
- `HYBRID_EXECUTION_THRESHOLD`: optional override for execution threshold

### Example

```bash
EXECUTION_STRATEGY=hybrid
HYBRID_THRESHOLD=0.7
HYBRID_REQUIRE_ENTRY_TRIGGERED=0
```

---

## EPERM Fix для Next.js Build

### Проблема

При повторных билдах Next.js может возникать ошибка:
```
EPERM: operation not permitted, open '.next/...'
```

### Решение

```bash
# Удалить кэш билда
rm -rf .next .next-local .next-work

# Пересобрать
npm run build
```

### Альтернатива (если rm не помогает)

```bash
# Восстановить права
chmod -R u+rw .next

# Или принудительно
sudo rm -rf .next
npm run build
```

### В CI/CD

В GitHub Actions это не воспроизводится — каждый билд чистый.

---

## Запуск тестов

### Backend

```bash
cd ibkr-trading-bot
source .venv/bin/activate
python -m pytest -q
# Ожидается: 130 passed
```

### Frontend

```bash
cd ibkr-trading-fronend
npm run lint
npm test
npm run build
# Ожидается: lint clean, 49 tests passed, build success
```

---

## CI/CD Pipeline

### Backend Workflow

```yaml
triggers: push/PR to main, master, develop
jobs:
  - Install Python 3.11
  - pip install requirements.txt + ruff + pytest
  - ruff check (non-blocking)
  - mypy check (non-blocking)
  - pytest tests/ (blocking)
  - compileall (blocking)
```

### Frontend Workflow

```yaml
triggers: push/PR to main, master, develop
jobs:
  - Install Node.js 20
  - npm ci
  - npm run lint (blocking)
  - tsc --noEmit (blocking)
  - npm test (blocking)
  - npm run build (blocking)
```

---

## Troubleshooting

### "Supabase client not configured"

**Причина:** Отсутствуют env переменные.

**Решение:**
```bash
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="your-service-role-key"
```

### "duplicate key value violates unique constraint"

**Причина:** Попытка вставить дубликат.

**Поведение:** Repos автоматически обрабатывают — возвращают существующий ID.

### Pydantic deprecation warnings

**Причина:** Сторонние пакеты (pyiceberg, storage3) используют устаревший API.

**Решение:** Игнорировать — исправится при обновлении пакетов.
