#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

case "$(uname -s):$(uname -m)" in
  Darwin:arm64) TARGET="aarch64-apple-darwin" ;;
  Darwin:x86_64) TARGET="x86_64-apple-darwin" ;;
  *) echo "This build script supports macOS arm64 and x86_64 hosts." >&2; exit 2 ;;
esac

PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
NUITKA_JOBS="${NUITKA_BUILD_JOBS:-2}"
case "$NUITKA_JOBS" in
  ''|*[!0-9]*) echo "NUITKA_BUILD_JOBS must be a positive integer." >&2; exit 2 ;;
  0) echo "NUITKA_BUILD_JOBS must be at least 1." >&2; exit 2 ;;
esac
if [ ! -x "$PYTHON" ]; then
  if [ "$PYTHON" = "$ROOT/.venv/bin/python" ]; then
    python3 -m venv "$ROOT/.venv"
  else
    echo "Python environment not found at $PYTHON; set PYTHON to an installed interpreter." >&2
    exit 2
  fi
fi

"$PYTHON" -m pip install -q -r backend/requirements.txt -r backend/requirements-desktop.txt
OUT_DIR="$ROOT/dashboard/src-tauri/binaries"
BUILD_DIR="$ROOT/.desktop-build/backend"
mkdir -p "$OUT_DIR" "$BUILD_DIR"
"$PYTHON" -m nuitka \
  --mode=onefile \
  --jobs="$NUITKA_JOBS" \
  --assume-yes-for-downloads \
  --output-dir="$BUILD_DIR" \
  --output-filename="devagent-backend-$TARGET" \
  --include-data-file="$ROOT/docs/PROJECT_CONVENTIONS.md=docs/PROJECT_CONVENTIONS.md" \
  backend/desktop_entry.py
cp "$BUILD_DIR/devagent-backend-$TARGET" "$OUT_DIR/devagent-backend-$TARGET"
chmod 755 "$OUT_DIR/devagent-backend-$TARGET"
echo "Built backend sidecar for $TARGET: $OUT_DIR/devagent-backend-$TARGET"
