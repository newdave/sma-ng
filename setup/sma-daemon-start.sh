#!/bin/sh
# Startup wrapper for sma-daemon systemd unit.
# The daemon reads SMA_DAEMON_DB_URL from the environment directly.

set -e

INSTALL_DIR="${SMA_INSTALL_DIR:-/opt/sma}"
PYTHON="${INSTALL_DIR}/venv/bin/python"
DAEMON="${INSTALL_DIR}/daemon.py"

exec "${PYTHON}" "${DAEMON}" \
    --host "${SMA_DAEMON_HOST:-0.0.0.0}" \
    --port "${SMA_DAEMON_PORT:-8585}" \
    --workers "${SMA_DAEMON_WORKERS:-1}" \
    --daemon-config "${INSTALL_DIR}/config/daemon.json" \
    --logs-dir "${INSTALL_DIR}/logs"
