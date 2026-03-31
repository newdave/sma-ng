#!/usr/bin/env bash
# SMA-NG CLI Trigger — submit a file or directory for conversion
#
# Usage:
#   scan.sh [OPTIONS] <path>
#
# Options:
#   -w, --wait            Wait for the job to complete (default: submit and exit)
#   -c, --config PATH     Override the autoProcess.ini config for this job
#   -a, --args ARGS       Extra arguments to pass to manual.py (e.g. "-tmdb 603")
#       --tmdb ID         Shorthand for --args "-tmdb ID"
#       --tvdb ID         Shorthand for --args "-tvdb ID"
#   -s, --season N        Season number (used with --tvdb)
#   -e, --episode N       Episode number (used with --tvdb)
#   -t, --timeout N       Seconds to wait before giving up (default: 0 = unlimited)
#   -i, --interval N      Polling interval in seconds (default: 5)
#   -h, --help            Show this help
#
# Environment variables:
#   SMA_DAEMON_HOST     Daemon host (default: 127.0.0.1)
#   SMA_DAEMON_PORT     Daemon port (default: 8585)
#   SMA_DAEMON_API_KEY  API key if authentication is enabled
#
# Examples:
#   scan.sh /media/movies/film.mkv
#   scan.sh --wait --tmdb 603 /media/movies/film.mkv
#   scan.sh --wait --tvdb 73871 -s 3 -e 10 /media/tv/show/episode.mkv
#   scan.sh --wait --config /etc/sma/4k.ini /media/4k/film.mkv
#   scan.sh --wait /media/tv/show/season1/    # directory — queues all files

set -euo pipefail

SMA_HOST="${SMA_DAEMON_HOST:-127.0.0.1}"
SMA_PORT="${SMA_DAEMON_PORT:-8585}"
SMA_BASE="http://${SMA_HOST}:${SMA_PORT}"

WAIT=false
CONFIG_OVERRIDE=""
EXTRA_ARGS=()
TMDB_ID=""
TVDB_ID=""
SEASON=""
EPISODE=""
TIMEOUT="${SMA_TIMEOUT:-0}"
POLL_INTERVAL="${SMA_POLL_INTERVAL:-5}"
TARGET=""

# ── helpers ───────────────────────────────────────────────────────────────────

usage() {
    sed -n '2,/^set -/{ /^#/{ s/^# \{0,1\}//; p }; /^set -/q }' "$0"
    exit "${1:-0}"
}

log()  { echo "[scan] $*" >&2; }
die()  { echo "[scan] ERROR: $*" >&2; exit 1; }

auth_header() {
    if [[ -n "${SMA_DAEMON_API_KEY:-}" ]]; then
        printf '%s\0%s\0' "-H" "X-API-Key: ${SMA_DAEMON_API_KEY}"
    fi
}

curl_get() {
    local url="$1"
    if [[ -n "${SMA_DAEMON_API_KEY:-}" ]]; then
        curl -sf -H "X-API-Key: ${SMA_DAEMON_API_KEY}" "$url"
    else
        curl -sf "$url"
    fi
}

curl_post_json() {
    local url="$1"
    local body="$2"
    if [[ -n "${SMA_DAEMON_API_KEY:-}" ]]; then
        curl -sf -X POST \
            -H "Content-Type: application/json" \
            -H "X-API-Key: ${SMA_DAEMON_API_KEY}" \
            -d "$body" \
            "$url"
    else
        curl -sf -X POST \
            -H "Content-Type: application/json" \
            -d "$body" \
            "$url"
    fi
}

wait_for_job() {
    local job_id="$1"
    local start
    start=$(date +%s)

    log "Waiting for job ${job_id} to complete (polling every ${POLL_INTERVAL}s)..."

    while true; do
        local response status elapsed
        response=$(curl_get "${SMA_BASE}/jobs/${job_id}" 2>/dev/null) || {
            die "Lost contact with daemon while polling job ${job_id}."
        }

        status=$(echo "$response" | python3 -c \
            "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)

        case "$status" in
            completed)
                elapsed=$(( $(date +%s) - start ))
                log "Job ${job_id} completed in ${elapsed}s."
                return 0
                ;;
            failed)
                local err
                err=$(echo "$response" | python3 -c \
                    "import sys,json; print(json.load(sys.stdin).get('error','unknown'))" 2>/dev/null)
                die "Job ${job_id} failed: ${err}"
                ;;
            pending|running)
                if [[ "$TIMEOUT" -gt 0 ]]; then
                    elapsed=$(( $(date +%s) - start ))
                    if [[ "$elapsed" -gt "$TIMEOUT" ]]; then
                        die "Timed out waiting for job ${job_id} after ${elapsed}s."
                    fi
                fi
                sleep "$POLL_INTERVAL"
                ;;
            *)
                die "Unknown job status '${status}' for job ${job_id}."
                ;;
        esac
    done
}

# ── argument parsing ──────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        -w|--wait)        WAIT=true; shift ;;
        -c|--config)      CONFIG_OVERRIDE="$2"; shift 2 ;;
        -a|--args)        read -ra _a <<< "$2"; EXTRA_ARGS+=("${_a[@]}"); shift 2 ;;
        --tmdb)           TMDB_ID="$2"; shift 2 ;;
        --tvdb)           TVDB_ID="$2"; shift 2 ;;
        -s|--season)      SEASON="$2"; shift 2 ;;
        -e|--episode)     EPISODE="$2"; shift 2 ;;
        -t|--timeout)     TIMEOUT="$2"; shift 2 ;;
        -i|--interval)    POLL_INTERVAL="$2"; shift 2 ;;
        -h|--help)        usage 0 ;;
        --)               shift; TARGET="$1"; break ;;
        -*)               die "Unknown option: $1 (try --help)" ;;
        *)                TARGET="$1"; shift ;;
    esac
done

[[ -z "$TARGET" ]] && die "No path specified. Usage: scan.sh [OPTIONS] <path>"

# Assemble manual.py args from convenience flags
[[ -n "$TMDB_ID" ]] && EXTRA_ARGS+=("-tmdb" "$TMDB_ID")
if [[ -n "$TVDB_ID" ]]; then
    EXTRA_ARGS+=("-tvdb" "$TVDB_ID")
    [[ -n "$SEASON"  ]] && EXTRA_ARGS+=("-s" "$SEASON")
    [[ -n "$EPISODE" ]] && EXTRA_ARGS+=("-e" "$EPISODE")
fi

# ── build payload and submit ──────────────────────────────────────────────────

# Encode extra_args as a JSON array
if [[ "${#EXTRA_ARGS[@]}" -gt 0 ]]; then
    args_json=$(python3 -c "import sys,json; print(json.dumps(sys.argv[1:]))" -- "${EXTRA_ARGS[@]}")
else
    args_json="[]"
fi

payload=$(python3 -c "
import sys, json
path   = sys.argv[1]
args   = json.loads(sys.argv[2])
config = sys.argv[3] if sys.argv[3] else None
obj = {'path': path}
if args:   obj['args']   = args
if config: obj['config'] = config
print(json.dumps(obj))
" "$TARGET" "$args_json" "${CONFIG_OVERRIDE:-}")

log "Submitting: $TARGET"
[[ "${#EXTRA_ARGS[@]}" -gt 0 ]] && log "Extra args: ${EXTRA_ARGS[*]}"
[[ -n "$CONFIG_OVERRIDE" ]] && log "Config:     $CONFIG_OVERRIDE"

response=$(curl_post_json "${SMA_BASE}/webhook" "$payload") || {
    die "Failed to reach daemon at ${SMA_BASE}. Is it running?"
}

job_id=$(echo "$response" | python3 -c \
    "import sys,json; print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null)

if [[ -z "$job_id" ]]; then
    log "Daemon response: $response"
    die "No job_id returned — submission may have failed."
fi

log "Submitted job ${job_id}."

if $WAIT; then
    wait_for_job "$job_id"
else
    echo "$job_id"
fi
