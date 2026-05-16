# Task Breakdown — Raise Test Coverage to ≥90%

> **STATUS: COMPLETE — landed 2026-05-16**
> Coverage gate enforced at 90% global / 70% per-module via `.mise/tasks/test/cov`. See commits `e68e3b6`, `3926130`, `1e0bbfa`, `8f09059`.

PRP: [docs/prps/test-coverage-90.md](../prps/test-coverage-90.md)

Each task is sized for a single PR (≤1 day's work for a developer
familiar with the suite). Tasks 1–11 add tests; Task 12 enables the
gate. Tasks 2–10 are independent and can be parallelized.

---

## T1 — Coverage configuration + exclusion list

**Effort:** S (½ day)
**Dependencies:** none
**Blocks:** T12

### Acceptance criteria

- **Given** the suite passes today,
- **When** `.coveragerc` is added with the documented `omit` and
  `exclude_lines` sections,
- **Then** `mise run test:cov` reports a baseline ≥ 87.5% (omitting
  the two hardware-bound modules) and exits 0.
- **And** `coverage.json` shows
  `resources/openvino_analyzer.py` and
  `resources/library_audit/probes.py` absent from the per-file list.

### Files

- `.coveragerc` (new)

---

## T2 — yaml_merge.py: 34% → ≥90%

**Effort:** S (½ day)
**Dependencies:** T1
**Owner:** anyone

### Acceptance criteria

- **Given** the new test file `tests/test_yaml_merge.py` (extended),
- **When** `pytest tests/test_yaml_merge.py --cov=yaml_merge` runs,
- **Then** coverage of `yaml_merge.py` is ≥ 90%.
- **And** at least one test asserts comment preservation through
  a merge round-trip (regression guard against ruamel-version drift).
- **And** at least one test asserts `--check` exits non-zero on
  drift, zero on no-drift.

### Files

- `tests/test_yaml_merge.py` (extend)
- Fixture: reuse `setup/sma-ng.yml.sample`

---

## T3 — library_audit/recycler.py: 19% → ≥90%

**Effort:** S (½ day)
**Dependencies:** T1
**Owner:** anyone

### Acceptance criteria

- **Given** a fresh `tmp_path`,
- **When** `move_to_recycle_bin()` is called for the matrix
  `(bin=None, bin=valid, src=missing, src=present, target-collides
  on .2 / .3)`,
- **Then** the function returns the documented sentinel for each
  case and never deletes the source on a failed copy.
- **And** `_next_collision_dst` is verified for the empty-dir,
  single-collision, and chain-collision cases (no infinite loop
  on saturated dirs).

### Files

- `tests/test_library_audit_recycler.py` (new)

---

## T4 — library_audit/tag_reader.py: 34% → ≥90%

**Effort:** S (½ day)
**Dependencies:** T1
**Owner:** anyone

### Acceptance criteria

- **Given** a synthesized MP4 with TMDB / TVDB / IMDB freeform
  atoms,
- **When** `read_ids()` (or equivalent) parses the file,
- **Then** all three IDs are returned with their canonical type.
- **And** missing-atom, malformed-atom, and non-MP4-input cases
  return `None` (or the documented empty result) without raising.

### Files

- `tests/test_library_audit_tag_reader.py` (new)

---

## T5 — library_audit/engine.py: 56% → ≥90%

**Effort:** M (1 day)
**Dependencies:** T1
**Owner:** anyone

### Acceptance criteria

- **Given** two `LibraryAuditEngine` instances against the same
  test DB,
- **When** both attempt to claim the same path concurrently,
- **Then** exactly one succeeds (FOR UPDATE SKIP LOCKED contract).
- **And** the audit decision tree is verified for the orphan,
  duplicate, stale-tag, and three-way-collision cases.
- **And** the recycle-vs-delete flag is honoured (recycle moves
  to bin; delete unlinks).

### Files

- `tests/test_library_audit_engine.py` (extend)

---

## T6 — manual.py: 59% → ≥85%

**Effort:** M (1 day)
**Dependencies:** T1
**Owner:** anyone

### Acceptance criteria

- **Given** a stubbed `MediaProcessor.process`,
- **When** `manual.py` is invoked with each documented argument
  combination,
- **Then** argparse routes to the right code path for:
  - `--profile <name>` overlay precedence
  - `--tmdb` / `--tvdb` / `--imdb` mutual exclusion
  - `--auto` on a directory (recursive walk)
  - unreadable input (exit code 2)
  - missing required arg (exit code 2)
  - `-cl` codec listing (exit code 0, prints list)

### Files

- `tests/test_manual.py` (extend)

---

## T7 — daemon/db.py: 54% → ≥85%

**Effort:** L (1.5 days)
**Dependencies:** T1
**Owner:** developer comfortable with psycopg / pytest fixtures

### Acceptance criteria

- **Given** `TEST_DB_URL` is set,
- **When** the live-DB tests run,
- **Then** they cover: connection retry, advisory-lock acquire/
  release, `claim_one` SKIP LOCKED contention, log-archive cursor
  advance, cluster_nodes upsert race.
- **Given** `TEST_DB_URL` is NOT set (dev laptop / CI without DB),
- **When** the mock-layer tests run,
- **Then** SQL-templating logic is exercised against a `MagicMock`
  cursor; coverage of statement-level branches still ≥ 85%.

### Files

- `tests/test_db.py` (extend or split into
  `tests/test_db_live.py` + `tests/test_db_mocked.py`)

---

## T8 — daemon/handler.py: 81% → ≥92%

**Effort:** M (1 day)
**Dependencies:** T1
**Owner:** anyone

### Acceptance criteria

- **Given** the daemon test harness,
- **When** error-path requests hit the handler,
- **Then** every documented error response is verified:
  401 (missing/invalid X-API-Key), 400 (malformed JSON),
  404 (unknown route), 405 (wrong method on a known route).
- **And** filter combinations on `/jobs?status=&node_id=&limit=`
  return the right subset for each combination.
- **And** `/admin/nodes/<host>/{drain,pause,resume}` are
  idempotent (calling twice does not error).
- **And** `/reload` while a config write is in flight does not
  corrupt state (test races a second `/reload` against the first).

### Files

- `tests/test_handler.py` (extend)

---

## T9 — mediaprocessor.py: 76% → ≥85%

**Effort:** L (2 days)
**Dependencies:** T1
**Owner:** developer with codec/transcode familiarity

### Acceptance criteria

- **Given** the `make_media_info` factory,
- **When** the new test classes (placed AFTER line 5030 in
  `tests/test_mediaprocessor.py`) run,
- **Then** the following uncovered surfaces are exercised:
  - HDR detection edges (no transfer characteristic, missing
    primaries, unsupported colorspace)
  - Audio downmix matrix (5.1 → 2.0, 7.1 → 5.1, language-
    routed downmix)
  - Subtitle extension allow-list edges (`.idx`/`.sub` pair,
    PGS in MP4 silently dropped, SRT-in-MKV passthrough)
  - `_recycle_to_bin` parity with library_audit/recycler
    (both must produce identical collision sequences)
  - Image-based subtitle burn fallback when the codec
    refuses copy

### Files

- `tests/test_mediaprocessor.py` (extend; insertions ONLY
  after the second `TestStripHwDecoderFromPreopts` block at
  line 5030)

---

## T10 — Mid-tier modules: readsettings, metadata, daemon/{config,threads}

**Effort:** M (1 day)
**Dependencies:** T1
**Owner:** anyone

### Acceptance criteria

- **Given** the modules currently sitting at 84–87% coverage,
- **When** the targeted tests are added,
- **Then** each module reaches ≥ 92% line coverage.
- **And** new tests focus on env-var precedence,
  Pydantic alias-collision warnings, and stop-event
  mid-iteration shutdown for daemon threads.

### Files

- `tests/test_readsettings.py` (extend)
- `tests/test_metadata.py` (extend)
- `tests/test_daemon.py` (extend; config + threads sections)

---

## T11 — Documentation + per-module-floor helper

**Effort:** S (½ day)
**Dependencies:** T2–T10 (so the doc reflects reality)
**Blocks:** T12

### Acceptance criteria

- **Given** Tasks 2–10 are merged,
- **When** `docs/test-coverage.md` is added,
- **Then** it documents: the policy (≥90% global, ≥80% per
  production module ≥100 statements), the exclusion list with
  reasons, and the local commands.
- **And** `scripts/check-coverage-floor.py` reads `coverage.json`
  and exits non-zero if any production module ≥100 statements is
  below 80%.
- **And** `CLAUDE.md` references the new doc under a new
  "Test Coverage" subsection.

### Files

- `docs/test-coverage.md` (new)
- `scripts/check-coverage-floor.py` (new)
- `CLAUDE.md` (extend)

---

## T12 — Enable the gate

**Effort:** S (½ day)
**Dependencies:** T11 (and T2–T10 indirectly via the ≥90% target)
**Owner:** anyone with CI access

### Acceptance criteria

- **Given** all prior tasks merged and `mise run test:cov` reports
  ≥ 90%,
- **When** `.mise/tasks/test/cov` is updated to pass
  `--cov-fail-under=${COV_FAIL_UNDER:-90}`,
- **Then** `mise run test:cov` exits 0 on a healthy main and
  exits non-zero when coverage drops below 90% (verified by
  temporarily disabling a passing test).
- **And** the CI workflow has a `coverage-gate` step that runs
  `mise run test:cov` after the existing test job and surfaces
  the failure as a PR check.
- **And** `COV_FAIL_UNDER=0 mise run test:cov` lets developers
  bypass the gate for WIP commits (documented in
  `docs/test-coverage.md`).

### Files

- `.mise/tasks/test/cov` (modify)
- `.github/workflows/ci.yml` (modify)

---

## Critical Path

```text
T1 (config)
 ├─> T2 (yaml_merge)        \
 ├─> T3 (recycler)           \
 ├─> T4 (tag_reader)          \
 ├─> T5 (audit engine)         ├─> T11 (docs + floor) ──> T12 (gate)
 ├─> T6 (manual.py)           /
 ├─> T7 (db.py)              /
 ├─> T8 (handler.py)        /
 ├─> T9 (mediaprocessor)   /
 └─> T10 (mid-tier)        /
```

Tasks T2–T10 are fully parallel after T1 lands.

## Estimate

- Critical path (T1 → T9 → T11 → T12): ~3.5 days
- Wall-clock with one developer: ~7 days
- Wall-clock with two developers parallelizing T2–T10: ~4 days
