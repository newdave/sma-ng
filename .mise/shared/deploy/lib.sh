#!/usr/bin/env bash

LOCAL="setup/local.yml"
CFG="python3 scripts/local-config.py"
# lc() is a shorthand for querying local.yml without needing to handle $CFG quoting.
lc() { python3 scripts/local-config.py "$LOCAL" "$@"; }
MISSING_PREREQ_PATTERN='(command not found|No such file or directory|mise.*not found|python3.*not found|python.*not found|rsync.*not found|venv.*not found)'

mk_ssh_opts() {
  local port="$1" key="$2"
  SSH_OPTS=(-p "$port" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)
  [ -n "$key" ] && SSH_OPTS+=(-i "$(eval echo "$key")")
}

init_host_context() {
  local host="$1"

  cfg="$CFG $LOCAL $host"
  dir=$($cfg deploy_dir ~/sma)
  port=$($cfg ssh_port 22)
  key=$($cfg ssh_key "")
  # shellcheck disable=SC2034  # ffmpeg_dir is consumed by task scripts that source this library
  ffmpeg_dir=$($cfg ffmpeg_dir "")
  auto_create_venv=$($cfg auto_create_venv true)
  venv_dir=$($cfg venv_dir venv)
  python_bin=$($cfg python_bin /usr/bin/python3)
  remote_user=$($cfg user "")
  remote_user="${remote_user:-$(whoami)}"
  remote_address=$($cfg address "$host")
  ssh_target="${remote_user}@${remote_address}"

  ssh_opts="-p $port -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
  if [ -n "$key" ]; then
    ssh_opts="$ssh_opts -i $(eval echo "$key")"
  fi

  command_env=""

  return 0
}

# Idempotently create the remote deploy_dir (e.g. /opt/sma) when it does not
# already exist. Uses sudo when deploy.use_sudo is true, then chowns the
# directory to the SSH user so subsequent rsync/mkdir calls (which run as
# the SSH user) can write inside it without further escalation. A no-op when
# the directory already exists. Requires init_host_context to have run first
# so cfg/ssh_opts/ssh_target/dir are populated.
ensure_remote_deploy_dir() {
  local host="$1"
  # shellcheck disable=SC2154  # cfg/dir populated by init_host_context above
  local host_use_sudo
  host_use_sudo=$($cfg use_sudo "false")
  # shellcheck disable=SC2086,SC2029,SC2154  # ssh_opts must word-split for ssh; remote command intentionally expands client-side
  if ssh $ssh_opts "$ssh_target" "test -d $dir"; then
    return 0
  fi
  echo "==> [$host] creating deploy_dir $dir"
  if [ "$host_use_sudo" = "true" ]; then
    # shellcheck disable=SC2086,SC2029,SC2154  # ssh_opts must word-split; dir/remote_user expand client-side intentionally
    ssh $ssh_opts "$ssh_target" "sudo mkdir -p $dir && sudo chown ${remote_user}: $dir"
  else
    # shellcheck disable=SC2086,SC2029  # ssh_opts must word-split; dir expands client-side intentionally
    ssh $ssh_opts "$ssh_target" "mkdir -p $dir"
  fi
}

sync_codebase_to_host() {
  local host="$1" rsync_extra="${2:-}"

  echo "==> [$host] syncing to $dir"
  # shellcheck disable=SC2086
  rsync -az --delete \
    -e "ssh $ssh_opts" \
    --exclude='.git/' \
    --exclude='venv/' \
    --exclude='config/' \
    --exclude='logs/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.local' \
    --exclude='*.egg-info/' \
    $rsync_extra \
    . "$ssh_target:$dir"

  chown_remote_path_to_ssh_user "$host" "$dir"
}

# Recursively reassign ownership of a path on the remote host to the SSH
# user. Only fires when deploy.use_sudo is true (the only mode where files
# can end up root-owned after sudo-managed steps); otherwise the SSH user
# already owns everything they touched and the call is a silent no-op.
# Requires init_host_context (or equivalent) to have populated ssh_opts,
# ssh_target, and remote_user for the host.
chown_remote_path_to_ssh_user() {
  local host="$1" target_path="$2"
  local host_use_sudo
  # use_sudo may not be set by every caller — re-query the local config to
  # avoid surprises when this helper is invoked outside init_docker_host_context.
  host_use_sudo=$(lc deploy use_sudo "false")
  if [ "$host_use_sudo" != "true" ]; then
    return 0
  fi
  # shellcheck disable=SC2086,SC2029,SC2154  # ssh_opts/ssh_target/remote_user populated by the caller
  ssh $ssh_opts "$ssh_target" "sudo chown -R ${remote_user}: ${target_path}" \
    || echo "  WARNING: [$host] chown -R ${remote_user}: ${target_path} failed (continuing)" >&2
}

# Idempotently create the persistent ${sma_install_dir}/{config,logs,data}
# directories on the remote host, owned by the SSH user. These are the
# bind-mount targets for the SMA Docker container; if Docker creates them
# implicitly via compose volumes they end up root-owned and the daemon
# can't write to them. Sudo is used when deploy.use_sudo is true. A no-op
# when all directories already exist.
# Requires init_host_context to have populated cfg/ssh_opts/ssh_target/
# remote_user for the host.
ensure_remote_install_dirs() {
  local host="$1"
  local install_dir
  # shellcheck disable=SC2154  # cfg populated by init_host_context above
  install_dir=$($cfg sma_install_dir "/opt/sma")
  local host_use_sudo
  host_use_sudo=$($cfg use_sudo "false")
  # shellcheck disable=SC2086,SC2029,SC2154  # ssh_opts must word-split for ssh
  if ssh $ssh_opts "$ssh_target" "test -d ${install_dir}/config && test -d ${install_dir}/logs && test -d ${install_dir}/data"; then
    return 0
  fi
  echo "  creating ${install_dir}/{config,logs,data} (owner: ${remote_user})"
  if [ "$host_use_sudo" = "true" ]; then
    # shellcheck disable=SC2086,SC2029,SC2154  # ssh_opts must word-split; remote command intentionally expands client-side
    ssh $ssh_opts "$ssh_target" "sudo mkdir -p ${install_dir}/config ${install_dir}/logs ${install_dir}/data && sudo chown ${remote_user}: ${install_dir} ${install_dir}/config ${install_dir}/logs ${install_dir}/data"
  else
    # shellcheck disable=SC2086,SC2029  # ssh_opts must word-split; remote command intentionally expands client-side
    ssh $ssh_opts "$ssh_target" "mkdir -p ${install_dir}/config ${install_dir}/logs ${install_dir}/data"
  fi
}

init_docker_host_context() {
  local host="$1"

  init_host_context "$host"

  use_sudo=$($cfg use_sudo "false")
  # shellcheck disable=SC2034  # profile is consumed by task scripts that source this library
  profile=$($cfg docker_profile "")
  compose_dir=$($cfg docker_compose_dir "$dir/docker")

  sma_image="${IMAGE:-$($cfg image "")}"
  sma_image_tag="${IMAGE_TAG:-$($cfg image_tag "")}"

  pg_env_str=""
  _append_env() {
    if [ -n "$2" ]; then
      pg_env_str="${pg_env_str}$1=$(printf '%q' "$2") "
    fi
  }
  _append_env IMAGE              "$sma_image"
  _append_env IMAGE_TAG          "$sma_image_tag"

  if [ "$use_sudo" = "true" ] && [ -n "$pg_env_str" ]; then
    compose_cmd="sudo env ${pg_env_str}docker compose"
  elif [ "$use_sudo" = "true" ]; then
    compose_cmd="sudo docker compose"
  else
    compose_cmd="${pg_env_str}docker compose"
  fi

  sudo_prefix=""
  if [ "$use_sudo" = "true" ]; then
    sudo_prefix="sudo "
  fi

  return 0
}

docker_profile_is_pg() {
  [[ "$1" == *-pg ]]
}

# Stop and remove any stale postgres container left over from earlier
# deploys when the container_name differed from today's canonical
# `sma-pgsql`. Historically the container was named `sma-postgres`
# while the compose service stayed `sma-pgsql`, leaving lib.sh helpers
# unable to find the running container by name and (more importantly)
# blocking the next recreate because port 5432 was still bound.
#
# Safe: only `docker rm -f` is run, never `docker volume rm`, so the
# named `sma-pgdata` volume keeps the database files. The new
# sma-pgsql container re-mounts the same volume and picks up the
# existing data on first start.
#
# Same SSH context requirements as ensure_remote_docker; respects
# deploy.use_sudo via the caller-populated sudo_prefix.
remove_stale_pg_containers() {
  local host="$1" active_name="${2:-sma-pgsql}"
  local known_names=(sma-postgres sma-pgsql)
  local stale=()
  for name in "${known_names[@]}"; do
    if [ "$name" = "$active_name" ]; then
      continue
    fi
    stale+=("$name")
  done
  if [ ${#stale[@]} -eq 0 ]; then
    return 0
  fi
  # shellcheck disable=SC2086,SC2029,SC2154  # ssh_opts must word-split for ssh; remote command intentionally expands client-side
  ssh $ssh_opts "$ssh_target" "bash -s" "${sudo_prefix:-}" "${stale[@]}" <<'REMOTE'
    set -euo pipefail
    sudo_prefix="$1"
    shift
    _run() {
      if [ -n "$sudo_prefix" ]; then
        sudo "$@"
      else
        "$@"
      fi
    }
    for name in "$@"; do
      if _run docker inspect "$name" >/dev/null 2>&1; then
        echo "  removing stale postgres container: $name (volume sma-pgdata preserved)"
        _run docker rm -f "$name" >/dev/null
      fi
    done
REMOTE
}

# Stop and remove any legacy SMA daemon containers whose container_name
# pre-dates the standardization on a single canonical `sma-ng` name.
# Earlier compose configs gave each profile its own container_name
# (sma-ng-{software,intel,nvidia}{,-pg}), so switching a host between
# profiles left the previous container running and `compose up -d
# sma-<new-profile>` failed with a port-bind conflict on 8585.
#
# With every variant now sharing container_name: sma-ng, compose's
# --force-recreate handles the in-profile case automatically. This
# helper only exists to clean up the legacy names on hosts that still
# carry them. Bounded to the historical SMA-owned names — never
# touches arbitrary user containers; sma-pgsql is preserved.
#
# Same SSH context requirements as ensure_remote_docker; respects
# deploy.use_sudo via the caller-populated sudo_prefix.
remove_stale_sma_containers() {
  local host="$1"
  local stale=(sma-ng-software sma-ng-software-pg sma-ng-intel sma-ng-intel-pg sma-ng-nvidia sma-ng-nvidia-pg)
  # shellcheck disable=SC2086,SC2029,SC2154  # ssh_opts must word-split for ssh; remote command intentionally expands client-side
  ssh $ssh_opts "$ssh_target" "bash -s" "${sudo_prefix:-}" "${stale[@]}" <<'REMOTE'
    set -euo pipefail
    sudo_prefix="$1"
    shift
    _run() {
      if [ -n "$sudo_prefix" ]; then
        sudo "$@"
      else
        "$@"
      fi
    }
    for name in "$@"; do
      if _run docker inspect "$name" >/dev/null 2>&1; then
        echo "  removing stale container: $name"
        _run docker rm -f "$name" >/dev/null
      fi
    done
REMOTE
}

run_remote_compose() {
  local host="$1" compose_args="$2"
  # shellcheck disable=SC2029,SC2086
  ssh $ssh_opts "$ssh_target" "cd $compose_dir && ${compose_cmd} ${compose_args}"
}

wait_for_remote_container_health() {
  local host="$1" container_name="$2" timeout="${3:-120}"
  local elapsed=0 status=""

  while true; do
    # shellcheck disable=SC2029,SC2086
    status=$(ssh $ssh_opts "$ssh_target" "${sudo_prefix}docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ${container_name}" 2>/dev/null || true)
    if [ "$status" = "healthy" ] || [ "$status" = "running" ]; then
      return 0
    fi
    if [ "$elapsed" -ge "$timeout" ]; then
      return 1
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done
}

print_remote_container_summary() {
  local host="$1" name_filter="$2"
  # shellcheck disable=SC2029,SC2086
  ssh $ssh_opts "$ssh_target" \
    "${sudo_prefix}docker ps --filter name=${name_filter} --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'"
}

remove_remote_pg_volume() {
  local host="$1" requested_volumes="${2:-}"
  local remote_script
  remote_script=$(cat <<'EOF'
set -euo pipefail

requested_volumes="${REQUESTED_VOLUMES:-}"

if [ -n "$requested_volumes" ]; then
  volumes="$requested_volumes"
else
  project_name=$(
    docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' sma-pgsql 2>/dev/null || true
  )

  if [ -z "$project_name" ]; then
    project_name=$(basename "$PWD")
  fi

  volumes=$(docker volume ls -q \
    --filter "label=com.docker.compose.project=${project_name}" \
    --filter "label=com.docker.compose.volume=sma-pgdata")
fi

if [ -z "$volumes" ]; then
  echo "  no compose-managed sma-pgdata volume found"
  exit 0
fi

echo "  removing postgres volume(s):"
printf '    %s\n' $volumes
docker volume rm $volumes
EOF
)

if [ "$use_sudo" = "true" ]; then
    # shellcheck disable=SC2029,SC2086
    REQUESTED_VOLUMES="$requested_volumes" printf '%s\n' "$remote_script" | ssh $ssh_opts "$ssh_target" "cd $compose_dir && sudo env REQUESTED_VOLUMES=$(printf '%q' "$requested_volumes") bash -s"
  else
    # shellcheck disable=SC2029,SC2086
    REQUESTED_VOLUMES="$requested_volumes" printf '%s\n' "$remote_script" | ssh $ssh_opts "$ssh_target" "cd $compose_dir && env REQUESTED_VOLUMES=$(printf '%q' "$requested_volumes") bash -s"
  fi
}

capture_remote_pg_volume_names() {
  local host="$1"
  # shellcheck disable=SC2029,SC2086
  ssh $ssh_opts "$ssh_target" "${sudo_prefix}docker inspect --format '{{range .Mounts}}{{if .Name}}{{.Name}}{{println}}{{end}}{{end}}' sma-pgsql 2>/dev/null || true"
}

run_remote_command() {
  local host="$1" command="$2"
  # shellcheck disable=SC2029,SC2086  # dir/command_env/command expand locally so the remote shell receives the composed command.
  ssh $ssh_opts "$ssh_target" "cd $dir && PATH=/usr/bin:/usr/local/bin:\$PATH $command_env $command"
}

run_remote_mise_task() {
  local host="$1" task="$2"
  run_remote_command "$host" "mise run $task"
}

install_remote_base_deps() {
  local host="$1"
  run_remote_command "$host" "$venv_dir/bin/pip install -r setup/requirements.txt"
}

venv_healthy() {
  local host="$1"
  run_remote_command "$host" "test -x $venv_dir/bin/python && test -x $venv_dir/bin/pip && $venv_dir/bin/python scripts/check_python_encodings.py >/dev/null 2>&1"
}

recreate_venv() {
  local host="$1"
  run_remote_command "$host" "rm -rf $venv_dir && $python_bin -m venv $venv_dir && chmod 755 $venv_dir $venv_dir/bin || true && chmod 755 $venv_dir/bin/python $venv_dir/bin/python3 $venv_dir/bin/python3.* 2>/dev/null || true && $venv_dir/bin/pip install --upgrade pip"
}

run_with_prereq_retry() {
  local host="$1" warning="$2" retry_label="$3" fn_name="$4"
  local errfile
  errfile=$(mktemp)

  if ! "$fn_name" "$host" 2> >(tee "$errfile" >&2); then
    if grep -qE "$MISSING_PREREQ_PATTERN" "$errfile"; then
      echo ""
      echo "  WARNING: [$host] $warning"
      HOST="$host" mise run deploy:setup
      echo ""
      [ -n "$retry_label" ] && echo "==> [$host] retrying: $retry_label"
      if ! "$fn_name" "$host"; then
        rm -f "$errfile"
        return 1
      fi
    else
      rm -f "$errfile"
      return 1
    fi
  fi

  rm -f "$errfile"
}

ensure_venv_ready() {
  local host="$1"
  if [ "$auto_create_venv" = "true" ] && ! venv_healthy "$host" >/dev/null 2>&1; then
    echo "==> [$host] virtualenv missing or unhealthy ($venv_dir), recreating with $python_bin"
    run_with_prereq_retry "$host" "pre-req missing — running deploy:setup then retrying venv recreation" "" recreate_venv
  fi
}

# Idempotently install Docker (Engine + Compose plugin + buildx) on a remote
# host via the official Docker apt repo when `docker` is not on PATH. Returns
# 0 if Docker is already installed or installs successfully; 1 on failure.
# Requires `init_host_context` (or equivalent) to have run first so ssh_opts
# and ssh_target are populated for this host. Each command inside the install
# heredoc carries an explicit `sudo` so the helper works regardless of how
# the calling task invokes the remote shell.
ensure_remote_docker() {
  local host="$1"
  # shellcheck disable=SC2086,SC2029,SC2154  # ssh_opts/ssh_target populated by the caller
  if ssh $ssh_opts "$ssh_target" "command -v docker >/dev/null 2>&1"; then
    return 0
  fi
  echo "==> [$host] docker not found — installing via the official Docker apt repo"
  # shellcheck disable=SC2086,SC2029  # ssh_opts must word-split for ssh
  ssh $ssh_opts "$ssh_target" "bash -s" <<'REMOTE'
    set -euo pipefail
    if ! command -v apt-get >/dev/null 2>&1; then
      echo '  ERROR: apt-get not found — cannot install Docker on this host' >&2
      exit 1
    fi
    sudo apt-get install -y ca-certificates curl
    sudo install -m 0755 -d /etc/apt/keyrings
    . /etc/os-release
    sudo curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME:-${UBUNTU_CODENAME}} stable" \
      | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
REMOTE
}

# Idempotently install mise on a remote host via the official install script
# (https://mise.run). Symlinks ~/.local/bin/mise to /usr/local/bin/mise so the
# binary is available to non-interactive SSH sessions without requiring shell
# init (.bashrc/.zshrc). Returns 0 if mise is already installed or installs
# successfully; 1 on failure. Same SSH context requirements as
# ensure_remote_docker.
ensure_remote_mise() {
  local host="$1"
  # shellcheck disable=SC2086,SC2029,SC2154  # ssh_opts/ssh_target populated by the caller
  if ssh $ssh_opts "$ssh_target" "command -v mise >/dev/null 2>&1 || test -x \$HOME/.local/bin/mise"; then
    echo "==> [$host] mise already installed"
    # shellcheck disable=SC2086,SC2029  # ssh_opts must word-split for ssh
    ssh $ssh_opts "$ssh_target" "command -v mise >/dev/null 2>&1 || sudo ln -sf \$HOME/.local/bin/mise /usr/local/bin/mise"
    return 0
  fi
  echo "==> [$host] mise not found — installing via https://mise.run"
  # shellcheck disable=SC2086,SC2029  # ssh_opts must word-split for ssh
  ssh $ssh_opts "$ssh_target" "bash -s" <<'REMOTE'
    set -euo pipefail
    if ! command -v curl >/dev/null 2>&1; then
      sudo apt-get install -y curl
    fi
    curl -fsSL https://mise.run | sh
    if [ -x "$HOME/.local/bin/mise" ] && ! command -v mise >/dev/null 2>&1; then
      sudo ln -sf "$HOME/.local/bin/mise" /usr/local/bin/mise
    fi
    /usr/local/bin/mise --version 2>/dev/null || "$HOME/.local/bin/mise" --version
REMOTE
}

# Ensure ruamel.yaml is importable by python3 on a remote host. The deploy
# stampers (stamp_daemon.py / stamp_ffmpeg.py / stamp_postprocess.py) all
# import it. Tries the project venv first; falls back to apt-installing the
# distro package (python3-ruamel.yaml on Debian/Ubuntu) so the system python3
# satisfies the import even when the venv has not yet been created.
# Same SSH context requirements as ensure_remote_docker.
ensure_remote_python_deps() {
  local host="$1" dir="$2"
  local probe="$dir/.mise/shared/deploy/lib/probe_ruamel.py"
  # shellcheck disable=SC2086,SC2029,SC2154  # ssh_opts/ssh_target populated by the caller
  if ssh $ssh_opts "$ssh_target" "[ -x $dir/venv/bin/python3 ] && $dir/venv/bin/python3 $probe 2>/dev/null"; then
    return 0
  fi
  # shellcheck disable=SC2086,SC2029
  if ssh $ssh_opts "$ssh_target" "command -v python3 >/dev/null 2>&1 && python3 $probe 2>/dev/null"; then
    return 0
  fi
  echo "==> [$host] installing python3-ruamel.yaml so the deploy stampers can run"
  # shellcheck disable=SC2086,SC2029
  ssh $ssh_opts "$ssh_target" "bash -s" <<'REMOTE'
    set -euo pipefail
    if ! command -v apt-get >/dev/null 2>&1; then
      echo '  ERROR: apt-get not found — install ruamel.yaml manually on this host' >&2
      exit 1
    fi
    sudo apt-get install -y python3-ruamel.yaml
REMOTE
}
