#!/usr/bin/env bash
# Start the FinAlly container (macOS/Linux). Idempotent: safe to run repeatedly.
#
# Usage: scripts/start_mac.sh [--build] [--no-open]
#   --build    force a rebuild of the Docker image
#   --no-open  don't open the browser
set -euo pipefail

IMAGE="finally"
CONTAINER="finally"
VOLUME="finally-data"
PORT="${FINALLY_PORT:-8000}"
URL="http://localhost:${PORT}"
# Health probe uses 127.0.0.1: "localhost" may resolve to ::1 first, which can hang on Docker Desktop.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BUILD=0
OPEN=1
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=1 ;;
    --no-open) OPEN=0 ;;
    -h|--help)
      sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not on PATH." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Error: the Docker daemon is not running. Start Docker and try again." >&2
  exit 1
fi

if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    echo "Warning: .env not found; creating it from .env.example."
    echo "         Edit .env to add your LITELLM_MASTER_KEY for AI chat."
    cp .env.example .env
  else
    echo "Error: neither .env nor .env.example exists in $ROOT_DIR." >&2
    exit 1
  fi
fi

if [ "$BUILD" -eq 1 ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Building Docker image '$IMAGE'..."
  docker build -t "$IMAGE" .
fi

if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "Removing existing container '$CONTAINER'..."
  docker rm -f "$CONTAINER" >/dev/null
fi

echo "Starting container '$CONTAINER'..."
docker run -d \
  --name "$CONTAINER" \
  -v "$VOLUME:/app/db" \
  --add-host=host.docker.internal:host-gateway \
  -p "$PORT:8000" \
  --env-file .env \
  "$IMAGE" >/dev/null

# Wait (up to ~30s) for the health endpoint
printf "Waiting for FinAlly to become ready"
ready=0
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  printf "."
  sleep 1
done
echo

if [ "$ready" -eq 1 ]; then
  echo "FinAlly is running at $URL"
else
  echo "FinAlly is starting at $URL (not healthy yet; check: docker logs $CONTAINER)"
fi

if [ "$OPEN" -eq 1 ]; then
  if command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  fi
fi
