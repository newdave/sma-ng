#!/bin/sh
# scripts/local-config.sh — read a key from an INI-style .local file.
#
# Usage: local-config.sh <file> <section> <key> [default]
#
# Values are resolved in two passes:
#   1. [deploy] section — provides project-wide defaults
#   2. [<section>]      — host-specific overrides win over [deploy]
#
# Lines beginning with # or ; are comments.  Inline comments are not
# supported (everything after the = is the value, trimmed of leading/
# trailing whitespace).

set -e

FILE="${1:-}"
SECTION="${2:-}"
KEY="${3:-}"
DEFAULT="${4:-}"

if [ -z "$FILE" ] || [ -z "$SECTION" ] || [ -z "$KEY" ]; then
    echo "Usage: $0 <file> <section> <key> [default]" >&2
    exit 1
fi

if [ ! -f "$FILE" ]; then
    printf '%s' "$DEFAULT"
    exit 0
fi

awk -v target_sec="$SECTION" -v key="$KEY" -v default_value="$DEFAULT" '
BEGIN {
    cur_sec = ""
    deploy_val = ""
    host_val   = ""
    found_host = 0
}

# Strip leading/trailing whitespace from the whole line
{ gsub(/^[[:space:]]+|[[:space:]]+$/, "") }

# Skip blank lines and comment lines
/^$/ || /^[#;]/ { next }

# Section header
/^\[/ {
    # Strip brackets and whitespace
    cur_sec = $0
    gsub(/^\[|][[:space:]]*$/, "", cur_sec)
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", cur_sec)
    next
}

# Key = value line
/=/ {
    eq = index($0, "=")
    k  = substr($0, 1, eq - 1)
    v  = substr($0, eq + 1)
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", k)
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", v)

    if (k != key) next

    if (cur_sec == "deploy") {
        deploy_val = v
    }
    if (cur_sec == target_sec) {
        host_val   = v
        found_host = 1
    }
}

END {
    if (found_host)      print host_val
    else if (deploy_val != "") print deploy_val
    else                 print default_value
}
' "$FILE"
