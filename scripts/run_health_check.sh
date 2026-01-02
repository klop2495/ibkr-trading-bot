#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=/app

python /app/scripts/check_broker_vs_db.py
