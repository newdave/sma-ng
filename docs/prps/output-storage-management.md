# Output Storage Management Blueprint

STATUS: IN-FLIGHT

Goal: prevent `/transcodes/sma` full-disk outages from burning worker slots
and leaking partial files, and give operators visibility before the next one.

Non-goals:
- Spillover across multiple output directories (deferred).
- Retention / auto-purge of completed outputs (deferred).
- Changing where post-processed finals land (`base.converter.output-directory`
  semantics unchanged).

Assumptions:
- The output directory is local to each daemon worker host (per-node check,
  not a cluster-wide lock).
- `base.converter.output-directory-space-ratio` already exists in the schema
  and is plumbed to `MediaProcessor.settings.output_dir_ratio`; default stays
  whatever it is today (no behavior change for existing operators who didn't
  set it).
- `prometheus_client` is already a runtime dep (it is — see `metrics_prom.py`).
- Orphan sweeping targets `*.sma` (configured temp_extension) and `*.smatmp`
  (atomic-copy partial); nothing else.

## Steps

### Part 1 — Pre-flight capacity check in the worker

1. `resources/processor/failures.py`
   Add `DISK_PRESSURE = "disk_pressure"` to whichever enum classifies
   pre-ffmpeg refusals (sibling of `DISK_FULL`, but raised *before* ffmpeg
   runs). If no such enum exists, add it as a sentinel string used only by
   the worker layer.
   Verify: `venv/bin/python -m pytest tests/test_failure_categorization.py -q`

2. `resources/daemon/worker.py`
   Between job claim and `MediaProcessor` invocation, call a new helper
   `preflight_output_capacity(settings, input_path) -> PreflightResult` that
   wraps `shutil.disk_usage(output_dir)` and the same ratio math currently
   in `MediaProcessor.outputDirHasFreeSpace` (lines 4028–4047). On shortfall:
   - Do NOT mark the job failed permanently. Push it back to the queue with
     a short backoff (reuse the existing requeue path) and a sentinel cause
     `WORKER_SENTINEL_DISK_PRESSURE`.
   - Record `metrics_prom.record_failure("disk_pressure", sentinel)` so
     operators can alert on `rate(sma_job_failures_total{cause="disk_pressure"})`.
   - Log a single-line structured event:
     `{"event":"worker.preflight","result":"deferred","cause":"disk_pressure",...}`.
   On success: proceed unchanged.
   Verify: `venv/bin/python -m pytest tests/test_worker.py -q`

3. `resources/mediaprocessor.py:539`
   Remove the silent `output_dir = None` fallback when
   `outputDirHasFreeSpace` returns False. The worker now owns this gate;
   leaving the silent fallback in place lets a manual `python manual.py`
   run still try to write next to the source on read-only mounts. Replace
   with an explicit `raise` (a new `InsufficientOutputSpace` exception) so
   both CLI and daemon paths see the same signal. CLI exit code maps to a
   distinct non-zero value so Sonarr/Radarr post-processing surfaces it.
   Verify: `venv/bin/python -m pytest tests/test_mediaprocessor.py -q`

4. Config schema — no new fields. We reuse `output-directory-space-ratio`.
   If the current default is `0` (disabled), bump the daemon-side default
   to `1.0` *only* inside the worker preflight (don't change the schema
   default; many CLI users rely on it being off). Document this divergence
   in `docs/daemon.md`.

5. Tests (`tests/test_worker.py`):
   - happy path: free space > input_size * ratio → job proceeds.
   - tight: free space < input_size * ratio → job requeued, metric ticked,
     no MediaProcessor invocation.
   - unreachable `output_dir` (ENOENT) → log + fail-open (proceed); we
     don't want a missing dir to wedge the queue.
   - ratio unset / zero → preflight is a no-op.

### Part 2 — Orphan cleanup + storage metrics

6. `resources/daemon/storage.py` (new file)
   - `sweep_output_directory(output_dir, temp_ext, max_age_seconds) -> SweptSummary`
     deletes `*.sma`, `*.smatmp`, and any zero-byte `*.mp4` older than
     `max_age_seconds` (default 6h). Returns counts + freed bytes.
   - `output_dir_usage(output_dir) -> DiskUsage` thin wrapper around
     `shutil.disk_usage` returning total/used/free in bytes. Tolerates
     ENOENT by returning zeros (don't crash the metric collector).
   Verify: `venv/bin/python -m pytest tests/test_storage.py -q` (new file)

7. `resources/daemon/metrics_prom.py`
   Add three gauges, following the existing `register_queue_depth_source`
   pattern (Gauge.set_function so they refresh lazily on scrape):
   - `sma_output_dir_total_bytes{node_id}`
   - `sma_output_dir_used_bytes{node_id}`
   - `sma_output_dir_free_bytes{node_id}`
   And one counter:
   - `sma_output_orphan_files_swept_total{node_id,kind}` where kind ∈
     {`sma`,`smatmp`,`empty_mp4`}.
   Public helpers: `register_output_dir_source(node_id, callback)` and
   `record_orphan_sweep(node_id, kind, count)`.
   Verify: `venv/bin/python -m pytest tests/test_metrics_prom.py -q`
   (must include cardinality drift assertion update.)

8. `resources/daemon/server.py`
   In daemon startup, after `register_queue_depth_source`, register the
   output-dir gauge source with a callback that reads
   `storage.output_dir_usage(settings.converter.output_directory)`.
   Verify: smoke test (`python daemon.py --smoke-test`) then
   `curl localhost:8585/metrics | grep sma_output_dir_`.

9. `resources/daemon/threads.py`
   Add a `StorageJanitorThread` running every 15 min (configurable via
   `daemon.storage-janitor-interval-seconds`, default 900). Calls
   `storage.sweep_output_directory` and emits the counter via
   `metrics_prom.record_orphan_sweep`. Also runs once at daemon startup
   so a crash-on-restart cleans up immediately.
   Verify: `venv/bin/python -m pytest tests/test_threads.py -q`

10. `resources/daemon/config.py` + `resources/config_schema.py` +
    `setup/sma-ng.yml.sample`
    Add `daemon.storage-janitor-interval-seconds: 900` and
    `daemon.storage-janitor-max-age-seconds: 21600` (6h). Both nullable to
    disable. Regenerate sample via `mise run config:sample`.
    Verify: `venv/bin/python -m pytest tests/test_sqlite_db.py tests/test_daemon.py -q`
    and `git diff setup/sma-ng.yml.sample` shows the two new keys only.

## Final Validation

```bash
source venv/bin/activate && \
  python -m pytest tests/test_worker.py tests/test_mediaprocessor.py \
                   tests/test_metrics_prom.py tests/test_threads.py \
                   tests/test_daemon.py tests/test_failure_categorization.py -q && \
  mise run test:lint && \
  python daemon.py --smoke-test
```

Manual: with `output_directory` pointed at a tmpfs sized below the next
queued input, confirm the job is deferred (not failed) and that
`/metrics` reports `sma_output_dir_free_bytes` decreasing as work runs.

## Docs/Config Sync

- docs:
  - `docs/daemon.md` — new "Storage management" section: preflight gate,
    janitor cadence, deferred-job behavior, the two new daemon keys.
  - `docs/metrics.md` — document the four new instruments and a sample
    PromQL alert (`sma_output_dir_free_bytes < 50e9 for 10m`).
  - `docs/troubleshooting.md` — "disk_pressure deferred jobs" entry
    pointing at the gauges and janitor logs.
  - `docs/configuration.md` — note that `output-directory-space-ratio`
    is enforced at the daemon worker layer when set.
- wiki: mirror `docs/daemon.md` storage section to `/tmp/sma-wiki/Daemon.md`.
- resources/docs.html: regenerated by the existing docs build step;
  confirm the new section renders.
- setup/sma-ng.yml.sample: regenerated via `mise run config:sample`.
