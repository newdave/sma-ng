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
  if [ -n "$ffmpeg_dir" ]; then
    command_env="SMA_DAEMON_FFMPEG_DIR=$ffmpeg_dir"
  fi

  return 0
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
}

init_docker_host_context() {
  local host="$1"

  init_host_context "$host"

  use_sudo=$($cfg use_sudo "false")
  # shellcheck disable=SC2034  # profile is consumed by task scripts that source this library
  profile=$($cfg docker_profile "")
  compose_dir=$($cfg docker_compose_dir "$dir/docker")

  sma_db_url=$($cfg db_url "")
  sma_db_host=$($cfg db_host "")
  sma_db_port=$($cfg db_port "")
  sma_db_user=$($cfg db_user "")
  sma_db_password=$($cfg db_password "")
  sma_db_name=$($cfg db_name "")

  pg_env_str=""
  _append_env() {
    if [ -n "$2" ]; then
      pg_env_str="${pg_env_str}$1=$(printf '%q' "$2") "
    fi
  }
  _append_env SMA_DAEMON_DB_URL      "$sma_db_url"
  _append_env SMA_DAEMON_DB_HOST     "$sma_db_host"
  _append_env SMA_DAEMON_DB_PORT     "$sma_db_port"
  _append_env SMA_DAEMON_DB_USER     "$sma_db_user"
  _append_env SMA_DAEMON_DB_PASSWORD "$sma_db_password"
  _append_env SMA_DAEMON_DB_NAME     "$sma_db_name"

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
    docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' sma-postgres 2>/dev/null || true
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
  ssh $ssh_opts "$ssh_target" "${sudo_prefix}docker inspect --format '{{range .Mounts}}{{if .Name}}{{.Name}}{{println}}{{end}}{{end}}' sma-postgres 2>/dev/null || true"
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
