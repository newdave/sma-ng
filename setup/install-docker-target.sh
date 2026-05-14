#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "${script_dir}/.." && pwd)
install_dir="${INSTALL_DIR:-/opt/sma}"
transcode_dir="${TRANSCODE_DIR:-/transcodes}"
owner="${OWNER:-${SUDO_USER:-${USER:-}}}"
snippet_path="${BASH_SNIPPET:-${install_dir}/sma-ng-docker-aliases.sh}"
config_source="${CONFIG_SOURCE:-${repo_root}/setup/sma-ng.yml.sample}"
env_source="${ENV_SOURCE:-${repo_root}/setup/daemon.env.sample}"
use_sudo="${USE_SUDO:-auto}"

usage() {
  cat <<'EOF'
Usage: setup/install-docker-target.sh

Creates host-side directories and seed files for the SMA-NG Docker Compose
container. Override paths with environment variables:

  INSTALL_DIR=/opt/sma
  TRANSCODE_DIR=/transcodes
  OWNER="$USER"
  BASH_SNIPPET=/opt/sma/sma-ng-docker-aliases.sh
  CONFIG_SOURCE=setup/sma-ng.yml.sample
  ENV_SOURCE=setup/daemon.env.sample
  USE_SUDO=auto
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

if [ -z "$owner" ]; then
  echo "ERROR: could not determine OWNER; set OWNER=user[:group]" >&2
  exit 1
fi

if [ "$use_sudo" = "false" ]; then
  as_root() { "$@"; }
elif [ "$(id -u)" -eq 0 ]; then
  as_root() { "$@"; }
else
  if ! command -v sudo >/dev/null 2>&1; then
    echo "ERROR: sudo is required when not running as root" >&2
    exit 1
  fi
  as_root() { sudo "$@"; }
fi

install_file_if_missing() {
  local source="$1" target="$2" mode="$3"

  if [ -f "$target" ]; then
    echo "  exists: $target"
    return 0
  fi

  if [ -f "$source" ]; then
    as_root install -m "$mode" "$source" "$target"
  else
    as_root install -m "$mode" /dev/null "$target"
  fi
  as_root chown "$owner" "$target"
  echo "  created: $target"
}

echo "==> Creating SMA-NG Docker host directories"
as_root mkdir -p \
  "${install_dir}/config" \
  "${install_dir}/logs" \
  "${install_dir}/cache" \
  "${install_dir}/data" \
  "${transcode_dir}/sma"

as_root chown -R "$owner" "$install_dir" "$transcode_dir"

echo "==> Seeding config files"
install_file_if_missing "$config_source" "${install_dir}/config/sma-ng.yml" 640
install_file_if_missing "$env_source" "${install_dir}/config/daemon.env" 640

echo "==> Writing Bash helper snippet"
as_root install -m 644 "${repo_root}/setup/sma-ng-docker-aliases.sh" "$snippet_path"
as_root chown "$owner" "$snippet_path"

cat <<EOF

SMA-NG Docker target is ready.

Host paths:
  config:     ${install_dir}/config
  logs:       ${install_dir}/logs
  cache:      ${install_dir}/cache
  data:       ${install_dir}/data
  transcodes: ${transcode_dir}/sma

Load CLI helpers in Bash with:
  source ${snippet_path}
EOF
