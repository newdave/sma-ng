#!/usr/bin/env bash

TRIGGERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JSON_TOOL="${TRIGGERS_DIR}/lib/json_tools.py"

sma_init_daemon() {
    DAEMON_HOST_VALUE="${DAEMON_HOST:-127.0.0.1}"
    DAEMON_PORT_VALUE="${DAEMON_PORT:-8585}"
    DAEMON_BASE="http://${DAEMON_HOST_VALUE}:${DAEMON_PORT_VALUE}"

    AUTH_ARGS=()
    if [[ -n "${DAEMON_API_KEY:-}" ]]; then
        AUTH_ARGS=(-H "X-API-Key: ${DAEMON_API_KEY}")
    elif [[ -n "${DAEMON_USERNAME:-}" && -n "${DAEMON_PASSWORD:-}" ]]; then
        AUTH_ARGS=(--user "${DAEMON_USERNAME}:${DAEMON_PASSWORD}")
    fi
}

sma_json_get_field() {
    local json_payload="$1"
    local field="$2"
    local default_value="${3:-}"
    printf '%s' "$json_payload" | python3 "$JSON_TOOL" get --field "$field" --default "$default_value"
}

sma_build_generic_payload() {
    local path="$1"
    local config="${2:-}"
    shift 2 || true

    local cmd=(python3 "$JSON_TOOL" build-generic --path "$path")
    if [[ -n "$config" ]]; then
        cmd+=(--config "$config")
    fi

    local arg
    for arg in "$@"; do
        cmd+=("--arg=${arg}")
    done

    "${cmd[@]}"
}

sma_wait_for_job() {
    local label="$1"
    local job_id="$2"
    local poll_interval="$3"
    local timeout="$4"
    local start response status elapsed error_text

    start=$(date +%s)
    echo "[${label}] Waiting for job ${job_id} to complete (polling every ${poll_interval}s)..." >&2

    while true; do
        response=$(curl -sf "${AUTH_ARGS[@]}" "${DAEMON_BASE}/jobs/${job_id}" 2>/dev/null) || {
            echo "[${label}] ERROR: Lost contact with daemon while polling job ${job_id}." >&2
            return 1
        }

        status=$(sma_json_get_field "$response" "status" "")
        case "$status" in
            completed)
                elapsed=$(( $(date +%s) - start ))
                echo "[${label}] Job ${job_id} completed in ${elapsed}s." >&2
                return 0
                ;;
            failed)
                error_text=$(sma_json_get_field "$response" "error" "unknown")
                echo "[${label}] ERROR: Job ${job_id} failed: ${error_text}" >&2
                return 1
                ;;
            pending|running)
                if [[ "$timeout" -gt 0 ]]; then
                    elapsed=$(( $(date +%s) - start ))
                    if [[ "$elapsed" -gt "$timeout" ]]; then
                        echo "[${label}] ERROR: Timed out waiting for job ${job_id} after ${elapsed}s." >&2
                        return 1
                    fi
                fi
                sleep "$poll_interval"
                ;;
            *)
                echo "[${label}] ERROR: Unknown job status '${status}' for job ${job_id}." >&2
                return 1
                ;;
        esac
    done
}
