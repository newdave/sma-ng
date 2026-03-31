#!/bin/sh
# Startup wrapper for sma-daemon systemd unit.
# Passes --db-url when SMA_DAEMON_DB_URL is set; otherwise the daemon
# uses its built-in SQLite default.

set -e

INSTALL_DIR="${SMA_INSTALL_DIR:-/opt/sma}"
PYTHON="${INSTALL_DIR}/venv/bin/python"
DAEMON="${INSTALL_DIR}/daemon.py"

if [ -n "${SMA_DAEMON_DB_URL:-}" ]; then
    exec "${PYTHON}" "${DAEMON}" \
        --host "${SMA_DAEMON_HOST:-0.0.0.0}" \
        --port "${SMA_DAEMON_PORT:-8585}" \
        --workers "${SMA_DAEMON_WORKERS:-1}" \
        --daemon-config "${INSTALL_DIR}/config/daemon.json" \
        --logs-dir "${INSTALL_DIR}/logs" \
        --db-url "${SMA_DAEMON_DB_URL}"
else
    exec "${PYTHON}" "${DAEMON}" \
        --host "${SMA_DAEMON_HOST:-0.0.0.0}" \
        --port "${SMA_DAEMON_PORT:-8585}" \
        --workers "${SMA_DAEMON_WORKERS:-1}" \
        --daemon-config "${INSTALL_DIR}/config/daemon.json" \
        --logs-dir "${INSTALL_DIR}/logs"
fi
