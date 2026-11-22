#!/usr/bin/env bash
# Shared implementation of `mise run cluster -- reload`. Source after lib.sh and cluster_compose.sh.
#
# Hot-reload counterpart to `cluster restart`. Use after `mise run deploy:config` when the change is
# hot-reloadable (api_key, scan_paths, audit settings, routing rules, codec lists, profile overlays).
# Worker count and API server bind host/port still require `cluster restart`.

cluster_reload() {
  local daemon_api_key failed ok host daemon_port daemon_url remote_reload response
  select_cluster_hosts
  daemon_api_key=$(lc daemon api_key "")

  failed=""
  ok=""
  for host in $DEPLOY_HOSTS; do
    init_docker_host_context "$host"
    # shellcheck disable=SC2154  # cfg/ssh_opts/ssh_target populated by init_docker_host_context above
    daemon_port=$($cfg daemon_port 8585)

    daemon_url="http://127.0.0.1:${daemon_port}"
    if [ -n "$daemon_api_key" ]; then
      remote_reload="curl -sf -X POST ${daemon_url}/reload -H 'X-API-Key: ${daemon_api_key}'"
    else
      remote_reload="curl -sf -X POST ${daemon_url}/reload"
    fi

    echo "==> [$host] POST /reload"
    # shellcheck disable=SC2086,SC2029,SC2154  # ssh_opts must word-split for ssh; remote command intentionally expands client-side
    if response=$(ssh $ssh_opts "$ssh_target" "$remote_reload" 2>&1); then
      echo "  [$host] reloaded${response:+: }${response}"
      ok="$ok $host"
    else
      echo "  [$host] reload failed (daemon down or unreachable): ${response:-no response}" >&2
      failed="$failed $host"
    fi
  done

  echo ""
  if [ -n "$ok" ]; then
    echo "Reloaded:$ok"
  fi
  if [ -n "$failed" ]; then
    echo "Failed: $failed" >&2
    echo "Run \`mise run cluster restart\` for hosts whose daemon isn't responding to /reload." >&2
    return 1
  fi
}
