# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Documentation Rules

- **Keep documentation in sync with code changes.** When you add, change, or remove a feature, update all relevant docs in the same commit (or immediately after).
- **Every documentation change must be applied in three places:**
  1. `docs/` — the canonical source in the main repo
  2. GitHub wiki (`/tmp/sma-wiki/`) — the corresponding wiki page(s); push with `git add -A && git commit -m "docs: ..." && git push origin HEAD:master`
  3. Web UI (`resources/docs.html`) — the inline help served at `http://localhost:8585/docs`
- Docs to update depending on change type:
  - New/changed daemon endpoints → `docs/daemon.md` + `docs/openapi.yaml` + `resources/docs.html` + wiki `Daemon-Mode.md` + wiki `API-Reference.md`
  - New/changed settings → `docs/configuration.md` + `setup/autoProcess.ini.sample` + `resources/docs.html` + wiki `Configuration.md`
  - New/changed CLI flags or daemon options → `docs/getting-started.md` + `docs/daemon.md` + `AGENTS.md` + `resources/docs.html` + wiki `Getting-Started.md` + wiki `Daemon-Mode.md`
  - New/changed `mise` tasks → `mise.toml` description field + wiki `Mise-Tasks.md`
  - New integrations → `docs/integrations.md` + `resources/docs.html` + wiki `Integrations.md`
  - New hardware-acceleration options → `docs/hardware-acceleration.md` + `resources/docs.html` + wiki `Hardware-Acceleration.md`
  - Deployment/CI changes → `docs/deployment.md` + `resources/docs.html` + wiki `Deployment.md`
  - Architecture changes → `docs/README.md` + `CLAUDE.md` + wiki `Home.md`
- Never leave stale or incorrect documentation behind — remove or correct it in the same PR.

## Git Commit Rules

- **Do NOT add `Co-Authored-By` lines referencing Claude, Anthropic, or any AI to commit messages.**
- Do not add any AI attribution to commits whatsoever.
- Break large changesets into smaller, contextual commits — each commit should represent one logical change.
- Write informative commit messages that describe what changed and why (use conventional commit prefixes: `fix:`, `feat:`, `refactor:`, etc.).
- Commit regularly rather than accumulating large diffs.
- After each commit, run `git pull --rebase` then `git push`.

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

## Architecture

### Entry Points

- `manual.py` - CLI tool for manual conversion/tagging
- `daemon.py` - Thin entry point (~170 lines): imports `resources.daemon.*`, runs `main()`
- `triggers/media_managers/sonarr.sh` / `radarr.sh` - Bash scripts triggered by Sonarr/Radarr
- `triggers/usenet/` / `triggers/torrents/` - Bash scripts for download client integrations

### Daemon Package (`resources/daemon/`)

The daemon is a package under `resources/daemon/`. `daemon.py` at project root is a thin entry point that re-exports all names for backward compatibility with tests.

| Module | Contents |
| --- | --- |
| `constants.py` | `SCRIPT_DIR`, `DEFAULT_*`, `STATUS_*` constants |
| `db.py` | `PostgreSQLJobDatabase` |
| `config.py` | `ConfigLockManager`, `ConfigLogManager`, `PathConfigManager` |
| `handler.py` | `WebhookHandler` + HTML helpers, multi-page docs routing |
| `threads.py` | `_StoppableThread`, `HeartbeatThread`, `ScannerThread` |
| `worker.py` | `ConversionWorker`, `WorkerPool` |
| `server.py` | `DaemonServer`, `_validate_hwaccel` |

### Core Modules

#### `resources/`

- `mediaprocessor.py` - Central class `MediaProcessor` handling the full conversion pipeline
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

### Configuration

The main config file is `config/autoProcess.ini` (copy from `setup/autoProcess.ini.sample`). Override location via `SMA_CONFIG` environment variable.

Key sections: `[Converter]`, `[Video]`, `[HDR]`, `[Audio]`, `[Subtitle]`, `[Metadata]`, `[Sonarr]`, `[Radarr]`, `[Plex]`

See [docs/configuration.md](docs/configuration.md) for full reference.

### Daemon Configuration

`config/daemon.json` (copy from `setup/daemon.json.sample`) controls path-based config routing, API key, PostgreSQL URL, FFmpeg dir, scan paths, and path rewrites.

All daemon settings follow: **CLI flag > environment variable > daemon.json > default**.

- API key: `--api-key` / `SMA_DAEMON_API_KEY` / `daemon.json api_key`
- DB URL: `SMA_DAEMON_DB_URL` / `daemon.json db_url` (no CLI flag — credentials must not appear in `ps`)
- FFmpeg dir: `--ffmpeg-dir` / `SMA_DAEMON_FFMPEG_DIR` / `daemon.json ffmpeg_dir`

See [docs/daemon.md](docs/daemon.md) for full daemon documentation.

### Recycle Bin

When `delete-original = True`, set `recycle-bin` in `[Converter]` to copy the original to a directory before deletion. Uses atomic copy + `.2`/`.3` suffix collision handling.

### Processing Flow

1. `MediaProcessor.isValidSource()` validates input using FFprobe
2. `MediaProcessor.process()` builds FFmpeg options based on settings and source info
3. Conversion runs with optional progress reporting
4. `Metadata.writeTags()` embeds metadata using mutagen (MP4 tags)
5. `qtfaststart` relocates moov atom for streaming optimization
6. Files copied/moved to destination directories
7. Post-process scripts run, Plex/media manager notified

## Documentation

Full documentation is in [docs/](docs/) and served at `http://localhost:8585/docs` when the daemon is running:

- [docs/README.md](docs/README.md) — Architecture and module reference
- [docs/getting-started.md](docs/getting-started.md) — Installation, quick start, CLI
- [docs/configuration.md](docs/configuration.md) — `autoProcess.ini` reference
- [docs/daemon.md](docs/daemon.md) — Daemon mode, API, clustering
- [docs/integrations.md](docs/integrations.md) — Sonarr, Radarr, download clients
- [docs/hardware-acceleration.md](docs/hardware-acceleration.md) — GPU config
- [docs/deployment.md](docs/deployment.md) — mise tasks, systemd, Docker, CI/release
- [docs/troubleshooting.md](docs/troubleshooting.md) — Logs, common issues

## CI / Release

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `ci.yml` | PR / push to main | Runs tests |
| `docker.yml` | PR / push to main or `v*` tag | PR: build-only; main/tag: build + push to GHCR |
| `release.yml` | Push to main | release-please manages release PR + version bump; on release: wheel/sdist + Docker semver tags |

Releases are driven by [release-please](https://github.com/googleapis/release-please). **Do not manually create `v*` tags.**

Conventional commit types: `fix:` → patch, `feat:` → minor, `feat!:` → major.

## Claude Code Slash Commands

- `/project:convert <file>` - Run conversion with auto-tagging
- `/project:preview <file>` - Show FFmpeg options without converting
- `/project:codecs` - List all supported codecs
- `/project:daemon` - Start the HTTP webhook daemon server

## Key Files for Modifications

When adding new codec support:

- `converter/avcodecs.py` - Add codec class with FFmpeg encoder mapping

When adding new settings:

- `resources/readsettings.py` - Add to `DEFAULTS` dict and `readConfig()` method
- `setup/autoProcess.ini.sample` - Add default value

When adding new API endpoints to the daemon:

- `resources/daemon/handler.py` - Add route handler + register in `_GET_ROUTES` or `_POST_ROUTES`

When adding new downloader/manager integration:

- Create new bash script in `triggers/` (usenet/, torrents/, or media_managers/)
- Add settings section in `readsettings.py` if config support is needed

When adding new daemon options:

- Add CLI arg in `daemon.py` `main()`
- Add env var support with `SMA_DAEMON_*` naming
- Add to `daemon.json` via `PathConfigManager.load_config()` in `resources/daemon/config.py`
- Update `setup/daemon.json.sample`
- Document in `docs/daemon.md`
