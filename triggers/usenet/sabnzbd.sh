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
#   DAEMON_HOST     Daemon host (default: 127.0.0.1)
#   DAEMON_PORT     Daemon port (default: 8585)
#   DAEMON_API_KEY  API key if authentication is enabled
#   BYPASS_CATS     Comma-separated category prefixes to skip (default: bypass)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../lib/common.sh
. "${SCRIPT_DIR}/../lib/common.sh"

DAEMON_HOST_VALUE="${DAEMON_HOST:-127.0.0.1}"
DAEMON_PORT_VALUE="${DAEMON_PORT:-8585}"
DAEMON_BASE="http://${DAEMON_HOST_VALUE}:${DAEMON_PORT_VALUE}"
BYPASS_CATS="${BYPASS_CATS:-bypass}"
sma_init_daemon

# ── helpers ───────────────────────────────────────────────────────────────────

log()  { echo "[sabnzbd] $*" >&2; }
info() { log "INFO: $*"; }
err()  { log "ERROR: $*"; }

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

if [[ ! -e "$PATH_ARG" ]]; then
    err "Path does not exist: ${PATH_ARG}"
    exit 1
fi

read -r SUBMITTED FAILED <<< "$(sma_submit_path "$PATH_ARG")"

if [[ "$FAILED" -gt 0 ]]; then
    err "${FAILED} file(s) failed to submit to daemon at ${DAEMON_BASE} (${SUBMITTED} submitted)."
    exit 1
fi

if [[ "$SUBMITTED" -eq 0 ]]; then
    err "No media files found to submit under: ${PATH_ARG}"
    exit 1
fi

info "Submitted ${SUBMITTED} job(s) to daemon."
