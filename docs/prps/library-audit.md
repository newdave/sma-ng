# Library Audit — distributed scanner that locates errors, stale extras, and TMDB/TVDB duplicates

> **STATUS: COMPLETE — landed 2026-05-16**
> Distributed library auditor shipped under `resources/library_audit/` with recycler, tag_reader, engine, enumerator, threads. See commits `32148b7`, `8f09059`, `bbc8dc4`.

name: "Library Audit — distributed scanner that locates errors, stale extras, and TMDB/TVDB duplicates"
description: |
  Add a daemon-resident library auditor that enumerates configured paths, persists findings
  in PostgreSQL, distributes per-file probe work across all live cluster nodes, and offers
  ack/dismiss/auto-fix workflows. Triggerable via CLI, REST, and a periodic thread.

## Purpose

Give operators a single source of truth for "what's wrong in my media library?" — corrupt files,
orphaned sidecars, leftover originals, and duplicate-by-id releases — without paying the cost of
a full re-conversion run. Findings are durable, ack-able, and (optionally) auto-actionable.

## Core Principles

1. Reuse `cluster_nodes` heartbeat + `FOR UPDATE SKIP LOCKED` claim semantics — same pattern as `claim_next_job`.
2. Reuse the recycle-bin path; never `os.unlink` a real file from the auditor.
3. Read-mostly by default; auto-fix is opt-in per finding kind.
4. Single-line, redaction-safe logging (`CLAUDE.md`).
5. Three-place doc updates (`docs/`, wiki mirror, web UI) per `CLAUDE.md`.

---

## Discovery Summary

### Initial Task Analysis

User asked for "media library scanning support — locate media with errors, stale extra files, duplicates, etc"
on top of SMA-NG (Python 3.12 + Postgres + FFmpeg, multi-node clusterable daemon).

### User Clarifications Received

- **Q1: errors definition?** → **1a**: files where FFprobe fails (unreadable / corrupt). Past-job failures are
  out of scope here (already in the `jobs` table).
- **Q2: stale definition?** → **2abc**: orphan sidecars, leftover `.tmp`/`.partial`/`.2.mp4` artifacts, and
  pre-conversion originals left by `delete-original: false`.
- **Q3: duplicate definition?** → **3a**: same TMDB/TVDB id at multiple paths (read from embedded MP4 tags).
- **Q4: action policy?** → **4bcd**: persist `library_findings` with ack/dismiss + auto-queue conversion
  for fixable errors + auto-clean stale sidecars (dry-run default, recycle-bin route).
- **Q5: trigger model?** → all three: `manual.py --audit`, `POST /library/audit`, scheduled thread.
- **Q6: path scope?** → new `daemon.audit_paths` (separate from `daemon.scan_paths`).
- **Q7: false-positive filter?** → skip Plex extras dirs (`Extras/`, `Featurettes/`, `Behind The Scenes/`,
  `Deleted Scenes/`, `Interviews/`, `Other/`, `Specials/Trailers/`).
- **Mid-flight clarifications:**
  - **Distributed workload** — every live node must take a share of the audit work, mirroring the
    existing job-claim fairness logic.
  - **Release version** — first commit landing this feature must release as **1.7.0**, not the next patch
    bump. Achieved via a `Release-As: 1.7.0` git-trailer on the merge commit (release-please honours it
    even with `always-bump-patch` configured).

### Missing Requirements Identified

- Concurrency: ffprobe is one fork per file; thousands of files will swamp a single node — hence
  the multi-node fan-out plus per-node concurrency cap.
- Resume: an audit can be killed mid-run; queue-based design lets a different node finish it.
- Metric reporting: surface counts on the existing `/cluster/status`-style page.

## Goal

A `library_audit` subsystem with:

- Enumeration produces a queue of per-path work units in a `library_audit_queue` PG table.
- Any live cluster node can claim units (`FOR UPDATE SKIP LOCKED`), probe, and write findings.
- Findings persist in `library_findings`, ack/dismiss/resolve via REST.
- Sidecar/duplicate detection are pure metadata operations (no ffprobe).
- Optional auto-fix: queue conversion for FFprobe-bad files; recycle-bin orphan sidecars.
- Visible from CLI (`manual.py --audit /path`) and from the web UI's docs page.

## Why

- Operators currently have no way to see corruption other than waiting for a conversion to fail.
- TMDB/TVDB duplicates accumulate when Sonarr/Radarr renames or libraries are merged; nothing
  surfaces them.
- Sidecar churn (`.srt`/`.nfo`) outlives parent media files — manual cleanup is tedious and error-prone.
- Fits SMA-NG's existing role as the "thing that knows about my media library."

## What

### User-visible behaviour

- **CLI**: `python manual.py --audit /path` prints a finding report and exits non-zero if any
  high-severity findings exist. `--audit-fix` enables the auto-fix policies declared in config.
- **REST**:
  - `POST /library/audit` — enqueues an audit run; returns `202 {"audit_id": ..., "status": "queued"}`.
  - `GET /library/audit/<id>` — status: `pending`/`enumerating`/`probing`/`completed`, plus per-node
    progress derived from `library_audit_queue.claimed_by` counts.
  - `GET /library/findings?status=open&kind=ffprobe_failed&limit=50` — paginated list.
  - `POST /library/findings/<id>/ack`, `/dismiss`, `/resolve` — workflow.
  - `GET /library/audit` — list recent audit runs.
- **Scheduled**: a daemon thread starts `LibraryAuditThread` per `daemon.audit_interval_seconds`
  (default 86400). Whichever node wins `pg_advisory_xact_lock(hashtext('library_audit_enumerate'))`
  enumerates; all nodes (including the enumerator) probe.
- **Web UI**: a new `docs/library-audit.md` rendered at `/docs/library-audit`.

### Distributed workload (CRITICAL)

- The audit run is split into two phases: **enumerate** (single-node, fast directory walk) and
  **probe** (multi-node, expensive ffprobe / mutagen reads).
- Enumerate inserts one row per file path into `library_audit_queue` with `status='pending'`,
  `audit_id=<run_id>`.
- Each node's `LibraryAuditWorkerThread` runs a tight loop:
  1. `claim_audit_units(node_id, batch=N)` — `UPDATE … SET status='claimed', claimed_by=%s, claimed_at=NOW() WHERE id IN (SELECT id FROM library_audit_queue WHERE status='pending' AND audit_id=%s ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED) RETURNING *`.
  2. For each unit, run the probe; insert/update a `library_findings` row; mark unit `done`.
  3. If no rows returned, sleep 5s and retry until run is complete.
- Stale-claim recovery: on `LibraryAuditWorkerThread` startup, requeue any units claimed by
  this node where `status='claimed'` (mirrors `_requeue_running_jobs_for_node`, db.py:228-241).
  An additional sweep clears claims older than `audit_claim_stale_seconds` (default 600s) regardless
  of node — handles the "node died mid-claim" case.
- The audit run is "completed" when zero `pending` or `claimed` rows remain for that audit_id.
  Only the enumerator transitions `library_audit_runs.status` from `probing` → `completed`.
- Workload is therefore self-balancing — fast nodes claim more units, slow/offline nodes simply
  don't claim. No explicit shard math.

### Success Criteria

- [x] `library_findings` and `library_audit_queue` / `library_audit_runs` tables created idempotently
      via `_init_db()`.
- [x] `daemon.audit_paths`, `audit_interval_seconds`, `audit_skip_dirs`, `audit_concurrency`,
      `audit_auto_fix` fields added to pydantic schema with kebab-case YAML aliases.
- [x] `LibraryAuditThread` (scheduler/enumerator) and `LibraryAuditWorkerThread` (per-node probe)
      registered in `DaemonServer.start()` and restarted on config reload (mirror existing
      `ScannerThread` / `RecycleBinCleanerThread` reload at server.py:263-283).
- [x] Two-node smoke test: a 200-file audit run with one slow node and one fast node completes
      with both nodes contributing units (`SELECT claimed_by, COUNT(*) FROM library_audit_queue
      WHERE audit_id=… GROUP BY claimed_by` shows >0 for both).
- [x] Killing a node mid-claim does not stall the run — the stale-claim sweep recovers within
      `audit_claim_stale_seconds`.
- [x] CLI `manual.py --audit /tmp/sample` runs without a daemon (single-process: enumerates and
      probes inline; does not touch the cluster tables).
- [x] Findings dedupe on `(kind, path)` with `ON CONFLICT DO UPDATE SET last_seen_at=NOW()`.
- [x] Auto-fix dry-run is the default; `audit_auto_fix.sidecars: true` plus a confirmed run actually
      moves files via the recycle-bin helper.
- [x] Auto-queued conversion jobs use `add_job` (db.py:252) and trigger `notify_workers()` exactly
      like `_queue_file` (handler.py:908-922).
- [x] All log lines pass `mise run test:lint` (single-line rule).
- [x] OpenAPI spec updated and `mise run test:openapi` passes.
- [x] Docs added in three places: `docs/library-audit.md`, wiki mirror, doc index in `docs/README.md`.
- [x] Release-please footer `Release-As: 1.7.0` present on the merge commit so the release lands
      as 1.7.0 instead of the next patch bump.

## All Needed Context

### Research Phase Summary

- **Codebase patterns found**: Yes — `ScannerThread`, `RecycleBinCleanerThread`, `HeartbeatThread`,
  `claim_next_job`, `add_job`, `_post_shutdown` async-202 idiom, `_recycle_to_bin`, `FFMpeg.probe`,
  `MP4` tag I/O.
- **External research needed**: No — every dependency (mutagen, psycopg2, plistlib, ffprobe wrapper,
  pydantic schema) is already imported and exercised.
- **Knowledge gaps identified**: Reading TMDB/TVDB ids out of MP4 tags currently has only a writer
  path (metadata.py); the PRP specifies a small reader helper.

### Documentation & References

```yaml
- file: resources/daemon/threads.py
  lines: 13-23, 26-115, 136-249, 252-373
  why: _StoppableThread base, HeartbeatThread (cluster command pattern), ScannerThread
       (per-entry interval scheduler), RecycleBinCleanerThread (hot-reload re-read pattern).
       Mirror RecycleBinCleanerThread for LibraryAuditThread (single class, internal interval,
       reads sma-ng.yml each cycle).

- file: resources/daemon/db.py
  lines: 33-61, 67-220, 228-266, 276-360, 425-443, 603-618, 1108-1130
  why: psycopg2 + ThreadedConnectionPool driver, _conn() ctx mgr, idempotent
       CREATE/ALTER DDL inside _init_db(), claim_next_job FOR-UPDATE-SKIP-LOCKED + cross-node
       fairness, _requeue_running_jobs_for_node (apply same pattern to audit queue),
       filter_unscanned/record_scanned (executemany w/ ON CONFLICT).

- file: resources/daemon/handler.py
  lines: 51-55, 71-115, 121-137, 414-420, 466-476, 567-624, 744-786, 874-922
  why: send_json_response, check_auth, _read_json_paths, query parsing, path-segment parsing,
       _post_shutdown 202+detached-thread idiom (THE async-trigger pattern), _queue_file
       (auto-queue conversion via add_job + notify_workers).

- file: resources/daemon/routes.py
  lines: 8, 28, 55-110
  why: _get_routes / _post_routes / _get_prefix_routes / _post_prefix_routes registration shape.
       Auth check at line 8 / line 28 already enforced before route lookup.

- file: resources/daemon/server.py
  lines: 87-179, 263-283, 293-353
  why: thread start order, config-reload restart pattern (stop + join(timeout=5) + reinstantiate),
       graceful shutdown order. Add LibraryAuditThread / LibraryAuditWorkerThread alongside
       ScannerThread / RecycleBinCleanerThread.

- file: resources/daemon/worker.py
  lines: 82-129, 211-214, 421-452
  why: ConversionWorker.run loop + log conventions (single-line, extra= dict).
       WorkerPool.notify / notify_one — call after queueing fixup conversion jobs.
       Note: audit work does NOT reuse ConversionWorker — it has its own worker thread.

- file: resources/mediaprocessor.py
  lines: 317-368, 492-533
  why: _recycle_to_bin (the function the auto-cleaner must call for sidecars / orphans),
       _cleanup_input (recycle-then-unlink), isValidSource (probe wrapper, but use
       FFMpeg().probe directly to avoid stream-presence checks).

- file: converter/ffmpeg.py
  lines: 666-711
  why: FFMpeg.probe call shape; returns None on unreadable. Subprocess-per-file → batch / cap.

- file: resources/metadata.py
  lines: 380-491
  why: MP4 tag write path; reveals tag keys for ID extraction. iTunMOVI plist holds tmdb/imdb/tvdb
       inside XML — parse via plistlib + regex/xml. The PRP also adds tiny TMDB/TVDB/IMDB writer
       lines so future audits can read them as native atoms.

- file: resources/config_schema.py
  lines: 25-32, 351-403
  why: alias_generator (kebab-case), populate_by_name=True, ScanPath model, DaemonConfig
       — mirror this exactly for AuditPath / DaemonConfig fields.

- file: resources/daemon/config.py
  lines: 281-300, 380-410, 562-590
  why: PathConfigManager projection of pydantic SmaConfig onto manager attrs;
       get_recycle_bin / is_recycle_bin_path (skip recycle-bin paths in stale-detection).

- file: resources/daemon/constants.py
  why: Add STATUS_OPEN, STATUS_ACKED, STATUS_DISMISSED, STATUS_RESOLVED. SECRET_KEYS lives here.

- file: manual.py
  lines: 880-1025
  why: argparse setup + main() branch points; insert --audit and --audit-fix flags.

- file: tests/test_threads.py
  lines: 17-47
  why: _make_cleaner / _make_scanner factory pattern using MagicMock. Mirror as _make_auditor.

- file: tests/conftest.py
  why: pytest fixtures + TEST_DB_URL gating. Audit DB tests must skip when unset.

- file: docs/openapi.yaml
  why: must add /library/audit, /library/audit/{id}, /library/findings, /library/findings/{id}/ack
       (and /dismiss, /resolve) endpoints. Validated by mise run test:openapi.

- file: setup/sma-ng.yml.sample
  why: regenerate via `mise run config:sample` after schema change — do NOT hand-edit.

- file: docs/daemon.md
  why: link new audit doc; per CLAUDE.md mirror to /tmp/sma-wiki/ and update docs/README.md.
```

### Current Codebase tree (relevant subset)

```bash
sma/
├── manual.py
├── daemon.py                       # thin entry; re-exports
├── converter/
│   └── ffmpeg.py                   # FFMpeg.probe()
├── resources/
│   ├── config_schema.py            # pydantic SmaConfig
│   ├── metadata.py                 # mutagen MP4 read/write
│   ├── mediaprocessor.py           # _recycle_to_bin, isValidSource
│   ├── log.py
│   └── daemon/
│       ├── constants.py            # STATUS_* + SECRET_KEYS + resolve_node_id
│       ├── config.py               # PathConfigManager
│       ├── db.py                   # PostgreSQLJobDatabase
│       ├── threads.py              # ScannerThread, RecycleBinCleanerThread, HeartbeatThread
│       ├── server.py               # DaemonServer
│       ├── handler.py              # WebhookHandler
│       ├── routes.py               # route tables
│       ├── worker.py               # ConversionWorker, WorkerPool
│       └── docs_ui.py              # markdown renderer for /docs
├── tests/
│   ├── conftest.py
│   ├── test_threads.py
│   ├── test_handler.py
│   └── test_daemon.py
├── docs/
│   ├── README.md
│   ├── daemon.md
│   ├── configuration.md
│   ├── openapi.yaml
│   └── prps/library-audit.md       # this file
└── setup/
    ├── sma-ng.yml.sample
    └── local.yml.sample
```

### Desired Codebase tree (additions)

```bash
sma/
├── resources/
│   ├── library_audit/                       # NEW package
│   │   ├── __init__.py                      # public API: AuditEngine, FindingKind, run_audit_inline
│   │   ├── kinds.py                         # FindingKind enum + skip-dir constants + sidecar exts
│   │   ├── enumerator.py                    # walk paths → (path, kind_hints) tuples
│   │   ├── probes.py                        # ffprobe_check, sidecar_orphan_check, dup_id_check
│   │   ├── tag_reader.py                    # MP4 → {"tmdbid": ..., "tvdbid": ..., "imdbid": ...}
│   │   ├── recycler.py                      # daemon-side wrapper around _recycle_to_bin for sidecars
│   │   └── engine.py                        # AuditEngine glue (enumerate, dispatch, dedupe insert)
│   └── daemon/
│       └── threads.py                       # MODIFY: add LibraryAuditThread, LibraryAuditWorkerThread
├── docs/
│   └── library-audit.md                     # NEW canonical doc
└── tests/
    ├── test_library_audit_engine.py         # NEW unit tests for probes + engine
    ├── test_library_audit_threads.py        # NEW thread tests (mocked db)
    └── test_library_audit_handler.py        # NEW handler-level tests
```

### Known Gotchas of our codebase & Library Quirks

```python
# CRITICAL: psycopg2 ThreadedConnectionPool — every cursor MUST be closed before the
# connection returns to the pool. Use `with self._conn() as conn: with conn.cursor() as cur:`
# (db.py:50-61). Never bare-acquire and forget — pool exhaustion is silent.

# CRITICAL: claim_next_job uses pg_advisory_xact_lock(hashtext(path)) to serialise per-path
# work across nodes (db.py:242-250). Audit fixup → conversion-queue path MUST go through
# add_job() so it inherits this lock. Never INSERT into jobs directly.

# CRITICAL: pydantic alias_generator (config_schema.py:25-32) auto-converts
# audit_paths ↔ audit-paths. Always declare snake_case in code, kebab-case in sample YAML.

# CRITICAL: SingleLineFormatter rejects multi-line log records (CLAUDE.md). Audit progress
# logs MUST be one line. Use percent-format + extra= dict like worker.py:211-214. Never
# json.dumps(..., indent=2) inside a log call.

# CRITICAL: `print(...)` is forbidden inside resources/daemon/. Use the module logger.
# `# noqa: log-print` is for the CLI (manual.py) only.

# GOTCHA: FFMpeg.probe forks a subprocess per call. For an audit run over thousands of
# files, batch and cap concurrency. Default audit_concurrency=2 per node — do NOT exceed
# the node's worker_count or you'll starve real conversions.

# GOTCHA: mutagen.MP4 raises MP4StreamInfoError on non-MP4 containers (mkv, avi).
# tag_reader must catch this and return {} so the file is reported as "no-mp4-tags"
# rather than crashing the worker.

# GOTCHA: iTunMOVI is a binary plist embedded as bytes inside an MP4 atom. Parse with
# plistlib.loads(tags["----:com.apple.iTunes:iTunMOVI"][0]). Old SMA writes pre-2020
# may not contain tmdb_id at all — fall back to filename guess via guessit if missing.

# GOTCHA: Plex extras dirs are case-sensitive on Linux but case-insensitive on macOS/Windows.
# Compare via path.lower().split(os.sep) ∩ skip_set, not equality.

# GOTCHA: When delete-original was off, the leftover original lives next to the new MP4
# with the SAME basename but different extension (e.g. movie.mkv + movie.mp4). The audit
# must NOT report the .mkv as stale unless the .mp4 sibling is valid (probe-passes).

# GOTCHA: release-please's always-bump-patch in this repo (CLAUDE.md) means feat: still
# yields a patch bump. To force 1.7.0 add `Release-As: 1.7.0` git trailer on the squash
# commit message — DO NOT manually create a v1.7.0 tag.

# GOTCHA: cluster_nodes table column for advertised host is `host`, not `hostname`
# (db.py around line 91). Querying live nodes for status display: SELECT node_id, host,
# last_heartbeat FROM cluster_nodes WHERE last_heartbeat > NOW() - interval '60 seconds'.
```

## Implementation Blueprint

### Data models and structure

```python
# resources/library_audit/kinds.py
from enum import Enum

class FindingKind(str, Enum):
  FFPROBE_FAILED   = "ffprobe_failed"      # converter cannot read the file
  ORPHAN_SIDECAR   = "orphan_sidecar"      # .srt/.nfo/.jpg with no matching parent
  LEFTOVER_TMP     = "leftover_tmp"        # *.tmp / *.partial / *.2.mp4
  PRECONV_ORIGINAL = "preconv_original"    # original .mkv/.avi next to a valid .mp4
  DUPLICATE_ID     = "duplicate_id"        # ≥2 paths share a tmdb/tvdb id

SIDECAR_EXTS = {".srt", ".nfo", ".jpg", ".jpeg", ".png", ".sub", ".idx", ".ass", ".ssa", ".vtt"}
TMP_EXTS     = {".tmp", ".partial"}
TMP_PATTERNS = (".2.mp4", ".3.mp4")  # collision-suffixed leftovers
PLEX_SKIP_DIRS = {"extras", "featurettes", "behind the scenes",
                  "deleted scenes", "interviews", "other", "specials", "trailers"}
```

```sql
-- DDL appended inside _init_db() (db.py before line 219)

CREATE TABLE IF NOT EXISTS library_audit_runs (
  id            BIGSERIAL PRIMARY KEY,
  status        TEXT NOT NULL,              -- queued / enumerating / probing / completed / failed
  triggered_by  TEXT,                       -- node_id or 'cli' or 'scheduled'
  scope_paths   TEXT[] NOT NULL,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at  TIMESTAMPTZ,
  total_units   INTEGER DEFAULT 0,
  done_units    INTEGER DEFAULT 0,
  error         TEXT
);

CREATE TABLE IF NOT EXISTS library_audit_queue (
  id           BIGSERIAL PRIMARY KEY,
  audit_id     BIGINT NOT NULL REFERENCES library_audit_runs(id) ON DELETE CASCADE,
  path         TEXT NOT NULL,
  kind_hint    TEXT NOT NULL,               -- which probe to run (media|sidecar|tag)
  status       TEXT NOT NULL DEFAULT 'pending',  -- pending|claimed|done|error
  claimed_by   TEXT,                        -- node_id
  claimed_at   TIMESTAMPTZ,
  finished_at  TIMESTAMPTZ,
  error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_queue_pending
  ON library_audit_queue (audit_id, status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_audit_queue_claimed
  ON library_audit_queue (status, claimed_at) WHERE status = 'claimed';

CREATE TABLE IF NOT EXISTS library_findings (
  id            BIGSERIAL PRIMARY KEY,
  kind          TEXT NOT NULL,
  path          TEXT NOT NULL,
  details       JSONB NOT NULL DEFAULT '{}'::jsonb,
  status        TEXT NOT NULL DEFAULT 'open',  -- open|acked|dismissed|resolved
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  acked_at      TIMESTAMPTZ,
  resolved_at   TIMESTAMPTZ,
  audit_id      BIGINT REFERENCES library_audit_runs(id) ON DELETE SET NULL,
  UNIQUE (kind, path)
);
CREATE INDEX IF NOT EXISTS idx_findings_status ON library_findings (status, kind);
CREATE INDEX IF NOT EXISTS idx_findings_path ON library_findings (path);
```

### Tasks (in execution order)

```yaml
Task 1 — Schema + DB methods:
MODIFY resources/daemon/db.py:
  - INJECT the three CREATE TABLE statements above inside _init_db()
    immediately before the closing `)` at line ~219
  - ADD methods (mirror existing style):
      create_audit_run(scope_paths, triggered_by) -> int
      enqueue_audit_units(audit_id, paths_with_hints)            # executemany
      claim_audit_units(node_id, audit_id, batch=50) -> list     # FOR UPDATE SKIP LOCKED
      mark_audit_unit_done(unit_id, error=None)
      release_stale_claims(stale_seconds)                        # idempotent sweep
      requeue_audit_claims_for_node(node_id)                     # mirror _requeue_running_jobs_for_node
      get_audit_run(audit_id), list_audit_runs(limit, offset)
      audit_run_progress(audit_id) -> dict
      complete_audit_run(audit_id)                               # status=completed when no pending/claimed
      upsert_finding(kind, path, details, audit_id) -> int       # ON CONFLICT (kind,path) DO UPDATE
      get_findings(status=, kind=, path=, limit=, offset=)
      set_finding_status(finding_id, status)                     # ack / dismiss / resolve
  - PRESERVE existing method signatures and connection-pool ctx mgr usage

Task 2 — Constants:
MODIFY resources/daemon/constants.py:
  - ADD: STATUS_OPEN="open", STATUS_ACKED="acked", STATUS_DISMISSED="dismissed", STATUS_RESOLVED="resolved"

Task 3 — Pydantic schema:
MODIFY resources/config_schema.py:
  - ADD AuditPath model (mirror ScanPath, lines 351-356)
  - ADD AuditAutoFix model with bool flags: ffprobe_failed, orphan_sidecar, leftover_tmp,
    preconv_original (default all False)
  - ADD AuditSettings model with: paths:list[AuditPath]=[], interval_seconds:int=86400,
    skip_dirs:list[str]=PLEX_SKIP_DIRS_DEFAULT, concurrency:int=2,
    claim_stale_seconds:int=600, batch_size:int=50, dry_run:bool=True, auto_fix:AuditAutoFix
  - INJECT into DaemonConfig (around line 402) as: audit: AuditSettings = AuditSettings()

Task 4 — PathConfigManager projection:
MODIFY resources/daemon/config.py:
  - ADD self.audit_settings = AuditSettings() default in __init__ near line 281
  - ADD projection inside _apply_smaconfig: self.audit_settings = sma_config.daemon.audit
  - ADD self.audit_paths convenience property returning self.audit_settings.paths

Task 5 — Library audit package:
CREATE resources/library_audit/__init__.py:
  - Public API: AuditEngine, FindingKind, run_audit_inline (for CLI)
CREATE resources/library_audit/kinds.py: enum + constants (see Data models)
CREATE resources/library_audit/tag_reader.py:
  - read_media_ids(path) -> dict
  - mutagen.MP4(path), catch MP4StreamInfoError → return {}
  - extract iTunMOVI plist, regex out `<key>tmdb_id</key>...`, also try
    "----:com.apple.iTunes:TMDB" / "TVDB" / "IMDB" if present (forward-compat)
CREATE resources/library_audit/probes.py:
  - ffprobe_check(path, ffmpeg_dir=None) -> dict | None
      uses FFMpeg(ffmpeg_path=…, ffprobe_path=…).probe(path); returns None when readable,
      else {"reason": "...","stderr_tail": "..."}
  - sidecar_orphan_check(path, media_files_set) -> dict | None
      returns {"parent_basename": "..."} if no matching parent
  - tmp_artifact_check(path) -> dict | None
  - preconv_original_check(path, mp4_companion, ffprobe_ok) -> dict | None
CREATE resources/library_audit/enumerator.py:
  - enumerate_paths(roots, skip_dirs, media_extensions) yields (path, kind_hint)
    where kind_hint ∈ {"media","sidecar","tmp","preconv","tag"}
  - skip-dir match is case-insensitive (path.lower() segments ∩ skip_dirs)
  - Recycle-bin paths excluded via path_config_manager.is_recycle_bin_path
CREATE resources/library_audit/recycler.py:
  - move_to_recycle_bin(path, recycle_bin_root, logger) -> str
    reuses the atomic-copy + .2/.3 collision logic from MediaProcessor._recycle_to_bin
    (extract the shared helper into this module and have MediaProcessor delegate to it)
CREATE resources/library_audit/engine.py:
  - class AuditEngine(job_db, path_config_manager, ffmpeg_dir, logger, dry_run=True)
      enumerate(audit_id, roots) → inserts queue rows
      probe_one(unit) → produces (FindingKind, details) or None
      maybe_auto_fix(finding, auto_fix_settings) → "queued"|"recycled"|"skipped"
  - run_audit_inline(roots, settings, logger) — CLI path; no DB; prints findings;
    yields (kind, path, details) for each finding

Task 6 — Distributed threads:
MODIFY resources/daemon/threads.py:
  - APPEND class LibraryAuditThread(_StoppableThread):
      runs interval-driven; each cycle:
        1. release_stale_claims(stale_seconds)
        2. acquire pg_advisory_lock(hashtext("library_audit_enumerate"))  # only one node enumerates
        3. if no `enumerating` or `probing` runs exist: create_audit_run(...) + enumerate
        4. release lock
        5. complete_audit_run(audit_id) if zero pending/claimed
      mirrors RecycleBinCleanerThread re-read pattern: pulls audit settings from
      path_config_manager every cycle so config reload Just Works
  - APPEND class LibraryAuditWorkerThread(_StoppableThread):
      on start: requeue_audit_claims_for_node(self.node_id)
      loop:
        for run_id in active runs:
          units = job_db.claim_audit_units(node_id, run_id, batch_size)
          if not units: continue
          process each unit via AuditEngine.probe_one(); job_db.mark_audit_unit_done(...)
        sleep with self._stop_event.wait(...)
      respect audit.concurrency via threading.Semaphore — never spawn more than N
      ffprobe subprocesses concurrently per node

Task 7 — Server wiring:
MODIFY resources/daemon/server.py:
  - INSTANTIATE LibraryAuditThread + LibraryAuditWorkerThread alongside ScannerThread
    in start() (around line 162-179)
  - ADD restart pattern in reload-handler (lines 263-283)
  - ADD shutdown stop+join in shutdown paths (lines 293-353)

Task 8 — REST endpoints:
MODIFY resources/daemon/routes.py:
  - REGISTER GETs: /library/findings, /library/findings/<id>, /library/audit, /library/audit/<id>
  - REGISTER POSTs: /library/audit, /library/findings/<id>/ack, /dismiss, /resolve
MODIFY resources/daemon/handler.py:
  - ADD _post_library_audit(): mirror _post_shutdown's 202+detached-thread idiom; payload
    schema {paths?: [str], dry_run?: bool}; defaults from path_config_manager.audit_settings
  - ADD _get_library_audit_run(audit_id): join library_audit_runs + audit_run_progress
  - ADD _get_library_findings(query): pagination via filter_unscanned-style param parsing
  - ADD _post_library_finding_ack/dismiss/resolve: id parsed via _parse_job_id pattern (h:567-577)

Task 9 — CLI:
MODIFY manual.py:
  - ADD argparse flags after line 920:
      parser.add_argument("--audit", action="store_true", help="...")
      parser.add_argument("--audit-fix", action="store_true", help="apply auto-fix per config")
  - INJECT branch after settings load (around line 944) before line 967:
      if args["audit"]:
        from resources.library_audit import run_audit_inline
        rc = run_audit_inline([path], settings, log)
        sys.exit(rc)
  - return code: 0 if no findings, 1 if open findings exist, 2 if engine error

Task 10 — Logging and lint compliance:
MODIFY all new modules to use:
  - log = getLogger("AUDIT") (resources.log.getLogger)
  - single-line percent-format + extra= dict (mirror worker.py:211-214)
  - log.exception("…") for traceback paths
  - Never print() inside resources/daemon/ — only inside manual.py audit branch (mark with
    # noqa: log-print on the few status lines)

Task 11 — Tests:
CREATE tests/test_library_audit_engine.py:
  - probe each FindingKind with fixture files in tmp_path
  - mock FFMpeg.probe to simulate readable / unreadable
  - sidecar orphan with vs without parent
  - duplicate id detection across two paths
CREATE tests/test_library_audit_threads.py:
  - _make_auditor / _make_audit_worker MagicMock factories (mirror tests/test_threads.py:17-47)
  - stale-claim release path
  - requeue-on-startup path
CREATE tests/test_library_audit_handler.py:
  - 202 contract on POST /library/audit
  - filter & pagination on GET /library/findings
  - ack/dismiss/resolve transitions

Task 12 — OpenAPI:
MODIFY docs/openapi.yaml:
  - ADD components.schemas.LibraryFinding, LibraryAuditRun
  - ADD all endpoints listed under "REST" with auth: X-API-Key

Task 13 — Sample config:
RUN: mise run config:sample
  - regenerates setup/sma-ng.yml.sample with the new audit block
  - DO NOT hand-edit the sample
MODIFY setup/local.yml.sample:
  - ADD a commented `# audit:` block under `# daemon:` so deploys can override

Task 14 — Documentation (three places per CLAUDE.md):
CREATE docs/library-audit.md:
  - Overview, finding kinds, configuration, REST API, CLI usage, distributed model,
    auto-fix safety semantics
MODIFY docs/README.md:
  - ADD link to new doc
MODIFY docs/daemon.md:
  - ADD section linking to library-audit.md
WIKI MIRROR:
  - Copy docs/library-audit.md to /tmp/sma-wiki/Library-Audit.md
  - From /tmp/sma-wiki: git add -A && git commit -m "docs: add library audit page" && git push origin HEAD:master

Task 15 — Release version override:
ON FINAL COMMIT:
  - Use Release-As trailer on the merge/squash commit message:
      "feat(audit): add distributed library audit subsystem

       Release-As: 1.7.0"
  - Verify by checking the next release-please PR title shows v1.7.0, not the next patch
```

### Per-task pseudocode (key bits)

```python
# Task 1 — claim_audit_units (db.py): exact SQL pattern
def claim_audit_units(self, node_id, audit_id, batch=50):
  # PATTERN: claim_next_job (db.py:276-360) — FOR UPDATE SKIP LOCKED across nodes
  with self._conn() as conn:
    with conn.cursor() as cur:
      cur.execute("""
        UPDATE library_audit_queue q
           SET status='claimed', claimed_by=%s, claimed_at=NOW()
          FROM (
                SELECT id FROM library_audit_queue
                 WHERE audit_id=%s AND status='pending'
                 ORDER BY id LIMIT %s
                 FOR UPDATE SKIP LOCKED
               ) sub
         WHERE q.id = sub.id
        RETURNING q.id, q.path, q.kind_hint
      """, (node_id, audit_id, batch))
      return cur.fetchall()
```

```python
# Task 6 — LibraryAuditThread.run (threads.py)
def run(self):
  # PATTERN: RecycleBinCleanerThread (threads.py:357-373)
  self.log.info("Library audit thread started — interval %ds" % self.interval)
  while self.running:
    try:
      self.job_db.release_stale_claims(self.stale_seconds)
      # CRITICAL: only one node enumerates at a time, advisory-lock keyed on a sentinel
      if self.job_db.try_audit_enumerate_lock():
        try:
          if not self.job_db.audit_run_in_progress():
            audit_id = self.job_db.create_audit_run(
                scope_paths=self._current_paths(),
                triggered_by="scheduled:%s" % self.node_id,
            )
            self._enumerate_into_queue(audit_id)
          self.job_db.complete_finished_audit_runs()
        finally:
          self.job_db.release_audit_enumerate_lock()
    except Exception:
      self.log.exception("Library audit cycle failed")
    self._stop_event.wait(timeout=self.interval)
```

```python
# Task 6 — LibraryAuditWorkerThread.run (threads.py)
def run(self):
  self.job_db.requeue_audit_claims_for_node(self.node_id)
  sem = threading.Semaphore(self.concurrency)
  while self.running:
    runs = self.job_db.list_active_audit_runs()
    progressed = False
    for run in runs:
      units = self.job_db.claim_audit_units(self.node_id, run["id"], batch=self.batch_size)
      if not units:
        continue
      progressed = True
      threads = []
      for u in units:
        if not self.running:
          # release without finishing — stale-claim sweep will recover
          break
        sem.acquire()
        t = threading.Thread(target=self._process_unit, args=(u, sem), daemon=True)
        t.start(); threads.append(t)
      for t in threads:
        t.join()
    if not progressed:
      self._stop_event.wait(timeout=5)

def _process_unit(self, unit, sem):
  try:
    finding = self.engine.probe_one(unit)
    if finding:
      finding_id = self.job_db.upsert_finding(
          kind=finding.kind.value, path=unit["path"],
          details=finding.details, audit_id=unit["audit_id"])
      self.engine.maybe_auto_fix(finding_id, finding)  # respects dry_run
    self.job_db.mark_audit_unit_done(unit["id"])
  except Exception as exc:
    self.job_db.mark_audit_unit_done(unit["id"], error=str(exc))
    self.log.exception("Probe failed for %s" % unit["path"])
  finally:
    sem.release()
```

```python
# Task 8 — _post_library_audit (handler.py): 202 + detached thread (mirror _post_shutdown)
def _post_library_audit(self):
  if not self._require_auth(): return
  body = self._read_body() or {}
  paths = body.get("paths") or [p.path for p in self.server.path_config_manager.audit_paths]
  audit_id = self.server.job_db.create_audit_run(
      scope_paths=paths, triggered_by="api:%s" % self.server.node_id)
  self.send_json_response(202, {"status": "queued", "audit_id": audit_id, "paths": paths})
  try: self.wfile.flush()
  except Exception: pass
  threading.Thread(
      target=self.server.run_audit_enumerate,        # new helper on DaemonServer
      args=(audit_id, paths), daemon=True).start()
```

### Integration Points

```yaml
DATABASE:
  migration: idempotent CREATE TABLE/INDEX inside _init_db() (db.py before line 219)
  index: idx_audit_queue_pending (partial), idx_audit_queue_claimed (partial), idx_findings_status, idx_findings_path
  schema: see "Data models" section

API/ROUTES:
  add to: resources/daemon/routes.py
  pattern: register in _get_routes()/_post_routes() (lines 55-103) and *_prefix_routes for path-segment ids
  middleware: existing X-API-Key auth check at routes.py:8/28 already covers /library/*

CONFIG:
  add to: resources/config_schema.py (DaemonConfig)
  pattern: kebab/snake auto-aliased; mirror ScanPath/RecycleBinSettings
  secrets: none (no new credentials)

CLI:
  add to: manual.py
  flags: --audit, --audit-fix
  branch: between argparse parse and existing path-isdir/isfile handling

THREADS:
  add to: resources/daemon/server.py start()/reload()/shutdown()
  pattern: stop+join(timeout=5)+reinstantiate (lines 263-283)

DOCS:
  add to: docs/library-audit.md, docs/README.md, docs/daemon.md, docs/openapi.yaml
  mirror: /tmp/sma-wiki/Library-Audit.md (push to wiki)
  web UI: served automatically via docs_ui.py from docs/

RELEASE:
  add to: final commit message
  pattern: "Release-As: 1.7.0" trailer (release-please honours it despite always-bump-patch)
```

## Validation Loop

### Level 1: Syntax & Style

```bash
source venv/bin/activate
mise run dev:format
mise run dev:lint
mise run test:lint        # logging single-line rule
mise run test:openapi     # spec validity
```

### Level 2: Unit tests

```bash
source venv/bin/activate
python -m pytest tests/test_library_audit_engine.py tests/test_library_audit_threads.py tests/test_library_audit_handler.py -v
```

### Level 3: Multi-node integration smoke

```bash
# Two daemons against shared Postgres; expect both node_ids to appear as claimed_by.
source venv/bin/activate
SMA_DAEMON_DB_URL="$TEST_DB_URL" python daemon.py --port 8585 --workers 2 &
SMA_DAEMON_DB_URL="$TEST_DB_URL" python daemon.py --port 8586 --workers 2 &
curl -s -X POST -H "X-API-Key: $KEY" http://localhost:8585/library/audit \
     -d '{"paths":["/tmp/sma-audit-fixture"]}' | jq
# wait for completion, then:
psql "$TEST_DB_URL" -c \
  "SELECT claimed_by, COUNT(*) FROM library_audit_queue WHERE audit_id=<id> GROUP BY claimed_by"
# Both node ids must show >0.
```

### Level 4: Failure-mode drills

```bash
# Kill node-2 mid-run; node-1 must finish the run after stale-claim sweep.
kill $NODE2_PID
sleep 700   # > audit_claim_stale_seconds default (600)
psql "$TEST_DB_URL" -c \
  "SELECT status, COUNT(*) FROM library_audit_queue WHERE audit_id=<id> GROUP BY status"
# Expect: status='done' for all rows.
```

## Final validation Checklist

- [x] `mise run test` green
- [x] `mise run test:lint` green (logging rules)
- [x] `mise run test:openapi` green
- [x] `mise run dev:lint` clean
- [x] `mise run dev:format` clean
- [x] Two-node smoke: both nodes contributed claims
- [x] Killed-node drill: run completes after stale sweep
- [x] Auto-fix dry_run default verified — no files moved when `audit_auto_fix.*: false`
- [x] CLI `python manual.py --audit /path` returns non-zero with seeded broken file
- [x] `setup/sma-ng.yml.sample` regenerated via `mise run config:sample` (not hand-edited)
- [x] `docs/library-audit.md` + `docs/README.md` updated
- [x] `/tmp/sma-wiki/Library-Audit.md` mirrored and pushed
- [x] `Release-As: 1.7.0` trailer present on the merge/squash commit
- [x] Release-please PR shows v1.7.0 (not the next patch)

---

## Anti-Patterns to Avoid

- ❌ Don't reuse `ConversionWorker` for audit work — its dispatch model is subprocess-per-job; audit
  needs in-process probes.
- ❌ Don't INSERT into `jobs` directly when auto-queueing fixups — call `add_job()` so the
  per-path advisory lock is honoured cluster-wide.
- ❌ Don't shard by hash(path) — the cluster size changes with heartbeats; queue-based claim
  is self-balancing without re-shard math.
- ❌ Don't `os.unlink` sidecars — always go through the recycle-bin helper.
- ❌ Don't run more than `audit.concurrency` ffprobe subprocesses per node; semaphore is required.
- ❌ Don't write multi-line log records — `mise run test:lint` will reject the build.
- ❌ Don't hand-edit `setup/sma-ng.yml.sample` — regenerate via mise.
- ❌ Don't add a `v1.7.0` git tag manually — let release-please own tagging.

---

## PRP Confidence Score

**Score: 8/10** — high confidence in one-pass execution. Risk concentrated in two places:

1. The two new threads (`LibraryAuditThread`, `LibraryAuditWorkerThread`) interact with the
   existing reload + shutdown lifecycle; missing a stop-and-join site in `server.py` causes
   slow shutdowns. Mitigation: explicit checklist in Task 7.
2. iTunMOVI plist parsing varies between SMA-NG versions of the writer. Mitigation: graceful
   fallback to `guessit` filename parse plus the forward-compat TMDB/TVDB/IMDB native atoms.

## Task Breakdown

A separate task-breakdown document will be generated at `docs/tasks/library-audit.md`
mirroring the 15 tasks above with Given-When-Then acceptance criteria and dependency
ordering.
