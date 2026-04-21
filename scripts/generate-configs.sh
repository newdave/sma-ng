#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${CONFIG_DIR:-$ROOT_DIR/config}"
SETUP_DIR="${SETUP_DIR:-$ROOT_DIR/setup}"
LOCAL_INI="${LOCAL_INI:-$SETUP_DIR/.local.ini}"
POPULATE_SCRIPT="${POPULATE_SCRIPT:-$ROOT_DIR/scripts/populate-service-configs.py}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DETECT_GPU_SCRIPT="${DETECT_GPU_SCRIPT:-$ROOT_DIR/scripts/detect-gpu.sh}"

if [ "${GPU+x}" = "x" ]; then
  GPU="${GPU}"
else
  GPU="$("$DETECT_GPU_SCRIPT")"
fi

mkdir -p "$CONFIG_DIR"

create_ini() {
  local dest="$1" sample="$2" label="$3"
  if [ -f "$dest" ]; then
    echo "$dest already exists, skipping (delete it first to regenerate)"
    return
  fi

  cp "$sample" "$dest"
  if [ "$GPU" != "software" ]; then
    sed -i.bak "s/^gpu *=.*/gpu = $GPU/" "$dest" && rm -f "${dest}.bak"
    echo "Created $dest ($label, gpu = $GPU)"
  else
    echo "Created $dest ($label, software encoding)"
  fi
}

create_ini "$CONFIG_DIR/autoProcess.ini" "$SETUP_DIR/autoProcess.ini.sample" "regular quality"
create_ini "$CONFIG_DIR/autoProcess.rq.ini" "$SETUP_DIR/autoProcess.ini.sample" "regular quality"
create_ini "$CONFIG_DIR/autoProcess.lq.ini" "$SETUP_DIR/autoProcess.ini.sample-lq" "lower quality"

if [ ! -f "$CONFIG_DIR/daemon.json" ]; then
  cp "$SETUP_DIR/daemon.json.sample" "$CONFIG_DIR/daemon.json"
  echo "Created $CONFIG_DIR/daemon.json"
fi

if [ -f "$LOCAL_INI" ]; then
  echo "Populating per-service configs from $LOCAL_INI..."
  "$PYTHON_BIN" "$POPULATE_SCRIPT" "$LOCAL_INI" "$SETUP_DIR/autoProcess.ini.sample" --gpu "$GPU"
fi
