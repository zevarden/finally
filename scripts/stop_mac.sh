#!/usr/bin/env bash
# Stop and remove the FinAlly container (macOS/Linux). Idempotent.
# The 'finally-data' volume is kept, so your portfolio persists.
set -euo pipefail

CONTAINER="finally"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not on PATH." >&2
  exit 1
fi

if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "Stopping and removing container '$CONTAINER'..."
  docker rm -f "$CONTAINER" >/dev/null
  echo "Stopped. Data volume 'finally-data' was kept."
else
  echo "Container '$CONTAINER' is not running. Nothing to do."
fi
