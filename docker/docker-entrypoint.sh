#!/bin/sh
# docker-entrypoint.sh — seed /app/config on first run, then exec the daemon.
#
# Behaviour
# ─────────
#  • Each sample file in /app/setup/ is copied to CONFIG_DIR only if the
#    destination does not already exist (first-run seeding; user edits are
#    never overwritten).
#  • The current samples are always written to CONFIG_DIR/defaults/ so users
#    can diff their running config against the latest shipped defaults.
#  • All output goes to stderr so it doesn't pollute structured log pipelines.
#
# Environment
# ───────────
#  CONFIG_DIR   Directory to seed (default: /config)
#  SMA_FFMPEG   Path to ffmpeg binary (default: /usr/local/bin/ffmpeg)
#  SMA_FFPROBE  Path to ffprobe binary (default: /usr/local/bin/ffprobe)
#  SMA_GPU      Force GPU backend: nvenc | qsv | vaapi | software
#               (default: auto-detected on first run via detect-gpu.sh)
set -e

CONFIG_DIR="${CONFIG_DIR:-/config}"
SETUP_DIR="/app/setup"
DEFAULTS_DIR="${CONFIG_DIR}/defaults"

# ── helpers ────────────────────────────────────────────────────────────────────

log() { printf '[entrypoint] %s\n' "$*" >&2; }

# Copy $1 to $2 if $2 does not exist yet.
seed_file() {
    src="$1"
    dst="$2"
    if [ ! -f "$dst" ]; then
        cp "$src" "$dst"
        log "Seeded $(basename "$dst") (first run)"
    fi
}

# ── ensure directories exist ───────────────────────────────────────────────────

mkdir -p "$CONFIG_DIR" "$DEFAULTS_DIR"

# ── seed user config files (skipped if already present) ───────────────────────

_config_is_new=false
[ ! -f "$CONFIG_DIR/sma-ng.yml" ] && _config_is_new=true
seed_file "$SETUP_DIR/sma-ng.yml.sample" "$CONFIG_DIR/sma-ng.yml"
seed_file "$SETUP_DIR/daemon.env.sample"      "$CONFIG_DIR/daemon.env"
seed_file "$SETUP_DIR/custom.py.sample"       "$CONFIG_DIR/custom.py"

# ── always refresh defaults/ with the latest shipped samples ──────────────────
# These are read-only reference copies — users should never edit them.

cp "$SETUP_DIR/sma-ng.yml.sample" "$DEFAULTS_DIR/sma-ng.yml.sample"
cp "$SETUP_DIR/daemon.env.sample"      "$DEFAULTS_DIR/daemon.env.sample"
cp "$SETUP_DIR/custom.py.sample"       "$DEFAULTS_DIR/custom.py.sample"

log "defaults/ refreshed with latest samples"

# ── patch sma-ng.yml with discovered ffmpeg paths ───────────────────────
# Only patch lines that still hold the bare default ("ffmpeg" / "ffprobe").
# User-defined absolute paths are left alone.

FFMPEG="${SMA_FFMPEG:-/usr/local/bin/ffmpeg}"
FFPROBE="${SMA_FFPROBE:-/usr/local/bin/ffprobe}"

CONFIG="$CONFIG_DIR/sma-ng.yml"

# sed -i is not POSIX; use a temp file for portability across BusyBox/GNU
_patch_yaml() {
    key="$1"; val="$2"
    tmp="${CONFIG}.patching"
    sed "s|^  ${key}: ffmpeg\$|  ${key}: ${val}|; \
         s|^  ${key}: ffprobe\$|  ${key}: ${val}|" \
        "$CONFIG" > "$tmp" && mv "$tmp" "$CONFIG"
}

_patch_yaml "ffmpeg"  "$FFMPEG"
_patch_yaml "ffprobe" "$FFPROBE"

log "FFmpeg: $FFMPEG  FFprobe: $FFPROBE"

# ── GPU auto-detection (first-run only; preserves user edits on restart) ──────
# SMA_GPU overrides detection at any time; detect-gpu.sh runs on first run only.
if [ -n "${SMA_GPU:-}" ]; then
    GPU="$SMA_GPU"
    tmp="${CONFIG}.patching"
    sed "s|^  gpu:.*|  gpu: ${GPU}|" "$CONFIG" > "$tmp" && mv "$tmp" "$CONFIG"
    log "GPU: ${GPU} (from SMA_GPU)"
elif [ "$_config_is_new" = "true" ]; then
    GPU="$(/app/scripts/detect-gpu.sh 2>/dev/null || echo software)"
    tmp="${CONFIG}.patching"
    sed "s|^  gpu:.*|  gpu: ${GPU}|" "$CONFIG" > "$tmp" && mv "$tmp" "$CONFIG"
    log "GPU: ${GPU} (auto-detected)"
fi

export SMA_CONFIG="${SMA_CONFIG:-$CONFIG}"

log "Config directory ready: $CONFIG_DIR"

# ── set LIBVA_DRIVER_NAME for Intel VAAPI/QSV ────────────────────────────────
# Must be set before detect-gpu.sh calls vainfo so the iHD driver is selected.
# The Intel compose profiles mount /dev/dri so guests with SR-IOV VFs exposed as
# card1/renderD128 still present the full DRI topology inside the container.
if [ -z "${LIBVA_DRIVER_NAME:-}" ] && [ -d /sys/module/i915 ]; then
    export LIBVA_DRIVER_NAME=iHD
    log "LIBVA_DRIVER_NAME=iHD (Intel GPU detected)"
fi

# ── hand off to the container CMD ─────────────────────────────────────────────
exec "$@"
