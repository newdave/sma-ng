#!/usr/bin/env bash
# SMA-NG Radarr Post-Processing Script
#
# Submits a conversion job to the SMA-NG daemon webhook and waits for completion.
# Configure via environment variables:
#
#   SMA_DAEMON_HOST     Daemon host (default: 127.0.0.1)
#   SMA_DAEMON_PORT     Daemon port (default: 8585)
#   SMA_DAEMON_API_KEY  API key if token authentication is enabled
#   SMA_DAEMON_USERNAME Username if HTTP Basic Auth is enabled
#   SMA_DAEMON_PASSWORD Password if HTTP Basic Auth is enabled
#   SMA_POLL_INTERVAL   Seconds between status checks (default: 5)
#   SMA_TIMEOUT         Max seconds to wait for completion (default: 0 = unlimited)
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

SMA_DAEMON_HOST="${SMA_DAEMON_HOST:-127.0.0.1}"
SMA_DAEMON_PORT="${SMA_DAEMON_PORT:-8585}"
SMA_DAEMON_API_KEY="${SMA_DAEMON_API_KEY:-}"
SMA_DAEMON_USERNAME="${SMA_DAEMON_USERNAME:-}"
SMA_DAEMON_PASSWORD="${SMA_DAEMON_PASSWORD:-}"
SMA_POLL_INTERVAL="${SMA_POLL_INTERVAL:-5}"
SMA_TIMEOUT="${SMA_TIMEOUT:-0}"

SMA_HOST="$SMA_DAEMON_HOST"
SMA_PORT="$SMA_DAEMON_PORT"
SMA_BASE="http://${SMA_HOST}:${SMA_PORT}"
POLL_INTERVAL="$SMA_POLL_INTERVAL"
TIMEOUT="$SMA_TIMEOUT"

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

PAYLOAD=$(python3 "$SMA_JSON_TOOL" build-radarr-env)

# ── submit job ─────────────────────────────────────────────────────────────────

log "Submitting job to ${SMA_BASE}/webhook/radarr..."

HTTP_CODE=$(curl -s -o /tmp/sma_response.json -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    "${AUTH_ARGS[@]}" \
    -d "$PAYLOAD" \
    "${SMA_BASE}/webhook/radarr" 2>/dev/null) || HTTP_CODE="000"

RESPONSE=$(cat /tmp/sma_response.json 2>/dev/null || true)

if [[ "$HTTP_CODE" == "000" ]]; then
    log "ERROR: Failed to connect to SMA-NG daemon at ${SMA_BASE}. Is the daemon running?"
    exit 1
elif [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" ]]; then
    # Show which auth vars are set (mask secrets).
    if [[ -n "${SMA_DAEMON_API_KEY:-}" ]]; then
        _key_hint="SMA_DAEMON_API_KEY=${SMA_DAEMON_API_KEY:0:4}**** (set)"
    else
        _key_hint="SMA_DAEMON_API_KEY=(not set)"
    fi
    if [[ -n "${SMA_DAEMON_USERNAME:-}" ]]; then
        _user_hint="SMA_DAEMON_USERNAME=${SMA_DAEMON_USERNAME} SMA_DAEMON_PASSWORD=$([ -n "${SMA_DAEMON_PASSWORD:-}" ] && echo '[set]' || echo '[not set]')"
    else
        _user_hint="SMA_DAEMON_USERNAME=(not set)"
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
