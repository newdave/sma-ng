# AGENTS.md

This file provides guidance to Codex when working with code in this repository.

## Authority and Compatibility

- **`CLAUDE.md` is the authoritative workflow document for this repository.**
- If `AGENTS.md` and `CLAUDE.md` overlap, treat `CLAUDE.md` as the source of truth and keep `AGENTS.md` aligned to it.
- The `.claude/` directory is not legacy; it is the authoritative tool-specific configuration surface for Claude-based workflows.
- Codex should use this file as its native bootstrap surface, but it must preserve compatibility with the Claude configuration and must not silently diverge from it.
- When updating workflow rules, commit standards, documentation policy, or repo conventions, update `CLAUDE.md` first and then sync any corresponding Codex-facing guidance in `AGENTS.md`.

## Documentation Rules

- **Keep documentation in sync with code changes.** When you add, change, or remove a feature, update all relevant docs in the same change.
- **Documentation and tests that explain or validate a feature change belong to the same logical commit as that feature change.** Do not split one logical change into separate code, docs, or test commits just because the files differ.
- **Every documentation change must be applied in three places:**
  1. `docs/` - the canonical source in the main repo
  2. GitHub wiki (`/tmp/sma-wiki/`) - the corresponding wiki page(s); push with `git add -A && git commit -m "docs: ..." && git push origin HEAD:master`
  3. `resources/docs.html` - the inline help served at `http://localhost:8585/docs`

## Git Commit Rules

- Do not add any AI attribution or `Co-Authored-By` lines to commits.
- Break large changes into smaller logical commits when the user asks for commits.
- Define “logical change” by behavior, feature, fix, or operational outcome - not by file type. A feature/fix and its tests/docs/config updates normally belong in the same commit.
- Never create a single mixed commit when the work spans multiple logical areas.
- Commit the full worktree as a series of small commits grouped by logical function when multiple areas are touched.
- Before committing, review the diff and split staged changes by change boundary rather than by file category or directory.
- If the user asks to "commit all changes", interpret that as committing the entire worktree using multiple logical commits, not one umbrella commit.
- Do not bundle unrelated daemon changes, trigger changes, tests, docs, or workflow/config updates into one commit. But when those files all support the same underlying change, keep them together in a single logical commit.
- Use informative conventional commit prefixes such as `fix:`, `feat:`, and `refactor:`.
- Do not create manual `v*` tags.
- `CLAUDE.md` is authoritative for commit workflow details, including post-commit sync expectations.

## Shell Script Rules

- Do not embed inline Python in shell scripts or shell commands committed to this repository. This includes `python -c`, `python3 -c`, and Python heredocs.
- If shell-based automation needs Python logic, move that logic into a standalone `.py` helper and call it from the shell script.
- Prefer keeping JSON parsing, payload construction, and non-trivial data transforms in those helper modules rather than re-embedding them in Bash.
- Shell scripts must conform to ShellCheck best practices and must not produce any ShellCheck warnings or errors. Suppress a warning with a `# shellcheck disable=SCxxxx` comment only when the flag is genuinely a false positive, and always add an inline explanation for why the suppression is safe.

## Markdown Rules

- Markdown files must conform to markdownlint best practices and must not produce any markdownlint warnings or errors.
- Use ATX-style headings (`#`), fenced code blocks (` ``` `), and consistent list markers.
- Every fenced code block must declare a language identifier.
- Blank lines are required before and after headings, lists, and code blocks.
- Lines must not exceed 120 characters (prose) or be wrapped mid-sentence; prefer semantic line breaks for long paragraphs.

## Development Environment

```bash
# With mise (recommended)
mise install
mise run setup:deps

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

`mise` is the preferred task runner for local development and deployment. Common tasks:

- `mise run setup:deps` - create `venv` and install dependencies
- `mise run setup:deps:dev` - install dev and test dependencies
- `mise run test` - run the test suite
- `mise run test:lint` - run `ruff`
- `mise run dev:lint` - auto-fix lint issues
- `mise run config:gpu` - detect available hardware acceleration
- `mise run config:generate` - generate config with detected GPU
- `mise run daemon:start` - start the daemon on `0.0.0.0:8585`
- `mise run media:convert -- /path/to/file.mkv` - convert a file
- `mise run media:preview -- /path/to/file.mkv` - preview options only
- `mise run media:codecs` - list supported codecs

## Repo Workflow Map

Use these task buckets to keep work predictable and aligned with the repo's existing Claude-oriented workflow.

### Exploration

Use for:

- understanding architecture
- locating config or daemon behavior
- mapping path-routing, logging, or integration behavior

Common commands:

- `rg --files`
- `rg "pattern" path/`
- `git diff`
- `git status`

### Implementation

Use for:

- feature work
- bug fixes
- daemon changes
- config parsing changes

Implementation expectations:

- keep docs in sync
- preserve repo conventions from `CLAUDE.md`
- prefer minimal logical commits over one large mixed commit

### Test Writing

Use for:

- regression coverage
- branch coverage increases
- daemon/API contract tests
- static Docker/docs/config tests

Typical test targets:

- daemon changes: `venv/bin/python -m pytest tests/test_daemon.py tests/test_handler.py tests/test_worker.py -q`
- media processor changes: `venv/bin/python -m pytest tests/test_mediaprocessor.py -q`
- Docker/compose changes: `venv/bin/python -m pytest tests/test_docker.py -q`
- rename/log helper changes: `venv/bin/python -m pytest tests/test_rename.py tests/test_log.py -q`

### Documentation

Use for:

- user-facing docs
- deployment/runbooks
- architecture notes
- getting-started and operations guidance

Documentation expectations:

- `docs/` is the canonical repo copy
- also sync `/tmp/sma-wiki/` and `resources/docs.html` when required by the change
- if only part of that sync is being done in the current task, call it out explicitly

## Project Overview

SMA-NG (Next-Generation Media Automator) is a Python-based media conversion and tagging automation tool. It converts media files to MP4 format using FFmpeg and tags them with metadata from TMDB. It integrates with media managers (Sonarr, Radarr) and downloaders (NZBGet, SABNZBD, Deluge, uTorrent, qBittorrent).

## Common Commands

```bash
# Install dependencies
pip install -r setup/requirements.txt

# Convert with mise wrapper
mise run media:convert -- /path/to/file.mkv

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

# Use alternate config file
python manual.py -i "/path/to/file.mkv" -a -c config/autoProcess.lq.ini

# Force re-encode even if format matches
python manual.py -i "/path/to/file.mp4" -a -fc

# Tag only
python manual.py -i "/path/to/file.mp4" -to

# Start daemon (HTTP webhook server)
python daemon.py --host 0.0.0.0 --port 8585 --workers 4

# Start daemon with API key authentication
python daemon.py --host 0.0.0.0 --port 8585 --workers 4 --api-key YOUR_SECRET_KEY
```

After conversion, `manual.py` automatically triggers a rescan on the matching Sonarr or Radarr instance based on the output file directory path.

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
python daemon.py --daemon-config /path/to/sma-ng.yml --workers 4

# Dry-run all configs and exit
python daemon.py --smoke-test

# Set a per-job timeout
python daemon.py --job-timeout 7200

# Graceful shutdown (waits for active conversions to finish)
curl -X POST http://localhost:8585/shutdown -H "X-API-Key: YOUR_SECRET_KEY"
```

### Public Endpoints

- `POST /webhook/generic` - Submit conversion job (returns job ID)
- `GET /health` - Health check with job statistics
- `GET /status` - Cluster-wide node and job status
- `GET /jobs` - List jobs with filtering and pagination
- `GET /jobs/<id>` - Get specific job details
- `GET /configs` - Show path-to-config mappings and status
- `GET /stats` - Job statistics by status
- `GET /scan` - Filter unscanned paths
- `GET /browse` - Browse configured filesystem paths
- `GET /logs` - List all log files
- `GET /logs/<name>` - Get log content
- `GET /logs/<name>/tail` - Poll for new entries after byte offset
- `POST /webhook/sonarr` - Native Sonarr webhook endpoint
- `POST /webhook/radarr` - Native Radarr webhook endpoint
- `POST /cleanup` - Remove old completed or failed jobs
- `POST /reload` - Reload `sma-ng.yml`
- `POST /restart` - Graceful restart
- `POST /shutdown` - Graceful shutdown
- `POST /jobs/<id>/requeue` - Requeue a failed job
- `POST /jobs/<id>/cancel` - Cancel a pending or running job
- `POST /jobs/<id>/priority` - Set job priority
- `POST /jobs/requeue` - Requeue failed jobs in bulk
- `POST /scan/filter` - Filter unscanned paths for large lists
- `POST /scan/record` - Mark paths as scanned

### Authentication

When enabled, all endpoints except `/health` require a valid API key.

Priority order:

1. `--api-key`
2. `SMA_DAEMON_API_KEY`
3. `api_key` in `sma-ng.yml` `daemon:` section

Public endpoints: `/`, `/dashboard`, `/admin`, `/health`, `/status`, `/docs`, `/favicon.png`

### Path-Based Configuration

The daemon can use different `autoProcess.ini` files based on the input path. Matching is longest-prefix-first, so more specific paths take priority over broader ones.

Important `sma-ng.yml` `daemon:` keys:

- `default_config` - fallback config when no path matches
- `api_key` - daemon authentication key
- `db_url` - PostgreSQL URL for distributed mode
- `ffmpeg_dir` - prepended to `PATH` for conversion jobs
- `media_extensions` - extensions used by scanning and `/browse`
- `path_rewrites` - prefix substitutions before config matching
- `scan_paths` - scheduled background scanning definitions
- `path_configs` - per-path config routing
- `smoke_test` - run dry-run validation of all configs at startup
- `job_timeout_seconds` - kill long-running jobs after the configured number of seconds
- `recycle_bin_max_age_days` - recycle-bin cleanup retention
- `recycle_bin_min_free_gb` - free-space watermark for recycle-bin cleanup

Each `path_configs` entry can also include `default_args` to prepend arguments to jobs from that path.

### Per-Config Logging

The daemon writes rotating logs to `logs/daemon.log` and separate per-config log files in `logs/` based on the config in use. Per-config logs rotate at 10 MB with 5 backups. Use `--logs-dir` to change the logs directory.

### Concurrency Control

Up to `--workers` jobs can run simultaneously. Jobs for different configs run in parallel immediately. Jobs for the same config share the worker limit and queue once that limit is reached.

### Job Persistence

Jobs are stored in PostgreSQL. This provides restart recovery, job history, filtering, and distributed coordination across multiple nodes.

Cluster-related runtime flags:

- `--heartbeat-interval` - heartbeat interval in seconds
- `--stale-seconds` - requeue running jobs if a node heartbeat goes stale

## Architecture

### Entry Points

- `manual.py` - CLI tool for manual conversion and tagging
- `daemon.py` - thin entry point that imports `resources.daemon.*` and runs `main()`
- `triggers/media_managers/sonarr.sh` and `triggers/media_managers/radarr.sh` - media-manager trigger scripts
- `triggers/usenet/` and `triggers/torrents/` - downloader integration scripts

### Daemon Package

The daemon implementation lives under `resources/daemon/`.

| Module         | Contents                                                     |
| -------------- | ------------------------------------------------------------ |
| `constants.py` | `SCRIPT_DIR`, default values, status constants               |
| `db.py`        | `PostgreSQLJobDatabase`                                      |
| `config.py`    | `ConfigLockManager`, `ConfigLogManager`, `PathConfigManager` |
| `handler.py`   | `WebhookHandler`, route handlers, HTML helpers               |
| `threads.py`   | `_StoppableThread`, `HeartbeatThread`, `ScannerThread`       |
| `worker.py`    | `ConversionWorker`, `WorkerPool`                             |
| `server.py`    | `DaemonServer`, `_validate_hwaccel`                          |

### Core Modules

- `resources/mediaprocessor.py` - central conversion pipeline
- `resources/readsettings.py` - parses `autoProcess.ini` and defines defaults
- `resources/metadata.py` - TMDB metadata fetch and MP4 tagging
- `resources/postprocess.py` - runs custom scripts from `post_process/`
- `resources/extensions.py` - TMDB API key and file extension definitions
- `resources/mediamanager.py` - Sonarr and Radarr helpers used by triggers and rescans
- `resources/log.py` and `resources/lang.py` - logging and language helpers
- `converter/ffmpeg.py` - FFmpeg and FFprobe wrapper classes
- `converter/avcodecs.py` - codec definitions and encoder mappings
- `converter/formats.py` - container format definitions
- `autoprocess/plex.py` - Plex refresh integration

## Configuration

The main config file is `config/autoProcess.ini` (copy from `setup/autoProcess.ini.sample`). Override location via `SMA_CONFIG`.

Important sections include `[Converter]`, `[Video]`, `[HDR]`, `[Audio]`, `[Subtitle]`, `[Metadata]`, `[Sonarr]`, `[Radarr]`, and `[Plex]`.

`mise run config:generate` generates three quality-profile configs:

- `config/autoProcess.ini` - default regular-quality profile
- `config/autoProcess.rq.ini` - explicit regular-quality profile
- `config/autoProcess.lq.ini` - lower-quality profile for bandwidth-limited destinations

Daemon settings follow:

- CLI flag
- environment variable
- `sma-ng.yml` `daemon:` section
- default

Examples:

- API key: `--api-key` / `SMA_DAEMON_API_KEY` / `sma-ng.yml daemon.api_key`
- DB URL: `SMA_DAEMON_DB_URL` / `sma-ng.yml daemon.db_url`
- FFmpeg dir: `--ffmpeg-dir` / `SMA_DAEMON_FFMPEG_DIR` / `sma-ng.yml daemon.ffmpeg_dir`

## Project Documentation

Main documentation lives in `docs/` and is also served at `http://localhost:8585/docs` when the daemon is running.

- `docs/README.md` - architecture and module reference
- `docs/getting-started.md` - installation, quick start, CLI
- `docs/configuration.md` - `autoProcess.ini` reference
- `docs/daemon.md` - daemon mode, API, clustering
- `docs/integrations.md` - Sonarr, Radarr, and downloader integrations
- `docs/hardware-acceleration.md` - GPU configuration
- `docs/deployment.md` - mise tasks, systemd, Docker, CI, release
- `docs/troubleshooting.md` - logs and common issues

When changing functionality, keep `docs/`, `resources/docs.html`, and the wiki copy in sync.

## Validation Expectations

Before closing a task, run the smallest validation set that meaningfully covers the area changed.

Suggested validation matrix:

- daemon package changes:
  `venv/bin/python -m pytest tests/test_daemon.py tests/test_handler.py tests/test_worker.py -q`
- FFmpeg/media processing changes:
  `venv/bin/python -m pytest tests/test_mediaprocessor.py tests/test_metadata.py -q`
- Docker/compose changes:
  `venv/bin/python -m pytest tests/test_docker.py -q`
- docs-only changes:
  no tests required unless the docs describe behavior changed in code in the same task
- broad confidence pass:
  `mise run test`

If you cannot run the appropriate validation, say so explicitly in the final response.

## Codex and `.claude/`

Codex should treat `.claude/` as an authoritative compatibility surface, not as disposable metadata.

Practical rules:

- do not remove or rewrite `.claude/` conventions just because Codex does not consume them directly
- when adding Codex-native guidance, prefer mirroring or translating existing Claude workflow rather than replacing it
- if a Codex-native file and a Claude-native file would express the same rule, keep the wording aligned and state which one is authoritative
- when in doubt, preserve Claude behavior and document the Codex equivalent

## Integrations

Sonarr and Radarr support two integration modes:

- native webhook endpoints at `/webhook/sonarr` and `/webhook/radarr` (recommended)
- local custom scripts under `triggers/media_managers/`

Any config section starting with `Sonarr` or `Radarr` is auto-discovered. Matching uses the configured `path` and longest-prefix-first behavior, which is also how `manual.py` chooses which instance to rescan after conversion.

Download client integrations live under `triggers/` and submit jobs to the daemon:

- `triggers/usenet/nzbget.sh`
- `triggers/usenet/sabnzbd.sh`
- `triggers/torrents/qbittorrent.sh`
- `triggers/torrents/deluge.sh`
- `triggers/torrents/utorrent.sh`

Plex refresh behavior is configured under `[Plex]`. Disable Plex auto-scanning to avoid scans during active conversions.

## Deployment

Remote deployment is built around `mise` tasks and `setup/.local.yml`.

Key tasks:

- `mise run deploy:setup` - first-time host prep
- `mise run remote:run` - sync code, install deps, reload systemd
- `mise run config:roll` - create missing configs, merge new keys, stamp credentials
- `mise run deploy:restart` - restart `sma-daemon` on all hosts

Systemd unit: `setup/sma-daemon.service`

Important service notes:

- loads `config/daemon.env`
- uses `KillMode=mixed` for graceful draining
- default `ReadWritePaths` include `/opt/sma/config`, `/opt/sma/logs`, `/transcodes`, and `/mnt`

Docker image tags are `latest`, semver tags, and `main`.

## CI / Release

| Workflow      | Trigger                           | What it does                                                     |
| ------------- | --------------------------------- | ---------------------------------------------------------------- |
| `ci.yml`      | PR / push to main                 | Runs tests                                                       |
| `docker.yml`  | PR / push to main (path-filtered) | PR build-only smoke test; main pushes rolling `main` tag to GHCR |
| `release.yml` | Push to main                      | release-please manages release PRs and release publishing        |

Releases are driven by release-please. The version source of truth is `pyproject.toml` under `[project] version`.

This repository uses release-please's `always-bump-patch` versioning strategy by default, so releases normally advance the point release and patch identifiers are not capped (for example `1.2.12323` is valid).

## Codex Equivalents For Claude Code Config

Claude-specific files under `.claude/` do not map directly to Codex runtime config. In this repository, the Codex equivalent is this `AGENTS.md` file plus the normal repo layout.

Translate the Claude setup as follows:

- `CLAUDE.md` -> `AGENTS.md`
- `.claude/commands/*.md` -> use the matching shell commands from the sections above unless a repo-local Codex mirror exists under `.codex/commands/`
- `.codex/commands/*.md` -> Codex-native mirrors of Claude slash-command workflows; keep `.claude/commands/*.md` authoritative and sync Codex copies when the Claude command changes
- `.claude/agents/*.md` -> follow the same intent directly in Codex:
  - `explorer` -> read-only repo inspection before changing code
  - `implementer` -> make the smallest working code change, then verify
  - `test-writer` -> add focused tests around changed behavior
  - `documentation-writer` -> keep docs scannable and example-driven
  - `code-reviewer` -> review for correctness, risk, regressions, and missing tests
- `.claude/skills/*` -> use the same workflow concepts when relevant: discovery, research, blueprinting, implementation, refactoring

Claude permission allowlists in `.claude/settings*.json` are informational only for Codex. Codex should still prefer the common commands documented here and avoid editing ignored/generated paths such as:

- `venv/`
- `**/__pycache__/`
- `*.pyc`
- `config/autoProcess.ini`

When diagnosing runtime issues, check:

- `logs/daemon.log`
- per-config logs in `logs/`
- `journalctl -u sma-daemon -f` for systemd deployments

Useful environment variables:

- `SMA_CONFIG`
- `SMA_DAEMON_API_KEY`
- `SMA_DAEMON_DB_URL`
- `SMA_DAEMON_FFMPEG_DIR`
- `SMA_DAEMON_HOST`
- `SMA_DAEMON_PORT`
- `SMA_DAEMON_WORKERS`
- `SMA_DAEMON_CONFIG`
- `SMA_DAEMON_LOGS_DIR`

## Key Files For Modifications

When adding new codec support:

- `converter/avcodecs.py` - add codec class with FFmpeg encoder mapping

When adding new settings:

- `resources/readsettings.py` - add to `DEFAULTS` and `readConfig()`
- `setup/autoProcess.ini.sample` - add the default value

When adding new API endpoints to the daemon:

- `resources/daemon/handler.py` - add the route handler and register it

When adding new downloader or manager integrations:

- create the script under `triggers/`
- do not embed inline Python in the shell entrypoint; place Python logic in a standalone helper module and invoke it
- add config support in `resources/readsettings.py` if needed

When adding new daemon options:

- add the CLI arg in `daemon.py`
- add env var support using `SMA_DAEMON_*`
- add config loading in `resources/daemon/config.py`
- update `setup/sma-ng.yml.sample`
- update `docs/daemon.md`

When adding or modifying `mise` tasks:

- define runnable tasks as executable scripts under grouped `.mise/tasks/<group>/` subdirectories with a
  `#MISE description=...` header
- keep `mise.toml` limited to tool and environment configuration; do not add inline `[tasks.*]` definitions
- shared helper code for task scripts belongs outside `.mise/tasks/` (for example `.mise/shared/`) so it is not
  exposed as a runnable task
- update wiki `Mise-Tasks.md` to document the task name, description, and usage
