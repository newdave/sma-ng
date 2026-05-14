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

# ── reconcile /dev/dri device GIDs (root only) ────────────────────────────────
# Render nodes on the host can be owned by `render`/`video` groups whose GIDs
# don't match the ones baked into the image. Rather than asking the operator
# to set RENDER_GID/VIDEO_GID in .env, stat the actual device files and add
# the `ubuntu` runtime user to whatever groups own them. Falls through silently
# if no /dev/dri devices are mapped (NVENC / software-only deployments).
if [ "$(id -u)" = "0" ] && [ -d /dev/dri ]; then
    for dev in /dev/dri/card0 /dev/dri/card1 /dev/dri/renderD128 /dev/dri/renderD129; do
        [ -e "$dev" ] || continue
        gid="$(stat -c '%g' "$dev" 2>/dev/null || true)"
        [ -n "$gid" ] || continue
        gname="$(getent group "$gid" | cut -d: -f1)"
        if [ -z "$gname" ]; then
            gname="dri_${gid}"
            groupadd -g "$gid" "$gname" 2>/dev/null || true
        fi
        if ! id -nG ubuntu 2>/dev/null | tr ' ' '\n' | grep -qx "$gname"; then
            usermod -aG "$gname" ubuntu 2>/dev/null && \
                log "granted ubuntu access to $dev (group=$gname gid=$gid)"
        fi
    done
fi

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

FFMPEG="/usr/local/bin/ffmpeg"
FFPROBE="/usr/local/bin/ffprobe"

CONFIG="$CONFIG_DIR/sma-ng.yml"

# sed -i is not POSIX; use a temp file for portability across BusyBox/GNU.
# The schema-generated sample puts ffmpeg/ffprobe under base.converter, i.e.
# four spaces of indentation. Only patch lines that still hold the bare
# default — user-defined absolute paths are left alone.
_patch_yaml() {
    key="$1"; val="$2"
    tmp="${CONFIG}.patching"
    sed "s|^    ${key}: ffmpeg\$|    ${key}: ${val}|; \
         s|^    ${key}: ffprobe\$|    ${key}: ${val}|" \
        "$CONFIG" > "$tmp" && mv "$tmp" "$CONFIG"
}

_patch_yaml "ffmpeg"  "$FFMPEG"
_patch_yaml "ffprobe" "$FFPROBE"

log "FFmpeg: $FFMPEG  FFprobe: $FFPROBE"

# ── GPU auto-detection (first-run only; preserves user edits on restart) ──────
if [ "$_config_is_new" = "true" ]; then
    GPU="$(/app/scripts/detect-gpu.sh 2>/dev/null || echo software)"
    tmp="${CONFIG}.patching"
    sed "s|^    gpu:.*|    gpu: ${GPU}|" "$CONFIG" > "$tmp" && mv "$tmp" "$CONFIG"
    log "GPU: ${GPU} (auto-detected)"
fi

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
# When started as root (so we could fix up /dev/dri GIDs above), drop to the
# unprivileged `ubuntu` user with --init-groups so the supplementary groups
# we just added (render/video/dri_*) take effect.
if [ "$(id -u)" = "0" ]; then
    chown -R ubuntu:ubuntu "$CONFIG_DIR" "$DEFAULTS_DIR" 2>/dev/null || true
    exec setpriv --reuid=ubuntu --regid=ubuntu --init-groups -- "$@"
fi

exec "$@"
