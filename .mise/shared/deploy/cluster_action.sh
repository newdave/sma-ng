#!/usr/bin/env bash
# Shared helper for cluster:drain / cluster:pause / cluster:resume — POSTs to
# /admin/nodes/<host>/<action> on each selected host's local daemon.
#
# The daemon writes the requested status into cluster_nodes.status (see
# resources/daemon/threads.py:_execute_command) so the dashboard reflects
# the action immediately. node_id is taken from the host key in
# setup/local.yml — that's the same value config:roll stamps into
# daemon.env as SMA_NODE_NAME.
#
# Usage: source "$(dirname "$0")/../../shared/deploy/cluster_action.sh"
#        cluster_action drain        # all hosts
#        HOST=sma-slave0 cluster_action pause
#        HOSTS="sma-slave0 sma-slave1" cluster_action resume

cluster_action() {
  local action="$1"

  local all_hosts
  all_hosts=$(lc deploy hosts "")
  local deploy_hosts
  if [ -n "${HOSTS:-}" ]; then
    deploy_hosts="$HOSTS"
  else
    deploy_hosts="${HOST:-$all_hosts}"
  fi

  if [ -z "$deploy_hosts" ]; then
    echo "ERROR: no hosts selected. Set HOST, HOSTS, or deploy.hosts in $LOCAL" >&2
    return 1
  fi

  local daemon_api_key
  daemon_api_key=$(lc daemon api_key "")

  local failed=""
  local host
  for host in $deploy_hosts; do
    init_host_context "$host"
    local daemon_port
    # shellcheck disable=SC2154  # cfg populated by init_host_context above
    daemon_port=$($cfg daemon_port 8585)
    local daemon_url="http://127.0.0.1:${daemon_port}"
    local remote_cmd
    if [ -n "$daemon_api_key" ]; then
      remote_cmd="curl -sf -X POST ${daemon_url}/admin/nodes/${host}/${action} -H 'X-API-Key: ${daemon_api_key}'"
    else
      remote_cmd="curl -sf -X POST ${daemon_url}/admin/nodes/${host}/${action}"
    fi

    echo "==> [$host] requesting ${action}"
    # shellcheck disable=SC2086,SC2029,SC2154  # ssh_opts/ssh_target populated by init_host_context; ssh_opts must word-split
    if ssh $ssh_opts "$ssh_target" "$remote_cmd" > /dev/null; then
      echo "  [$host] ${action} accepted"
    else
      echo "  ERROR: [$host] ${action} request failed" >&2
      failed="$failed $host"
    fi
  done

  if [ -n "$failed" ]; then
    echo "" >&2
    echo "ERROR: cluster ${action} failed for:$failed" >&2
    return 1
  fi
}
