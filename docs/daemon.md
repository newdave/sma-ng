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
  --daemon-config config/daemon.json \
  --logs-dir logs/ \
  --ffmpeg-dir /usr/local/bin
```

All options can also be set via environment variables — see [Environment Variables](#environment-variables).

**Additional flags:**

| Flag | Default | Description |
| --- | --- | --- |
| `--smoke-test` | | Run a dry-run option-generation check against all configs then exit. Safe pre-flight before systemd considers the unit started. |
| `--job-timeout SECONDS` | `0` | Kill a conversion job after this many seconds (0 = no timeout). Also settable via `job_timeout_seconds` in `daemon.json`. |
| `--heartbeat-interval N` | `30` | Seconds between PostgreSQL cluster heartbeat updates |
| `--stale-seconds N` | `120` | Seconds without a heartbeat before a node's running jobs are requeued |

---

## Web Dashboard

Open `http://localhost:8585/` in a browser (redirects to `/dashboard`). Features:

- Real-time job statistics and status
- Active/waiting job panels with per-worker progress
- Cluster node status (PostgreSQL mode — shows all nodes with active jobs, uptime, and remote restart/shutdown buttons)
- Config mapping overview
- Filterable job history table with requeue/cancel actions
- Submit Job form with path autocomplete (config prefixes, recent jobs, live filesystem browsing)
- Job priority controls
- Log viewer: browse, filter, and live-tail per-config log files

---

## API Endpoints

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/` | No | Redirects to `/dashboard` |
| `GET` | `/dashboard` | No | Web dashboard |
| `GET` | `/admin` | No | Admin panel (destructive actions) |
| `GET` | `/health` | No | Health check with job stats (local node) |
| `GET` | `/status` | No | Cluster-wide status across all nodes |
| `GET` | `/docs` | No | Rendered documentation |
| `GET` | `/jobs` | Yes | List jobs. Query: `?status=pending&limit=50&offset=0` |
| `GET` | `/jobs/<id>` | Yes | Get specific job (includes `progress` when running) |
| `GET` | `/configs` | Yes | Config mappings and status |
| `GET` | `/stats` | Yes | Job statistics by status |
| `GET` | `/scan` | Yes | Filter unscanned paths. Query: `?path=/a.mkv&path=/b.mkv` |
| `GET` | `/browse` | Yes | List filesystem dirs/files within configured paths. Query: `?path=/dir` |
| `GET` | `/logs` | Yes | List all log files with metadata |
| `GET` | `/logs/<name>` | Yes | Get log content. Query: `?lines=200&level=ERROR&job_id=42&offset=0` |
| `GET` | `/logs/<name>/tail` | Yes | Poll for new entries after byte offset. Query: `?offset=<bytes>` |
| `POST` | `/webhook` | Yes | Submit conversion job (file or directory path) |
| `POST` | `/webhook/sonarr` | Yes | Native Sonarr webhook endpoint (On Download/Upgrade) |
| `POST` | `/webhook/radarr` | Yes | Native Radarr webhook endpoint (On Download/Upgrade) |
| `POST` | `/cleanup` | Yes | Remove old jobs. Query: `?days=30` |
| `POST` | `/reload` | Yes | Reload `daemon.json` without restarting |
| `POST` | `/restart` | Yes | Graceful restart. Query: `?node=<id>` for remote node (PostgreSQL) |
| `POST` | `/shutdown` | Yes | Graceful shutdown. Query: `?node=<id>` for remote node (PostgreSQL) |
| `POST` | `/jobs/<id>/requeue` | Yes | Requeue a specific failed job |
| `POST` | `/jobs/<id>/cancel` | Yes | Cancel a pending or running job |
| `POST` | `/jobs/<id>/priority` | Yes | Set job priority. Body: `{"priority": 10}` |
| `POST` | `/jobs/requeue` | Yes | Requeue all failed jobs. Query: `?config=...` to filter |
| `POST` | `/scan/filter` | Yes | Filter unscanned paths (large lists). Body: `{"paths": [...]}` |
| `POST` | `/scan/record` | Yes | Mark paths as scanned. Body: `{"paths": [...]}` |

---

## Webhook Request Formats

```bash
# Plain text body
curl -X POST http://localhost:8585/webhook \
  -H "X-API-Key: SECRET" \
  -d "/path/to/movie.mkv"

# JSON body
curl -X POST http://localhost:8585/webhook \
  -H "X-API-Key: SECRET" \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/movie.mkv"}'

# JSON with extra manual.py arguments
curl -X POST http://localhost:8585/webhook \
  -H "X-API-Key: SECRET" \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/movie.mkv", "args": ["-tmdb", "603"]}'

# JSON with config override (bypasses path matching)
curl -X POST http://localhost:8585/webhook \
  -H "X-API-Key: SECRET" \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/movie.mkv", "config": "/custom/autoProcess.ini"}'
```

---

## Authentication

API key priority order:

1. `--api-key` CLI argument
2. `SMA_DAEMON_API_KEY` environment variable
3. `api_key` field in `daemon.json`

Send the key via header:

```bash
# X-API-Key (recommended)
curl -H "X-API-Key: SECRET" ...

# Authorization Bearer
curl -H "Authorization: Bearer SECRET" ...
```

Public endpoints (no auth required): `/`, `/dashboard`, `/admin`, `/health`, `/status`, `/docs`, `/favicon.png`

---

## Path-Based Configuration (daemon.json)

Create `config/daemon.json` (copy from `setup/daemon.json.sample`) to route files to different `autoProcess.ini` files based on their path:

```json
{
  "default_config": "config/autoProcess.ini",
  "api_key": "your_secret_key",
  "db_url": null,
  "ffmpeg_dir": null,
  "media_extensions": [".mp4", ".mkv", ".avi", ".mov", ".ts"],
  "path_rewrites": [
    {
      "from": "/mnt/local/Media",
      "to": "/mnt/unionfs/Media"
    }
  ],
  "scan_paths": [
    {
      "path": "/mnt/local/Media",
      "interval": 3600,
      "rewrite_from": "/mnt/local/Media",
      "rewrite_to": "/mnt/unionfs/Media",
      "enabled": true
    }
  ],
  "path_configs": [
    {"path": "/mnt/media/TV", "config": "config/autoProcess.tv.ini"},
    {"path": "/mnt/media/Movies/4K", "config": "config/autoProcess.movies-4k.ini"},
    {"path": "/mnt/media/Movies", "config": "config/autoProcess.movies.ini"}
  ]
}
```

### Top-Level Keys

| Key | Description |
| --- | --- |
| `default_config` | Config file used when no `path_configs` prefix matches |
| `api_key` | API authentication key |
| `db_url` | PostgreSQL URL for distributed mode |
| `ffmpeg_dir` | Directory containing `ffmpeg`/`ffprobe` binaries, prepended to PATH for each conversion |
| `media_extensions` | File extensions considered media for directory scanning and `/browse` |
| `path_rewrites` | Prefix substitutions applied to incoming webhook paths before config matching |
| `scan_paths` | Directories for scheduled background scanning |
| `path_configs` | Array of `{"path": "...", "config": "..."}` entries for per-directory config selection |
| `smoke_test` | Run option-generation dry-run against all configs at startup. Exits 1 on failure. |
| `job_timeout_seconds` | Maximum seconds a conversion may run (0 = no timeout) |
| `recycle_bin_max_age_days` | Delete recycle-bin media files older than this many days (default: `3`, `0` = disabled) |
| `recycle_bin_min_free_gb` | Delete oldest recycle-bin files when free space on the mount drops below this many GiB (default: `50`, `0` = disabled) |

Matching is longest-prefix-first: `/mnt/media/Movies/4K/film.mkv` matches `Movies/4K`, not `Movies`.

### Per-Path Default Args

Each `path_configs` entry can include `default_args` to prepend args to every job submitted from that path:

```json
{"path": "/mnt/media/TV", "config": "config/autoProcess.tv.ini", "default_args": ["--tv"]}
```

---

## Concurrency

`--workers` controls how many conversions run at the same time.

- Jobs targeting the **same config** run up to `--workers` at a time; excess jobs queue
- Jobs targeting **different configs** always run in parallel (up to `--workers` total)

```text
Job 1: /TV/show1.mkv     -> autoProcess.tv.ini     [starts immediately]
Job 2: /Movies/film1.mkv -> autoProcess.movies.ini [starts immediately]
Job 3: /TV/show2.mkv     -> autoProcess.tv.ini     [waits for Job 1 to finish]
```

Check active/waiting jobs: `curl http://localhost:8585/health`

---

## Per-Config Logging

Each config gets a separate rotating log file in `logs/` named after the config file stem:

| Config | Log File |
| --- | --- |
| `config/autoProcess.ini` | `logs/autoProcess.log` |
| `config/autoProcess.tv.ini` | `logs/autoProcess.tv.log` |
| `config/autoProcess.movies-4k.ini` | `logs/autoProcess.movies-4k.log` |

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
  {"name": "autoProcess", "file": "/app/logs/autoProcess.log", "size": 102400, "mtime": "2024-04-19T12:34:56Z"},
  {"name": "autoProcess.tv", "file": "/app/logs/autoProcess.tv.log", "size": 51200, "mtime": "2024-04-19T10:20:30Z"}
]
```

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
    {"timestamp": "2024-04-19T12:00:00Z", "level": "INFO", "message": "Starting conversion", "job_id": 42}
  ],
  "file_size": 102400
}
```

**Query parameters:**

| Parameter | Default | Description |
| --- | --- | --- |
| `lines` | `200` | Maximum lines to return (tail of file) |
| `level` | — | Minimum log level filter: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `job_id` | — | Filter entries to a specific job |
| `offset` | — | Return content starting from this byte offset (auto-resets on log rotation) |

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

| Field | Description |
| --- | --- |
| `path` | Directory to scan |
| `interval` | Scan interval in seconds |
| `rewrite_from` | Path prefix to replace before submitting jobs |
| `rewrite_to` | Replacement prefix |
| `enabled` | Set to `false` to disable without removing the entry |

Files already in the `scanned_files` database table are skipped on subsequent scans. Only extensions matching `media_extensions` are submitted. `.mp4` files are always skipped (SMA converts *to* mp4 — any `.mp4` present is already processed).

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

The daemon automatically purges old media files from every `recycle-bin` directory configured in any `autoProcess.ini`. Two independent eviction triggers run once per hour:

| Trigger | Key | Default | Behaviour |
| --- | --- | --- | --- |
| Age | `recycle_bin_max_age_days` | `3` | Delete files whose last-modified time is older than N days |
| Space pressure | `recycle_bin_min_free_gb` | `50` | Delete the oldest files first until free space on the mount point exceeds N GiB |

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

## Job Persistence

Jobs are stored in a PostgreSQL database configured via `SMA_DAEMON_DB_URL` or `db_url` in `daemon.json`. The database provides restart recovery, job history, cluster coordination, and deduplication across nodes.

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
2. `db_url` field in `daemon.json`

```bash
# daemon.json
{ "db_url": "postgresql://sma:password@db-host:5432/sma" }

# daemon.env
SMA_DAEMON_DB_URL=postgresql://sma:password@db-host:5432/sma
```

**Cluster-specific options:**

- `--heartbeat-interval N` — seconds between node heartbeat updates (default: 30)
- `--stale-seconds N` — seconds without a heartbeat before a node's running jobs are requeued (default: 120)

**Cluster status:** The `/status` endpoint returns all nodes with their active jobs, worker count, uptime, and last-seen time. The dashboard shows this as a Cluster Nodes panel.

**Remote restart/shutdown:** Use the dashboard buttons or API with `?node=<id>`:

```bash
# Restart a specific node
curl -X POST "http://localhost:8585/restart?node=media-server-2" -H "X-API-Key: SECRET"

# Shutdown all nodes
curl -X POST http://localhost:8585/shutdown -H "X-API-Key: SECRET"
```

---

## Config Reload

Reload `daemon.json` without restarting the daemon or interrupting active conversions:

```bash
curl -X POST http://localhost:8585/reload -H "X-API-Key: SECRET"
```

Reloaded immediately: `path_configs`, `path_rewrites`, `scan_paths`, `api_key`, `media_extensions`, `default_args`, `ffmpeg_dir`.

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

| Variable | Description |
| --- | --- |
| `SMA_DAEMON_API_KEY` | API key (overrides `--api-key`) |
| `SMA_DAEMON_DB_URL` | PostgreSQL connection URL |
| `SMA_DAEMON_FFMPEG_DIR` | Directory containing `ffmpeg`/`ffprobe` (prepended to PATH) |
| `SMA_DAEMON_HOST` | Bind host (Docker default: `0.0.0.0`) |
| `SMA_DAEMON_PORT` | Port (Docker default: `8585`) |
| `SMA_DAEMON_WORKERS` | Number of concurrent workers (Docker default: `2`) |
| `SMA_DAEMON_CONFIG` | Path to `daemon.json` |
| `SMA_DAEMON_LOGS_DIR` | Directory for per-config log files |
