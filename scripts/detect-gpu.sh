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

# Intel QSV: vainfo must succeed and confirm an Intel driver is active.
# Checking /sys/module/i915 alone is unreliable on KVM hosts where i915 is
# loaded for management but only bochs virtual video is exposed to the guest.
if command -v vainfo >/dev/null 2>&1 && vainfo 2>&1 | grep -qi intel; then
  echo qsv
  exit 0
fi

# Generic VAAPI (AMD, older Intel i965): render node accessible and vainfo passes.
if [ -e /dev/dri/renderD128 ] && command -v vainfo >/dev/null 2>&1 && vainfo >/dev/null 2>&1; then
  echo vaapi
  exit 0
fi

echo software
