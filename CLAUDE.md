# CLAUDE.md

This is the authoritative workflow file for Claude Code in this repository.
Keep it compact: prefer pointers to project docs over duplicating reference material.

## Mission

SMA-NG is a Python 3.12+ media transcoding application built around FFmpeg/FFprobe.
The core job is to convert media to MP4, preserve or transform audio/subtitle/video streams according to
`sma-ng.yml`, tag output with metadata, and integrate with Sonarr, Radarr, Plex, and download clients.

Optimize work for:

- correct FFmpeg option generation and source probing
- predictable daemon job handling and path-based config routing
- safe config schema changes with generated samples
- useful tests around media, daemon, and integration behavior
- documentation that helps operators run the transcoder

## High-Signal Paths

- CLI: `manual.py`
- Daemon entrypoint: `daemon.py`
- Daemon package: `resources/daemon/`
- Conversion pipeline: `resources/mediaprocessor.py`
- Settings schema and projection: `resources/config_schema.py`, `resources/readsettings.py`
- FFmpeg wrapper and codec/container definitions: `converter/ffmpeg.py`, `converter/avcodecs.py`, `converter/formats.py`
- Metadata and integrations: `resources/metadata.py`, `resources/mediamanager.py`, `autoprocess/plex.py`, `triggers/`
- Canonical docs: `docs/`, served help: `resources/docs.html`, wiki copy: `/tmp/sma-wiki/`
- Generated/local files to avoid: `venv/`, `**/__pycache__/`, `*.pyc`, `config/sma-ng.yml`

## Working Rules

- Read the nearby implementation and tests before changing code.
- Keep changes small and aligned with existing Python patterns.
- Do not add abstractions, settings, or integrations unless the task requires them.
- Keep shell entrypoints POSIX/ShellCheck clean.
  Do not embed inline Python in shell scripts or committed shell commands; move Python logic to a `.py` helper.
- Keep application log records single-line.
  Do not use `print(...)` in `resources/daemon/` except with a justified `# noqa: log-print`.
  Do not log secrets such as API keys, DB URLs, usernames, passwords, tokens, or node IDs.
- Markdown must pass markdownlint: ATX headings, fenced code languages, blank lines around blocks, and prose lines up
  to 120 characters.
- Do not add AI attribution or `Co-Authored-By` lines to commits.
- Do not create manual `v*` tags; release-please owns releases.

## Documentation And Commits

- Code, tests, config samples, and docs for one behavior change belong in the same logical change.
- For user-facing behavior changes, update all relevant docs in the same change:
  `docs/`, matching `/tmp/sma-wiki/` pages, and `resources/docs.html`.
- If asked to commit multiple areas, split by behavior or operational outcome, not file type.
- If asked to "commit all changes", commit the whole worktree as multiple logical commits.
- Use conventional commit prefixes such as `fix:`, `feat:`, `refactor:`, `docs:`.
- After each requested commit, run `git pull --rebase` and `git push`.

## Common Commands

Prefer `mise` tasks when available.

```bash
mise install
mise run setup:deps
mise run setup:deps:dev
mise run test
mise run test:lint
mise run dev:lint
mise run config:gpu
mise run config:generate
mise run daemon:start
mise run media:convert -- /path/to/file.mkv
mise run media:preview -- /path/to/file.mkv
mise run media:codecs
```

Direct commands that are often useful:

```bash
python manual.py -i "/path/to/file.mkv" -a
python manual.py -i "/path/to/file.mkv" -oo
python manual.py -cl
python daemon.py --host 0.0.0.0 --port 8585 --workers 4
python daemon.py --smoke-test
```

## Validation Matrix

Run the smallest meaningful check for the files touched.

- Daemon: `venv/bin/python -m pytest tests/test_daemon.py tests/test_handler.py tests/test_worker.py -q`
- Media/FFmpeg: `venv/bin/python -m pytest tests/test_mediaprocessor.py tests/test_metadata.py -q`
- Rename/log helpers: `venv/bin/python -m pytest tests/test_rename.py tests/test_log.py -q`
- Docker/compose: `venv/bin/python -m pytest tests/test_docker.py -q`
- Broad pass: `mise run test`
- Coverage policy: global line coverage is at least 90%, and production modules with at least 100 statements need
  at least 70% per-module coverage. Do not lower thresholds or hide logic with `# pragma: no cover`.

## Change Recipes

- New codec: update `converter/avcodecs.py`; add tests for option mapping and compatibility.
- New config field: update `resources/config_schema.py`; regenerate `setup/sma-ng.yml.sample` with
  `mise run config:sample`; add `resources/readsettings.py` projection only for legacy `settings.*` consumers.
- New daemon option: update `daemon.py`, `resources/daemon/config.py`, `setup/sma-ng.yml.sample`, and
  `docs/daemon.md`; daemon runtime settings should live in `sma-ng.yml` or explicit CLI flags.
- New daemon endpoint: update `resources/daemon/handler.py` route tables and handler tests.
- New downloader/media-manager integration: add script under `triggers/`; keep Python logic in helper modules; add
  schema/sample/docs support as needed.
- New or changed `mise` task: add an executable script under `.mise/tasks/<group>/` with
  `#MISE description=...`; keep shared helpers outside `.mise/tasks/`; update wiki `Mise-Tasks.md`.

## Configuration Model

The main config is YAML: `config/sma-ng.yml` copied from `setup/sma-ng.yml.sample`.
Top-level buckets are `daemon:`, `base:`, `profiles:`, and `services:`.
Use kebab-case in YAML.
Daemon settings resolve as CLI flag, then `sma-ng.yml`, then default.
The daemon no longer reads `SMA_*` environment variables for runtime configuration.
See `docs/configuration.md` and `docs/daemon.md` for full reference.

## Claude Tooling Surface

The `.claude/` directory is active configuration, not legacy metadata.
Keep slash commands, agents, and skills short and specific to this Python FFmpeg transcoder.
When workflow rules change, update this file first and sync `AGENTS.md` plus any matching `.codex/` mirrors.
