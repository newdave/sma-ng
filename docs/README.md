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
  - [Path-Based Configuration](#path-based-configuration)
  - [Per-Config Logging](#per-config-logging)
  - [Scheduled Directory Scanning](#scheduled-directory-scanning)
  - [Job Persistence (PostgreSQL)](#job-persistence-postgresql)
  - [Graceful Shutdown](#graceful-shutdown)
- [Media Manager Integration](#media-manager-integration)
- [Download Client Integration](#download-client-integration)
- [Docker Compose Quick Start](docker-compose-quickstart.md)
- [Environment Architecture](environment-architecture.md)
- [Hardware Acceleration](#hardware-acceleration)
- [Multi-Instance Deployment](multi-instance-deployment.md)
- [Processing Pipeline](#processing-pipeline)
- [Module Reference](#module-reference)
- [Post-Process Scripts](#post-process-scripts)
- [Deployment (mise)](#deployment-mise)
- [Troubleshooting](#troubleshooting)
- [Migrating from sickbeard_mp4_automator](migration.md)

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
mise run setup:deps

# Generate config (auto-detects GPU)
mise run config:generate

# Test a conversion
mise run media:convert -- /path/to/file.mkv

# Start the daemon
mise run daemon:start
```

### Without mise

```bash
git clone <repo> && cd sma
python3 -m venv venv && source venv/bin/activate
pip install -r setup/requirements.txt

# Generate config with auto GPU detection
make config

# Or copy sample and edit manually
cp setup/sma-ng.yml.sample config/sma-ng.yml
$EDITOR config/sma-ng.yml

# Test a conversion
python manual.py -i /path/to/file.mkv -a

# Start the daemon
python daemon.py --host 0.0.0.0 --port 8585
```

---

## Architecture

```mermaid
graph TD
    subgraph Entry["Entry Points"]
        CLI["manual.py\nCLI tool"]
        D["daemon.py\nHTTP server"]
        T["triggers/*.sh\nbash scripts"]
    end

    subgraph DaemonPkg["resources/daemon/"]
        S["server.py\nDaemonServer"]
        H["handler.py\nWebhookHandler"]
        W["worker.py\nWorkerPool"]
        DB["db.py\nPostgreSQL"]
        TH["threads.py\nHeartbeat · Scanner"]
    end

    subgraph Resources["resources/"]
        RS["readsettings.py"]
        MD["metadata.py"]
        PP["postprocess.py"]
        MM["mediamanager.py"]
        EX["extensions.py"]
    end

    subgraph Conv["converter/"]
        FF["ffmpeg.py"]
        AC["avcodecs.py"]
        FMT["formats.py"]
    end

    MP["mediaprocessor.py\nMediaProcessor"]
    PX["autoprocess/plex.py"]

    CLI --> MP
    T --> MP
    D --> S --> H --> W --> MP
    W --> DB
    S --> TH
    MP --> RS
    MP --> FF
    MP --> MD
    MP --> PP
    MP --> MM
    MP --> PX
    FF --> AC
    FF --> FMT
```

### Data Flow

```mermaid
flowchart TD
    A([Input File]) --> B{FFprobe\nvalidation}
    B -- invalid --> Z([Skip / Error])
    B -- valid --> C[Stream analysis\ngenerateOptions]
    C --> D{Hardware accel\navailable?}
    D -- yes --> E[FFmpeg encode\nHW accelerated]
    D -- no --> F[FFmpeg encode\nsoftware]
    E & F --> G{TMDB metadata\nconfigured?}
    G -- yes --> H[writeTags\nmutagen MP4]
    G -- skip --> I[qtfaststart\nmoov atom relocation]
    H --> I
    I --> J["replicate\noutput_dir → copy-to / move-to"]
    J --> K[Post-process scripts]
    K --> L[Plex / Sonarr / Radarr notify]
    L --> M([Done])
```

---

## Configuration Reference

Configuration lives in `config/sma-ng.yml` (YAML format). Copy from `setup/sma-ng.yml.sample`.

Override path via `SMA_CONFIG` environment variable.

### [Converter]

| Option                    | Type    | Default         | Description                                                             |
| ------------------------- | ------- | --------------- | ----------------------------------------------------------------------- |
| `ffmpeg`                  | path    | `ffmpeg`        | Path to FFmpeg binary                                                   |
| `ffprobe`                 | path    | `ffprobe`       | Path to FFprobe binary                                                  |
| `threads`                 | int     | `0`             | FFmpeg threads (0 = auto)                                               |
| `hwaccels`                | list    |                 | Hardware acceleration platforms: `qsv`, `vaapi`, `cuda`, `videotoolbox` |
| `hwaccel-decoders`        | list    |                 | HW decoders to use: `hevc_qsv`, `h264_qsv`, `h264_vaapi`, etc.          |
| `hwdevices`               | dict    |                 | Device mapping, e.g. `qsv:/dev/dri/renderD128`                          |
| `hwaccel-output-format`   | dict    |                 | Output format per hwaccel: `qsv:qsv`, `vaapi:vaapi`                     |
| `output-directory`        | path    |                 | Temporary output location (files moved back after)                      |
| `output-format`           | string  | `mp4`           | Container format: `mp4`, `mkv`, `mov`                                   |
| `output-extension`        | string  | `mp4`           | Output file extension                                                   |
| `temp-extension`          | string  |                 | Temporary file extension during conversion                              |
| `temp-output`             | bool    | `true`          | Use temporary output file during conversion                             |
| `minimum-size`            | int     | `0`             | Minimum source file size in MB (0 = disabled)                           |
| `ignored-extensions`      | list    | `nfo, ds_store` | Extensions to skip                                                      |
| `copy-to`                 | path(s) |                 | Copy output to additional directories (pipe-separated)                  |
| `move-to`                 | path    |                 | Move output to final destination                                        |
| `delete-original`         | bool    | `true`          | Delete source file after successful conversion                          |
| `recycle-bin`             | path    |                 | Copy original here before deleting (only when `delete-original = True`) |
| `process-same-extensions` | bool    | `false`         | Reprocess files already in output format                                |
| `bypass-if-copying-all`   | bool    | `false`         | Skip conversion if all streams can be copied                            |
| `force-convert`           | bool    | `false`         | Force conversion even if codec matches                                  |
| `post-process`            | bool    | `false`         | Run post-process scripts                                                |
| `wait-post-process`       | bool    | `false`         | Wait for post-process scripts to finish                                 |
| `preopts`                 | list    |                 | Extra FFmpeg options before input                                       |
| `postopts`                | list    |                 | Extra FFmpeg options after codec options                                |
| `opts-separator`          | string  | `,`             | Separator for preopts/postopts lists                                    |

### [Video]

| Option                      | Type   | Default | Description                                                                                                                   |
| --------------------------- | ------ | ------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `codec`                     | list   | `h264`  | Video codecs in priority order. First is used for encoding, rest are copy-eligible. See [Supported Codecs](#supported-codecs) |
| `max-bitrate`               | int    | `0`     | Maximum video bitrate in kbps (0 = unlimited). Source exceeding this is re-encoded                                            |
| `bitrate-ratio`             | dict   |         | Scale source bitrate per codec: `hevc:1.0, h264:0.65, mpeg2video:0.45`                                                        |
| `crf`                       | int    | `24`    | Constant Rate Factor (quality). Lower = better quality, larger files                                                          |
| `crf-profiles`              | list   |         | Tiered CRF by source bitrate: `20000:20:5000k:10000k, 10000:22:5000k:10000k` (format: `source_kbps:crf:maxrate:bufsize`)      |
| `preset`                    | string |         | Encoder preset: `ultrafast` to `veryslow` (speed vs compression)                                                              |
| `profile`                   | list   |         | Video profile: `main`, `high`, `main10`                                                                                       |
| `max-level`                 | float  |         | Maximum H.264/H.265 level (e.g., `5.2`)                                                                                       |
| `max-width`                 | int    | `0`     | Maximum output width (0 = no limit). Triggers downscale                                                                       |
| `pix-fmt`                   | list   |         | Pixel format whitelist. Non-matching sources are re-encoded                                                                   |
| `dynamic-parameters`        | bool   | `false` | Pass HDR/color metadata to encoder                                                                                            |
| `prioritize-source-pix-fmt` | bool   | `true`  | Keep source pix_fmt if in whitelist                                                                                           |
| `filter`                    | string |         | Custom FFmpeg video filter                                                                                                    |
| `force-filter`              | bool   | `false` | Force re-encode when filter is set                                                                                            |
| `codec-parameters`          | string |         | Extra codec params (e.g., x265-params)                                                                                        |

### [HDR]

Override video settings for HDR content (detected automatically).

| Option             | Type   | Description                           |
| ------------------ | ------ | ------------------------------------- |
| `codec`            | list   | Video codec for HDR content           |
| `pix-fmt`          | list   | Pixel format for HDR (e.g., `p010le`) |
| `space`            | list   | Color space: `bt2020nc`               |
| `transfer`         | list   | Transfer function: `smpte2084`        |
| `primaries`        | list   | Color primaries: `bt2020`             |
| `preset`           | string | Encoder preset override for HDR       |
| `profile`          | string | Profile override for HDR              |
| `codec-parameters` | string | Extra params for HDR encoding         |
| `filter`           | string | Video filter for HDR content          |
| `force-filter`     | bool   | Force re-encode for HDR filter        |

### [Audio]

| Option                      | Type   | Default | Description                                                                                 |
| --------------------------- | ------ | ------- | ------------------------------------------------------------------------------------------- |
| `codec`                     | list   | `aac`   | Audio codecs in priority order. Streams matching any are copied; others re-encoded to first |
| `languages`                 | list   |         | Language whitelist (ISO 639-3, e.g., `eng`). Empty = all languages                          |
| `default-language`          | string | `eng`   | Default language for unlabeled streams                                                      |
| `first-stream-of-language`  | bool   | `false` | Keep only first stream per language                                                         |
| `allow-language-relax`      | bool   | `true`  | If no whitelisted language found, keep all audio                                            |
| `include-original-language` | bool   | `false` | Include original media language even if not in whitelist                                    |
| `channel-bitrate`           | int    | `128`   | Bitrate per channel in kbps (0 = auto)                                                      |
| `variable-bitrate`          | int    | `0`     | VBR quality level (0 = disabled/CBR)                                                        |
| `max-bitrate`               | int    | `0`     | Maximum audio bitrate in kbps                                                               |
| `max-channels`              | int    | `0`     | Maximum audio channels (0 = unlimited, 6 = 5.1)                                             |
| `copy-original`             | bool   | `false` | Copy original audio stream in addition to transcoded                                        |
| `aac-adtstoasc`             | bool   | `true`  | Apply AAC ADTS to ASC bitstream filter                                                      |
| `ignored-dispositions`      | list   |         | Skip streams with these dispositions: `comment`, `hearing_impaired`                         |
| `unique-dispositions`       | bool   | `false` | One stream per disposition per language                                                     |
| `stream-codec-combinations` | list   |         | Identify duplicate streams by codec combo                                                   |
| `ignore-trudhd`             | bool   | `true`  | Ignore TrueHD streams                                                                       |
| `atmos-force-copy`          | bool   | `false` | Always copy Atmos tracks                                                                    |
| `force-default`             | bool   | `false` | Override source default stream                                                              |
| `relax-to-default`          | bool   | `false` | If preferred language absent, default to any                                                |

### [Audio.Sorting]

| Option            | Type | Description                                                          |
| ----------------- | ---- | -------------------------------------------------------------------- |
| `sorting`         | list | Sort order for audio streams: `language, channels.d, map, d.comment` |
| `default-sorting` | list | Sort order for default stream selection                              |
| `codecs`          | list | Codec priority for sorting                                           |

### [Universal Audio]

Generates an additional audio stream (usually stereo AAC) for maximum device compatibility.

| Option              | Type | Default | Description                              |
| ------------------- | ---- | ------- | ---------------------------------------- |
| `codec`             | list |         | UA codec (e.g., `aac`). Empty = disabled |
| `channel-bitrate`   | int  | `128`   | Bitrate per channel                      |
| `first-stream-only` | bool | `true`  | Only add UA for first audio stream       |

### [Subtitle]

| Option                      | Type   | Default    | Description                                     |
| --------------------------- | ------ | ---------- | ----------------------------------------------- |
| `codec`                     | list   | `mov_text` | Subtitle codec for text-based subs              |
| `codec-image-based`         | list   |            | Codec for image-based subs (PGS, VobSub)        |
| `languages`                 | list   |            | Language whitelist (ISO 639-3)                  |
| `default-language`          | string | `eng`      | Default for unlabeled subs                      |
| `first-stream-of-language`  | bool   | `false`    | One subtitle per language                       |
| `burn-subtitles`            | bool   | `false`    | Burn subtitles into video                       |
| `burn-dispositions`         | list   | `forced`   | Only burn subs with these dispositions          |
| `embed-subs`                | bool   | `true`     | Embed subtitle streams in output                |
| `embed-image-subs`          | bool   | `false`    | Embed image-based subs                          |
| `embed-only-internal-subs`  | bool   | `false`    | Only embed subs from source (no external files) |
| `ignored-dispositions`      | list   |            | Skip subs with these dispositions               |
| `remove-bitstream-subs`     | list   | `true`     | Remove bitstream subtitle formats               |
| `include-original-language` | bool   | `false`    | Include original language subs                  |

### [Subtitle.CleanIt]

| Option        | Type | Description                                  |
| ------------- | ---- | -------------------------------------------- |
| `enabled`     | bool | Enable subtitle cleaning via cleanit library |
| `config-path` | path | Custom cleanit config                        |
| `tags`        | list | Cleanit tag sets: `default, no-style`        |

### [Subtitle.FFSubsync]

| Option    | Type | Description                        |
| --------- | ---- | ---------------------------------- |
| `enabled` | bool | Enable subtitle sync via ffsubsync |

### [Subtitle.Subliminal]

| Option                           | Type | Description                         |
| -------------------------------- | ---- | ----------------------------------- |
| `download-subs`                  | bool | Download missing subtitles          |
| `providers`                      | list | Subtitle providers: `opensubtitles` |
| `download-forced-subs`           | bool | Download forced subtitle variants   |
| `download-hearing-impaired-subs` | bool | Include HI subs in downloads        |

### [Metadata]

| Option             | Type   | Default | Description                                           |
| ------------------ | ------ | ------- | ----------------------------------------------------- |
| `relocate-moov`    | bool   | `true`  | Move moov atom to file start (streaming optimization) |
| `full-path-guess`  | bool   | `true`  | Use full file path for guessit metadata matching      |
| `tag`              | bool   | `true`  | Enable TMDB metadata tagging                          |
| `tag-language`     | string | `eng`   | Language for TMDB metadata                            |
| `download-artwork` | bool   | `false` | Embed cover art from TMDB                             |
| `strip-metadata`   | bool   | `true`  | Remove existing metadata before tagging               |
| `keep-titles`      | bool   | `false` | Preserve original stream titles                       |

### [Permissions]

| Option  | Type  | Default | Description                 |
| ------- | ----- | ------- | --------------------------- |
| `chmod` | octal | `0664`  | File permissions for output |
| `uid`   | int   | `-1`    | Owner UID (-1 = no change)  |
| `gid`   | int   | `-1`    | Group GID (-1 = no change)  |

### [Sonarr] / [Sonarr-Kids] / [Radarr] / [Radarr-4K] / [Radarr-Kids]

Multiple instances supported. Any section starting with `Sonarr` or `Radarr` is loaded.

| Option              | Type   | Default       | Description                                               |
| ------------------- | ------ | ------------- | --------------------------------------------------------- |
| `host`              | string | `localhost`   | API hostname                                              |
| `port`              | int    | `8989`/`7878` | API port                                                  |
| `apikey`            | string |               | API key                                                   |
| `ssl`               | bool   | `false`       | Use HTTPS                                                 |
| `webroot`           | string |               | URL base path                                             |
| `path`              | string |               | Media root path for directory matching (manual.py rescan) |
| `force-rename`      | bool   | `false`       | Trigger rename after processing                           |
| `rescan`            | bool   | `true`        | Trigger library rescan after processing                   |
| `block-reprocess`   | bool   | `false`       | Prevent reprocessing same-extension files                 |
| `in-progress-check` | bool   | `true`        | Wait for in-progress scans before rescanning              |

Instances are matched by `path` using longest-prefix matching. When `manual.py` processes a file, it finds the matching instance and triggers a rescan via the API.

### [Plex]

| Option         | Type   | Description                                                             |
| -------------- | ------ | ----------------------------------------------------------------------- |
| `host`         | string | Plex server hostname                                                    |
| `port`         | int    | Plex server port (default 32400)                                        |
| `refresh`      | bool   | Trigger library refresh after processing                                |
| `token`        | string | Plex authentication token                                               |
| `ssl`          | bool   | Use HTTPS                                                               |
| `ignore-certs` | bool   | Skip SSL certificate verification                                       |
| `path-mapping` | dict   | Map SMA-NG paths to Plex library paths (comma-separated, `=` delimited) |

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

# Use a named profile from the config file
python manual.py -i /path/to/file.mkv -a -c config/sma-ng.yml --profile rq

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

| Flag    | Long                      | Description                                |
| ------- | ------------------------- | ------------------------------------------ |
| `-i`    | `--input`                 | Input file or directory                    |
| `-c`    | `--config`                | Alternate config file                      |
| `-a`    | `--auto`                  | Auto mode (no prompts, guesses metadata)   |
| `-s`    | `--season`                | Season number                              |
| `-e`    | `--episode`               | Episode number                             |
| `-tvdb` | `--tvdbid`                | TVDB ID                                    |
| `-imdb` | `--imdbid`                | IMDB ID                                    |
| `-tmdb` | `--tmdbid`                | TMDB ID                                    |
| `-nm`   | `--nomove`                | Disable move-to and output-directory       |
| `-nc`   | `--nocopy`                | Disable copy-to                            |
| `-nd`   | `--nodelete`              | Disable original file deletion             |
| `-nt`   | `--notag`                 | Disable metadata tagging                   |
| `-to`   | `--tagonly`               | Tag only, no conversion                    |
| `-np`   | `--nopost`                | Disable post-process scripts               |
| `-pr`   | `--preserverelative`      | Preserve relative directory structure      |
| `-pse`  | `--processsameextensions` | Reprocess files already in target format   |
| `-fc`   | `--forceconvert`          | Force conversion + process-same-extensions |
| `-m`    | `--moveto`                | Override move-to path                      |
| `-oo`   | `--optionsonly`           | Show conversion options, don't convert     |
| `-cl`   | `--codeclist`             | List all supported codecs                  |
| `-o`    | `--original`              | Specify original filename for guessing     |
| `-ms`   | `--minsize`               | Minimum file size in MB                    |
| `-pa`   | `--processedarchive`      | Path to processed files archive JSON       |

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
  --daemon-config config/sma-ng.yml \
  --logs-dir logs/ \
  --ffmpeg-dir /usr/local/bin
```

### Concurrency

`--workers` controls how many conversions run at the same time. The daemon also enforces per-config concurrency: up to `--workers` jobs can run against the same config simultaneously, preventing hardware encoder contention.

- Jobs run up to `--workers` at a time; excess jobs queue

Example with `--workers 2`:

```text
Job 1: /TV/show.mkv      -> sma-ng.yml profile rq [starts immediately]
Job 2: /Movies/film.mkv  -> sma-ng.yml profile lq [starts immediately]
Job 3: /TV/other.mkv     -> sma-ng.yml profile rq [waits for an available worker]
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

| Method | Path                 | Auth | Description                                                             |
| ------ | -------------------- | ---- | ----------------------------------------------------------------------- |
| `GET`  | `/`                  | No   | Redirects to `/dashboard`                                               |
| `GET`  | `/dashboard`         | No   | Web dashboard                                                           |
| `GET`  | `/health`            | No   | Health check with job stats (local node)                                |
| `GET`  | `/status`            | No   | Cluster-wide status across all nodes                                    |
| `GET`  | `/docs`              | No   | Rendered documentation                                                  |
| `GET`  | `/jobs`              | Yes  | List jobs. Query: `?status=pending&limit=50&offset=0`                   |
| `GET`  | `/jobs/<id>`         | Yes  | Get specific job                                                        |
| `GET`  | `/configs`           | Yes  | Config mappings and status                                              |
| `GET`  | `/stats`             | Yes  | Job statistics by status                                                |
| `GET`  | `/scan`              | Yes  | Filter paths not yet scanned. Query: `?path=/a.mkv&path=/b.mkv`         |
| `GET`  | `/browse`            | Yes  | List filesystem dirs/files within configured paths. Query: `?path=/dir` |
| `POST` | `/webhook`           | Yes  | Submit conversion job (file or directory path)                          |
| `POST` | `/cleanup`           | Yes  | Remove old jobs. Query: `?days=30`                                      |
| `POST` | `/shutdown`          | Yes  | Graceful shutdown (drains in-progress jobs)                             |
| `POST` | `/jobs/<id>/requeue` | Yes  | Requeue a specific failed job                                           |
| `POST` | `/jobs/requeue`      | Yes  | Requeue all failed jobs. Query: `?config=...` to filter                 |
| `POST` | `/scan/filter`       | Yes  | Filter unscanned paths. Body: `{"paths": [...]}`                        |
| `POST` | `/scan/record`       | Yes  | Mark paths as scanned. Body: `{"paths": [...]}`                         |

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
  -d '{"path": "/path/to/movie.mkv", "config": "/custom/sma-ng.yml"}'
```

### Authentication

API key can be set via (priority order):

1. `--api-key` CLI argument
2. `SMA_DAEMON_API_KEY` environment variable
3. `Daemon.api_key` in `sma-ng.yml`

Send via header: `X-API-Key: SECRET` or `Authorization: Bearer SECRET`

Public endpoints (no auth): `/`, `/dashboard`, `/health`, `/status`, `/docs`, `/favicon.png`

### Path-Based Configuration

```yaml
Daemon:
  default_config: config/sma-ng.yml
  api_key: your_secret_key
  db_url:
  ffmpeg_dir:
  media_extensions: [.mkv, .m4v, .avi, .mov, .ts]
  scan_paths:
    - path: /mnt/local/Media
      interval: 3600
      rewrite_from: /mnt/local/Media
      rewrite_to: /mnt/unionfs/Media
  path_configs:
    - path: /mnt/media/TV
      profile: rq
    - path: /mnt/media/Movies/4K
      profile: rq
    - path: /mnt/media/Movies
      profile: lq
```

**Top-level keys:**

| Key                | Description                                                                                                                                        |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `default_config`   | Config file used when no `path_configs` prefix matches                                                                                             |
| `api_key`          | API authentication key (overridable via `--api-key` or `SMA_DAEMON_API_KEY`)                                                                       |
| `db_url`           | PostgreSQL URL for distributed mode (overridable via `--db-url` or `SMA_DAEMON_DB_URL`)                                                            |
| `ffmpeg_dir`       | Directory containing `ffmpeg`/`ffprobe` binaries. Prepended to PATH for each conversion. Overridable via `--ffmpeg-dir` or `SMA_DAEMON_FFMPEG_DIR` |
| `media_extensions` | File extensions considered media files for directory scanning and `/browse` (default: `.mkv .m4v .avi .mov .wmv .ts .flv .webm`)                   |
| `path_rewrites`    | Prefix substitutions applied before config matching; overlapping rewrites are matched longest-prefix-first                                         |
| `scan_paths`       | Directories for scheduled background scanning. See [Scheduled Directory Scanning](#scheduled-directory-scanning)                                   |
| `path_configs`     | Array of `{"path": "...", "config": "..."}` entries for per-directory config selection                                                             |

Matching is longest-prefix-first. `/mnt/media/Movies/4K/film.mkv` matches `Movies/4K`, not `Movies`. If `path_rewrites` overlap, the most specific rewrite is applied before config matching.

### Per-Config Logging

Each config gets a separate log file in `logs/`. The log filename is derived from the config file stem (filename without extension):

| Config                             | Log File                         |
| ---------------------------------- | -------------------------------- |
| `config/sma-ng.yml` | `logs/sma-ng.log` |

Log rotation: 10MB max, 5 backups.

### Scheduled Directory Scanning

The daemon can periodically scan directories for new media files and queue them automatically. Configure `scan_paths` in `Daemon:` section in `sma-ng.yml`:

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

| Field          | Description                                                                                                  |
| -------------- | ------------------------------------------------------------------------------------------------------------ |
| `path`         | Directory to scan for media files                                                                            |
| `interval`     | Scan interval in seconds (e.g., `3600` = every hour)                                                         |
| `rewrite_from` | Path prefix to replace before submitting the job (optional)                                                  |
| `rewrite_to`   | Replacement prefix (optional; use when the scanner sees files at a different mount point than the converter) |

The daemon tracks which files have been submitted in the `scanned_files` database table. Files already in that table are skipped on subsequent scans. Any file whose extension matches `media_extensions` is eligible for submission. `.mp4` is excluded from the default list to avoid re-processing already-converted output; add it explicitly if you want scanning to pick it up.

**Manual batch scan (script):** Use `scripts/sma-scan.sh` to walk a directory and submit each media file via webhook, with the same deduplication logic:

```bash
# Submit all unscanned media files in a directory
bash scripts/sma-scan.sh /mnt/media/Movies

# Force resubmit everything (ignore scan history)
bash scripts/sma-scan.sh /mnt/media/Movies --reset

# Dry-run: show what would be submitted
bash scripts/sma-scan.sh /mnt/media/Movies --dry-run

# Use a specific config for all files
bash scripts/sma-scan.sh /mnt/media/Movies --config config/sma-ng.yml
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

### Job Persistence (PostgreSQL)

The daemon uses PostgreSQL exclusively. Jobs, scanned-files state, cluster nodes, and per-config logs all live in the database, so any number of daemons can share the same backend and split work across the cluster. Single-node deployments still need PostgreSQL — there is no embedded SQLite fallback.

Configure the connection via either:

```yaml
# config/sma-ng.yml
daemon:
  db_url: postgresql://sma:secret@db.example.com:5432/sma
```

Or via the environment (preferred when running under Docker, since secrets stay out of `ps` output):

```bash
SMA_DAEMON_DB_URL=postgresql://sma:secret@db.example.com:5432/sma python daemon.py
# or split into parts and let the daemon assemble the URL
SMA_DAEMON_DB_HOST=db.example.com
SMA_DAEMON_DB_USER=sma
SMA_DAEMON_DB_PASSWORD=secret
SMA_DAEMON_DB_NAME=sma
```

There is no `--db-url` CLI flag — credentials must not appear in `ps`. The daemon's startup order is `SMA_DAEMON_DB_URL` env → assembled `SMA_DAEMON_DB_*` parts → `daemon.db_url` in `sma-ng.yml`. If none is set, startup fails fast with an error.

Common queries (all hit the same Postgres instance):

```bash
# View statistics
curl http://localhost:8585/stats

# List pending jobs
curl "http://localhost:8585/jobs?status=pending"

# Cleanup old jobs (default: 30 days)
curl -X POST "http://localhost:8585/cleanup?days=7"
```

**Schema (managed by the daemon on startup):**

```sql
jobs(id, path, config, args, status, worker_id, node_id, error, created_at, started_at, completed_at, ...)
scanned_files(path, scanned_at)
cluster_nodes(node_id, host, workers, last_seen, started_at, status, approval_status, ...)
node_commands(id, node_id, command, status, ...)
logs(id, config, node_id, level, message, timestamp)
```

The `scanned_files` table prevents duplicate submissions across scanner restarts. `cluster_nodes` is the source of truth for the dashboard's node list and the heartbeat/approval flow. `node_commands` is the durable channel daemons use to broadcast control operations (reload, restart, drain) across the cluster.

### Graceful Shutdown

Send `POST /shutdown` to drain in-progress conversions before stopping:

```bash
curl -X POST http://localhost:8585/shutdown -H "X-API-Key: SECRET"
```

The daemon stops accepting new jobs immediately, waits for all active conversions to finish, then exits. The Docker entrypoint forwards SIGTERM to the daemon, so `docker compose stop` triggers the same graceful drain.

---

## Media Manager Integration

### Sonarr

1. Configure `[Sonarr]` section in `sma-ng.yml` with host, port, API key
2. In Sonarr: Settings → Connect → Add Custom Script
   - On Download/Import: Yes, On Upgrade: Yes
   - Path: `/bin/bash`
   - Arguments: Full path to `triggers/media_managers/sonarr.sh`
3. Multiple instances: Add `[Sonarr-Kids]` etc. sections with unique `path` values

**Per-instance profile routing:** Set `Daemon.path_configs` entries in `sma-ng.yml` to choose `rq` or `lq` based on the imported path:

```yaml
Daemon:
  path_configs:
    - path: /mnt/media/TV
      profile: rq
```

This is useful when Sonarr imports files to a staging/download path that doesn't match the `path_configs` prefixes in `Daemon:` section in `sma-ng.yml`.

### Radarr

1. Configure `[Radarr]` section in `sma-ng.yml`
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

In Settings → Folders → Scripts Folder, point to the `triggers/usenet/` directory. Set `sabnzbd.sh` as the category script. Configure `[SABNZBD]` section in `sma-ng.yml`.

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

The `gpu` key is a runtime setting in `sma-ng.yml` that selects which hardware acceleration backend SMA-NG uses during conversion. `make config` and `mise run config:generate` now call the same generator, auto-detect the GPU the same way, and write the correct value into the generated `autoProcess*.ini` files, but you can also set or change it manually at any time. The runtime settings driven by `gpu` are `hwaccels`, `hwaccel-decoders`, `hwdevices`, `hwaccel-output-format`, and `[Video] codec`.

Valid values for `gpu`:

| Value          | Platform               | Notes                                                                                           |
| -------------- | ---------------------- | ----------------------------------------------------------------------------------------------- |
| `qsv`          | Intel Quick Sync Video | Requires an accessible DRI render node such as `/dev/dri/renderD128` and the i915 kernel module |
| `vaapi`        | Intel/AMD VAAPI        | Requires an accessible DRI render node such as `/dev/dri/renderD128` and `vainfo`               |
| `nvenc`        | NVIDIA NVENC           | Requires NVIDIA driver and `nvidia-smi`                                                         |
| `videotoolbox` | Apple Silicon / macOS  | Built into macOS; no device path needed                                                         |
| `software`     | CPU only               | No hardware acceleration                                                                        |

Auto-detection runs the same shared script behind `mise run config:gpu` and `make detect-gpu`, checking each platform in order: NVIDIA → Intel QSV → VAAPI → VideoToolbox → software.

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

**ReadSettings**: Parses `sma-ng.yml` into typed attributes. Handles defaults, type coercion, multi-instance Sonarr/Radarr discovery.

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

Shared Sonarr/Radarr API helpers. After in-place conversion the daemon issues a two-step chain keyed on the series/movie ID resolved via `/api/v3/parse`:

1. `RescanSeries` (Sonarr) / `RescanMovie` (Radarr) — re-evaluates known episodefile/moviefile records and unlinks the now-missing original path (extension and/or basename changed during conversion).
2. `DownloadedEpisodesScan` (Sonarr) / `DownloadedMoviesScan` (Radarr) with the converted *file* path and `importMode: Move` — imports the new file and links it to the existing series/movie. Without this second step Sonarr/Radarr leave the file orphaned and the episode/movie shows as missing/deleted. Passing a single file path (rather than a directory) lets these commands operate on library paths in Sonarr v3+/Radarr v4+.

When the instance has `force-rename: true`, a `RenameFiles` command is chained after the import completes.

---

## Supported Codecs

Run `python manual.py -cl` for the full list. Key codecs:

### Video

| SMA-NG Name     | FFmpeg Encoder | Notes             |
| --------------- | -------------- | ----------------- |
| `h264`          | libx264        | Software H.264    |
| `h265` / `hevc` | libx265        | Software HEVC     |
| `h264qsv`       | h264_qsv       | Intel QSV H.264   |
| `h265qsv`       | hevc_qsv       | Intel QSV HEVC    |
| `h264vaapi`     | h264_vaapi     | Intel VAAPI H.264 |
| `h265vaapi`     | hevc_vaapi     | Intel VAAPI HEVC  |
| `av1qsv`        | av1_qsv        | Intel QSV AV1     |
| `av1vaapi`      | av1_vaapi      | Intel VAAPI AV1   |
| `h265_nvenc`    | hevc_nvenc     | NVIDIA HEVC       |
| `av1`           | libaom-av1     | Software AV1      |
| `svtav1`        | libsvtav1      | SVT-AV1           |
| `vp9`           | libvpx-vp9     | Software VP9      |

### Audio

| SMA-NG Name | FFmpeg Encoder   |
| ----------- | ---------------- |
| `aac`       | aac / libfdk_aac |
| `ac3`       | ac3              |
| `eac3`      | eac3             |
| `flac`      | flac             |
| `opus`      | libopus          |
| `mp3`       | libmp3lame       |
| `dts`       | dca              |
| `truehd`    | truehd           |

---

## Post-Process Scripts

Place executable scripts in the `post_process/` directory. They receive environment variables:

| Variable      | Description                     |
| ------------- | ------------------------------- |
| `SMA_FILES`   | JSON array of output file paths |
| `SMA_TMDBID`  | TMDB ID                         |
| `SMA_SEASON`  | Season number (TV only)         |
| `SMA_EPISODE` | Episode number (TV only)        |

See `setup/post_process/` for examples (Plex, Emby, Jellyfin, iTunes).

---

## Deployment (mise)

SMA-NG uses [mise](https://mise.jdx.dev/) as a task runner for both local development and remote deployments.
Runnable tasks are executable scripts under `.mise/tasks/`; `mise.toml` only defines tools and shared environment.

### Local Development Tasks

```bash
mise run setup:deps          # Create venv and install dependencies
mise run setup:deps:dev      # Install dev + test dependencies
mise run test             # Run test suite
mise run test:daemon      # Run focused daemon/API worker tests
mise run test:deploy      # Run focused deploy/config task tests
mise run dev:check            # Run local CI-equivalent checks
mise run test:lint             # Run ruff linter
mise run dev:lint         # Auto-fix lint issues
mise run dev:format           # Format Python code
mise run test:openapi          # Validate docs/openapi.yaml
mise run config:gpu       # Detect available GPU acceleration
mise run config:generate           # Generate config with auto-detected GPU
mise run config:audit     # Audit local config files
mise run daemon:smoke     # Validate daemon config and exit
mise run daemon:start           # Start daemon on 0.0.0.0:8585
mise run media:convert -- /path/to/file.mkv  # Convert a file
mise run media:preview -- /path/to/file.mkv  # Preview options only
mise run media:codecs           # List supported codecs
```

#### Docker tasks

```bash
mise run build:docker     # Build image locally (TAG=sma-ng:local to override)
SMA_DAEMON_DB_URL=postgresql://user:pass@host/db mise run docker:run
mise run build:shell      # Open shell in locally-built image
mise run test:smoke       # Smoke-test imports and ffmpeg
```

### Deployment System

The deploy tasks push code to remote hosts via SSH/rsync and manage the Docker stack.

#### Initial setup

1. Copy `setup/local.yml.sample` to `setup/local.yml` and configure it (this file is gitignored).
   See [`docs/deployment.md`](deployment.md#configuration) for the canonical reference of every
   top-level section. Minimal multi-host example:

```yaml
deploy:
  hosts: [sma-master, sma-worker-1]
  deploy_dir: /opt/sma
  ssh_key: ~/.ssh/id_ed25519_sma
  ffmpeg_dir: /usr/local/bin
  docker_profile: intel

hosts:
  sma-master:
    address: 192.168.1.10
    user: deploy
    docker_profile: intel-pg     # bundled postgres on this host
  sma-worker-1:
    address: 192.168.1.11
    user: deploy

daemon:
  api_key: your_secret_key

base:                            # encoder defaults locked across every host
  video:
    gpu: qsv
    codec: [hevc]
    preset: fast

profiles:                        # overlays selected per routing rule
  rq:
    video:
      crf-profiles: '0:22:1M:2M,2000:22:2M:4M,4000:22:3M:8M,8000:22:6M:8M'
  lq:
    video:
      crf-profiles: '0:22:3M:6M,8000:22:5M:10M'

services:                        # nested <type>.<instance>; auto-builds daemon.routing
  sonarr:
    main:
      url: https://sonarr.example.com
      apikey: abc123...
      path: /mnt/media/TV
      profile: rq
  radarr:
    main:
      url: https://radarr.example.com
      apikey: def456...
      path: /mnt/media/Movies
      profile: rq
```

1. Run first-time setup (generates SSH key, installs apt dependencies, installs Docker, creates deploy directory):

```bash
mise run deploy:setup
```

#### Deploying code

```bash
# Sync code and install dependencies
mise run deploy:sync

# Then restart the service
mise run deploy:restart

# Optional: stop Docker services on one host
HOST=sma-master mise run deploy:dockerstop

# Optional: stop Docker services on multiple hosts
HOSTS="sma-master sma-worker-1" mise run deploy:dockerstop
```

`deploy:sync` does the following on each host in `deploy.hosts`:

- rsyncs the repo (excluding `venv/`, `config/`, `logs/`, `__pycache__/`)
- creates or repairs the virtualenv and installs base Python dependencies

If dependency installation fails due to missing prerequisites (Python, `rsync`, or `venv` support), it automatically
runs `deploy:setup` for that host and retries.

#### Rolling config to remote hosts

```bash
mise run config:roll
```

This task manages `config/sma-ng.yml` on remote hosts without overwriting your customisations.
For each host it:

- **Creates missing configs** from `setup/sma-ng.yml.sample` (auto-detects GPU on first creation)
- **Backs up** `config/` to `.backup/<timestamp>/` before any mutation
- **Merges new keys** from the sample via `yaml_merge.py` (non-destructive)
- **Deep-merges `base:` and `profiles:`** from `setup/local.yml` into the same blocks of
  `sma-ng.yml`. Dicts recurse, lists/scalars overwrite, anything you omit inherits from
  the sample.
- **Stamps `services.<type>.<instance>`** from `setup/local.yml` into `services:` and
  rebuilds `daemon.routing` from every instance carrying both `path` and `profile`
  (longest match first)
- **Stamps `daemon.api-key` / `daemon.db-url` / `daemon.ffmpeg-dir`** (kebab-case) into
  `daemon:` and writes the corresponding `SMA_DAEMON_*` env vars plus `SMA_NODE_NAME`
  into `config/daemon.env`
- **Stamps `base.converter.{ffmpeg,ffprobe}`** from the host's resolved `ffmpeg_dir`
- **Deploys post-process scripts** from `setup/post_process/` to `post_process/`,
  stamping in Plex/Jellyfin/Emby credentials and updating shebangs to the venv Python

#### Per-host overrides

Any key from `deploy:` can be overridden for a specific host under `hosts:`:

```yaml
hosts:
  sma-master:
    address: 192.168.1.10
    user: deploy
    deploy_dir: /opt/sma
    ssh_port: 2222
    ffmpeg_dir: /opt/ffmpeg/bin
```

#### Restarting without deploying

```bash
mise run deploy:restart
```

Sends a graceful shutdown webhook to each host, waits for the daemon to drain, then runs `docker compose --profile <docker_profile> restart sma`.

#### Summary of deploy tasks

| Task             | Description                                                                |
| ---------------- | -------------------------------------------------------------------------- |
| `deploy:check`   | Verify `setup/local.yml` exists and `deploy.hosts` is set                  |
| `deploy:setup`   | First-time host prep: SSH key, apt deps, deploy dir, Docker install       |
| `deploy:mise`    | Sync the local `.mise/` deploy control plane to each remote `deploy_dir`   |
| `deploy:sync`    | Sync code and install deps on all hosts                     |
| `config:roll`    | Roll configs: create missing, merge new keys, stamp credentials & overlays |
| `deploy:restart` | Gracefully shut down `sma-daemon` on all hosts, then restart its Docker container |
| `deploy:docker`  | Rsync the local code to Docker hosts, pull the latest image, recreate SMA  |
| `pg:restart`     | Restart bundled PostgreSQL on hosts whose `docker_profile` ends in `-pg`   |
| `pg:recreate`    | Stop bundled PostgreSQL, remove its Docker volume, and recreate            |

Additional Docker lifecycle helper: `deploy:dockerstop` (alias: `deploy:docker:stop`) stops
services for selected hosts using each host's configured `DOCKER_PROFILE`.

---

## Troubleshooting

### Logs

All SMA-NG output goes to stdout/stderr. For Docker deployments, follow the container logs:

```bash
journalctl -u sma-daemon -f
```

The daemon also writes per-config rotating log files in `logs/`:

| Config                       | Log File                       |
| ---------------------------- | ------------------------------ |
| `config/sma-ng.yml`    | `logs/autoProcess.log`         |
| `config/sma-ng.yml-tv` | `logs/sma-ng.yml-tv.log` |

### Common Issues

#### "Invalid source, no video stream detected"

- File may be corrupt or not a media file
- Check FFprobe path in config

#### Hardware acceleration not working

- Verify `hwdevices` key matches encoder codec name (e.g., `qsv` for `h265qsv`)
- Verify `hwaccel-output-format` uses dict format: `qsv:qsv` not just `qsv`
- Check FFmpeg build supports the hwaccel: `ffmpeg -hwaccels`
- Check the selected render device exists: `ls /dev/dri/renderD128`
- On Intel SR-IOV guests, verify the Intel VF appears as a matching `card*` and `renderD*` pair under `/dev/dri`
- If using Docker Compose, ensure the container joins both the host `render` and `video` groups
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

#### Docker: "Read-only file system" errors

- Verify every path FFmpeg writes to (output dir, temp dir, media mounts) is bind-mounted
  read-write into the `sma` service in `docker/docker-compose.yml`
- The bundled compose file mounts `/opt/sma/config`, `/opt/sma/logs`, and `/transcodes` by
  default — extend `volumes:` for any additional paths your setup needs

### Environment Variables

| Variable                | Description                                                                 |
| ----------------------- | --------------------------------------------------------------------------- |
| `SMA_CONFIG`            | Override path to `sma-ng.yml`                                         |
| `SMA_DAEMON_API_KEY`    | Daemon API key                                                              |
| `SMA_DAEMON_DB_URL`     | PostgreSQL connection URL for distributed mode                              |
| `SMA_DAEMON_FFMPEG_DIR` | Directory containing `ffmpeg`/`ffprobe` (prepended to PATH for conversions) |
| `SMA_DAEMON_HOST`       | Daemon bind host (Docker default: empty = 0.0.0.0)                          |
| `SMA_DAEMON_PORT`       | Daemon port (Docker default: 8585)                                          |
| `SMA_DAEMON_WORKERS`    | Number of concurrent workers (Docker default: 4)                            |
| `SMA_DAEMON_CONFIG`     | Path to daemon config, normally `config/sma-ng.yml`                         |
| `SMA_DAEMON_API_KEY`    | API key (overrides `daemon.api_key` in `sma-ng.yml`)                        |
| `SMA_DAEMON_DB_HOST`    | PostgreSQL host (used when `SMA_DAEMON_DB_URL` is not set)                  |
| `SMA_DAEMON_DB_USER`    | PostgreSQL user (used with `SMA_DAEMON_DB_HOST`)                            |
| `SMA_DAEMON_DB_PASSWORD`| PostgreSQL password (used with `SMA_DAEMON_DB_HOST`)                        |
| `SMA_DAEMON_DB_NAME`    | PostgreSQL database name (default: `sma`)                                   |
| `SMA_DAEMON_DB_PORT`    | PostgreSQL port (default: `5432`)                                           |
| `SMA_NODE_NAME`         | Cluster node identity (overrides `socket.gethostname()`)                    |
| `SMA_DAEMON_LOGS_DIR`   | Directory for per-config log files                                          |

---

## License

See [license.md](../license.md).
