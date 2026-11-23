# Cluster Operations

Deployment patterns and operator runbooks for running SMA-NG on one host with several daemons, or as a cluster of
nodes managed from your workstation with `mise` and `docker compose`. Each runbook is a self-contained scenario: the
steps to follow when a particular thing happens.

For the underlying reference material, see:

- [Deployment](deployment.md) for every mise task, the `setup/local.yml` schema and the CI and release flow.
- [Daemon Mode](daemon.md) for cluster internals: heartbeats, node approval, the job queue and the admin API.

---

## Cluster shape

```mermaid
graph LR
    O[Operator workstation<br/>mise run ...] -->|ssh| M[sma-master<br/>sma daemon]
    O -->|ssh| S0[sma-slave0<br/>sma daemon]
    O -->|ssh| S1[sma-slave1<br/>sma daemon]
    M -->|SQL| P[(PostgreSQL<br/>jobs, cluster_nodes,<br/>node_commands, ...)]
    S0 -->|SQL| P
    S1 -->|SQL| P
    M -->|webhook| EXT[Sonarr / Radarr<br/>Plex / Autoscan]
    S0 -->|webhook| EXT
    S1 -->|webhook| EXT
```

- Every node runs an identical `sma-ng` container that reads `config/sma-ng.yml`. `mise run deploy:config` generates
  that file per host from `setup/local.yml`.
- All nodes point `daemon.db-url` at the same external PostgreSQL server. Compose does not bundle PostgreSQL.
- Cluster identity is `daemon.node-id`. `deploy:config` sets it to the host key from `setup/local.yml`, and the same
  string is the `<host>` in every `/admin/nodes/<host>/...` call and every `mise run cluster` command. If you run
  nodes without `deploy:config`, set `daemon.node-id` yourself. Otherwise the daemon generates a UUID and saves it
  into `sma-ng.yml`.
- `config/daemon.env` holds only container settings (such as `SMA_WORKERS`, `LIBVA_DRIVER_NAME` and `NVIDIA_*`).
  `deploy:setup` seeds it once from `setup/daemon.env.sample`, and `deploy:config` does not touch it.

---

## Quick reference: cluster control surface

Run every `mise run cluster` verb with `HOST=<one>`, `HOSTS='<a> <b>'`, or neither (all of `deploy.hosts`).

| Action | Mise task | Admin API | Node status |
| --- | --- | --- | --- |
| Show container state | `mise run cluster -- status` | `GET /status` lists every node in `cluster` | none |
| Drain (finish active jobs, accept no new ones) | `HOST=<host> mise run cluster -- drain` | `POST /admin/nodes/<host>/drain` | `draining` |
| Pause (stop picking up jobs; active jobs continue) | `HOST=<host> mise run cluster -- pause` | `POST /admin/nodes/<host>/pause` | `paused` |
| Resume from drain or pause | `HOST=<host> mise run cluster -- resume` | `POST /admin/nodes/<host>/resume` | `online` |
| Reload `sma-ng.yml` without a restart | `mise run cluster -- reload` | `POST /reload` | unchanged |
| Start, stop or restart the container | `HOST=<host> mise run cluster -- start` (or `stop`, `restart`) | none | `offline` while stopped |
| Rolling upgrade | `mise run cluster -- upgrade` | none | drained, then `online` |
| Graceful daemon restart (drain, then re-exec) | the admin UI or `curl` | `POST /admin/nodes/<host>/restart` | `restarting`, then `online` |
| Graceful daemon shutdown (drain, then exit) | the admin UI or `curl` | `POST /admin/nodes/<host>/shutdown` | `offline` |
| Approve a pending node | the admin UI or `curl` | `POST /admin/nodes/<host>/approve` | approval `approved` |
| Remove an offline node's row | the admin UI or `curl` | `POST /admin/nodes/<host>/delete` | row removed |

The admin API calls need the `X-API-Key` header when `daemon.api-key` is set.

---

## Deployment patterns

The key requirement for more than one daemon is a shared PostgreSQL database. When `daemon.db-url` points at the same
instance, all nodes coordinate through the job table and the heartbeat system:

- Pending jobs are visible to every node.
- Only one node can claim a given job.
- Stale running jobs are requeued if a node disappears.
- `/status` shows the whole cluster, not only the local process.

### Deployment models

#### 1. One host, one instance

Use this for a single daemon with one worker pool. SQLite is enough; PostgreSQL is optional.

```text
Host A
└─ sma-ng daemon (SQLite or PostgreSQL)
```

#### 2. One host, multiple instances

Use this when one machine needs separate daemons, usually because different media trees need different path mappings,
different ports or API keys are required, you want process isolation, or you want to dedicate instances to different
GPUs or configs.

```text
Host A
├─ sma-ng-tv       -> port 8585
├─ sma-ng-movies   -> port 8586
└─ PostgreSQL
```

#### 3. Multiple hosts, shared cluster

Use this to scale conversion work across machines.

```text
Host A               Host B               Host C
├─ sma-ng daemon     ├─ sma-ng daemon     ├─ sma-ng daemon
└─ shared mounts     └─ shared mounts     └─ shared mounts
         \               |               /
          \              |              /
           └──── shared PostgreSQL ────┘
```

### Requirements for multi-instance setups

- Every instance uses the same PostgreSQL database through `daemon.db-url`.
- Every instance sees the same effective media paths for any job it can claim.
- Path rewriting normalizes host-specific mount differences.
- API keys and ports do not conflict on the same host.
- Each instance has its own config and logs directory, unless you intend to share them.

If two nodes see the same file under different paths, fix that first with `daemon.path-rewrites` and a consistent
mount layout. See [Shared storage and paths](deployment.md#shared-storage-and-paths) for one worked layout.

### Shared PostgreSQL

Set the same database URL on every node:

```yaml
daemon:
  db-url: postgresql://sma:password@db-host:5432/sma
```

The daemon reads the database URL only from `sma-ng.yml`; environment variables are ignored for this setting. Put
PostgreSQL on the same machine for a small single-host setup, and on a stable host or managed service for a
multi-host cluster. SQLite suits only a single daemon on one host.

### Path strategy

The safest layout gives every node the same media roots under the same paths:

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

The shipped compose file does not mount `/downloads`. A download client in another container that reports
`/downloads/...` needs a rewrite with `from: /downloads` and `to: /mnt/unionfs/downloads`. Rewrites matter most when
Sonarr or Radarr webhooks come from another host, when one machine uses local disks and another NFS or SMB, and when
containers see different paths than the host.

### One host, multiple instances

Give each instance its own `sma-ng.yml`, port, logs directory and container name (and `daemon.env` for containers):

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

The `daemon:` block of the TV instance, for example:

```yaml
daemon:
  api-key: tv-secret
  db-url: postgresql://sma:password@127.0.0.1:5432/sma
  routing:
    - match: /mnt/unionfs/Media/TV
      profile: rq
```

Start the instances on different ports:

```bash
python daemon.py --port 8585 --daemon-config /opt/sma-tv/config/sma-ng.yml --logs-dir /opt/sma-tv/logs
python daemon.py --port 8586 --daemon-config /opt/sma-movies/config/sma-ng.yml --logs-dir /opt/sma-movies/logs
```

Do not point independent daemons at the same logs directory unless you want mixed daemon-level logs. Per-config logs
are named from config stems, so separate directories keep the log API and dashboard clean.

### Multiple hosts

Keep these values aligned across hosts: the shared `daemon.db-url`, compatible `daemon.routing`, and the same image
version. Host-specific differences belong in mount paths plus `daemon.path-rewrites`, `ffmpeg_dir`, the GPU settings
and the worker count. With `deploy:config`, these come from `setup/local.yml` (see
[Deployment](deployment.md#configuration)).

Two nodes that share a queue need only the same database and working media paths:

```yaml
daemon:
  db-url: postgresql://sma:password@db.example.com:5432/sma
  path-rewrites:
    - from: /downloads
      to: /mnt/unionfs/downloads
```

### Docker and compose pattern

- Run one compose project per host, and point every `sma-ng` container at the same PostgreSQL when clustering.
- Publish a distinct port per instance on one host, and mount the same media paths into the container on every node.
- Give each daemon a distinct `daemon.node-id`. The compose service uses the fixed hostname `sma-ng`, so nodes that
  rely on the hostname for identity would collide.

For a starting point, see [Docker Quick Start](docker-quickstart.md#docker-compose). The compose profile table is in
[Deployment](deployment.md#docker-compose-profile-reference).

### Worker count and capacity

Each daemon has its own worker pool (`daemon.workers`, or `--workers`). Three hosts with two workers each allow six
concurrent conversions cluster-wide, and one host with two instances of three workers allows six on that host. More
workers do not always mean more throughput, because software encodes compete for CPU, QSV instances share one
iGPU, and NVENC sessions share one card. Start with one or two workers per host or GPU, and raise the number only
while throughput improves. Set a per-host value with `workers:` under `hosts.<host>` in `setup/local.yml`.

### Job history and node rows

Finished jobs are kept indefinitely: `daemon.job-retention-days` defaults to `0`, which disables automatic pruning.
Set it only when you want history deleted, and note that lifetime job totals survive pruning. Offline node rows are
removed after `daemon.node-expiry-days` (default `7`).

### Verification checklist

After bringing nodes up:

1. `GET /health` succeeds on each node.
2. `GET /status` lists every node in `cluster`.
3. Submit one job and confirm that exactly one node claims it.
4. Stop one node during a job and confirm that stale recovery requeues the job after `stale-seconds`.
5. Check `/logs` and `/jobs/<id>` on each instance.

### Common failure modes

| Symptom | Likely causes |
| --- | --- |
| Jobs are never picked up | `daemon.db-url` is not set or not shared; PostgreSQL is unreachable; the node is still pending approval; no workers are available |
| Jobs fail only on some nodes | Mismatched mount paths; missing FFmpeg or GPU access on one host; a stale config or image version |
| The same webhook works on one node only | Inconsistent `daemon.path-rewrites`; different container bind mounts |
| The dashboard shows only one node | The instance uses SQLite; heartbeats do not reach the database; several containers share a `node-id`; nodes use different `db-url` values |

---

## Runbook: first-time cluster bootstrap

Local prerequisites: `mise`, `python3`, `rsync` and `ssh`. Each host needs only SSH access; `deploy:setup` installs
Docker.

1. Copy the sample local config and fill it in:

   ```bash
   cp setup/local.yml.sample setup/local.yml
   $EDITOR setup/local.yml
   ```

   A minimal shape for three nodes that share an external PostgreSQL server:

   ```yaml
   deploy:
     hosts: [sma-master, sma-slave0, sma-slave1]
     deploy_dir: /opt/sma
     ssh_port: 22
     ssh_key: ~/.ssh/id_ed25519_sma
     use_sudo: true
     docker_profile: intel
   hosts:
     sma-master:
       address: 10.30.10.40
       user: iadmin
     sma-slave0:
       address: 10.30.0.50
       user: iadmin
     sma-slave1:
       address: 10.30.0.51
       user: iadmin
   daemon:
     api_key: <random-string>
     db_url: postgresql://sma:<strong-pw>@10.30.10.5:5432/sma
   ```

2. Prepare each host once. This creates an SSH key if needed, installs Docker, creates the `/opt/sma` layout (`config`,
   `logs`, `data`) and seeds `config/daemon.env`:

   ```bash
   mise run deploy:setup
   ```

3. Build and push each host's `config/sma-ng.yml`:

   ```bash
   mise run deploy:config
   ```

4. Bring up the containers and check them:

   ```bash
   mise run deploy:docker
   mise run cluster -- status
   ```

5. Approve every pending node in the `/admin` page (or with `POST /admin/nodes/<host>/approve` and `X-API-Key`).
   Until a node is approved, its workers wait without claiming jobs. See [Daemon Mode](daemon.md#node-management).

---

## Runbook: add a node to an existing cluster

1. Add the host to `setup/local.yml`:

   ```yaml
   deploy:
     hosts: [sma-master, sma-slave0, sma-slave1, sma-slave2]
   hosts:
     sma-slave2:
       address: 10.30.0.52
       user: iadmin
   ```

2. Bootstrap only that host:

   ```bash
   HOST=sma-slave2 mise run deploy:setup
   HOST=sma-slave2 mise run deploy:remote
   ```

3. Approve the node in the `/admin` page. It starts claiming jobs on its next heartbeat (within `heartbeat-interval`,
   30 seconds by default).

---

## Runbook: roll a config change to all nodes

Edit `setup/local.yml`, then rebuild and push every host's config:

```bash
mise run deploy:config
```

`deploy:config` regenerates each host's `sma-ng.yml` from the sample plus `setup/local.yml` and replaces the file on
the host, so keep durable settings in `setup/local.yml` rather than editing the file on a host. It does not restart the
daemon. Apply the change with one of:

```bash
mise run cluster -- reload     # hot-reloadable: routing, scan paths, codec lists, profile overlays
mise run cluster -- restart    # everything else: worker count, bind address and port
```

`cluster restart` gives the daemon `STOP_GRACE_PERIOD` (default `2m`) to drain before SIGKILL, and jobs still running
at the deadline are requeued; see [Graceful stop and restart](deployment.md#graceful-stop-and-restart). For restarts
that lose no work, use the upgrade runbook below.

---

## Runbook: cluster-wide image upgrade with no queued-job loss

After CI publishes a new image (see [Deployment](deployment.md#deployment-workflow)), `cluster upgrade` updates the
nodes one at a time, in `deploy.hosts` order:

```bash
mise run cluster -- upgrade
DRAIN_TIMEOUT=3600 mise run cluster -- upgrade   # raise the per-host wait
```

For each host, it:

1. Sends `POST /admin/nodes/<host>/drain`, so workers finish active jobs and accept no new ones.
2. Polls the node until `running_jobs` is `0`. The default timeout is 1800 seconds (`DRAIN_TIMEOUT`), and the poll
   interval is 10 seconds (`POLL_INTERVAL`).
3. Runs `deploy:remote` for that host: config push, then image pull and recreate if the image changed, then a health
   check.
4. Leaves the new daemon to heartbeat `online` by itself. Drain state is not persisted, so no explicit resume is
   needed.

The other nodes keep claiming jobs while one drains, so the queue stays serviced. If a host does not drain in time, or
`deploy:remote` fails for it, the task resumes that host, skips it and continues. The task exits non-zero and lists the
failed hosts. Re-run `HOST=<host> mise run cluster -- upgrade` once the slow conversion has finished.

---

## Runbook: take a node out of rotation for maintenance

```bash
HOST=sma-slave1 mise run cluster -- drain
# Wait until /status shows no running jobs on that node
HOST=sma-slave1 mise run cluster -- stop
# Do the maintenance
HOST=sma-slave1 mise run cluster -- start
# The heartbeat sets the status back to online by itself
```

Use `cluster pause` instead of `cluster drain` when you want active jobs to continue but no new pickups, for example
while you investigate a slow job.

---

## Runbook: recover from a crashed or unreachable node

When a node misses heartbeats for `daemon.stale-seconds` (default `120`), the next heartbeat of any live node runs
stale-node recovery. In a single transaction, it:

- locks the stale `cluster_nodes` rows with `FOR UPDATE SKIP LOCKED`, so two live nodes never recover the same node;
- requeues every job that the node had marked `running`, so another node can claim it;
- marks the node row `offline`.

The daemon log shows `Recovered N jobs from M stale node(s)`; node IDs are not logged. If the node was only
partitioned and comes back, its workers notice that their jobs were requeued, stop the local FFmpeg and discard the
results instead of overwriting the new owner's row.

If the node never comes back, its row is deleted after `daemon.node-expiry-days` (default `7`). To remove it sooner,
the delete call works only while the node is offline:

```bash
curl -sf -X POST -H "X-API-Key: $SMA_DAEMON_API_KEY" http://<master>:8585/admin/nodes/sma-slave2/delete
```

If the node is alive but stuck (for example, a hung FFmpeg), restart its container:

```bash
HOST=sma-slave2 mise run cluster -- restart
```

To trace the requeued jobs, look at the job's `node_id` in `GET /jobs/<id>`, and at the node's log file for the FFmpeg
output (see the next runbook).

---

## Runbook: read logs across the cluster

Three log surfaces exist:

1. **Per-node file log.** `/opt/sma/logs/sma-ng.log` on each host holds worker output, FFmpeg output, conversion
   results, the final-output marker (`SMA_FINAL_OUTPUT:`) and the per-job summary line.

   ```bash
   ssh iadmin@10.30.10.40 'tail -F /opt/sma/logs/sma-ng.log'
   ```

2. **Centralized cluster log.** Every node also writes its log records to the shared PostgreSQL `logs` table. Read
   them from any node with `GET /cluster/logs`, which accepts `node_id`, `level`, `limit` (up to 500) and `offset`:

   ```bash
   curl -sf -H "X-API-Key: $SMA_DAEMON_API_KEY" \
     "http://<master>:8585/cluster/logs?node_id=sma-slave1&level=ERROR&limit=50"
   ```

3. **Archived per-config files.** When `daemon.log-archive-after-days` is set, older per-config `.log` files are
   compressed into `daemon.log-archive-dir`. See [Daemon Mode](daemon.md#log-archival) for retention tuning.

---

## Runbook: PostgreSQL lifecycle

The `-pg` profiles use an external PostgreSQL server. Restart, backup and recovery happen outside SMA-NG, and no mise
task touches the database. After any database reset, every node registers a fresh row at its next heartbeat, so
approve the nodes again in `/admin`.

---

## See also

- [Deployment](deployment.md) for task reference, the full `setup/local.yml` schema and the compose profile table.
- [Daemon Mode](daemon.md) for the admin API, heartbeats, node approval, drain, pause and resume, and log archival.
