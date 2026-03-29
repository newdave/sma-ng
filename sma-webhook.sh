#!/usr/bin/env bash
#
# sma-webhook.sh - Submit webhooks to the SMA-NG daemon service
#
# Usage:
#   sma-webhook.sh submit /path/to/file.mkv          Submit a file for conversion
#   sma-webhook.sh submit /path/to/file.mkv -tmdb 603  Submit with extra args
#   sma-webhook.sh submit /path/to/file.mkv --config /path/to/autoProcess.ini
#   sma-webhook.sh health                             Check daemon health
#   sma-webhook.sh jobs                               List all jobs
#   sma-webhook.sh jobs pending                       List jobs by status
#   sma-webhook.sh job 42                             Get specific job details
#   sma-webhook.sh stats                              Show job statistics
#   sma-webhook.sh configs                            Show path-to-config mappings
#   sma-webhook.sh cleanup [days]                     Remove old completed/failed jobs
#
# Environment variables:
#   SMA_DAEMON_URL    Base URL (default: http://127.0.0.1:8585)
#   SMA_API_KEY       API key (overrides config/daemon.json)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DAEMON_CONFIG="$SCRIPT_DIR/config/daemon.json"

: "${SMA_DAEMON_URL:=http://127.0.0.1:8585}"

if [[ -z "${SMA_API_KEY:-}" && -f "$DAEMON_CONFIG" ]]; then
    SMA_API_KEY=$(jq -r '.api_key // empty' "$DAEMON_CONFIG" 2>/dev/null || true)
fi
: "${SMA_API_KEY:=}"

auth_headers=()
if [[ -n "$SMA_API_KEY" ]]; then
    auth_headers=(-H "X-API-Key: $SMA_API_KEY")
fi

usage() {
    sed -n '3,17s/^# \?//p' "$0"
    exit 1
}

die() { echo "Error: $*" >&2; exit 1; }

cmd_submit() {
    [[ $# -ge 1 ]] || die "submit requires a file path"
    local filepath="$1"; shift

    local config=""
    local extra_args=()

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --config)
                [[ $# -ge 2 ]] || die "--config requires a value"
                config="$2"; shift 2 ;;
            *)
                extra_args+=("$1"); shift ;;
        esac
    done

    local json
    if [[ -n "$config" && ${#extra_args[@]} -gt 0 ]]; then
        json=$(jq -n --arg p "$filepath" --arg c "$config" --argjson a "$(printf '%s\n' "${extra_args[@]}" | jq -R . | jq -s .)" \
            '{path: $p, config: $c, args: $a}')
    elif [[ -n "$config" ]]; then
        json=$(jq -n --arg p "$filepath" --arg c "$config" '{path: $p, config: $c}')
    elif [[ ${#extra_args[@]} -gt 0 ]]; then
        json=$(jq -n --arg p "$filepath" --argjson a "$(printf '%s\n' "${extra_args[@]}" | jq -R . | jq -s .)" \
            '{path: $p, args: $a}')
    else
        json=$(jq -n --arg p "$filepath" '{path: $p}')
    fi

    curl -s -X POST "$SMA_DAEMON_URL/webhook" \
        -H "Content-Type: application/json" \
        "${auth_headers[@]}" \
        -d "$json" | jq .
}

cmd_health() {
    curl -s "$SMA_DAEMON_URL/health" | jq .
}

cmd_jobs() {
    local status="${1:-}"
    local url="$SMA_DAEMON_URL/jobs"
    if [[ -n "$status" ]]; then
        url+="?status=$status"
    fi
    curl -s "$url" "${auth_headers[@]}" | jq .
}

cmd_job() {
    [[ $# -ge 1 ]] || die "job requires an ID"
    curl -s "$SMA_DAEMON_URL/jobs/$1" "${auth_headers[@]}" | jq .
}

cmd_stats() {
    curl -s "$SMA_DAEMON_URL/stats" "${auth_headers[@]}" | jq .
}

cmd_configs() {
    curl -s "$SMA_DAEMON_URL/configs" "${auth_headers[@]}" | jq .
}

cmd_cleanup() {
    local days="${1:-30}"
    curl -s -X POST "$SMA_DAEMON_URL/cleanup?days=$days" "${auth_headers[@]}" | jq .
}

# --- Main ---
[[ $# -ge 1 ]] || usage
command="$1"; shift

case "$command" in
    submit)  cmd_submit "$@" ;;
    health)  cmd_health ;;
    jobs)    cmd_jobs "$@" ;;
    job)     cmd_job "$@" ;;
    stats)   cmd_stats ;;
    configs) cmd_configs ;;
    cleanup) cmd_cleanup "$@" ;;
    help|-h|--help) usage ;;
    *)       die "Unknown command: $command" ;;
esac
