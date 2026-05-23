"""Prometheus instruments for the SMA-NG daemon.

Single home for every Counter / Histogram / Gauge so cardinality drift is
guarded by one test (see ``tests/test_metrics_prom.py``). Business code
calls the ``record_*`` / ``set_*`` helpers; it must never reach into the
underlying instruments directly.

This module is part of the additive Prometheus exposition layer landed
before the per-job schema columns. Once those columns ship, additional
labels (``encoder_backend``, ``request_source``, ``request_profile``,
``failure_category``) will be threaded through the helpers here. Until
then the label sets are intentionally minimal.
"""

from __future__ import annotations

from typing import Callable

# Orphan-file kinds tracked by ``sma_output_orphan_files_swept_total``.
# Bounded to three; new kinds must extend this tuple AND update the
# drift-guard test.
ORPHAN_KINDS: tuple[str, ...] = ("sma", "smatmp", "empty_mp4")

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram, generate_latest

PROM_CONTENT_TYPE = CONTENT_TYPE_LATEST

# Terminal job statuses we track. Bounded to three; new statuses must
# extend this tuple AND update the drift-guard test.
TERMINAL_STATUSES: tuple[str, ...] = ("completed", "failed", "cancelled")

# ---------- Build / version info ----------
#
# Per Prometheus best-practice ("the info pattern"), expose immutable
# build metadata as a labelled Gauge fixed at 1. Operators join this
# series against rate() metrics to attribute traffic to a release.

BUILD_INFO = Gauge(
  "sma_build_info",
  "SMA-NG daemon build metadata (value is always 1; labels carry the data).",
  ["version", "node_id"],
)

# ---------- Counters (monotonic; rate() in PromQL) ----------

JOBS_TOTAL = Counter(
  "sma_jobs_total",
  "Total jobs processed by terminal status.",
  ["status"],
)

FALLBACK_TRANSITIONS_TOTAL = Counter(
  "sma_fallback_transitions_total",
  "Ladder-tier transitions emitted by MediaProcessor._attempt_ladder.",
  ["from_tier", "to_tier", "failure_class"],
)

# Enqueue-time counter (distinct from sma_jobs_total which is terminal-state).
# `request_source` is bounded to the documented taxonomy below;
# `request_profile` is bounded by the operator's `profiles:` config block —
# typical deployment has ≤ 20 named profiles. New values are accepted but
# the worker collapses missing/unrecognised request_source to "unknown".
REQUEST_SOURCES: tuple[str, ...] = (
  "sonarr",
  "radarr",
  "webhook",
  "scan",
  "audit",
  "unknown",
)
JOBS_ENQUEUED_TOTAL = Counter(
  "sma_jobs_enqueued_total",
  "Jobs accepted into the queue, by request source and resolved profile.",
  ["request_source", "request_profile"],
)

# Savings counters labeled by encoder backend.
#
# Bounded label set: `qsv`, `vaapi`, `nvenc`, `videotoolbox`, `software`,
# `copy`, `unknown`. The drift-guard test asserts the label list matches
# this constant; the worker collapses missing/None backend strings to
# `"unknown"` so we never emit an empty-string label.
ENCODER_BACKENDS: tuple[str, ...] = (
  "qsv",
  "vaapi",
  "nvenc",
  "videotoolbox",
  "amf",
  "software",
  "copy",
  "unknown",
)

# Counters are per-job clamped: bytes_saved sums max(input - output, 0)
# across jobs, bytes_grown sums max(output - input, 0). Both can be
# non-zero on the same operator's workload (mixed HDR / SDR transcodes).
BYTES_SAVED_TOTAL = Counter(
  "sma_bytes_saved_bytes_total",
  "Cumulative bytes reclaimed by transcodes (clamped to >= 0 per job).",
  ["encoder_backend"],
)
BYTES_GROWN_TOTAL = Counter(
  "sma_bytes_grown_bytes_total",
  "Cumulative bytes added by transcodes whose output exceeded the source.",
  ["encoder_backend"],
)
SOURCE_SECONDS_TRANSCODED_TOTAL = Counter(
  "sma_source_seconds_transcoded_total",
  "Cumulative source container seconds transcoded.",
  ["encoder_backend"],
)

# Failure breakdown — bounded to the six FailureCategory values plus the
# raw failure_cause string. failure_cause cardinality is bounded by the
# union of FfmpegFailureClass + FfmpegFailureCause + worker sentinels
# (currently ~40 values). The drift-guard test catches additions.
FAILURES_TOTAL = Counter(
  "sma_failures_total",
  "Job failures by operator category and raw cause.",
  ["failure_category", "failure_cause"],
)

# ---------- Histograms ----------

# Buckets cover typical SMA-NG transcode durations: ~30s for trivial
# remuxes to ~2h for 4K HEVC software fallback. Last bucket is +Inf
# (added automatically by prometheus_client) which captures runaways.
JOB_DURATION_SECONDS = Histogram(
  "sma_job_duration_seconds",
  "Wall-clock job duration from claim to terminal status.",
  ["status"],
  buckets=(5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200),
)

# ---------- Gauges (saturation) ----------

# JOBS_IN_FLIGHT is maintained by the worker via inc()/dec() at the
# process_job boundary. Per-node so cluster operators can identify a
# stuck node from the gauge alone.
JOBS_IN_FLIGHT = Gauge(
  "sma_jobs_in_flight",
  "Currently-running job count on this daemon.",
  ["node_id"],
)

# QUEUE_DEPTH is populated lazily via Gauge.set_function() so the value
# is fetched fresh at scrape time without an extra background poller.
QUEUE_DEPTH = Gauge(
  "sma_queue_depth",
  "Pending + queued job count visible to this daemon (sampled at scrape).",
  ["node_id"],
)

# Output-directory capacity gauges. Populated lazily via Gauge.set_function()
# so a scrape reads fresh disk_usage() rather than relying on a background
# poller. ``register_output_dir_source`` wires the callback at startup.
OUTPUT_DIR_TOTAL_BYTES = Gauge(
  "sma_output_dir_total_bytes",
  "Total bytes on the filesystem hosting the configured output_directory (per node, sampled at scrape).",
  ["node_id"],
)
OUTPUT_DIR_USED_BYTES = Gauge(
  "sma_output_dir_used_bytes",
  "Used bytes on the filesystem hosting the configured output_directory (per node, sampled at scrape).",
  ["node_id"],
)
OUTPUT_DIR_FREE_BYTES = Gauge(
  "sma_output_dir_free_bytes",
  "Free bytes on the filesystem hosting the configured output_directory (per node, sampled at scrape).",
  ["node_id"],
)

# Orphan-sweep counter. ``kind`` is bounded to :data:`ORPHAN_KINDS`; the
# helper collapses unrecognised kinds rather than minting new labels.
OUTPUT_ORPHAN_FILES_SWEPT_TOTAL = Counter(
  "sma_output_orphan_files_swept_total",
  "Total orphan output-directory files removed by the storage janitor, by kind.",
  ["node_id", "kind"],
)


def record_job_terminal(status: str, duration_seconds: float | None = None) -> None:
  """Record a terminal job state. ``status`` must be in :data:`TERMINAL_STATUSES`."""
  if status not in TERMINAL_STATUSES:
    return
  JOBS_TOTAL.labels(status=status).inc()
  if duration_seconds is not None and duration_seconds >= 0:
    JOB_DURATION_SECONDS.labels(status=status).observe(duration_seconds)


def _normalise_backend(backend: str | None) -> str:
  """Collapse missing/unknown backends to a single ``"unknown"`` label."""
  if not backend:
    return "unknown"
  if backend not in ENCODER_BACKENDS:
    return "unknown"
  return backend


def _normalise_request_source(source: str | None) -> str:
  """Collapse missing/unrecognised request sources to ``"unknown"``."""
  if not source:
    return "unknown"
  if source not in REQUEST_SOURCES:
    return "unknown"
  return source


def record_job_enqueued(request_source: str | None, request_profile: str | None) -> None:
  """Increment ``sma_jobs_enqueued_total`` for one accepted job.

  ``request_profile`` is bounded by the operator's `profiles:` block; we
  do NOT validate it against an allowlist (operators rename profiles
  freely) but collapse empty/None to ``"none"`` to avoid empty-string
  label values.
  """
  JOBS_ENQUEUED_TOTAL.labels(
    request_source=_normalise_request_source(request_source),
    request_profile=request_profile or "none",
  ).inc()


def record_job_savings(
  input_size: int | None,
  output_size: int | None,
  source_duration_seconds: float | None,
  encoder_backend: str | None = None,
) -> None:
  """Record byte savings + source duration counters for one completed job.

  Each argument may be ``None`` when probing failed; the helper records
  only the metrics it can compute and never raises. ``encoder_backend``
  is collapsed to ``"unknown"`` when missing so the label cardinality is
  bounded to :data:`ENCODER_BACKENDS`.
  """
  backend = _normalise_backend(encoder_backend)
  if input_size is not None and output_size is not None:
    delta = int(input_size) - int(output_size)
    if delta > 0:
      BYTES_SAVED_TOTAL.labels(encoder_backend=backend).inc(delta)
    elif delta < 0:
      BYTES_GROWN_TOTAL.labels(encoder_backend=backend).inc(-delta)
  if source_duration_seconds is not None and source_duration_seconds > 0:
    SOURCE_SECONDS_TRANSCODED_TOTAL.labels(encoder_backend=backend).inc(float(source_duration_seconds))


def record_failure(failure_category: str, failure_cause: str | None) -> None:
  """Increment ``sma_failures_total`` for a failed job.

  Both labels collapse to ``"unknown"`` on missing values so we never
  emit empty-string label values (which Prometheus accepts but operators
  read as "label not set" — confusing in alert messages).
  """
  FAILURES_TOTAL.labels(
    failure_category=failure_category or "unknown",
    failure_cause=failure_cause or "unknown",
  ).inc()


def record_fallback_transition(from_tier: str, to_tier: str, failure_class: str) -> None:
  """Record a fallback ladder transition. Mirrors ``server.increment_fallback_counter``."""
  FALLBACK_TRANSITIONS_TOTAL.labels(
    from_tier=from_tier or "unknown",
    to_tier=to_tier or "unknown",
    failure_class=failure_class or "unknown",
  ).inc()


def in_flight_counter(node_id: str) -> Gauge:
  """Return the ``sma_jobs_in_flight`` child for *node_id*.

  Workers call ``.inc()`` at the start of ``process_job`` and ``.dec()`` in
  ``finally``. Using the prometheus_client child directly keeps the
  inc/dec atomic and avoids a second wrapper layer.
  """
  return JOBS_IN_FLIGHT.labels(node_id=node_id or "local")


def set_queue_depth(node_id: str, value: int) -> None:
  """Set the queue-depth gauge for *node_id*. Prefer :func:`register_queue_depth_source`."""
  QUEUE_DEPTH.labels(node_id=node_id or "local").set(value)


def register_queue_depth_source(node_id: str, callback: Callable[[], int]) -> None:
  """Wire *callback* (e.g. ``job_db.pending_count``) so scrapes read fresh state.

  The callable runs at every Prometheus collect() — keep it O(1) or O(log n).
  Pass-through to :py:meth:`prometheus_client.Gauge.set_function`.
  """
  QUEUE_DEPTH.labels(node_id=node_id or "local").set_function(lambda: float(callback() or 0))


def set_build_info(version: str, node_id: str) -> None:
  """Pin ``sma_build_info{version=..., node_id=...} == 1`` for this process."""
  BUILD_INFO.labels(version=version or "unknown", node_id=node_id or "local").set(1)


def render_exposition() -> bytes:
  """Render the registry in Prometheus text exposition format."""
  return generate_latest(REGISTRY)


__all__ = [
  "BUILD_INFO",
  "BYTES_GROWN_TOTAL",
  "BYTES_SAVED_TOTAL",
  "CONTENT_TYPE_LATEST",
  "FAILURES_TOTAL",
  "FALLBACK_TRANSITIONS_TOTAL",
  "JOBS_ENQUEUED_TOTAL",
  "JOBS_IN_FLIGHT",
  "JOBS_TOTAL",
  "JOB_DURATION_SECONDS",
  "PROM_CONTENT_TYPE",
  "QUEUE_DEPTH",
  "SOURCE_SECONDS_TRANSCODED_TOTAL",
  "TERMINAL_STATUSES",
  "in_flight_counter",
  "record_failure",
  "record_fallback_transition",
  "record_job_enqueued",
  "record_job_savings",
  "record_job_terminal",
  "register_queue_depth_source",
  "render_exposition",
  "set_build_info",
  "set_queue_depth",
]
