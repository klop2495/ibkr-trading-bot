#!/usr/bin/env bash
# State capture helper for control-plane chain (previews -> decisions -> verdicts)
# Usage:
#   SUPABASE_PGURI="postgresql://..." ./scripts/state_capture_backend.sh
# If SUPABASE_PGURI is not provided, the script will print the SQL so you can run it manually

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SQL_FILE="$ROOT/scripts/sql/state_capture.sql"

if [[ ! -f "$SQL_FILE" ]]; then
  echo "SQL file not found: $SQL_FILE" >&2
  exit 1
fi

if [[ -z "${SUPABASE_PGURI:-}" ]]; then
  echo "SUPABASE_PGURI is not set. Please run the queries below in Supabase SQL editor or psql:" >&2
  echo "------------------------------------------------------------------"
  cat "$SQL_FILE"
  exit 0
fi

psql "$SUPABASE_PGURI" -f "$SQL_FILE"
