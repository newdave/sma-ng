# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Git Commit Rules

- **Do NOT add `Co-Authored-By` lines referencing Codex, Anthropic, or any AI to commit messages.**
- Do not add any AI attribution to commits whatsoever.

## Development Environment

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r setup/requirements.txt

# For qBittorrent integration
pip install -r setup/requirements-qbittorrent.txt

# For Deluge integration
pip install -r setup/requirements-deluge.txt

# Generate config with auto-detected GPU (nvenc, qsv, vaapi, videotoolbox, or software)
make config

# Override GPU detection: make config GPU=nvenc
# Detect GPU without generating config: make detect-gpu
```

Requires Python 3.12+ and FFmpeg installed on system.

## Project Overview

SMA-NG (Next-Generation Media Automator) is a Python-based media conversion and tagging automation tool. It converts media files to MP4 format using FFmpeg and tags them with metadata from TMDB. It integrates with media managers (Sonarr, Radarr) and downloaders (NZBGet, SABNZBD, Deluge, uTorrent, qBittorrent).

## Common Commands

```bash
# Install dependencies
pip install -r setup/requirements.txt

# Manual conversion with auto-tagging (guesses metadata from filename)
python manual.py -i "/path/to/file.mkv" -a

# Manual conversion with specific TMDB ID
python manual.py -i "/path/to/movie.mkv" -tmdb 603

# TV episode with TVDB ID
python manual.py -i "/path/to/episode.mkv" -tvdb 73871 -s 3 -e 10

# Process directory in batch mode
python manual.py -i "/path/to/directory" -a

# Show conversion options without converting
python manual.py -i "/path/to/file.mkv" -oo

# List supported codecs
python manual.py -cl

# Start daemon (HTTP webhook server)
python daemon.py --host 0.0.0.0 --port 8585 --workers 4

# Start daemon with API key authentication
python daemon.py --host 0.0.0.0 --port 8585 --workers 4 --api-key YOUR_SECRET_KEY
```

## Daemon Mode

The daemon runs an HTTP server that listens for webhook requests to trigger conversions.

```bash
# Start with defaults (127.0.0.1:8585, 1 worker)
python daemon.py

# Listen on all interfaces with multiple workers
python daemon.py --host 0.0.0.0 --port 8585 --workers 4

# With API key authentication
python daemon.py --api-key YOUR_SECRET_KEY
# Or use environment variable:
SMA_DAEMON_API_KEY=YOUR_SECRET_KEY python daemon.py

# Custom daemon config (for path mappings)
python daemon.py --daemon-config /path/to/daemon.json --workers 4

# Graceful shutdown (waits for active conversions to finish)
curl -X POST http://localhost:8585/shutdown -H "X-API-Key: YOUR_SECRET_KEY"
```

**Endpoints:**

- `POST /webhook` - Submit conversion job (returns job_id)
- `GET /health` - Health check with job statistics
- `GET /jobs` - List jobs (`?status=pending&limit=50&offset=0`)
- `GET /jobs/<id>` - Get specific job details
- `GET /configs` - Show path-to-config mappings and status
- `GET /stats` - Job statistics by status
- `POST /cleanup` - Remove old completed/failed jobs (`?days=30`)
- `POST /shutdown` - Graceful shutdown (waits for active conversions to finish)

**Request formats:**

```bash
# Plain text body (just the path)
curl -X POST http://localhost:8585/webhook -d "/path/to/movie.mkv"

# JSON body
curl -X POST http://localhost:8585/webhook \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/movie.mkv"}'

# JSON with extra manual.py arguments
curl -X POST http://localhost:8585/webhook \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/movie.mkv", "args": ["-tmdb", "603"]}'

# JSON with config override (bypasses path matching)
curl -X POST http://localhost:8585/webhook \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/movie.mkv", "config": "/custom/autoProcess.ini"}'
```

### Authentication

The daemon supports API key authentication. When enabled, all endpoints except `/health` require a valid API key.

**Configure API key (priority order):**

1. Command line: `--api-key YOUR_SECRET_KEY`
2. Environment variable: `SMA_DAEMON_API_KEY=YOUR_SECRET_KEY`
3. Config file: `"api_key": "YOUR_SECRET_KEY"` in daemon.json

**Send authenticated requests:**

```bash
# Using X-API-Key header (recommended)
curl -X POST http://localhost:8585/webhook \
  -H "X-API-Key: YOUR_SECRET_KEY" \
  -d "/path/to/movie.mkv"

# Using Authorization Bearer header
curl -X POST http://localhost:8585/webhook \
  -H "Authorization: Bearer YOUR_SECRET_KEY" \
  -d "/path/to/movie.mkv"
```

Public endpoints (no auth required): `/`, `/dashboard`, `/health`, `/status`, `/docs`

### Path-Based Configuration

The daemon can use different `autoProcess.ini` files based on the input file path. Create `config/daemon.json` (copy from `setup/daemon.json.sample`):

```json
{
  "default_config": "config/autoProcess.ini",
  "api_key": "your_secret_key",
  "db_url": null,
  "ffmpeg_dir": null,
  "media_extensions": [".mp4", ".mkv", ".avi", ".mov", ".ts"],
  "scan_paths": [
    {
      "path": "/mnt/local/Media",
      "interval": 3600,
      "rewrite_from": "/mnt/local/Media",
      "rewrite_to": "/mnt/unionfs/Media"
    }
  ],
  "path_configs": [
    {
      "path": "/mnt/unionfs/Media/TV",
      "config": "config/autoProcess.tv.ini"
    },
    {
      "path": "/mnt/unionfs/Media/Movies/4K",
      "config": "config/autoProcess.movies-4k.ini"
    },
    {
      "path": "/mnt/unionfs/Media/Movies",
      "config": "config/autoProcess.movies.ini"
    }
  ]
}
```

**Matching rules:**

- Paths are matched using longest-prefix-first (more specific paths take priority)
- `/mnt/unionfs/Media/Movies/4K/film.mkv` matches `Movies/4K` config, not `Movies`
- Paths not matching any prefix use `default_config`
- Config paths can be relative (to SMA-NG root) or absolute

### Per-Config Logging

The daemon logs output to separate files in the `logs/` directory based on which config is used:

| Config File | Log File |
| --- | --- |
| `config/autoProcess.ini` | `logs/autoProcess.log` |
| `config/autoProcess.tv.ini` | `logs/autoProcess.tv.log` |
| `config/autoProcess.movies-4k.ini` | `logs/autoProcess.movies-4k.log` |

Log files use rotation (10MB max, 5 backups). Use `--logs-dir` to change the logs directory.

### Concurrency Control

Up to `--workers` jobs can run simultaneously. Concurrency is managed per-config using a semaphore: jobs for the same config run concurrently up to the worker limit, and jobs for different configs always run in parallel.

- Jobs for **different configs** run in parallel immediately
- Jobs for the **same config** run concurrently up to the worker count, then queue
- Use `--workers N` to set concurrency (default: 1)

Example with `--workers 4` and 5 queued jobs:

```text
Job 1: /TV/show1.mkv     -> autoProcess.tv.ini     [runs immediately]
Job 2: /TV/show2.mkv     -> autoProcess.tv.ini     [runs immediately]
Job 3: /Movies/film1.mkv -> autoProcess.movies.ini [runs immediately]
Job 4: /Movies/film2.mkv -> autoProcess.movies.ini [runs immediately]
Job 5: /TV/show3.mkv     -> autoProcess.tv.ini     [waits for slot]
```

Check active/waiting jobs via the health endpoint:

```bash
curl http://localhost:8585/health
# Returns: {"active": {...}, "waiting": {...}, ...}
```

### SQLite Persistence

Jobs are stored in `config/daemon.db` (SQLite). This provides:

- **Restart recovery**: Pending/interrupted jobs resume automatically
- **Job history**: View completed/failed jobs with timestamps
- **Filtering**: Query jobs by status, config, with pagination

```bash
# List pending jobs
curl "http://localhost:8585/jobs?status=pending"

# Get specific job
curl http://localhost:8585/jobs/42

# View statistics
curl http://localhost:8585/stats
# Returns: {"pending": 3, "running": 1, "completed": 150, "failed": 2, "total": 156}

# Cleanup old jobs (default: 30 days)
curl -X POST "http://localhost:8585/cleanup?days=7"
```

**Database schema:**

```sql
jobs(id, path, config, args, status, worker_id, error, created_at, started_at, completed_at)
```

Use `--db /path/to/daemon.db` to customize database location.

### PostgreSQL (Distributed / Multi-Node)

For multi-node deployments, the daemon can use a shared PostgreSQL database instead of SQLite. This enables distributed job coordination — no two nodes will ever process the same file.

**Configure PostgreSQL (priority order):**

1. Command line: `--db-url postgresql://user:pass@host/sma`
2. Environment variable: `SMA_DAEMON_DB_URL=postgresql://user:pass@host/sma`
3. Config file: `"db_url": "postgresql://user:pass@host/sma"` in daemon.json

When `db_url` is set, `--db` (SQLite path) is ignored.

**daemon.json example:**

```json
{
  "default_config": "config/autoProcess.ini",
  "db_url": "postgresql://sma:password@db-host:5432/sma",
  "path_configs": [...]
}
```

**daemon.env example:**

```bash
SMA_DAEMON_DB_URL=postgresql://sma:password@db-host:5432/sma
```

**Cluster-specific options:**

- `--heartbeat-interval N` — seconds between node heartbeat updates (default: 30)
- `--stale-seconds N` — seconds without a heartbeat before a node's running jobs are requeued (default: 120)

The `/health` endpoint includes cluster-wide status when using PostgreSQL, showing active and waiting jobs across all nodes.

## Architecture

### Entry Points

- `manual.py` - CLI tool for manual conversion/tagging
- `daemon.py` - HTTP webhook server for triggering conversions via API
- `triggers/media_managers/sonarr.sh` / `radarr.sh` - Bash scripts triggered by Sonarr/Radarr
- `triggers/usenet/` / `triggers/torrents/` - Bash scripts for download client integrations

### Core Modules

#### `resources/`

- `mediaprocessor.py` - Central class `MediaProcessor` handling the full conversion pipeline: validation, FFmpeg conversion, tagging, file operations, and post-processing
- `readsettings.py` - `ReadSettings` class parses `autoProcess.ini`, defines all defaults in `DEFAULTS` dict
- `metadata.py` - `Metadata` class fetches and writes tags from TMDB using `tmdbsimple` and `mutagen`
- `postprocess.py` - Runs custom post-process scripts from `post_process/` directory
- `extensions.py` - Contains TMDB API key and file extension definitions

#### `converter/`

- `ffmpeg.py` - FFmpeg/FFprobe wrapper with `MediaFormatInfo`, `MediaStreamInfo`, progress parsing
- `avcodecs.py` - Codec definitions with FFmpeg encoder mappings
- `formats.py` - Container format definitions

#### `autoprocess/`

- `plex.py` - Plex library refresh integration

#### Additional `resources/`

- `mediamanager.py` - Shared Sonarr/Radarr API helpers used by trigger scripts

### Configuration

The main config file is `config/autoProcess.ini` (copy from `setup/autoProcess.ini.sample`). Override location via `SMA_CONFIG` environment variable.

Key sections:

- `[Converter]` - FFmpeg paths, output format, threading, file disposition (`delete-original`, `copy-to`, `move-to`, `recycle-bin`)
- `[Video]` - Codec preferences, bitrate, CRF profiles
- `[Audio]` - Codec, languages, channel handling
- `[Subtitle]` - Embedding, burning, subtitle downloads via Subliminal
- `[Sonarr]`/`[Radarr]` - API settings for media manager integration
- `[Plex]` - Library refresh settings

### Recycle Bin

When `delete-original = True`, SMA can preserve the original source file in a configurable directory before deleting it. Set `recycle-bin` in `[Converter]`:

```ini
[Converter]
delete-original = True
recycle-bin = /mnt/recycle
```

- Only runs when `delete-original = True` and the recycle bin path is set
- Uses atomic copy (temp file + `os.replace`) — safe across filesystems
- Appends `.2`, `.3`... suffix if a file with the same name already exists in the bin
- A failed copy is logged but does not abort the conversion or the deletion

### Processing Flow

1. `MediaProcessor.isValidSource()` validates input using FFprobe
2. `MediaProcessor.process()` builds FFmpeg options based on settings and source info
3. Conversion runs with optional progress reporting
4. `Metadata.writeTags()` embeds metadata using mutagen (MP4 tags)
5. `qtfaststart` relocates moov atom for streaming optimization
6. Files copied/moved to destination directories
7. Post-process scripts run, Plex/media manager notified

## CI / Release

### Workflows

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `ci.yml` | PR / push to main | Runs tests |
| `docker.yml` | PR / push to main (path-filtered) | PR: build-only + smoke test; main: build + push rolling `main` tag to GHCR |
| `release.yml` | Push to main | Runs release-please (manages release PR + version bump); on release: builds wheel/sdist + Docker image with semver tags |

### Release Flow

Releases are driven by [release-please](https://github.com/googleapis/release-please). No manual tagging.

1. Merge conventional commits to `main` — release-please opens/updates a Release PR
2. Merge the Release PR — release-please creates the GitHub Release and `v*` tag
3. The `publish` and `docker` jobs in `release.yml` run automatically:
   - Python wheel + sdist built and attached to the GitHub Release
   - Docker image pushed to GHCR with tags `1.2.3`, `1.2`, `1`, and `latest`

### Version Source of Truth

`pyproject.toml` → `[project] version` is the single version source. release-please bumps it when a release PR is merged. The Git tag and Docker image tags are derived from it.

Do **not** manually create `v*` tags — this will cause a duplicate release.

### Conventional Commits

release-please determines the next version from commit messages:

- `fix:` → patch bump (1.2.3 → 1.2.4)
- `feat:` → minor bump (1.2.3 → 1.3.0)
- `feat!:` or `BREAKING CHANGE:` → major bump (1.2.3 → 2.0.0)

## Codex Slash Commands

- `/project:convert <file>` - Run conversion with auto-tagging
- `/project:preview <file>` - Show FFmpeg options without converting
- `/project:codecs` - List all supported codecs
- `/project:daemon` - Start the HTTP webhook daemon server

## Key Files for Modifications

When adding new codec support, modify:

- `converter/avcodecs.py` - Add codec class with FFmpeg encoder mapping

When adding new settings, modify:

- `resources/readsettings.py` - Add to `DEFAULTS` dict and `readConfig()` method
- `setup/autoProcess.ini.sample` - Add default value

When adding new downloader/manager integration:

- Create new bash script in `triggers/` (usenet/, torrents/, or media_managers/)
- Add settings section in `readsettings.py` if config support is needed
