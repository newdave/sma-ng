# Library Audit

The library auditor locates problems in your media library that scheduled
conversion runs cannot surface on their own — corrupt files, orphan sidecars,
leftover transcoder artifacts, leftover pre-conversion originals, and
TMDB/TVDB-id duplicates. Findings are persisted in PostgreSQL with an
ack/dismiss/resolve workflow. The probe workload is distributed across every
live cluster node.

## Finding kinds

| Kind                | Trigger                                                                      |
| ------------------- | ---------------------------------------------------------------------------- |
| `ffprobe_failed`    | FFprobe cannot read the file (corrupt, truncated, empty, or zero-byte).      |
| `orphan_sidecar`    | A `.srt`/`.nfo`/`.jpg`/`.sub`/`.idx`/`.ass`/`.ssa`/`.vtt` with no matching parent media file in the same directory. |
| `leftover_tmp`      | A `.tmp`/`.partial` file or a recycle-bin collision artifact (`*.2.mp4`, `*.3.mp4`, …). |
| `preconv_original`  | A non-MP4 (`.mkv`/`.avi`/etc.) sitting next to a same-stem `.mp4` that probes cleanly — the original was never deleted (`delete-original: false`). |
| `duplicate_id`      | The same TMDB/TVDB id appears at two or more paths within the same audit run. |
| `hybrid_aspect`     | An MP4 whose picture was stretched by the former QSV alignment bug (e.g. a 1080p source shipped as 1920x1088, or a max-width downscale as 1920x1072) and that carries no corrective display aspect ratio. Detection matches a fixed signature table (1920x1088/1072/1056, 3840x2176/2144, 1280x736/704) with a square pixel aspect, so already-repaired or genuinely anamorphic files are never flagged. |

## Configuration (`sma-ng.yml`)

```yaml
daemon:
  audit:
    enabled: false              # master switch
    paths:                      # list of root directories to walk
      - path: /media/movies
        enabled: true
      - path: /media/tv
        enabled: true
    interval-seconds: 86400     # scheduled cycle (24h default)
    skip-dirs:                  # case-insensitive directory basenames to skip
                                # (an explicit list REPLACES these defaults —
                                # keep entries like Plex Versions when editing)
      - Extras
      - Featurettes
      - Behind The Scenes
      - Deleted Scenes
      - Interviews
      - Other
      - Plex Versions
      - Specials
      - Trailers
    concurrency: 2              # max ffprobe subprocesses per node
    batch-size: 50              # queue rows claimed per database round-trip
    claim-stale-seconds: 600    # release claims orphaned by killed nodes
    dry-run: true               # auto-fix is a no-op when true
    auto-fix:
      ffprobe-failed: false     # auto-queue conversion for unreadable files
      orphan-sidecar: false     # recycle orphaned subtitle/nfo/cover files
      leftover-tmp: false       # recycle leftover .tmp / .partial files
      preconv-original: false   # recycle pre-conversion originals once mp4 is good
      hybrid-aspect: false      # losslessly remux stretched outputs with a corrective display aspect
```

`paths` is a separate list from `daemon.scan-paths`; the auditor and the
conversion scanner can target different roots.

## Distributed workload

The auditor runs in two phases:

1. **Enumerate** — exactly one node at a time. The cluster acquires a
   PostgreSQL session-scoped advisory lock; the winner walks the configured
   paths and inserts one row per file into `library_audit_queue` with
   `status='pending'`. Other nodes skip enumeration on this cycle.
2. **Probe** — every live node runs a `LibraryAuditWorkerThread` that claims
   units in batches via `UPDATE … FOR UPDATE SKIP LOCKED` and writes findings.
   Faster nodes claim more units. There is no shard math — the queue
   self-balances as nodes join, leave, or slow down.

Recovery from node failure mid-run:

- Stale-claim sweep: claims older than `claim-stale-seconds` are reset to
  `pending` and become eligible for re-claim by any node.
- Startup sweep: when a worker thread starts, it requeues every claim that
  matches its own `node_id` from a previous process.

The run is marked `completed` automatically when zero `pending` or `claimed`
units remain (only the enumerator transitions runs). Duplicate-id findings
are written during the same transition by aggregating recorded media-ids
across paths.

## Triggers

### CLI

```bash
python manual.py --audit -i /path/to/library
```

Walks the path in-process, prints one line per finding, and exits non-zero
when any finding is produced that was not fixed in place. The CLI does not
touch the cluster tables — useful for ad-hoc one-shot inspections.

Two fix flags apply findings as the audit walks:

```bash
# Remux hybrid-aspect findings losslessly as they are found, and move any
# media that fails ffprobe into the configured recycle-bin.
python manual.py --audit --repair-hybrid --recycle-failed -i /path/to/library
```

- `--repair-hybrid` (remux mode only; `reencode` is rejected with `--audit`)
  stamps the corrective display aspect ratio into each hybrid finding.
- `--recycle-failed` moves ffprobe-failed media to the `recycle-bin`
  configured in `sma-ng.yml` (atomic copy + unlink — never a plain delete).
  The flag errors at startup when no recycle-bin is configured.

Fixed findings print an `action=` column (`repaired`, `recycled`,
`repair_failed`, `recycle_failed`) and no longer count toward the non-zero
exit code: the audit exits 0 when every finding was fixed.

Hybrid-aspect findings can also be repaired directly from the CLI without
enabling the daemon auto-fix:

```bash
# Lossless: sweep a directory (or single file) and stamp the corrective
# display aspect ratio into every detected MP4 via stream-copy remux.
python manual.py --repair-hybrid -i /path/to/library

# Full rebuild: re-encode one file back to its true dimensions
# (e.g. 1920x1088 -> 1920x1080) through the normal conversion pipeline.
python manual.py --repair-hybrid reencode -i /path/to/file.mp4
```

Remux mode exits 0 when everything detected was repaired (or nothing was
detected), 1 when a repair failed. Reencode mode requires a single file and
exits 2 when the input is not a detected hybrid.

### REST

```bash
curl -X POST -H "X-API-Key: $KEY" \
  -d '{"paths":["/media/movies"]}' \
  http://localhost:8585/library/audit
# → 202 {"status":"queued","audit_id":42,"paths":["/media/movies"]}

curl -H "X-API-Key: $KEY" http://localhost:8585/library/audit/42
# → run details with per-node progress

curl -H "X-API-Key: $KEY" "http://localhost:8585/library/findings?status=open&kind=ffprobe_failed&limit=50"
# → paginated finding list

curl -X POST -H "X-API-Key: $KEY" http://localhost:8585/library/findings/123/ack
curl -X POST -H "X-API-Key: $KEY" http://localhost:8585/library/findings/123/dismiss
curl -X POST -H "X-API-Key: $KEY" http://localhost:8585/library/findings/123/resolve
```

The `POST /library/audit` endpoint returns `202` immediately and runs
enumeration in a detached thread. Probing is then performed by every node's
worker thread.

### Scheduled

When `daemon.audit.enabled` is `true` and `daemon.audit.paths` is non-empty,
each node's `LibraryAuditThread` wakes every `interval-seconds` and (if it
wins the advisory lock) starts a new run. Workers on every node — including
the enumerator — drain the queue concurrently.

## Auto-fix safety semantics

Auto-fix is opt-in per finding kind and is a no-op while `dry-run: true`.
When enabled:

- `ffprobe-failed` → calls `add_job(...)` exactly like a webhook, so the
  per-path advisory lock and existing duplicate-suppression apply.
- `orphan-sidecar`, `leftover-tmp`, `preconv-original` → atomic copy to
  `base.converter.recycle-bin` (collision-safe with `.2`/`.3` suffix),
  followed by `os.remove` on the source. Files are never deleted directly.
- `hybrid-aspect` → lossless in-place remux: the file is renamed aside to
  `<file>.aspectfix`, stream-copied back with `-aspect W:H` stamping the
  true display aspect ratio, and the aside copy is removed on success (on
  failure the original is restored untouched). No re-encode occurs; storage
  dimensions stay as-is and players render the corrected geometry from the
  container aspect. To instead rebuild the video stream at its true
  dimensions, use `manual.py --repair-hybrid reencode` per file.

When `base.converter.recycle-bin` is empty the recycle path returns
`skipped` for cleanup actions — the finding stays open until either an
operator manually addresses it or a recycle bin is configured.

## Schema

| Table                       | Purpose                                                |
| --------------------------- | ------------------------------------------------------ |
| `library_audit_runs`        | One row per audit invocation                            |
| `library_audit_queue`       | Per-file work units; claim/done/error lifecycle        |
| `library_findings`          | Persistent findings with `(kind, path)` uniqueness      |
| `library_audit_media_ids`   | Scratch table used during a run to roll up duplicate-ids |

All four tables are created idempotently inside `_init_db()` — no separate
migration step required.

## Pitfalls

- TMDB/TVDB-id duplicate detection only sees files written by SMA-NG's
  current tagger (which writes dedicated `----:com.apple.iTunes:{TMDB,TVDB,IMDB}`
  atoms). Re-tag older files to bring them into duplicate-id scope.
- The auditor never traverses paths under any configured
  `base.converter.recycle-bin` directory, so files quarantined for review
  do not produce findings.
- Audit work runs in addition to conversion work on every node. Tune
  `concurrency` so it does not starve real conversions on small machines —
  the default of `2` is conservative.
