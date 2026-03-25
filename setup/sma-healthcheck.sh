#!/bin/sh
# SMA-NG Daemon Health Check
# Usage: sma-healthcheck.sh [host] [port]
# Returns exit code 0 if healthy, 1 if unhealthy
# Suitable for use with Docker HEALTHCHECK, monitoring systems, or cron

HOST="${1:-127.0.0.1}"
PORT="${2:-8585}"
URL="http://${HOST}:${PORT}/health"
TIMEOUT=5

response=$(curl -sf --max-time "$TIMEOUT" "$URL" 2>/dev/null)
if [ $? -ne 0 ]; then
    echo "UNHEALTHY: Cannot reach SMA-NG daemon at $URL"
    exit 1
fi

status=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
if [ "$status" = "ok" ]; then
    echo "HEALTHY: SMA-NG daemon responding at $URL"
    exit 0
else
    echo "UNHEALTHY: SMA-NG daemon returned status '$status'"
    exit 1
fi
