#!/usr/bin/env bash

LOCAL="setup/.local.ini"
CFG="scripts/local-config.sh"
MISSING_PREREQ_PATTERN='(command not found|No such file or directory|not found.*make|make.*not found|python3.*not found|python.*not found|rsync.*not found|venv.*not found)'

init_host_context() {
  local host="$1"

  cfg="$CFG $LOCAL $host"
  dir=$($cfg DEPLOY_DIR ~/sma)
  port=$($cfg SSH_PORT 22)
  key=$($cfg SSH_KEY "")
  ffmpeg_dir=$($cfg FFMPEG_DIR "")
  auto_create_venv=$($cfg AUTO_CREATE_VENV true)
  venv_dir=$($cfg VENV_DIR venv)
  python_bin=$($cfg PYTHON_BIN /usr/bin/python3)
  remote_user=$(echo "$host" | cut -s -d@ -f1)
  remote_user="${remote_user:-$(whoami)}"

  ssh_opts="-p $port -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
  [ -n "$key" ] && ssh_opts="$ssh_opts -i $(eval echo "$key")"

  make_env=""
  [ -n "$ffmpeg_dir" ] && make_env="SMA_DAEMON_FFMPEG_DIR=$ffmpeg_dir"
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
    . "$host:$dir"
}

init_docker_host_context() {
  local host="$1"

  init_host_context "$host"

  use_sudo=$($cfg DEPLOY_USE_SUDO "false")
  # shellcheck disable=SC2034  # profile is consumed by task scripts that source this library
  profile=$($cfg DOCKER_PROFILE "")
  compose_dir=$($cfg DOCKER_COMPOSE_DIR "$dir/docker")

  sma_db_url=$($cfg SMA_DAEMON_DB_URL "")
  sma_db_host=$($cfg SMA_DAEMON_DB_HOST "")
  sma_db_port=$($cfg SMA_DAEMON_DB_PORT "")
  sma_db_user=$($cfg SMA_DAEMON_DB_USER "")
  sma_db_password=$($cfg SMA_DAEMON_DB_PASSWORD "")
  sma_db_name=$($cfg SMA_DAEMON_DB_NAME "")

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
  [ "$use_sudo" = "true" ] && sudo_prefix="sudo "
}

docker_profile_is_pg() {
  [[ "$1" == *-pg ]]
}

run_remote_compose() {
  local host="$1" compose_args="$2"
  # shellcheck disable=SC2029,SC2086
  ssh $ssh_opts "$host" "cd $compose_dir && ${compose_cmd} ${compose_args}"
}

wait_for_remote_container_health() {
  local host="$1" container_name="$2" timeout="${3:-120}"
  local elapsed=0 status=""

  while true; do
    # shellcheck disable=SC2029,SC2086
    status=$(ssh $ssh_opts "$host" "${sudo_prefix}docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ${container_name}" 2>/dev/null || true)
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
  ssh $ssh_opts "$host" \
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
    REQUESTED_VOLUMES="$requested_volumes" printf '%s\n' "$remote_script" | ssh $ssh_opts "$host" "cd $compose_dir && sudo env REQUESTED_VOLUMES=$(printf '%q' "$requested_volumes") bash -s"
  else
    # shellcheck disable=SC2029,SC2086
    REQUESTED_VOLUMES="$requested_volumes" printf '%s\n' "$remote_script" | ssh $ssh_opts "$host" "cd $compose_dir && env REQUESTED_VOLUMES=$(printf '%q' "$requested_volumes") bash -s"
  fi
}

capture_remote_pg_volume_names() {
  local host="$1"
  # shellcheck disable=SC2029,SC2086
  ssh $ssh_opts "$host" "${sudo_prefix}docker inspect --format '{{range .Mounts}}{{if .Name}}{{.Name}}{{println}}{{end}}{{end}}' sma-postgres 2>/dev/null || true"
}

run_remote_command() {
  local host="$1" command="$2"
  # shellcheck disable=SC2086
  ssh $ssh_opts "$host" "cd $dir && PATH=/usr/bin:/usr/local/bin:\$PATH $make_env $command"
}

run_remote_make() {
  local host="$1" target="$2"
  run_remote_command "$host" "make $target VENV=$venv_dir PYTHON=$python_bin"
}

venv_healthy() {
  local host="$1"
  run_remote_command "$host" "test -x $venv_dir/bin/python && test -x $venv_dir/bin/pip && $venv_dir/bin/python -c 'import encodings' >/dev/null 2>&1"
}

recreate_venv() {
  local host="$1"
  run_remote_command "$host" "rm -rf $venv_dir && make venv VENV=$venv_dir PYTHON=$python_bin"
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
