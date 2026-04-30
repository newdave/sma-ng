name: "Remove the .ini fallback in update.py"
description: |
  Drop the legacy INI handling from `update.py` (the boot-time
  config-patcher). What started as a narrow "remove .ini fallback"
  cleanup surfaces a larger reality: the entire file is orphaned. The
  recommended path is to delete `update.py` and its (skipped) tests
  outright. A narrower alternative — strip only the INI branches and
  rewrite the YAML branch to match the four-bucket schema — is
  documented but not preferred.

---

## Discovery Summary

### Initial Task Analysis

User asked to "remove .ini fallback" after I flagged the dead code in
`update.py` lines 33–35, 42–44, and 96–98. While preparing the PRP I
verified that the file has no live caller and that the residual YAML
branch is also broken under the current four-bucket schema. The PRP
therefore frames the cleanup as a delete rather than a partial strip.

### Evidence the file is orphaned

```text
$ grep -rn "update\.py\|python update\|SMA_FFMPEG_PATH\|SMA_FFPROBE_PATH\|SMA_RS\b" \
    --include='*.sh' --include='*.py' --include='*.md' \
    --include='*.yml' --include='Dockerfile*' .
docker/Dockerfile:285:    -name 'update.py' \      # shebang rewrite only
update.py:…                                          # itself
tests/test_update.py:…                               # skipped suite
```

No invocation. No reference in `docker-entrypoint.sh`, `triggers/`, or
any operational doc. The docker entrypoint uses different env var
names (`SMA_FFMPEG` / `SMA_FFPROBE`) and patches `sma-ng.yml` directly
with `sed` (`docker/docker-entrypoint.sh:62-83`). The Dockerfile entry
on line 285 only rewrites a shebang — it doesn't call the script.

### Evidence the YAML branch is also broken

`update.py` writes:

```python
config.setdefault("Converter", {})["ffmpeg"] = ffmpegpath
config.setdefault(section, {})["apikey"]      = apikey   # SMA_RS = "Sonarr"
```

This produces top-level `Converter:` and `Sonarr:` keys — i.e. the
**flat-shape** YAML that `ConfigLoader._reject_old_shape()` explicitly
rejects (`resources/config_loader.py`). The current four-bucket schema
expects `base.converter.ffmpeg` and `services.sonarr.<name>.apikey`.

So both branches in `update.py` write a config the loader will refuse:

- INI branch → rejected by `path.lower().endswith(".ini")` check.
- YAML branch → rejected by `_reject_old_shape()`.

### Evidence the tests do not exercise live behavior

`tests/test_update.py:16` is `pytestmark = pytest.mark.skip(...)` with
the comment "tracked as WP-5 follow-up". Every test asserts INI shape
(`configparser.ConfigParser().read(ini)` then `cfg.get("Converter", …)`).
None of them run.

### User Clarifications Received

None. Auto-mode; the scope-decision below is the proceed-on-assumption
move.

### Scope decision

**Primary recommendation: delete `update.py` and `tests/test_update.py`.**

- The file is orphaned (no caller).
- Its replacement (the entrypoint `sed` patch) already runs in production.
- Both code paths in `update.py` write configs the loader refuses; the
  file cannot have been working since the YAML migration landed.
- Deleting beats refactoring orphaned code — refactoring would
  resurrect a duplicate of work the entrypoint already does, and the
  resurrected version would need re-validating against the live
  schema.

A narrower alternative is documented in **Alternative B** below for
the case where the user wants to keep `update.py` as a manual operator
tool. The implementer must NOT pick that path silently — escalate
back to the user if there is any reason to believe the file is in
use.

## Goal

Remove the `.ini` fallback from `update.py`. Recommended: delete
`update.py` and its skipped test module entirely.

## Why

- Dead code attracts maintenance overhead and confuses readers (the
  prior PRP cleanup spent extra effort renaming a module-level
  `autoProcess` variable that exists only here).
- Both code paths produce configs the loader rejects, so any future
  user who tries to invoke it gets a confusing failure.
- The docker entrypoint already does the equivalent work with `sed`,
  using a different (currently-documented) env-var contract
  (`SMA_FFMPEG`/`SMA_FFPROBE` vs `SMA_FFMPEG_PATH`/`SMA_FFPROBE_PATH`).
  Two parallel implementations of the same job is a footgun.

## What

### Recommended (Alternative A): delete the orphan

Remove these files entirely:

- `update.py`
- `tests/test_update.py`

Adjust the Dockerfile shebang rewrite to drop the `update.py` entry
(line 285), and adjust `tests/test_docker.py:266` if it asserts
`update.py` is present.

### Success Criteria

- [ ] `update.py` and `tests/test_update.py` deleted from the repo.
- [ ] `docker/Dockerfile` no longer references `update.py`.
- [ ] `tests/test_docker.py` no longer asserts `update.py` is present.
- [ ] `pytest` passes (overall count drops by the number of skipped
      tests in `test_update.py` — 11).
- [ ] `docker/docker-entrypoint.sh` continues to patch ffmpeg paths
      (smoke check: `bash docker/docker-entrypoint.sh --dry-run` if
      that flag exists, or visual review of the relevant lines).
- [ ] `docker build .` succeeds (the `find -name update.py` no longer
      matches anything; that's still valid syntax).
- [ ] CHANGELOG / commit messages note the removal as a breaking
      change for anyone manually invoking `update.py` (none expected).
- [ ] No lingering references to `SMA_FFMPEG_PATH`, `SMA_FFPROBE_PATH`,
      or `SMA_RS` in the repo or wiki.

## All Needed Context

### Research Phase Summary

- **Codebase patterns found**: The docker entrypoint already patches
  `sma-ng.yml` via `sed` (lines 61-93). It is the live equivalent of
  what `update.py` was meant to do. The entrypoint patches
  `base.converter.ffmpeg` / `base.converter.ffprobe` correctly under
  the four-bucket schema.
- **External research needed**: No.
- **Knowledge gaps identified**: None — the call-graph search is
  conclusive.

### Documentation & References

```yaml
- file: update.py
  why: |
    The orphan being deleted. Both INI and YAML branches write configs
    the loader refuses. No live caller.

- file: tests/test_update.py
  why: |
    Skipped suite that asserts INI shape. Deleted alongside update.py.

- file: docker/docker-entrypoint.sh
  why: |
    Live replacement (lines 61-93). Uses different env var names
    (SMA_FFMPEG, SMA_FFPROBE) and writes the four-bucket schema correctly.
    Verify this is unchanged at the end of the cleanup.

- file: docker/Dockerfile
  why: |
    Line 285 includes 'update.py' in the shebang-rewrite find clause.
    Drop that line.

- file: tests/test_docker.py
  why: |
    Line 266 asserts 'update.py' appears in the Dockerfile. Drop or
    invert depending on context.

- file: resources/config_loader.py
  why: |
    Documents both rejection paths (INI extension, flat-shape YAML)
    that prove update.py's outputs are rejected today. Read
    _reject_old_shape() to understand why the YAML branch is also dead.

- file: docs/prps/config-restructure.md
  why: |
    Historical PRP for the YAML migration. Confirms the four-bucket
    schema is canonical.

- file: docs/brainstorming/2026-04-24-yaml-config-migration.md
  why: |
    Line 126 documents that the original migration plan included
    "Update update.py — branch on YAML vs INI file presence". That
    work was started (the YAML branch exists) but never completed
    (the YAML branch writes the wrong shape) and the file was orphaned
    before completion.
```

### Known Gotchas

```python
# CRITICAL: Do NOT rewrite update.py to be "correct" YAML-only. The
#   docker entrypoint already does the same job. Two parallel
#   implementations of the same patch step is exactly what you don't
#   want — they will diverge.

# CRITICAL: Per CLAUDE.md, the deletion is one logical commit:
#     remove update.py + test_update.py + Dockerfile reference + docker
#     test reference. They all support the same change.

# CRITICAL: Per CLAUDE.md, no AI attribution / Co-Authored-By in
#   commits.

# CRITICAL: Some external user docs or operator runbooks may mention
#   `python update.py`. Grep the wiki AND `docs/` AND `setup/` AND
#   `README*.md` once more before committing. Empty grep is the
#   precondition.

# CRITICAL: If the implementer finds *any* live caller (e.g. a Helm
#   chart, a systemd unit file, an external automation snippet) the
#   primary recommendation is wrong — escalate to user before
#   proceeding.
```

## Implementation Blueprint

### Tasks (Alternative A — recommended)

```yaml
Task 1 — Verify orphan status (precondition):
RUN: rg -n "update\.py|SMA_FFMPEG_PATH|SMA_FFPROBE_PATH|\bSMA_RS\b" \
       --glob '!update.py' --glob '!tests/test_update.py' \
       --glob '!CHANGELOG.md' --glob '!docs/brainstorming/**' \
       --glob '!docs/prps/**' --glob '!docs/tasks/**'
  → expected hits ONLY in:
       docker/Dockerfile (line 285, shebang rewrite)
       tests/test_docker.py (asserts update.py is in the Dockerfile)
  → any other hit means escalate; do not proceed silently.

Task 2 — Delete the file pair:
DELETE update.py
DELETE tests/test_update.py

Task 3 — Drop the Dockerfile shebang reference:
MODIFY docker/Dockerfile line ~285:
  Remove the `-name 'update.py'` line from the find clause. The
  remaining find still rewrites daemon.py / manual.py / rename.py
  shebangs.

Task 4 — Update the docker test:
MODIFY tests/test_docker.py:
  - If the assertion is "update.py is present in the find clause",
    delete it (the file no longer exists).
  - If the assertion just lists entry points, drop update.py from the
    list.
  - Keep the rest of the test intact.

Task 5 — Repo + wiki sweep:
RUN: rg -n "update\.py|SMA_FFMPEG_PATH|SMA_FFPROBE_PATH|\bSMA_RS\b" \
       --glob '!CHANGELOG.md' --glob '!docs/brainstorming/**' \
       --glob '!docs/prps/**' --glob '!docs/tasks/**'
  → expected: no hits.
RUN: rg -n "update\.py|SMA_FFMPEG_PATH|SMA_FFPROBE_PATH|SMA_RS" /tmp/sma-wiki/
  → expected: no hits. If hits, remove them and push the wiki repo
    per the CLAUDE.md three-place rule.

Task 6 — Validation:
RUN: pytest
RUN: python scripts/lint-logging.py
RUN: shellcheck docker/docker-entrypoint.sh   (regression check)
RUN: docker build -t sma-ng:test docker/      (optional, slow)

Task 7 — Commit + push:
  - One logical commit covering update.py + test_update.py +
    Dockerfile + test_docker.py.
  - Conventional prefix: `chore(deploy): drop orphaned update.py`
  - Body: explain the file was unused, both branches wrote
    loader-rejected configs, and the live entrypoint patches
    sma-ng.yml directly.
  - `git pull --rebase && git push`.
  - No AI attribution.
```

### Suggested commit message (Alternative A)

```text
chore(deploy): drop orphaned update.py

`update.py` had no live caller: the docker entrypoint patches
sma-ng.yml directly via sed (`docker/docker-entrypoint.sh:62-83`),
using a different env-var contract (`SMA_FFMPEG`/`SMA_FFPROBE`).

Both branches in update.py wrote configs the loader now rejects:
- the .ini branch is gated by `ConfigLoader.load`'s extension check
- the YAML branch wrote flat-shape YAML caught by
  `_reject_old_shape()`

The associated test module was already skipped pending a YAML-shape
rewrite. Drop the file, the skipped tests, and the Dockerfile
shebang reference.
```

---

### Alternative B — Keep update.py, strip only the INI fallback

Use this only if Task 1 above turns up an unexpected live caller.

```yaml
Task B1 — Strip INI branches in update.py:
MODIFY update.py:
  - Remove `import configparser` (line 4).
  - Remove the legacy_ini fallback (lines 32-38) entirely; replace
    with a plain "config does not exist" error if the YAML is missing.
  - Remove the `.ini` extension branch (lines 42-44).
  - Remove every `isinstance(config, configparser.ConfigParser)`
    branch (lines 52-57, 75-86, 91-94, 96-100). The dict-update
    branch becomes the only path.

Task B2 — Fix the YAML branch to match the four-bucket schema:
MODIFY update.py:
  - `config.setdefault("Converter", {})["ffmpeg"]` →
    `config.setdefault("base", {}).setdefault("converter", {})["ffmpeg"]`
    (and ffprobe).
  - `config.setdefault(section, {})["apikey"]` →
    `config.setdefault("services", {}).setdefault(section.lower(), {})`
    `.setdefault("default", {})["apikey"]`
    (and the rest of the service-section keys).
  - Verify the result loads via `ConfigLoader().load()` against a
    real `setup/sma-ng.yml.sample`.

Task B3 — Rewrite tests/test_update.py for YAML shape:
  - Remove the module-level `pytest.mark.skip`.
  - Replace `configparser.ConfigParser().read(ini)` reads with
    `yaml.safe_load(open(...))` and assertions against the
    four-bucket structure.
  - Drop the `import configparser # noqa: F401` line.

Task B4 — Validation:
  - `pytest` (now exercises the YAML branch).
  - Operator smoke: run `python update.py` against a sample yaml in
    a tmpdir and confirm `ConfigLoader().load(...)` succeeds on the
    output.

NOTE: Alternative B leaves a duplicate of the docker entrypoint's
sed-based patcher in pure Python. The duplication is the long-term
hazard; recommend collapsing on the entrypoint instead.
```

## Validation Loop

### Level 1: Pre-deletion sanity

```bash
source venv/bin/activate
rg -n "update\.py|SMA_FFMPEG_PATH|SMA_FFPROBE_PATH|\bSMA_RS\b" \
   --glob '!update.py' --glob '!tests/test_update.py' \
   --glob '!CHANGELOG.md' --glob '!docs/brainstorming/**' \
   --glob '!docs/prps/**' --glob '!docs/tasks/**'
# Expected hits: docker/Dockerfile and tests/test_docker.py only.
```

### Level 2: Post-deletion full validation

```bash
source venv/bin/activate
pytest -q
python scripts/lint-logging.py
shellcheck docker/docker-entrypoint.sh
markdownlint docs/ AGENTS.md
# Optional, slow:
docker build -t sma-ng:cleanup-test docker/
```

### Level 3: Behavioral smoke check (entrypoint still patches)

```bash
# In a scratch dir, run the entrypoint logic against a sample:
mkdir -p /tmp/sma-cleanup/{config,defaults,setup,logs}
cp setup/sma-ng.yml.sample /tmp/sma-cleanup/setup/
SMA_FFMPEG=/usr/local/bin/ffmpeg \
SMA_FFPROBE=/usr/local/bin/ffprobe \
CONFIG_DIR=/tmp/sma-cleanup/config \
SETUP_DIR=/tmp/sma-cleanup/setup \
DEFAULTS_DIR=/tmp/sma-cleanup/defaults \
bash -x docker/docker-entrypoint.sh 2>&1 | tail -30

# Expected: ffmpeg/ffprobe values stamped into base.converter.
grep -A1 'converter:' /tmp/sma-cleanup/config/sma-ng.yml | head
```

## Final validation Checklist

- [ ] `rg "update\.py" --glob '!CHANGELOG.md' --glob '!docs/brainstorming/**' --glob '!docs/prps/**' --glob '!docs/tasks/**'` → 0 hits (after Task 3+4)
- [ ] `pytest` passes (skipped count drops by 11)
- [ ] `shellcheck` clean for `docker-entrypoint.sh`
- [ ] `docker build` succeeds
- [ ] No remaining mention of `SMA_FFMPEG_PATH`, `SMA_FFPROBE_PATH`,
      `SMA_RS` outside historical docs
- [ ] Single logical commit; no AI attribution
- [ ] Wiki sweep done (per CLAUDE.md three-place rule)

---

## Anti-Patterns to Avoid

- ❌ Don't pick Alternative B silently. The narrow strip resurrects
  orphaned code and creates a duplicate of the entrypoint logic.
- ❌ Don't bundle this with unrelated cleanups. One logical commit.
- ❌ Don't add Co-Authored-By or AI attribution.
- ❌ Don't delete `docker/docker-entrypoint.sh` lines 61-93 — that's
  the live replacement, the keeper.
- ❌ Don't extend the deletion to `_validate_hwaccel` in
  `resources/daemon/server.py`. That is also dead INI-reading code,
  but it's outside this PRP's scope and worth its own focused PR.

## Task Breakdown

A companion task breakdown lives at
`docs/tasks/remove-update-py-ini-fallback.md`.

## Confidence Score

**9 / 10** for one-pass implementation success.

Why not 10: the only material risk is an unexpected live caller of
`update.py` outside this repository (e.g. an operator's Ansible role
or a custom container). Task 1 is the gate; if it surfaces such a
caller, the implementer must escalate rather than proceed. Why not
lower: the deletion itself is mechanical and reversible (one
`git revert` away), and the docker entrypoint guarantees the
ffmpeg-patch behavior is preserved.
