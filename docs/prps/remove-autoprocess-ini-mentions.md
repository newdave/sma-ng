name: "Remove all mention of autoProcess.ini from the codebase"
description: |
  Sweep the active codebase, docs, wiki, and tests of references to the
  legacy `autoProcess.ini` config filename. The format itself was already
  removed in the config-restructure work; this PRP cleans up the residual
  textual references that survive in CLI help, docstrings, README/wiki
  prose, and test fixtures.

---

## Discovery Summary

### Initial Task Analysis

User request: "remove all mention of autoProcess.ini from codebase".

The legacy INI loader was already deleted as part of the YAML migration
(see `docs/prps/config-restructure.md`). What remains is residual text:

- CLI help strings and shell-script header comments that still describe
  `--config` flags as overriding "autoProcess.ini"
- Module docstrings that reference the old filename
- A user-facing error message in `resources/config_loader.py` that names
  `autoProcess.ini`
- Doc prose in `docs/configuration.md`, `post_process/post_process.md`,
  `triggers/README.md`
- Heavy stale content in `AGENTS.md` (predates the YAML migration)
- Test fixtures that use the literal filename `autoProcess.ini` for fake
  config paths
- One genuine functional test in `tests/test_config_loader.py` that
  validates `.ini` rejection — this **must keep** the literal string
- Log filename examples in the wiki (`autoProcess.log`, endpoints like
  `/logs/autoProcess`) — derived from the old config basename and now
  stale (active config basename is `sma-ng`, so logs are `sma-ng.log`)

### Scope decisions (auto-mode assumptions)

These were decided without asking the user, based on the codebase rules
in `CLAUDE.md` ("keep documentation in sync with code", three-place doc
update) and standard cleanup hygiene:

1. **Historical docs are preserved.** Files under
   `docs/brainstorming/`, `docs/prps/config-restructure.md`, and
   `docs/tasks/config-restructure.md` are dated historical records of
   the migration that **removed** `autoProcess.ini`. Leaving the term
   in those documents is correct — they describe the removal. **Do
   not modify these files.**
2. **Functional rejection is preserved, but the error message is
   genericized.** `resources/config_loader.py` rejects `.ini` files at
   load time. The behavior stays. The error string keeps the word
   `.ini` (so it remains diagnostic) but drops the brand-name
   "autoProcess.ini" — say "INI-format config files are no longer
   supported." The matching test in `tests/test_config_loader.py`
   updates its `match=` regex to the new wording.
3. **Bare `autoProcess` log-name references in the wiki are also
   updated.** The user said "all mention of autoProcess.ini", but the
   `/logs/autoProcess` endpoint examples and `logs/autoProcess.log`
   path mentions are factually wrong now (the log filename derives from
   the active config basename, which is `sma-ng`). Treat these as in
   scope — same logical cleanup.
4. **Tests that use `autoProcess.ini` as an arbitrary opaque path
   string** (not testing INI rejection) are updated to use neutral
   YAML paths like `sma-ng.yml`. Some of these tests also assert on a
   derived log basename (e.g. `log_name == "autoProcess"`); those
   assertions update to match the new config basename.
5. **`AGENTS.md` is rewritten in the affected sections.** It currently
   describes the project as if the INI loader were live; the active
   reality is documented in `CLAUDE.md`. Replace the stale prose with
   pointers to `CLAUDE.md` / `docs/configuration.md`.

### User Clarifications Received

None — the task is narrow and unambiguous in auto-mode. If the
implementer hits a case the scope decisions above don't cover, prefer
the conservative option: rename in tests and CLI help, leave historical
docs untouched.

## Goal

Eliminate every active reference to the literal string `autoProcess.ini`
(and the related stale `autoProcess` log-name examples in the wiki) from
the codebase, docs, wiki, and active tests, while:

- Preserving the runtime safety net that rejects `.ini` paths
- Preserving historical migration records under `docs/brainstorming/`,
  `docs/prps/config-restructure.md`, and `docs/tasks/config-restructure.md`
- Keeping `tests/test_config_loader.py` covering the `.ini` rejection
  path (with updated wording)
- Updating docs in all three required locations per `CLAUDE.md`
  (`docs/`, `/tmp/sma-wiki/`, `resources/docs.html`)

## Why

- New users reading `triggers/README.md`, `--help` output, or the wiki
  see a config filename that no longer exists, which is confusing.
- `AGENTS.md` actively contradicts `CLAUDE.md`; whichever an agent
  reads first determines whether it gets correct or stale guidance.
- The `/logs/autoProcess` examples in the wiki produce 404s for
  current installs.
- Cleanup unblocks future contributors who grep for `autoProcess` and
  currently get a flood of stale hits.

## What

A repo-wide sweep that removes the dead term while preserving
functional behavior and historical context.

### Success Criteria

- [ ] `grep -rn "autoProcess\.ini" --include='*.py' --include='*.sh' --include='*.html'`
      returns zero hits.
- [ ] `grep -rn "autoProcess\.ini"` outside `docs/brainstorming/`,
      `docs/prps/config-restructure.md`, `docs/tasks/config-restructure.md`
      returns zero hits.
- [ ] `grep -rn "autoProcess"` in `/tmp/sma-wiki/` returns zero hits
      (log-name examples updated to `sma-ng`).
- [ ] `pytest` (full suite) passes.
- [ ] `python scripts/lint-logging.py` passes (no logging regressions
      from edits).
- [ ] `markdownlint docs/ AGENTS.md /tmp/sma-wiki/` passes.
- [ ] `shellcheck` passes for any edited `.sh` files.
- [ ] `python daemon.py --help` and `python manual.py --help` and
      `python rename.py --help` show no mention of `autoProcess.ini`.
- [ ] Pointing `SMA_CONFIG` at a `.ini` file still fails fast with a
      clear, generic message that names the supported format
      (`sma-ng.yml`).
- [ ] Wiki commit pushed to `master` per `CLAUDE.md` rules.

## All Needed Context

### Research Phase Summary

- **Codebase patterns found**: Three-place documentation rule
  (`docs/`, `/tmp/sma-wiki/`, `resources/docs.html`) per `CLAUDE.md`.
  Existing pattern for rejecting old config: `resources/config_loader.py`
  raising `ConfigError`.
- **External research needed**: No.
- **Knowledge gaps identified**: None — this is mechanical cleanup.

### Documentation & References

```yaml
- file: CLAUDE.md
  why: |
    Documentation rules (three-place sync), commit-style rules
    (logical commits, no AI attribution), and the canonical
    description of current config (sma-ng.yml).

- file: resources/config_loader.py
  why: |
    Holds the `.ini`-rejection branch (line 76-77). Behavior must be
    preserved; only the user-facing error string changes.

- file: tests/test_config_loader.py
  why: |
    Lines 46-54 validate the rejection. Update the `match=` regex when
    the error string changes.

- file: docs/prps/config-restructure.md
  why: |
    Historical PRP that documents the removal of INI parsing. DO NOT
    EDIT — it is a deliberate record of completed work.

- file: docs/configuration.md
  why: |
    Lines 8-10 mention "legacy autoProcess.ini". Rewrite to drop the
    brand name while keeping the diagnostic guidance.
```

### Current Codebase grep map (the work list)

```text
ACTIVE CODE (must update):
  resources/config_loader.py:77       — error message
  rename.py:8, 86                     — module docstring + arg help
  scripts/plexmatch.py:15, 22, 173    — module docstring + arg help
  scripts/sma-scan.sh:10              — header comment
  triggers/cli/scan.sh:9              — header comment
  sma-webhook.sh:8                    — header comment

ACTIVE DOCS (three-place rule applies where mirrored):
  docs/configuration.md:8             — prose
  triggers/README.md:73               — table cell
  post_process/post_process.md:6      — prose
  AGENTS.md (multiple lines)          — heavy stale content; rewrite
                                         the affected sections
  /tmp/sma-wiki/Configuration.md:8    — mirrors docs/configuration.md
  /tmp/sma-wiki/Daemon-Mode.md:303,306,309,312,340  — log endpoints
  /tmp/sma-wiki/Troubleshooting.md:36,39,42         — log endpoints
  /tmp/sma-wiki/Home.md:861, 1270     — log path + GPU prose
  resources/docs.html                 — verify; one match in source

TESTS (rename literal config paths to neutral .yml):
  tests/test_handler.py:72, 406, 409, 497, 500, 504, 516, 519, 558,
                          562, 563, 1481, 1482
  tests/test_daemon.py:599, 618, 636, 649, 662, 692, 696, 697, 701,
                          702, 703, 707, 717, 719, 721, 726, 730,
                          731, 753, 754, 758, 759, 763, 768, 769,
                          778, 788, 789, 812, 818, 824, 830, 1024,
                          1034, 1043, 1981, 1995, 2010
  tests/test_daemon_entry.py:74, 84, 98
  tests/test_server.py:84, 93, 102, 113, 123, 133, 154, 164, 173,
                       182, 427, 752, 762, 772, 782, 797, 811
  tests/test_update.py:69
  tests/test_handler.py log_name="autoProcess" assertions update to
    match new neutral config basename (e.g. "sma-ng")

TESTS THAT KEEP THE STRING:
  tests/test_config_loader.py:46, 48, 54
    — these test the .ini rejection path. Keep the file named
      `autoProcess.ini` *or* a neutral `.ini` name; update the
      `match=` regex if the error message changes.

HISTORICAL DOCS (DO NOT EDIT):
  docs/brainstorming/2026-04-24-yaml-config-migration.md
  docs/brainstorming/2026-04-26-config-restructure.md
  docs/prps/config-restructure.md
  docs/tasks/config-restructure.md
```

### Known Gotchas

```python
# CRITICAL: ConfigLogManager.get_log_file() derives the log filename
#   from the basename of the config path. Tests that pass
#   "/cfg/autoProcess.ini" assert on "autoProcess.log". When you
#   rename the config path in those tests, you MUST update the
#   asserted log filename too. Search for `.log` near each renamed
#   path.

# CRITICAL: tests/test_handler.py:72,406,409 assert log_name ==
#   "autoProcess" because that's the stem of "/config/autoProcess.ini".
#   When you rename the path to "/config/sma-ng.yml", the asserted
#   log_name becomes "sma-ng".

# CRITICAL: Keep one negative test that proves a `.ini` path is
#   rejected (test_config_loader.py). The functional behavior stays.

# CRITICAL: Per CLAUDE.md, every active doc change is applied in
#   THREE places: docs/, /tmp/sma-wiki/, resources/docs.html.
#   Confirm whether each edit has a wiki / inline-help mirror before
#   commit.

# CRITICAL: Per CLAUDE.md, no AI attribution / Co-Authored-By in
#   commits. Break the change into logical commits (one per area:
#   loader+test, CLI help, docs, wiki, AGENTS.md, tests).

# CRITICAL: Per CLAUDE.md, run markdownlint and shellcheck on any
#   edited .md / .sh file. ATX headings, fenced code with language,
#   120-col line limit.

# CRITICAL: resources/docs.html is the inline help served at
#   http://localhost:8585/docs. Verify changes render in browser if
#   possible (auto-mode allows skipping the live render check).
```

## Implementation Blueprint

### Data models and structure

No data model changes. Pure text edits.

### Tasks (in order)

```yaml
Task 1 — Generalize the loader error message:
MODIFY resources/config_loader.py:
  - FIND: 'raise ConfigError("autoProcess.ini is no longer supported. Convert to sma-ng.yml — see docs/configuration.md.")'
  - REPLACE WITH: 'raise ConfigError("INI-format config files are no longer supported. Use sma-ng.yml — see docs/configuration.md.")'

MODIFY tests/test_config_loader.py:
  - UPDATE the two `match=` regexes (lines 48, 54) to match the new
    wording. Keep the temp filename `autoProcess.ini` so the test
    still proves a `.ini` extension is rejected (the filename is the
    input under test, not a brand-name reference).

Task 2 — CLI help / docstrings / shell headers:
MODIFY rename.py:
  - line 8 (module docstring "the correct per-directory autoProcess.ini")
    → reword to "the correct per-directory sma-ng.yml"
  - line 86 arg help: "Alternate sma-ng.yml path (disables routing)"

MODIFY scripts/plexmatch.py:
  - lines 15, 22, 173: replace "autoProcess.ini" with "sma-ng.yml"

MODIFY scripts/sma-scan.sh, triggers/cli/scan.sh, sma-webhook.sh:
  - replace "autoProcess.ini" in header comments with "sma-ng.yml"
  - run shellcheck on each edited script

Task 3 — Active docs (three-place rule):
MODIFY docs/configuration.md (lines 8-10):
  - rewrite to: "INI-format configs and flat-shape YAML are not
    supported. Pointing SMA-NG at a .ini file or a flat-shape YAML
    fails fast at startup with a pointer to this document."
MIRROR the same edit in /tmp/sma-wiki/Configuration.md.
MIRROR in resources/docs.html if the same prose appears there
  (grep first; not all docs.html sections mirror configuration.md).

MODIFY triggers/README.md:73:
  - "Override sma-ng.yml for this job"

MODIFY post_process/post_process.md:6:
  - replace with: "Set `post-process: true` under `base.converter` in
    `sma-ng.yml` to enable."

Task 4 — Update wiki log-endpoint examples:
MODIFY /tmp/sma-wiki/Daemon-Mode.md (303, 306, 309, 312, 340):
  - replace `/logs/autoProcess` with `/logs/sma-ng`

MODIFY /tmp/sma-wiki/Troubleshooting.md (36, 39, 42):
  - same: `/logs/autoProcess` → `/logs/sma-ng`

MODIFY /tmp/sma-wiki/Home.md:
  - line 861: rewrite GPU prose; reference "sma-ng.yml" not
    "autoProcess*.ini"
  - line 1270: log path table — `logs/sma-ng.log`

MIRROR any of the above that already exists in `docs/` or
  `resources/docs.html` (grep before editing — the wiki has some
  pages without a docs/ counterpart).

Task 5 — Rewrite stale sections of AGENTS.md:
MODIFY AGENTS.md (lines 191, 278, 340, 353, 359-361, 382, 495, 524):
  - The cleanest path is to delete the stale config sections
    wholesale and replace with a one-line pointer to CLAUDE.md and
    docs/configuration.md, since CLAUDE.md is now authoritative.
  - If a full rewrite is too aggressive for one PR, at minimum
    s/autoProcess.ini/sma-ng.yml/g and s/autoProcess.lq.ini/profile
    "lq" in sma-ng.yml/g, and remove the bullet list of
    config/autoProcess.*.ini files.

Task 6 — Sweep test fixtures:
MODIFY tests/test_handler.py, tests/test_daemon.py,
       tests/test_daemon_entry.py, tests/test_server.py,
       tests/test_update.py:
  - Replace literal "autoProcess.ini" with "sma-ng.yml" in path
    fixtures.
  - For tests that assert on a derived log_name / log filename,
    update the assertion to match the new basename.
  - Run pytest after each file to localize regressions.

Task 7 — Final sweep + validation:
RUN: rg -n "autoProcess\.ini" --glob '!docs/brainstorming/**' \
       --glob '!docs/prps/config-restructure.md' \
       --glob '!docs/tasks/config-restructure.md'
  → expect 0 matches.
RUN: rg -n "autoProcess" /tmp/sma-wiki/
  → expect 0 matches.
RUN: pytest
RUN: python scripts/lint-logging.py
RUN: markdownlint on edited docs
RUN: shellcheck on edited shell scripts

Task 8 — Commit + push (per CLAUDE.md):
  - Split into logical commits, suggested grouping:
      1) "refactor(config): genericize INI rejection error"
         (resources/config_loader.py + tests/test_config_loader.py)
      2) "chore(cli): drop autoProcess.ini from help text and shell headers"
         (rename.py, scripts/plexmatch.py, *.sh)
      3) "docs: replace autoProcess.ini references with sma-ng.yml"
         (docs/, post_process/, triggers/README.md, AGENTS.md,
          resources/docs.html, /tmp/sma-wiki/)
      4) "test: rename autoProcess.ini fixtures to sma-ng.yml"
         (test_handler/test_daemon/test_server/test_daemon_entry/test_update)
  - Wiki commits push to `master` of /tmp/sma-wiki.
  - After each git commit: `git pull --rebase && git push`.
  - No AI attribution / Co-Authored-By lines.
```

### Per task pseudocode

```python
# Task 1 — loader
# resources/config_loader.py:76-77
if path.lower().endswith(".ini"):
    raise ConfigError(
        "INI-format config files are no longer supported. "
        "Use sma-ng.yml — see docs/configuration.md."
    )

# tests/test_config_loader.py
with pytest.raises(ConfigError, match="INI-format config files are no longer supported"):
    ...
with pytest.raises(ConfigError, match="INI-format"):
    ...
```

```python
# Task 6 — example test fixture rename
# tests/test_handler.py:406-409 (before)
h.server.job_db.get_jobs.return_value = [
    {"id": 1, "path": "/foo.mkv", "config": "/config/autoProcess.ini"}
]
assert body["jobs"][0]["log_name"] == "autoProcess"

# (after)
h.server.job_db.get_jobs.return_value = [
    {"id": 1, "path": "/foo.mkv", "config": "/config/sma-ng.yml"}
]
assert body["jobs"][0]["log_name"] == "sma-ng"
```

### Integration Points

```yaml
DOCS:
  - canonical:   docs/configuration.md, docs/daemon.md (verify), AGENTS.md
  - wiki:        /tmp/sma-wiki/Configuration.md, Daemon-Mode.md,
                 Troubleshooting.md, Home.md
  - inline-help: resources/docs.html
  - rule:        CLAUDE.md "Documentation Rules" — all three updated
                 in the same logical commit

CONFIG (no schema changes — message-only):
  - resources/config_loader.py — error string only

TESTS:
  - keep the .ini rejection test (test_config_loader.py)
  - rename opaque-path fixtures elsewhere
```

## Validation Loop

### Level 1: Syntax & Style

```bash
source venv/bin/activate

# Repo-wide: confirm zero hits in active code
rg -n "autoProcess\.ini" \
   --glob '!docs/brainstorming/**' \
   --glob '!docs/prps/config-restructure.md' \
   --glob '!docs/tasks/config-restructure.md'
# Expected: no output

# Wiki sweep
rg -n "autoProcess" /tmp/sma-wiki/
# Expected: no output

# Logging lint (project-specific)
python scripts/lint-logging.py

# Markdown lint
markdownlint docs/ AGENTS.md /tmp/sma-wiki/

# Shell lint (only edited scripts)
shellcheck sma-webhook.sh scripts/sma-scan.sh triggers/cli/scan.sh
```

### Level 2: Tests

```bash
source venv/bin/activate
pytest
# Expected: all green. Common failures to expect:
#   - assertions on log_name / log filename derived from config path
#     basename. Update the asserted basename to match the new path.
#   - the test_config_loader.py `match=` regex — update to the new
#     error wording.
```

### Level 3: Smoke checks

```bash
source venv/bin/activate

# Help text — confirm the brand name is gone
python daemon.py --help    | grep -i autoProcess && echo FAIL || echo OK
python manual.py --help    | grep -i autoProcess && echo FAIL || echo OK
python rename.py --help    | grep -i autoProcess && echo FAIL || echo OK
python scripts/plexmatch.py --help 2>&1 | grep -i autoProcess && echo FAIL || echo OK

# Behavior preserved: .ini still rejected
python -c "
from resources.config_loader import ConfigLoader, ConfigError
import tempfile, pathlib
p = pathlib.Path(tempfile.mkdtemp()) / 'x.ini'
p.write_text('')
try:
    ConfigLoader().load(str(p))
    print('FAIL: should have raised')
except ConfigError as e:
    print('OK:', e)
"
```

## Final validation Checklist

- [ ] `rg "autoProcess\.ini" --glob '!docs/brainstorming/**' --glob '!docs/prps/config-restructure.md' --glob '!docs/tasks/config-restructure.md'` → 0 hits
- [ ] `rg "autoProcess" /tmp/sma-wiki/` → 0 hits
- [ ] `pytest` passes
- [ ] `python scripts/lint-logging.py` passes
- [ ] `markdownlint` passes on edited docs
- [ ] `shellcheck` passes on edited shell scripts
- [ ] `--help` for `daemon.py`, `manual.py`, `rename.py`, `plexmatch.py` is clean
- [ ] `.ini` rejection still raises `ConfigError` with the new message
- [ ] Three-place doc rule honored for every doc edit
- [ ] Wiki commit pushed to `origin/master` per `CLAUDE.md`
- [ ] Commits split logically; no AI attribution

---

## Anti-Patterns to Avoid

- ❌ Don't edit historical brainstorming / completed-PRP / completed-task
  docs — they record the removal that already happened.
- ❌ Don't delete the `.ini` rejection branch in
  `resources/config_loader.py`. Keep the safety net; only generalize
  the wording.
- ❌ Don't bulk-rename test paths without re-checking the asserted
  log filename / log_name — those derive from the config basename.
- ❌ Don't bundle this cleanup into one giant commit. Split per
  `CLAUDE.md` "logical commit" rules.
- ❌ Don't add Co-Authored-By or AI attribution to commits.
- ❌ Don't skip the wiki update — `CLAUDE.md` requires three-place
  doc sync.

## Task Breakdown

A companion task breakdown lives at
`docs/tasks/remove-autoprocess-ini-mentions.md`.

## Confidence Score

**8 / 10** for one-pass implementation success.

Why not higher: the test sweep is wide (5 files, ~50 sites) and a
handful of those tests assert on basenames derived from the path
string — a careless `sed` will leave assertion mismatches. The grouped
commits also require discipline. Why not lower: the change is
mechanical, the safety net is a single well-tested branch, and the
validation gates are easy to run.
