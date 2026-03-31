#!/usr/bin/env bash
##############################################################################
### NZBGET POST-PROCESSING SCRIPT                                          ###
### SMA-NG webhook integration for NZBGet                                  ###
##############################################################################
#
# Submits conversion jobs to the SMA-NG daemon via webhook.
#
# NOTE: This script requires NZBGet v11.0+.
#
# NOTE: Configure the daemon connection via environment variables:
#   SMA_DAEMON_HOST, SMA_DAEMON_PORT, SMA_DAEMON_API_KEY
#
##############################################################################
### OPTIONS                                                                ###
#
# Convert file before passing to destination (true, false).
# SHOULDCONVERT=true
#
# Sonarr category name.
# SONARR_CAT=sonarr
#
# Radarr category name.
# RADARR_CAT=radarr
#
# Bypass category name.
# BYPASS_CAT=bypass
#
### NZBGET POST-PROCESSING SCRIPT                                          ###
##############################################################################

set -euo pipefail

# NZBGet exit codes
POSTPROCESS_SUCCESS=93
POSTPROCESS_ERROR=94
POSTPROCESS_NONE=95

SMA_HOST="${SMA_DAEMON_HOST:-127.0.0.1}"
SMA_PORT="${SMA_DAEMON_PORT:-8585}"
SMA_BASE="http://${SMA_HOST}:${SMA_PORT}"

# ── helpers ───────────────────────────────────────────────────────────────────

log()  { echo "[nzbget] $*" >&2; }
info() { log "INFO: $*"; }
warn() { log "WARNING: $*"; }
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

# ── validate NZBGet environment ───────────────────────────────────────────────

if [[ -z "${NZBOP_VERSION:-}" ]]; then
    err "This script requires NZBGet v11.0+."
    exit $POSTPROCESS_ERROR
fi

# ── read NZBGet script options ────────────────────────────────────────────────

SHOULD_CONVERT="${NZBPO_SHOULDCONVERT:-true}"
BYPASS_CAT="${NZBPO_BYPASS_CAT:-bypass}"

TOTAL_STATUS="${NZBPP_TOTALSTATUS:-}"
DIRECTORY="${NZBPP_DIRECTORY:-}"
CATEGORY="${NZBPP_CATEGORY:-}"
CATEGORY_LOWER="${CATEGORY,,}"
BYPASS_LOWER="${BYPASS_CAT,,}"

# ── checks ────────────────────────────────────────────────────────────────────

if [[ "$TOTAL_STATUS" != "SUCCESS" ]]; then
    warn "Download not successful (status: ${TOTAL_STATUS}), skipping."
    exit $POSTPROCESS_NONE
fi

info "Directory: ${DIRECTORY}"
info "Category:  ${CATEGORY}"

if [[ -z "$DIRECTORY" || ! -d "$DIRECTORY" ]]; then
    err "Invalid directory: ${DIRECTORY}"
    exit $POSTPROCESS_ERROR
fi

if [[ "${CATEGORY_LOWER}" == "${BYPASS_LOWER}"* ]]; then
    info "Bypass category matched, skipping."
    exit $POSTPROCESS_NONE
fi

if [[ "${SHOULD_CONVERT,,}" != "true" ]]; then
    info "Conversion disabled, skipping."
    exit $POSTPROCESS_NONE
fi

# ── submit files ──────────────────────────────────────────────────────────────

SUBMITTED=0
FAILED=0

while IFS= read -r -d '' filepath; do
    if submit_file "$filepath"; then
        SUBMITTED=$(( SUBMITTED + 1 ))
    else
        warn "Failed to submit: ${filepath}"
        FAILED=$(( FAILED + 1 ))
    fi
done < <(find "$DIRECTORY" -type f -print0)

if [[ $SUBMITTED -gt 0 ]]; then
    info "Submitted ${SUBMITTED} job(s) to daemon."
    exit $POSTPROCESS_SUCCESS
else
    warn "No jobs submitted."
    exit $POSTPROCESS_NONE
fi
