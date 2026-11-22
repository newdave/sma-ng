# Cluster Operations

Deployment patterns and operator runbooks for running SMA-NG on one host with several daemons, or as a cluster of
nodes managed remotely via `mise` and `docker compose`. Each runbook is a self-contained scenario — the steps to
run when something happens.

For the underlying *reference* material see:

- [Deployment](deployment.md) — every mise task in detail
- [Daemon Mode](daemon.md) — cluster-mode internals (heartbeats, approval flow,
  job queue, admin API)

---

## Cluster shape

```mermaid
graph LR
    O[Operator workstation<br/>mise run …] -->|ssh| M[sma-master<br/>sma daemon]
    O -->|ssh| S0[sma-slave0<br/>sma daemon]
    O -->|ssh| S1[sma-slave1<br/>sma daemon]
    M -->|SQL| P[(PostgreSQL<br/>jobs, cluster_nodes,<br/>node_commands, …)]
    S0 -->|SQL| P
    S1 -->|SQL| P
    M -->|webhook| EXT[Sonarr / Radarr<br/>Plex / Autoscan]
    S0 -->|webhook| EXT
    S1 -->|webhook| EXT
```

- One node uses the `*-pg` Docker profile against an external PostgreSQL server
  (`daemon.db-url`); compose does not bundle PostgreSQL. All other nodes connect
  to the same database.
- Every node runs an identical `sma` container reading `config/sma-ng.yml` and
  `config/daemon.env`. Both are stamped per-host by `mise run deploy:config`.
- Cluster identity comes from `SMA_NODE_NAME` (stamped into `daemon.env`,
  defaults to the host key in `setup/local.yml`). The same string is what every
  cluster API and `mise run cluster` command uses as `node_id`.

---

## Quick reference: cluster control surface

| Action | Mise task | Admin API | DB effect |
|---|---|---|---|
| List node status | `mise run cluster -- status` | `GET /admin/nodes` | reads `cluster_nodes` |
| Drain (finish active jobs, stop accepting new) | `HOST=<host> mise run cluster -- drain` | `POST /admin/nodes/<host>/drain` | `status='draining'` |
| Pause (immediately stop pickup; active jobs continue) | `HOST=<host> mise run cluster -- pause` | `POST /admin/nodes/<host>/pause` | `status='paused'` |
| Resume from drain or pause | `HOST=<host> mise run cluster -- resume` | `POST /admin/nodes/<host>/resume` | `status='online'` |
| Graceful restart (drain → reexec) | — (use admin UI or curl) | `POST /admin/nodes/<host>/restart` | `status='restarting'` → `online` |
| Graceful shutdown (drain → exit) | — (use admin UI or curl) | `POST /admin/nodes/<host>/shutdown` | `status='offline'` |
| Approve pending node | — (use admin UI or curl) | `POST /admin/nodes/<host>/approve` | `approval_status='approved'` |
| Hard-delete an offline node row | — (use admin UI or curl) | `POST /admin/nodes/<host>/delete` | row removed |
| Stop / start / restart container | `HOST=<host> mise run cluster -- {stop,start,restart}` | — | docker compose |
| Rolling cluster upgrade | `mise run cluster -- upgrade` | — | drain → deploy:docker, per host |

The `cluster` mise task (every verb) accepts `HOST=<one>`, `HOSTS='<a> <b>'`, or default
to `deploy.hosts` from `setup/local.yml`.

---

## Deployment patterns

The key requirement for more than one daemon is a shared PostgreSQL database. When `daemon.db-url` points at the same
PostgreSQL instance, all SMA-NG nodes coordinate through the job table and heartbeat system:

- pending jobs are visible to every node
- only one node can claim a given job
- stale running jobs are requeued if a node disappears
- `/status` shows the full cluster, not just the local process

### Deployment Models

#### 1. One host, one instance

Use this when you just want a single daemon with one worker pool.

```text
Host A
└─ sma-ng daemon
   └─ PostgreSQL
```

#### 2. One host, multiple instances

Use this when one machine needs separate SMA daemons, usually because:

- different media trees need different path mappings
- different ports or API keys are required
- you want process isolation between workloads
- you want to dedicate different instances to different GPUs or configs

```text
Host A
├─ sma-ng-tv       -> port 8585
├─ sma-ng-movies   -> port 8586
└─ PostgreSQL
```

#### 3. Multiple hosts, shared cluster

Use this when you want to scale out conversion work across multiple machines.

```text
Host A               Host B               Host C
├─ sma-ng daemon     ├─ sma-ng daemon     ├─ sma-ng daemon
└─ shared mounts?    └─ shared mounts?    └─ shared mounts?
         \               |               /
          \              |              /
           └──── shared PostgreSQL ────┘
```

### Non-Negotiable Requirements

For multi-instance or multi-host setups:

- every instance must use the same PostgreSQL database via `daemon.db-url`
- every instance must have access to the same effective media paths for any jobs it can claim
- path rewriting must normalize host-specific mount differences
- API keys and daemon ports must not conflict on the same host
- each instance should have its own config directory and logs directory unless intentional sharing is required

If two nodes see the same file under different paths, fix that first with `daemon.path-rewrites` and consistent mount layout.

### Shared PostgreSQL

Set the same database URL on every node:

```yaml
daemon:
  db-url: postgresql://sma:password@db-host:5432/sma
```

Database URLs are intentionally read only from `sma-ng.yml`; environment variables are ignored for this setting.

#### Recommended PostgreSQL Placement

- small single-host setup: PostgreSQL on the same machine
- multi-host cluster: PostgreSQL on a stable host or managed service
- SQLite is suitable for a single SMA-NG daemon on one host. Use PostgreSQL when multiple daemons or hosts share work.

### Path Strategy

The safest clustered layout is for all nodes to see the same media roots under the same paths:

```text
/mnt/unionfs/Media/TV
/mnt/unionfs/Media/Movies
/mnt/unionfs/downloads
```

If that is not possible, normalize incoming paths with `daemon.path-rewrites`:

```yaml
daemon:
  path-rewrites:
    - from: /srv/media
      to: /mnt/unionfs/Media
    - from: /volume1/media
      to: /mnt/unionfs/Media
```

The shipped compose file no longer mounts `/downloads`. Download clients in other containers that report
`/downloads/...` need a `daemon.path-rewrites` entry with `from: /downloads` and `to: /mnt/unionfs/downloads`.

This is especially important when:

- Sonarr/Radarr webhooks come from a different host than SMA-NG
- one machine uses local disks and another uses NFS/SMB mounts
- Docker containers see different inside-container paths than the host

### One Host, Multiple Instances

On a single machine, run each instance with its own:

- `sma-ng.yml` (its `daemon:` block)
- `daemon.env`
- port
- logs directory
- container name

Example layout:

```text
/opt/sma-tv/
├─ config/
│  ├─ sma-ng.yml
│  └─ daemon.env
└─ logs/

/opt/sma-movies/
├─ config/
│  ├─ sma-ng.yml
│  └─ daemon.env
└─ logs/
```

Example `daemon:` block in `sma-ng.yml` values:

```yaml
daemon:
  api-key: tv-secret
  db-url: postgresql://sma:password@127.0.0.1:5432/sma
  routing:
    - match: /mnt/unionfs/Media/TV
      profile: rq
```

```yaml
daemon:
  api-key: movies-secret
  db-url: postgresql://sma:password@127.0.0.1:5432/sma
  routing:
    - match: /mnt/unionfs/Media/Movies
      profile: rq
```

Start them on different ports:

```bash
python daemon.py --port 8585 --daemon-config /opt/sma-tv/config/sma-ng.yml --logs-dir /opt/sma-tv/logs
python daemon.py --port 8586 --daemon-config /opt/sma-movies/config/sma-ng.yml --logs-dir /opt/sma-movies/logs
```

### Multiple Hosts

For multiple hosts, keep these values aligned:

- shared `daemon.db-url`
- compatible `daemon.routing`
- identical API behavior and webhook expectations

Typical pattern:

1. mount media on every node
2. deploy the same code version everywhere
3. give each node its own `daemon:` block in `sma-ng.yml` and `daemon.env`
4. point all nodes at the same PostgreSQL
5. set per-node `ffmpeg_dir`, GPU config, and worker count as needed

Host-specific differences usually belong in:

- mount paths plus `daemon.path-rewrites`
- `ffmpeg_dir`
- hardware-specific `sma-ng.yml`
- worker count

### Worker Count and Capacity Planning

Each daemon process has its own `--workers` pool.

Examples:

- 3 hosts × 2 workers each = up to 6 concurrent conversions cluster-wide
- 1 host with 2 daemon instances × 3 workers each = up to 6 concurrent conversions on that host

Be realistic about hardware contention:

- software encodes compete for CPU
- QSV instances compete for the same iGPU
- NVENC sessions compete for the same NVIDIA card
- too many workers will usually reduce throughput, not improve it

Start conservative:

- software: `1-2` workers per host
- Intel QSV: `1-2` workers per iGPU
- NVIDIA: depends on model and session limits; start with `1-2`

### Logging

Each instance should have its own `--logs-dir`.

On one host, do not point multiple independent daemons at the same logs directory unless you explicitly want mixed daemon-level logs. Per-config logs are named from config stems, so separate directories keep the log API and dashboard cleaner.

Recommended:

```text
/opt/sma-tv/logs
/opt/sma-movies/logs
/opt/sma-node-a/logs
/opt/sma-node-b/logs
```

### Docker / Compose Pattern

For Docker-based clusters:

- run one compose project per host
- point every `sma-ng` container at the same PostgreSQL if clustering across hosts
- publish a distinct port per instance on the same host
- mount the same media paths into the container on every node
- set a distinct `daemon.node_id` for every daemon instance in its
  `sma-ng.yml`

If you want a quick starting point, see [Docker Quick Start](docker-quickstart.md#docker-compose).

### Verification Checklist

After bringing nodes up:

1. `GET /health` on each node should succeed
2. `GET /status` should show all nodes when PostgreSQL is enabled
3. submit one job and verify exactly one node claims it
4. stop one node mid-job and confirm stale recovery works after `stale_seconds`
5. check `/logs` and `/jobs/<id>` on each instance

For the day-2 procedures (drain, rolling upgrade, recover a stale node, read cluster logs), see the runbooks below.

### Common Failure Modes

#### Jobs never get picked up

- `daemon.db-url` not set or not shared
- PostgreSQL connectivity broken
- all nodes excluded by locked configs or no workers available

#### Jobs fail only on some nodes

- mismatched mount paths
- missing FFmpeg binaries or GPU access on one host
- different config files or stale deployment version

#### Same webhook path works on one node but not another

- inconsistent `daemon.path-rewrites`
- different container bind mounts

#### Dashboard only shows one node

- instance is not using PostgreSQL mode
- heartbeat not reaching the database
- multiple Docker daemon containers are sharing the same hostname / node ID
- nodes are using different `daemon.db-url` values

### Minimal Multi-Host Example

Node A and Node B both run:

```yaml
daemon:
  db-url: postgresql://sma:password@db.example.com:5432/sma
  path-rewrites:
    - from: /downloads
      to: /mnt/unionfs/downloads
  routing:
    - match: /mnt/unionfs/Media/TV
      profile: rq
    - match: /mnt/unionfs/Media/Movies
      profile: lq
```

Node A:

```bash
python daemon.py --host 0.0.0.0 --port 8585 --workers 2
```

Node B:

```bash
python daemon.py --host 0.0.0.0 --port 8585 --workers 2
```

Both nodes will participate in the same queue as long as the media paths resolve correctly on both machines.

---

## Runbook: first-time cluster bootstrap

Local prereqs: `mise`, `python3`, `rsync`, `ssh`, GitHub CLI (`gh`) optional.

1. **Copy the sample local config and fill it in:**

   ```bash
   cp setup/local.yml.sample setup/local.yml
   $EDITOR setup/local.yml
   ```

   Minimum viable shape (one master with bundled Postgres, two slaves):

   ```yaml
   deploy:
     hosts: [sma-master, sma-slave0, sma-slave1]
     deploy_dir: /opt/sma
     ssh_port: 22
     ssh_key: ~/.ssh/id_ed25519_sma
     use_sudo: true
     db_host: 10.30.10.40   # the master's IP
     db_port: 5432
     db_user: iadmin
     db_password: <strong-pw>
     db_name: sma
     docker_profile: intel
   hosts:
     sma-master:
       address: 10.30.10.40
       user: iadmin
       docker_profile: intel-pg   # this host runs the bundled Postgres
     sma-slave0:
       address: 10.30.0.50
       user: iadmin
     sma-slave1:
       address: 10.30.0.51
       user: iadmin
   daemon:
     api_key: <random-string>
   ```

2. **Bootstrap each host (once)** — installs Docker, creates the
   `/opt/sma` layout (config, logs, data, seeded `daemon.env`), and sets up an ssh key:

   ```bash
   mise run deploy:setup
   ```

3. **Stamp configs and credentials:** writes `config/sma-ng.yml` and
   `config/daemon.env` on every host, with `SMA_NODE_NAME=<host-key>` baked
   into the env file:

   ```bash
   mise run deploy:config
   ```

4. **Bring up the containers:**

   ```bash
   mise run cluster -- start
   mise run cluster -- status   # confirm sma healthy on every node
   ```

5. **Approve every pending node** in the master's `/admin` UI (or via
   `POST /admin/nodes/<host>/approve` with `X-API-Key`). Until a node is
   approved its workers wait without claiming jobs — see
   [Daemon Mode → Node approval](daemon.md#node-approval).

---

## Runbook: add a new node to an existing cluster

1. **Add the host entry to `setup/local.yml`:**

   ```yaml
   deploy:
     hosts: [sma-master, sma-slave0, sma-slave1, sma-slave2]
   hosts:
     sma-slave2:
       address: 10.30.0.52
       user: iadmin
   ```

2. **Bootstrap that host only:**

   ```bash
   mise run deploy:setup HOST=sma-slave2
   mise run deploy:config   HOST=sma-slave2
   HOST=sma-slave2 mise run cluster -- start
   ```

3. **Approve the node** in the master `/admin` UI. The node will start
   claiming jobs on its next heartbeat (≤30s).

---

## Runbook: roll a config change to all nodes

Edit `setup/local.yml` (or any file deep-merged into the rendered
`config/sma-ng.yml` — `base.*`, `profiles.*`, `services.*`), then:

```bash
mise run deploy:config
```

`deploy:config` is non-destructive: it merges new keys into the existing
`config/sma-ng.yml` on each host, leaving operator-tuned values in place. It
does **not** restart the daemon. Apply it with:

```bash
mise run cluster -- reload     # hot-reloadable changes (routing, scan paths, codec lists, profile overlays)
mise run cluster -- restart    # anything else (worker count, bind address/port)
```

`cluster restart` gives the daemon `STOP_GRACE_PERIOD` (default `2m`) to drain before SIGKILL; jobs still
running at the deadline are requeued. The effective drain is the smaller of `daemon.shutdown-grace-seconds`
(default `100`) and `STOP_GRACE_PERIOD`: raise both together and keep `shutdown-grace-seconds` below
`STOP_GRACE_PERIOD`. Both tasks honour `HOST=` / `HOSTS=`.

For zero-loss restarts, use the upgrade runbook below.

---

## Runbook: cluster-wide image upgrade with no queued-job loss

`cluster upgrade` does this for you, one node at a time, in `deploy.hosts`
order:

```bash
mise run cluster -- upgrade
# DRAIN_TIMEOUT=3600 mise run cluster -- upgrade   # raise the per-host wait
```

For each host it:

1. Sends `POST /admin/nodes/<host>/drain` (workers finish active jobs, stop
   accepting new ones).
2. Polls `GET /admin/nodes/<host>` for `running_jobs == 0` (default timeout
   1800s; override with `DRAIN_TIMEOUT=<seconds>`).
3. Runs `mise run deploy:remote HOST=<host>` (config push, `docker compose pull`,
   `docker compose up -d --force-recreate sma`, healthcheck).
4. The new daemon starts fresh and heartbeats `online` automatically — no
   explicit resume needed.

While one node is draining/upgrading, the other nodes keep claiming new jobs,
so the queue stays serviced.

If a host fails to drain within `DRAIN_TIMEOUT`, the task issues a `resume`
for that host (so it doesn't stay drained forever) and skips it; you can
re-run `HOST=<host> mise run cluster -- upgrade` after the slow conversion completes.

---

## Runbook: take a node out of rotation for maintenance

```bash
HOST=sma-slave1 mise run cluster -- drain
# Wait for cluster status to show running_jobs=0 on that node
HOST=sma-slave1 mise run cluster -- stop   # docker compose stop
# Do the maintenance
HOST=sma-slave1 mise run cluster -- start
# Heartbeat will flip status back to 'online' on its own
```

Use `cluster pause` instead of `cluster drain` only when you want active jobs
to continue but new pickups halted (e.g. while you investigate a slow job).

---

## Runbook: recover from a crashed or unreachable node

When a node misses heartbeats for `--stale-seconds` / `daemon.stale-seconds`
(default 120s), the next heartbeat of any live node runs
`recover_stale_nodes`, which in a single transaction:

- Locks the stale `cluster_nodes` rows with `FOR UPDATE SKIP LOCKED`, so two
  live nodes never recover the same node twice
- Re-queues every job that node had marked `running` so another node can claim
  it on its next poll
- Marks the row `status='offline'`

The daemon log shows `Recovered N jobs from M stale node(s)` (node IDs are not
logged). If the stale node was only partitioned and comes back, its workers
notice within about 10 seconds that their jobs were requeued, kill the local
ffmpeg, and discard their results instead of overwriting the new owner's row.

If the node never comes back, eventually `daemon.node-expiry-days` (default 7) will
delete the row. To force it sooner:

```bash
# Check status first
curl -sf -H "X-API-Key: $SMA_DAEMON_API_KEY" \
    http://<master>:8585/admin/nodes/sma-slave2

# Hard-delete (only allowed when status='offline')
curl -sf -X POST -H "X-API-Key: $SMA_DAEMON_API_KEY" \
    http://<master>:8585/admin/nodes/sma-slave2/delete
```

If the node is alive but stuck (e.g. hung ffmpeg), restart its container:

```bash
HOST=sma-slave2 mise run cluster -- restart
```

To trace what happened to the requeued jobs: every job carries the original
`node_id` in its DB row, and the per-config log file
(`/opt/sma/logs/sma-ng.log` on the offending host) holds the FFmpeg output.
See "Read logs across the cluster" below.

---

## Runbook: read logs across the cluster

Three log surfaces exist:

1. **Per-node file log** — `/opt/sma/logs/sma-ng.log` on each host. Worker
   output, FFmpeg stdout, conversion result, the final-output marker
   (`SMA_FINAL_OUTPUT:`) and the per-job summary line all land here.

   ```bash
   ssh iadmin@10.30.10.40 'tail -F /opt/sma/logs/sma-ng.log'
   ```

2. **Centralised cluster log** — every node also writes its log records to the
   `logs` table in the shared Postgres so the master's `/admin/logs` UI can
   show a unified view filterable by node, config, job, and level. The CLI
   equivalent is `GET /admin/logs?node=<host>&job=<id>&level=ERROR`.

3. **Archived per-config files** — when `daemon.log_archive_after_days` is set,
   per-config `.log` files older than that are gzipped into
   `daemon.log_archive_dir`. Read the
   [Daemon Mode → Log archival](daemon.md#log-archival) section for retention
   tuning.

---

## Runbook: PostgreSQL lifecycle

`*-pg` Docker profiles point `daemon.db-url` at an **external** PostgreSQL
instance (production reality on every host we run today). Restart and
recovery are handled out-of-band against that database — there is no
mise task that touches it. If you ever bring back a bundled `sma-pgsql`
service you'll want to reintroduce dedicated lifecycle tasks for it.

After any database reset (planned or recovery), every node's heartbeat
registers a fresh row; re-approve them via `/admin`.

---

## Docker compose profiles

The profile table lives in [Deployment](deployment.md#docker-compose-profile-reference).
Only one node should run a `*-pg` profile. Set it in `hosts.<that-host>.docker_profile`; leave the cluster-wide
`deploy.docker_profile` as the non-pg variant for the slaves.

---

## See also

- [Deployment](deployment.md) — task-by-task reference and the full
  `setup/local.yml` schema
- [Daemon Mode](daemon.md) — admin API, heartbeats, approval flow,
  drain/pause/resume internals, log archival
