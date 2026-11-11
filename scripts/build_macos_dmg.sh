#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "Usage: build_macos_dmg.sh <application.app> <output.dmg>" >&2
  exit 2
fi

APP_PATH="$1"
OUTPUT_PATH="$2"
VOLUME_NAME="DevAgent Orchestrator"

if [ ! -d "$APP_PATH" ] || [ "${APP_PATH##*.}" != "app" ]; then
  echo "A built .app bundle is required: $APP_PATH" >&2
  exit 2
fi
if [ "${OUTPUT_PATH##*.}" != "dmg" ]; then
  echo "DMG output path must end in .dmg: $OUTPUT_PATH" >&2
  exit 2
fi

OUTPUT_DIR="$(dirname -- "$OUTPUT_PATH")"
mkdir -p "$OUTPUT_DIR"
STAGING_DIR="$(mktemp -d "${TMPDIR:-/tmp}/devagent-dmg.XXXXXX")"
trap 'rm -rf "$STAGING_DIR"' EXIT HUP INT TERM

cp -R "$APP_PATH" "$STAGING_DIR/"
ln -s /Applications "$STAGING_DIR/Applications"
hdiutil create -volname "$VOLUME_NAME" -srcfolder "$STAGING_DIR" -format UDZO -ov "$OUTPUT_PATH"
test -s "$OUTPUT_PATH"
echo "Created DMG without mounting an intermediate image: $OUTPUT_PATH"
