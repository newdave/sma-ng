#!/usr/bin/env bash
#
# sma-webhook.sh - Submit webhooks to the SMA-NG daemon service
#
# Usage:
#   sma-webhook.sh submit /path/to/file.mkv          Submit a file for conversion
#   sma-webhook.sh submit /path/to/file.mkv -tmdb 603  Submit with extra args
#   sma-webhook.sh submit /path/to/file.mkv --config /path/to/autoProcess.ini
#   sma-webhook.sh submit /path/to/file.mkv --retries 3  Submit with retry-on-failure
#   sma-webhook.sh health                             Check daemon health
#   sma-webhook.sh jobs                               List all jobs
#   sma-webhook.sh jobs pending                       List jobs by status
#   sma-webhook.sh job 42                             Get specific job details
#   sma-webhook.sh cancel 42                          Cancel a running or pending job
#   sma-webhook.sh stats                              Show job statistics
#   sma-webhook.sh configs                            Show path-to-config mappings
#   sma-webhook.sh cleanup [days]                     Remove old completed/failed jobs (default: 30)
#   sma-webhook.sh requeue                            Requeue all failed/interrupted jobs
#   sma-webhook.sh requeue 42                         Requeue a specific job by ID
#   sma-webhook.sh reload                             Reload sma-ng.yml config without restart
#   sma-webhook.sh restart                            Gracefully restart the daemon
#   sma-webhook.sh shutdown                           Gracefully shut down the daemon
#
# Environment variables:
#   SMA_DAEMON_URL    Base URL (default: http://127.0.0.1:8585)
#   SMA_API_KEY       API key (overrides config/sma-ng.yml)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DAEMON_CONFIG="$SCRIPT_DIR/config/sma-ng.yml"

: "${SMA_DAEMON_URL:=http://127.0.0.1:8585}"

if [[ -z "${SMA_API_KEY:-}" && -f "$DAEMON_CONFIG" ]]; then
    SMA_API_KEY=$(python3 "$SCRIPT_DIR/scripts/local-config.py" "$DAEMON_CONFIG" daemon api_key 2>/dev/null || true)
fi
: "${SMA_API_KEY:=}"

auth_headers=()
if [[ -n "$SMA_API_KEY" ]]; then
    auth_headers=(-H "X-API-Key: $SMA_API_KEY")
fi

usage() {
    sed -n '3,22s/^# \?//p' "$0"
    exit 1
}

die() { echo "Error: $*" >&2; exit 1; }

cmd_submit() {
    [[ $# -ge 1 ]] || die "submit requires a file path"
    local filepath="$1"; shift

    local config=""
    local retries=0
    local extra_args=()

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --config)
                [[ $# -ge 2 ]] || die "--config requires a value"
                config="$2"; shift 2 ;;
            --retries)
                [[ $# -ge 2 ]] || die "--retries requires a value"
                retries="$2"; shift 2 ;;
            *)
                extra_args+=("$1"); shift ;;
        esac
    done

    local payload=()
    payload+=(--arg p "$filepath")
    # shellcheck disable=SC2016  # $p/$c/$a/$r are jq variables, not shell variables
    local jq_expr='{path: $p'
    if [[ -n "$config" ]]; then
        payload+=(--arg c "$config")
        # shellcheck disable=SC2016
        jq_expr+=', config: $c'
    fi
    if [[ ${#extra_args[@]} -gt 0 ]]; then
        payload+=(--argjson a "$(printf '%s\n' "${extra_args[@]}" | jq -R . | jq -s .)")
        # shellcheck disable=SC2016
        jq_expr+=', args: $a'
    fi
    if [[ "$retries" -gt 0 ]]; then
        payload+=(--argjson r "$retries")
        # shellcheck disable=SC2016
        jq_expr+=', max_retries: $r'
    fi
    jq_expr+='}'

    local json
    json=$(jq -n "${payload[@]}" "$jq_expr")

    curl -s -X POST "$SMA_DAEMON_URL/webhook/generic" \
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

cmd_requeue() {
    if [[ $# -ge 1 ]]; then
        local job_id="$1"
        curl -s -X POST "$SMA_DAEMON_URL/jobs/$job_id/requeue" "${auth_headers[@]}" | jq .
    else
        curl -s -X POST "$SMA_DAEMON_URL/jobs/requeue" "${auth_headers[@]}" | jq .
    fi
}

cmd_cancel() {
    [[ $# -ge 1 ]] || die "cancel requires a job ID"
    curl -s -X POST "$SMA_DAEMON_URL/jobs/$1/cancel" "${auth_headers[@]}" | jq .
}

cmd_reload() {
    curl -s -X POST "$SMA_DAEMON_URL/reload" "${auth_headers[@]}" | jq .
}

cmd_restart() {
    curl -s -X POST "$SMA_DAEMON_URL/restart" "${auth_headers[@]}" | jq .
}

cmd_shutdown() {
    curl -s -X POST "$SMA_DAEMON_URL/shutdown" "${auth_headers[@]}" | jq .
}

# --- Main ---
[[ $# -ge 1 ]] || usage
command="$1"; shift

case "$command" in
    submit)   cmd_submit "$@" ;;
    health)   cmd_health ;;
    jobs)     cmd_jobs "$@" ;;
    job)      cmd_job "$@" ;;
    cancel)   cmd_cancel "$@" ;;
    stats)    cmd_stats ;;
    configs)  cmd_configs ;;
    cleanup)  cmd_cleanup "$@" ;;
    requeue)  cmd_requeue "$@" ;;
    reload)   cmd_reload ;;
    restart)  cmd_restart ;;
    shutdown) cmd_shutdown ;;
    help|-h|--help) usage ;;
    *)        die "Unknown command: $command" ;;
esac
