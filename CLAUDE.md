# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git Commit Rules

- **Do NOT add `Co-Authored-By` lines referencing Claude, Anthropic, or any AI to commit messages.**
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
```

Requires Python 3.12+ and FFmpeg installed on system.

## Project Overview

SMA-NG (Next-Generation Media Automator) is a Python-based media conversion and tagging automation tool. It converts media files to MP4 format using FFmpeg and tags them with metadata from TMDB. It integrates with media managers (Sonarr, Radarr, Sickbeard) and downloaders (NZBGet, SABNZBD, Deluge, uTorrent, qBittorrent).

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
python daemon.py --host 0.0.0.0 --port 8585 --workers 2

# Start daemon with API key authentication
python daemon.py --host 0.0.0.0 --port 8585 --api-key YOUR_SECRET_KEY
```

## Daemon Mode

The daemon runs an HTTP server that listens for webhook requests to trigger conversions.

```bash
# Start with defaults (127.0.0.1:8585, 2 workers)
python daemon.py

# Listen on all interfaces
python daemon.py --host 0.0.0.0 --port 8585

# With API key authentication
python daemon.py --api-key YOUR_SECRET_KEY
# Or use environment variable:
SMA_DAEMON_API_KEY=YOUR_SECRET_KEY python daemon.py

# Custom daemon config (for path mappings)
python daemon.py --daemon-config /path/to/daemon.json --workers 4
```

**Endpoints:**
- `POST /webhook` - Submit conversion job (returns job_id)
- `GET /health` - Health check with job statistics
- `GET /jobs` - List jobs (`?status=pending&limit=50&offset=0`)
- `GET /jobs/<id>` - Get specific job details
- `GET /configs` - Show path-to-config mappings and status
- `GET /stats` - Job statistics by status
- `POST /cleanup` - Remove old completed/failed jobs (`?days=30`)

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

Public endpoints (no auth required): `/`, `/health`, `/status`

### Path-Based Configuration

The daemon can use different `autoProcess.ini` files based on the input file path. Create `config/daemon.json` (copy from `setup/daemon.json.sample`):

```json
{
  "default_config": "config/autoProcess.ini",
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
|-------------|----------|
| `config/autoProcess.ini` | `logs/autoProcess.log` |
| `config/autoProcess.tv.ini` | `logs/autoProcess.tv.log` |
| `config/autoProcess.movies-4k.ini` | `logs/autoProcess.movies-4k.log` |

Log files use rotation (10MB max, 5 backups). Use `--logs-dir` to change the logs directory.

### Concurrency Control

Only one conversion process runs per config at a time. This prevents resource conflicts when multiple jobs target the same media library.

- Jobs for the **same config** execute sequentially (queue up)
- Jobs for **different configs** can run in parallel (up to `--workers` count)

Example with 2 workers and 4 jobs:
```
Job 1: /TV/show.mkv      -> autoProcess.tv.ini     [runs immediately]
Job 2: /Movies/film.mkv  -> autoProcess.movies.ini [runs immediately]
Job 3: /TV/other.mkv     -> autoProcess.tv.ini     [waits for Job 1]
Job 4: /Movies/other.mkv -> autoProcess.movies.ini [waits for Job 2]
```

Check active/waiting jobs via the health endpoint:
```bash
curl http://localhost:8585/health
# Returns: {"active_jobs": {...}, "waiting_jobs": {...}, ...}
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

## Architecture

### Entry Points
- `manual.py` - CLI tool for manual conversion/tagging
- `daemon.py` - HTTP webhook server for triggering conversions via API
- `postSonarr.py` / `postRadarr.py` - Post-processing scripts triggered by media managers
- `postSickbeard.py` - Sickbeard post-processing
- `*PostProcess.py` - Scripts for various downloaders (NZBGet, SAB, Deluge, qBittorrent, uTorrent)

### Core Modules

**resources/**
- `mediaprocessor.py` - Central class `MediaProcessor` handling the full conversion pipeline: validation, FFmpeg conversion, tagging, file operations, and post-processing
- `readsettings.py` - `ReadSettings` class parses `autoProcess.ini`, defines all defaults in `DEFAULTS` dict
- `metadata.py` - `Metadata` class fetches and writes tags from TMDB using `tmdbsimple` and `mutagen`
- `postprocess.py` - Runs custom post-process scripts from `post_process/` directory
- `extensions.py` - Contains TMDB API key and file extension definitions

**converter/**
- `ffmpeg.py` - FFmpeg/FFprobe wrapper with `MediaFormatInfo`, `MediaStreamInfo`, progress parsing
- `avcodecs.py` - Codec definitions with FFmpeg encoder mappings
- `formats.py` - Container format definitions

**autoprocess/**
- `sonarr.py` / `radarr.py` - API integrations for triggering rescans/renames
- `plex.py` - Plex library refresh integration

### Configuration

The main config file is `config/autoProcess.ini` (copy from `setup/autoProcess.ini.sample`). Override location via `SMA_CONFIG` environment variable.

Key sections:
- `[Converter]` - FFmpeg paths, output format, threading
- `[Video]` - Codec preferences, bitrate, CRF profiles
- `[Audio]` - Codec, languages, channel handling
- `[Subtitle]` - Embedding, burning, subtitle downloads via Subliminal
- `[Sonarr]`/`[Radarr]` - API settings for media manager integration
- `[Plex]` - Library refresh settings

### Processing Flow

1. `MediaProcessor.isValidSource()` validates input using FFprobe
2. `MediaProcessor.process()` builds FFmpeg options based on settings and source info
3. Conversion runs with optional progress reporting
4. `Metadata.writeTags()` embeds metadata using mutagen (MP4 tags)
5. `qtfaststart` relocates moov atom for streaming optimization
6. Files copied/moved to destination directories
7. Post-process scripts run, Plex/media manager notified

## Claude Code Slash Commands

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
- Create new `*PostProcess.py` entry point
- Add settings section in `readsettings.py`
