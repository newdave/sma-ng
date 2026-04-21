#!/usr/bin/env bash
set -euo pipefail

TAG="${TAG:-sma-ng:local}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -z "${SMA_DAEMON_DB_URL:-}" ]; then
  echo "Error: SMA_DAEMON_DB_URL is required (no SQLite fallback)" >&2
  echo "  Example: SMA_DAEMON_DB_URL=postgresql://sma:pass@host:5432/sma make docker-run" >&2
  exit 1
fi

# Detect GPU and build appropriate docker flags.
# Override by setting SMA_GPU=nvenc|qsv|vaapi|software before calling this script.
GPU="${SMA_GPU:-$("$ROOT_DIR/scripts/detect-gpu.sh" 2>/dev/null || echo software)}"
GPU_FLAGS=""
case "$GPU" in
  nvenc)
    GPU_FLAGS="--gpus all"
    ;;
  qsv|vaapi)
    GPU_FLAGS="--device /dev/dri"
    ;;
esac
echo "GPU: ${GPU}${GPU_FLAGS:+ ($GPU_FLAGS)}"

mkdir -p config logs
# shellcheck disable=SC2086
docker run --rm \
  -p 8585:8585 \
  -e SMA_DAEMON_DB_URL="${SMA_DAEMON_DB_URL}" \
  -e SMA_GPU="${GPU}" \
  -v "$(pwd)/config:/config" \
  -v "$(pwd)/logs:/logs" \
  ${GPU_FLAGS} \
  "$TAG"
