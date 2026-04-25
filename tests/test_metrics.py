"""Unit tests for the /api/metrics and /metrics handler endpoints."""

import json

import pytest

from tests.test_handler import _make_handler, _make_server


def _get_response_body(handler):
  handler.wfile.seek(0)
  return json.loads(handler.wfile.read().decode("utf-8"))


_METRICS_FIXTURE = {
  "available": True,
  "window": "24h",
  "kpis": {
    "completed": 10,
    "failed": 1,
    "cancelled": 0,
    "pending": 2,
    "running": 1,
    "total": 14,
    "failure_rate_pct": 9.09,
    "avg_duration_seconds": 120.5,
    "p95_duration_seconds": 300.0,
    "avg_compression_pct": 35.0,
    "throughput_per_hour": 0.42,
  },
  "timeseries": [
    {"bucket": "2026-04-25T00:00:00+00:00", "completed": 5, "failed": 0},
    {"bucket": "2026-04-25T01:00:00+00:00", "completed": 5, "failed": 1},
  ],
  "nodes": [
    {"node_id": "abc", "node_name": "worker-01", "completed": 10, "failed": 1, "avg_duration_seconds": 120.5},
  ],
}


class TestGetMetricsApi:
  def test_returns_503_when_not_distributed(self):
    server = _make_server(is_distributed=False)
    handler = _make_handler(path="/api/metrics", server=server)
    handler._get_metrics_api("/api/metrics", {})
    assert handler._response_code == 503
    body = _get_response_body(handler)
    assert body["available"] is False
    assert "reason" in body

  def test_returns_200_with_metrics_when_distributed(self):
    server = _make_server(is_distributed=True)
    server.job_db.get_metrics.return_value = _METRICS_FIXTURE
    handler = _make_handler(path="/api/metrics", server=server)
    handler._get_metrics_api("/api/metrics", {"window": ["24h"]})
    assert handler._response_code == 200
    body = _get_response_body(handler)
    assert body["available"] is True
    assert body["window"] == "24h"
    assert "kpis" in body
    assert "timeseries" in body
    assert "nodes" in body

  def test_valid_window_passed_to_db(self):
    server = _make_server(is_distributed=True)
    server.job_db.get_metrics.return_value = {**_METRICS_FIXTURE, "window": "7d"}
    handler = _make_handler(path="/api/metrics", server=server)
    handler._get_metrics_api("/api/metrics", {"window": ["7d"]})
    server.job_db.get_metrics.assert_called_once_with(window="7d")

  def test_invalid_window_falls_back_to_24h(self):
    server = _make_server(is_distributed=True)
    server.job_db.get_metrics.return_value = _METRICS_FIXTURE
    handler = _make_handler(path="/api/metrics", server=server)
    handler._get_metrics_api("/api/metrics", {"window": ["999d"]})
    server.job_db.get_metrics.assert_called_once_with(window="24h")

  def test_missing_window_defaults_to_24h(self):
    server = _make_server(is_distributed=True)
    server.job_db.get_metrics.return_value = _METRICS_FIXTURE
    handler = _make_handler(path="/api/metrics", server=server)
    handler._get_metrics_api("/api/metrics", {})
    server.job_db.get_metrics.assert_called_once_with(window="24h")

  @pytest.mark.parametrize("window", ["24h", "7d", "30d", "all"])
  def test_all_valid_windows_accepted(self, window):
    server = _make_server(is_distributed=True)
    server.job_db.get_metrics.return_value = {**_METRICS_FIXTURE, "window": window}
    handler = _make_handler(path="/api/metrics", server=server)
    handler._get_metrics_api("/api/metrics", {"window": [window]})
    server.job_db.get_metrics.assert_called_once_with(window=window)


class TestGetMetricsPage:
  def test_returns_200_html(self):
    import os
    import tempfile

    # Write a minimal metrics.html for the test
    metrics_html = "<html><body>Metrics</body></html>"
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False)
    tmp.write(metrics_html)
    tmp.close()

    import resources.daemon.docs_ui as docs_ui

    original_path = docs_ui.METRICS_HTML_PATH
    docs_ui.METRICS_HTML_PATH = tmp.name
    try:
      server = _make_server()
      handler = _make_handler(path="/metrics", server=server)
      handler._get_metrics_page("/metrics", {})
      assert handler._response_code == 200
      handler.wfile.seek(0)
      body = handler.wfile.read().decode("utf-8")
      assert "Metrics" in body
    finally:
      docs_ui.METRICS_HTML_PATH = original_path
      os.unlink(tmp.name)
