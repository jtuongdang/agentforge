#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
.venv/bin/python -m pytest backend/tests -q
(cd dashboard && npm run build)
