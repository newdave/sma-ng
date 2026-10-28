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
- Stream-copy repair and library audit: `repair.py`, `resources/media_repair.py`, `resources/library_audit/`
- Docker host CLI and container entrypoint: `setup/sma-host.sh` (shipped as `sma`), `docker/docker-entrypoint.sh`
- Canonical docs: `docs/`, served help: `resources/daemon/docs_ui.py` (renders `docs/` live), GitHub wiki:
  generated from `docs/` by `scripts/wiki_sync.py` (`mise run wiki:build`); never edit the wiki by hand
- Generated/local files to avoid: `venv/`, `**/__pycache__/`, `*.pyc`, `config/sma-ng.yml`

## Working Rules

- Read the nearby implementation and tests before changing code.
- Keep changes small and aligned with existing Python patterns.
- Do not add abstractions, settings, or integrations unless the task requires them.
- Every FFmpeg remux or conversion drops cover art, unknown and data streams: select streams with
  `MediaInfo.cover_art` / `is_unknown_stream` (`converter/ffmpeg.py`) and map by index, never with type maps
  like `0:V` / `0:a?`. Never overwrite an existing output; number it instead.
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

**Documentation is part of the change, not a follow-up.** Any code change that
alters observable behavior MUST include matching doc updates in the same
commit (or split commit series). "Behavior" includes: new or changed config
keys, new or changed CLI flags, new or changed HTTP endpoints, new or
changed mise tasks, new failure modes / error messages operators will see,
schema field additions/removals/renames, and changes to the deploy flow.

Concrete rule per change type:

| Change                                              | Doc that MUST be updated in the same commit                                                          |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| New / changed config key                            | `docs/configuration.md`, `docs/daemon.md` (if under `daemon:`), regenerated `setup/sma-ng.yml.sample` |
| New / changed CLI flag (`manual.py`, `daemon.py`)   | `docs/getting-started.md` or `docs/daemon.md` flag table; usage examples                             |
| New / changed HTTP endpoint                         | `docs/daemon.md` endpoint section AND `docs/openapi.yaml`                                            |
| New / changed mise task                             | `docs/deployment.md` task table; description in the task's `#MISE description=`                      |
| New failure mode / diagnoser pattern                | `docs/troubleshooting.md` if operator-actionable                                                     |
| Changed deploy flow                                 | `docs/deployment.md`, `docs/cluster-operations.md` if cluster-affecting                              |
| New integration (Sonarr/Radarr/Plex/Emby/Jellyfin)  | `docs/integrations.md`                                                                               |
| Bug fix that changes *visible* behavior             | The doc that described the old behavior (don't leave the wrong description live)                     |

If a change has no operator-visible effect (internal refactor, test-only,
private helper rename, dead-code removal), the doc-update requirement is
waived — say so explicitly in the commit message ("internal refactor, no
doc impact"). Don't invent a doc change to satisfy the rule.

Other rules:

- Code, tests, config samples, and docs for one behavior change belong in the same logical change.
- If asked to commit multiple areas, split by behavior or operational outcome, not file type.
- If asked to "commit all changes", commit the whole worktree as multiple logical commits.
- Use conventional commit prefixes such as `fix:`, `feat:`, `refactor:`, `docs:`.
- After each requested commit, run `git pull --rebase` and `git push`.
- After every push to GitHub whose commits changed `docs/` (including `docs/openapi.yaml`) or `.mise/tasks/`,
  run `mise run wiki:publish`. The PostToolUse hook `.claude/hooks/wiki-publish-after-push.sh` does this
  automatically after a `git push` of `main` (Claude and Codex); run it by hand when pushing another way, and
  report the hook's result. A new top-level `docs/*.md` needs an entry in `PAGES` in `scripts/wiki_sync.py`;
  `mise run docs:check` (also in CI) catches broken links, stale `mise run` references and unmapped docs.
- Don't author new PRPs / task breakdowns into `docs/prps/` or `docs/tasks/` for *shipped* features.
  Shipped-state planning docs go stale fast and the git history already preserves intent.
  Write the rationale into the commit message and the operator-facing doc instead.
- Exception: *in-flight* PRPs and task breakdowns
  may live in `docs/prps/` and `docs/tasks/` while implementation is active. They MUST
  carry a `STATUS:` banner under the H1 (`IN-FLIGHT`, `COMPLETE`, `SUPERSEDED`).
  Once `STATUS: COMPLETE` lands, prune the doc in a follow-up unless it documents
  operator-visible behavior that doesn't fit elsewhere.

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
mise run media:repair -- -n /path/to/file-or-dir
mise run test:cov
mise run test:shell
mise run docs:check
mise run wiki:publish
mise run release:status
mise run release:notes
```

Direct commands that are often useful:

```bash
python manual.py -i "/path/to/file.mkv" -a
python manual.py -i "/path/to/file.mkv" -oo
python manual.py -cl
python manual.py --audit -i /path/to/library
python repair.py -n /path/to/file-or-dir
python daemon.py --host 0.0.0.0 --port 8585 --workers 4
python daemon.py --smoke-test
```

On a Docker host, operators use the `sma` command (`sma convert|repair|audit ...`) rather than `docker exec`.

## Validation Matrix

Run the smallest meaningful check for the files touched.

- Daemon: `venv/bin/python -m pytest tests/test_daemon.py tests/test_handler.py tests/test_worker.py -q`
- Media/FFmpeg: `venv/bin/python -m pytest tests/test_mediaprocessor.py tests/test_metadata.py -q`
- Rename/log helpers: `venv/bin/python -m pytest tests/test_rename.py tests/test_log.py -q`
- Repair/audit: `venv/bin/python -m pytest tests/test_media_repair.py tests/test_repair.py tests/test_library_audit_engine.py -q`
- Docker/compose and host CLI: `venv/bin/python -m pytest tests/test_docker.py tests/test_sma_host.py -q`
- Shell scripts: `mise run test:shell`
- Docs and wiki: `mise run docs:check`
- Broad pass: `mise run test`
- Coverage policy: global line coverage is at least 90%, and production modules with at least 100 statements need
  at least 70% per-module coverage; `mise run test:cov` (also run in CI) enforces both. Do not lower thresholds or
  hide logic with `# pragma: no cover`.

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
  `#MISE description=...`; keep shared helpers outside `.mise/tasks/`; add a row to `docs/deployment.md`
  (the wiki `Mise-Tasks` page is generated from the descriptions).

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

Plugins and skills are scoped to this project:

- `.claude/settings.json` `enabledPlugins` and `.codex/config.toml` `[plugins.*]` hold the required plugin set:
  `pyright-lsp`.
- The same file sets unneeded globally enabled plugins to `false` (`bp`, `codex`, `code-simplifier`,
  `frontend-design`, `rust-analyzer-lsp`).
- Project skills are vendored in `.claude/skills/`; `.skillfish.json` records the upstream source.
- Add new plugins or skills here, never in `~/.claude/`.
- The skill-reminder hook runs on `UserPromptSubmit`: Claude via `.claude/settings.json` `hooks`
  (`.claude/hooks/claude-code/`), Codex via `.codex/hooks.json` (`.claude/hooks/codex/`).
- The wiki-publish hook runs on `PostToolUse` (`Bash`) from both files: `.claude/hooks/wiki-publish-after-push.sh`
  publishes the generated wiki after a `git push` of `main` that changed docs; `SMA_HOOK_WIKI_DRY_RUN=1` makes it
  a dry run.

### Activation Routing

Prefer the installed skills and subagents over ad-hoc reasoning. Route by trigger, not by habit.
If a row matches the current request, invoke the listed tool before (or instead of) writing code directly.

| Trigger / signal in the request                                          | Tool to invoke                          |
| ------------------------------------------------------------------------ | --------------------------------------- |
| Vague goal, no files named, no acceptance criteria                       | skill `discovering`                     |
| New FFmpeg pipeline / hwaccel / codec / container / persistence approach | skill `researching`                     |
| Change spans 2+ of converter/, resources/, triggers/, .mise/tasks/, docs/ | skill `blueprinting`                    |
| Implementation plan is agreed; ready to write code                       | skill `implementing` + `python-testing` |
| "refactor / clean up / simplify / rename / extract / dedupe"             | skill `refactoring`                     |
| Edits to `resources/config_schema.py`, `readsettings.py`, `daemon/config.py`, `setup/sma-ng.yml.sample`, or any field under daemon:/base:/profiles:/services: | skill `python-configuration` + subagent `config-schema-reviewer` |
| Edits to `converter/*.py`, `resources/mediaprocessor.py`, hwaccel filter graphs, encoder option blocks | skill `ffmpeg-cli` / `ffmpeg-hardware-acceleration` + subagent `ffmpeg-args-auditor` |
| Writing or updating pytest coverage                                      | skill `python-testing` + subagent `test-writer` |
| Operator docs, openapi, changelog, deploy/cluster docs                   | subagent `documentation-writer`         |
| Read-only exploration ("where is X", "what references Y")                | subagent `explorer`                     |
| Pre-merge / pre-commit review of staged changes                          | subagent `code-reviewer`                |
| Adding a new skill                                                       | skill `creating-skills`                 |
| Adding a new subagent                                                    | skill `creating-subagents`              |

When two rows match (e.g. schema + FFmpeg args), invoke both reviewers; their scopes do not overlap.
A skill miss is cheaper than a regression — bias toward invoking when uncertain.
