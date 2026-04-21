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
