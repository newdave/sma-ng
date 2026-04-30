# Task Breakdown — Remove update.py .ini Fallback

Companion to [docs/prps/remove-update-py-ini-fallback.md](../prps/remove-update-py-ini-fallback.md).

The PRP recommends **deleting `update.py` outright** rather than
narrowly stripping its `.ini` branches, because the file is orphaned
and both code paths it ships write configs the loader rejects today.

This breakdown covers the recommended path (Alternative A in the PRP).
If T1 surfaces an unexpected live caller, **stop and escalate** — do
not proceed to T2.

## Conventions

- All commands run from an activated venv (`source venv/bin/activate`).
- Single logical commit per `CLAUDE.md`.
- After commit: `git pull --rebase && git push`.
- No `Co-Authored-By` / AI attribution.

## Critical path

```text
T1 (orphan check) ── T2 (delete) ── T3 (Dockerfile) ──┐
                                                       ├── T5 (validate) ── T6 (commit + push)
                                T4 (test_docker.py) ──┘
```

T1 is a hard gate. T2/T3/T4 are independent edits but must land in one
commit per the "logical commit" rule.

---

## T1 — Orphan-status precondition

**Goal**: prove no live caller exists.

**Steps**

```bash
source venv/bin/activate
rg -n "update\.py|SMA_FFMPEG_PATH|SMA_FFPROBE_PATH|\bSMA_RS\b" \
   --glob '!update.py' \
   --glob '!tests/test_update.py' \
   --glob '!CHANGELOG.md' \
   --glob '!docs/brainstorming/**' \
   --glob '!docs/prps/**' \
   --glob '!docs/tasks/**'
```

**Expected hits (acceptable)**

- `docker/Dockerfile:285` — shebang rewrite clause.
- `tests/test_docker.py:266` — assertion that `update.py` is in the
  Dockerfile.

**Acceptance (Given-When-Then)**

- **Given** the grep above,
  **When** it runs,
  **Then** the only hits are the two acceptable ones listed above.
- **Given** any other hit (helm chart, Ansible, runbook, README,
  triggers/, etc.),
  **When** it surfaces,
  **Then** stop and escalate to the user — do not delete the file.

Also sweep the wiki:

```bash
rg -n "update\.py|SMA_FFMPEG_PATH|SMA_FFPROBE_PATH|SMA_RS" /tmp/sma-wiki/
```

Wiki hits are acceptable but must be cleaned up in T5.

---

## T2 — Delete the file pair

**Files**

- `update.py`
- `tests/test_update.py`

**Steps**

```bash
git rm update.py tests/test_update.py
```

**Acceptance**

- **Given** `git status`,
  **When** read,
  **Then** both files appear staged for deletion.

---

## T3 — Drop the Dockerfile shebang reference

**File**: `docker/Dockerfile` (line ~285)

**Steps**

Edit the shebang-rewrite `find` clause to remove the `update.py`
entry. Before:

```dockerfile
RUN find /app -maxdepth 1 -type f \( \
    -name 'daemon.py' -o \
    -name 'manual.py' -o \
    -name 'rename.py' -o \
    -name 'update.py' \
    \) -exec sed -i '1s|^#!/opt/sma/venv/bin/python3$|#!/venv/bin/python3|' {} +
```

After (drop the `-name 'update.py'` line, fix the trailing `-o` on the
preceding line):

```dockerfile
RUN find /app -maxdepth 1 -type f \( \
    -name 'daemon.py' -o \
    -name 'manual.py' -o \
    -name 'rename.py' \
    \) -exec sed -i '1s|^#!/opt/sma/venv/bin/python3$|#!/venv/bin/python3|' {} +
```

**Acceptance**

- **Given** `grep update.py docker/Dockerfile`,
  **When** it runs,
  **Then** zero hits.
- **Given** the shebang-rewrite block,
  **When** parsed by Docker,
  **Then** it still rewrites daemon/manual/rename shebangs correctly
  (validate via `docker build .` in T5).

---

## T4 — Adjust `tests/test_docker.py:266`

**File**: `tests/test_docker.py`

**Steps**

1. Read the assertion context (the test that mentions `update.py`).
2. If the test asserts `"update.py" in dockerfile_raw`, delete that
   single assertion.
3. If the test enumerates entry points and asserts each appears in
   the Dockerfile, drop `update.py` from the list.
4. Run `pytest tests/test_docker.py` to confirm the surrounding test
   still passes.

**Acceptance**

- **Given** `pytest tests/test_docker.py`,
  **When** it runs,
  **Then** all tests pass.
- **Given** `grep update.py tests/test_docker.py`,
  **When** it runs,
  **Then** zero hits.

---

## T5 — Repo + wiki sweep + validation

**Steps**

```bash
source venv/bin/activate

# Repo grep — should be empty
rg -n "update\.py|SMA_FFMPEG_PATH|SMA_FFPROBE_PATH|\bSMA_RS\b" \
   --glob '!CHANGELOG.md' \
   --glob '!docs/brainstorming/**' \
   --glob '!docs/prps/**' \
   --glob '!docs/tasks/**'

# Wiki sweep — should be empty
rg -n "update\.py|SMA_FFMPEG_PATH|SMA_FFPROBE_PATH|SMA_RS" /tmp/sma-wiki/

# Full test suite
pytest -q

# Logging lint
python scripts/lint-logging.py

# Shell lint (regression check on the entrypoint we did NOT touch)
shellcheck docker/docker-entrypoint.sh

# Markdown lint
markdownlint docs/ AGENTS.md
```

**Optional but recommended** (slow, ~5 min):

```bash
docker build -t sma-ng:cleanup-test docker/
```

**Behavioral smoke check** that the entrypoint still patches ffmpeg
paths after the cleanup (proves the live replacement still works):

```bash
mkdir -p /tmp/sma-cleanup/{config,defaults,setup,logs}
cp setup/sma-ng.yml.sample /tmp/sma-cleanup/setup/
SMA_FFMPEG=/usr/local/bin/ffmpeg \
SMA_FFPROBE=/usr/local/bin/ffprobe \
CONFIG_DIR=/tmp/sma-cleanup/config \
SETUP_DIR=/tmp/sma-cleanup/setup \
DEFAULTS_DIR=/tmp/sma-cleanup/defaults \
bash docker/docker-entrypoint.sh 2>&1 | tail -20
grep -A1 'converter:' /tmp/sma-cleanup/config/sma-ng.yml
```

**Acceptance**

- All four lint/test commands exit 0.
- Both grep sweeps return zero hits.
- The entrypoint smoke check stamps the ffmpeg/ffprobe values into
  `base.converter`.

If wiki had hits: clean them, then in `/tmp/sma-wiki`:

```bash
git add -A
git commit -m "docs: drop update.py references"
git push origin HEAD:master
```

---

## T6 — Single commit + push

**Steps**

```bash
git add -A      # update.py + test_update.py deletions, Dockerfile, test_docker.py
git commit -m "$(cat <<'EOF'
chore(deploy): drop orphaned update.py

`update.py` had no live caller: the docker entrypoint patches
sma-ng.yml directly via sed (`docker/docker-entrypoint.sh:62-83`),
using a different env-var contract (SMA_FFMPEG / SMA_FFPROBE).

Both branches in update.py wrote configs the loader now rejects:
- the .ini branch is gated by ConfigLoader.load's extension check
- the YAML branch wrote flat-shape YAML caught by _reject_old_shape()

The associated test module was already skipped pending a YAML-shape
rewrite. Drop the file, the skipped tests, the Dockerfile shebang
reference, and the docker test assertion.
EOF
)"
git pull --rebase
git push
```

**Acceptance**

- `git log -1 --stat` shows the four-file deletion / edit set in one
  commit.
- `git status` is clean post-push.
- No `Co-Authored-By` line.

---

## Out of scope (do not touch)

- `resources/daemon/server.py:_validate_hwaccel` — also dead INI code,
  but a separate concern; gets its own PR.
- `resources/log.py` — uses `RawConfigParser` for Python's standard
  logging config (`logging.ini`). That is a different mechanism and
  is live; leave it alone.
- The docker entrypoint's `sed` patches — these are the live
  replacement; do not touch.
- Historical migration docs.

## Escalation triggers

Stop and ask the user if any of these are true:

1. T1's grep finds a hit you can't explain.
2. The wiki has operator runbooks calling `python update.py`.
3. `docker build` fails after T3.
4. The pytest count changes by anything other than 11 skipped tests.
