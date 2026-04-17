#!/usr/bin/env bash
# SMA-NG Radarr Post-Processing Script
#
# Submits a conversion job to the SMA-NG daemon webhook and waits for completion.
# Configure via environment variables:
#
#   SMA_DAEMON_HOST     Daemon host (default: 127.0.0.1)
#   SMA_DAEMON_PORT     Daemon port (default: 8585)
#   SMA_DAEMON_API_KEY  API key if authentication is enabled
#   SMA_CONFIG          Path to autoProcess.ini to use for this Radarr instance.
#                       When set, overrides daemon path-based config selection.
#                       Use this when you have multiple Radarr instances each
#                       with a different autoProcess.ini (e.g. 1080p vs 4K).
#                       Leave unset to let the daemon select config by file path.
#   SMA_POLL_INTERVAL   Seconds between status checks (default: 5)
#   SMA_TIMEOUT         Max seconds to wait for completion (default: 0 = unlimited)
#
# Radarr environment variables are provided automatically when the script is
# called as a Radarr Custom Script connection.

set -euo pipefail

SMA_HOST="${SMA_DAEMON_HOST:-127.0.0.1}"
SMA_PORT="${SMA_DAEMON_PORT:-8585}"
SMA_BASE="http://${SMA_HOST}:${SMA_PORT}"
POLL_INTERVAL="${SMA_POLL_INTERVAL:-5}"
TIMEOUT="${SMA_TIMEOUT:-0}"

# ── helpers ───────────────────────────────────────────────────────────────────

log() { echo "[radarr] $*" >&2; }

# Build auth header args as an array so word-splitting doesn't corrupt the value.
AUTH_ARGS=()
if [[ -n "${SMA_DAEMON_API_KEY:-}" ]]; then
    AUTH_ARGS=(-H "X-API-Key: ${SMA_DAEMON_API_KEY}")
fi

curl_get() {
    local url="$1"
    curl -sf "${AUTH_ARGS[@]}" "$url"
}

wait_for_job() {
    local job_id="$1"
    local start
    start=$(date +%s)

    log "Waiting for job ${job_id} to complete (polling every ${POLL_INTERVAL}s)..."

    while true; do
        local response status elapsed
        response=$(curl_get "${SMA_BASE}/jobs/${job_id}" 2>/dev/null) || {
            log "ERROR: Lost contact with daemon while polling job ${job_id}."
            return 1
        }

        status=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)

        case "$status" in
            completed)
                elapsed=$(( $(date +%s) - start ))
                log "Job ${job_id} completed in ${elapsed}s."
                return 0
                ;;
            failed)
                local err
                err=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error','unknown'))" 2>/dev/null)
                log "ERROR: Job ${job_id} failed: ${err}"
                return 1
                ;;
            pending|running)
                if [[ "$TIMEOUT" -gt 0 ]]; then
                    elapsed=$(( $(date +%s) - start ))
                    if [[ "$elapsed" -gt "$TIMEOUT" ]]; then
                        log "ERROR: Timed out waiting for job ${job_id} after ${elapsed}s."
                        return 1
                    fi
                fi
                sleep "$POLL_INTERVAL"
                ;;
            *)
                log "ERROR: Unknown job status '${status}' for job ${job_id}."
                return 1
                ;;
        esac
    done
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

# ── build webhook payload ──────────────────────────────────────────────────────

INPUTFILE="${radarr_moviefile_path:-}"
TMDB_ID="${radarr_movie_tmdbid:-}"
IMDB_ID="${radarr_movie_imdbid:-}"

if [[ -z "$INPUTFILE" ]]; then
    log "ERROR: radarr_moviefile_path is not set."
    exit 1
fi

log "Input file: ${INPUTFILE}"
log "TMDB ID: ${TMDB_ID}, IMDB ID: ${IMDB_ID}"

# Build args array: [-tmdb <id>] [-imdb <id>]
ARGS="[]"
if [[ -n "$TMDB_ID" ]]; then
    ARGS=$(echo "$ARGS" | python3 -c "
import sys, json
a = json.load(sys.stdin)
a += ['-tmdb', '${TMDB_ID}']
print(json.dumps(a))
")
fi

if [[ -n "$IMDB_ID" ]]; then
    ARGS=$(echo "$ARGS" | python3 -c "
import sys, json
a = json.load(sys.stdin)
a += ['-imdb', '${IMDB_ID}']
print(json.dumps(a))
")
fi

PAYLOAD=$(python3 -c "
import json, os
obj = {'path': '${INPUTFILE}', 'args': ${ARGS}}
config = os.environ.get('SMA_CONFIG', '').strip()
if config:
    obj['config'] = config
print(json.dumps(obj))
")

# ── submit job ─────────────────────────────────────────────────────────────────

log "Submitting job to ${SMA_BASE}/webhook..."

HTTP_CODE=$(curl -s -o /tmp/sma_response.json -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    "${AUTH_ARGS[@]}" \
    -d "$PAYLOAD" \
    "${SMA_BASE}/webhook" 2>/dev/null) || HTTP_CODE="000"

RESPONSE=$(cat /tmp/sma_response.json 2>/dev/null || true)

if [[ "$HTTP_CODE" == "000" ]]; then
    log "ERROR: Failed to connect to SMA-NG daemon at ${SMA_BASE}. Is the daemon running?"
    exit 1
elif [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" ]]; then
    log "ERROR: Daemon rejected request (HTTP ${HTTP_CODE}). Check SMA_DAEMON_API_KEY."
    exit 1
elif [[ "$HTTP_CODE" -ge 400 ]]; then
    ERR=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error','unknown'))" 2>/dev/null || echo "$RESPONSE")
    log "ERROR: Daemon returned HTTP ${HTTP_CODE}: ${ERR}"
    exit 1
fi

JOB_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null)

if [[ -z "$JOB_ID" ]]; then
    ERR=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error','unknown'))" 2>/dev/null)
    log "ERROR: Daemon rejected job: ${ERR}"
    exit 1
fi

log "Job ${JOB_ID} queued."

# ── wait for completion ────────────────────────────────────────────────────────

wait_for_job "$JOB_ID" || exit 1

log "Radarr post-processing complete."
