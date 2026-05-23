# Expanded Metrics: Backends, Savings, Failures, Media, Requests, Profiles + Prometheus Exposition

> **STATUS: IN-FLIGHT — generated 2026-05-22**

**Task breakdown**: [docs/tasks/metrics-expansion.md](../tasks/metrics-expansion.md)

## Discovery Summary

### Initial Task Analysis

User wants five new daemon-level metrics aggregates exposed alongside the
existing KPI block at `/api/metrics`:

1. **Encode count broken down by encoder backend** (qsv / vaapi / nvenc /
   videotoolbox / amf / software / copy).
2. **Aggregate bytes of disk space saved** by transcodes (`input_size_bytes
   − output_size_bytes`, summed over completed jobs).
3. **Aggregate minutes of video transcoded** (sum of source container
   duration).
4. **Failure count broken down by category** (config / source_media /
   hardware / disk / system / unknown) so operators can answer
   "what's actually failing?" without grepping logs.
5. **Source + destination media characteristics breakdown** — for each
   completed job, record the source video codec / resolution / audio
   codec / audio channels / HDR class, plus the same fields for the
   produced output. Surface as a nested `media: { source: {...},
   destination: {...} }` block so operators can answer "what is my
   library actually made of?" and "what am I shipping?" without
   re-probing every file.
6. **Request source breakdown** — for each enqueued job, record where
   the request came from (`sonarr` / `radarr` / `webhook` / `cli` /
   `scan` / `unknown`) **and** the resolved profile name
   (`4k` / `1080p` / `anime` / …). Surface a `requests:
   {source-profile: count}` block keyed `<source>-<profile>`
   (e.g. `radarr-4k`, `sonarr-1080p`) so operators can answer "which
   integration is driving load?" without grepping handler logs.

### User Clarifications Received

The user instructed work-without-pausing. Decisions taken in lieu of asking:

- **Storage**: persist on `jobs` rows via new columns; both DB backends
  (SQLite + Postgres) get the new aggregates. **In-memory `server.py`
  counters are deleted** — `prometheus_client` Counters/Histograms/Gauges
  exposed on `/metrics` replace every in-memory dict (`fallback_counters`,
  proposed `by_backend_counters`, `failure_category_counters`,
  `request_source_counters`). See the "Prometheus Instrumentation Layer"
  section below.
- **GPU granularity**: bucket by **backend** (`qsv`, `vaapi`, `nvenc`,
  `videotoolbox`, `amf`, `software`, `copy`). The concrete encoder name
  (`hevc_qsv`, `libx265`, …) is also stored on the job row so future
  re-bucketing is cheap.
- **Copy-only jobs**: separate `copy` bucket. Excluded from "encodes" KPI
  but bytes-saved + minutes are still credited (copy-only remuxes still
  shrink files via audio/subtitle changes).
- **Tier counting**: count the **final successful tier's encoder only**.
  Attempt-level fallback stats already exist in
  `server.py::fallback_counters` — we don't duplicate them.
- **Bytes-saved sign**: report net (allow negative). Also surface a
  separate `bytes_grown_total` for visibility into transcodes that
  enlarged the file.
- **Duration source**: container `format.duration` (already parsed by
  worker at `worker.py:301-310` for progress).
- **Backfill**: rollout-forward only. No stderr scraping of legacy rows.
- **Latent bug**: `complete_job(output_size=...)` is supported by both
  backends but **never called with a value** from the worker — fix as
  part of this PRP (otherwise bytes-saved is permanently 0).
- **Media characteristics scope**: video codec (ffprobe codec name —
  `hevc`, `h264`, `av1`, …), video width/height as raw INTEGERs plus a
  derived resolution bucket (`4k` / `1080p` / `720p` / `sd`) computed
  in SQL via `CASE WHEN`, primary audio stream codec
  (`aac`/`ac3`/`eac3`/`dts`/`truehd`/`flac`/…), primary audio channel
  count as raw INTEGER, and an HDR class
  (`sdr`/`hdr10`/`hdr10plus`/`dolby_vision`) derived from the existing
  `MediaStreamInfo.color` block plus the HDR helpers already used by
  `mediaprocessor.py`. Container is implicit in `output-format` /
  source filename — skip persisting it to keep the column count tight.
- **Request source taxonomy** — operator-facing enum: `sonarr`,
  `radarr`, `webhook` (generic POST not parsed as sonarr/radarr),
  `cli` (manual.py / API client submitting via `POST /jobs`), `scan`
  (the internal library-audit enqueuer at `threads.py:246`),
  `unknown` (catch-all for unattributed enqueues). The webhook
  handlers at `handler.py:1274,1339` and the scanner at
  `threads.py:246` are the **two** sites that classify; CLI/API
  callers default to `cli` unless they explicitly pass a different
  source header.
- **Profile attribution**: `add_job(path, config, args, max_retries)`
  already takes the *resolved* profile config — the profile **name**
  is known at the handler level before the call (router resolution).
  Plumb the name through as a new `add_job(..., request_source=...,
  request_profile=...)` kwarg pair. The `request_profile` column
  doubles as the join key for the standalone `profiles:` aggregate
  (success criterion 7).
- **Per-profile aggregate is independent of request_source**: a job
  enqueued by sonarr with profile `1080p` and a job enqueued by CLI
  with profile `1080p` both contribute to `profiles["1080p"]`. The
  `requests` block (success criterion 6) is the only place where the
  two dimensions are stitched together.
- **Profile failure rate**: include `failure_rate_pct` per profile
  (matches the existing top-level `failure_rate_pct` shape). Compute
  as `failed / (completed + failed)` per profile group, NULL when the
  group has no completed-or-failed rows yet.
- **Source vs destination probe**: source characteristics come straight
  from the existing `MediaInfo` that MediaProcessor already builds
  before transcoding (`converter/ffmpeg.py:130-141`). Destination
  characteristics come from re-probing the produced output **once** at
  job completion. The re-probe is cheap (a single `ffprobe` call on the
  finished file) and runs in MediaProcessor right before
  `_emit_attempt_log`, so the worker doesn't need to learn ffprobe.
- **Copy-stream destination fields**: if a stream was copied
  (`vcodec=="copy"` or audio stream copied), the destination codec /
  resolution / channels equal the source — no special-casing needed,
  the destination re-probe sees the muxed-as-is stream.
- **Failure categorization**: roll the existing
  `FfmpegFailureClass` (high-level, 6 values, in
  `resources/processor/failures.py:26-39`) and `FfmpegFailureCause`
  (fine-grained, ~30 values, lines 168-198) into an operator-facing
  category enum: `config` / `source_media` / `hardware` / `disk` /
  `system` / `unknown`. Store both the **category** (for the aggregate)
  and the **raw cause** (for drill-down) on the job row. Worker-level
  failures that never reach FFmpeg (path missing, invalid args at
  `worker.py:144,150`) bucket as `system`. The category mapping lives
  beside the existing enums (single source of truth, not duplicated in
  the daemon).

### Missing Requirements Identified

None blocking. The "what bucket does copy land in" and "bytes-saved sign"
questions were resolved by inspection of the existing `compression_pct`
KPI (Postgres-only, computed from `input_size_bytes / output_size_bytes`
at `db.py:1073-1212`) — the new aggregates mirror that pattern.

## Goal

Operators get four new aggregates on `/api/metrics` (and proportional
exposure on `/health`):

- `kpis.bytes_saved_total` — integer, can be negative.
- `kpis.bytes_grown_total` — integer, ≥ 0 (sum of negative deltas, sign-
  flipped).
- `kpis.minutes_transcoded_total` — float, two-decimal precision.
- `encoders: {backend: {count, bytes_saved, minutes}, ...}` block,
  mirroring the existing `nodes:` breakdown.
- `failures: {category: {count, top_cause}, ...}` block, where
  `category` is one of `config` / `source_media` / `hardware` / `disk`
  / `system` / `unknown` and `top_cause` is the most frequent raw
  `FfmpegFailureCause`/`FfmpegFailureClass` value within the bucket
  (purely informational; operators can drill into logs for the rest).

Plus the dashboard at `resources/metrics.html` renders an "Encoders" card
and a "Failures" card, both in the same style as the existing "Nodes" card.

## Why

- Operators currently can't answer "is our GPU pool earning its keep?"
  without manually grepping `ffmpeg.attempts` logs.
- Hardware-acceleration tuning (e.g. promoting `fallback-policy:
  aggressive`, choosing QSV vs VAAPI as the primary tier) is steered by
  guesswork without per-backend volume data.
- "Bytes saved" is the single most-quoted metric to justify continued
  SMA-NG operation to non-technical stakeholders.

## What

### User-visible behavior

- New keys on `GET /api/metrics`:

  ```json
  {
    "kpis": {
      "completed": 12345,
      "compression_pct": 38.2,
      "bytes_saved_total": 1234567890123,
      "bytes_grown_total": 4567890123,
      "minutes_transcoded_total": 87421.50,
      "...": "..."
    },
    "encoders": {
      "qsv":           { "count": 9876, "bytes_saved": 987654321098, "minutes": 65432.10 },
      "vaapi":         { "count":  321, "bytes_saved":   3210987654, "minutes":  2100.40 },
      "software":      { "count":  120, "bytes_saved":     12345678, "minutes":   800.00 },
      "copy":          { "count":   28, "bytes_saved":    100000000, "minutes":   480.00 }
    },
    "failures": {
      "hardware":      { "count": 43, "top_cause": "qsv_surface_pool_exhausted" },
      "source_media":  { "count": 11, "top_cause": "input_truncated" },
      "config":        { "count":  7, "top_cause": "qsv_unsupported_profile" },
      "disk":          { "count":  2, "top_cause": "disk_full" },
      "system":        { "count":  1, "top_cause": null },
      "unknown":       { "count":  0, "top_cause": null }
    },
    "media": {
      "source": {
        "video_codec":       { "hevc": 7421, "h264": 4892, "av1": 12, "vp9": 8 },
        "resolution_bucket": { "4k": 412, "1080p": 9876, "720p": 1521, "sd": 524 },
        "audio_codec":       { "ac3": 6512, "eac3": 3201, "aac": 2104, "dts": 412, "truehd": 88 },
        "audio_channels":    { "2": 5421, "6": 6210, "8": 612, "1": 89 },
        "hdr":               { "sdr": 11800, "hdr10": 412, "hdr10plus": 71, "dolby_vision": 50 }
      },
      "destination": {
        "video_codec":       { "hevc": 10918, "h264": 1415 },
        "resolution_bucket": { "4k": 412, "1080p": 9876, "720p": 1521, "sd": 524 },
        "audio_codec":       { "aac": 9210, "ac3": 2104, "eac3": 1019 },
        "audio_channels":    { "2": 6010, "6": 5712, "8": 612 },
        "hdr":               { "sdr": 11800, "hdr10": 412, "hdr10plus": 71, "dolby_vision": 50 }
      }
    },
    "requests": {
      "radarr-4k":      { "count":  412 },
      "radarr-1080p":   { "count": 6210 },
      "sonarr-1080p":   { "count": 4012 },
      "sonarr-anime":   { "count":  890 },
      "webhook":        { "count":  120 },
      "cli":            { "count":   58 },
      "scan":           { "count":  640 },
      "unknown":        { "count":    3 }
    },
    "profiles": {
      "4k":     { "count":  412, "bytes_saved":  82345678901, "minutes":  3210.50, "failure_rate_pct":  2.1 },
      "1080p":  { "count": 10222, "bytes_saved": 980123456789, "minutes": 75432.20, "failure_rate_pct":  0.4 },
      "anime":  { "count":   890, "bytes_saved":  60123456789, "minutes":  6210.10, "failure_rate_pct":  1.8 },
      "default":{ "count":   281, "bytes_saved":   8123456789, "minutes":  1568.70, "failure_rate_pct":  0.7 }
    }
  }
  ```

- `GET /health` payload gains a `by_backend` field under the existing
  `fallback` block (or alongside it) reflecting since-process-start
  counts only.
- Dashboard at `/metrics` renders an Encoders card with one row per
  backend showing `count / bytes_saved (GiB) / minutes`.

### Success Criteria

- [ ] `complete_job()` is called with `output_size`, `encoder_backend`,
      `encoder_name`, and `source_duration_seconds` on every successful
      completion (no `None` for these on a row that has `status=completed`).
- [ ] `fail_job()` is called with `failure_category` + `failure_cause`
      on every failure path. `worker.py:144,150,191,197` callers each
      classify (path missing / invalid args / process failed /
      exception) into the correct category.
- [ ] Every `FfmpegFailureCause` and `FfmpegFailureClass` value maps to
      exactly one operator category via a single resolver in
      `resources/processor/failures.py`. New enum values added in the
      future will fail a unit test until they're mapped (no silent
      `unknown` regression).
- [ ] SQLite + Postgres `get_metrics()` both return the new keys with
      correct sums when seeded with synthetic rows.
- [ ] Bucket selection: `vcodec == "copy"` → `encoders.copy`; any
      `VideoCodec` subclass with `hw_prefix` attribute → that backend;
      otherwise → `software`.
- [ ] Negative deltas (output > input) are summed into `bytes_grown_total`
      (sign-flipped to positive) and the net amount stays in
      `bytes_saved_total`. The two are **not** mutually exclusive — a
      mixed workload populates both.
- [ ] `metrics.html` renders the Encoders card with at least the
      backends present in the response; missing backends omitted (do not
      hard-code the full list — the JSON drives the table).
- [ ] `docs/metrics.md`, `docs/daemon.md`, `docs/openapi.yaml`,
      `resources/metrics.html` updated in the same commit series.
- [ ] Coverage: ≥ 90 % global, ≥ 70 % per-module on `resources/daemon/db.py`,
      `resources/daemon/worker.py`, `resources/daemon/server.py`,
      `resources/daemon/handler.py`.

## All Needed Context

### Research Phase Summary

- **Codebase patterns found**: complete. Single source of truth for
  encoder→backend mapping is `hw_prefix` on `VideoCodec` subclasses in
  `converter/avcodecs.py`. Existing `compression_pct` KPI in
  `db.py:1073-1212` is the structural mirror for the new sums. SQLite
  schema migrations are PRAGMA-then-ALTER (`db.py:125-130`); Postgres is
  `ADD COLUMN IF NOT EXISTS` (`db.py:692-696`).
- **External research**: not needed. No new libraries; all mechanisms
  already exist.
- **Knowledge gaps**: none.

### Documentation & References

```yaml
- file: resources/daemon/db.py
  lines: 104-130, 234-240, 308, 607-696, 753-755, 984-990, 1073-1212
  why: |
    104-130: SQLite jobs CREATE + the PRAGMA migrator pattern to copy.
    607-696, 753-755: Postgres jobs CREATE + ADD COLUMN IF NOT EXISTS pattern.
    234-240, 984-990: complete_job signature (both backends) — note the
    unused output_size parameter that this PRP makes load-bearing.
    308: SQLite get_metrics stub — must be lifted to parity for the new aggregates.
    1073-1212: Postgres get_metrics — extend in place; mirror compression_pct
    style for the three new sums and add the encoders breakdown next to nodes.

- file: resources/daemon/worker.py
  lines: 179-186, 274, 301-310, 324, 353-396
  why: |
    179-186: complete_job call site — extend with output_size + encoder
    metadata + source_duration. output_size from os.path.getsize(output_path);
    encoder + duration parsed from the ffmpeg.attempts JSON payload that
    MediaProcessor now must emit.
    301-310: container duration parser (already exists, just hand it off).
    353-396: _parse_ffmpeg_attempts_line — extend to surface the new fields.

- file: resources/mediaprocessor.py
  lines: 1361, 1428, 1651, 1672, 1834-1837, 3386-3494, 3523-3541
  why: |
    1361, 1428, 1651: where vcodec is selected and threaded into the build.
    1834-1837: Converter.encoder(vcodec) — read .hw_prefix here to derive backend.
    3523-3541: _emit_attempt_log — add encoder_name + encoder_backend +
    output_size_bytes + source_duration_seconds to the JSON payload.

- file: resources/daemon/server.py
  lines: 130-132, 148, 319-332
  why: |
    Mirror this pattern for new self.by_backend_counters dict +
    increment_backend_counter method + by_backend_summary snapshot.
    Wire from worker pool via a new callback (parallel to fallback_counter_callback).

- file: resources/daemon/handler.py
  lines: 153-182, 574-593
  why: |
    153-182 (/health): add by_backend_summary into the snapshot payload.
    574-593 (/api/metrics): no code change beyond passing through the
    extended dict from job_db.get_metrics().

- file: converter/avcodecs.py
  lines: 704-709, 1394-1437, 1452, 1495-1497, 1527-1530, 1740-1743, 1830, 1842-1843
  why: |
    hw_prefix is the SSoT. Verify videotoolbox encoders (1436-1437) have
    hw_prefix set; add it if missing rather than special-casing in the
    new resolver.

- file: resources/metrics.html
  lines: 203, 267, 390-408
  why: |
    KPI binding pattern (267) — kpis are auto-rendered, so the three new
    keys appear without HTML changes if the labels map is updated.
    Nodes card (203, 390-408) — mirror this for the Encoders card.

- file: tests/test_daemon.py
  lines: 525-527, 926-987, 1284-1303, 3641-3711
  why: |
    525-527: TEST_DB_URL skip gate for real Postgres tests.
    926-987: fake psycopg2 pattern for Postgres tests without psycopg2.
    1284-1303: complete_job lifecycle test pattern.
    3641-3711: get_metrics analytics test pattern — extend with new aggregates.

- file: tests/test_metrics.py
  lines: 18, 53
  why: _METRICS_FIXTURE pattern — grow with the new keys.
```

### Current Codebase tree (relevant slice)

```text
converter/
  avcodecs.py            # hw_prefix on VideoCodec subclasses (SSoT)
resources/
  daemon/
    db.py                # SQLite + Postgres backends, jobs table, get_metrics
    handler.py           # /api/metrics, /health route handlers
    routes.py            # route table
    server.py            # in-memory counters + lifecycle
    worker.py            # job execution, ffmpeg.attempts parsing
  mediaprocessor.py      # _attempt_ladder + _emit_attempt_log
  metrics.html           # dashboard
docs/
  metrics.md             # operator metrics doc
  daemon.md              # endpoint reference
  openapi.yaml           # add /api/metrics entry
tests/
  test_daemon.py         # db + worker integration
  test_handler.py        # route tests
  test_metrics.py        # metrics-specific handler tests
  test_worker.py         # worker unit tests
  test_avcodecs.py       # codec/encoder coverage
```

### Desired Codebase tree

No new files. All changes are additive edits to the files above plus:

```text
tests/test_metrics_aggregates.py   # NEW — unit tests for the three new
                                   #       sums (both backends, fake psycopg2)
```

### Known Gotchas & Library Quirks

```python
# CRITICAL: SQLite cannot ALTER TABLE ADD COLUMN IF NOT EXISTS — must use
# PRAGMA table_info(jobs) introspection (see db.py:125-130 for the
# existing pattern). Postgres uses ADD COLUMN IF NOT EXISTS directly.

# CRITICAL: complete_job(output_size=...) parameter already exists on both
# backends but is never passed. Tests for the new feature MUST exercise the
# full plumbing from worker.py:186 down to the DB row — a unit test on
# get_metrics() alone will pass while production silently records 0 bytes saved.

# CRITICAL: ffmpeg.attempts JSON is a single line and worker.py greps for it
# with a literal substring check at worker.py:324. Do not break that line
# into multiple records or change the substring '"event": "ffmpeg.attempts"'.

# CRITICAL: hw_prefix is missing on h264_videotoolbox (avcodecs.py:1436-1437).
# Add hw_prefix = "videotoolbox" on the VideoToolbox encoders rather than
# special-casing in the resolver — keeps the SSoT intact.

# CRITICAL: vcodec == "copy" must be bucketed as "copy", not "software".
# Converter.encoder("copy") returns a VideoCopyCodec instance which has no
# hw_prefix; the resolver must check for the literal "copy" string first.

# CRITICAL: source_duration_seconds can be None on .dav and a handful of
# malformed inputs (per the farmnvr-timelapse PRP gotcha). Treat NULL/None
# as 0 for the sum — do not raise. Tested separately.

# CRITICAL: SQLite get_metrics is currently a stub returning {"available":
# False}. The new aggregates must NOT be silently dropped on SQLite. Lift
# the stub to compute at least the new three sums + the encoders breakdown
# + the failures breakdown (compression_pct can stay null/derived if it's
# a heavier lift).

# CRITICAL: SQLite has no mode() WITHIN GROUP. For top_cause on SQLite, use:
#   SELECT failure_cause, COUNT(*) c FROM jobs
#    WHERE status='failed' AND failure_category=?
#    GROUP BY failure_cause ORDER BY c DESC LIMIT 1
# run per category. It's N+1 but N=6 and the dataset is small.

# CRITICAL: failure categorization MUST cover every existing enum value.
# Add a unit test that iterates FfmpegFailureClass + FfmpegFailureCause and
# fails if any value maps to "unknown" — this is the only safeguard against
# silent drift when new enum values land later.

# CRITICAL: worker.py:191 ("Conversion process failed") should prefer the
# *last attempt's* failure_class from the ffmpeg.attempts JSON line, not
# the generic "process_failed" sentinel — otherwise every hardware bug
# lands in `system` instead of `hardware` and the breakdown is useless.

# CRITICAL: HDR derivation must reuse the existing helpers (MediaInfo
# carries `color` dict at converter/ffmpeg.py:146). Do not add a new HDR
# detector — mediaprocessor.py already classifies HDR for the transcode
# path (search for HDR10/Dolby Vision handling). Wire the *same* function
# into the persistence step. If the classifier returns None, persist 'sdr'.

# CRITICAL: destination probe is ONE ffprobe call on the finished file
# inside MediaProcessor *after* the successful encode and *before*
# _emit_attempt_log. Do not re-probe per-stream in a loop, and do not
# move it into the worker — worker should stay ffprobe-agnostic.

# CRITICAL: ffprobe of the destination CAN fail (rare: file truncated,
# moved by another process). Wrap in try/except, log at WARNING, persist
# the destination columns as NULL, and let the aggregate query exclude
# NULL bins. Do NOT abort the success path because of a probe glitch.

# CRITICAL: "primary audio stream" = first audio stream in the muxed
# output that isn't a commentary track. MediaProcessor already tracks
# which audio stream is the principal one (via `audio.sorting` config) —
# reuse that selection rather than picking stream index 0 blindly.

# CRITICAL: resolution_bucket is computed in SQL, NOT Python. Persist raw
# width/height; let get_metrics do the bucket categorisation. This keeps
# bucket boundaries operator-tunable without a schema migration.

# CRITICAL: request_source must be set at the FIRST point where the
# request shape is known — i.e. inside the webhook parser dispatch in
# handler.py, NOT later in add_job. Otherwise sonarr POSTs that fall
# through to the generic JSON branch get mis-attributed as "webhook".

# CRITICAL: request_profile MUST come from the routing/resolution layer
# that already runs before add_job — do NOT introduce a second profile
# resolver. If the routing layer returns no named profile (e.g. an
# unrouted path falling back to base:), persist NULL, not an empty string.

# CRITICAL: per-profile failure_rate_pct over a window with zero
# (completed + failed) is NULL, not 0 and not an exception. Both
# backends already handle this for the top-level failure_rate_pct
# (db.py:1100-1104 uses NULLIF) — mirror exactly.

# CRITICAL: the requests aggregate key is built in SQL with COALESCE
# (see Task 14 pseudocode). Building it in Python after fetching rows
# would force a SELECT * over the jobs table and defeat get_metrics's
# single-query design.
```

## Implementation Blueprint

### Data models and structure

```python
# resources/daemon/db.py — both backends, additive columns on `jobs`
#
#   encoder_backend           TEXT       # 'qsv'|'vaapi'|'nvenc'|'videotoolbox'|'amf'|'software'|'copy'
#   encoder_name              TEXT       # 'hevc_qsv'|'libx265'|'copy'|...
#   source_duration_seconds   REAL       # SQLite / DOUBLE PRECISION on Postgres
#   failure_category          TEXT       # 'config'|'source_media'|'hardware'|'disk'|'system'|'unknown'
#   failure_cause             TEXT       # raw FfmpegFailureCause or FfmpegFailureClass value, or worker
#                                        #   sentinel ('path_missing'|'invalid_args'|'process_failed'|'exception')
#
#   source_video_codec        TEXT       # ffprobe codec name: 'hevc'|'h264'|'av1'|'vp9'|...
#   source_video_width        INTEGER    # pixels
#   source_video_height       INTEGER    # pixels
#   source_audio_codec        TEXT       # primary audio stream codec
#   source_audio_channels     INTEGER    # primary audio stream channel count
#   source_hdr                TEXT       # 'sdr'|'hdr10'|'hdr10plus'|'dolby_vision'
#
#   dest_video_codec          TEXT       # post-transcode ffprobe codec name
#   dest_video_width          INTEGER
#   dest_video_height         INTEGER
#   dest_audio_codec          TEXT
#   dest_audio_channels       INTEGER
#   dest_hdr                  TEXT
#
#   request_source            TEXT       # 'sonarr'|'radarr'|'webhook'|'cli'|'scan'|'unknown'
#   request_profile           TEXT       # resolved profile name (e.g. '4k', '1080p', 'anime')

# resources/processor/failures.py — single resolver alongside the existing enums
#
#   FAILURE_CATEGORY_MAP: dict[str, str] = { ... }   # cause/class string -> category
#
#   def categorize_failure(cause_or_class: str | None) -> str:
#       if cause_or_class is None:
#           return "unknown"
#       return FAILURE_CATEGORY_MAP.get(cause_or_class, "unknown")

# converter/avcodecs.py — verify hw_prefix on VideoToolbox encoders; add if missing.

# resources/mediaprocessor.py::_emit_attempt_log — extended JSON payload:
#   {
#     "event": "ffmpeg.attempts",
#     "result": "ok",
#     "attempts": [...],
#     "encoder_name": "hevc_qsv",            # NEW
#     "encoder_backend": "qsv",              # NEW (derived from hw_prefix)
#     "output_size_bytes": 1234567,          # NEW (os.path.getsize at success)
#     "source_duration_seconds": 1801.23     # NEW (MediaFormatInfo.duration)
#   }

# resources/daemon/server.py — new in-memory counter
#   self.by_backend_counters: dict[str, int] = {}   # backend -> count
#   self.by_backend_lock = threading.Lock()
```

### Task list (ordered)

```yaml
Task 1 — Schema columns + migration (additive):
MODIFY resources/daemon/db.py:
  - FIND SQLite CREATE TABLE jobs (line 104) — add the three new columns inline
  - FIND PRAGMA table_info(jobs) block (line 125) — add the three ALTER TABLE branches
  - FIND Postgres CREATE TABLE jobs (line 607) — add the three columns inline
  - FIND idempotent ALTERs (line 753) — add three ADD COLUMN IF NOT EXISTS
CREATE tests/test_metrics_aggregates.py:
  - SQLite + fake-psycopg2 round-trip test confirming the three columns persist

Task 2 — VideoToolbox hw_prefix gap (single-line fix if missing):
MODIFY converter/avcodecs.py:
  - Verify h264_videotoolbox / hevc_videotoolbox classes carry hw_prefix = "videotoolbox"
  - Add if absent (no-op if present)

Task 3 — Encoder/backend resolver:
MODIFY resources/mediaprocessor.py (near line 1834 where Converter.encoder(vcodec) is called):
  - Add helper _resolve_encoder_backend(vcodec, vencoder) -> tuple[str, str]
    returning (encoder_name, encoder_backend) where:
      "copy" -> ("copy", "copy")
      vencoder.hw_prefix set -> (vcodec, vencoder.hw_prefix)
      else -> (vcodec, "software")
  - Plumb the tuple into the _emit_attempt_log payload (line 3523-3541)
  - Also attach output_size_bytes (stat the temp output if it exists) and
    source_duration_seconds (info.format.duration)

Task 4 — Worker ingestion of new fields:
MODIFY resources/daemon/worker.py:
  - FIND _parse_ffmpeg_attempts_line (line 353) — extract the four new fields
  - FIND complete_job call site (line 186) — pass output_size + encoder_backend
    + encoder_name + source_duration_seconds. Fallback to os.path.getsize on
    output_path if the attempts line lacked output_size_bytes (older builds).

Task 5 — complete_job signature extension (both backends):
MODIFY resources/daemon/db.py:
  - Extend SQLite complete_job (234) and Postgres complete_job (984)
    signatures with encoder_backend, encoder_name, source_duration_seconds
    (default None)
  - Include them in the UPDATE statements

Task 6 — get_metrics aggregates:
MODIFY resources/daemon/db.py:
  - Postgres get_metrics (1073-1212): add SUM(input_size_bytes - output_size_bytes)
    FILTER (WHERE output_size_bytes <= input_size_bytes) for bytes_saved_total,
    sign-flipped for bytes_grown_total, SUM(source_duration_seconds)/60 for
    minutes; plus a GROUP BY encoder_backend block for the encoders dict.
  - SQLite get_metrics (308): replace stub with at minimum the three sums
    and the encoders breakdown. Reuse compression_pct math if straightforward.

Task 7 — In-memory by_backend counters in server.py:
MODIFY resources/daemon/server.py:
  - Mirror fallback_counters: add by_backend_counters dict + lock (130-132)
  - Add increment_by_backend(backend) (after 327)
  - Add by_backend_summary() (after 332)
  - Wire callback via worker pool init (after 148, parallel to fallback_counter_callback)
MODIFY resources/daemon/worker.py:
  - Call the new callback once per completed job after complete_job lands

Task 8 — Surface on /health:
MODIFY resources/daemon/handler.py (_get_health at 153-182):
  - Inject by_backend_summary into payload["by_backend"]

Task 9 — Dashboard:
MODIFY resources/metrics.html:
  - Add three KPI label mappings (bytes_saved_total, bytes_grown_total, minutes_transcoded_total)
  - Add an "Encoders" card mirroring the Nodes card (203, 390-408)

Task 10 — Docs (same-commit-series per CLAUDE.md):
MODIFY docs/metrics.md:
  - Document the three new kpis keys + the encoders block + the /health by_backend field
MODIFY docs/daemon.md:
  - Update /api/metrics endpoint description (64-65, 157, 285)
MODIFY docs/openapi.yaml:
  - Add /api/metrics path entry with the response schema (currently missing entirely)

Task 11 — Tests:
ADD to tests/test_daemon.py near 3641-3711:
  - Seed jobs with mixed backends and assert encoders breakdown sums
  - Negative delta row populates bytes_grown_total, not bytes_saved_total
  - NULL source_duration_seconds row contributes 0 minutes (no exception)
  - Seed failed jobs with mixed failure_category values and assert
    failures breakdown counts + top_cause is the modal raw cause per bucket
ADD to tests/test_worker.py:
  - _parse_ffmpeg_attempts_line picks up the new fields
  - complete_job is called with all four new arguments
  - fail_job is called with (failure_category, failure_cause) on each
    of the four failure paths (path_missing/invalid_args/process_failed/exception)
ADD to tests/test_metrics.py:
  - _METRICS_FIXTURE gains encoders + failures + new kpis keys; handler returns them
ADD to tests/test_avcodecs.py (if hw_prefix gap):
  - Assert h264_videotoolbox.hw_prefix == "videotoolbox"
ADD tests/test_failure_categorization.py (new):
  - Every FfmpegFailureCause and FfmpegFailureClass enum value maps to a
    non-"unknown" category (guards against silent drift when new causes land)
  - categorize_failure(None) == "unknown"
  - Worker sentinels (path_missing, invalid_args, process_failed, exception)
    each resolve to "system" or the right operator-facing category

Task 12 — Failure categorization + plumbing:
MODIFY resources/processor/failures.py:
  - Add FAILURE_CATEGORY_MAP and categorize_failure(...) next to the enums (line 26+, 168+)
  - Mapping rules (full table — these MUST cover every existing enum value):
      hardware:    DEVICE_OPEN_FAILED, DECODER_INIT_FAILED, ENCODER_INIT_FAILED,
                   FILTER_INIT_FAILED, all QSV_*, NVENC_*, VAAPI_*
      config:      QSV_UNSUPPORTED_PROFILE, QSV_UNSUPPORTED_PIX_FMT, STRICT_FLAG_REQUIRED,
                   HEVC_REF_FRAME_LIMIT, BFRAME_COPY_INCOMPATIBLE, HDR_TAGGING_MISMATCH,
                   DOLBY_VISION_REQUIRES_STRICT, BITRATE_TOO_LOW_FOR_RESOLUTION
      source_media: INPUT_TRUNCATED, PTS_DTS_NONMONOTONIC, SOURCE_UNAVAILABLE,
                   AUDIO_CHANNEL_LAYOUT_MISMATCH, AUDIO_SAMPLE_RATE_MISMATCH,
                   SUBTITLE_MUX_FAIL, IMAGE_SUBTITLE_TO_TEXT, ATTACHMENT_MUX_FAIL,
                   VBV_UNDERRUN
      disk:        DISK_FULL, PERMISSION_DENIED
      system:      RUNTIME_ERROR, OTHER, and worker sentinels
                   (path_missing, invalid_args, process_failed, exception)
      unknown:     None / unmapped string fallback only

MODIFY resources/daemon/db.py:
  - Extend fail_job (SQLite line 258, Postgres line 1011) signatures with
    failure_category=None, failure_cause=None
  - Persist them on the UPDATE statement
  - Add two columns to the jobs CREATE + migrator: failure_category TEXT, failure_cause TEXT
  - Extend get_metrics (both backends) with a GROUP BY failure_category
    query that also returns the modal failure_cause per bucket
    (Postgres: mode() WITHIN GROUP; SQLite: nested SELECT with COUNT)

MODIFY resources/daemon/worker.py:
  - Each fail_job call site (144, 150, 191, 197) classifies via
    categorize_failure(sentinel) and passes the pair through.
  - For 191 (Conversion process failed), prefer the last AttemptRecord's
    failure_class from the parsed ffmpeg.attempts JSON when present; fall
    back to "process_failed" sentinel otherwise.

MODIFY resources/daemon/server.py:
  - Add self.failure_category_counters (mirrors by_backend_counters pattern)
  - Add increment_failure_category(category) + failure_category_summary()
  - Wire callback parallel to fallback_counter_callback

Task 13 — Source + destination media characteristics:
MODIFY resources/daemon/db.py:
  - Add 12 columns to jobs CREATE + migrator (see Data Models block above):
      source_video_codec, source_video_width, source_video_height,
      source_audio_codec, source_audio_channels, source_hdr,
      dest_video_codec,   dest_video_width,   dest_video_height,
      dest_audio_codec,   dest_audio_channels, dest_hdr
  - Extend complete_job signature with all 12 (default None)
  - Persist on the UPDATE statement
  - Extend get_metrics (both backends) with a media: {source, destination}
    block. For resolution_bucket use CASE WHEN in SQL:
        CASE WHEN height >= 2160 THEN '4k'
             WHEN height >= 1080 THEN '1080p'
             WHEN height >=  720 THEN '720p'
             ELSE                       'sd'  END
    Six GROUP BY queries per side (5 sub-breakdowns + the resolution CASE).

MODIFY converter/ffmpeg.py (probe path):
  - Verify MediaInfo.format / primary video stream / primary audio stream
    expose: codec, width, height, channels, color/HDR class.
  - No new fields needed unless HDR class isn't already a property — if
    it lives only in mediaprocessor.py, extract the classifier to a small
    helper on MediaInfo (NOT a new class) so it's callable from both
    the source-side and destination-side probe.

MODIFY resources/mediaprocessor.py:
  - Before _emit_attempt_log on the success path, run ONE ffprobe on the
    finished output file → MediaInfo. Build a media payload:
        {
          "source":      {"video_codec":..., "video_width":..., ...},
          "destination": {"video_codec":..., "video_width":..., ...}
        }
  - Attach to the ffmpeg.attempts JSON payload (extends the encoder_name /
    output_size_bytes additions from Task 3).
  - On destination ffprobe failure: log at WARNING via the existing logger,
    set destination fields to None, continue.

MODIFY resources/daemon/worker.py:
  - _parse_ffmpeg_attempts_line extracts the 12 new fields from the media
    payload and passes them to complete_job.

MODIFY resources/metrics.html:
  - Render the media:source and media:destination blocks as two grouped
    cards ("Source Library" / "Output Library"), each containing the five
    sub-breakdowns. Mirror the Nodes/Encoders card pattern.

ADD to tests:
  - tests/test_daemon.py: seed jobs with mixed media characteristics; assert
    media.source and media.destination breakdowns sum correctly.
  - tests/test_daemon.py: 4k/1080p/720p/sd boundary test (height = 2160 →
    '4k'; height = 2159 → '1080p'; height = 720 → '720p'; height = 719 → 'sd').
  - tests/test_mediaprocessor.py: destination probe failure does NOT
    abort the success path; columns end up as None.
  - tests/test_mediaprocessor.py: copy-stream job has identical
    source/destination codec + resolution + channels.

Task 14 — Request source + per-profile attribution:
MODIFY resources/daemon/db.py:
  - Add `request_source TEXT` and `request_profile TEXT` to jobs CREATE +
    migrator (mirrors T1.x pattern on both backends).
  - Extend add_job(path, config, args, max_retries, *, request_source=None,
    request_profile=None) signature on both backends. INSERT writes them.
  - Extend get_metrics with two new blocks:
      requests:  GROUP BY COALESCE(request_source || '-' || request_profile,
                                   request_source, 'unknown')
      profiles:  GROUP BY request_profile WITH count, sum(bytes_saved),
                 sum(minutes), failed/(completed+failed) AS failure_rate_pct

MODIFY resources/daemon/handler.py:
  - Sonarr webhook path (handler.py:1274 area): pass request_source="sonarr".
  - Radarr webhook path (handler.py:1339 area): pass request_source="radarr".
  - Generic webhook (parse_generic_webhook_body): pass request_source="webhook".
  - Direct /jobs POST (CLI/API caller): pass request_source="cli" (allow override
    via X-SMA-Request-Source header for callers that know better).
  - In ALL cases, pass request_profile = the resolved profile name from the
    routing step before add_job. The profile name is already in scope at
    that point — no new lookups needed.

MODIFY resources/daemon/threads.py:
  - Library-audit enqueuer (threads.py:246): pass request_source="scan",
    request_profile = whatever the scan profile resolves to (None if absent).

MODIFY resources/daemon/server.py:
  - Add self.request_source_counters dict + lock + increment + summary
    mirroring the by_backend_counters pattern.
  - Increment at job *completion* (not enqueue) so the in-memory counter
    aligns with what the DB aggregate sees. Surface on /health.

MODIFY resources/metrics.html:
  - Add a "Requests" card (one row per source-profile pair) and a
    "Profiles" card (one row per profile with count / bytes_saved / minutes
    / failure_rate_pct). Mirror the Nodes/Encoders card structure.

ADD to tests:
  - tests/test_handler.py: sonarr POST persists request_source="sonarr" + the
    resolved profile name on the row.
  - tests/test_handler.py: generic webhook persists request_source="webhook".
  - tests/test_handler.py: X-SMA-Request-Source header overrides the default
    "cli" attribution on direct POST /jobs.
  - tests/test_daemon.py: requests aggregate key is "<source>-<profile>" when
    profile is set, bare source otherwise.
  - tests/test_daemon.py: profiles aggregate sums bytes_saved + minutes
    across rows with the SAME request_profile regardless of request_source.
  - tests/test_daemon.py: profile with zero completed AND zero failed yields
    failure_rate_pct = None (not 0, not a ZeroDivisionError).
```

### Per-task pseudocode (only the non-obvious ones)

```python
# Task 3 — resources/mediaprocessor.py
def _resolve_encoder_backend(vcodec: str, vencoder) -> tuple[str, str]:
    # GOTCHA: copy must be checked BEFORE hw_prefix, because VideoCopyCodec
    # could in theory carry hw_prefix in the future.
    if vcodec == "copy":
        return ("copy", "copy")
    backend = getattr(vencoder, "hw_prefix", None) or "software"
    return (vcodec, backend)

# Inside _emit_attempt_log (3523-3541) on success path:
payload["encoder_name"]            = encoder_name
payload["encoder_backend"]         = encoder_backend
payload["output_size_bytes"]       = _safe_stat(output_path)        # None if temp file gone
payload["source_duration_seconds"] = info.format.duration            # may be None

# Task 6 — Postgres get_metrics (mirror compression_pct pattern at 1073-1212)
# CRITICAL: use FILTER (WHERE ...) clauses, NOT WHERE on the outer query —
# the existing query computes all KPIs in one pass and we want to stay
# consistent so the time-window joins keep working.
SELECT
    ...,
    COALESCE(SUM(GREATEST(input_size_bytes - output_size_bytes, 0)), 0)  AS bytes_saved_total,
    COALESCE(SUM(GREATEST(output_size_bytes - input_size_bytes, 0)), 0)  AS bytes_grown_total,
    COALESCE(SUM(source_duration_seconds), 0) / 60.0                     AS minutes_transcoded_total
FROM jobs
WHERE status = 'completed';

# Separate query for the encoders breakdown (one row per backend):
SELECT
    encoder_backend                                                       AS backend,
    COUNT(*)                                                              AS count,
    COALESCE(SUM(GREATEST(input_size_bytes - output_size_bytes, 0)), 0)  AS bytes_saved,
    COALESCE(SUM(source_duration_seconds), 0) / 60.0                     AS minutes
FROM jobs
WHERE status = 'completed' AND encoder_backend IS NOT NULL
GROUP BY encoder_backend;
```

### Integration Points

```yaml
DATABASE:
  - migration: |
      SQLite: PRAGMA table_info(jobs) gate + 3x ALTER TABLE jobs ADD COLUMN
      Postgres: 3x ALTER TABLE jobs ADD COLUMN IF NOT EXISTS
  - index: none — aggregates are full-scan over `status='completed'`; existing
           jobs table is small enough (typical operator: <1M rows) that an
           index on (status, encoder_backend) is premature. Add only if a
           prod operator reports get_metrics > 200ms.
  - schema: encoder_backend TEXT, encoder_name TEXT, source_duration_seconds REAL/DOUBLE PRECISION

API/ROUTES:
  - no new routes — extend existing /api/metrics response and /health payload.

CONFIG:
  - no new config keys. The feature is always-on.
```

## Validation Loop

### Level 1: Syntax & Style

```bash
mise run dev:lint
mise run test:lint
```

### Level 2: Targeted tests

```bash
source venv/bin/activate && python -m pytest tests/test_daemon.py tests/test_handler.py \
  tests/test_worker.py tests/test_metrics.py tests/test_metrics_aggregates.py -q

source venv/bin/activate && python -m pytest tests/test_mediaprocessor.py tests/test_avcodecs.py -q
```

### Level 3: Real Postgres (optional, gated)

```bash
TEST_DB_URL=postgresql://localhost/sma_test \
  source venv/bin/activate && python -m pytest tests/test_daemon.py::test_get_metrics_encoder_aggregates -q
```

### Level 4: Manual smoke

```bash
mise run daemon:start &
curl -s localhost:8585/api/metrics | jq '.kpis.bytes_saved_total, .kpis.minutes_transcoded_total, .encoders'
curl -s localhost:8585/health     | jq '.by_backend'
```

## Final Validation Checklist

- [ ] `mise run test` passes
- [ ] `mise run test:lint` clean
- [ ] Coverage ≥ 90 % global, ≥ 70 % per touched module ≥ 100 stmts
- [ ] `complete_job` test asserts all four new arguments are persisted
- [ ] Encoders breakdown sums match the per-job rows on a synthetic dataset
- [ ] `bytes_grown_total` populated when output > input on at least one row
- [ ] NULL `source_duration_seconds` rows contribute 0 minutes (no exception)
- [ ] `/health` shows `by_backend` and `failure_categories` since process start
- [ ] `metrics.html` Encoders + Failures cards render with synthetic data
- [ ] Every `FfmpegFailureClass` + `FfmpegFailureCause` value maps to a
      non-`unknown` category (enforced by unit test)
- [ ] `worker.py:191` failure path uses the last attempt's `failure_class`
      from the parsed `ffmpeg.attempts` line — hardware failures land in
      `failures.hardware`, not `failures.system`
- [ ] Source media characteristics (video codec, width, height, audio
      codec, audio channels, HDR class) are recorded on every completed
      job from the existing pre-transcode `MediaInfo`.
- [ ] Destination media characteristics are recorded by a single post-
      transcode `ffprobe` call on the produced output, attached to the
      `ffmpeg.attempts` JSON payload.
- [ ] Resolution bucket is derived in SQL (`CASE WHEN height >= 2160
      THEN '4k' WHEN height >= 1080 THEN '1080p' ...`) — NOT a Python
      categorisation. Keeps the SSoT in one place.
- [ ] `media.source` and `media.destination` blocks contain the five
      sub-breakdowns (video_codec, resolution_bucket, audio_codec,
      audio_channels, hdr) with `{value: count}` shapes.
- [ ] A copy-stream job shows the *same* codec/resolution/channels on
      source and destination (no special-casing — verified via test).
- [ ] Every enqueue call site populates `request_source` (`sonarr` /
      `radarr` / `webhook` / `cli` / `scan` / `unknown`). The two
      webhook handlers at `handler.py:1274,1339` and the scanner at
      `threads.py:246` carry deterministic values; unattributed
      enqueues default to `cli`.
- [ ] `requests` block aggregate-keyed `f"{source}-{profile}"` when
      `request_profile` is set; falls back to bare `source` otherwise.
- [ ] `profiles` block aggregates `count`, `bytes_saved`, `minutes`,
      `failure_rate_pct` per resolved profile name across all
      request sources.
- [ ] A job enqueued by CLI with profile `1080p` and a job enqueued
      by sonarr with profile `1080p` both appear in `profiles["1080p"]`
      but in different `requests` entries.
- [ ] `/metrics` returns Prometheus text exposition; `/dashboard/metrics`
      serves the HTML view (former `/metrics`); `/api/metrics` (JSON)
      stays unchanged.
- [ ] `prometheus-client` is the only new runtime dependency.
- [ ] No metric uses an unbounded label (`job_id`, `path`,
      `error_message`, `output_path`) — enforced by a unit test that
      iterates every registered metric.
- [ ] The in-memory counter dicts in `server.py:130-332` are removed in
      the same commit series; `/health` no longer carries `fallback` /
      `by_backend` / `failure_categories` / `request_sources` blocks.
- [ ] CI runs `promtool check metrics` against a captured fixture of
      the daemon's `/metrics` response.
- [ ] `docs/metrics.md`, `docs/daemon.md`, `docs/openapi.yaml`,
      `resources/metrics.html` updated in the same logical change
- [ ] No regressions on the existing `compression_pct` / `nodes` KPIs
- [ ] SQLite-mode operators see the new aggregates (no `"available": False` stub)

---

## Anti-Patterns to Avoid

- ❌ Don't introduce a new `encoder_backend.py` module — `hw_prefix` on
  `VideoCodec` subclasses is the SSoT. A new module clones state.
- ❌ Don't special-case VideoToolbox in the resolver — fix the missing
  `hw_prefix` instead.
- ❌ Don't gate the new aggregates behind a config flag. They're always-on
  derived data; a flag would create two operator modes for no benefit.
- ❌ Don't change the `ffmpeg.attempts` event name or its single-line shape
  — `worker.py:324` is a substring match.
- ❌ Don't add a separate "transcodes minutes" *vs* "copy minutes" split
  in the top-level KPI — that's what the `encoders` breakdown is for.
- ❌ Don't backfill historical rows from stderr scrapes. Forward-only.
- ❌ Don't duplicate the cause→category mapping in the daemon. It lives
  in `resources/processor/failures.py` next to the enums.
- ❌ Don't add a new "operator-facing" failure enum class — strings on
  the job row are sufficient. A third enum is one too many to maintain.
- ❌ Don't ffprobe the source file twice — MediaProcessor already has
  the source `MediaInfo` in scope. Re-probe only the destination.
- ❌ Don't compute resolution buckets in Python and persist the bucket
  string — persist raw width/height and bucket in SQL.
- ❌ Don't fold per-profile aggregation into the `requests` block.
  They answer different questions (workload sources vs encoder behaviour
  per profile) and merging hurts readability.
- ❌ Don't infer `request_source` from the path or the user-agent.
  Classify it explicitly at the entry-point handler.
- ❌ Don't use unbounded values as Prometheus labels (job_id, file path,
  error message, output filename). The cardinality skill rule from
  `.claude/skills/python-observability` is a hard line — violating it
  blows up Prometheus storage in production.
- ❌ Don't duplicate the in-memory `server.py` counter dicts and the
  Prometheus counters. Prometheus subsumes them.

---

## Prometheus Instrumentation Layer

Refactor the in-memory counters in `resources/daemon/server.py` (currently
keyed `(from_tier, to_tier, reason)` at lines 130-332) into
`prometheus_client` instruments, and expose them on the standard `/metrics`
endpoint in the Prometheus text exposition format. The DB columns +
`get_metrics()` SQL aggregates remain the source of truth for lifetime
operator-visible stats (the HTML dashboard); Prometheus becomes the
source of truth for **operational time-series** consumed by
Grafana/Alertmanager.

### Why both layers, not just one

- **DB aggregates** answer "since the beginning of time, what does my
  library look like?" — operator-quotable numbers (`bytes_saved_total`,
  `minutes_transcoded_total`) that must survive daemon restarts.
- **Prometheus** answers "what happened in the last 5 / 60 / 1440
  minutes, broken down by labels, with rate() and histogram_quantile()?"
  — alerting, throughput trends, p95 latency, failure-rate spikes.

The two views complement each other. The in-memory `(from_tier,
to_tier, reason)` dict in `server.py` does neither well — it's a
volatile counter with no time-series and no exposition format. The
refactor removes it.

### Endpoint convention (breaking change, documented)

The existing HTML dashboard at `GET /metrics` moves to `GET
/dashboard/metrics`. `GET /metrics` is reclaimed for the Prometheus
text exposition (industry convention; any scrape config expects it
there). `GET /api/metrics` (JSON) stays unchanged — it's the
machine-readable mirror of the HTML dashboard.

| Path                  | Format          | Audience                           |
| --------------------- | --------------- | ---------------------------------- |
| `/metrics`            | Prometheus text | Prometheus scraper / Grafana       |
| `/api/metrics`        | JSON            | The HTML dashboard + automation    |
| `/dashboard/metrics`  | HTML            | Operators (rendered by browser)    |

This is the **only** behavioural breaking change in this PRP — bump the
operator-doc rollout note in `docs/daemon.md` accordingly.

### Bounded-label cardinality budget

Per `python-observability` Pattern 6, every label set is enumerable
ahead of time:

| Label              | Cardinality cap | Source                                          |
| ------------------ | --------------- | ----------------------------------------------- |
| `encoder_backend`  | 7               | `qsv,vaapi,nvenc,videotoolbox,amf,software,copy`|
| `failure_category` | 6               | `config,source_media,hardware,disk,system,unknown` |
| `failure_cause`    | ~40             | Enumerated by `FfmpegFailureClass`+`FfmpegFailureCause` (bounded by code) |
| `request_source`   | 6               | `sonarr,radarr,webhook,cli,scan,unknown`        |
| `request_profile`  | ≤ 20            | Bounded by `profiles:` keys in `sma-ng.yml`     |
| `video_codec`      | ~15             | ffprobe codec names (bounded by ffmpeg)         |
| `audio_codec`      | ~15             | ffprobe audio codec names                       |
| `resolution_bucket`| 4               | `4k,1080p,720p,sd`                              |
| `audio_channels`   | ~6              | `1,2,6,8,other`                                 |
| `hdr`              | 4               | `sdr,hdr10,hdr10plus,dolby_vision`              |
| `node_id`          | ≤ cluster nodes | Bounded by deployment                           |

**Never** label by `job_id`, `path`, `error_message`, `output_path`,
`user_id`, or any free-form string. Those stay in structured logs
(see "Structured Logging Conventions" below), addressable via
`correlation_id`.

### Metric inventory

```python
# resources/daemon/metrics_prom.py — NEW module, single home for instruments

from prometheus_client import Counter, Histogram, Gauge

# ---------- Counters (monotonic; rate() in PromQL) ----------

JOBS_TOTAL = Counter(
    "sma_jobs_total",
    "Total jobs processed by terminal status",
    ["status", "encoder_backend", "request_source", "request_profile"],
)
# status ∈ {completed, failed, cancelled}

FAILURES_TOTAL = Counter(
    "sma_failures_total",
    "Job failures by operator category and raw cause",
    ["failure_category", "failure_cause", "encoder_backend"],
)

FALLBACK_TRANSITIONS_TOTAL = Counter(
    "sma_fallback_transitions_total",
    "Ladder-tier transitions (replaces the in-memory fallback_counters dict)",
    ["from_tier", "to_tier", "failure_class"],
)

BYTES_SAVED_TOTAL = Counter(
    "sma_bytes_saved_bytes_total",
    "Cumulative bytes saved (input - output, clamped to ≥ 0 per job)",
    ["encoder_backend"],
)
BYTES_GROWN_TOTAL = Counter(
    "sma_bytes_grown_bytes_total",
    "Cumulative bytes added (output - input, clamped to ≥ 0 per job)",
    ["encoder_backend"],
)
SECONDS_TRANSCODED_TOTAL = Counter(
    "sma_seconds_transcoded_total",
    "Cumulative source seconds transcoded",
    ["encoder_backend"],
)

# ---------- Histograms (latency + size distributions) ----------

JOB_DURATION = Histogram(
    "sma_job_duration_seconds",
    "Wall-clock per job from started_at to terminal status",
    ["encoder_backend", "status"],
    buckets=(5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200),
)

SOURCE_DURATION = Histogram(
    "sma_source_duration_seconds",
    "Source container duration per job",
    ["encoder_backend"],
    buckets=(60, 300, 600, 1200, 1800, 2700, 3600, 5400, 7200, 10800),
)

COMPRESSION_RATIO = Histogram(
    "sma_compression_ratio",
    "output_size / input_size per job (1.0 = same; 0.5 = halved)",
    ["encoder_backend"],
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0, 1.25, 2.0),
)

# ---------- Media-characteristics counters ----------

MEDIA_SOURCE_TOTAL = Counter(
    "sma_media_source_total",
    "Source-stream characteristics, one increment per completed job",
    ["video_codec", "resolution_bucket", "audio_codec", "audio_channels", "hdr"],
)
MEDIA_DESTINATION_TOTAL = Counter(
    "sma_media_destination_total",
    "Destination-stream characteristics, one increment per completed job",
    ["video_codec", "resolution_bucket", "audio_codec", "audio_channels", "hdr"],
)
# CARDINALITY NOTE: this is the highest-risk metric. Combined upper bound
# is ~15 * 4 * 15 * 6 * 4 = 21,600 series per metric. In practice the
# realised set is < 200 because most operators run a narrow codec mix.
# Still: if a future change adds an unbounded label (e.g. exact resolution
# instead of bucket), Prometheus storage will explode. Test fixture must
# verify the label set is exactly the five names above.

# ---------- Gauges (saturation, current state) ----------

JOBS_IN_FLIGHT = Gauge(
    "sma_jobs_in_flight",
    "Current count of running jobs across all workers",
    ["node_id"],
)
QUEUE_DEPTH = Gauge(
    "sma_queue_depth",
    "Current pending+queued job count",
    ["node_id"],
)
```

### Integration points

```yaml
NEW MODULE:
  resources/daemon/metrics_prom.py:
    - Defines every Counter/Histogram/Gauge (single import surface)
    - Exposes a `record_job_completion(job_row)` helper that the worker
      calls after `complete_job` lands. The helper does ALL the
      `.labels(...).inc()/.observe()/.set()` plumbing in one place so
      business code stays clean (per python-observability "separate
      concerns" rule).
    - Exposes `record_job_failure(job_row, failure_category, failure_cause)`
      with the same shape.
    - Exposes `record_fallback_transition(from_tier, to_tier, failure_class)`
      called from the existing `fallback_counter_callback` site.

ROUTES (resources/daemon/routes.py:91-92):
  - "/metrics"           → Prometheus text exposition (NEW handler
                            _get_metrics_prom; uses
                            prometheus_client.generate_latest)
  - "/api/metrics"       → unchanged (JSON dashboard data)
  - "/dashboard/metrics" → MOVED from previous /metrics (HTML dashboard)

DEPENDENCY:
  - Add `prometheus-client>=0.20` to pyproject.toml
  - No structlog migration required for this PRP — see the next section.

REMOVE:
  resources/daemon/server.py:130-332:
    - fallback_counters dict + lock + increment_fallback_counter + fallback_summary
    - by_backend_counters (proposed Task 7) — never added; Prometheus subsumes
    - failure_category_counters (proposed Task 9.7) — never added; Prometheus subsumes
    - request_source_counters (proposed Task 11.8) — never added; Prometheus subsumes
  resources/daemon/handler.py:161-164:
    - /health payload no longer carries `fallback`, `by_backend`,
      `failure_categories`, `request_sources` blocks; operators consume
      them via /metrics instead.
```

### Structured Logging Conventions (paired)

The PRP already emits the `ffmpeg.attempts` JSON line at
`mediaprocessor.py:3523-3541`. Make the *same* completion event the
single emission point that:

1. Increments the Prometheus counters (via
   `metrics_prom.record_job_completion`).
2. Writes the structured log line with the same field set, plus a
   `correlation_id` field bound from `set_job_id` (already used in
   `resources/daemon/context.py`).

```python
# Worker completion path — illustrative
logger.info(
    "job.completed",
    correlation_id=job_id,
    encoder_backend=row.encoder_backend,
    encoder_name=row.encoder_name,
    request_source=row.request_source,
    request_profile=row.request_profile,
    failure_category=None,
    bytes_saved=row.input_size_bytes - row.output_size_bytes,
    source_duration_seconds=row.source_duration_seconds,
    duration_ms=elapsed_ms,
)
metrics_prom.record_job_completion(row)
```

Failure path mirrors the same structure with `failure_category` and
`failure_cause` set; `metrics_prom.record_job_failure(row, ...)` runs
in lieu of `record_job_completion`.

**Do not** add structlog wholesale in this PRP — it's a sibling
refactor with its own risk surface. The existing `resources/log.py`
loggers already produce single-line JSON (per `CLAUDE.md`'s
"single-line log records" rule). What this PRP adds is **field
consistency** at the completion site, not a logging-stack swap.

### Validation additions

```bash
# Prometheus exposition format validation:
curl -s localhost:8585/metrics | promtool check metrics
# (promtool is part of Prometheus; CI runs it via the prometheus/promtool
#  Docker image — see docs/deployment.md addition)

# Label cardinality smoke test (CI):
source venv/bin/activate && python -m pytest tests/test_metrics_prom.py::test_label_cardinality_bounded -q
# The test asserts each metric's label set is exactly the names declared
# in metrics_prom.py — guards against silent drift adding a new label.

# Scrape smoke test:
curl -s localhost:8585/metrics | grep -E '^sma_(jobs|failures|bytes|seconds)_'
# Returns one HELP/TYPE/value triplet per declared metric.
```

### Validation gates

- [ ] `/metrics` returns Prometheus text format with `Content-Type:
      text/plain; version=0.0.4; charset=utf-8`
- [ ] `promtool check metrics` passes (CI)
- [ ] Every declared metric appears with at least HELP + TYPE on a
      freshly started daemon (zero-state)
- [ ] Label cardinality test: each metric's label set matches
      `metrics_prom.py` exactly; no metric uses `job_id`, `path`,
      `error_message`, `output_path`, or any free-form string as a label
- [ ] Removing the in-memory counters in `server.py` does not break
      any existing test — paired with Tasks 7 / 9.7 / 11.8 being
      *not done* (they were preempted by this refactor)
- [ ] `docs/daemon.md` documents the `/metrics` → `/dashboard/metrics`
      rename as a breaking change with the rationale (Prometheus
      convention) and a Grafana-dashboard JSON reference
- [ ] Grafana dashboard JSON committed under `docs/dashboards/sma.json`
      pinned to the metric names above (regression guard for renames)
- ❌ Don't compute bytes-saved in Python after fetching all job rows —
  do it in SQL. The Postgres path already does this for `compression_pct`.

---

**Confidence score**: 8 / 10. The implementation path is fully evidenced
from the codebase, but two risks lower the score:

- SQLite `get_metrics` was a stub; bringing it to parity may surface
  pre-existing SQLite-mode operator expectations not captured in tests.
- The `complete_job(output_size=...)` plumbing is a latent bug — any
  hidden caller that relied on the missing value (none found in
  ripgrep, but worth re-checking under load) could regress.
