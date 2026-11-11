#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -q -r backend/requirements.txt
if [ ! -d dashboard/node_modules ]; then (cd dashboard && npm ci); fi
.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8765 &
API_PID=$!
cleanup() { kill "$API_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
cd dashboard
npm run dev -- --host 127.0.0.1
