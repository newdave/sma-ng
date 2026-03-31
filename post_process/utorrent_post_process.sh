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

log()  { echo "[utorrent_post_process] $*" >&2; }
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
        "${SMA_BASE}/webhook" > /dev/null
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

COUNT=$(submit_path "$TARGET")

if [[ "$COUNT" -eq 0 ]]; then
    err "No files submitted from: ${TARGET}"
    exit 1
fi

info "Submitted ${COUNT} job(s) to daemon."
