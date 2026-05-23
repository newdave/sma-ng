# Task Breakdown: Expanded Metrics — Backends, Savings, Failures, Media, Requests, Profiles + Prometheus

> **STATUS: IN-FLIGHT — generated 2026-05-22**

**Source PRP**: [docs/prps/metrics-expansion.md](../prps/metrics-expansion.md)
**Feature**: New daemon aggregates on `/api/metrics`: `bytes_saved_total`, `bytes_grown_total`,
`minutes_transcoded_total` KPIs, plus `encoders:`, `failures:`, `media: {source, destination}`, `requests:`,
and `profiles:` breakdowns. Mirrored on `/health` and rendered on the dashboard. Closes the latent
`complete_job(output_size=...)` plumbing gap.
**Validation**: see PRP "Validation Loop" — `mise run dev:lint`, `mise run test:lint`,
`source venv/bin/activate && python -m pytest tests/test_daemon.py tests/test_handler.py tests/test_worker.py tests/test_metrics.py tests/test_metrics_aggregates.py -q`.

---

## Task 1: Schema columns + migration

### T1.1 Add three `jobs` columns inline (SQLite + Postgres CREATE)

- **Title**: Append `encoder_backend TEXT`, `encoder_name TEXT`, `source_duration_seconds REAL/DOUBLE PRECISION`
  to both `CREATE TABLE jobs` definitions.
- **Files**: `resources/daemon/db.py:104-130` (SQLite CREATE), `resources/daemon/db.py:607-696` (Postgres CREATE)
- **Effort**: S
- **Dependencies**: —
- **Given-When-Then**:
  - Given a fresh database on either backend,
  - When the daemon initialises the schema,
  - Then `PRAGMA table_info(jobs)` / `information_schema.columns` lists the three new columns with the documented
    types and all are nullable.

### T1.2 Idempotent migration for existing databases

- **Title**: Extend the PRAGMA-introspection block (SQLite) and the `ADD COLUMN IF NOT EXISTS` block (Postgres) so
  pre-existing `jobs` tables gain the three columns without data loss.
- **Files**: `resources/daemon/db.py:125-130` (SQLite PRAGMA migrator), `resources/daemon/db.py:753-755` (Postgres
  idempotent ALTERs)
- **Effort**: S
- **Dependencies**: T1.1
- **Given-When-Then**:
  - Given a pre-existing `jobs` table without the new columns,
  - When the daemon starts twice in a row,
  - Then the first start adds the columns, the second is a no-op, and no rows are lost.
- **Gotcha**: SQLite cannot use `ADD COLUMN IF NOT EXISTS`; reuse the existing `PRAGMA table_info(jobs)` pattern.

### T1.3 Round-trip test for the new columns

- **Title**: Create `tests/test_metrics_aggregates.py` with a SQLite + fake-psycopg2 round-trip confirming the three
  columns persist and read back correctly.
- **Files**: `tests/test_metrics_aggregates.py` (new); mirror fixture style from `tests/test_daemon.py:926-987`.
- **Effort**: M
- **Dependencies**: T1.1, T1.2
- **Given-When-Then**:
  - Given a row inserted with `encoder_backend="qsv"`, `encoder_name="hevc_qsv"`, `source_duration_seconds=1801.23`,
  - When the row is fetched on both backends,
  - Then all three values are preserved with type-correct round-trips (string, string, float).

---

## Task 2: VideoToolbox hw_prefix gap

### T2.1 Backfill `hw_prefix = "videotoolbox"` on VideoToolbox encoders

- **Title**: Audit `h264_videotoolbox` / `hevc_videotoolbox` classes and add the missing `hw_prefix` attribute so the
  resolver in Task 3 stays SSoT-clean (no special cases).
- **Files**: `converter/avcodecs.py:1394-1437`, cross-check `converter/avcodecs.py:704-709`,
  `converter/avcodecs.py:1452`, `converter/avcodecs.py:1495-1497`, `converter/avcodecs.py:1527-1530`,
  `converter/avcodecs.py:1740-1743`, `converter/avcodecs.py:1830`, `converter/avcodecs.py:1842-1843`
- **Effort**: S
- **Dependencies**: —
- **Given-When-Then**:
  - Given a `VideoToolboxEncoder` subclass instance,
  - When `getattr(enc, "hw_prefix", None)` is read,
  - Then it returns `"videotoolbox"`; the equivalent QSV/VAAPI/NVENC/AMF classes still return their existing prefixes.

### T2.2 Codec test for hw_prefix coverage

- **Title**: Assert every hardware `VideoCodec` subclass (qsv/vaapi/nvenc/videotoolbox/amf) exposes its `hw_prefix`;
  add the videotoolbox row that's missing today.
- **Files**: `tests/test_avcodecs.py`
- **Effort**: S
- **Dependencies**: T2.1
- **Given-When-Then**:
  - Given a parametrised list of hardware encoder class names,
  - When the test inspects each class attribute,
  - Then `hw_prefix` is non-empty and matches the expected backend bucket.

---

## Task 3: Encoder / backend resolver + attempt log emission

### T3.1 Add `_resolve_encoder_backend` helper

- **Title**: Implement `(encoder_name, encoder_backend)` resolver: `copy` → `("copy", "copy")`; otherwise
  `(vcodec, vencoder.hw_prefix or "software")`. Place near where `Converter.encoder(vcodec)` is called.
- **Files**: `resources/mediaprocessor.py:1834-1837` (Converter.encoder call site)
- **Effort**: S
- **Dependencies**: T2.1
- **Given-When-Then**:
  - Given `vcodec == "copy"` (with any encoder object),
  - When the resolver runs,
  - Then it returns `("copy", "copy")` without consulting `hw_prefix`; for `hevc_qsv` it returns
    `("hevc_qsv", "qsv")`; for `libx265` it returns `("libx265", "software")`.
- **Gotcha**: check the literal string `"copy"` BEFORE reading `hw_prefix` (PRP "Known Gotchas").

### T3.2 Emit the four new fields in `_emit_attempt_log`

- **Title**: Extend the success-path payload with `encoder_name`, `encoder_backend`, `output_size_bytes` (stat the
  output path; tolerate missing file), and `source_duration_seconds` (`info.format.duration`, may be `None`).
- **Files**: `resources/mediaprocessor.py:3523-3541` (`_emit_attempt_log`), cross-check
  `resources/mediaprocessor.py:3386-3494` (attempt-ladder context), `resources/mediaprocessor.py:1361`,
  `resources/mediaprocessor.py:1428`, `resources/mediaprocessor.py:1651`, `resources/mediaprocessor.py:1672`
- **Effort**: M
- **Dependencies**: T3.1
- **Given-When-Then**:
  - Given a successful QSV transcode of a 30-minute source,
  - When `_emit_attempt_log` writes the `ffmpeg.attempts` line,
  - Then the JSON contains `encoder_backend="qsv"`, `encoder_name="hevc_qsv"`,
    `output_size_bytes>0`, and `source_duration_seconds≈1800.0`; on `vcodec="copy"` the payload still includes the
    four fields with `encoder_backend="copy"`.
- **Critical**: keep the event name `"ffmpeg.attempts"` and the single-line JSON shape — `worker.py:324` does a
  literal substring match (PRP "Known Gotchas").

### T3.3 Unit tests for the resolver + emitter

- **Title**: Cover `copy` short-circuit, hw_prefix passthrough, software fallback, and the success-payload schema.
- **Files**: `tests/test_mediaprocessor.py`
- **Effort**: M
- **Dependencies**: T3.1, T3.2
- **Given-When-Then**:
  - Given a stubbed `_emit_attempt_log` call with each of the seven backend buckets,
  - When the emitted line is parsed,
  - Then `encoder_backend` matches the bucket and `output_size_bytes`/`source_duration_seconds` round-trip as
    int/float (or `None` without raising).

---

## Task 4: Worker ingestion + complete_job extension

### T4.1 Parse the four new fields in `_parse_ffmpeg_attempts_line`

- **Title**: Extract `encoder_name`, `encoder_backend`, `output_size_bytes`, `source_duration_seconds` from the
  attempts-line JSON and return them alongside the existing fields.
- **Files**: `resources/daemon/worker.py:353-396` (`_parse_ffmpeg_attempts_line`), cross-check
  `resources/daemon/worker.py:301-310` (existing duration parser handoff), `resources/daemon/worker.py:324`
  (substring match — do not change)
- **Effort**: S
- **Dependencies**: T3.2
- **Given-When-Then**:
  - Given a real attempt line emitted by Task 3,
  - When `_parse_ffmpeg_attempts_line` consumes it,
  - Then it returns a structure containing all four new fields; missing fields default to `None` (older builds).

### T4.2 Pass the four arguments at the `complete_job` call site

- **Title**: Thread `output_size`, `encoder_backend`, `encoder_name`, `source_duration_seconds` into the
  `complete_job(...)` call. Fallback to `os.path.getsize(output_path)` when the attempts line lacked
  `output_size_bytes` (older builds).
- **Files**: `resources/daemon/worker.py:179-186`, `resources/daemon/worker.py:274`
- **Effort**: S
- **Dependencies**: T4.1, T5.1 (signature must exist before this call lands)
- **Given-When-Then**:
  - Given a successful job whose attempts line carries the four fields,
  - When the worker finalises the job,
  - Then `complete_job` is invoked with non-`None` values for all four arguments and the row reflects them.
- **Critical** (per PRP): the latent bug is here — `complete_job(output_size=...)` was never being passed; without
  this fix `bytes_saved_total` is permanently zero.

### T4.3 Worker tests for parsing + completion call

- **Title**: Verify `_parse_ffmpeg_attempts_line` surfaces the new fields and `complete_job` is invoked with all
  four arguments under happy-path and missing-field-fallback scenarios.
- **Files**: `tests/test_worker.py`
- **Effort**: M
- **Dependencies**: T4.1, T4.2
- **Given-When-Then**:
  - Given a mocked `complete_job` and a synthetic attempts line missing `output_size_bytes`,
  - When the worker finishes,
  - Then `complete_job` is called with `output_size = os.path.getsize(output_path)` and the other three new fields
    forwarded from the attempts line.

---

## Task 5: `complete_job` signature extension (both backends)

### T5.1 Extend SQLite + Postgres `complete_job` signatures

- **Title**: Add `encoder_backend=None`, `encoder_name=None`, `source_duration_seconds=None` to both signatures and
  include them in the UPDATE statements.
- **Files**: `resources/daemon/db.py:234-240` (SQLite), `resources/daemon/db.py:984-990` (Postgres)
- **Effort**: S
- **Dependencies**: T1.1, T1.2
- **Given-When-Then**:
  - Given a job row in `running` state,
  - When `complete_job(job_id, output_size=N, encoder_backend="qsv", encoder_name="hevc_qsv",
    source_duration_seconds=1800.0)` is called on either backend,
  - Then the row transitions to `completed` with all four columns populated.

### T5.2 Lifecycle test on both backends

- **Title**: Extend the existing complete_job lifecycle test to assert the four new arguments persist.
- **Files**: `tests/test_daemon.py:1284-1303`
- **Effort**: S
- **Dependencies**: T5.1
- **Given-When-Then**:
  - Given a queued job,
  - When the test drives `complete_job(...)` with all four new arguments,
  - Then `SELECT encoder_backend, encoder_name, source_duration_seconds, output_size_bytes FROM jobs WHERE id=?`
    returns the inserted values on both SQLite and the fake-psycopg2 Postgres harness.

---

## Task 6: `get_metrics` aggregates

### T6.1 Extend Postgres `get_metrics` with the three sums + encoders breakdown

- **Title**: Add `bytes_saved_total`, `bytes_grown_total`, `minutes_transcoded_total` (sign-flipped via
  `GREATEST(...)`, divide by 60 for minutes) to the main KPI query; add a sibling `GROUP BY encoder_backend` query
  for the `encoders:` block.
- **Files**: `resources/daemon/db.py:1073-1212`
- **Effort**: L
- **Dependencies**: T1.1, T1.2, T5.1
- **Given-When-Then**:
  - Given a synthetic mix of `completed` rows across `qsv`, `vaapi`, `software`, `copy` with at least one
    output-larger-than-input row,
  - When `get_metrics()` runs on Postgres,
  - Then `kpis.bytes_saved_total` equals the SQL sum, `kpis.bytes_grown_total > 0`, `minutes_transcoded_total`
    matches `SUM(source_duration_seconds)/60`, and `encoders[*]` lists one row per backend with matching subtotals.
- **Critical**: do the math in SQL, not Python-after-fetch (PRP "Anti-Patterns").

### T6.2 Lift SQLite `get_metrics` from stub to parity

- **Title**: Replace the `{"available": False}` stub with at minimum the three sums + the encoders breakdown so
  SQLite-mode operators see the new aggregates. `compression_pct` may remain unavailable if heavier.
- **Files**: `resources/daemon/db.py:308`
- **Effort**: M
- **Dependencies**: T1.1, T1.2, T5.1
- **Given-When-Then**:
  - Given a SQLite-backed daemon with seeded `completed` rows,
  - When `get_metrics()` returns,
  - Then it surfaces the three new KPI sums and a populated `encoders:` block; the response shape is
    JSON-compatible with the Postgres path.

### T6.3 Aggregate correctness tests (mixed dataset)

- **Title**: Seed mixed-backend rows including a negative delta and a `NULL source_duration_seconds`; assert
  bucket sums, `bytes_grown_total > 0`, and that NULL durations contribute zero minutes without raising.
- **Files**: `tests/test_daemon.py:3641-3711` (extension) and/or `tests/test_metrics_aggregates.py`
- **Effort**: M
- **Dependencies**: T6.1, T6.2
- **Given-When-Then**:
  - Given rows with `encoder_backend in {qsv, software, copy}` and one row with `output_size_bytes >
    input_size_bytes`,
  - When `get_metrics()` runs on each backend,
  - Then `kpis.bytes_saved_total` matches the net delta, `kpis.bytes_grown_total` matches the sign-flipped sum of
    negative deltas, and `encoders["copy"]["count"]` reflects only the copy rows.

---

## Task 7: In-memory `by_backend` counters + `/health` surface

### T7.1 Add `by_backend_counters` to `Server`

- **Title**: Mirror `fallback_counters`: add `self.by_backend_counters: dict[str, int]`, a lock, an
  `increment_by_backend(backend)` method, and a `by_backend_summary()` snapshot accessor.
- **Files**: `resources/daemon/server.py:130-132` (counter dict + lock), `resources/daemon/server.py:319-332`
  (increment + summary methods)
- **Effort**: S
- **Dependencies**: —
- **Given-When-Then**:
  - Given a fresh `Server`,
  - When `increment_by_backend("qsv")` is called three times and `increment_by_backend("vaapi")` once,
  - Then `by_backend_summary() == {"qsv": 3, "vaapi": 1}` and the operation is thread-safe under the lock.

### T7.2 Wire the worker-pool callback

- **Title**: Plumb a `by_backend_counter_callback` parallel to `fallback_counter_callback` from `Server` into the
  worker pool init. Call it once per completed job after `complete_job` succeeds.
- **Files**: `resources/daemon/server.py:148` (worker pool init), `resources/daemon/worker.py:179-186` (call site)
- **Effort**: S
- **Dependencies**: T4.2, T7.1
- **Given-When-Then**:
  - Given a worker completes a QSV job,
  - When `complete_job` returns,
  - Then the callback fires exactly once with `backend="qsv"` and the server's `by_backend_counters["qsv"]`
    increments by 1.

### T7.3 Expose `by_backend` on `/health`

- **Title**: Inject `by_backend_summary()` into the `/health` payload (alongside or under the existing `fallback`
  block) for since-process-start visibility without a DB read.
- **Files**: `resources/daemon/handler.py:153-182`
- **Effort**: S
- **Dependencies**: T7.1, T7.2
- **Given-When-Then**:
  - Given two completed jobs (one qsv, one software) since process start,
  - When `GET /health` is called,
  - Then the response's `by_backend` field equals `{"qsv": 1, "software": 1}`.

### T7.4 `/api/metrics` passthrough confirmation

- **Title**: Verify `/api/metrics` (no handler logic change needed) returns the extended `kpis` + `encoders` dict
  directly from `job_db.get_metrics()`.
- **Files**: `resources/daemon/handler.py:574-593`
- **Effort**: S
- **Dependencies**: T6.1, T6.2
- **Given-When-Then**:
  - Given `job_db.get_metrics()` returns the extended dict,
  - When `GET /api/metrics` is called,
  - Then the response JSON contains the three new KPI keys and the `encoders` block unmodified.

### T7.5 Handler + server tests

- **Title**: Extend `tests/test_handler.py` / `tests/test_metrics.py` (`_METRICS_FIXTURE`) to assert `/health`
  carries `by_backend` and `/api/metrics` carries the new keys. Add a server-side test for the increment +
  summary path.
- **Files**: `tests/test_handler.py`, `tests/test_metrics.py:18`, `tests/test_metrics.py:53` (fixture extension)
- **Effort**: M
- **Dependencies**: T7.3, T7.4
- **Given-When-Then**:
  - Given the fixture extended with `encoders` and the new KPIs,
  - When the handler responds,
  - Then the JSON shape is preserved and `/health` exposes the per-backend counts.

---

## Task 8: Dashboard + docs

### T8.1 Add KPI label mappings + Encoders card

- **Title**: Map `bytes_saved_total`, `bytes_grown_total`, `minutes_transcoded_total` to human labels (auto-render
  picks them up). Add an "Encoders" card mirroring the Nodes card — JSON-driven, do not hard-code the backend list.
- **Files**: `resources/metrics.html:203`, `resources/metrics.html:267` (KPI label binding),
  `resources/metrics.html:390-408` (Nodes card to mirror)
- **Effort**: M
- **Dependencies**: T6.1, T6.2
- **Given-When-Then**:
  - Given a `/api/metrics` response containing `encoders: {qsv: {...}, software: {...}}`,
  - When the dashboard renders,
  - Then an "Encoders" card displays one row per present backend with `count / bytes_saved (GiB) / minutes`, and
    backends absent from the response are omitted.

### T8.2 Update operator docs in the same commit

- **Title**: Document the three new `kpis.*` keys, the `encoders:` block, and the `/health by_backend` field in
  `docs/metrics.md`; update the `/api/metrics` description at `docs/daemon.md:64-65`, `:157`, `:285`; add the
  `/api/metrics` path entry to `docs/openapi.yaml` (currently missing entirely).
- **Files**: `docs/metrics.md`, `docs/daemon.md`, `docs/openapi.yaml`
- **Effort**: M
- **Dependencies**: T6.1, T6.2, T7.3
- **Given-When-Then**:
  - Given an operator reads `docs/metrics.md`,
  - When they look up "bytes saved",
  - Then they find the JSON shape, sign convention (net allowed negative; growth surfaced separately), and the
    `/health` since-process-start counterpart.

### T8.3 Dashboard smoke check

- **Title**: Manual + automated check that the Encoders card renders with synthetic data and degrades cleanly when
  `encoders: {}`.
- **Files**: `resources/metrics.html` (visual), `tests/test_metrics.py` (assertion on `_METRICS_FIXTURE`)
- **Effort**: S
- **Dependencies**: T8.1
- **Given-When-Then**:
  - Given an empty `encoders` dict,
  - When the dashboard renders,
  - Then the card is hidden or shows an empty-state row without JS errors.

---

## Task 9: Failure categorization + plumbing

### T9.1 Add `FAILURE_CATEGORY_MAP` + `categorize_failure` next to the enums

- **Title**: Single source of truth mapping every `FfmpegFailureClass` and `FfmpegFailureCause` value (plus four
  worker sentinels: `path_missing`, `invalid_args`, `process_failed`, `exception`) to one of `config`,
  `source_media`, `hardware`, `disk`, `system`, `unknown`.
- **Files**: `resources/processor/failures.py:26-39, 168-198`
- **Effort**: M
- **Dependencies**: —
- **Given-When-Then**:
  - Given any defined enum value or worker sentinel,
  - When `categorize_failure(value)` is called,
  - Then it returns a non-`unknown` operator category per the table in the PRP "Task 12" pseudocode.
- **Mapping table**: see PRP "Task 12" — `hardware` covers device/init/QSV/NVENC/VAAPI codes; `config` covers
  profile/level/HDR/strict-flag codes; `source_media` covers input/PTS/audio/subtitle codes; `disk` covers
  `DISK_FULL` and `PERMISSION_DENIED`; `system` is the catch-all for `RUNTIME_ERROR`, `OTHER`, and worker sentinels.

### T9.2 Drift-guard test

- **Title**: Iterate both enums and assert every value maps to a non-`unknown` category.
- **Files**: `tests/test_failure_categorization.py` (new)
- **Effort**: S
- **Dependencies**: T9.1
- **Given-When-Then**:
  - Given an unmapped enum value is later added to `failures.py`,
  - When `pytest tests/test_failure_categorization.py` runs,
  - Then the test fails with a clear message naming the unmapped value.

### T9.3 Persist `failure_category` + `failure_cause` columns

- **Title**: Add two columns to the `jobs` table on both backends (additive ALTER pattern mirroring T1.x).
- **Files**: `resources/daemon/db.py:104-130` (SQLite), `:607-696, 753-754` (Postgres)
- **Effort**: S
- **Dependencies**: T1.2 (so the migrator pattern is in place)
- **Given-When-Then**:
  - Given an existing database upgrades to the new daemon,
  - When schema init runs,
  - Then `failure_category TEXT` and `failure_cause TEXT` exist on `jobs`, nullable, no data loss.

### T9.4 Extend `fail_job` signature on both backends

- **Title**: Add `failure_category=None, failure_cause=None` to `fail_job` and persist on the UPDATE.
- **Files**: `resources/daemon/db.py:258` (SQLite), `:1011` (Postgres)
- **Effort**: S
- **Dependencies**: T9.3
- **Given-When-Then**:
  - Given a job fails,
  - When `fail_job(job_id, error, failure_category=..., failure_cause=...)` is called,
  - Then both columns land on the row and the existing free-text `error` is unaffected.

### T9.5 Worker call sites classify before calling `fail_job`

- **Title**: Each of the four `fail_job` call sites passes the right `(category, cause)` pair.
- **Files**: `resources/daemon/worker.py:144, 150, 191, 197`
- **Effort**: M
- **Dependencies**: T9.1, T9.4
- **Given-When-Then**:
  - Given the worker hits the "path does not exist" branch,
  - When it calls `fail_job`,
  - Then `failure_category="system"`, `failure_cause="path_missing"`.
  - Given a conversion subprocess exits non-zero **and** the parsed `ffmpeg.attempts` line carried a final-attempt
    `failure_class`,
  - When the worker calls `fail_job`,
  - Then `failure_cause` is that `failure_class` value and `failure_category` is its resolved category
    (e.g. `hardware` for QSV alignment errors) — **not** the generic `process_failed` sentinel.

### T9.6 `get_metrics` failure breakdown (both backends)

- **Title**: GROUP BY failure_category with a modal failure_cause per bucket.
- **Files**: `resources/daemon/db.py:308` (SQLite stub lift), `:1073-1212` (Postgres extension)
- **Effort**: M
- **Dependencies**: T9.3
- **Given-When-Then**:
  - Given failed jobs seeded with mixed categories,
  - When `get_metrics()` returns,
  - Then `metrics["failures"][category]["count"]` matches the SQL count and `top_cause` is the modal
    `failure_cause` within that bucket (or `null` if all rows in the bucket have NULL cause).
- **SQLite quirk**: no `mode() WITHIN GROUP`; run a per-category `ORDER BY count DESC LIMIT 1` subquery
  (N=6, dataset small).

### T9.7 In-memory `failure_category_counters` on `server.py`

- **Title**: Mirror the `by_backend_counters` pattern for failure categories so `/health` exposes since-process-start counts.
- **Files**: `resources/daemon/server.py:130-132, 148, 319-332`
- **Effort**: S
- **Dependencies**: T7.1 (paired with the `by_backend` lane)
- **Given-When-Then**:
  - Given a job fails with category `hardware`,
  - When the daemon increments the counter and `/health` is queried,
  - Then `payload["failure_categories"]["hardware"]` is ≥ 1.

### T9.8 Dashboard "Failures" card

- **Title**: Render the failures breakdown next to the encoders card, mirroring the Nodes card structure.
- **Files**: `resources/metrics.html:203, 390-408`
- **Effort**: S
- **Dependencies**: T9.6, T8.1
- **Given-When-Then**:
  - Given the metrics response contains a populated `failures` block,
  - When the dashboard renders,
  - Then each category row shows `count` and `top_cause`, with an empty-state row when the block is empty.

### T9.9 Docs

- **Title**: Document the failure category enum, the cause→category mapping table, and the new JSON keys.
- **Files**: `docs/metrics.md`, `docs/openapi.yaml`, `docs/daemon.md`
- **Effort**: S
- **Dependencies**: T9.6
- **Given-When-Then**:
  - Given an operator reads `docs/metrics.md`,
  - When they look up "failures",
  - Then they find the six-category enum, examples of which raw causes land in each, and the `/health`
    since-process-start counterpart.

---

## Task 10: Source + destination media characteristics

### T10.1 Add 12 media columns to `jobs`

- **Title**: Append `source_video_codec`, `source_video_width`, `source_video_height`, `source_audio_codec`,
  `source_audio_channels`, `source_hdr`, plus the mirrored six `dest_*` columns. Apply the T1.2 migrator
  pattern on both backends.
- **Files**: `resources/daemon/db.py:104-130`, `:607-696, 753-754`
- **Effort**: S
- **Dependencies**: T1.2
- **Given-When-Then**: Given an upgrading database, when schema init runs, then all 12 columns exist on both
  backends, nullable, no data loss.

### T10.2 Extract HDR classifier to a `MediaInfo` helper

- **Title**: Move (or extract) the HDR classifier already used inside `mediaprocessor.py` into a small helper
  callable on `MediaInfo` so it can run against both source and destination probes from one site.
- **Files**: `converter/ffmpeg.py:146` (color block), wherever the existing classifier lives in `mediaprocessor.py`
- **Effort**: S
- **Dependencies**: —
- **Given-When-Then**: Given a `MediaInfo` parsed from ffprobe, when `info.classify_hdr()` is called, then it
  returns one of `sdr`/`hdr10`/`hdr10plus`/`dolby_vision` using the existing logic.

### T10.3 Destination ffprobe + payload extension

- **Title**: In MediaProcessor, immediately after a successful encode and before `_emit_attempt_log`, run one
  ffprobe on the output file and build a `media: {source, destination}` payload. Catch failures, log WARNING,
  leave destination keys as `None`.
- **Files**: `resources/mediaprocessor.py:3523-3541`
- **Effort**: M
- **Dependencies**: T10.2
- **Given-When-Then**: Given a successful transcode, when `ffmpeg.attempts` is emitted, then the line carries a
  `media` object with both source (from the pre-existing `MediaInfo`) and destination (from the post-probe);
  given a destination probe that raises, the success path is unaffected and destination keys are `None`.

### T10.4 Worker plumbing — parse + persist media payload

- **Title**: Extend `_parse_ffmpeg_attempts_line` to pull the 12 media fields out of the payload and pass
  them to `complete_job`.
- **Files**: `resources/daemon/worker.py:353-396`, `:179-186`
- **Effort**: S
- **Dependencies**: T10.1, T10.3, T5.1
- **Given-When-Then**: Given a successful job whose attempts line carries `media: {...}`, when the worker
  completes, then the 12 columns on the `jobs` row reflect the payload.

### T10.5 `get_metrics` media block + SQL resolution bucketing

- **Title**: Extend `get_metrics` on both backends with a `media: {source, destination}` block. Resolution
  buckets are computed via `CASE WHEN height >= 2160 THEN '4k' WHEN height >= 1080 THEN '1080p' WHEN
  height >= 720 THEN '720p' ELSE 'sd' END`.
- **Files**: `resources/daemon/db.py:308` (SQLite), `:1073-1212` (Postgres)
- **Effort**: M
- **Dependencies**: T10.1
- **Given-When-Then**: Given seeded jobs with mixed media characteristics, when `get_metrics()` runs, then
  `metrics["media"]["source"]["video_codec"]`, `resolution_bucket`, `audio_codec`, `audio_channels`, `hdr`
  sums match the seed data — and the destination block mirrors that shape.

### T10.6 Boundary + copy-stream tests

- **Title**: Tests for the SQL resolution-bucket boundaries (heights 2160/2159/1080/720/719) and for the
  copy-stream invariant (source and destination codec/resolution/channels match exactly).
- **Files**: `tests/test_daemon.py`, `tests/test_mediaprocessor.py`
- **Effort**: S
- **Dependencies**: T10.5
- **Given-When-Then**: see PRP Final Validation Checklist boundary items.

### T10.7 Dashboard cards for source + destination

- **Title**: Render "Source Library" and "Output Library" cards on the dashboard, each containing the five
  sub-breakdowns. Mirror the existing Nodes card.
- **Files**: `resources/metrics.html:203, 390-408`
- **Effort**: S
- **Dependencies**: T10.5
- **Given-When-Then**: Given a populated `media` block, when the dashboard renders, then both cards show one
  row per sub-breakdown value; given an empty block, the cards show an empty-state row without JS errors.

---

## Task 11: Request source + per-profile attribution

### T11.1 Add `request_source` + `request_profile` columns

- **Title**: Append both columns to the `jobs` table on both backends using the T1.2 migrator pattern.
- **Files**: `resources/daemon/db.py:104-130`, `:607-696, 753-754`
- **Effort**: S
- **Dependencies**: T1.2
- **Given-When-Then**: Given an upgrading database, then both columns exist and are nullable.

### T11.2 Extend `add_job` signature on both backends

- **Title**: Append keyword-only `request_source=None, request_profile=None` to `add_job(...)`. INSERT writes them.
- **Files**: `resources/daemon/db.py:174` (SQLite), `:853` (Postgres)
- **Effort**: S
- **Dependencies**: T11.1
- **Given-When-Then**: Given a caller passes `request_source="radarr", request_profile="4k"`, when the row lands
  in `jobs`, then both columns are populated; given the caller omits them, both are NULL.

### T11.3 Classify at the two webhook handler call sites

- **Title**: Sonarr branch passes `request_source="sonarr"`, Radarr branch passes `request_source="radarr"`,
  generic webhook passes `"webhook"`. Each also passes the resolved profile name already in scope from routing.
- **Files**: `resources/daemon/handler.py:1274, 1339`
- **Effort**: S
- **Dependencies**: T11.2
- **Given-When-Then**: Given a sonarr-shaped POST body, when the job lands, then `request_source="sonarr"` and
  `request_profile` matches the routed profile.

### T11.4 Classify at the direct `/jobs` POST site (CLI/API)

- **Title**: Default to `request_source="cli"`; allow override via `X-SMA-Request-Source` header for callers
  that supply it (e.g. external automation that wants to label itself).
- **Files**: `resources/daemon/handler.py` (the direct POST /jobs path — same neighbourhood as 1339)
- **Effort**: S
- **Dependencies**: T11.2
- **Given-When-Then**: Given a direct `POST /jobs` with no override header, then `request_source="cli"`;
  given the same POST with `X-SMA-Request-Source: ci-runner`, then `request_source="ci-runner"`.

### T11.5 Classify at the library-audit enqueuer

- **Title**: Pass `request_source="scan"` and the audit's resolved profile name (None if absent).
- **Files**: `resources/daemon/threads.py:246`
- **Effort**: S
- **Dependencies**: T11.2
- **Given-When-Then**: Given the audit pipeline enqueues a path, then the row carries `request_source="scan"`.

### T11.6 `get_metrics` `requests` block

- **Title**: GROUP BY `COALESCE(request_source || '-' || request_profile, request_source, 'unknown')` with
  `count(*)`. Both backends.
- **Files**: `resources/daemon/db.py:308`, `:1073-1212`
- **Effort**: S
- **Dependencies**: T11.1
- **Given-When-Then**: Given mixed-attribution jobs, when `get_metrics()` runs, then `metrics["requests"]` is
  keyed `<source>-<profile>` (or bare `<source>` when profile is NULL) with correct counts.

### T11.7 `get_metrics` `profiles` block

- **Title**: GROUP BY `request_profile` returning `count`, `bytes_saved`, `minutes`, and a per-group
  `failure_rate_pct` computed as `failed::NUMERIC / NULLIF(completed + failed, 0) * 100`.
- **Files**: `resources/daemon/db.py:308`, `:1073-1212`
- **Effort**: M
- **Dependencies**: T11.1, T6.1 (bytes_saved / minutes sums already defined for top-level)
- **Given-When-Then**: Given jobs spread across two profiles, when `get_metrics()` runs, then `metrics["profiles"]`
  has one row per profile with the four fields; a profile with zero completed+failed yields
  `failure_rate_pct: None`.

### T11.8 In-memory `request_source_counters`

- **Title**: Mirror the `by_backend_counters` pattern; increment at job completion; surface on `/health`.
- **Files**: `resources/daemon/server.py:130-132, 148, 319-332`; `resources/daemon/handler.py:153-182`
- **Effort**: S
- **Dependencies**: T11.1, T7.1
- **Given-When-Then**: Given a completed job with `request_source="radarr"`, when `/health` is queried, then
  `payload["request_sources"]["radarr"]` is ≥ 1.

### T11.9 Dashboard Requests + Profiles cards

- **Title**: Two cards mirroring the Nodes/Encoders pattern. Profiles card includes `failure_rate_pct` column.
- **Files**: `resources/metrics.html:203, 390-408`
- **Effort**: S
- **Dependencies**: T11.6, T11.7
- **Given-When-Then**: Given populated blocks, both cards render rows; given empty blocks, both show empty-state.

### T11.10 Tests

- **Title**: Handler-level (sonarr/radarr/generic/CLI/header override), DB-aggregate-level (requests key shape,
  profiles per-group rates including the zero-denominator case), and scan-enqueuer tests.
- **Files**: `tests/test_handler.py`, `tests/test_daemon.py`, `tests/test_threads.py` (if it exists; else inline
  in `test_daemon.py`)
- **Effort**: M
- **Dependencies**: T11.3, T11.4, T11.5, T11.6, T11.7
- **Given-When-Then**: See the bullet list under "ADD to tests" in Task 14 of the PRP.

---

## Task 12: Prometheus exposition layer + remove in-memory counters

> **Pre-empts**: Tasks T7.x, T9.7, T11.8 (in-memory counter mirrors). Each
> of those becomes a no-op once `metrics_prom` is the single increment site.
> Update those tasks' Status to **SUPERSEDED** when T12.x lands.

### T12.1 Add `prometheus-client` dependency

- **Title**: Pin `prometheus-client>=0.20` in `pyproject.toml` under the
  daemon's runtime dependencies block.
- **Files**: `pyproject.toml`
- **Effort**: S
- **Dependencies**: —
- **Given-When-Then**: Given `mise run setup:deps` is rerun, when the daemon
  imports `prometheus_client`, then it succeeds without `ImportError`.

### T12.2 Create `resources/daemon/metrics_prom.py`

- **Title**: New module declaring every Counter / Histogram / Gauge from the
  PRP "Metric inventory" block, plus the three recorder helpers
  `record_job_completion`, `record_job_failure`,
  `record_fallback_transition`.
- **Files**: `resources/daemon/metrics_prom.py` (new)
- **Effort**: M
- **Dependencies**: T12.1
- **Given-When-Then**: Given a fresh daemon process, when the recorder
  helpers are invoked with synthetic job rows, then the relevant
  `Counter` / `Histogram` / `Gauge` reflects the increment/observation
  with the documented labels.

### T12.3 Label-cardinality drift-guard test

- **Title**: Iterate `prometheus_client.REGISTRY` after `metrics_prom`
  imports, assert each metric's label set matches the documented set
  in the PRP table exactly. Fails if a developer adds an unbounded label.
- **Files**: `tests/test_metrics_prom.py` (new)
- **Effort**: S
- **Dependencies**: T12.2
- **Given-When-Then**: Given the metric `sma_failures_total` declared with
  labels `("failure_category","failure_cause","encoder_backend")`,
  when the test runs, then it passes; given a developer adds
  `("job_id",...)`, the test fails with a message naming the metric.

### T12.4 Route table — claim `/metrics` for Prometheus, move HTML to `/dashboard/metrics`

- **Title**: Swap the `/metrics` route to the new Prometheus handler;
  add `/dashboard/metrics` for the existing HTML view. `/api/metrics`
  (JSON) is untouched.
- **Files**: `resources/daemon/routes.py:91-92`, `resources/daemon/handler.py:574-593`
- **Effort**: S
- **Dependencies**: T12.2
- **Given-When-Then**: Given a `GET /metrics` request, when the handler
  responds, then the body is Prometheus text exposition and the
  `Content-Type` is `text/plain; version=0.0.4; charset=utf-8`. Given a
  `GET /dashboard/metrics` request, then the response is the HTML
  dashboard previously served at `/metrics`.

### T12.5 Wire recorder helpers into the worker

- **Title**: Call `metrics_prom.record_job_completion(row)` and
  `record_job_failure(row, category, cause)` immediately after the
  matching `complete_job` / `fail_job` call in the worker. Call
  `record_fallback_transition` from the existing `fallback_counter_callback`
  site instead of `server.increment_fallback_counter`.
- **Files**: `resources/daemon/worker.py:179-197`, `resources/daemon/server.py:319-327`
- **Effort**: S
- **Dependencies**: T12.2, T11.x and T10.x columns (so the row carries
  the labels)
- **Given-When-Then**: Given a job completes successfully, when the worker
  finishes the path, then `sma_jobs_total{status="completed",...}` is +1
  and `sma_seconds_transcoded_total{encoder_backend=...}` is observed.

### T12.6 Remove the in-memory counter dicts in `server.py`

- **Title**: Delete `fallback_counters`, `fallback_counters_lock`,
  `increment_fallback_counter`, `fallback_summary`. Remove the
  `fallback_counter_callback` wiring (workers now call
  `metrics_prom.record_fallback_transition` directly).
- **Files**: `resources/daemon/server.py:130-132, 148, 319-332`,
  `resources/daemon/handler.py:161-164`
- **Effort**: S
- **Dependencies**: T12.5
- **Given-When-Then**: Given a clean checkout after this task, when
  `grep -nE "fallback_counters|by_backend_counters|failure_category_counters|request_source_counters" resources/`
  is run, then it returns no hits.

### T12.7 Mark superseded tasks as `STATUS: SUPERSEDED`

- **Title**: Update the headers of T7.1–T7.5, T9.7, T11.8 in this task
  doc to note they are superseded by T12.x.
- **Files**: `docs/tasks/metrics-expansion.md` (this file)
- **Effort**: S
- **Dependencies**: T12.6
- **Given-When-Then**: Given a reader scans the task doc, when they reach
  any superseded task, then the first line under the header reads
  `**Status:** SUPERSEDED by T12.5 / T12.6 — Prometheus refactor.`

### T12.8 Structured-logging field consistency at completion sites

- **Title**: Ensure every worker log line at job completion/failure carries
  the same field set fed to Prometheus (`encoder_backend`,
  `encoder_name`, `request_source`, `request_profile`, `failure_category`,
  `failure_cause`, `bytes_saved`, `source_duration_seconds`,
  `duration_ms`). Use the existing `set_job_id` (`resources/daemon/context.py`)
  as the correlation_id source — no structlog migration in this task.
- **Files**: `resources/daemon/worker.py:179-197`
- **Effort**: S
- **Dependencies**: T12.5
- **Given-When-Then**: Given a successful job, when the JSON log line for
  `job.completed` is captured by `caplog`, then it carries the documented
  field set and the existing job-id correlation field.

### T12.9 Promtool fixture + CI check

- **Title**: Capture a representative `/metrics` snapshot under
  `tests/fixtures/prometheus/sma_metrics.txt`; add a CI step that pipes
  it to `promtool check metrics` via the official prometheus/promtool
  Docker image (no host install required).
- **Files**: `tests/fixtures/prometheus/sma_metrics.txt` (new),
  `.mise/tasks/test/lint` (extend), CI workflow file under `.github/workflows/`
- **Effort**: M
- **Dependencies**: T12.4
- **Given-When-Then**: Given the fixture is well-formed, when CI runs
  the lint stage, then `promtool check metrics` exits 0; given a
  malformed sample is committed, then CI fails with the promtool error.

### T12.10 Docs

- **Title**: Document the new endpoint table, the Prometheus metric
  catalogue, the label cardinality budget, and the breaking-change move
  of `/metrics` → `/dashboard/metrics`. Add an example Prometheus
  `scrape_config` and a Grafana dashboard JSON skeleton under
  `docs/dashboards/sma.json`.
- **Files**: `docs/metrics.md`, `docs/daemon.md`, `docs/openapi.yaml`,
  `docs/deployment.md` (Grafana wire-up), `docs/dashboards/sma.json` (new)
- **Effort**: M
- **Dependencies**: T12.4
- **Given-When-Then**: Given an operator reads `docs/metrics.md`, when
  they look up "Prometheus", then they find the metric catalogue, the
  labels for each, the endpoint convention, and the migration note for
  the dashboard URL change.

---

## Critical Path

```text
          T1.1 ──► T1.2 ──► T1.3
              │        │
              ▼        ▼
              T5.1 ──► T5.2
                │
T2.1 ──► T2.2   │
   │            │
   ▼            │
   T3.1 ─► T3.2 ─► T3.3
              │
              ▼
              T4.1 ─► T4.2 ─► T4.3
                         │
                         ▼
                         T6.1 ─► T6.3
                         T6.2 ──┘
                         │
                         ▼
              T7.1 ─► T7.2 ─► T7.3 ─► T7.4 ─► T7.5
                                         │
                                         ▼
                                  T8.1 ─► T8.2 ─► T8.3
```

Blocking chain (longest path to operator-visible green):

```text
T1.1 → T1.2 → T5.1 → T4.2 → T6.1 → T7.4 → T8.1 → T8.2
                       ▲
T3.1 → T3.2 → T4.1 ────┘
T2.1 ──────────────────┘
```

**Parallelisable lanes**:

- Schema lane (T1.1 → T1.2 → T1.3) runs alongside the avcodecs hw_prefix lane (T2.1 → T2.2) and the resolver lane
  (T3.1 → T3.2 → T3.3) until they converge at the worker (T4.x).
- `complete_job` signature (T5.1) only needs the schema columns; it can land in parallel with the resolver lane and
  unblocks worker call-site work (T4.2).
- In-memory counters (T7.1) are independent of the DB lane and can be built and tested in isolation; T7.2 is the
  only join point with T4.2.
- Failure categorization lane (T9.1 → T9.2) is independent and can land at any point before T9.5 (worker
  classification). T9.3 reuses the T1.2 migrator and can land in parallel with the success-path schema work.
  T9.6 (failures aggregate) joins the metrics-shape lane at T6.x; T9.7 mirrors T7.x; T9.8 / T9.9 follow T8.x.
- Media characteristics lane (T10.1 → T10.5) is a self-contained side branch that joins at T6.x for the
  aggregate query and at T8.x for the dashboard. T10.2 (HDR helper extraction) and T10.3 (destination
  ffprobe) are the only entries that touch `mediaprocessor.py`; everything else is additive plumbing.
- Request/profile lane (T11.1 → T11.7) lands earliest at the enqueue path — T11.3/T11.4/T11.5 are independent
  per-call-site changes that can be split across PRs if needed. T11.6 / T11.7 / T11.8 join the metrics + health
  lanes at the same points as T9.x.
- Prometheus lane (T12.1 → T12.10) is the **last** to land in the dependency graph but the **first** that
  invalidates earlier in-memory work. Land T12.1–T12.6 in one PR so the `/metrics` endpoint moves atomically.
  T12.7 (SUPERSEDED markers) must precede T12.6 deletion when reviewers cross-reference the task doc, or land
  in the same commit. T12.9 (promtool CI gate) and T12.10 (docs + Grafana JSON) can land independently after.
- Docs (T8.2) and dashboard (T8.1) start as soon as the metrics shape (T6.1/T6.2) is stable; no need to wait for
  T7.x to finish.
