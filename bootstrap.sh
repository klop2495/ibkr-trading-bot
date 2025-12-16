#!/usr/bin/env bash
set -e

mkdir -p app/{models,storage,data,features,signals,risk,decision,execution,recon,health}
mkdir -p migrations tests

touch app/__init__.py
touch app/models/__init__.py
touch app/storage/__init__.py
touch app/data/__init__.py
touch app/features/__init__.py
touch app/signals/__init__.py
touch app/risk/__init__.py
touch app/decision/__init__.py
touch app/execution/__init__.py
touch app/recon/__init__.py
touch app/health/__init__.py

cat > app/main.py <<'PY'
def main():
    print("agent-fx-bot skeleton: OK")

if __name__ == "__main__":
    main()
PY

cat > app/config.py <<'PY'
# Configuration placeholder
PY

cat > app/logging.py <<'PY'
# Logging placeholder
PY

cat > requirements.txt <<'TXT'
pydantic>=2.7
python-dotenv>=1.0
supabase>=2.6.0
ib-insync>=0.9.86
TXT

cat > .env.example <<'ENV'
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
BOT_MODE=paper
TRADING_ENABLED=false
ENV

cat > README.md <<'MD'
# agent-fx-bot

Skeleton structure for IBKR trading bot + Supabase backend.
MD

echo "Skeleton created successfully."

