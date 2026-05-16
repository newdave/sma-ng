# Library Audit — Task Breakdown

> **STATUS: COMPLETE — landed 2026-05-16**
> Distributed library auditor shipped under `resources/library_audit/` with recycler, tag_reader, engine, enumerator, threads. See commits `32148b7`, `8f09059`, `bbc8dc4`.

**Source PRP**: [docs/prps/library-audit.md](../prps/library-audit.md)
**Target release**: `1.7.0` (via `Release-As:` trailer; release-please default is `always-bump-patch`)

This document is the actionable execution plan. See the PRP for full rationale, context, file
references, gotchas, and pseudocode. Do not duplicate that content here.

## 1. Work Breakdown Structure

Sizing key: **S** ~ <0.5 day, **M** ~ 0.5–1.5 days, **L** ~ 2+ days.

| #   | Task                                | Size | Depends on        | Notes                                                              |
| --- | ----------------------------------- | ---- | ----------------- | ------------------------------------------------------------------ |
| 1   | DB schema + methods (`db.py`)       | L    | —                 | DDL inside `_init_db()`; claim/upsert/sweep methods                |
| 2   | Status constants                    | S    | —                 | `STATUS_OPEN/ACKED/DISMISSED/RESOLVED`                             |
| 3   | Pydantic schema (`AuditSettings`)   | M    | —                 | kebab/snake aliases; new `DaemonConfig.audit`                      |
| 4   | `PathConfigManager` projection      | S    | 3                 | Surface `audit_settings` / `audit_paths`                           |
| 5   | `resources/library_audit/` package  | L    | 2                 | enumerator, probes, tag\_reader, recycler, engine                  |
| 6   | Distributed threads                 | L    | 1, 4, 5           | `LibraryAuditThread` + `LibraryAuditWorkerThread`                  |
| 7   | Server wiring (start/reload/stop)   | M    | 6                 | Mirror `ScannerThread` lifecycle in `server.py`                    |
| 8   | REST endpoints (routes + handler)   | M    | 1, 6, 7           | `POST /library/audit`, findings CRUD                               |
| 9   | CLI flags (`--audit`, `--audit-fix`)| S    | 5                 | Inline single-process path; non-zero exit on findings              |
| 10  | Logging / lint compliance pass      | S    | 5, 6, 8, 9        | Single-line, `extra=` dict, no `print()` in `daemon/`              |
| 11  | Unit tests                          | M    | 5, 6, 8           | engine / threads / handler test files                              |
| 12  | OpenAPI spec                        | S    | 8                 | New schemas + endpoints; `mise run test:openapi`                   |
| 13  | Sample config regen                 | S    | 3                 | `mise run config:sample`; do not hand-edit                         |
| 14  | Docs (3 places + wiki mirror)       | M    | 8, 9, 12          | `docs/library-audit.md`, `docs/README.md`, `docs/daemon.md`, wiki  |
| 15  | Release-As trailer + verify         | S    | all               | Trailer on merge commit; confirm release-please PR shows `v1.7.0`  |

Critical-path nodes are bolded in the diagram below.

```text
[1]──┐
     ├──►[5]──►[6]──►[7]──►[8]──►[11]
[2]──┤                          │
[3]──►[4]──┘                    ├──►[12]──►[14]──►[15]
                                └──►[9]
[3]──►[13]
```

## 2. Per-Task Acceptance Criteria (Given-When-Then)

### Task 1 — DB schema + methods

- **Given** a fresh Postgres with no SMA tables
  **When** the daemon starts and `_init_db()` runs
  **Then** `library_audit_runs`, `library_audit_queue`, `library_findings` and their indexes exist,
  and a re-run is a no-op.
- **Given** two nodes calling `claim_audit_units(node_id, audit_id, batch=50)` simultaneously
  **When** there are 50 pending units
  **Then** the union of returned rows is exactly 50 with no overlap (FOR UPDATE SKIP LOCKED).
- **Given** a unit `claimed` for longer than `stale_seconds`
  **When** `release_stale_claims()` runs
  **Then** the unit is back to `pending` with `claimed_by` / `claimed_at` cleared.
- **Given** an existing finding `(kind, path)`
  **When** `upsert_finding()` is called for the same key
  **Then** `last_seen_at` updates, status is preserved, and no duplicate row is inserted.

### Task 2 — Constants

- **Given** new finding-status values
  **When** any caller imports them from `resources.daemon.constants`
  **Then** all four `STATUS_*` symbols resolve and equal the documented strings.

### Task 3 — Pydantic schema

- **Given** a YAML doc using `audit-paths:` (kebab-case)
  **When** `SmaConfig` validates it
  **Then** the field is accessible as `daemon.audit.paths` (snake_case) without error.
- **Given** a YAML omitting the `audit:` block
  **When** validation runs
  **Then** defaults apply (`interval_seconds=86400`, `concurrency=2`, `dry_run=true`,
  `auto_fix.*=false`).

### Task 4 — `PathConfigManager` projection

- **Given** a reload of `sma-ng.yml` with new `audit-paths`
  **When** `PathConfigManager._apply_smaconfig` runs
  **Then** `manager.audit_paths` reflects the new list within the same call (no restart needed).

### Task 5 — `resources/library_audit/` package

- **Given** a corrupt media file
  **When** `probes.ffprobe_check(path)` runs
  **Then** it returns a dict with `reason` and a non-empty `stderr_tail`.
- **Given** a `.srt` with no matching media basename
  **When** `sidecar_orphan_check()` runs
  **Then** it returns `{"parent_basename": ...}`.
- **Given** an `.mkv` adjacent to a probe-passing `.mp4` of the same basename
  **When** `preconv_original_check()` runs
  **Then** the `.mkv` is reported `PRECONV_ORIGINAL`; if the `.mp4` fails probe it is **not** reported.
- **Given** a Plex `Extras/` subdirectory under a root
  **When** `enumerator.enumerate_paths()` walks it
  **Then** no entries from that subtree are yielded (case-insensitive segment match).
- **Given** a non-MP4 container
  **When** `tag_reader.read_media_ids()` runs
  **Then** `MP4StreamInfoError` is caught and `{}` is returned.

### Task 6 — Distributed threads

- **Given** two daemons sharing a Postgres
  **When** the audit thread fires on both
  **Then** exactly one node holds `pg_advisory_xact_lock(hashtext('library_audit_enumerate'))`
  and only that node enumerates.
- **Given** a `LibraryAuditWorkerThread` starting up after a crash
  **When** the constructor runs
  **Then** `requeue_audit_claims_for_node(node_id)` is called before the loop begins.
- **Given** `audit.concurrency=2`
  **When** 10 units are claimed in one batch
  **Then** at most 2 ffprobe subprocesses run concurrently per node (semaphore-bounded).
- **Given** `self.running` becomes `False` mid-batch
  **When** the worker observes the stop event
  **Then** unfinished claims are released by the stale-claim sweep within `claim_stale_seconds`
  and the run still completes.

### Task 7 — Server wiring

- **Given** a SIGHUP / config-reload event
  **When** `DaemonServer` processes it
  **Then** both audit threads are stopped, joined (timeout=5s), and reinstantiated, mirroring
  `ScannerThread` reload behaviour.
- **Given** SIGTERM
  **When** the daemon shuts down
  **Then** both audit threads exit within 5s without leaving claimed units in `claimed` state.

### Task 8 — REST endpoints

- **Given** a valid `X-API-Key`
  **When** `POST /library/audit` is sent with `{"paths": [...]}`
  **Then** the response is `202` with body `{audit_id, status:"queued", paths}` and the response
  is flushed before enumeration starts (mirrors `_post_shutdown`).
- **Given** an audit run in progress
  **When** `GET /library/audit/<id>` is queried
  **Then** the response includes `total_units`, `done_units`, and per-`claimed_by` counts.
- **Given** an `open` finding
  **When** `POST /library/findings/<id>/ack` is called
  **Then** status transitions to `acked`, `acked_at` is set, and a second ack is idempotent.
- **Given** a missing `X-API-Key`
  **When** any `/library/*` route is hit
  **Then** `401` is returned by the existing auth middleware.

### Task 9 — CLI

- **Given** `python manual.py --audit /path` against a directory with one corrupt file
  **When** the command completes
  **Then** stdout lists the finding and exit code is `1` (or `2` on engine error, `0` on clean run).
- **Given** `--audit` without `--audit-fix`
  **When** sidecar orphans are detected
  **Then** no files are moved (dry-run by default).

### Task 10 — Logging / lint

- **Given** all new modules
  **When** `mise run test:lint` runs
  **Then** no multi-line records, no `json.dumps(..., indent=...)` inside log calls, and no
  `print()` calls inside `resources/daemon/`.

### Task 11 — Tests

- **Given** the three new test files
  **When** `pytest tests/test_library_audit_*.py -v` runs
  **Then** all tests pass; DB-touching tests skip cleanly when `TEST_DB_URL` is unset.

### Task 12 — OpenAPI

- **Given** the new endpoints
  **When** `mise run test:openapi` runs
  **Then** the spec validates and includes `LibraryFinding` / `LibraryAuditRun` schemas with
  `X-API-Key` security on every `/library/*` operation.

### Task 13 — Sample config

- **Given** schema changes from Task 3
  **When** `mise run config:sample` is run
  **Then** `setup/sma-ng.yml.sample` contains a kebab-case `audit:` block under `daemon:` and the
  diff shows no hand edits elsewhere.

### Task 14 — Docs

- **Given** the feature is complete
  **When** docs are reviewed
  **Then** `docs/library-audit.md` exists, `docs/README.md` and `docs/daemon.md` link it,
  `/tmp/sma-wiki/Library-Audit.md` is mirrored and pushed, and `/docs/library-audit` renders in the
  daemon's web UI.

### Task 15 — Release-As trailer

- **Given** all prior tasks merged on a single PR
  **When** the merge/squash commit is created
  **Then** the commit message body contains `Release-As: 1.7.0` on its own line.
- **Given** the release-please workflow runs after merge
  **When** the resulting release PR opens
  **Then** its title shows `v1.7.0` (not the next `1.6.x` patch) and no manual `v1.7.0` tag exists.

## 3. Critical Path (gates Validation Level 3 smoke test)

The two-node smoke test in PRP "Validation Level 3" requires a daemon that can accept
`POST /library/audit`, enumerate, distribute claims, and probe across nodes. The minimum gating set:

**1 → 3 → 4 → 5 → 6 → 7 → 8** (with **2** required by 5, **10** required to keep the lint gate
green). Tasks 9, 11, 12, 13, 14, 15 are **not** on the smoke-test critical path but are on the
release critical path.

Bottlenecks: Task 1 (DB) blocks 6 and 8; Task 6 (threads) blocks 7 and 8. Front-load 1, 3, 5 in
parallel where possible.

## 4. Pull-Request / Commit Grouping

Per `CLAUDE.md`: feature + its tests + its docs + its config belong in the same logical commit.
Suggested execution-order grouping (one PR, 6 commits):

1. **`feat(audit): add db schema and status constants`** — Tasks **1, 2**.
   DDL, claim/upsert/sweep methods, `STATUS_*` constants. No behaviour wired up yet.

2. **`feat(audit): config schema and sample regeneration`** — Tasks **3, 4, 13**.
   `AuditSettings` model, `PathConfigManager` projection, regenerated `sma-ng.yml.sample`,
   `local.yml.sample` stanza.

3. **`feat(audit): library_audit package with engine, probes, tag reader`** — Tasks **5, 9**, plus
   the engine slice of **11** (`test_library_audit_engine.py`) and **10** logging compliance for
   these modules. Includes CLI `--audit` / `--audit-fix` since they consume `run_audit_inline`.

4. **`feat(audit): distributed threads and server lifecycle wiring`** — Tasks **6, 7**, plus the
   thread slice of **11** (`test_library_audit_threads.py`) and **10** for these modules.

5. **`feat(audit): REST endpoints and OpenAPI schema`** — Tasks **8, 12**, plus the handler slice
   of **11** (`test_library_audit_handler.py`) and **10** for the handler.

6. **`docs(audit): library audit documentation and wiki mirror`** — Task **14**.
   `docs/library-audit.md`, `docs/README.md`, `docs/daemon.md` link, wiki push.
   Append `Release-As: 1.7.0` trailer on the merge/squash commit (Task **15**).

If reviewers prefer fewer commits, collapse 3+4 into a single "engine + threads" commit (still one
logical change: probe orchestration). Do **not** collapse docs or config-sample regen into unrelated
commits.

## 5. Final Release Verification (Task 15)

After merge:

```bash
git log -1 --format=%B origin/main | grep -E '^Release-As: 1\.7\.0$'
gh pr list --repo "$REPO" --search 'in:title release-please' --state open --json title,number
```

Expected:

- The merge commit message contains a `Release-As: 1.7.0` line (no leading whitespace, on its own
  line in the trailer block).
- The open release-please PR title is `chore(main): release sma-ng 1.7.0`.
- No manually created `v1.7.0` tag exists (`git tag -l v1.7.0` returns empty until release-please
  publishes it).

If the PR shows the next `1.6.x` patch instead, the trailer was missing or malformed — amend via a
follow-up empty commit:

```bash
git commit --allow-empty -m "chore: trigger 1.7.0 release" -m "Release-As: 1.7.0"
git push
```

## 6. Implementation Recommendations

- **Team shape**: one backend engineer end-to-end is sufficient; if parallelised, split as
  (a) DB+schema+config (Tasks 1-4, 13) and (b) package+threads+handler (Tasks 5-8) with a sync
  point before Task 11.
- **Parallelisable**: Tasks 1, 3, and the kinds/probes/tag\_reader sub-modules of 5 have no
  inter-dependencies and can land in any order. Task 13 (sample regen) can run as soon as 3 lands.
- **Sequencing risk**: Task 6 must not begin before Task 1's claim/sweep methods exist — the
  thread is otherwise un-testable. Task 7 must not begin before Task 6 is importable, or
  `server.py` will fail to start.
- **Validation cadence**: run `mise run test:lint` after every commit in the series — the
  single-line logging rule is the most common late-stage rejection.
