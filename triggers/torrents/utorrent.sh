#!/usr/bin/env bash
# SMA-NG uTorrent Post-Processing Script
#
# Submits conversion jobs to the SMA-NG daemon webhook on torrent completion.
#
# Configure in uTorrent: Preferences → Advanced → Run program → On torrent completion:
#   /path/to/uTorrentPostProcess.sh "%L" "%T" "%D" "%K" "%F" "%I" "%N"
#   Where: %L = label, %T = tracker, %D = directory, %K = single|multi,
#          %F = filename (single-file torrents), %I = info hash, %N = torrent name
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

log()  { echo "[utorrent] $*" >&2; }
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
# Expected: label tracker directory kind filename info_hash [name]

info "uTorrent post-processing started."

if [[ $# -lt 6 ]]; then
    err "Not enough arguments. Expected: label tracker directory kind filename info_hash [name]"
    exit 1
fi

LABEL="$1"
# $2 = tracker (unused)
DIRECTORY="$3"
KIND="${4,,}"
FILENAME="$5"
INFO_HASH="$6"
TORRENT_NAME="${7:-$INFO_HASH}"

info "Label:   ${LABEL}"
info "Path:    ${DIRECTORY}"
info "Kind:    ${KIND}"
info "Torrent: ${TORRENT_NAME} (${INFO_HASH})"

# ── bypass check ──────────────────────────────────────────────────────────────

if is_bypassed "$LABEL"; then
    info "Bypass label matched, skipping."
    exit 0
fi

# ── submit path ───────────────────────────────────────────────────────────────

# Single-file torrents provide the filename directly; multi-file use the directory
if [[ "$KIND" == "single" && -n "$FILENAME" ]]; then
    TARGET="${DIRECTORY%/}/${FILENAME}"
else
    TARGET="$DIRECTORY"
fi

read -r COUNT FAILED <<< "$(sma_submit_path "$TARGET")"

if [[ "$FAILED" -gt 0 ]]; then
    err "${FAILED} file(s) failed to submit to daemon at ${DAEMON_BASE} (${COUNT} submitted)."
    exit 1
fi

if [[ "$COUNT" -eq 0 ]]; then
    err "No media files submitted from: ${TARGET}"
    exit 1
fi

info "Submitted ${COUNT} job(s) to daemon."
