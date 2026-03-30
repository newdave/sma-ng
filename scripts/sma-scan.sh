#!/usr/bin/env bash
#
# scripts/sma-scan.sh — Walk a directory and submit each media file to the SMA-NG daemon.
#
# Usage:
#   sma-scan.sh <directory> [options]
#
# Options:
#   --config <path>   Override autoProcess.ini for all submitted files
#   --dry-run         Print files that would be submitted without submitting
#   --delay <secs>    Seconds to wait between submissions (default: 0)
#   -h, --help        Show this help
#
# Environment variables:
#   SMA_DAEMON_URL    Base URL (default: http://127.0.0.1:8585)
#   SMA_API_KEY       API key (overrides config/daemon.json)
#
# Files with these extensions are skipped (already-converted or non-media):
#   mp4, nfo, txt, log, md, jpg, jpeg, png, xml, srt, ass, vtt, sup, py, pyc, ds_store

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WEBHOOK="$SCRIPT_DIR/../sma-webhook.sh"

# Extensions to skip — non-media files that the daemon would reject anyway.
SKIP_EXTENSIONS="mp4 nfo txt log md jpg jpeg png gif xml srt ass vtt sup mks py pyc ds_store ini json sh bat"

usage() {
    sed -n '3,17s/^# \?//p' "$0"
    exit 1
}

die() { echo "Error: $*" >&2; exit 1; }

[[ -x "$WEBHOOK" ]] || die "sma-webhook.sh not found or not executable at $WEBHOOK"

# --- Parse arguments ---
scan_dir=""
config=""
dry_run=false
delay=0

while [[ $# -gt 0 ]]; do
    case "$1" in
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

# Build a grep pattern from the skip list for fast extension filtering.
skip_pattern=$(echo "$SKIP_EXTENSIONS" | tr ' ' '\n' | sed 's/.*/\\.&$/' | paste -sd '|')

echo "Scanning: $scan_dir"
[[ "$dry_run" == true ]] && echo "(dry run — no files will be submitted)"
echo ""

submitted=0
skipped=0

while IFS= read -r -d '' filepath; do
    ext="${filepath##*.}"
    ext_lower="${ext,,}"

    if echo "$ext_lower" | grep -qE "$skip_pattern"; then
        (( skipped++ )) || true
        continue
    fi

    if [[ "$dry_run" == true ]]; then
        echo "  would submit: $filepath"
    else
        echo "  submitting: $filepath"
        submit_args=("$filepath")
        [[ -n "$config" ]] && submit_args+=(--config "$config")
        "$WEBHOOK" submit "${submit_args[@]}"
        [[ "$delay" -gt 0 ]] && sleep "$delay"
    fi
    (( submitted++ )) || true
done < <(find "$scan_dir" -type f -print0 | sort -z)

echo ""
echo "Done. Submitted: $submitted  Skipped: $skipped"
