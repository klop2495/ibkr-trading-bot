#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

REQUIRED_VARS=(SUPABASE_URL SUPABASE_SERVICE_ROLE_KEY BOT_OWNER_USER_ID)
MISSING=0
for var in "${REQUIRED_VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: $var is not set" >&2
    MISSING=1
  fi
done

if [[ "$MISSING" -ne 0 ]]; then
  exit 1
fi

echo "Starting bot control-plane..."
echo "SUPABASE_URL: set"
echo "BOT_OWNER_USER_ID: set"
if [[ -n "${BOT_SETTINGS_POLL_SECONDS:-}" ]]; then
  echo "BOT_SETTINGS_POLL_SECONDS: ${BOT_SETTINGS_POLL_SECONDS}"
fi

exec python -m app.main
