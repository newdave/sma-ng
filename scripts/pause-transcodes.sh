#!/usr/bin/env bash
# pause-transcodes.sh — graceful pause of the SMA-NG daemon, suitable for cron.
#
# Calls POST /admin/nodes/<node>/pause on the daemon's HTTP API. The worker
# pool's pause gate fires between jobs, so jobs currently in flight finish
# normally; only the *next* claim_next_job is blocked. Pair with the resume
# script (or cron entry) to unpause once the maintenance window ends.
#
# Exit codes:
#   0  pause requested; if WAIT=1 all running jobs completed; if
#      POWERCYCLE=1 the Proxmox VM reboot was successfully POSTed
#   1  configuration error (missing host, api key, proxmox token, etc.)
#   2  HTTP error talking to the daemon or to Proxmox
#   3  timed out waiting for running jobs to finish (WAIT=1 / POWERCYCLE=1
#      only); when POWERCYCLE=1 the Proxmox reboot is NOT sent on timeout
#      — that would kill in-flight ffmpeg and undo the whole point.
#
# Usage from cron (no waiting — fire-and-forget):
#   0 23 * * *  /opt/sma/scripts/pause-transcodes.sh sma-master >>/var/log/sma-pause.log 2>&1
#
# Usage from cron + wait until drained (e.g. before a backup window):
#   30 22 * * *  WAIT=1 WAIT_TIMEOUT=3600 \
#                  /opt/sma/scripts/pause-transcodes.sh sma-master >>/var/log/sma-pause.log 2>&1
#
# Usage from cron + drain + powercycle the Proxmox VM hosting the daemon
# (pair with autostart so the VM comes back up on its own):
#   30 22 * * *  WAIT=1 POWERCYCLE=1 WAIT_TIMEOUT=3600 \
#                  PROXMOX_HOST=proxmox.lan PROXMOX_NODE=pve PROXMOX_VMID=109 \
#                  PROXMOX_TOKEN='sma@pve!cron=00000000-0000-0000-0000-000000000000' \
#                  /opt/sma/scripts/pause-transcodes.sh sma-master >>/var/log/sma-pause.log 2>&1
#
# Environment — daemon side:
#   SMA_HOST           daemon hostname / IP   (overrides positional $1)
#   SMA_PORT           daemon HTTP port       (default 8585)
#   SMA_API_KEY        X-API-Key header       (required; daemon.api-key in sma-ng.yml)
#   SMA_NODE_ID        node_id to pause       (default = positional $1 or SMA_HOST)
#   WAIT               1 = poll until running=0; 0 = fire-and-forget (default 0)
#   WAIT_TIMEOUT       seconds to wait when WAIT=1   (default 1800 = 30 min)
#   WAIT_INTERVAL      poll cadence in seconds       (default 30)
#   ACTOR              X-Actor header (audit log)    (default "cron")
#
# Environment — Proxmox side (only required when POWERCYCLE=1):
#   POWERCYCLE         1 = after drain, ask Proxmox to reboot the VM
#                      hosting the daemon. Implies WAIT=1 (forced so
#                      in-flight ffmpeg children aren't orphaned).
#                      Default 0.
#   POWERCYCLE_MODE    Proxmox status action: 'reboot' (ACPI shutdown
#                      then start — guest OS gets a clean syncs+unmount)
#                      or 'reset' (hard reset, like a power button).
#                      Default 'reboot'. Use 'reset' only when the guest
#                      OS is known wedged.
#   PROXMOX_HOST       Proxmox API endpoint hostname or IP (no scheme).
#                      Port defaults to 8006 unless embedded (host:port).
#   PROXMOX_NODE       Proxmox node name hosting the VM (e.g. 'pve').
#   PROXMOX_VMID       QEMU VM ID to act on. Default 109.
#   PROXMOX_TOKEN      API token in the form 'USER@REALM!TOKENID=SECRET'.
#                      Create under Datacenter → Permissions → API Tokens
#                      with VM.PowerMgmt on /vms/<vmid> at minimum.
#   PROXMOX_INSECURE   1 = skip TLS verification (self-signed certs).
#                      Default 0.
#
# Required tools: curl, awk. Optional: jq (used when present for cleaner
# parsing; falls back to grep/awk so vanilla minimal images work).

set -euo pipefail

log() {
  printf '[%(%Y-%m-%dT%H:%M:%S%z)T] %s\n' -1 "$*"
}

die() {
  local code=$1
  shift
  log "ERROR: $*" >&2
  exit "$code"
}

# ---- config resolution ----------------------------------------------------

POSITIONAL_HOST="${1:-}"
SMA_HOST="${SMA_HOST:-${POSITIONAL_HOST}}"
SMA_PORT="${SMA_PORT:-8585}"
SMA_API_KEY="${SMA_API_KEY:-}"
SMA_NODE_ID="${SMA_NODE_ID:-${POSITIONAL_HOST:-${SMA_HOST}}}"
WAIT="${WAIT:-0}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-1800}"
WAIT_INTERVAL="${WAIT_INTERVAL:-30}"
POWERCYCLE="${POWERCYCLE:-0}"
PROXMOX_VMID="${PROXMOX_VMID:-109}"
ACTOR="${ACTOR:-cron}"

# POWERCYCLE=1 forces WAIT=1 — rebooting the VM with ffmpeg children
# still running would kill those subprocesses mid-transcode.
if [ "$POWERCYCLE" = "1" ] && [ "$WAIT" != "1" ]; then
  log "POWERCYCLE=1 implies WAIT=1; enabling wait so in-flight jobs aren't orphaned."
  WAIT=1
fi

# POWERCYCLE_MODE must be a Proxmox-recognised VM status verb. We allow
# the two that make sense here; 'shutdown'/'stop'/'start' aren't useful
# because we need the VM to come back up.
if [ "$POWERCYCLE" = "1" ]; then
  case "$POWERCYCLE_MODE" in
    reboot|reset) ;;
    *) die 1 "POWERCYCLE_MODE must be 'reboot' (graceful) or 'reset' (hard); got '${POWERCYCLE_MODE}'." ;;
  esac
  [ -z "$PROXMOX_HOST" ]  && die 1 "POWERCYCLE=1 requires PROXMOX_HOST (e.g. proxmox.lan or 10.30.0.10)."
  [ -z "$PROXMOX_NODE" ]  && die 1 "POWERCYCLE=1 requires PROXMOX_NODE (Proxmox cluster node name, e.g. 'pve')."
  [ -z "$PROXMOX_TOKEN" ] && die 1 "POWERCYCLE=1 requires PROXMOX_TOKEN ('USER@REALM!TOKENID=SECRET')."
fi

[ -z "$SMA_HOST" ] && die 1 "Usage: $0 <host> (or set SMA_HOST). See script header for env vars."
[ -z "$SMA_API_KEY" ] && die 1 "SMA_API_KEY is required (matches daemon.api-key in sma-ng.yml)."

BASE_URL="http://${SMA_HOST}:${SMA_PORT}"

# ---- helpers --------------------------------------------------------------

api_post() {
  local path=$1
  curl -sS -m 10 --fail-with-body \
    -X POST \
    -H "X-API-Key: ${SMA_API_KEY}" \
    -H "X-Actor: ${ACTOR}" \
    -H "Content-Length: 0" \
    "${BASE_URL}${path}"
}

api_get() {
  local path=$1
  curl -sS -m 10 --fail-with-body \
    -H "X-API-Key: ${SMA_API_KEY}" \
    "${BASE_URL}${path}"
}

running_count() {
  local body
  if ! body=$(api_get "/health" 2>/dev/null); then
    echo "-1"
    return
  fi
  if command -v jq >/dev/null 2>&1; then
    echo "$body" | jq -r '.jobs.running // 0'
  else
    # Fallback: grep "running": <int> out of the health JSON. Brittle but
    # works on busybox / minimal Alpine images where jq isn't installed.
    echo "$body" | awk -F'[,:}]' '/"running"/ {for (i=1;i<=NF;i++) if ($i ~ /"running"/) {gsub(/[^0-9]/,"",$(i+1)); print $(i+1); exit}}'
  fi
}

# ---- step 1: send the pause command ---------------------------------------

log "Pausing node '${SMA_NODE_ID}' on ${BASE_URL} (actor=${ACTOR})..."

# URL-encode the node id minimally (treat spaces and slashes; SMA node ids
# are typically hostnames so this is mostly defensive).
encoded_node=$(printf '%s' "$SMA_NODE_ID" | sed -e 's, ,%20,g' -e 's,/,%2F,g')

if ! response=$(api_post "/admin/nodes/${encoded_node}/pause" 2>&1); then
  die 2 "Pause request failed: ${response}"
fi
log "Pause requested: ${response}"

# ---- step 2 (optional): wait for in-flight jobs to drain ------------------

if [ "$WAIT" != "1" ]; then
  log "WAIT=0; fire-and-forget. In-flight jobs will continue to completion; no new jobs will start."
  exit 0
fi

log "Waiting up to ${WAIT_TIMEOUT}s for in-flight jobs to finish (poll every ${WAIT_INTERVAL}s)..."
deadline=$(( $(date +%s) + WAIT_TIMEOUT ))

while :; do
  remaining=$(( deadline - $(date +%s) ))
  if [ "$remaining" -le 0 ]; then
    log "Timeout: in-flight jobs did not drain within ${WAIT_TIMEOUT}s."
    exit 3
  fi
  count=$(running_count)
  case "$count" in
    -1)
      log "Health probe failed (daemon unreachable?); will retry in ${WAIT_INTERVAL}s."
      ;;
    0)
      log "All in-flight jobs completed; node is fully paused."
      break
      ;;
    ''|*[!0-9]*)
      log "Unexpected running count '${count}'; will retry in ${WAIT_INTERVAL}s."
      ;;
    *)
      log "Still ${count} job(s) running; ${remaining}s remaining before timeout."
      ;;
  esac
  sleep "$WAIT_INTERVAL"
done

# ---- step 3 (optional): powercycle the Proxmox VM hosting the daemon ----

if [ "$POWERCYCLE" != "1" ]; then
  exit 0
else
  log "POWERCYCLE=1: sending ${POWERCYCLE_MODE} command to Proxmox VM ${PROXMOX_VMID} on node '${PROXMOX_NODE}' (${PROXMOX_HOST})..."
  pvesh create /nodes/localhost/qemu/"${PROXMOX_VMID}"/stop
  pvesh create /nodes/localhost/qemu/"${PROXMOX_VMID}"/start
  log "VM ${PROXMOX_VMID} is powercycling. Daemon HTTP will be unreachable until the guest finishes booting and the sma-ng container starts."
fi

log "Exiting..."
exit 0
