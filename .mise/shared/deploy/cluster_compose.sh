#!/usr/bin/env bash
# Shared helpers for the cluster:* tasks. Source after lib.sh.
#
#   select_cluster_hosts        # sets DEPLOY_HOSTS from HOSTS, HOST or deploy.hosts
#   cluster_compose start started
#   cluster_compose ps "status checked"
#
# cluster_compose runs `docker compose --profile <profile> <verb>` on each
# selected host. Stopping and restarting rely on the compose
# stop_grace_period (STOP_GRACE_PERIOD, default 2m) for the daemon's graceful
# drain; jobs still running at the deadline are requeued on the next start.

select_cluster_hosts() {
  local all_hosts
  all_hosts=$(lc deploy hosts "")
  if [ -n "${HOSTS:-}" ]; then
    DEPLOY_HOSTS="$HOSTS"
  else
    DEPLOY_HOSTS="${HOST:-$all_hosts}"
  fi
  if [ -z "$DEPLOY_HOSTS" ]; then
    echo "ERROR: no hosts selected. Set HOST, HOSTS, or deploy.hosts in $LOCAL" >&2
    return 1
  fi
}

cluster_compose() {
  local verb="$1" done_label="$2" failed="" host
  select_cluster_hosts || return 1
  for host in $DEPLOY_HOSTS; do
    init_docker_host_context "$host"
    # shellcheck disable=SC2154  # profile is set by init_docker_host_context
    if [ -z "$profile" ]; then
      echo "ERROR: [$host] docker_profile is not set in $LOCAL — add it to deploy: or the host entry" >&2
      failed="$failed $host"
      continue
    fi
    echo ""
    echo "==> [$host] docker compose $verb (profile: $profile)"
    if ! run_remote_compose "$host" "--profile $profile $verb"; then
      echo "ERROR: [$host] docker compose $verb failed" >&2
      failed="$failed $host"
    fi
  done
  if [ -n "$failed" ]; then
    echo "" >&2
    echo "ERROR: cluster $verb failed for:$failed" >&2
    return 1
  fi
  echo ""
  echo "Cluster services $done_label on: $DEPLOY_HOSTS"
}
