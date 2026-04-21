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

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../lib/common.sh
. "${SCRIPT_DIR}/../lib/common.sh"

# Auto-load daemon.env if SMA_DAEMON_API_KEY is not already set
if [[ -z "${SMA_DAEMON_API_KEY:-}" ]]; then
    for _env in \
        "${SMA_INSTALL_DIR:-/opt/sma}/config/daemon.env" \
        "$(dirname "$(dirname "$(dirname "$(realpath "$0")")")")/config/daemon.env"
    do
        if [[ -f "$_env" ]]; then
            set +u
            # shellcheck source=/dev/null
            . "$_env"
            set -u
            break
        fi
    done
    unset _env
fi

SMA_HOST="${SMA_DAEMON_HOST:-127.0.0.1}"
SMA_PORT="${SMA_DAEMON_PORT:-8585}"
SMA_BASE="http://${SMA_HOST}:${SMA_PORT}"
sma_init_daemon

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
    local http_code response_body tmp
    tmp=$(mktemp)
    if [[ -n "${SMA_DAEMON_API_KEY:-}" ]]; then
        http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST \
            -H "Content-Type: application/json" \
            -H "X-API-Key: ${SMA_DAEMON_API_KEY}" \
            -d "$body" \
            "$url" 2>/dev/null) || { rm -f "$tmp"; return 1; }
    else
        http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST \
            -H "Content-Type: application/json" \
            -d "$body" \
            "$url" 2>/dev/null) || { rm -f "$tmp"; return 1; }
    fi
    response_body=$(cat "$tmp"); rm -f "$tmp"
    if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
        log "Daemon returned HTTP ${http_code}: ${response_body}"
        return 1
    fi
    echo "$response_body"
}

wait_for_job() {
    sma_wait_for_job "scan" "$1" "$POLL_INTERVAL" "$TIMEOUT" || die "Job $1 failed."
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
payload=$(sma_build_generic_payload "$TARGET" "${CONFIG_OVERRIDE:-}" "${EXTRA_ARGS[@]}")

log "Submitting: $TARGET"
[[ "${#EXTRA_ARGS[@]}" -gt 0 ]] && log "Extra args: ${EXTRA_ARGS[*]}"
[[ -n "$CONFIG_OVERRIDE" ]] && log "Config:     $CONFIG_OVERRIDE"

response=$(curl_post_json "${SMA_BASE}/webhook/generic" "$payload") || {
    die "Submission failed. Check daemon is running at ${SMA_BASE}."
}

job_id=$(sma_json_get_field "$response" "job_id" "")

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
