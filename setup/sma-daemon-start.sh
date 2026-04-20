#!/bin/sh
# Startup wrapper for sma-daemon systemd unit.
# The daemon reads SMA_DAEMON_DB_URL from the environment directly.

set -e

INSTALL_DIR="${SMA_INSTALL_DIR:-/opt/sma}"
PYTHON="${INSTALL_DIR}/venv/bin/python"
DAEMON="${INSTALL_DIR}/daemon.py"

if [ ! -x "${PYTHON}" ]; then
    echo "ERROR: ${PYTHON} is missing or not executable." >&2
    # Best-effort repair for deployments where execute bits were lost.
    chmod 755 "${INSTALL_DIR}/venv" "${INSTALL_DIR}/venv/bin" 2>/dev/null || true
    chmod 755 "${INSTALL_DIR}"/venv/bin/python "${INSTALL_DIR}"/venv/bin/python3 "${INSTALL_DIR}"/venv/bin/python3.* 2>/dev/null || true
fi

if [ ! -x "${PYTHON}" ]; then
    echo "ERROR: unable to execute ${PYTHON}. Recreate venv and reinstall dependencies (for example: 'cd ${INSTALL_DIR} && make install')." >&2
    exit 126
fi

exec "${PYTHON}" "${DAEMON}" \
    --host "${SMA_DAEMON_HOST:-0.0.0.0}" \
    --port "${SMA_DAEMON_PORT:-8585}" \
    --workers "${SMA_DAEMON_WORKERS:-1}" \
    --daemon-config "${INSTALL_DIR}/config/daemon.json" \
    --logs-dir "${INSTALL_DIR}/logs"
