# AGENTS.md

Codex bootstrap for this repository.
`CLAUDE.md` is authoritative; keep this file aligned and shorter than the Claude source.

## Mission

SMA-NG is a Python 3.12+ FFmpeg/FFprobe media transcoder.
Work should protect conversion correctness, daemon reliability, config schema safety, and operator docs.

## Priority Paths

- `manual.py` - manual conversion CLI
- `daemon.py`, `resources/daemon/` - webhook daemon, jobs, path routing, logs
- `resources/mediaprocessor.py` - conversion pipeline
- `converter/ffmpeg.py`, `converter/avcodecs.py`, `converter/formats.py` - FFmpeg interface and mappings
- `resources/config_schema.py`, `resources/readsettings.py` - YAML schema and legacy settings projection
- `resources/metadata.py`, `resources/mediamanager.py`, `autoprocess/plex.py`, `triggers/` - tagging and integrations
- `docs/`, `/tmp/sma-wiki/`, `resources/docs.html` - docs surfaces that must stay synchronized

## Rules

- Read nearby code and tests before editing.
- Keep changes small, Pythonic, and focused on the transcoding behavior requested.
- Do not edit generated/local paths such as `venv/`, `**/__pycache__/`, `*.pyc`, or `config/sma-ng.yml`.
- No inline Python in shell scripts or committed shell commands.
- Shell scripts must stay ShellCheck clean.
- Markdown must stay markdownlint clean.
- Daemon logs must be single-line and must not include secrets.
- No AI attribution or `Co-Authored-By` lines in commits.
- Do not create manual `v*` tags.

## Docs And Commits

- User-facing behavior changes require synchronized updates in `docs/`, matching `/tmp/sma-wiki/` pages, and
  `resources/docs.html`.
- Keep code, tests, samples, and docs for one behavior change in the same logical change.
- If committing multiple areas, split by behavior or operational outcome.
- Conventional commit prefixes are expected.
- `CLAUDE.md` remains the source of truth for post-commit sync details.

## Commands

```bash
mise install
mise run setup:deps
mise run setup:deps:dev
mise run test
mise run test:lint
mise run dev:lint
mise run config:generate
mise run daemon:start
mise run media:convert -- /path/to/file.mkv
mise run media:preview -- /path/to/file.mkv
mise run media:codecs
```

Direct equivalents:

```bash
python manual.py -i "/path/to/file.mkv" -a
python manual.py -i "/path/to/file.mkv" -oo
python manual.py -cl
python daemon.py --host 0.0.0.0 --port 8585 --workers 4
python daemon.py --smoke-test
```

## Validation

- Daemon: `venv/bin/python -m pytest tests/test_daemon.py tests/test_handler.py tests/test_worker.py -q`
- Media/FFmpeg: `venv/bin/python -m pytest tests/test_mediaprocessor.py tests/test_metadata.py -q`
- Rename/log helpers: `venv/bin/python -m pytest tests/test_rename.py tests/test_log.py -q`
- Docker/compose: `venv/bin/python -m pytest tests/test_docker.py -q`
- Broad pass: `mise run test`

Say explicitly when the relevant validation could not be run.

## Change Map

- Codec: `converter/avcodecs.py`
- Config field: `resources/config_schema.py`, regenerated `setup/sma-ng.yml.sample`, optional
  `resources/readsettings.py`
- Daemon option: `daemon.py`, `resources/daemon/config.py`, sample config, `docs/daemon.md`
- Daemon endpoint: `resources/daemon/handler.py`
- Integration script: `triggers/` plus helper Python module when logic is non-trivial
- `mise` task: executable `.mise/tasks/<group>/` script with `#MISE description=...`

## Claude Compatibility

Codex treats `.claude/` as active compatibility configuration.
When workflow rules change, update `CLAUDE.md` first, then sync this file and any Codex mirrors under `.codex/`.
