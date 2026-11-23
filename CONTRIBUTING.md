# Contributing

Thank you for helping improve SMA-NG. This page explains how to set up a development environment, validate a change
and submit it.

`CLAUDE.md` is the authoritative workflow document for this repository, and `AGENTS.md` is its Codex-facing
counterpart. If this page and `CLAUDE.md` ever disagree, `CLAUDE.md` wins.

## Development Setup

SMA-NG needs Python 3.12 or newer and FFmpeg. The preferred setup uses [mise](https://mise.jdx.dev/), which installs
the pinned Python version, creates `venv/` and installs the dependencies:

```bash
mise install
mise run setup:deps:dev
```

Without mise, create the virtual environment yourself:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r setup/requirements.txt -r setup/requirements-dev.txt
```

Activate the virtual environment (or use `venv/bin/python`) before running any Python command directly.

Useful tasks:

```bash
mise run test            # the test suite
mise run test:lint       # ruff linter
mise run dev:lint        # ruff linter with auto-fix
mise run test:cov        # tests with coverage; enforces the coverage gates
mise run test:shell      # ShellCheck and shell-script tests
mise run config:generate # create config/sma-ng.yml with the GPU auto-detected
mise run daemon:start    # start the daemon
mise run docs:check      # check documentation links, task references and markdownlint
```

`mise tasks` lists every task, and the [Deployment](docs/deployment.md) page documents them.

## Validation

Run the smallest meaningful check for the area you changed, and the broad suite before you open a pull request.

```bash
# Daemon changes
venv/bin/python -m pytest tests/test_daemon.py tests/test_handler.py tests/test_worker.py -q

# Media processor and metadata changes
venv/bin/python -m pytest tests/test_mediaprocessor.py tests/test_metadata.py -q

# Docker, compose and host CLI changes
venv/bin/python -m pytest tests/test_docker.py tests/test_sma_host.py -q

# Broad suite, linter and coverage
mise run test
mise run test:lint
mise run test:cov
```

The coverage gates are 90% global line coverage and a 70% floor for every production module with at least 100
statements. See [Test Coverage](docs/test-coverage.md). Do not lower the thresholds or hide logic with
`# pragma: no cover`.

Daemon tests that need a database skip themselves unless `TEST_DB_URL` points at a PostgreSQL instance.

## Pre-commit Hooks

The repository uses [pre-commit](https://pre-commit.com/) for whitespace checks, ShellCheck, markdownlint and ruff.
The configuration pins Python 3.12 and Node 22 for the Node-based hooks, and pre-commit installs them itself.

```bash
pre-commit install
mise run dev:precommit   # run every hook against all files
```

## Change Expectations

- Keep changes small and aligned with the existing code. Do not add abstractions, settings or integrations the task
  does not need.
- Add or update tests for every behavior change.
- Update the documentation in the same change when behavior that operators or users can see changes. The table in
  `CLAUDE.md` lists which document to update for each kind of change.
- Do not mix unrelated work in one commit.

When you add something new, update these places:

- A config option: `resources/config_schema.py`, then regenerate the sample with `mise run config:sample`, and update
  `docs/configuration.md`. Add a `resources/readsettings.py` projection only for legacy `settings.*` consumers.
- A daemon option: `daemon.py`, `resources/daemon/config.py`, `setup/sma-ng.yml.sample` and `docs/daemon.md`.
- An HTTP endpoint: the route tables in `resources/daemon/handler.py`, the handler tests, `docs/daemon.md` and
  `docs/openapi.yaml`.
- A mise task: an executable script in `.mise/tasks/<group>/` with a `#MISE description=` header, and a row in
  `docs/deployment.md`.
- A trigger script for a downloader or media manager: `triggers/`, `triggers/README.md` and `docs/integrations.md`.

## Documentation

`docs/` is the canonical documentation. Two other surfaces are derived from it:

- The GitHub wiki is generated from `docs/` by `mise run wiki:build`. Never edit wiki pages by hand. After a push to
  `main` that changes `docs/` or `.mise/tasks/`, `mise run wiki:publish` updates the wiki (a hook does this
  automatically for agent sessions).
- The daemon serves `docs/` live at `/docs`. A new page needs an entry in `DOC_PAGES` in
  `resources/daemon/docs_ui.py`, and a new top-level `docs/*.md` also needs an entry in `PAGES` in
  `scripts/wiki_sync.py`.

Run `mise run docs:check` before you submit. Markdown must pass markdownlint: ATX headings, fenced code blocks with a
language, blank lines around blocks, and prose lines of at most 120 characters.

## Commits and Releases

- Use conventional commit prefixes such as `feat:`, `fix:`, `docs:`, `test:`, `refactor:` and `chore:`.
- Split unrelated work into separate commits, grouped by behavior or operational outcome rather than by file type.
- Do not add AI attribution or `Co-Authored-By` lines to commits or pull requests.
- Do not create `v*` tags by hand. release-please reads the conventional commits, opens the release pull request and
  owns versions, tags and `CHANGELOG.md`.

## Agent Configuration

The `.claude/` directory is active configuration for Claude Code. Edit skills and agents only in `.claude/skills/`
and `.claude/agents/`. The Codex copies in `.agents/skills/` and `.codex/agents/` are generated; regenerate them
after any change with:

```bash
mise run dev:agents
```

A test fails when the generated copies drift. Agent behavior changes belong in the project (`CLAUDE.md`,
`AGENTS.md`, `.claude/`, `.codex/`), never in a user's global configuration.
