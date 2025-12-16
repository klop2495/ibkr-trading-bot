#!/usr/bin/env bash
set -e
export PYTHONPATH=.
source .venv/bin/activate
python -m app.main
