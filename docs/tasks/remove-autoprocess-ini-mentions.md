# Task Breakdown — Remove autoProcess.ini Mentions

Companion to [docs/prps/remove-autoprocess-ini-mentions.md](../prps/remove-autoprocess-ini-mentions.md).

Mechanical sweep of residual `autoProcess.ini` references in active
code, docs, wiki, and tests. Historical migration records under
`docs/brainstorming/`, `docs/prps/config-restructure.md`, and
`docs/tasks/config-restructure.md` are intentionally **not** modified.

## Conventions

- All Python work runs from an activated venv (`source venv/bin/activate`).
- Each numbered task maps to one logical commit per `CLAUDE.md`.
- After each commit: `git pull --rebase && git push`.
- No AI attribution / `Co-Authored-By` lines in commits.

## Critical path

```text
T1 (loader) ─┐
             ├── T7 (validate) ── T8 (commit + push)
T2 (CLI)  ───┤
T3 (docs) ───┤   T2..T6 are independent and can be done in parallel
T4 (wiki) ───┤   if you are willing to manage merges; serial is safer.
T5 (AGENTS) ─┤
T6 (tests) ──┘
```

T1 is the only one with a behavioral coupling (error string ↔ test
regex). Everything else is text-only.

---

## T1 — Genericize the loader rejection message

**Files**

- `resources/config_loader.py` (line 77)
- `tests/test_config_loader.py` (lines 46–54)

**Steps**

1. Update the `ConfigError` message in `resources/config_loader.py:77`
   to drop the brand name. New wording:
   `"INI-format config files are no longer supported. Use sma-ng.yml — see docs/configuration.md."`
2. Update the two `pytest.raises(..., match=...)` regexes in
   `tests/test_config_loader.py` to match the new wording. Keep the
   temp filename `autoProcess.ini` (or use any `.ini` name) — the
   filename is the input under test, not a brand reference.
3. Run `pytest tests/test_config_loader.py`.

**Acceptance (Given-When-Then)**

- **Given** a `.ini` path is passed to `ConfigLoader.load`,
  **When** the loader executes,
  **Then** it raises `ConfigError` whose message matches the new
  generic wording and contains "sma-ng.yml".
- **Given** the test suite,
  **When** `pytest tests/test_config_loader.py` runs,
  **Then** all tests pass.

**Suggested commit**: `refactor(config): genericize INI rejection error`

---

## T2 — CLI help text and shell script headers

**Files**

- `rename.py` (lines 8, 86)
- `scripts/plexmatch.py` (lines 15, 22, 173)
- `scripts/sma-scan.sh` (line 10)
- `triggers/cli/scan.sh` (line 9)
- `sma-webhook.sh` (line 8)

**Steps**

1. Replace each `autoProcess.ini` with `sma-ng.yml` in module
   docstrings, `argparse` `help=` strings, and shell `#` headers.
2. `shellcheck sma-webhook.sh scripts/sma-scan.sh triggers/cli/scan.sh`.
3. Spot-check: `python rename.py --help`, `python scripts/plexmatch.py --help`.

**Acceptance**

- **Given** `--help` for any of the four CLI tools,
  **When** the help is printed,
  **Then** "autoProcess.ini" does not appear.
- **Given** `shellcheck` runs on the three edited `.sh` files,
  **When** it finishes,
  **Then** there are zero warnings or errors.

**Suggested commit**: `chore(cli): drop autoProcess.ini from help text and shell headers`

---

## T3 — Active in-repo docs

**Files**

- `docs/configuration.md` (lines 8–10)
- `triggers/README.md` (line 73)
- `post_process/post_process.md` (line 6)
- `resources/docs.html` (grep for any match; mirror the docs/ rewording)

**Steps**

1. Rewrite `docs/configuration.md:8-10` to drop the brand name while
   keeping the diagnostic guidance (suggested wording in the PRP).
2. Update the `triggers/README.md` table cell and the `post_process`
   doc preamble.
3. `grep -n autoProcess resources/docs.html` and mirror any wording
   changes there.
4. Per `CLAUDE.md`, mirror `docs/configuration.md` edits to
   `/tmp/sma-wiki/Configuration.md` (covered in T4).
5. `markdownlint docs/configuration.md triggers/README.md post_process/post_process.md`.

**Acceptance**

- **Given** the four files above,
  **When** grep'd for `autoProcess`,
  **Then** zero hits remain.
- **Given** `markdownlint`,
  **When** it runs against the edited files,
  **Then** zero warnings or errors.

**Suggested commit**: `docs: drop autoProcess.ini references from active docs`

---

## T4 — Wiki sweep (and the bare `autoProcess` log refs)

**Files** (all in `/tmp/sma-wiki/`)

- `Configuration.md:8` (mirror of T3)
- `Daemon-Mode.md:303, 306, 309, 312, 340` (`/logs/autoProcess` →
  `/logs/sma-ng`)
- `Troubleshooting.md:36, 39, 42` (same)
- `Home.md:861, 1270` (GPU prose + log path table)

**Steps**

1. Apply the wording change from T3 to `Configuration.md`.
2. Replace `/logs/autoProcess` examples with `/logs/sma-ng` in
   `Daemon-Mode.md` and `Troubleshooting.md`.
3. Update `Home.md`:
   - Line 861: rewrite GPU prose to reference `sma-ng.yml` instead of
     `autoProcess*.ini`.
   - Line 1270: log path becomes `logs/sma-ng.log`.
4. `markdownlint /tmp/sma-wiki/`.
5. `cd /tmp/sma-wiki && git add -A && git commit -m "docs: drop autoProcess references" && git push origin HEAD:master`.

**Acceptance**

- **Given** `rg autoProcess /tmp/sma-wiki/`,
  **When** it runs,
  **Then** zero hits.
- **Given** wiki HEAD on `master` after push,
  **When** the user views the wiki on GitHub,
  **Then** the same content is live.

**Suggested commit (wiki repo)**: `docs: drop autoProcess references`

---

## T5 — Rewrite stale sections of AGENTS.md

**Files**

- `AGENTS.md` (lines 191, 278, 340, 353, 359–361, 382, 495, 524)

**Steps**

1. Recommended: replace the four affected config sections with a
   one-line pointer to `CLAUDE.md` and `docs/configuration.md`.
2. Minimum: rewrite each line to use `sma-ng.yml`, profile names
   (`profile: lq`), and remove the obsolete `config/autoProcess.*.ini`
   bullet list.
3. `markdownlint AGENTS.md`.

**Acceptance**

- **Given** `grep autoProcess AGENTS.md`,
  **When** it runs,
  **Then** zero hits.
- **Given** `markdownlint AGENTS.md`,
  **When** it runs,
  **Then** zero warnings or errors.

**Suggested commit**: `docs(agents): refresh AGENTS.md to reflect sma-ng.yml`

---

## T6 — Test fixture rename

**Files**

- `tests/test_handler.py`
- `tests/test_daemon.py`
- `tests/test_daemon_entry.py`
- `tests/test_server.py`
- `tests/test_update.py`

**Steps**

1. Replace literal `"autoProcess.ini"` in path strings with
   `"sma-ng.yml"` (or another neutral basename consistent across the
   file).
2. Update assertions that depend on the derived log basename:
   - `test_handler.py` `log_name == "autoProcess"` → `log_name == "sma-ng"` (or whatever you picked).
   - Similar `.log` filename assertions in `test_daemon.py` and
     `test_handler.py`.
3. `pytest -x` after each file to localize regressions.
4. **Do not** modify `tests/test_config_loader.py` (handled in T1).

**Acceptance**

- **Given** the full test suite,
  **When** `pytest` runs,
  **Then** all tests pass.
- **Given** `grep "autoProcess\.ini" tests/`,
  **When** it runs,
  **Then** the only remaining hits are inside
  `tests/test_config_loader.py` (input under test).

**Suggested commit**: `test: rename autoProcess.ini fixtures to sma-ng.yml`

---

## T7 — Final repo-wide validation

**Steps**

```bash
source venv/bin/activate
rg -n "autoProcess\.ini" \
   --glob '!docs/brainstorming/**' \
   --glob '!docs/prps/config-restructure.md' \
   --glob '!docs/tasks/config-restructure.md' \
   --glob '!tests/test_config_loader.py'
# expected: no output

rg -n "autoProcess" /tmp/sma-wiki/
# expected: no output

pytest
python scripts/lint-logging.py
markdownlint docs/ AGENTS.md /tmp/sma-wiki/
shellcheck sma-webhook.sh scripts/sma-scan.sh triggers/cli/scan.sh
```

**Acceptance**

- All four greps / lints / pytest produce clean output.

---

## T8 — Commit, rebase, push

**Steps**

1. Verify `git status` shows changes grouped sensibly per the
   suggested commit messages in T1–T6.
2. Make each commit individually (no umbrella commit), with a
   conventional-commit prefix and informative body.
3. After each commit: `git pull --rebase && git push`.
4. Push the wiki repo (`/tmp/sma-wiki`) separately to `master`.
5. **No** `Co-Authored-By` or AI attribution lines.

**Acceptance**

- `git log --oneline` shows the cleanup as a series of small,
  topically-grouped commits.
- `git status` is clean post-push.

---

## Out of scope (do not touch)

- `docs/brainstorming/2026-04-24-yaml-config-migration.md`
- `docs/brainstorming/2026-04-26-config-restructure.md`
- `docs/prps/config-restructure.md`
- `docs/tasks/config-restructure.md`

These are dated migration records and must remain as written.
