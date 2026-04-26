#!/usr/bin/env bash
# SMA-NG SABnzbd Post-Processing Script
#
# Submits conversion jobs to the SMA-NG daemon via webhook.
#
# SABnzbd calls post-processing scripts with these positional arguments:
#   $1  Final directory of the job
#   $2  Original name of the NZB file
#   $3  Clean version of the job name
#   $4  Newzbin message ID (if any)
#   $5  Category
#   $6  Group
#   $7  Status (0 = success)
#   $8  Failure URL (if any)
#
# Configure daemon connection via environment variables:
#   SMA_DAEMON_HOST     Daemon host (default: 127.0.0.1)
#   SMA_DAEMON_PORT     Daemon port (default: 8585)
#   SMA_DAEMON_API_KEY  API key if authentication is enabled
#   SMA_BYPASS_CATS     Comma-separated category prefixes to skip (default: bypass)

set -euo pipefail

SMA_HOST="${SMA_DAEMON_HOST:-127.0.0.1}"
SMA_PORT="${SMA_DAEMON_PORT:-8585}"
SMA_BASE="http://${SMA_HOST}:${SMA_PORT}"
BYPASS_CATS="${SMA_BYPASS_CATS:-bypass}"

# ── helpers ───────────────────────────────────────────────────────────────────

log()  { echo "[sabnzbd_post_process] $*" >&2; }
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

is_bypassed() {
    local category="$1"
    local cat_lower="${category,,}"
    IFS=',' read -ra bypasses <<< "$BYPASS_CATS"
    for b in "${bypasses[@]}"; do
        b="${b,,}"
        b="${b#"${b%%[![:space:]]*}"}"  # ltrim
        b="${b%"${b##*[![:space:]]}"}"  # rtrim
        if [[ -n "$b" && "$cat_lower" == "${b}"* ]]; then
            return 0
        fi
    done
    return 1
}

# ── argument validation ───────────────────────────────────────────────────────

info "SABnzbd post-processing started."

if [[ $# -lt 7 ]]; then
    err "Not enough arguments from SABnzbd (got $#, expected at least 7)."
    exit 1
fi

PATH_ARG="$1"
CATEGORY="$5"
STATUS="$7"

info "Path:     ${PATH_ARG}"
info "Category: ${CATEGORY}"
info "Status:   ${STATUS}"

# ── checks ────────────────────────────────────────────────────────────────────

if [[ "$STATUS" != "0" ]]; then
    err "Download failed with status ${STATUS}, skipping."
    exit 1
fi

if is_bypassed "$CATEGORY"; then
    info "Bypass category matched, skipping conversion."
    exit 0
fi

# ── submit path (file or directory) ──────────────────────────────────────────

if [[ -f "$PATH_ARG" ]]; then
    if submit_file "$PATH_ARG"; then
        info "Submitted: ${PATH_ARG}"
    else
        err "Failed to submit job to daemon at ${SMA_BASE}."
        exit 1
    fi
elif [[ -d "$PATH_ARG" ]]; then
    SUBMITTED=0
    while IFS= read -r -d '' filepath; do
        if submit_file "$filepath"; then
            SUBMITTED=$(( SUBMITTED + 1 ))
        fi
    done < <(find "$PATH_ARG" -type f -print0)
    info "Submitted ${SUBMITTED} job(s) to daemon."
else
    err "Path does not exist: ${PATH_ARG}"
    exit 1
fi
