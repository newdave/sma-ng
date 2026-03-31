#!/usr/bin/env bash
# SMA-NG Sonarr Post-Processing Script
#
# Submits a conversion job to the SMA-NG daemon webhook and waits for completion.
# Configure via environment variables:
#
#   SMA_DAEMON_HOST   Daemon host (default: 127.0.0.1)
#   SMA_DAEMON_PORT   Daemon port (default: 8585)
#   SMA_DAEMON_API_KEY  API key if authentication is enabled
#   SMA_POLL_INTERVAL   Seconds between status checks (default: 5)
#   SMA_TIMEOUT         Max seconds to wait for completion (default: 0 = unlimited)
#
# Sonarr environment variables are provided automatically when the script is
# called as a Sonarr Custom Script connection.

set -euo pipefail

SMA_HOST="${SMA_DAEMON_HOST:-127.0.0.1}"
SMA_PORT="${SMA_DAEMON_PORT:-8585}"
SMA_BASE="http://${SMA_HOST}:${SMA_PORT}"
POLL_INTERVAL="${SMA_POLL_INTERVAL:-5}"
TIMEOUT="${SMA_TIMEOUT:-0}"

# ── helpers ───────────────────────────────────────────────────────────────────

log() { echo "[sonarr_post_process] $*" >&2; }

auth_header() {
    if [[ -n "${SMA_DAEMON_API_KEY:-}" ]]; then
        echo "-H" "X-API-Key: ${SMA_DAEMON_API_KEY}"
    fi
}

curl_get() {
    local url="$1"
    curl -sf $(auth_header) "$url"
}

curl_post_json() {
    local url="$1"
    local body="$2"
    curl -sf -X POST \
        -H "Content-Type: application/json" \
        $(auth_header) \
        -d "$body" \
        "$url"
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

EVENT="${sonarr_eventtype:-}"

if [[ "$EVENT" == "Test" ]]; then
    log "Successful postSonarr.sh SMA-NG test, exiting."
    exit 0
fi

if [[ "$EVENT" != "Download" ]]; then
    log "ERROR: Invalid event type '${EVENT}'. Script only handles On Download/On Import and On Upgrade."
    exit 1
fi

# ── build webhook payload ──────────────────────────────────────────────────────

INPUTFILE="${sonarr_episodefile_path:-}"
TVDB_ID="${sonarr_series_tvdbid:-}"
IMDB_ID="${sonarr_series_imdbid:-}"
SEASON="${sonarr_episodefile_seasonnumber:-}"
EPISODE_NUMBERS="${sonarr_episodefile_episodenumbers:-}"

if [[ -z "$INPUTFILE" ]]; then
    log "ERROR: sonarr_episodefile_path is not set."
    exit 1
fi

log "Input file: ${INPUTFILE}"
log "TVDB ID: ${TVDB_ID}, S$(printf '%02d' "$SEASON")E$(echo "$EPISODE_NUMBERS" | cut -d, -f1 | xargs printf '%02d')"

# Build args array: -tvdb <id> -s <season> -e <ep1> [-e <ep2> ...]
ARGS="[]"
if [[ -n "$TVDB_ID" ]]; then
    ARGS=$(echo "$ARGS" | python3 -c "
import sys, json
a = json.load(sys.stdin)
a += ['-tvdb', '${TVDB_ID}', '-s', '${SEASON}']
for ep in '${EPISODE_NUMBERS}'.split(','):
    ep = ep.strip()
    if ep:
        a += ['-e', ep]
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
import json
print(json.dumps({'path': '${INPUTFILE}', 'args': $(echo "$ARGS")}))
")

# ── submit job ─────────────────────────────────────────────────────────────────

log "Submitting job to ${SMA_BASE}/webhook..."

RESPONSE=$(curl_post_json "${SMA_BASE}/webhook" "$PAYLOAD") || {
    log "ERROR: Failed to connect to SMA-NG daemon at ${SMA_BASE}. Is the daemon running?"
    exit 1
}

JOB_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null)

if [[ -z "$JOB_ID" ]]; then
    ERR=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error','unknown'))" 2>/dev/null)
    log "ERROR: Daemon rejected job: ${ERR}"
    exit 1
fi

log "Job ${JOB_ID} queued."

# ── wait for completion ────────────────────────────────────────────────────────

wait_for_job "$JOB_ID" || exit 1

log "Sonarr post-processing complete."
