#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -q -r backend/requirements.txt
if [ ! -d dashboard/node_modules ]; then (cd dashboard && npm ci); fi
API_HOST="${CLINIC_ORCHESTRATOR_HOST:-127.0.0.1}"
API_PORT="${CLINIC_ORCHESTRATOR_PORT:-8765}"
API_TARGET_HOST="$API_HOST"
case "$API_HOST" in *:*) API_TARGET_HOST="[$API_HOST]" ;; esac
export VITE_API_PROXY_TARGET="http://${API_TARGET_HOST}:${API_PORT}"
.venv/bin/uvicorn app.main:app --app-dir backend --host "$API_HOST" --port "$API_PORT" &
API_PID=$!
cleanup() { kill "$API_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
cd dashboard
npm run dev -- --host 127.0.0.1
