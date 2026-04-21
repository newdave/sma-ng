# Contributing

This repository uses `CLAUDE.md` as the authoritative workflow and repo-convention document.

For agent-specific guidance:

- Codex: see [AGENTS.md](AGENTS.md)
- Claude: see [CLAUDE.md](CLAUDE.md)

## Source of Truth

- **`CLAUDE.md` is authoritative for workflow rules, commit rules, and documentation-sync policy.**
- `AGENTS.md` is the Codex-facing translation layer and should remain aligned to `CLAUDE.md`.
- `.claude/` is an active configuration surface for Claude workflows and should not be treated as legacy or disposable.

## Development Setup

Preferred:

```bash
mise install
mise run install
```

Alternative manual setup:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r setup/requirements.txt
```

Useful tasks:

```bash
mise run install-dev
mise run test
mise run lint
mise run detect-gpu
mise run config
mise run daemon
```

## Change Expectations

Keep changes scoped and contextual:

- fix code with the smallest sensible diff
- add or update tests for behavior changes
- update docs when user-facing or operator-facing behavior changes
- avoid mixing unrelated work into one commit

## Documentation Policy

When functionality changes, keep documentation in sync.

Required documentation surfaces:

1. `docs/` in the main repo
2. GitHub wiki copy under `/tmp/sma-wiki/`
3. `resources/docs.html`

If you are only updating one or two of these surfaces in a task, call out the remaining sync explicitly.

## Commit Policy

- use conventional commit prefixes such as `fix:`, `feat:`, `docs:`, `test:`, and `refactor:`
- do not add AI attribution or `Co-Authored-By` lines
- split unrelated work into separate commits
- do not make a single mixed commit when the work spans multiple logical areas
- commit the full worktree as a sequence of smaller commits grouped by logical function when multiple areas are touched
- review the diff before committing and stage changes by area instead of bundling unrelated work together
- if someone asks to "commit all changes", treat that as "commit the entire worktree using multiple logical commits"
- do not create manual `v*` tags

The repository’s Claude workflow also expects:

1. commit the logical change
2. run `git pull --rebase`
3. run `git push`

## Validation

Run the smallest useful validation set for the area changed.

Examples:

```bash
# Daemon changes
venv/bin/python -m pytest tests/test_daemon.py tests/test_handler.py tests/test_worker.py -q

# Media processor changes
venv/bin/python -m pytest tests/test_mediaprocessor.py -q

# Docker/compose changes
venv/bin/python -m pytest tests/test_docker.py -q

# Broad suite
mise run test

# Coverage report
mise run test-cov
```

Docs-only changes usually do not require tests unless code changed in the same task.

## Repo Areas

- `manual.py` - CLI conversions
- `daemon.py` - daemon entrypoint
- `resources/daemon/` - daemon package
- `resources/mediaprocessor.py` - conversion pipeline
- `resources/readsettings.py` - INI config parsing/defaults
- `converter/` - codec and FFmpeg abstractions
- `docs/` - canonical documentation

## When Adding New Behavior

If you add:

- new config options: update `resources/readsettings.py`, samples, and docs
- new daemon options: update CLI/env/config handling and docs
- new endpoint behavior: update handler/server docs and tests
- new Docker behavior: update `docker/docker-compose.yml`, static tests, and docs

## Working With Tool-Specific Config

The repo contains Claude-specific structure under `.claude/`. If you add Codex-facing equivalents:

- do not contradict `CLAUDE.md`
- make the Claude authority explicit
- keep the intent aligned across tools
