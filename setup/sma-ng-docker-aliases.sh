# shellcheck shell=bash
# DEPRECATED: thin shim over the `sma` host command. Removed in a later release.
# Install `sma` instead (no sourcing needed; see docs/sma-cli-quickstart.md):
#   curl -fsSL https://raw.githubusercontent.com/newdave/sma-ng/main/install-smang.sh | sh

printf '%s\n' "sma-ng-docker-aliases.sh is deprecated: use the 'sma' command (sma convert, sma repair, ...)" >&2

_sma_cmd() {
  if command -v sma >/dev/null 2>&1; then
    command sma "$@"
  else
    echo "sma: host command not installed; see docs/sma-cli-quickstart.md" >&2
    return 3
  fi
}

_sma_gone() {
  echo "sma: '$1' was removed; $2" >&2
  return 2
}

sma_manual() { _sma_gone sma-manual "use sma convert, sma preview or sma audit"; }
sma_convert() { _sma_cmd convert "$@"; }
sma_preview() { _sma_cmd preview "$@"; }
sma_codecs() { _sma_cmd codecs "$@"; }
sma_daemon_smoke() { _sma_gone sma-smoke "run daemon.py --smoke-test inside the daemon container"; }
sma_rename() { _sma_cmd rename "$@"; }
sma_repair() { _sma_cmd repair "$@"; }
sma_logs() { _sma_gone sma-logs "use docker logs -f sma-ng"; }
sma_shell() { _sma_cmd shell "$@"; }

alias sma-shell='sma_shell'
alias sma-manual='sma_manual'
alias sma-convert='sma_convert'
alias sma-preview='sma_preview'
alias sma-codecs='sma_codecs'
alias sma-smoke='sma_daemon_smoke'
alias sma-rename='sma_rename'
alias sma-repair='sma_repair'
alias sma-logs='sma_logs'
