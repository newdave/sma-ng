#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${CONFIG_DIR:-$ROOT_DIR/config}"
SETUP_DIR="${SETUP_DIR:-$ROOT_DIR/setup}"
DETECT_GPU_SCRIPT="${DETECT_GPU_SCRIPT:-$ROOT_DIR/scripts/detect-gpu.sh}"

if [ -z "${GPU+x}" ]; then
  GPU="$("$DETECT_GPU_SCRIPT")"
fi

mkdir -p "$CONFIG_DIR"

create_yaml() {
  local dest="$1" sample="$2" label="$3"
  if [ -f "$dest" ]; then
    echo "$dest already exists, skipping (delete it first to regenerate)"
    return
  fi

  cp "$sample" "$dest"
  if [ "$GPU" != "software" ]; then
    sed -i.bak "s/^  gpu:.*/  gpu: $GPU/" "$dest" && rm -f "${dest}.bak"
    echo "Created $dest ($label, gpu: $GPU)"
  else
    echo "Created $dest ($label, software encoding)"
  fi
}

create_yaml "$CONFIG_DIR/sma-ng.yml" "$SETUP_DIR/sma-ng.yml.sample" "base config with profiles"
