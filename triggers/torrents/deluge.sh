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
#   SMA_DAEMON_HOST     Daemon host (default: 127.0.0.1)
#   SMA_DAEMON_PORT     Daemon port (default: 8585)
#   SMA_DAEMON_API_KEY  API key if authentication is enabled
#   SMA_BYPASS_LABELS   Comma-separated label prefixes to skip (default: bypass)

set -euo pipefail

SMA_HOST="${SMA_DAEMON_HOST:-127.0.0.1}"
SMA_PORT="${SMA_DAEMON_PORT:-8585}"
SMA_BASE="http://${SMA_HOST}:${SMA_PORT}"
BYPASS_LABELS="${SMA_BYPASS_LABELS:-bypass}"

# ── helpers ───────────────────────────────────────────────────────────────────

log()  { echo "[deluge] $*" >&2; }
info() { log "INFO: $*"; }
err()  { log "ERROR: $*"; }

auth_args() {
    if [[ -n "${SMA_DAEMON_API_KEY:-}" ]]; then
        echo "-H" "X-API-Key: ${SMA_DAEMON_API_KEY}"
    fi
}

submit_file() {
    local filepath="$1"
    local payload
    payload=$(python3 -c "import json,sys; print(json.dumps({'path': sys.argv[1]}))" "$filepath")
    curl -sf -X POST \
        -H "Content-Type: application/json" \
        $(auth_args) \
        -d "$payload" \
        "${SMA_BASE}/webhook/generic" > /dev/null
}

submit_path() {
    local target="$1"
    local submitted=0
    if [[ -f "$target" ]]; then
        submit_file "$target" && submitted=1
    elif [[ -d "$target" ]]; then
        while IFS= read -r -d '' filepath; do
            submit_file "$filepath" && submitted=$(( submitted + 1 ))
        done < <(find "$target" -type f -print0)
    fi
    echo "$submitted"
}

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

# ── bypass check ──────────────────────────────────────────────────────────────

if is_bypassed "$LABEL"; then
    info "Bypass label matched, skipping conversion."
    exit 0
fi

# ── submit path ───────────────────────────────────────────────────────────────

# Try the path directly; if nothing found, try path/torrent_name
COUNT=$(submit_path "$PATH_ARG")

if [[ "$COUNT" -eq 0 ]]; then
    COMBINED="${PATH_ARG%/}/${TORRENT_NAME}"
    COUNT=$(submit_path "$COMBINED")
fi

if [[ "$COUNT" -eq 0 ]]; then
    err "No files submitted. Path does not exist or is empty: ${PATH_ARG}"
    exit 1
fi

info "Submitted ${COUNT} job(s) to daemon."
