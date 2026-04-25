#!/usr/bin/env bash
#
# scripts/sma-scan.sh — Walk a directory and submit each media file to the SMA-NG daemon.
#
# Usage:
#   sma-scan.sh <directory> [options]
#
# Options:
#   --reset           Re-submit all files, ignoring scan history
#   --config <path>   Override autoProcess.ini for all submitted files
#   --dry-run         Print files that would be submitted without submitting
#   --delay <secs>    Seconds to wait between submissions (default: 0)
#   -h, --help        Show this help
#
# Scan state is stored in the daemon's database (scanned_files table).
# Files already recorded there are skipped on subsequent runs.
# Use --reset to ignore history and resubmit everything.
#
# Environment variables:
#   SMA_DAEMON_URL    Base URL (default: http://127.0.0.1:8585)
#   SMA_API_KEY       API key (overrides config/sma-ng.yml)
#
# Files with these extensions are skipped (already-converted or non-media):
#   mp4, nfo, txt, log, md, jpg, jpeg, png, gif, xml, srt, ass, vtt, sup, py, pyc, ds_store

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WEBHOOK="$SCRIPT_DIR/../sma-webhook.sh"

# Extensions to skip — non-media files that the daemon would reject anyway.
SKIP_EXTENSIONS="mp4 nfo txt log md jpg jpeg png gif xml srt ass vtt sup mks py pyc ds_store ini json sh bat"

usage() {
    sed -n '3,21s/^# \?//p' "$0"
    exit 1
}

die() { echo "Error: $*" >&2; exit 1; }

[[ -x "$WEBHOOK" ]] || die "sma-webhook.sh not found or not executable at $WEBHOOK"

: "${SMA_DAEMON_URL:=http://127.0.0.1:8585}"

# --- Parse arguments ---
scan_dir=""
config=""
dry_run=false
reset=false
delay=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --reset)
            reset=true; shift ;;
        --config)
            [[ $# -ge 2 ]] || die "--config requires a value"
            config="$2"; shift 2 ;;
        --dry-run)
            dry_run=true; shift ;;
        --delay)
            [[ $# -ge 2 ]] || die "--delay requires a value"
            delay="$2"; shift 2 ;;
        -h|--help)
            usage ;;
        -*)
            die "Unknown option: $1" ;;
        *)
            [[ -z "$scan_dir" ]] || die "Unexpected argument: $1"
            scan_dir="$1"; shift ;;
    esac
done

[[ -n "$scan_dir" ]] || usage
[[ -d "$scan_dir" ]] || die "Not a directory: $scan_dir"

scan_dir="$(cd "$scan_dir" && pwd)"  # canonicalise

# --- Auth headers ---
DAEMON_CONFIG="$SCRIPT_DIR/../config/sma-ng.yml"
if [[ -z "${SMA_API_KEY:-}" && -f "$DAEMON_CONFIG" ]]; then
    SMA_API_KEY=$(python3 "$SCRIPT_DIR/local-config.py" "$DAEMON_CONFIG" daemon api_key 2>/dev/null || true)
fi
: "${SMA_API_KEY:=}"
auth_headers=()
[[ -n "$SMA_API_KEY" ]] && auth_headers=(-H "X-API-Key: $SMA_API_KEY")

# Build a grep pattern from the skip list for fast extension filtering.
skip_pattern=$(echo "$SKIP_EXTENSIONS" | tr ' ' '\n' | sed 's/.*/\\.&$/' | paste -sd '|')

echo "Scanning: $scan_dir"
[[ "$dry_run" == true ]] && echo "(dry run — no files will be submitted)"

# --- Collect candidate files (extension filter only) ---
candidates=()
skipped_ext=0
while IFS= read -r -d '' filepath; do
    ext="${filepath##*.}"
    if echo "${ext,,}" | grep -qE "$skip_pattern"; then
        (( skipped_ext++ )) || true
    else
        candidates+=("$filepath")
    fi
done < <(find "$scan_dir" -type f -print0 | sort -z)

# --- Filter candidates through the daemon's scanned_files table ---
unscanned=()
skipped_done=0

if [[ "${#candidates[@]}" -eq 0 ]]; then
    : # nothing to do
elif [[ "$reset" == true ]]; then
    unscanned=("${candidates[@]}")
else
    # POST /scan/filter with the full candidate list as JSON.
    json=$(printf '%s\n' "${candidates[@]}" | jq -R . | jq -sc '{paths: .}')
    response=$(curl -s -X POST \
        "${auth_headers[@]+"${auth_headers[@]}"}" \
        -H "Content-Type: application/json" \
        -d "$json" \
        "$SMA_DAEMON_URL/scan/filter") || die "Failed to reach daemon at $SMA_DAEMON_URL"

    mapfile -t unscanned < <(echo "$response" | jq -r '.unscanned[]')
    skipped_done=$(( ${#candidates[@]} - ${#unscanned[@]} ))
fi

total=$(( ${#candidates[@]} + skipped_ext ))
echo "  Total files found:    $total"
echo "  Skipped (extension):  $skipped_ext"
echo "  Skipped (db):         $skipped_done"
echo "  To submit:            ${#unscanned[@]}"
echo ""

submitted=0
for filepath in "${unscanned[@]}"; do
    if [[ "$dry_run" == true ]]; then
        echo "  would submit: $filepath"
        (( submitted++ )) || true
        continue
    fi

    echo "  submitting: $filepath"
    submit_args=("$filepath")
    [[ -n "$config" ]] && submit_args+=(--config "$config")
    "$WEBHOOK" submit "${submit_args[@]}"

    # Record in the daemon db immediately after successful submission.
    curl -s -X POST \
        "${auth_headers[@]+"${auth_headers[@]}"}" \
        -H "Content-Type: application/json" \
        -d "$(jq -nc --arg p "$filepath" '{paths: [$p]}')" \
        "$SMA_DAEMON_URL/scan/record" > /dev/null

    (( submitted++ )) || true
    [[ "$delay" -gt 0 ]] && sleep "$delay"
done

echo ""
echo "Done. Submitted: $submitted"
