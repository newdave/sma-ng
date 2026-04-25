# Daemon Mode

The daemon runs an HTTP server that accepts webhook requests to queue media conversions. Jobs are persisted to a PostgreSQL database, workers process them in the background, and a web dashboard provides real-time status.

## Starting

```bash
# Basic (binds to 127.0.0.1:8585, 1 worker)
python daemon.py

# Production: all interfaces, multiple workers, API key
python daemon.py \
  --host 0.0.0.0 \
  --port 8585 \
  --workers 4 \
  --api-key YOUR_SECRET_KEY \
  --daemon-config config/sma-ng.yml \
  --logs-dir logs/ \
  --ffmpeg-dir /usr/local/bin
```

All options can also be set via environment variables — see [Environment Variables](#environment-variables).

**Additional flags:**

| Flag                     | Default | Description                                                                                                                     |
| ------------------------ | ------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `--smoke-test`           |         | Run a dry-run option-generation check against all configs then exit. Safe pre-flight before systemd considers the unit started. |
| `--job-timeout SECONDS`  | `0`     | Kill a conversion job after this many seconds (0 = no timeout). Also settable via `Daemon.job_timeout_seconds` in `sma-ng.yml`.       |
| `--heartbeat-interval N` | `30`    | Seconds between PostgreSQL cluster heartbeat updates                                                                            |
| `--stale-seconds N`      | `120`   | Seconds without a heartbeat before a node's running jobs are requeued                                                           |

---

## Web Dashboard

Open `http://localhost:8585/` in a browser (redirects to `/dashboard`). Features:

- Real-time job statistics and status
- Active/waiting job panels with per-worker progress
- Cluster node status (PostgreSQL mode — includes approval state and last command audit details)
- Config mapping overview
- Filterable job history table with requeue/cancel actions
- Submit Job form with path autocomplete (config prefixes, recent jobs, live filesystem browsing)
- Job priority controls
- Log viewer: browse, filter, and live-tail per-config log files
- Admin node controls for approve/reject/restart/shutdown/delete

---

## API Endpoints

| Method | Path                  | Auth | Description                                                             |
| ------ | --------------------- | ---- | ----------------------------------------------------------------------- |
| `GET`  | `/`                   | No   | Redirects to `/dashboard`                                               |
| `GET`  | `/dashboard`          | No   | Web dashboard                                                           |
| `GET`  | `/admin`              | No   | Admin panel (destructive actions)                                       |
| `GET`  | `/health`             | No   | Health check with job stats (local node)                                |
| `GET`  | `/status`             | No   | Cluster-wide status across all nodes                                    |
| `GET`  | `/docs`               | No   | Rendered documentation                                                  |
| `GET`  | `/jobs`               | Yes  | List jobs. Query: `?status=pending&limit=50&offset=0`                   |
| `GET`  | `/jobs/<id>`          | Yes  | Get specific job (includes `progress` when running)                     |
| `GET`  | `/configs`            | Yes  | Config mappings and status                                              |
| `GET`  | `/metrics`            | No   | Cluster metrics web page (PostgreSQL required)                          |
| `GET`  | `/api/metrics`        | Yes  | Metrics JSON. Query: `?window=24h\|7d\|30d\|all` (PostgreSQL required)  |
| `GET`  | `/stats`              | Yes  | Job statistics by status                                                |
| `GET`  | `/scan`               | Yes  | Filter unscanned paths. Query: `?path=/a.mkv&path=/b.mkv`               |
| `GET`  | `/browse`             | Yes  | List filesystem dirs/files within configured paths. Query: `?path=/dir` |
| `GET`  | `/logs`               | Yes  | List all log files with metadata                                        |
| `GET`  | `/logs/<name>`        | Yes  | Get log content. Query: `?lines=200&level=ERROR&job_id=42&offset=0`     |
| `GET`  | `/logs/<name>/tail`   | Yes  | Poll for new entries after byte offset. Query: `?offset=<bytes>`        |
| `POST` | `/webhook/generic`    | Yes  | Submit conversion job (file or directory path)                          |
| `POST` | `/webhook/sonarr`     | Yes  | Native Sonarr webhook endpoint (On Download/Upgrade)                    |
| `POST` | `/webhook/radarr`     | Yes  | Native Radarr webhook endpoint (On Download/Upgrade)                    |
| `POST` | `/cleanup`            | Yes  | Remove old jobs. Query: `?days=30`                                      |
| `POST` | `/reload`             | Yes  | Reload `Daemon:` section in `sma-ng.yml` without restarting                                 |
| `POST` | `/restart`            | Yes  | Graceful restart. Query: `?node=<id>` for remote node (PostgreSQL)      |
| `POST` | `/shutdown`           | Yes  | Graceful shutdown. Query: `?node=<id>` for remote node (PostgreSQL)     |
| `POST` | `/jobs/<id>/requeue`  | Yes  | Requeue a specific failed job                                           |
| `POST` | `/jobs/<id>/cancel`   | Yes  | Cancel a pending or running job                                         |
| `POST` | `/jobs/<id>/priority` | Yes  | Set job priority. Body: `{"priority": 10}`                              |
| `POST` | `/jobs/requeue`       | Yes  | Requeue all failed jobs. Query: `?config=...` to filter                 |
| `POST` | `/scan/filter`        | Yes  | Filter unscanned paths (large lists). Body: `{"paths": [...]}`          |
| `POST` | `/scan/record`        | Yes  | Mark paths as scanned. Body: `{"paths": [...]}`                         |

Node admin API route:

- `POST /admin/nodes/<node_id>/<action>` where action is one of `approve`, `reject`, `restart`, `shutdown`, `delete`

---

## Webhook Request Formats

```bash
# Plain text body
curl -X POST http://localhost:8585/webhook/generic \
  -H "X-API-Key: SECRET" \
  -d "/path/to/movie.mkv"

# JSON body
curl -X POST http://localhost:8585/webhook/generic \
  -H "X-API-Key: SECRET" \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/movie.mkv"}'

# JSON with extra manual.py arguments
curl -X POST http://localhost:8585/webhook/generic \
  -H "X-API-Key: SECRET" \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/movie.mkv", "args": ["-tmdb", "603"]}'

# JSON with config override (bypasses path matching)
curl -X POST http://localhost:8585/webhook/generic \
  -H "X-API-Key: SECRET" \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/movie.mkv", "config": "/custom/sma-ng.yml"}'
```

---

## Authentication

API key priority order:

1. `--api-key` CLI argument
2. `SMA_DAEMON_API_KEY` environment variable
3. `Daemon.api_key` in `sma-ng.yml`

```mermaid
flowchart LR
    A["--api-key\nCLI flag"] -->|highest priority| V["Resolved\nAPI Key"]
    B["SMA_DAEMON_API_KEY\nenv var"] --> V
    C["api_key\n`Daemon:` section in `sma-ng.yml`"] --> V
    D["none\nauth disabled"] -->|lowest priority| V
    A -.->|overrides| B -.->|overrides| C -.->|overrides| D
```

Send the key via header:

```bash
# X-API-Key (recommended)
curl -H "X-API-Key: SECRET" ...

# Authorization Bearer
curl -H "Authorization: Bearer SECRET" ...
```

Public endpoints (no auth required): `/`, `/dashboard`, `/admin`, `/metrics`, `/health`, `/status`, `/docs`, `/favicon.png`

---

## Path-Based Configuration

Configure the `Daemon:` section in `config/sma-ng.yml` to route files by path, apply profiles, and set daemon runtime options.

```yaml
Daemon:
  api_key: your_secret_key
  db_url: null
  ffmpeg_dir: null
  media_extensions: [.mp4, .mkv, .avi, .mov, .ts]
  path_rewrites:
    - from: /mnt/local/Media
      to: /mnt/unionfs/Media
  scan_paths:
    - path: /mnt/local/Media
      interval: 3600
      rewrite_from: /mnt/local/Media
      rewrite_to: /mnt/unionfs/Media
      enabled: true
  path_configs:
    - path: /mnt/media/TV
      profile: rq
      default_args: [--tv]
    - path: /mnt/media/Movies/Kids
      profile: lq
      default_args: [--movie]
    - path: /mnt/media/Special
      config: config/autoProcess.special.yaml
```

### Top-Level Keys

| Key                        | Description                                                                                                                          |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `api_key`                  | API authentication key                                                                                                               |
| `db_url`                   | PostgreSQL URL for distributed mode                                                                                                  |
| `ffmpeg_dir`               | Directory containing `ffmpeg`/`ffprobe` binaries, prepended to PATH for each conversion                                              |
| `media_extensions`         | File extensions considered media for directory scanning and `/browse`                                                                |
| `path_rewrites`            | Prefix substitutions applied to incoming webhook paths before config matching; overlapping rewrites are matched longest-prefix-first |
| `scan_paths`               | Directories for scheduled background scanning                                                                                        |
| `path_configs`             | Per-directory routing entries. Use `profile` for named profiles in this file or `config` for an external YAML config                |
| `smoke_test`               | Run option-generation dry-run against all configs at startup. Exits 1 on failure.                                                    |
| `job_timeout_seconds`      | Maximum seconds a conversion may run (0 = no timeout)                                                                                |
| `recycle_bin_max_age_days` | Delete recycle-bin media files older than this many days (default: `3`, `0` = disabled)                                              |
| `recycle_bin_min_free_gb`  | Delete oldest recycle-bin files when free space on the mount drops below this many GiB (default: `50`, `0` = disabled)               |

Matching is longest-prefix-first: `/mnt/media/Movies/4K/film.mkv` matches `Movies/4K`, not `Movies`. If `path_rewrites` overlap, the most specific rewrite is applied before config matching.

### Per-Path Default Args

Each `path_configs` entry can include `default_args` to prepend args to every job submitted from that path:

```yaml
path: /mnt/media/TV
profile: rq
default_args: [--tv]
```

---

## Concurrency

`--workers` controls how many conversions run at the same time.

- Jobs run up to `--workers` at a time; excess jobs queue

```text
Job 1: /TV/show1.mkv     -> sma-ng.yml profile rq [starts immediately]
Job 2: /Movies/film1.mkv -> sma-ng.yml profile lq [starts immediately]
Job 3: /TV/show2.mkv     -> sma-ng.yml profile rq [waits for an available worker]
```

Check active/waiting jobs: `curl http://localhost:8585/health`

---

## Per-Config Logging

Each config gets a separate rotating log file in `logs/` named after the config file stem:

| Config                             | Log File                         |
| ---------------------------------- | -------------------------------- |
| `config/sma-ng.yml` | `logs/sma-ng.log` |

Rotation: 10MB max, 5 backups. Use `--logs-dir` to change the directory.

---

## Log Viewer API

Log files can be read programmatically via the API or browsed in the dashboard's log viewer drawer.

### List log files

```bash
curl -H "X-API-Key: SECRET" http://localhost:8585/logs
```

Returns an array of log file objects:

```json
[
  {"name": "sma-ng", "file": "/app/logs/sma-ng.log", "size": 102400, "mtime": "2024-04-19T12:34:56-04:00"}
]
```

`mtime` is emitted in the daemon host's local timezone and includes the UTC offset.

### Get log content

```bash
# Last 200 lines
curl -H "X-API-Key: SECRET" "http://localhost:8585/logs/autoProcess?lines=200"

# Filter by log level
curl -H "X-API-Key: SECRET" "http://localhost:8585/logs/autoProcess?level=ERROR"

# Filter by job ID
curl -H "X-API-Key: SECRET" "http://localhost:8585/logs/autoProcess?job_id=42"

# Read from byte offset (for polling)
curl -H "X-API-Key: SECRET" "http://localhost:8585/logs/autoProcess?offset=51200"
```

Returns:

```json
{
  "entries": [
    {"timestamp": "2024-04-19T12:00:00-04:00", "level": "INFO", "message": "Starting conversion", "job_id": 42}
  ],
  "file_size": 102400
}
```

Structured timestamps returned by the daemon API use the daemon host's local timezone and include the UTC offset.

**Query parameters:**

| Parameter | Default | Description                                                                 |
| --------- | ------- | --------------------------------------------------------------------------- |
| `lines`   | `200`   | Maximum lines to return (tail of file)                                      |
| `level`   | —       | Minimum log level filter: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`   |
| `job_id`  | —       | Filter entries to a specific job                                            |
| `offset`  | —       | Return content starting from this byte offset (auto-resets on log rotation) |

### Poll for new entries (live tail)

```bash
curl -H "X-API-Key: SECRET" "http://localhost:8585/logs/autoProcess/tail?offset=51200"
```

Use the `file_size` from each response as the `offset` for the next request. The dashboard log viewer uses this automatically in live mode.

---

## Job Priority

Jobs are dequeued highest-priority-first (default priority is 0). Set priority via the dashboard or API:

```bash
curl -X POST http://localhost:8585/jobs/42/priority \
  -H "X-API-Key: SECRET" \
  -H "Content-Type: application/json" \
  -d '{"priority": 10}'
```

Higher numbers = higher priority. Pending jobs only.

---

## Scheduled Directory Scanning

The daemon can periodically scan directories for new media files and queue them automatically:

```json
{
  "scan_paths": [
    {
      "path": "/mnt/local/Media",
      "interval": 3600,
      "rewrite_from": "/mnt/local/Media",
      "rewrite_to": "/mnt/unionfs/Media",
      "enabled": true
    }
  ]
}
```

| Field          | Description                                          |
| -------------- | ---------------------------------------------------- |
| `path`         | Directory to scan                                    |
| `interval`     | Scan interval in seconds                             |
| `rewrite_from` | Path prefix to replace before submitting jobs        |
| `rewrite_to`   | Replacement prefix                                   |
| `enabled`      | Set to `false` to disable without removing the entry |

Files already in the `scanned_files` database table are skipped on subsequent scans. Any file whose extension matches `media_extensions` is eligible for submission, including `.mp4` if you leave it in that list.

**Manual batch scan script:**

```bash
# Submit all unscanned media files in a directory
bash scripts/sma-scan.sh /mnt/media/Movies

# Force resubmit everything
bash scripts/sma-scan.sh /mnt/media/Movies --reset

# Dry-run
bash scripts/sma-scan.sh /mnt/media/Movies --dry-run
```

---

## Recycle Bin Cleanup

The daemon automatically purges old media files from every `recycle-bin` directory configured in any `sma-ng.yml`. Two independent eviction triggers run once per hour:

| Trigger        | Key                        | Default | Behaviour                                                                       |
| -------------- | -------------------------- | ------- | ------------------------------------------------------------------------------- |
| Age            | `recycle_bin_max_age_days` | `3`     | Delete files whose last-modified time is older than N days                      |
| Space pressure | `recycle_bin_min_free_gb`  | `50`    | Delete the oldest files first until free space on the mount point exceeds N GiB |

Set either key to `0` to disable that trigger independently.

```json
{
  "recycle_bin_max_age_days": 3,
  "recycle_bin_min_free_gb": 50
}
```

Only recognised media file extensions are deleted (`.mp4`, `.mkv`, `.avi`, `.mov`, `.ts`, `.m4v`, `.m2ts`, `.wmv`, `.flv`, `.webm`). NFO files, artwork, and other non-media files are never touched.

The free-space check uses `statvfs` and is mount-point-aware, so it works correctly with CephFS, NFS, and other network filesystems.

---

## Job Lifecycle

```mermaid
stateDiagram-v2
    [*] --> pending : webhook received\nor scan match
    pending --> running : worker picks up
    running --> done : conversion succeeded
    running --> failed : error or timeout
    pending --> cancelled : /jobs/id/cancel
    running --> cancelled : /jobs/id/cancel
    failed --> pending : /jobs/requeue
    done --> [*]
    cancelled --> [*]
```

---

## Job Persistence

Jobs are stored in a PostgreSQL database configured via `SMA_DAEMON_DB_URL` or `db_url` in `Daemon:` section in `sma-ng.yml`. The database provides restart recovery, job history, cluster coordination, and deduplication across nodes.

```bash
curl http://localhost:8585/stats
# Returns: {"pending": 3, "running": 1, "completed": 150, "failed": 2, "total": 156}

curl "http://localhost:8585/jobs?status=pending"
curl -X POST "http://localhost:8585/cleanup?days=7"
```

**Database schema:**

```sql
jobs(id, path, config, args, status, priority, worker_id, node_id, error, created_at, started_at, completed_at)
scanned_files(path, scanned_at)
```

---

## PostgreSQL (Distributed / Multi-Node)

For multi-node deployments, configure a shared PostgreSQL database so no two nodes ever process the same file.

**Configure (priority order):**

1. `SMA_DAEMON_DB_URL` environment variable
2. `Daemon.db_url` in `sma-ng.yml`

```yaml
Daemon:
  db_url: postgresql://sma:password@db-host:5432/sma
```

```bash
# daemon.env
SMA_DAEMON_DB_URL=postgresql://sma:password@db-host:5432/sma
```

**Cluster-specific options:**

- `--heartbeat-interval N` — seconds between node heartbeat updates (default: 30)
- `--stale-seconds N` — seconds without a heartbeat before a node's running jobs are requeued (default: 120)

**Cluster status:** The `/status` endpoint returns all nodes with their active jobs, worker count, uptime, and last-seen time. The dashboard shows this as a Cluster Nodes panel.

Node approval is enforced in distributed mode:

- New nodes start as `pending` and do not claim jobs.
- Admin users can approve or reject nodes from `/admin` (or the node admin API route).
- Rejected and pending nodes continue heartbeating but cannot process media.
- Approved nodes can receive queued restart/shutdown commands, recorded with request metadata.

```mermaid
graph LR
    subgraph NodeA["Node A (sma-ng-host1)"]
        W1["WorkerPool\n4 workers"]
        H1["HeartbeatThread"]
    end
    subgraph NodeB["Node B (sma-ng-host2)"]
        W2["WorkerPool\n2 workers"]
        H2["HeartbeatThread"]
    end
    PG[("PostgreSQL\njobs · scanned_files\nheartbeat")]
    W1 <-->|claim / complete jobs| PG
    W2 <-->|claim / complete jobs| PG
    H1 -->|heartbeat every 30s| PG
    H2 -->|heartbeat every 30s| PG
    PG -->|stale detection → requeue orphaned jobs| W1
    PG -->|stale detection → requeue orphaned jobs| W2
```

**Remote restart/shutdown:** Use the dashboard buttons or API with `?node=<id>`:

```bash
# Restart a specific node
curl -X POST "http://localhost:8585/restart?node=media-server-2" -H "X-API-Key: SECRET"

# Shutdown all nodes
curl -X POST http://localhost:8585/shutdown -H "X-API-Key: SECRET"
```

---

## Config Reload

Reload daemon config without restarting the daemon or interrupting active conversions:

```bash
curl -X POST http://localhost:8585/reload -H "X-API-Key: SECRET"
```

Reloaded immediately: `path_configs`, `path_rewrites`, `scan_paths`, `api_key`, `media_extensions`, `default_args`, `ffmpeg_dir`, `job_timeout_seconds`, `progress_log_interval`.

Not reloaded (require full restart): `--host`, `--port`, `--workers`.

---

## Graceful Shutdown / Restart

Both operations drain in-progress conversions before stopping or re-execing.

```bash
# Shutdown (waits for all active jobs to finish)
curl -X POST http://localhost:8585/shutdown -H "X-API-Key: SECRET"

# Restart (drains then re-execs with same args)
curl -X POST http://localhost:8585/restart -H "X-API-Key: SECRET"

# Restart via signal
kill -HUP $(pgrep -f "python daemon.py")
```

All CLI flags (`--host`, `--port`, `--workers`, etc.) are preserved across restart. No running jobs are reset to pending.

---

## Environment Variables

| Variable                | Description                                                 |
| ----------------------- | ----------------------------------------------------------- |
| `SMA_DAEMON_API_KEY`    | API key (overrides `--api-key`)                             |
| `SMA_DAEMON_DB_URL`     | PostgreSQL connection URL                                   |
| `SMA_DAEMON_FFMPEG_DIR` | Directory containing `ffmpeg`/`ffprobe` (prepended to PATH) |
| `SMA_DAEMON_HOST`       | Bind host (Docker default: `0.0.0.0`)                       |
| `SMA_DAEMON_PORT`       | Port (Docker default: `8585`)                               |
| `SMA_DAEMON_WORKERS`    | Number of concurrent workers (Docker default: `2`)          |
| `SMA_DAEMON_CONFIG`     | Path to daemon config, normally `config/sma-ng.yml`   |
| `SMA_DAEMON_LOGS_DIR`   | Directory for per-config log files                          |

`SMA_NODE_NAME` sets the explicit cluster node ID and is recommended for Docker and multi-node deployments.

---

## Cluster Mode

Cluster mode extends multi-node PostgreSQL deployments with unique node identity, coordinated
fleet management, and aggregated log visibility. All cluster features require `db_url` to point
at a shared PostgreSQL instance. Single-node SQLite setups are unaffected — all cluster code
paths are gated on the distributed flag.

### Node Identity

Every node auto-generates a UUID on first start and persists it in `sma-ng.yml` under `daemon.node_id`.
The UUID is used as the node's identity in the shared PostgreSQL `cluster_nodes` table, preventing
job-claiming collisions that can occur with hostname-based identity.

Override the auto-generated UUID in two ways:

- Set `SMA_NODE_NAME` environment variable — takes precedence over `sma-ng.yml`.
- Set `node_id` explicitly in the `daemon:` section of `sma-ng.yml`.

```yaml
daemon:
  # Auto-generated on first start. Override for a human-readable cluster identity.
  node_id: media-server-prod-1
```

Once a UUID is written to `sma-ng.yml`, it is never regenerated. Delete or null the field only
if you intentionally want a new identity assigned.

### Shared Work Queue

All nodes pointing at the same PostgreSQL instance share a single job queue. Workers claim
jobs using `SELECT FOR UPDATE SKIP LOCKED`, which prevents two nodes from ever processing
the same file simultaneously.

### Node Management

Open any node's admin UI (`/admin`) and select the **Cluster** tab to manage all nodes from
a single view.

**Node grid columns:**

| Column | Description |
| --- | --- |
| Hostname | Reported hostname of the node |
| Hardware accel | Detected GPU backend (`qsv`, `nvenc`, `vaapi`, `videotoolbox`, or blank for software) |
| Version | Installed `sma-ng` package version |
| Status | Current node status (see below) |
| Last heartbeat | Time of the most recent heartbeat |
| Uptime | Time since the node started |

**Node status values:**

| Status | Meaning |
| --- | --- |
| `online` | Node is running and accepting jobs normally |
| `idle` | Node is running but has no active workers |
| `draining` | Node finished active jobs and stopped accepting new ones |
| `paused` | Workers are frozen; no new jobs are picked up |
| `offline` | Node has not sent a heartbeat within the stale threshold |

**Available actions per node:**

| Action | Behaviour |
| --- | --- |
| **Drain** | Node finishes all active jobs, then stops accepting new ones. Stays registered and online but idle. Use this before scheduled maintenance to let work complete gracefully. |
| **Pause** | Workers immediately stop picking up new jobs. Active conversions continue to completion. The node stays registered. |
| **Resume** | Clears a `drain` or `pause` state. Workers resume normal job pickup on the next heartbeat cycle. |
| **Restart** | Graceful restart — drains active jobs, then re-execs with the same arguments. |
| **Shutdown** | Graceful shutdown — drains active jobs, then exits. |

Command dispatch is poll-based. Each node checks for pending commands on every heartbeat tick.
The default heartbeat interval is 30 seconds (`--heartbeat-interval`). Commands are acknowledged
and their status (`pending` → `executing` → `done` / `failed`) is recorded in the `node_commands`
table for auditability.

Remote commands can also be issued via the API:

```bash
# Drain a specific node
curl -X POST "http://localhost:8585/admin/nodes/media-server-2/drain" -H "X-API-Key: SECRET"

# Pause a node
curl -X POST "http://localhost:8585/admin/nodes/media-server-2/pause" -H "X-API-Key: SECRET"

# Resume a node
curl -X POST "http://localhost:8585/admin/nodes/media-server-2/resume" -H "X-API-Key: SECRET"
```

### Cluster Log Viewer

All nodes write log entries to the shared `logs` PostgreSQL table. The admin UI Cluster tab
includes a unified log viewer that aggregates entries from every node.

Filter controls:

- **Node** — show logs from one node or all nodes
- **Level** — minimum severity (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`)

Logs are paginated and ordered newest-first.

**API access:**

```bash
# All nodes, last 100 entries
curl -H "X-API-Key: SECRET" "http://localhost:8585/cluster/logs"

# Filter to one node and level
curl -H "X-API-Key: SECRET" "http://localhost:8585/cluster/logs?node_id=media-server-2&level=ERROR"

# Paginate
curl -H "X-API-Key: SECRET" "http://localhost:8585/cluster/logs?limit=50&offset=100"
```

**Log retention:**

Log entries older than `daemon.log_ttl_days` are deleted automatically. Cleanup runs on every
heartbeat tick and is idempotent — multiple nodes running cleanup concurrently produce no
duplicate deletions.

Set `log_ttl_days: 0` in `sma-ng.yml` to disable automatic cleanup entirely.

### Centralised Base Config

When multiple nodes share a PostgreSQL backend, you can push a shared base configuration to the
database so every node uses consistent defaults without duplicating `sma-ng.yml` files.

**How it works:**

- One node pushes its current config to the shared `cluster_config` table via the admin UI or API.
- On each config reload, every node fetches the DB base config and merges it with its local
  `sma-ng.yml`. Local values always win — the DB config is a source of shared defaults, not
  an override.
- Secrets (`api_key`, `db_url`, `username`, `password`, `node_id`) are stripped before writing
  to the database and never replicated to other nodes.

**Push from the admin UI:**

Open the **Cluster** tab in the admin UI. Click **Push config from this node** to write the
current node's `sma-ng.yml` to the database (secrets stripped). All nodes will pick it up on
their next config reload or heartbeat tick.

**Read/write via API:**

```bash
# Read the current DB base config
curl -H "X-API-Key: SECRET" http://localhost:8585/admin/config

# Write a new base config (YAML body as JSON-encoded string)
curl -X POST -H "X-API-Key: SECRET" -H "Content-Type: application/json" \
  -d '{"config": {"video": {"preset": "fast"}, "daemon": {"log_ttl_days": 14}}}' \
  http://localhost:8585/admin/config
```

The `GET /admin/config` endpoint returns `{"config": {...}}` as a parsed dict or `null` when
no cluster config has been stored yet. `POST /admin/config` accepts the same shape.

**Merge semantics:**

1. Local `sma-ng.yml` is parsed first and applied.
2. If a distributed DB is configured, the DB base config is fetched.
3. Only keys *explicitly present* in the local `sma-ng.yml` override DB values. Keys absent
   from the local file fall through to the DB-provided value.
4. If the DB fetch fails, the node continues with local settings and logs a warning.

### Node Expiry

Offline nodes remain in the `cluster_nodes` table until explicitly deleted or until the
automatic expiry TTL elapses. Expiry is useful for long-running clusters where decommissioned
nodes accumulate over time.

Configure the expiry threshold in `sma-ng.yml`:

```yaml
daemon:
  # Days after which offline nodes are hard-deleted from the registry.
  # Set to 0 to disable automatic node expiry.
  node_expiry_days: 0
```

When a node's `last_seen` timestamp is older than `node_expiry_days`, the node row and all
its pending `node_commands` rows are deleted on the next heartbeat tick of any running node.

Only nodes in `offline` status are eligible for expiry. Online, paused, draining, and idle
nodes are never expired automatically regardless of the threshold.

### Log Archival

By default, log rows older than `log_ttl_days` are deleted from PostgreSQL. Log archival
provides an alternative: aged rows are moved to compressed JSONL files on the local filesystem
before being removed from the database.

Configure archival in `sma-ng.yml`:

```yaml
daemon:
  # Directory for archived cluster log files (gzipped JSONL, one per node per day).
  # Set to null to disable log archival.
  log_archive_dir: /mnt/logs/sma-archive

  # Move cluster logs older than this many days from DB to log_archive_dir.
  # Set to 0 to disable archival (logs are deleted per log_ttl_days instead).
  log_archive_after_days: 7

  # Delete archived log files older than this many days from log_archive_dir.
  # Set to 0 to disable archive file pruning.
  log_delete_after_days: 90
```

**Archive layout:**

```text
log_archive_dir/
  <node_id>/
    2025-01-14.jsonl.gz   # one file per node per day
    2025-01-15.jsonl.gz
  <other-node-id>/
    2025-01-14.jsonl.gz
```

Each `.jsonl.gz` file contains newline-delimited JSON records with fields `node_id`, `level`,
`logger`, `message`, and `timestamp`.

**Archival guarantees:**

- Files are written atomically (`.tmp` + `os.replace`). A partial write never replaces an
  existing archive.
- DB rows are only deleted after all files for the same archival batch have been successfully
  written. If any write fails, the DB rows are kept and the next run retries.
- Pruning old archive files (`log_delete_after_days`) runs independently and only after
  archival succeeds for the current batch.

Archival runs on every heartbeat tick of every node. Because the DB deletion is idempotent
(`DELETE WHERE timestamp < cutoff`), concurrent archival across nodes produces no data loss.

### Cluster Configuration Reference

Add these fields to the `daemon:` section of `sma-ng.yml`:

| Setting | Default | Description |
| --- | --- | --- |
| `daemon.node_id` | *(auto-generated UUID)* | Unique node identity. Generated on first start and persisted. Override with `SMA_NODE_NAME` or by setting this field explicitly. |
| `daemon.log_ttl_days` | `30` | Days to retain cluster log entries in PostgreSQL. Set to `0` to disable TTL cleanup. |
| `daemon.node_expiry_days` | `0` | Days after which offline nodes are hard-deleted. Set to `0` to disable automatic expiry. |
| `daemon.log_archive_dir` | `null` | Filesystem path for gzipped JSONL log archives. Set to `null` to disable archival. |
| `daemon.log_archive_after_days` | `0` | Move DB log rows older than this many days to `log_archive_dir`. Set to `0` to disable archival. |
| `daemon.log_delete_after_days` | `0` | Delete archive files older than this many days from `log_archive_dir`. Set to `0` to keep archives indefinitely. |

```yaml
daemon:
  # node_id is auto-generated as a UUID on first start and persisted here.
  # Override only if you need a human-readable cluster identity.
  node_id: null
  # Number of days to retain cluster log entries in PostgreSQL. 0 disables cleanup.
  log_ttl_days: 30
  # Days after which offline nodes are hard-deleted from the registry.
  # Set to 0 to disable automatic node expiry.
  node_expiry_days: 0
  # Directory for archived cluster log files (gzipped JSONL, one per node per day).
  # Set to null to disable log archival.
  log_archive_dir: null
  # Move cluster logs older than this many days from DB to log_archive_dir.
  # Set to 0 to disable archival (logs are deleted per log_ttl_days instead).
  log_archive_after_days: 0
  # Delete archived log files older than this many days from log_archive_dir.
  # Set to 0 to disable archive file pruning.
  log_delete_after_days: 0
```
