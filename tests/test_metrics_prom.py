"""Prometheus instrumentation contract tests.

These tests are intentionally narrow: they guard the label-cardinality
budget and the exposition format. They do NOT replicate the much larger
test_metrics.py / test_handler.py coverage of the JSON dashboard.
"""

from __future__ import annotations

import pytest

from resources.daemon import metrics_prom

# Authoritative label sets. A new label must be added here AND to the
# metric declaration in metrics_prom.py — drift fails this test.
EXPECTED_LABELS: dict[str, tuple[str, ...]] = {
  "sma_jobs_total": ("status",),
  "sma_fallback_transitions_total": ("from_tier", "to_tier", "failure_class"),
  "sma_job_duration_seconds": ("status",),
  "sma_jobs_in_flight": ("node_id",),
  "sma_queue_depth": ("node_id",),
  "sma_build_info": ("version", "node_id"),
  "sma_bytes_saved_bytes_total": ("encoder_backend",),
  "sma_bytes_grown_bytes_total": ("encoder_backend",),
  "sma_source_seconds_transcoded_total": ("encoder_backend",),
  "sma_failures_total": ("failure_category", "failure_cause"),
  "sma_jobs_enqueued_total": ("request_source", "request_profile"),
  "sma_output_dir_total_bytes": ("node_id",),
  "sma_output_dir_used_bytes": ("node_id",),
  "sma_output_dir_free_bytes": ("node_id",),
  "sma_output_orphan_files_swept_total": ("node_id", "kind"),
}

# Labels we must NEVER use anywhere — these would explode Prometheus
# storage under realistic traffic.
FORBIDDEN_LABELS = frozenset({"job_id", "path", "filename", "error_message", "output_path", "user_id", "correlation_id"})


_ATTR_TO_METRIC_NAME = {
  "JOBS_TOTAL": "sma_jobs_total",
  "FALLBACK_TRANSITIONS_TOTAL": "sma_fallback_transitions_total",
  "JOB_DURATION_SECONDS": "sma_job_duration_seconds",
  "JOBS_IN_FLIGHT": "sma_jobs_in_flight",
  "QUEUE_DEPTH": "sma_queue_depth",
  "BUILD_INFO": "sma_build_info",
  "BYTES_SAVED_TOTAL": "sma_bytes_saved_bytes_total",
  "BYTES_GROWN_TOTAL": "sma_bytes_grown_bytes_total",
  "SOURCE_SECONDS_TRANSCODED_TOTAL": "sma_source_seconds_transcoded_total",
  "FAILURES_TOTAL": "sma_failures_total",
  "JOBS_ENQUEUED_TOTAL": "sma_jobs_enqueued_total",
  "OUTPUT_DIR_TOTAL_BYTES": "sma_output_dir_total_bytes",
  "OUTPUT_DIR_USED_BYTES": "sma_output_dir_used_bytes",
  "OUTPUT_DIR_FREE_BYTES": "sma_output_dir_free_bytes",
  "OUTPUT_ORPHAN_FILES_SWEPT_TOTAL": "sma_output_orphan_files_swept_total",
}


def _all_instruments():
  """Yield (registered_metric_name, metric_obj) pairs declared by metrics_prom.

  prometheus_client strips the ``_total`` suffix from counters' internal
  ``_name`` and the ``_count``/``_sum``/``_bucket`` suffixes from histograms,
  so we map by the externally-visible name we test against the exposition.
  """
  for attr, name in _ATTR_TO_METRIC_NAME.items():
    yield name, getattr(metrics_prom, attr)


def test_label_sets_match_budget():
  """Every declared metric carries exactly the budgeted label set."""
  for metric_name, obj in _all_instruments():
    expected = EXPECTED_LABELS.get(metric_name)
    assert expected is not None, "Undocumented metric %r — update EXPECTED_LABELS." % metric_name
    assert tuple(obj._labelnames) == expected, "Label drift on %r: got %r, expected %r." % (metric_name, obj._labelnames, expected)


def test_no_forbidden_labels():
  """No metric uses an unbounded-cardinality label (job_id, path, ...)."""
  for metric_name, obj in _all_instruments():
    overlap = FORBIDDEN_LABELS.intersection(obj._labelnames)
    assert not overlap, "Metric %r uses forbidden unbounded label(s) %r — see test_metrics_prom.FORBIDDEN_LABELS." % (metric_name, overlap)


def test_record_job_terminal_rejects_unknown_status():
  """Non-terminal statuses are silently dropped; never inflate cardinality."""
  before = metrics_prom.JOBS_TOTAL._metrics.copy()
  metrics_prom.record_job_terminal("running", 1.0)
  metrics_prom.record_job_terminal("pending", 1.0)
  metrics_prom.record_job_terminal("", 1.0)
  assert metrics_prom.JOBS_TOTAL._metrics == before


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_record_job_terminal_accepts_terminal_statuses(status):
  metrics_prom.record_job_terminal(status, 1.5)
  sample = metrics_prom.JOBS_TOTAL.labels(status=status)._value.get()
  assert sample >= 1.0


def test_record_fallback_transition_normalises_blank_values():
  """None / empty labels collapse to 'unknown' rather than ''."""
  metrics_prom.record_fallback_transition("", "", "")
  series = metrics_prom.FALLBACK_TRANSITIONS_TOTAL.labels(from_tier="unknown", to_tier="unknown", failure_class="unknown")
  assert series._value.get() >= 1.0


def test_render_exposition_is_prometheus_text():
  """Output declares HELP + TYPE for every registered metric and parses cleanly."""
  body = metrics_prom.render_exposition().decode("utf-8")
  for metric_name in EXPECTED_LABELS:
    assert "# HELP %s " % metric_name in body, "Missing HELP for %s" % metric_name
    assert "# TYPE %s " % metric_name in body, "Missing TYPE for %s" % metric_name


def test_content_type_constant_is_prometheus_standard():
  assert metrics_prom.PROM_CONTENT_TYPE.startswith("text/plain")
  assert "version=" in metrics_prom.PROM_CONTENT_TYPE


def test_set_build_info_emits_pinned_gauge():
  metrics_prom.set_build_info("9.9.9", "test-node")
  series = metrics_prom.BUILD_INFO.labels(version="9.9.9", node_id="test-node")
  assert series._value.get() == 1.0


def test_register_queue_depth_source_evaluates_at_scrape():
  state = {"depth": 7}
  metrics_prom.register_queue_depth_source("test-node", lambda: state["depth"])
  rendered_first = metrics_prom.render_exposition().decode()
  assert 'sma_queue_depth{node_id="test-node"} 7.0' in rendered_first

  state["depth"] = 42
  rendered_second = metrics_prom.render_exposition().decode()
  assert 'sma_queue_depth{node_id="test-node"} 42.0' in rendered_second


def test_record_job_savings_clamps_per_job_and_handles_missing_values():
  qsv_saved = metrics_prom.BYTES_SAVED_TOTAL.labels(encoder_backend="qsv")
  qsv_grown = metrics_prom.BYTES_GROWN_TOTAL.labels(encoder_backend="qsv")
  qsv_secs = metrics_prom.SOURCE_SECONDS_TRANSCODED_TOTAL.labels(encoder_backend="qsv")
  baseline_saved = qsv_saved._value.get()
  baseline_grown = qsv_grown._value.get()
  baseline_secs = qsv_secs._value.get()

  # Net savings → bytes_saved only.
  metrics_prom.record_job_savings(1000, 600, 120.0, encoder_backend="qsv")
  # File grew → bytes_grown only.
  metrics_prom.record_job_savings(400, 550, 60.5, encoder_backend="qsv")
  # Equal sizes → neither bytes counter moves, duration still recorded.
  metrics_prom.record_job_savings(200, 200, 30.0, encoder_backend="qsv")
  # Missing values → no-op, no exception.
  metrics_prom.record_job_savings(None, None, None, encoder_backend="qsv")
  metrics_prom.record_job_savings(100, None, 10.0, encoder_backend="qsv")

  assert qsv_saved._value.get() == baseline_saved + 400
  assert qsv_grown._value.get() == baseline_grown + 150
  # 120 + 60.5 + 30 + 10 = 220.5
  assert qsv_secs._value.get() == pytest.approx(baseline_secs + 220.5)


def test_record_job_savings_unknown_backend_collapses_to_unknown_label():
  metrics_prom.record_job_savings(500, 100, 5.0, encoder_backend="some-future-backend")
  unknown_saved = metrics_prom.BYTES_SAVED_TOTAL.labels(encoder_backend="unknown")
  assert unknown_saved._value.get() >= 400
  # None also collapses to "unknown".
  metrics_prom.record_job_savings(200, 50, 1.0, encoder_backend=None)
  assert unknown_saved._value.get() >= 550


def test_record_failure_collapses_missing_to_unknown_label():
  metrics_prom.record_failure("", None)
  series = metrics_prom.FAILURES_TOTAL.labels(failure_category="unknown", failure_cause="unknown")
  assert series._value.get() >= 1.0


def test_record_job_enqueued_collapses_unknown_source_and_blank_profile():
  metrics_prom.record_job_enqueued("not-a-real-source", "")
  series = metrics_prom.JOBS_ENQUEUED_TOTAL.labels(request_source="unknown", request_profile="none")
  assert series._value.get() >= 1.0


def test_record_job_enqueued_uses_documented_sources():
  metrics_prom.record_job_enqueued("sonarr", "1080p")
  series = metrics_prom.JOBS_ENQUEUED_TOTAL.labels(request_source="sonarr", request_profile="1080p")
  assert series._value.get() >= 1.0


def test_request_sources_is_bounded():
  assert metrics_prom.REQUEST_SOURCES == ("sonarr", "radarr", "webhook", "scan", "audit", "unknown")


def test_record_failure_uses_provided_labels():
  metrics_prom.record_failure("hardware", "qsv_surface_pool_exhausted")
  series = metrics_prom.FAILURES_TOTAL.labels(failure_category="hardware", failure_cause="qsv_surface_pool_exhausted")
  assert series._value.get() >= 1.0


def test_in_flight_counter_inc_dec_round_trip():
  child = metrics_prom.in_flight_counter("rt-test")
  baseline = child._value.get()
  child.inc()
  child.inc()
  child.dec()
  assert child._value.get() == baseline + 1
  child.dec()
  assert child._value.get() == baseline


def test_record_orphan_sweep_increments_counter_by_kind():
  """record_orphan_sweep increments the labelled counter monotonically."""
  baseline = metrics_prom.OUTPUT_ORPHAN_FILES_SWEPT_TOTAL.labels(node_id="orphan-test", kind="sma")._value.get()
  metrics_prom.record_orphan_sweep("orphan-test", "sma", 3)
  metrics_prom.record_orphan_sweep("orphan-test", "sma", 2)
  series = metrics_prom.OUTPUT_ORPHAN_FILES_SWEPT_TOTAL.labels(node_id="orphan-test", kind="sma")
  assert series._value.get() == baseline + 5


def test_record_orphan_sweep_zero_and_negative_is_noop():
  before = metrics_prom.OUTPUT_ORPHAN_FILES_SWEPT_TOTAL.labels(node_id="orphan-zero", kind="smatmp")._value.get()
  metrics_prom.record_orphan_sweep("orphan-zero", "smatmp", 0)
  metrics_prom.record_orphan_sweep("orphan-zero", "smatmp", -5)
  series = metrics_prom.OUTPUT_ORPHAN_FILES_SWEPT_TOTAL.labels(node_id="orphan-zero", kind="smatmp")
  assert series._value.get() == before


def test_record_orphan_sweep_unknown_kind_collapses_to_unknown_label():
  metrics_prom.record_orphan_sweep("orphan-test", "not-a-kind", 1)
  series = metrics_prom.OUTPUT_ORPHAN_FILES_SWEPT_TOTAL.labels(node_id="orphan-test", kind="unknown")
  assert series._value.get() >= 1.0


def test_orphan_kinds_is_bounded():
  assert metrics_prom.ORPHAN_KINDS == ("sma", "smatmp", "empty_mp4")


def test_register_output_dir_source_evaluates_at_scrape():
  state = {"total": 1000, "used": 400, "free": 600}

  def _cb():
    return state["total"], state["used"], state["free"]

  metrics_prom.register_output_dir_source("output-test", _cb)
  rendered = metrics_prom.render_exposition().decode()
  assert 'sma_output_dir_total_bytes{node_id="output-test"} 1000.0' in rendered
  assert 'sma_output_dir_used_bytes{node_id="output-test"} 400.0' in rendered
  assert 'sma_output_dir_free_bytes{node_id="output-test"} 600.0' in rendered

  state["free"] = 9000
  rendered_after = metrics_prom.render_exposition().decode()
  assert 'sma_output_dir_free_bytes{node_id="output-test"} 9000.0' in rendered_after


def test_register_output_dir_source_accepts_attr_object():
  class _Usage:
    total = 800
    used = 100
    free = 700

  metrics_prom.register_output_dir_source("output-attr", lambda: _Usage())
  rendered = metrics_prom.render_exposition().decode()
  assert 'sma_output_dir_total_bytes{node_id="output-attr"} 800.0' in rendered
  assert 'sma_output_dir_free_bytes{node_id="output-attr"} 700.0' in rendered


def test_register_output_dir_source_callback_exception_yields_zeros():
  def _boom():
    raise RuntimeError("disk gone")

  metrics_prom.register_output_dir_source("output-boom", _boom)
  rendered = metrics_prom.render_exposition().decode()
  assert 'sma_output_dir_total_bytes{node_id="output-boom"} 0.0' in rendered
  assert 'sma_output_dir_free_bytes{node_id="output-boom"} 0.0' in rendered
