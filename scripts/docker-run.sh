#!/usr/bin/env bash
set -euo pipefail

TAG="${TAG:-sma-ng:local}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Detect GPU and build appropriate docker flags.
# Override by setting GPU=nvenc|qsv|vaapi|software before calling this script.
GPU="${GPU:-$("$ROOT_DIR/scripts/detect-gpu.sh" 2>/dev/null || echo software)}"
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

mkdir -p config logs data
# shellcheck disable=SC2086
docker run --rm \
  -p 8585:8585 \
  -v "$(pwd)/config:/config" \
  -v "$(pwd)/logs:/logs" \
  -v "$(pwd)/data:/data" \
  ${GPU_FLAGS} \
  "$TAG"
