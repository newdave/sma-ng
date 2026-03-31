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

seed_file "$SETUP_DIR/autoProcess.ini.sample" "$CONFIG_DIR/autoProcess.ini"
seed_file "$SETUP_DIR/daemon.json.sample"     "$CONFIG_DIR/daemon.json"
seed_file "$SETUP_DIR/daemon.env.sample"      "$CONFIG_DIR/daemon.env"
seed_file "$SETUP_DIR/custom.py.sample"       "$CONFIG_DIR/custom.py"

# ── always refresh defaults/ with the latest shipped samples ──────────────────
# These are read-only reference copies — users should never edit them.

cp "$SETUP_DIR/autoProcess.ini.sample" "$DEFAULTS_DIR/autoProcess.ini.sample"
cp "$SETUP_DIR/daemon.json.sample"     "$DEFAULTS_DIR/daemon.json.sample"
cp "$SETUP_DIR/daemon.env.sample"      "$DEFAULTS_DIR/daemon.env.sample"
cp "$SETUP_DIR/custom.py.sample"       "$DEFAULTS_DIR/custom.py.sample"

log "defaults/ refreshed with latest samples"

# ── patch autoProcess.ini with discovered ffmpeg paths ────────────────────────
# Only patch lines that still hold the bare default ("ffmpeg" / "ffprobe").
# User-defined absolute paths are left alone.

FFMPEG="${SMA_FFMPEG:-/usr/local/bin/ffmpeg}"
FFPROBE="${SMA_FFPROBE:-/usr/local/bin/ffprobe}"

INI="$CONFIG_DIR/autoProcess.ini"

# sed -i is not POSIX; use a temp file for portability across BusyBox/GNU
_patch_ini() {
    key="$1"; val="$2"
    tmp="${INI}.patching"
    sed "s|^${key} = ffmpeg\$|${key} = ${val}|; \
         s|^${key} = ffprobe\$|${key} = ${val}|" \
        "$INI" > "$tmp" && mv "$tmp" "$INI"
}

_patch_ini "ffmpeg"  "$FFMPEG"
_patch_ini "ffprobe" "$FFPROBE"

log "FFmpeg: $FFMPEG  FFprobe: $FFPROBE"
log "Config directory ready: $CONFIG_DIR"

# ── hand off to the container CMD ─────────────────────────────────────────────
exec "$@"
