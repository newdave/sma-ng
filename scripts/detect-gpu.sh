#!/usr/bin/env bash
set -euo pipefail

if [ "$(uname)" = "Darwin" ]; then
  if sysctl -n machdep.cpu.brand_string 2>/dev/null | grep -qi apple; then
    echo videotoolbox
  else
    echo software
  fi
  exit 0
fi

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  echo nvenc
  exit 0
fi

if [ -d /sys/module/i915 ] || (command -v vainfo >/dev/null 2>&1 && vainfo 2>&1 | grep -qi intel); then
  echo qsv
  exit 0
fi

if [ -e /dev/dri/renderD128 ] && command -v vainfo >/dev/null 2>&1 && vainfo >/dev/null 2>&1; then
  echo vaapi
  exit 0
fi

echo software
