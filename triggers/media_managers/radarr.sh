#!/usr/bin/env bash
# SMA-NG Radarr Post-Processing Script
#
# Submits a conversion job to the SMA-NG daemon webhook and waits for completion.
# Configure via environment variables:
#
#   DAEMON_HOST     Daemon host (default: 127.0.0.1)
#   DAEMON_PORT     Daemon port (default: 8585)
#   DAEMON_API_KEY  API key if token authentication is enabled
#   DAEMON_USERNAME Username if HTTP Basic Auth is enabled
#   DAEMON_PASSWORD Password if HTTP Basic Auth is enabled
#   POLL_INTERVAL   Seconds between status checks (default: 5)
#   TIMEOUT         Max seconds to wait for completion (default: 0 = unlimited)
#
# Radarr environment variables are provided automatically when the script is
# called as a Radarr Custom Script connection.
#
# Payload sent to POST /webhook/radarr:
# {
#   "eventType": "Download",
#   "movie":     { "tmdbId": 603, "imdbId": "tt0133093" },
#   "movieFile": { "path": "/mnt/media/Movies/The Matrix.mkv" }
# }
#
# Alternatively, configure Radarr → Settings → Connect → Webhook and point it
# directly at http://<host>:<port>/webhook/radarr — no script required.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../lib/common.sh
. "${SCRIPT_DIR}/../lib/common.sh"

# ── configuration (override with environment variables) ───────────────────────

DAEMON_HOST="${DAEMON_HOST:-127.0.0.1}"
DAEMON_PORT="${DAEMON_PORT:-8585}"
DAEMON_API_KEY="${DAEMON_API_KEY:-}"
DAEMON_USERNAME="${DAEMON_USERNAME:-}"
DAEMON_PASSWORD="${DAEMON_PASSWORD:-}"
POLL_INTERVAL="${POLL_INTERVAL:-5}"
TIMEOUT="${TIMEOUT:-0}"

DAEMON_HOST_VALUE="$DAEMON_HOST"
DAEMON_PORT_VALUE="$DAEMON_PORT"
DAEMON_BASE="http://${DAEMON_HOST_VALUE}:${DAEMON_PORT_VALUE}"
POLL_INTERVAL="$POLL_INTERVAL"
TIMEOUT="$TIMEOUT"

# ── helpers ───────────────────────────────────────────────────────────────────

log() { echo "[radarr] $*" >&2; }

sma_init_daemon

wait_for_job() {
    sma_wait_for_job "radarr" "$1" "$POLL_INTERVAL" "$TIMEOUT"
}

# ── event handling ─────────────────────────────────────────────────────────────

EVENT="${radarr_eventtype:-}"

if [[ "$EVENT" == "Test" ]]; then
    log "Successful postRadarr.sh SMA-NG test, exiting."
    exit 0
fi

if [[ "$EVENT" != "Download" ]]; then
    log "ERROR: Invalid event type '${EVENT}'. Script only handles On Download/On Import and On Upgrade."
    exit 1
fi

# ── build native Radarr webhook payload ───────────────────────────────────────

INPUTFILE="${radarr_moviefile_path:-}"

if [[ -z "$INPUTFILE" ]]; then
    log "ERROR: radarr_moviefile_path is not set."
    exit 1
fi

log "Input file: ${INPUTFILE}"
log "TMDB ID: ${radarr_movie_tmdbid:-}, IMDB ID: ${radarr_movie_imdbid:-}"

PAYLOAD=$(python3 "$JSON_TOOL" build-radarr-env)

# ── submit job ─────────────────────────────────────────────────────────────────

log "Submitting job to ${DAEMON_BASE}/webhook/radarr..."

HTTP_CODE=$(curl -s -o /tmp/sma_response.json -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    "${AUTH_ARGS[@]}" \
    -d "$PAYLOAD" \
    "${DAEMON_BASE}/webhook/radarr" 2>/dev/null) || HTTP_CODE="000"

RESPONSE=$(cat /tmp/sma_response.json 2>/dev/null || true)

if [[ "$HTTP_CODE" == "000" ]]; then
    log "ERROR: Failed to connect to SMA-NG daemon at ${DAEMON_BASE}. Is the daemon running?"
    exit 1
elif [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" ]]; then
    # Show which auth vars are set (mask secrets).
    if [[ -n "${DAEMON_API_KEY:-}" ]]; then
        _key_hint="DAEMON_API_KEY=${DAEMON_API_KEY:0:4}**** (set)"
    else
        _key_hint="DAEMON_API_KEY=(not set)"
    fi
    if [[ -n "${DAEMON_USERNAME:-}" ]]; then
        _user_hint="DAEMON_USERNAME=${DAEMON_USERNAME} DAEMON_PASSWORD=$([ -n "${DAEMON_PASSWORD:-}" ] && echo '[set]' || echo '[not set]')"
    else
        _user_hint="DAEMON_USERNAME=(not set)"
    fi
    log "ERROR: Daemon rejected request (HTTP ${HTTP_CODE}). Auth state: ${_key_hint}  ${_user_hint}"
    exit 1
elif [[ "$HTTP_CODE" -ge 400 ]]; then
    ERR=$(sma_json_get_field "$RESPONSE" "error" "unknown")
    log "ERROR: Daemon returned HTTP ${HTTP_CODE}: ${ERR}"
    exit 1
fi

JOB_ID=$(sma_json_get_field "$RESPONSE" "job_id" "")

if [[ -z "$JOB_ID" ]]; then
    ERR=$(sma_json_get_field "$RESPONSE" "error" "unknown")
    log "ERROR: Daemon rejected job: ${ERR}"
    exit 1
fi

log "Job ${JOB_ID} queued."

# ── wait for completion ────────────────────────────────────────────────────────

wait_for_job "$JOB_ID" || exit 1

log "Radarr post-processing complete."
