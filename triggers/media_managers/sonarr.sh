#!/usr/bin/env bash
# SMA-NG Sonarr Post-Processing Script
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
# Sonarr environment variables are provided automatically when the script is
# called as a Sonarr Custom Script connection.
#
# Payload sent to POST /webhook/sonarr:
# {
#   "eventType":   "Download",
#   "series":      { "tvdbId": 73871, "imdbId": "tt0472308" },
#   "episodes":    [ { "seasonNumber": 3, "episodeNumber": 10 } ],
#   "episodeFile": { "path": "/mnt/media/TV/Show/S03E10.mkv" }
# }
#
# Alternatively, configure Sonarr → Settings → Connect → Webhook and point it
# directly at http://<host>:<port>/webhook/sonarr — no script required.

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

log() { echo "[sonarr] $*" >&2; }

sma_init_daemon

wait_for_job() {
    sma_wait_for_job "sonarr" "$1" "$POLL_INTERVAL" "$TIMEOUT"
}

# ── event handling ─────────────────────────────────────────────────────────────

EVENT="${sonarr_eventtype:-}"

if [[ "$EVENT" == "Test" ]]; then
    log "Successful postSonarr.sh SMA-NG test, exiting."
    exit 0
fi

if [[ "$EVENT" != "Download" ]]; then
    log "ERROR: Invalid event type '${EVENT}'. Script only handles On Download/On Import and On Upgrade."
    exit 1
fi

# ── build native Sonarr webhook payload ───────────────────────────────────────

INPUTFILE="${sonarr_episodefile_path:-}"

if [[ -z "$INPUTFILE" ]]; then
    log "ERROR: sonarr_episodefile_path is not set."
    exit 1
fi

SEASON="${sonarr_episodefile_seasonnumber:-}"
EPISODE_NUMBERS="${sonarr_episodefile_episodenumbers:-}"
log "Input file: ${INPUTFILE}"
log "TVDB ID: ${sonarr_series_tvdbid:-}, S$(printf '%02d' "${SEASON:-0}")E$(echo "${EPISODE_NUMBERS:-0}" | cut -d, -f1 | xargs printf '%02d')"

PAYLOAD=$(python3 "$SMA_JSON_TOOL" build-sonarr-env)

# ── submit job ─────────────────────────────────────────────────────────────────

log "Submitting job to ${SMA_BASE}/webhook/sonarr..."

HTTP_CODE=$(curl -s -o /tmp/sma_response.json -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    "${AUTH_ARGS[@]}" \
    -d "$PAYLOAD" \
    "${SMA_BASE}/webhook/sonarr" 2>/dev/null) || HTTP_CODE="000"

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

log "Sonarr post-processing complete."
