# SMA-NG — Next-Generation Media Automator

![SMA-NG](../logo.png)

Automated media conversion, tagging, and integration pipeline. Converts media files to MP4/MKV using FFmpeg with hardware acceleration, tags them with TMDB metadata, and integrates with media managers and download clients.

## Table of Contents

- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Configuration Reference](#configuration-reference)
- [CLI Usage (manual.py)](#cli-usage)
- [Daemon Mode](#daemon-mode)
  - [Starting](#starting)
  - [Concurrency](#concurrency)
  - [Web Dashboard](#web-dashboard)
  - [API Endpoints](#api-endpoints)
  - [Webhook Request Formats](#webhook-request-formats)
  - [Authentication](#authentication)
  - [Path-Based Configuration (daemon.json)](#path-based-configuration-daemonjson)
  - [Per-Config Logging](#per-config-logging)
  - [Scheduled Directory Scanning](#scheduled-directory-scanning)
  - [SQLite Persistence](#sqlite-persistence)
  - [PostgreSQL (Distributed / Multi-Node)](#postgresql-distributed--multi-node)
  - [Graceful Shutdown](#graceful-shutdown)
- [Media Manager Integration](#media-manager-integration)
- [Download Client Integration](#download-client-integration)
- [Hardware Acceleration](#hardware-acceleration)
- [Processing Pipeline](#processing-pipeline)
- [Module Reference](#module-reference)
- [Post-Process Scripts](#post-process-scripts)
- [Deployment (mise)](#deployment-mise)
- [Troubleshooting](#troubleshooting)

---

## Requirements

- Python 3.12+
- FFmpeg (system install or custom path)
- Python packages: `pip install -r setup/requirements.txt`

Optional:

- qBittorrent integration: `pip install -r setup/requirements-qbittorrent.txt`
- Deluge integration: `pip install -r setup/requirements-deluge.txt`

## Quick Start

### With mise (recommended)

[mise](https://mise.jdx.dev/) is a dev-tool manager. Install it once, then:

```bash
# Clone and set up
git clone <repo> && cd sma

# Install Python 3.12, create venv, install dependencies
mise install
mise run install

# Generate config (auto-detects GPU)
mise run config

# Test a conversion
mise run convert -- /path/to/file.mkv

# Start the daemon
mise run daemon
```

### Without mise

```bash
git clone <repo> && cd sma
python3 -m venv venv && source venv/bin/activate
pip install -r setup/requirements.txt

# Generate config with auto GPU detection
make config

# Or copy sample and edit manually
cp setup/autoProcess.ini.sample config/autoProcess.ini
$EDITOR config/autoProcess.ini

# Test a conversion
python manual.py -i /path/to/file.mkv -a

# Start the daemon
python daemon.py --host 0.0.0.0 --port 8585
```

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                         Entry Points                            │
├──────────┬──────────┬──────────────────────────────────────────┤
│ manual.py│daemon.py │         triggers/ (bash scripts)         │
│ CLI tool │HTTP server│  sonarr.sh  radarr.sh  sabnzbd.sh  ...  │
└────┬─────┴────┬─────┴──────────────────────┬───────────────────┘
     │          │           │           │              │
     ▼          ▼           ▼           ▼              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    resources/mediaprocessor.py                   │
│                     MediaProcessor (core)                        │
│  isValidSource → generateOptions → convert → tag → replicate    │
├─────────────────────┬───────────────────┬───────────────────────┤
│ resources/          │ converter/        │ autoprocess/           │
│  readsettings.py    │  __init__.py      │  plex.py              │
│  mediamanager.py    │  ffmpeg.py        │                       │
│  metadata.py        │  avcodecs.py      │                       │
│  postprocess.py     │  formats.py       │                       │
│  extensions.py      │                   │                       │
│  log.py / lang.py   │                   │                       │
└─────────────────────┴───────────────────┴───────────────────────┘
```

### Data Flow

```text
Input File
  → FFprobe validation (isValidSource)
  → Stream analysis & option generation (generateOptions)
  → FFmpeg conversion with HW accel (convert)
  → TMDB metadata tagging (writeTags)
  → moov atom relocation for streaming (QTFS)
  → File placement: output_dir → restore → copy-to / move-to (replicate)
  → Post-process scripts + Plex/Sonarr/Radarr notifications (post)
  → Output files
```

---

## Configuration Reference

Configuration lives in `config/autoProcess.ini` (INI format). Copy from `setup/autoProcess.ini.sample`.

Override path via `SMA_CONFIG` environment variable.

### [Converter]

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `ffmpeg` | path | `ffmpeg` | Path to FFmpeg binary |
| `ffprobe` | path | `ffprobe` | Path to FFprobe binary |
| `threads` | int | `0` | FFmpeg threads (0 = auto) |
| `hwaccels` | list | | Hardware acceleration platforms: `qsv`, `vaapi`, `cuda`, `videotoolbox` |
| `hwaccel-decoders` | list | | HW decoders to use: `hevc_qsv`, `h264_qsv`, `h264_vaapi`, etc. |
| `hwdevices` | dict | | Device mapping: `qsv:/dev/dri/renderD128` |
| `hwaccel-output-format` | dict | | Output format per hwaccel: `qsv:qsv`, `vaapi:vaapi` |
| `output-directory` | path | | Temporary output location (files moved back after) |
| `output-format` | string | `mp4` | Container format: `mp4`, `mkv`, `mov` |
| `output-extension` | string | `mp4` | Output file extension |
| `temp-extension` | string | | Temporary file extension during conversion |
| `temp-output` | bool | `true` | Use temporary output file during conversion |
| `minimum-size` | int | `0` | Minimum source file size in MB (0 = disabled) |
| `ignored-extensions` | list | `nfo, ds_store` | Extensions to skip |
| `copy-to` | path(s) | | Copy output to additional directories (pipe-separated) |
| `move-to` | path | | Move output to final destination |
| `delete-original` | bool | `true` | Delete source file after successful conversion |
| `recycle-bin` | path | | Copy original here before deleting (only when `delete-original = True`) |
| `process-same-extensions` | bool | `false` | Reprocess files already in output format |
| `bypass-if-copying-all` | bool | `false` | Skip conversion if all streams can be copied |
| `force-convert` | bool | `false` | Force conversion even if codec matches |
| `post-process` | bool | `false` | Run post-process scripts |
| `wait-post-process` | bool | `false` | Wait for post-process scripts to finish |
| `preopts` | list | | Extra FFmpeg options before input |
| `postopts` | list | | Extra FFmpeg options after codec options |
| `opts-separator` | string | `,` | Separator for preopts/postopts lists |

### [Video]

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `codec` | list | `h264` | Video codecs in priority order. First is used for encoding, rest are copy-eligible. See [Supported Codecs](#supported-codecs) |
| `max-bitrate` | int | `0` | Maximum video bitrate in kbps (0 = unlimited). Source exceeding this is re-encoded |
| `bitrate-ratio` | dict | | Scale source bitrate per codec: `hevc:1.0, h264:0.65, mpeg2video:0.45` |
| `crf` | int | `24` | Constant Rate Factor (quality). Lower = better quality, larger files |
| `crf-profiles` | list | | Tiered CRF by source bitrate: `20000:20:5000k:10000k, 10000:22:5000k:10000k` (format: `source_kbps:crf:maxrate:bufsize`) |
| `preset` | string | | Encoder preset: `ultrafast` to `veryslow` (speed vs compression) |
| `profile` | list | | Video profile: `main`, `high`, `main10` |
| `max-level` | float | | Maximum H.264/H.265 level (e.g., `5.2`) |
| `max-width` | int | `0` | Maximum output width (0 = no limit). Triggers downscale |
| `pix-fmt` | list | | Pixel format whitelist. Non-matching sources are re-encoded |
| `dynamic-parameters` | bool | `false` | Pass HDR/color metadata to encoder |
| `prioritize-source-pix-fmt` | bool | `true` | Keep source pix_fmt if in whitelist |
| `filter` | string | | Custom FFmpeg video filter |
| `force-filter` | bool | `false` | Force re-encode when filter is set |
| `codec-parameters` | string | | Extra codec params (e.g., x265-params) |

### [HDR]

Override video settings for HDR content (detected automatically).

| Option | Type | Description |
| --- | --- | --- |
| `codec` | list | Video codec for HDR content |
| `pix-fmt` | list | Pixel format for HDR (e.g., `p010le`) |
| `space` | list | Color space: `bt2020nc` |
| `transfer` | list | Transfer function: `smpte2084` |
| `primaries` | list | Color primaries: `bt2020` |
| `preset` | string | Encoder preset override for HDR |
| `profile` | string | Profile override for HDR |
| `codec-parameters` | string | Extra params for HDR encoding |
| `filter` | string | Video filter for HDR content |
| `force-filter` | bool | Force re-encode for HDR filter |

### [Audio]

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `codec` | list | `aac` | Audio codecs in priority order. Streams matching any are copied; others re-encoded to first |
| `languages` | list | | Language whitelist (ISO 639-3, e.g., `eng`). Empty = all languages |
| `default-language` | string | `eng` | Default language for unlabeled streams |
| `first-stream-of-language` | bool | `false` | Keep only first stream per language |
| `allow-language-relax` | bool | `true` | If no whitelisted language found, keep all audio |
| `include-original-language` | bool | `false` | Include original media language even if not in whitelist |
| `channel-bitrate` | int | `128` | Bitrate per channel in kbps (0 = auto) |
| `variable-bitrate` | int | `0` | VBR quality level (0 = disabled/CBR) |
| `max-bitrate` | int | `0` | Maximum audio bitrate in kbps |
| `max-channels` | int | `0` | Maximum audio channels (0 = unlimited, 6 = 5.1) |
| `copy-original` | bool | `false` | Copy original audio stream in addition to transcoded |
| `aac-adtstoasc` | bool | `true` | Apply AAC ADTS to ASC bitstream filter |
| `ignored-dispositions` | list | | Skip streams with these dispositions: `comment`, `hearing_impaired` |
| `unique-dispositions` | bool | `false` | One stream per disposition per language |
| `stream-codec-combinations` | list | | Identify duplicate streams by codec combo |
| `ignore-trudhd` | bool | `true` | Ignore TrueHD streams |
| `atmos-force-copy` | bool | `false` | Always copy Atmos tracks |
| `force-default` | bool | `false` | Override source default stream |
| `relax-to-default` | bool | `false` | If preferred language absent, default to any |

### [Audio.Sorting]

| Option | Type | Description |
| --- | --- | --- |
| `sorting` | list | Sort order for audio streams: `language, channels.d, map, d.comment` |
| `default-sorting` | list | Sort order for default stream selection |
| `codecs` | list | Codec priority for sorting |

### [Universal Audio]

Generates an additional audio stream (usually stereo AAC) for maximum device compatibility.

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `codec` | list | | UA codec (e.g., `aac`). Empty = disabled |
| `channel-bitrate` | int | `128` | Bitrate per channel |
| `first-stream-only` | bool | `true` | Only add UA for first audio stream |

### [Subtitle]

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `codec` | list | `mov_text` | Subtitle codec for text-based subs |
| `codec-image-based` | list | | Codec for image-based subs (PGS, VobSub) |
| `languages` | list | | Language whitelist (ISO 639-3) |
| `default-language` | string | `eng` | Default for unlabeled subs |
| `first-stream-of-language` | bool | `false` | One subtitle per language |
| `burn-subtitles` | bool | `false` | Burn subtitles into video |
| `burn-dispositions` | list | `forced` | Only burn subs with these dispositions |
| `embed-subs` | bool | `true` | Embed subtitle streams in output |
| `embed-image-subs` | bool | `false` | Embed image-based subs |
| `embed-only-internal-subs` | bool | `false` | Only embed subs from source (no external files) |
| `ignored-dispositions` | list | | Skip subs with these dispositions |
| `remove-bitstream-subs` | list | `true` | Remove bitstream subtitle formats |
| `include-original-language` | bool | `false` | Include original language subs |

### [Subtitle.CleanIt]

| Option | Type | Description |
| --- | --- | --- |
| `enabled` | bool | Enable subtitle cleaning via cleanit library |
| `config-path` | path | Custom cleanit config |
| `tags` | list | Cleanit tag sets: `default, no-style` |

### [Subtitle.FFSubsync]

| Option | Type | Description |
| --- | --- | --- |
| `enabled` | bool | Enable subtitle sync via ffsubsync |

### [Subtitle.Subliminal]

| Option | Type | Description |
| --- | --- | --- |
| `download-subs` | bool | Download missing subtitles |
| `providers` | list | Subtitle providers: `opensubtitles` |
| `download-forced-subs` | bool | Download forced subtitle variants |
| `download-hearing-impaired-subs` | bool | Include HI subs in downloads |

### [Metadata]

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `relocate-moov` | bool | `true` | Move moov atom to file start (streaming optimization) |
| `full-path-guess` | bool | `true` | Use full file path for guessit metadata matching |
| `tag` | bool | `true` | Enable TMDB metadata tagging |
| `tag-language` | string | `eng` | Language for TMDB metadata |
| `download-artwork` | bool | `false` | Embed cover art from TMDB |
| `strip-metadata` | bool | `true` | Remove existing metadata before tagging |
| `keep-titles` | bool | `false` | Preserve original stream titles |

### [Permissions]

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `chmod` | octal | `0664` | File permissions for output |
| `uid` | int | `-1` | Owner UID (-1 = no change) |
| `gid` | int | `-1` | Group GID (-1 = no change) |

### [Sonarr] / [Sonarr-Kids] / [Radarr] / [Radarr-4K] / [Radarr-Kids]

Multiple instances supported. Any section starting with `Sonarr` or `Radarr` is loaded.

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `host` | string | `localhost` | API hostname |
| `port` | int | `8989`/`7878` | API port |
| `apikey` | string | | API key |
| `ssl` | bool | `false` | Use HTTPS |
| `webroot` | string | | URL base path |
| `path` | string | | Media root path for directory matching (manual.py rescan) |
| `force-rename` | bool | `false` | Trigger rename after processing |
| `rescan` | bool | `true` | Trigger library rescan after processing |
| `block-reprocess` | bool | `false` | Prevent reprocessing same-extension files |
| `in-progress-check` | bool | `true` | Wait for in-progress scans before rescanning |

Instances are matched by `path` using longest-prefix matching. When `manual.py` processes a file, it finds the matching instance and triggers a rescan via the API.

### [Plex]

| Option | Type | Description |
| --- | --- | --- |
| `host` | string | Plex server hostname |
| `port` | int | Plex server port (default 32400) |
| `refresh` | bool | Trigger library refresh after processing |
| `token` | string | Plex authentication token |
| `ssl` | bool | Use HTTPS |
| `ignore-certs` | bool | Skip SSL certificate verification |
| `path-mapping` | dict | Map SMA-NG paths to Plex library paths (comma-separated, `=` delimited) |

### [SABNZBD] / [Deluge] / [qBittorrent] / [uTorrent]

Download client integration settings. Each has category/label mappings for routing downloads to the correct media manager.

---

## CLI Usage

### manual.py

```bash
# Basic conversion with auto-tagging
python manual.py -i /path/to/file.mkv -a

# Specify TMDB ID for movies
python manual.py -i /path/to/movie.mkv -tmdb 603

# TV episode with TVDB ID
python manual.py -i /path/to/episode.mkv -tvdb 73871 -s 3 -e 10

# Batch process a directory
python manual.py -i /path/to/directory/ -a

# Preview conversion options (no conversion)
python manual.py -i /path/to/file.mkv -oo

# List supported codecs
python manual.py -cl

# Use alternate config file
python manual.py -i /path/to/file.mkv -a -c config/autoProcess.ini-movies4k

# Force re-encode even if format matches
python manual.py -i /path/to/file.mp4 -a -fc

# Convert without tagging
python manual.py -i /path/to/file.mkv -a -nt

# Tag only (no conversion)
python manual.py -i /path/to/file.mp4 -to

# Skip file operations (no move, no copy, no delete)
python manual.py -i /path/to/file.mkv -a -nm -nc -nd

# Batch with processed archive (skip already-done files)
python manual.py -i /path/to/directory/ -a -pa archive.json
```

After conversion, `manual.py` automatically triggers a rescan on the matching Sonarr/Radarr instance based on the output file's directory path.

### All Options

| Flag | Long | Description |
| --- | --- | --- |
| `-i` | `--input` | Input file or directory |
| `-c` | `--config` | Alternate config file |
| `-a` | `--auto` | Auto mode (no prompts, guesses metadata) |
| `-s` | `--season` | Season number |
| `-e` | `--episode` | Episode number |
| `-tvdb` | `--tvdbid` | TVDB ID |
| `-imdb` | `--imdbid` | IMDB ID |
| `-tmdb` | `--tmdbid` | TMDB ID |
| `-nm` | `--nomove` | Disable move-to and output-directory |
| `-nc` | `--nocopy` | Disable copy-to |
| `-nd` | `--nodelete` | Disable original file deletion |
| `-nt` | `--notag` | Disable metadata tagging |
| `-to` | `--tagonly` | Tag only, no conversion |
| `-np` | `--nopost` | Disable post-process scripts |
| `-pr` | `--preserverelative` | Preserve relative directory structure |
| `-pse` | `--processsameextensions` | Reprocess files already in target format |
| `-fc` | `--forceconvert` | Force conversion + process-same-extensions |
| `-m` | `--moveto` | Override move-to path |
| `-oo` | `--optionsonly` | Show conversion options, don't convert |
| `-cl` | `--codeclist` | List all supported codecs |
| `-o` | `--original` | Specify original filename for guessing |
| `-ms` | `--minsize` | Minimum file size in MB |
| `-pa` | `--processedarchive` | Path to processed files archive JSON |

---

## Daemon Mode

The daemon runs an HTTP server that accepts webhook requests to queue conversions.

### Starting

```bash
# Basic (binds to 127.0.0.1:8585, 1 worker)
python daemon.py

# Production: listen on all interfaces, multiple workers
python daemon.py \
  --host 0.0.0.0 \
  --port 8585 \
  --workers 4 \
  --api-key YOUR_SECRET_KEY \
  --daemon-config config/daemon.json \
  --logs-dir logs/ \
  --db config/daemon.db \
  --ffmpeg-dir /usr/local/bin
```

### Concurrency

`--workers` controls how many conversions run at the same time. The daemon also enforces per-config concurrency: up to `--workers` jobs can run against the same config simultaneously, preventing hardware encoder contention.

- Jobs targeting the **same config** run up to `--workers` at a time; excess jobs queue
- Jobs targeting **different configs** run in parallel (up to `--workers` total)

Example with `--workers 2`:

```text
Job 1: /TV/show.mkv      -> autoProcess.tv.ini     [starts immediately]
Job 2: /Movies/film.mkv  -> autoProcess.movies.ini [starts immediately]
Job 3: /TV/other.mkv     -> autoProcess.tv.ini     [waits for Job 1 or 2 to finish]
```

Check active/waiting jobs:

```bash
curl http://localhost:8585/health
```

### Web Dashboard

Open `http://localhost:8585/` in a browser (redirects to `/dashboard`). Features:

- Real-time job statistics and status
- Active/waiting job panels
- Config mapping overview
- Filterable job history table
- Submit Job form for triggering conversions via the web, with path autocomplete (config prefixes, recent jobs, and live filesystem browsing)

### API Endpoints

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/` | No | Redirects to `/dashboard` |
| `GET` | `/dashboard` | No | Web dashboard |
| `GET` | `/health` | No | Health check with job stats (local node) |
| `GET` | `/status` | No | Cluster-wide status (PostgreSQL) or local health (SQLite) |
| `GET` | `/docs` | No | Rendered documentation |
| `GET` | `/jobs` | Yes | List jobs. Query: `?status=pending&limit=50&offset=0` |
| `GET` | `/jobs/<id>` | Yes | Get specific job |
| `GET` | `/configs` | Yes | Config mappings and status |
| `GET` | `/stats` | Yes | Job statistics by status |
| `GET` | `/scan` | Yes | Filter paths not yet scanned. Query: `?path=/a.mkv&path=/b.mkv` |
| `GET` | `/browse` | Yes | List filesystem dirs/files within configured paths. Query: `?path=/dir` |
| `POST` | `/webhook` | Yes | Submit conversion job (file or directory path) |
| `POST` | `/cleanup` | Yes | Remove old jobs. Query: `?days=30` |
| `POST` | `/shutdown` | Yes | Graceful shutdown (drains in-progress jobs) |
| `POST` | `/jobs/<id>/requeue` | Yes | Requeue a specific failed job |
| `POST` | `/jobs/requeue` | Yes | Requeue all failed jobs. Query: `?config=...` to filter |
| `POST` | `/scan/filter` | Yes | Filter unscanned paths. Body: `{"paths": [...]}` |
| `POST` | `/scan/record` | Yes | Mark paths as scanned. Body: `{"paths": [...]}` |

### Webhook Request Formats

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

# JSON with extra arguments
curl -X POST http://localhost:8585/webhook \
  -H "X-API-Key: SECRET" \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/movie.mkv", "args": ["-tmdb", "603"]}'

# JSON with config override
curl -X POST http://localhost:8585/webhook \
  -H "X-API-Key: SECRET" \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/movie.mkv", "config": "/custom/autoProcess.ini"}'
```

### Authentication

API key can be set via (priority order):

1. `--api-key` CLI argument
2. `SMA_DAEMON_API_KEY` environment variable
3. `api_key` field in `daemon.json`

Send via header: `X-API-Key: SECRET` or `Authorization: Bearer SECRET`

Public endpoints (no auth): `/`, `/dashboard`, `/health`, `/status`, `/docs`, `/favicon.png`

### Path-Based Configuration (daemon.json)

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
    {"path": "/mnt/media/TV", "config": "config/autoProcess.tv.ini"},
    {"path": "/mnt/media/Movies/4K", "config": "config/autoProcess.movies-4k.ini"},
    {"path": "/mnt/media/Movies", "config": "config/autoProcess.movies-1080p.ini"}
  ]
}
```

**Top-level keys:**

| Key | Description |
| --- | --- |
| `default_config` | Config file used when no `path_configs` prefix matches |
| `api_key` | API authentication key (overridable via `--api-key` or `SMA_DAEMON_API_KEY`) |
| `db_url` | PostgreSQL URL for distributed mode (overridable via `--db-url` or `SMA_DAEMON_DB_URL`) |
| `ffmpeg_dir` | Directory containing `ffmpeg`/`ffprobe` binaries. Prepended to PATH for each conversion. Overridable via `--ffmpeg-dir` or `SMA_DAEMON_FFMPEG_DIR` |
| `media_extensions` | File extensions considered media files for directory scanning and `/browse` (default: `.mp4 .mkv .avi .mov .ts`) |
| `scan_paths` | Directories for scheduled background scanning. See [Scheduled Directory Scanning](#scheduled-directory-scanning) |
| `path_configs` | Array of `{"path": "...", "config": "..."}` entries for per-directory config selection |

Matching is longest-prefix-first. `/mnt/media/Movies/4K/film.mkv` matches `Movies/4K`, not `Movies`.

### Per-Config Logging

Each config gets a separate log file in `logs/`. The log filename is derived from the config file stem (filename without extension):

| Config | Log File |
| --- | --- |
| `config/autoProcess.ini` | `logs/autoProcess.log` |
| `config/autoProcess.tv.ini` | `logs/autoProcess.tv.log` |
| `config/autoProcess.movies-4k.ini` | `logs/autoProcess.movies-4k.log` |

Log rotation: 10MB max, 5 backups.

### Scheduled Directory Scanning

The daemon can periodically scan directories for new media files and queue them automatically. Configure `scan_paths` in `daemon.json`:

```json
{
  "scan_paths": [
    {
      "path": "/mnt/local/Media",
      "interval": 3600,
      "rewrite_from": "/mnt/local/Media",
      "rewrite_to": "/mnt/unionfs/Media"
    }
  ]
}
```

| Field | Description |
| --- | --- |
| `path` | Directory to scan for media files |
| `interval` | Scan interval in seconds (e.g., `3600` = every hour) |
| `rewrite_from` | Path prefix to replace before submitting the job (optional) |
| `rewrite_to` | Replacement prefix (optional; use when the scanner sees files at a different mount point than the converter) |

The daemon tracks which files have been submitted in the `scanned_files` database table. Files already in that table are skipped on subsequent scans. Only files whose extension matches `media_extensions` are submitted.

**Manual batch scan (script):** Use `scripts/sma-scan.sh` to walk a directory and submit each media file via webhook, with the same deduplication logic:

```bash
# Submit all unscanned media files in a directory
bash scripts/sma-scan.sh /mnt/media/Movies

# Force resubmit everything (ignore scan history)
bash scripts/sma-scan.sh /mnt/media/Movies --reset

# Dry-run: show what would be submitted
bash scripts/sma-scan.sh /mnt/media/Movies --dry-run

# Use a specific config for all files
bash scripts/sma-scan.sh /mnt/media/Movies --config config/autoProcess.movies.ini
```

**Scan API endpoints:**

```bash
# Check which paths have NOT been scanned yet (small list)
curl "http://localhost:8585/scan?path=/mnt/media/film1.mkv&path=/mnt/media/film2.mkv"
# Returns: {"unscanned": ["/mnt/media/film2.mkv"], "total": 2, "already_scanned": 1}

# Same check for large lists (POST)
curl -X POST http://localhost:8585/scan/filter \
  -H 'Content-Type: application/json' \
  -d '{"paths": ["/mnt/media/film1.mkv", "/mnt/media/film2.mkv"]}'

# Record paths as scanned (mark without submitting a job)
curl -X POST http://localhost:8585/scan/record \
  -H 'Content-Type: application/json' \
  -d '{"paths": ["/mnt/media/film1.mkv"]}'
```

### SQLite Persistence

Jobs are stored in `config/daemon.db` (SQLite). This provides restart recovery, job history, and filtering.

```bash
# View statistics
curl http://localhost:8585/stats

# List pending jobs
curl "http://localhost:8585/jobs?status=pending"

# Cleanup old jobs (default: 30 days)
curl -X POST "http://localhost:8585/cleanup?days=7"
```

Use `--db /path/to/daemon.db` to customize database location.

**Database schema:**

```sql
jobs(id, path, config, args, status, worker_id, node_id, error, created_at, started_at, completed_at)
scanned_files(path, scanned_at)
```

The `scanned_files` table tracks paths submitted by directory scanning to prevent duplicate submissions on re-scan. The `node_id` column records which daemon node claimed a job (relevant in distributed mode).

### PostgreSQL (Distributed / Multi-Node)

For multi-node deployments, configure a shared PostgreSQL database:

```bash
# CLI
python daemon.py --db-url postgresql://user:pass@host/sma

# Environment variable
SMA_DAEMON_DB_URL=postgresql://user:pass@host/sma python daemon.py

# daemon.json
{ "db_url": "postgresql://user:pass@host/sma" }
```

When `db_url` is set, `--db` (SQLite path) is ignored. The `/health` endpoint shows cluster-wide status.

### Graceful Shutdown

Send `POST /shutdown` to drain in-progress conversions before stopping:

```bash
curl -X POST http://localhost:8585/shutdown -H "X-API-Key: SECRET"
```

The daemon stops accepting new jobs immediately, waits for all active conversions to finish, then exits. The systemd unit uses `KillMode=mixed` and `TimeoutStopSec=infinity` so SIGTERM triggers the same graceful drain.

---

## Media Manager Integration

### Sonarr

1. Configure `[Sonarr]` section in `autoProcess.ini` with host, port, API key
2. In Sonarr: Settings → Connect → Add Custom Script
   - On Download/Import: Yes, On Upgrade: Yes
   - Path: `/bin/bash`
   - Arguments: Full path to `triggers/media_managers/sonarr.sh`
3. Multiple instances: Add `[Sonarr-Kids]` etc. sections with unique `path` values

**Per-instance config override:** Set `SMA_CONFIG` in Sonarr's environment (Settings → General → Environment Variables) to force a specific `autoProcess.ini` for that Sonarr instance:

```bash
SMA_CONFIG=/opt/sma/config/autoProcess.tv.ini
```

This is useful when Sonarr imports files to a staging/download path that doesn't match the `path_configs` prefixes in `daemon.json`.

### Radarr

1. Configure `[Radarr]` section in `autoProcess.ini`
2. In Radarr: Settings → Connect → Add Custom Script
   - On Download/Import: Yes, On Upgrade: Yes
   - Path: `/bin/bash`
   - Arguments: Full path to `triggers/media_managers/radarr.sh`
3. Multiple instances: Add `[Radarr-4K]`, `[Radarr-Kids]` etc.

**Per-instance config override:** Set `SMA_CONFIG` in Radarr's environment to force a specific config, same as Sonarr above.

### Multiple Instance Support

Any config section starting with `Sonarr` or `Radarr` is automatically discovered. Each instance requires a `path` field for directory-based matching:

```ini
[Sonarr]
path = /mnt/media/TV
host = sonarr.example.com
apikey = abc123...

[Sonarr-Kids]
path = /mnt/media/TV-Kids
host = sonarr-kids.example.com
apikey = def456...

[Radarr]
path = /mnt/media/Movies
host = radarr.example.com
apikey = ghi789...

[Radarr-4K]
path = /mnt/media/Movies/4K
host = radarr-4k.example.com
apikey = jkl012...
```

When `manual.py` processes `/mnt/media/Movies/4K/film.mp4`, it matches `Radarr-4K` (longest prefix) and triggers a rescan on that instance.

### Plex

Configure `[Plex]` section. SMA-NG refreshes the matching library section after conversion. Use `path-mapping` if Plex sees files at different mount points.

Connect directly to the Plex server using its local hostname or IP on port `32400` (or your custom port), and set the Plex `token` in `[Plex]`.

---

## Download Client Integration

All download client integrations use bash scripts in `triggers/` that submit jobs to the daemon via webhook.

### NZBGet

In Settings → Extension Scripts, add `triggers/usenet/nzbget.sh`. Configure categories under the script settings. The script requires the daemon to be running.

### SABnzbd

In Settings → Folders → Scripts Folder, point to the `triggers/usenet/` directory. Set `sabnzbd.sh` as the category script. Configure `[SABNZBD]` section in `autoProcess.ini`.

### qBittorrent

In Tools → Options → Downloads → Run external program on torrent completion:

```bash
bash /path/to/triggers/torrents/qbittorrent.sh "%L" "%T" "%R" "%F" "%N" "%I"
```

Configure `[qBittorrent]` section with host, credentials, and label mappings.

### Deluge

Enable Execute plugin in Deluge WebUI. Set `triggers/torrents/deluge.sh` as the Torrent Complete handler. Configure `[Deluge]` section with daemon host and credentials.

### uTorrent

In Options → Preferences → Advanced → Run Program, set:

```bash
bash /path/to/triggers/torrents/utorrent.sh %L %T %D %K %F %I %N
```

---

## Hardware Acceleration

The `gpu` key in `[Converter]` sets the hardware acceleration profile used at runtime. You can also set each hwaccel option manually.

### GPU Config Option

The `gpu` key is a runtime setting in `autoProcess.ini` that selects which hardware acceleration backend SMA-NG uses during conversion. `make config` / `mise run config` auto-detects the GPU and writes the correct value, but you can also set or change it manually at any time. The runtime settings driven by `gpu` are `hwaccels`, `hwaccel-decoders`, `hwdevices`, `hwaccel-output-format`, and `[Video] codec`.

Valid values for `gpu`:

| Value | Platform | Notes |
| --- | --- | --- |
| `qsv` | Intel Quick Sync Video | Requires `/dev/dri/renderD128` and i915 kernel module |
| `vaapi` | Intel/AMD VAAPI | Requires `/dev/dri/renderD128` and `vainfo` |
| `nvenc` | NVIDIA NVENC | Requires NVIDIA driver and `nvidia-smi` |
| `videotoolbox` | Apple Silicon / macOS | Built into macOS; no device path needed |
| `software` | CPU only | No hardware acceleration |

Auto-detection runs `mise run detect-gpu` (or `make detect-gpu`) which checks for each platform in order: NVIDIA → Intel QSV → VAAPI → VideoToolbox → software.

### Intel QSV

```ini
[Converter]
hwaccels = qsv
hwaccel-decoders = hevc_qsv, h264_qsv, vp9_qsv, av1_qsv
hwdevices = qsv:/dev/dri/renderD128
hwaccel-output-format = qsv:qsv

[Video]
codec = h265qsv, h265
```

Supported QSV codecs: `h264qsv`, `h265qsv`, `av1qsv`, `vp9qsv`

### Intel VAAPI

```ini
[Converter]
hwaccels = vaapi
hwaccel-decoders = hevc_vaapi, h264_vaapi
hwdevices = vaapi:/dev/dri/renderD128
hwaccel-output-format = vaapi:vaapi

[Video]
codec = h265vaapi, h265
```

Supported VAAPI codecs: `h264vaapi`, `h265vaapi`, `av1vaapi`

### NVIDIA NVENC

```ini
[Converter]
hwaccels = cuda
hwaccel-decoders = hevc_cuvid, h264_cuvid

[Video]
codec = h265_nvenc, h265
```

### Apple VideoToolbox

```ini
[Video]
codec = h264_videotoolbox, h264
```

### Key Configuration Rules

- `hwdevices` format is `type:device_path` where `type` must be a substring of the encoder codec name
- `hwaccel-output-format` format is `hwaccel_name:output_format` (dict format, not bare value)
- The codec list's first entry is used for encoding; subsequent entries allow stream copying
- CRF is mapped to `-global_quality` for QSV and `-qp` for VAAPI automatically

---

## Processing Pipeline

### Video Decision Tree

1. Source codec in allowed list → **copy** (unless overridden by bitrate/width/level/profile/filter)
2. Bitrate exceeds `max-bitrate` (after `bitrate-ratio` scaling) → **re-encode**
3. Width exceeds `max-width` → **re-encode + downscale**
4. Level exceeds `max-level` → **re-encode**
5. Profile not in whitelist → **re-encode**
6. Burn subtitles enabled → **re-encode**
7. Otherwise → **copy**

### Bitrate Calculation

1. `estimateVideoBitrate()` computes source video bitrate from container total minus audio
2. `bitrate-ratio` scales the estimate per source codec (e.g., H.264 at 0.65x for HEVC target)
3. `crf-profiles` selects CRF/maxrate/bufsize tier based on scaled bitrate
4. `max-bitrate` caps the final result

### Audio Decision Tree

1. Filter by `languages` whitelist + `include-original-language`
2. Filter by `ignored-dispositions`
3. Source codec in allowed list → **copy**; otherwise → **re-encode** to first codec
4. Apply channel limits (`max-channels`), bitrate limits (`max-bitrate`)
5. Sort by `[Audio.Sorting]` rules
6. Select default stream
7. Optionally generate Universal Audio stream (stereo compatibility)

---

## Module Reference

### converter/avcodecs.py

Codec definitions mapping SMA-NG names to FFmpeg encoders. Each codec class handles its own option parsing and FFmpeg flag generation.

**Video codecs**: H264, H265, AV1, VP9, MPEG-1/2, H263, FLV, Theora + hardware variants (QSV, VAAPI, NVENC, VideoToolbox, V4L2M2M, OMX)

**Audio codecs**: AAC, AC3, EAC3, DTS, FLAC, MP3, Vorbis, Opus, PCM variants, TrueHD, ALAC

**Subtitle codecs**: mov_text, SRT, SSA/ASS, WebVTT, PGS, DVDSub, DVBSub, copy

### converter/ffmpeg.py

FFmpeg/FFprobe wrapper. Key classes:

- **MediaInfo** / **MediaStreamInfo** / **MediaFormatInfo**: Parsed probe results
- **FFMpeg**: Binary wrapper with `probe()`, `convert()`, `thumbnail()`, codec/hwaccel queries

### converter/formats.py

Container format definitions (MP4, MKV, AVI, WebM, etc.) mapping to FFmpeg muxer names.

### converter/\_\_init\_\_.py

**Converter** class: Orchestrates codec/format selection, builds FFmpeg commands, manages conversion with progress tracking.

### resources/mediaprocessor.py

**MediaProcessor**: Core pipeline orchestrator. Key methods:

- `isValidSource()`: FFprobe validation
- `generateOptions()`: Stream analysis → FFmpeg option dict (~800 lines)
- `convert()`: Execute FFmpeg with collision handling
- `fullprocess()`: Complete pipeline (validate → convert → tag → relocate → replicate → notify)
- `setAcceleration()`: Hardware accel configuration with bit-depth checks
- `estimateVideoBitrate()`: Source bitrate estimation
- `replicate()`: copy-to / move-to file operations

### resources/readsettings.py

**ReadSettings**: Parses `autoProcess.ini` into typed attributes. Handles defaults, type coercion, multi-instance Sonarr/Radarr discovery.

### resources/metadata.py

**Metadata**: TMDB API client + MP4 tagger (via mutagen). Resolves TMDB/TVDB/IMDB IDs, writes iTunes-compatible tags.

### resources/postprocess.py

**PostProcessor**: Discovers and runs scripts in `post_process/` directory with SMA-NG environment variables.

### resources/log.py

Logging setup with INI-based configuration. All output goes to stdout/stderr — no log files are written by the core library. The daemon writes per-config log files in `logs/` using rotating file handlers.

### resources/lang.py

Language code conversion (ISO 639 alpha2/alpha3) via babelfish.

### resources/custom.py

Optional hook points loaded from `config/custom.py`: `validation()`, `blockVideoCopy()`, `blockAudioCopy()`, `skipStream()`, `streamTitle()`.

### autoprocess/plex.py

Plex library refresh via PlexAPI with path mapping support.

### resources/mediamanager.py

Shared Sonarr/Radarr API helpers used by trigger scripts to post `DownloadedEpisodesScan` / `DownloadedMoviesScan` commands.

---

## Supported Codecs

Run `python manual.py -cl` for the full list. Key codecs:

### Video

| SMA-NG Name | FFmpeg Encoder | Notes |
| --- | --- | --- |
| `h264` | libx264 | Software H.264 |
| `h265` / `hevc` | libx265 | Software HEVC |
| `h264qsv` | h264_qsv | Intel QSV H.264 |
| `h265qsv` | hevc_qsv | Intel QSV HEVC |
| `h264vaapi` | h264_vaapi | Intel VAAPI H.264 |
| `h265vaapi` | hevc_vaapi | Intel VAAPI HEVC |
| `av1qsv` | av1_qsv | Intel QSV AV1 |
| `av1vaapi` | av1_vaapi | Intel VAAPI AV1 |
| `h265_nvenc` | hevc_nvenc | NVIDIA HEVC |
| `av1` | libaom-av1 | Software AV1 |
| `svtav1` | libsvtav1 | SVT-AV1 |
| `vp9` | libvpx-vp9 | Software VP9 |

### Audio

| SMA-NG Name | FFmpeg Encoder |
| --- | --- |
| `aac` | aac / libfdk_aac |
| `ac3` | ac3 |
| `eac3` | eac3 |
| `flac` | flac |
| `opus` | libopus |
| `mp3` | libmp3lame |
| `dts` | dca |
| `truehd` | truehd |

---

## Post-Process Scripts

Place executable scripts in the `post_process/` directory. They receive environment variables:

| Variable | Description |
| --- | --- |
| `SMA_FILES` | JSON array of output file paths |
| `SMA_TMDBID` | TMDB ID |
| `SMA_SEASON` | Season number (TV only) |
| `SMA_EPISODE` | Episode number (TV only) |

See `setup/post_process/` for examples (Plex, Emby, Jellyfin, iTunes).

---

## Deployment (mise)

SMA-NG uses [mise](https://mise.jdx.dev/) as a task runner for both local development and remote deployments. All tasks are defined in `mise.toml` (local dev) and `.mise/tasks/deploy/` (deployment).

### Local Development Tasks

```bash
mise run install          # Create venv and install dependencies
mise run install-dev      # Install dev + test dependencies
mise run test             # Run test suite
mise run lint             # Run ruff linter
mise run lint-fix         # Auto-fix lint issues
mise run detect-gpu       # Detect available GPU acceleration
mise run config           # Generate config with auto-detected GPU
mise run daemon           # Start daemon on 0.0.0.0:8585
mise run convert -- /path/to/file.mkv  # Convert a file
mise run preview -- /path/to/file.mkv  # Preview options only
mise run codecs           # List supported codecs
```

#### Docker tasks

```bash
mise run docker:build     # Build image locally (TAG=sma-ng:local to override)
mise run docker:run       # Run locally-built image
mise run docker:shell     # Open shell in locally-built image
mise run docker:smoke     # Smoke-test imports and ffmpeg
```

### Deployment System

The deploy tasks push code to remote hosts via SSH/rsync and manage the systemd service.

#### Initial setup

1. Copy `setup/.local.ini.sample` to `setup/.local.ini` and configure it (this file is gitignored):

```ini
[deploy]
DEPLOY_HOSTS = user@server1.example.com user@server2.example.com
DEPLOY_DIR   = ~/sma
SSH_KEY      = ~/.ssh/id_ed25519_sma
FFMPEG_DIR   = /usr/local/bin

[daemon]
api_key = your_secret_key
db_url  =                          # leave blank for SQLite

[Sonarr]
host        = sonarr.example.com
port        = 443
ssl         = true
apikey      = abc123...
media_path  = /mnt/media/TV
config_file = config/autoProcess.sonarr.ini

[Radarr]
host        = radarr.example.com
apikey      = def456...
media_path  = /mnt/media/Movies
config_file = config/autoProcess.radarr.ini
```

1. Run first-time setup (generates SSH key, installs apt dependencies, creates deploy directory, installs systemd service):

```bash
mise run deploy:setup
```

#### Deploying code

```bash
# Sync code, install dependencies, reload systemd unit
mise run deploy:run

# Then restart the service
mise run deploy:restart
```

`deploy:run` does the following on each host in `DEPLOY_HOSTS`:

- rsync the repo (excluding `venv/`, `config/`, `logs/`, `__pycache__/`)
- `make install` on the remote host (creates venv, installs Python deps)
- Updates `User=` and `Group=` in the systemd unit file to match the SSH user
- Runs `systemctl daemon-reload`

If `make install` fails due to missing prerequisites (Python, make, rsync), it automatically runs `deploy:setup` for that host and retries.

#### Rolling config to remote hosts

```bash
mise run deploy:config
```

This task manages config files on remote hosts without overwriting customizations:

- **Creates missing configs** from samples (auto-detects GPU for `autoProcess.ini`)
- **Merges new keys** from `autoProcess.ini.sample` into existing `*.ini` configs — new settings added to the sample are propagated without touching existing values
- **Stamps service credentials** from `[Sonarr]`, `[Radarr]`, `[Plex]` etc. sections in `.local.ini` into all `*.ini` configs
- **Updates `daemon.json`** with `api_key`, `db_url`, and `path_configs` entries built from `media_path` + `config_file` fields in each service section
- **Updates `daemon.env`** with `SMA_DAEMON_*` environment variables
- **Stamps ffmpeg/ffprobe paths** from `FFMPEG_DIR` into all `*.ini` configs
- **Deploys post-process scripts** from `setup/post_process/` to `post_process/`, stamping in credentials (Plex token, Jellyfin URL, etc.) and updating shebangs to the venv Python

#### Per-host overrides

Any key from `[deploy]` can be overridden for a specific host:

```ini
[user@server1.example.com]
DEPLOY_DIR  = /opt/sma
SSH_PORT    = 2222
FFMPEG_DIR  = /opt/ffmpeg/bin
```

#### Restarting without deploying

```bash
mise run deploy:restart
```

Runs `sudo systemctl restart sma-daemon` on each host.

#### Running arbitrary make targets remotely

```bash
REMOTE_MAKE=test mise run deploy:remote-make
```

Runs the specified make target on each host without syncing code first.

#### Summary of deploy tasks

| Task | Description |
| --- | --- |
| `deploy:check` | Verify `setup/.local.ini` exists and `DEPLOY_HOSTS` is set |
| `deploy:setup` | First-time host prep: SSH key, apt deps, deploy dir, systemd install |
| `deploy:run` | Sync code + install deps + reload systemd on all hosts |
| `deploy:config` | Roll configs: create missing, merge new keys, stamp credentials |
| `deploy:restart` | Restart `sma-daemon` on all hosts |
| `deploy:remote-make` | Run an arbitrary make target on all hosts |

---

## Troubleshooting

### Logs

All SMA-NG output goes to stdout/stderr. When running as a systemd service, use `journalctl`:

```bash
journalctl -u sma-daemon -f
```

The daemon also writes per-config rotating log files in `logs/`:

| Config | Log File |
| --- | --- |
| `config/autoProcess.ini` | `logs/autoProcess.log` |
| `config/autoProcess.ini-tv` | `logs/autoProcess.ini-tv.log` |

### Common Issues

#### "Invalid source, no video stream detected"

- File may be corrupt or not a media file
- Check FFprobe path in config

#### Hardware acceleration not working

- Verify `hwdevices` key matches encoder codec name (e.g., `qsv` for `h265qsv`)
- Verify `hwaccel-output-format` uses dict format: `qsv:qsv` not just `qsv`
- Check FFmpeg build supports the hwaccel: `ffmpeg -hwaccels`
- Check device exists: `ls /dev/dri/renderD128`
- On Linux, ensure the service user is in the `render` or `video` group

#### Conversion produces larger file

- Lower `crf` value or add `max-bitrate` cap
- Use `bitrate-ratio` to scale based on source codec
- Use `crf-profiles` for tiered quality

#### Subtitles show as "English (MOV_TEXT)" in Plex

- This is Plex reading the raw codec name. SMA-NG sets a title on subtitle streams to improve display.

#### Sonarr/Radarr not rescanning after `manual.py`

- Verify `path` field is set in the `[Sonarr]`/`[Radarr]` config section
- Verify `apikey` is set and `rescan = true`
- Check the file path starts with the configured `path` prefix

#### `systemd`: "Read-only file system" errors

- Check `ReadWritePaths` in the systemd unit includes all paths FFmpeg writes to (temp dir, output dir, media mounts)
- Default unit includes `/opt/sma/config /opt/sma/logs /transcodes /mnt` — add any additional paths

### Environment Variables

| Variable | Description |
| --- | --- |
| `SMA_CONFIG` | Override path to `autoProcess.ini` |
| `SMA_DAEMON_API_KEY` | Daemon API key |
| `SMA_DAEMON_DB_URL` | PostgreSQL connection URL for distributed mode |
| `SMA_DAEMON_FFMPEG_DIR` | Directory containing `ffmpeg`/`ffprobe` (prepended to PATH for conversions) |
| `SMA_DAEMON_HOST` | Daemon bind host (Docker default: empty = 0.0.0.0) |
| `SMA_DAEMON_PORT` | Daemon port (Docker default: 8585) |
| `SMA_DAEMON_WORKERS` | Number of concurrent workers (Docker default: 4) |
| `SMA_DAEMON_CONFIG` | Path to daemon.json config file |
| `SMA_DAEMON_DB` | Path to SQLite database file |
| `SMA_DAEMON_LOGS_DIR` | Directory for per-config log files |

---

## License

See [license.md](../license.md).
