# SMA-NG — Next-Generation Media Automator

Automated media conversion, tagging, and integration pipeline. Converts media files to MP4/MKV using FFmpeg with hardware acceleration, tags them with TMDB metadata, and integrates with media managers and download clients.

## Table of Contents

- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Configuration Reference](#configuration-reference)
- [CLI Usage (manual.py)](#cli-usage)
- [Daemon Mode](#daemon-mode)
- [Media Manager Integration](#media-manager-integration)
- [Download Client Integration](#download-client-integration)
- [Hardware Acceleration](#hardware-acceleration)
- [Processing Pipeline](#processing-pipeline)
- [Module Reference](#module-reference)
- [Post-Process Scripts](#post-process-scripts)
- [Troubleshooting](#troubleshooting)

---

## Requirements

- **Python 3.12+**
- **FFmpeg** (system install or custom path)
- Python packages: `pip install -r setup/requirements.txt`

Optional:
- qBittorrent integration: `pip install -r setup/requirements-qbittorrent.txt`
- Deluge integration: `pip install -r setup/requirements-deluge.txt`

## Quick Start

```bash
# Clone and set up
git clone <repo> && cd sma
python3 -m venv venv && source venv/bin/activate
pip install -r setup/requirements.txt

# Create config from sample
cp setup/autoProcess.ini.sample config/autoProcess.ini

# Edit config (set FFmpeg paths, codec preferences, API keys)
$EDITOR config/autoProcess.ini

# Test a conversion
python manual.py -i /path/to/file.mkv -a

# Preview conversion without running it
python manual.py -i /path/to/file.mkv -oo

# Start the daemon for webhook-driven conversions
python daemon.py --host 0.0.0.0 --port 8585
```

---

## Architecture

```
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
│  readsettings.py    │  __init__.py      │  sonarr.py            │
│  metadata.py        │  ffmpeg.py        │  radarr.py            │
│  postprocess.py     │  avcodecs.py      │  plex.py              │
│  extensions.py      │  formats.py       │                       │
│  log.py / lang.py   │                   │                       │
└─────────────────────┴───────────────────┴───────────────────────┘
```

### Data Flow

```
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
|--------|------|---------|-------------|
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
|--------|------|---------|-------------|
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
|--------|------|-------------|
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
|--------|------|---------|-------------|
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
|--------|------|-------------|
| `sorting` | list | Sort order for audio streams: `language, channels.d, map, d.comment` |
| `default-sorting` | list | Sort order for default stream selection |
| `codecs` | list | Codec priority for sorting |

### [Universal Audio]

Generates an additional audio stream (usually stereo AAC) for maximum device compatibility.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `codec` | list | | UA codec (e.g., `aac`). Empty = disabled |
| `channel-bitrate` | int | `128` | Bitrate per channel |
| `first-stream-only` | bool | `true` | Only add UA for first audio stream |

### [Subtitle]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
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
|--------|------|-------------|
| `enabled` | bool | Enable subtitle cleaning via cleanit library |
| `config-path` | path | Custom cleanit config |
| `tags` | list | Cleanit tag sets: `default, no-style` |

### [Subtitle.FFSubsync]

| Option | Type | Description |
|--------|------|-------------|
| `enabled` | bool | Enable subtitle sync via ffsubsync |

### [Subtitle.Subliminal]

| Option | Type | Description |
|--------|------|-------------|
| `download-subs` | bool | Download missing subtitles |
| `providers` | list | Subtitle providers: `opensubtitles` |
| `download-forced-subs` | bool | Download forced subtitle variants |
| `download-hearing-impaired-subs` | bool | Include HI subs in downloads |

### [Metadata]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `relocate-moov` | bool | `true` | Move moov atom to file start (streaming optimization) |
| `full-path-guess` | bool | `true` | Use full file path for guessit metadata matching |
| `tag` | bool | `true` | Enable TMDB metadata tagging |
| `tag-language` | string | `eng` | Language for TMDB metadata |
| `download-artwork` | bool | `false` | Embed cover art from TMDB |
| `strip-metadata` | bool | `true` | Remove existing metadata before tagging |
| `keep-titles` | bool | `false` | Preserve original stream titles |

### [Permissions]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `chmod` | octal | `0664` | File permissions for output |
| `uid` | int | `-1` | Owner UID (-1 = no change) |
| `gid` | int | `-1` | Group GID (-1 = no change) |

### [Sonarr] / [Sonarr-Kids] / [Radarr] / [Radarr-4K] / [Radarr-Kids]

Multiple instances supported. Any section starting with `Sonarr` or `Radarr` is loaded.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
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
|--------|------|-------------|
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
|------|------|-------------|
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
# Basic
python daemon.py

# Full options
python daemon.py \
  --host 0.0.0.0 \
  --port 8585 \
  --workers 4 \
  --api-key YOUR_SECRET_KEY \
  --daemon-config config/daemon.json \
  --logs-dir logs/ \
  --db config/daemon.db
```

### Web Dashboard

Open `http://localhost:8585/dashboard` in a browser (or just `/` — it redirects). Features:
- Real-time job statistics and status
- Active/waiting job panels
- Config mapping overview
- Filterable job history table
- Submit Job form for triggering conversions via the web

### API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/` | No | Redirects to `/dashboard` |
| `GET` | `/dashboard` | No | Web dashboard |
| `GET` | `/health` | No | Health check with job stats |
| `GET` | `/docs` | No | Rendered documentation |
| `GET` | `/jobs` | Yes | List jobs. Query: `?status=pending&limit=50&offset=0` |
| `GET` | `/jobs/<id>` | Yes | Get specific job |
| `GET` | `/configs` | Yes | Config mappings and status |
| `GET` | `/stats` | Yes | Job statistics by status |
| `POST` | `/webhook` | Yes | Submit conversion job |
| `POST` | `/cleanup` | Yes | Remove old jobs. Query: `?days=30` |

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

Public endpoints (no auth): `/`, `/dashboard`, `/health`, `/status`, `/docs`

### Path-Based Configuration (daemon.json)

```json
{
  "default_config": "config/autoProcess.ini",
  "api_key": "your_secret_key",
  "path_configs": [
    {"path": "/mnt/media/TV", "config": "config/autoProcess.ini-tv"},
    {"path": "/mnt/media/Movies/4K", "config": "config/autoProcess.ini-movies4k"},
    {"path": "/mnt/media/Movies", "config": "config/autoProcess.ini-movies"}
  ]
}
```

Matching is longest-prefix-first. `/mnt/media/Movies/4K/film.mkv` matches `Movies/4K`, not `Movies`.

### Concurrency

- One conversion per config at a time (prevents resource conflicts)
- Different configs run in parallel up to `--workers` count
- Jobs for the same config queue and execute sequentially
- SQLite database (`config/daemon.db`) persists jobs across restarts

### Per-Config Logging

Each config gets a separate log file in `logs/`:

| Config | Log File |
|--------|----------|
| `config/autoProcess.ini` | `logs/autoProcess.log` |
| `config/autoProcess.ini-tv` | `logs/autoProcess.ini-tv.log` |

Log rotation: 10MB max, 5 backups.

---

## Media Manager Integration

### Sonarr

1. Configure `[Sonarr]` section in `autoProcess.ini` with host, port, API key
2. In Sonarr: Settings → Connect → Add Custom Script
   - On Download/Import: Yes, On Upgrade: Yes
   - Path: `/bin/bash`
   - Arguments: Full path to `triggers/media_managers/sonarr.sh`
3. Multiple instances: Add `[Sonarr-Kids]` etc. sections with unique `path` values

### Radarr

1. Configure `[Radarr]` section in `autoProcess.ini`
2. In Radarr: Settings → Connect → Add Custom Script
   - On Download/Import: Yes, On Upgrade: Yes
   - Path: `/bin/bash`
   - Arguments: Full path to `triggers/media_managers/radarr.sh`
3. Multiple instances: Add `[Radarr-4K]`, `[Radarr-Kids]` etc.

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

Logging setup with rotating file handlers and config-driven format.

### resources/lang.py

Language code conversion (ISO 639 alpha2/alpha3) via babelfish.

### resources/custom.py

Optional hook points loaded from `config/custom.py`: `validation()`, `blockVideoCopy()`, `blockAudioCopy()`, `skipStream()`, `streamTitle()`.

### autoprocess/sonarr.py & radarr.py

API clients for triggering `DownloadedEpisodesScan` / `DownloadedMoviesScan` commands.

### autoprocess/plex.py

Plex library refresh via PlexAPI with path mapping support.

---

## Supported Codecs

Run `python manual.py -cl` for the full list. Key codecs:

### Video
| SMA-NG Name | FFmpeg Encoder | Notes |
|----------|---------------|-------|
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
|----------|---------------|
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
|----------|-------------|
| `SMA_FILES` | JSON array of output file paths |
| `SMA_TMDBID` | TMDB ID |
| `SMA_SEASON` | Season number (TV only) |
| `SMA_EPISODE` | Episode number (TV only) |

See `setup/post_process/` for examples (Plex, Emby, Jellyfin, iTunes).

---

## Troubleshooting

### Logs

- Main log: `config/sma.log` (rotating, 100KB × 3)
- Daemon per-config logs: `logs/<config-name>.log` (rotating, 10MB × 5)

### Common Issues

**"Invalid source, no video stream detected"**
- File may be corrupt or not a media file
- Check FFprobe path in config

**Hardware acceleration not working**
- Verify `hwdevices` key matches encoder codec name (e.g., `qsv` for `h265qsv`)
- Verify `hwaccel-output-format` uses dict format: `qsv:qsv` not just `qsv`
- Check FFmpeg build supports the hwaccel: `ffmpeg -hwaccels`
- Check device exists: `ls /dev/dri/renderD128`

**Conversion produces larger file**
- Lower `crf` value or add `max-bitrate` cap
- Use `bitrate-ratio` to scale based on source codec
- Use `crf-profiles` for tiered quality

**Subtitles show as "English (MOV_TEXT)" in Plex**
- This is Plex reading the raw codec name. SMA-NG sets a title on subtitle streams to improve display.

**Sonarr/Radarr not rescanning after manual.py**
- Verify `path` field is set in the `[Sonarr]`/`[Radarr]` config section
- Verify `apikey` is set and `rescan = true`
- Check the file path starts with the configured `path` prefix

### Environment Variables

| Variable | Description |
|----------|-------------|
| `SMA_CONFIG` | Override path to `autoProcess.ini` |
| `SMA_DAEMON_API_KEY` | Daemon API key |
| `SMA_DAEMON_DB_URL` | PostgreSQL connection URL for distributed mode |
| `SMA_DAEMON_FFMPEG_DIR` | Directory containing `ffmpeg`/`ffprobe` (prepended to PATH for conversions) |

---

## License

See [license.md](../license.md).
