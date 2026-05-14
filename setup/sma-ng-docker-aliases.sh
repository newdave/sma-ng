# shellcheck shell=bash
# SMA-NG Docker CLI helpers.
# Source this file from Bash after the sma-ng container is running:
#   source /opt/sma/sma-ng-docker-aliases.sh

CONTAINER="${CONTAINER:-sma-ng}"
DOCKER_BIN="${DOCKER_BIN:-docker}"

_sma_exec() {
  "$DOCKER_BIN" exec -it "$CONTAINER" "$@"
}

sma_manual() {
  _sma_exec python manual.py "$@"
}

sma_convert() {
  if [ "$#" -lt 1 ]; then
    echo "usage: sma-convert /container/path/to/file.mkv [manual.py args...]" >&2
    return 2
  fi
  local input="$1"
  shift
  sma_manual -i "$input" -a "$@"
}

sma_preview() {
  if [ "$#" -lt 1 ]; then
    echo "usage: sma-preview /container/path/to/file.mkv [manual.py args...]" >&2
    return 2
  fi
  local input="$1"
  shift
  sma_manual -i "$input" -oo "$@"
}

sma_codecs() {
  sma_manual -cl "$@"
}

sma_daemon_smoke() {
  _sma_exec python daemon.py --smoke-test "$@"
}

sma_rename() {
  _sma_exec python rename.py "$@"
}

sma_logs() {
  "$DOCKER_BIN" logs -f "$CONTAINER" "$@"
}

alias sma-shell='_sma_exec bash'
alias sma-manual='sma_manual'
alias sma-convert='sma_convert'
alias sma-preview='sma_preview'
alias sma-codecs='sma_codecs'
alias sma-smoke='sma_daemon_smoke'
alias sma-rename='sma_rename'
alias sma-logs='sma_logs'
