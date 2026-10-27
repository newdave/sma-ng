#!/usr/bin/env bash
# SMA-NG Deluge Post-Processing Script
#
# Submits conversion jobs to the SMA-NG daemon webhook on torrent completion.
#
# Configure in Deluge: Preferences → Execute → Event: TorrentComplete
#   Command: /path/to/delugePostProcess.sh "%T" "%N" "%L" "%I"
#   Where: %T = torrent name, %N = download path, %L = label, %I = info hash
#
# Configure daemon connection via environment variables:
#   DAEMON_HOST     Daemon host (default: 127.0.0.1)
#   DAEMON_PORT     Daemon port (default: 8585)
#   DAEMON_API_KEY  API key if authentication is enabled
#   BYPASS_LABELS   Comma-separated label prefixes to skip (default: bypass)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../lib/common.sh
. "${SCRIPT_DIR}/../lib/common.sh"

DAEMON_HOST_VALUE="${DAEMON_HOST:-127.0.0.1}"
DAEMON_PORT_VALUE="${DAEMON_PORT:-8585}"
DAEMON_BASE="http://${DAEMON_HOST_VALUE}:${DAEMON_PORT_VALUE}"
BYPASS_LABELS="${BYPASS_LABELS:-bypass}"
sma_init_daemon

# ── helpers ───────────────────────────────────────────────────────────────────

log()  { echo "[deluge] $*" >&2; }
info() { log "INFO: $*"; }
err()  { log "ERROR: $*"; }

is_bypassed() {
    local label="$1"
    local label_lower="${label,,}"
    IFS=',' read -ra bypasses <<< "$BYPASS_LABELS"
    for b in "${bypasses[@]}"; do
        b="${b,,}"
        b="${b#"${b%%[![:space:]]*}"}"
        b="${b%"${b##*[![:space:]]}"}"
        if [[ -n "$b" && "$label_lower" == "${b}"* ]]; then
            return 0
        fi
    done
    return 1
}

# ── argument parsing ──────────────────────────────────────────────────────────

info "Deluge post-processing started."

if [[ $# -lt 3 ]]; then
    err "Not enough arguments. Usage: delugePostProcess.sh <torrent_name> <path> <label> [info_hash]"
    exit 1
fi

TORRENT_NAME="$1"
PATH_ARG="$2"
LABEL="${3:-}"
INFO_HASH="${4:-}"

info "Torrent: ${TORRENT_NAME}"
info "Path:    ${PATH_ARG}"
info "Label:   ${LABEL}"
info "Hash:    ${INFO_HASH:-n/a}"

# ── bypass check ──────────────────────────────────────────────────────────────

if is_bypassed "$LABEL"; then
    info "Bypass label matched, skipping conversion."
    exit 0
fi

# ── submit path ───────────────────────────────────────────────────────────────

# Try the path directly; if nothing found, try path/torrent_name
read -r COUNT FAILED <<< "$(sma_submit_path "$PATH_ARG")"

if [[ "$COUNT" -eq 0 && "$FAILED" -eq 0 ]]; then
    COMBINED="${PATH_ARG%/}/${TORRENT_NAME}"
    read -r COUNT FAILED <<< "$(sma_submit_path "$COMBINED")"
fi

if [[ "$FAILED" -gt 0 ]]; then
    err "${FAILED} file(s) failed to submit to daemon at ${DAEMON_BASE} (${COUNT} submitted)."
    exit 1
fi

if [[ "$COUNT" -eq 0 ]]; then
    err "No media files submitted. Path does not exist or has no media: ${PATH_ARG}"
    exit 1
fi

info "Submitted ${COUNT} job(s) to daemon."
