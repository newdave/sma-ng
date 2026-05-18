"""Unit tests for resources/daemon/handler.py - WebhookHandler."""

import io
import json
import os
import threading
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from resources.daemon.handler import (
  WebhookHandler,
  _inline,
  _render_markdown_to_html,
)

_tail_lines = WebhookHandler._tail_lines
_read_from_offset = WebhookHandler._read_from_offset


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


def _make_server(
  api_key=None,
  basic_auth=None,
  is_distributed=False,
):
  """Build a minimal mock DaemonServer."""
  server = MagicMock()
  server.api_key = api_key
  server.basic_auth = basic_auth
  server.node_id = "test-node-1"
  server.worker_count = 2
  server.stale_seconds = 120
  server.started_at = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
  server.logger = MagicMock()
  server._job_progress = {}
  server._job_processes = {}

  # job_db
  server.job_db.get_stats.return_value = {"pending": 0, "running": 0, "completed": 5, "failed": 0, "total": 5}
  server.job_db.get_jobs.return_value = []
  server.job_db.get_job.return_value = None
  server.job_db.add_job.return_value = 1
  server.job_db.find_active_job.return_value = None
  server.job_db.filter_unscanned.return_value = []
  server.job_db.cleanup_old_jobs.return_value = 3
  server.job_db.recover_stale_nodes.return_value = []
  server.job_db.get_cluster_nodes.return_value = []
  server.job_db.pending_count_for_config.return_value = 0
  server.job_db.requeue_failed_jobs.return_value = 0
  server.job_db.requeue_job.return_value = False
  server.job_db.set_job_priority.return_value = False
  server.job_db.delete_failed_jobs.return_value = 2
  server.job_db.delete_offline_nodes.return_value = 1
  server.job_db.delete_all_jobs.return_value = 10
  server.job_db.set_node_approval.return_value = {"node_id": "test-node-1", "approval_status": "approved"}
  server.job_db.delete_node.return_value = True
  server.job_db.record_scanned.return_value = None
  server.job_db.is_distributed = is_distributed

  # config_lock_manager
  server.config_lock_manager.get_status.return_value = {"active": {}, "waiting": {}}
  server.config_lock_manager.get_active_jobs.return_value = []
  server.config_lock_manager.is_locked.return_value = False

  # config_log_manager
  server.config_log_manager.get_log_file.return_value = "/logs/sma-ng.log"
  server.config_log_manager.get_all_log_files.return_value = []
  server.config_log_manager.logs_dir = "/logs"

  # path_config_manager
  server.path_config_manager.default_config = "/config/sma-ng.yml"
  server.path_config_manager.default_args = []
  server.path_config_manager.media_extensions = {".mkv", ".mp4", ".avi"}
  server.path_config_manager.scan_paths = []
  server.path_config_manager.get_config_for_path.return_value = "/config/sma-ng.yml"
  server.path_config_manager.rewrite_path.side_effect = lambda p: p
  server.path_config_manager.is_recycle_bin_path.return_value = False
  server.path_config_manager.get_args_for_path.return_value = []
  # New routing-engine API surfaces — return JSON-friendly defaults so
  # /configs and /browse responses serialize cleanly.
  server.path_config_manager.routing_rules_admin.return_value = []
  server.path_config_manager.routing_match_paths.return_value = []
  server.path_config_manager.get_profile_for_path.return_value = None
  server.path_config_manager.get_services_for_path.return_value = []
  # Default to "do not skip" so directory-submission tests don't see every
  # file silently dropped by the same-extension gate (mp4 already matches the
  # default output extension). Individual tests override this when they need
  # the gate active.
  server.path_config_manager.should_skip_same_extension.return_value = False

  # server methods
  server.notify_workers.return_value = None
  server.cancel_job.return_value = False
  server.shutdown.return_value = None
  server.graceful_restart.return_value = None
  server.reload_config.return_value = None

  # Hardware capability snapshot + fallback counters (Phase 1 / T5).
  # Defaults below mirror what the daemon attaches when probe-hw.py is
  # missing or fails open, so /health stays JSON-serialisable in tests
  # that don't override these.
  server.hw_capabilities = {
    "gpu_status": "ok",
    "selected_backend": "software",
    "capabilities": {},
  }
  server.fallback_summary.return_value = []

  return server


def _make_handler(
  method="GET",
  path="/health",
  body=b"",
  headers=None,
  api_key=None,
  basic_auth=None,
  is_distributed=False,
  server=None,
):
  """Construct a WebhookHandler without going through BaseHTTPRequestHandler.__init__."""
  handler = object.__new__(WebhookHandler)
  handler.server = server or _make_server(api_key=api_key, basic_auth=basic_auth, is_distributed=is_distributed)
  handler.path = path
  handler.command = method
  handler.rfile = io.BytesIO(body)
  handler.wfile = io.BytesIO()
  handler.connection = MagicMock()
  handler.connection.getsockname.return_value = ("127.0.0.1", 8585)

  # Build headers dict
  default_headers = {"Content-Type": "application/octet-stream", "Accept": "application/json"}
  if headers:
    default_headers.update(headers)
  handler.headers = default_headers

  # Track send_response calls
  handler._response_code = None
  handler._response_headers = {}

  def _send_response(code, message=None):
    handler._response_code = code

  def _send_header(key, value):
    handler._response_headers[key] = value

  def _end_headers():
    pass

  def _address_string():
    return "127.0.0.1"

  handler.send_response = _send_response
  handler.send_header = _send_header
  handler.end_headers = _end_headers
  handler.address_string = _address_string

  return handler


def _get_response_body(handler):
  """Return the decoded JSON body written to handler.wfile."""
  handler.wfile.seek(0)
  return json.loads(handler.wfile.read().decode("utf-8"))


def _get_response_bytes(handler):
  handler.wfile.seek(0)
  return handler.wfile.read()


# ---------------------------------------------------------------------------
# TestCheckAuth
# ---------------------------------------------------------------------------


class TestCheckAuth:
  def test_no_auth_configured_allows_all(self):
    h = _make_handler(api_key=None, basic_auth=None)
    assert h.check_auth() is True

  def test_valid_api_key_in_x_api_key_header(self):
    h = _make_handler(api_key="secret", headers={"X-API-Key": "secret"})
    assert h.check_auth() is True

  def test_wrong_api_key_returns_false_and_sends_401(self):
    h = _make_handler(api_key="secret", headers={"X-API-Key": "wrong"})
    assert h.check_auth() is False
    assert h._response_code == 401

  def test_missing_api_key_returns_false_and_sends_401(self):
    h = _make_handler(api_key="secret", headers={})
    assert h.check_auth() is False
    assert h._response_code == 401

  def test_bearer_token_auth(self):
    h = _make_handler(api_key="mytoken", headers={"Authorization": "Bearer mytoken"})
    assert h.check_auth() is True

  def test_wrong_bearer_token(self):
    h = _make_handler(api_key="mytoken", headers={"Authorization": "Bearer wrongtoken"})
    assert h.check_auth() is False
    assert h._response_code == 401

  def test_valid_basic_auth(self):
    import base64

    creds = base64.b64encode(b"admin:password").decode()
    h = _make_handler(basic_auth=("admin", "password"), headers={"Authorization": "Basic " + creds})
    assert h.check_auth() is True

  def test_wrong_basic_auth(self):
    import base64

    creds = base64.b64encode(b"admin:wrongpass").decode()
    h = _make_handler(basic_auth=("admin", "password"), headers={"Authorization": "Basic " + creds})
    assert h.check_auth() is False
    assert h._response_code == 401

  def test_invalid_base64_basic_auth(self):
    h = _make_handler(basic_auth=("admin", "password"), headers={"Authorization": "Basic !!!invalid!!!"})
    assert h.check_auth() is False
    assert h._response_code == 401

  def test_401_response_body_contains_error(self):
    h = _make_handler(api_key="secret", headers={})
    h.check_auth()
    body = _get_response_body(h)
    assert "error" in body
    assert body["error"] == "Unauthorized"


# ---------------------------------------------------------------------------
# TestIsPublicEndpoint
# ---------------------------------------------------------------------------


class TestIsPublicEndpoint:
  def test_health_is_public(self):
    h = _make_handler()
    assert h.is_public_endpoint("/health") is True

  def test_dashboard_is_public(self):
    h = _make_handler()
    assert h.is_public_endpoint("/dashboard") is True

  def test_admin_is_public(self):
    h = _make_handler()
    assert h.is_public_endpoint("/admin") is True

  def test_docs_root_is_public(self):
    h = _make_handler()
    assert h.is_public_endpoint("/docs") is True

  def test_docs_subpath_is_public(self):
    h = _make_handler()
    assert h.is_public_endpoint("/docs/daemon") is True

  def test_jobs_is_not_public(self):
    h = _make_handler()
    assert h.is_public_endpoint("/jobs") is False

  def test_webhook_is_not_public(self):
    h = _make_handler()
    assert h.is_public_endpoint("/webhook/generic") is False

  def test_stats_is_not_public(self):
    h = _make_handler()
    assert h.is_public_endpoint("/stats") is False


# ---------------------------------------------------------------------------
# TestGetHealth
# ---------------------------------------------------------------------------


class TestGetHealth:
  def test_returns_ok_status(self):
    h = _make_handler(path="/health")
    h._get_health()
    body = _get_response_body(h)
    assert body["status"] == "ok"

  def test_returns_node_id(self):
    h = _make_handler(path="/health")
    h._get_health()
    body = _get_response_body(h)
    assert body["node"] == "test-node-1"

  def test_returns_workers(self):
    h = _make_handler(path="/health")
    h._get_health()
    body = _get_response_body(h)
    assert body["workers"] == 2

  def test_returns_uptime_seconds(self):
    h = _make_handler(path="/health")
    h._get_health()
    body = _get_response_body(h)
    assert "uptime_seconds" in body
    assert isinstance(body["uptime_seconds"], int)

  def test_returns_200_status_code(self):
    h = _make_handler(path="/health")
    h._get_health()
    assert h._response_code == 200

  def test_returns_jobs_stats(self):
    h = _make_handler(path="/health")
    h._get_health()
    body = _get_response_body(h)
    assert "jobs" in body

  def test_returns_active_and_waiting(self):
    h = _make_handler(path="/health")
    h._get_health()
    body = _get_response_body(h)
    assert "active" in body
    assert "waiting" in body

  def test_returns_gpu_status_and_capabilities(self):
    h = _make_handler(path="/health")
    h.server.hw_capabilities = {
      "gpu_status": "ok",
      "selected_backend": "qsv",
      "capabilities": {"hwaccels": ["qsv", "vaapi"], "encoders": {"h264_qsv": True}},
    }
    h._get_health()
    body = _get_response_body(h)
    assert body["gpu_status"] == "ok"
    assert body["selected_backend"] == "qsv"
    assert "qsv" in body["capabilities"]["hwaccels"]

  def test_returns_fallback_summary(self):
    h = _make_handler(path="/health")
    h.server.fallback_summary.return_value = [
      {"from": "hw", "to": "sw_decode", "reason": "device_open_failed", "count": 2},
    ]
    h._get_health()
    body = _get_response_body(h)
    assert body["fallback"] == [
      {"from": "hw", "to": "sw_decode", "reason": "device_open_failed", "count": 2},
    ]

  def test_returns_unknown_gpu_status_when_capabilities_missing(self):
    h = _make_handler(path="/health")
    h.server.hw_capabilities = {}
    h._get_health()
    body = _get_response_body(h)
    assert body["gpu_status"] == "unknown"
    assert body["selected_backend"] == "software"
    assert body["capabilities"] == {}


# ---------------------------------------------------------------------------
# TestGetStats
# ---------------------------------------------------------------------------


class TestGetStats:
  def test_returns_stats_from_db(self):
    h = _make_handler()
    h.server.job_db.get_stats.return_value = {"pending": 3, "running": 1, "completed": 10, "failed": 2, "total": 16}
    h._get_stats(None, None)
    body = _get_response_body(h)
    assert body["pending"] == 3
    assert body["running"] == 1
    assert body["total"] == 16

  def test_returns_200(self):
    h = _make_handler()
    h._get_stats(None, None)
    assert h._response_code == 200


# ---------------------------------------------------------------------------
# TestDocsAndAssets
# ---------------------------------------------------------------------------


class TestDocsAndAssets:
  def test_get_docs_renders_index_page(self):
    h = _make_handler(path="/docs")
    with (
      patch("resources.daemon.handler._load_docs_template", return_value="<html>%s</html>"),
      patch("resources.daemon.handler._render_markdown_to_html", return_value="<h1>Overview</h1>"),
      patch("builtins.open", new_callable=MagicMock) as mock_open,
    ):
      mock_open.return_value.__enter__.return_value.read.return_value = "# Overview"
      h._get_docs("/docs", {})
    assert h._response_code == 200
    body = _get_response_bytes(h).decode("utf-8")
    assert "<h1>Overview</h1>" in body

  def test_get_docs_returns_404_when_markdown_file_missing(self):
    h = _make_handler(path="/docs/missing")
    with patch("builtins.open", side_effect=FileNotFoundError):
      h._get_docs("/docs/missing", {})
    assert h._response_code == 404
    assert "Page not found" in _get_response_bytes(h).decode("utf-8")

  def test_get_docs_sanitizes_slug_before_loading_file(self):
    h = _make_handler(path="/docs/../../daemon")
    with (
      patch("resources.daemon.handler._load_docs_template", return_value="<html>%s</html>"),
      patch("resources.daemon.handler._render_markdown_to_html", return_value="<p>ok</p>"),
      patch("builtins.open", new_callable=MagicMock) as mock_open,
    ):
      mock_open.return_value.__enter__.return_value.read.return_value = "ok"
      h._get_docs("/docs/../../daemon", {})
    opened_path = mock_open.call_args[0][0]
    assert ".." not in opened_path
    assert opened_path.endswith("docs/daemon.md")

  def test_get_favicon_returns_404_when_missing(self):
    h = _make_handler(path="/favicon.png")
    with patch("builtins.open", side_effect=FileNotFoundError):
      h._get_favicon(None, None)
    assert h._response_code == 404
    assert _get_response_body(h)["error"] == "favicon not found"


# ---------------------------------------------------------------------------
# TestGetJobs
# ---------------------------------------------------------------------------


class TestGetJobs:
  def test_returns_empty_list_by_default(self):
    h = _make_handler()
    h._get_jobs({})
    body = _get_response_body(h)
    assert body["jobs"] == []
    assert body["count"] == 0

  def test_passes_status_filter(self):
    h = _make_handler()
    h._get_jobs({"status": ["pending"]})
    h.server.job_db.get_jobs.assert_called_once_with(status="pending", config=None, path=None, limit=100, offset=0)

  def test_passes_limit_and_offset(self):
    h = _make_handler()
    h._get_jobs({"limit": ["10"], "offset": ["20"]})
    h.server.job_db.get_jobs.assert_called_once_with(status=None, config=None, path=None, limit=10, offset=20)

  def test_adds_log_name_to_jobs_with_config(self):
    h = _make_handler()
    h.server.job_db.get_jobs.return_value = [{"id": 1, "path": "/foo.mkv", "config": "/config/sma-ng.yml"}]
    h._get_jobs({})
    body = _get_response_body(h)
    assert body["jobs"][0]["log_name"] == "sma-ng"

  def test_returns_200(self):
    h = _make_handler()
    h._get_jobs({})
    assert h._response_code == 200


# ---------------------------------------------------------------------------
# TestGetJob
# ---------------------------------------------------------------------------


class TestGetJob:
  def test_returns_job_when_found(self):
    h = _make_handler()
    h.server.job_db.get_job.return_value = {"id": 42, "path": "/test.mkv", "status": "completed"}
    h._get_job("/jobs/42")
    body = _get_response_body(h)
    assert body["id"] == 42

  def test_returns_404_when_not_found(self):
    h = _make_handler()
    h.server.job_db.get_job.return_value = None
    h._get_job("/jobs/99")
    assert h._response_code == 404

  def test_returns_400_for_invalid_id(self):
    h = _make_handler()
    h._get_job("/jobs/notanint")
    assert h._response_code == 400

  def test_includes_progress_for_running_job(self):
    h = _make_handler()
    h.server.job_db.get_job.return_value = {"id": 5, "path": "/f.mkv", "status": "running"}
    h.server._job_progress = {5: {"percent": 50}}
    h._get_job("/jobs/5")
    body = _get_response_body(h)
    assert body["progress"]["percent"] == 50

  def test_no_progress_for_non_running_job(self):
    h = _make_handler()
    h.server.job_db.get_job.return_value = {"id": 3, "path": "/f.mkv", "status": "completed"}
    h.server._job_progress = {3: {"percent": 99}}
    h._get_job("/jobs/3")
    body = _get_response_body(h)
    assert "progress" not in body


class TestGetJobFfmpegStderr:
  def test_returns_plain_text_when_stored(self):
    h = _make_handler()
    h.server.job_db.get_job.return_value = {"id": 7, "ffmpeg_stderr": "ffmpeg: bad option"}
    h._get_job_ffmpeg_stderr("/jobs/7/ffmpeg-stderr")
    assert h._response_code == 200
    assert h._response_headers["Content-Type"] == "text/plain; charset=utf-8"
    assert _get_response_bytes(h) == b"ffmpeg: bad option"

  def test_returns_404_when_job_missing(self):
    h = _make_handler()
    h.server.job_db.get_job.return_value = None
    h._get_job_ffmpeg_stderr("/jobs/99/ffmpeg-stderr")
    assert h._response_code == 404

  def test_returns_404_when_stderr_empty(self):
    h = _make_handler()
    h.server.job_db.get_job.return_value = {"id": 7, "ffmpeg_stderr": None}
    h._get_job_ffmpeg_stderr("/jobs/7/ffmpeg-stderr")
    assert h._response_code == 404

  def test_returns_400_for_invalid_id(self):
    h = _make_handler()
    h._get_job_ffmpeg_stderr("/jobs/notanint/ffmpeg-stderr")
    assert h._response_code == 400


# ---------------------------------------------------------------------------
# TestGetConfigs
# ---------------------------------------------------------------------------


class TestGetConfigs:
  def test_returns_default_config(self):
    h = _make_handler()
    h._get_configs()
    body = _get_response_body(h)
    assert body["default_config"] == "/config/sma-ng.yml"

  def test_returns_routing_list(self):
    """Four-bucket schema renamed path_configs → routing."""
    h = _make_handler()
    h._get_configs()
    body = _get_response_body(h)
    assert "routing" in body
    assert isinstance(body["routing"], list)

  def test_returns_200(self):
    h = _make_handler()
    h._get_configs()
    assert h._response_code == 200


# ---------------------------------------------------------------------------
# TestGetLogs
# ---------------------------------------------------------------------------


class TestGetLogs:
  def test_returns_empty_list_when_no_logs(self):
    h = _make_handler()
    h._get_logs()
    body = _get_response_body(h)
    assert body == []

  def test_returns_log_entry_with_name(self, tmp_path):
    log_file = tmp_path / "sma-ng.log"
    log_file.write_text('{"level": "INFO", "message": "test"}\n')
    h = _make_handler()
    h.server.config_log_manager.get_all_log_files.return_value = [{"name": "sma-ng", "path": str(log_file)}]
    h._get_logs()
    body = _get_response_body(h)
    assert len(body) == 1
    assert body[0]["name"] == "sma-ng"
    assert body[0]["size"] > 0

  def test_handles_missing_log_file(self):
    h = _make_handler()
    h.server.config_log_manager.get_all_log_files.return_value = [{"name": "missing", "path": "/nonexistent/log.log"}]
    h._get_logs()
    body = _get_response_body(h)
    assert body[0]["size"] == 0
    assert body[0]["mtime"] is None

  def test_formats_log_mtime_in_local_timezone(self, tmp_path):
    log_file = tmp_path / "sma-ng.log"
    log_file.write_text('{"level": "INFO", "message": "test"}\n')
    h = _make_handler()
    h.server.config_log_manager.get_all_log_files.return_value = [{"name": "sma-ng", "path": str(log_file)}]
    local_tz = timezone(timedelta(hours=-5))
    with patch("resources.daemon.handler._LOCAL_TIMEZONE", local_tz):
      h._get_logs()
    body = _get_response_body(h)
    assert body[0]["mtime"].endswith("-05:00")


class TestJsonSerialization:
  def test_health_serializes_started_at_in_local_timezone(self):
    local_tz = timezone(timedelta(hours=-5))
    h = _make_handler()
    h.server.started_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    with (
      patch("resources.daemon.handler._LOCAL_TIMEZONE", local_tz),
      patch(
        "resources.daemon.handler._local_now",
        return_value=datetime(2024, 1, 1, 7, 0, 30, tzinfo=local_tz),
      ),
    ):
      h._get_health()
    body = _get_response_body(h)
    assert body["started_at"] == "2024-01-01T07:00:00-05:00"
    assert body["uptime_seconds"] == 30


# ---------------------------------------------------------------------------
# TestGetLogContent
# ---------------------------------------------------------------------------


class TestGetLogContent:
  def test_returns_404_for_unknown_log(self):
    h = _make_handler()
    h.server.config_log_manager.get_all_log_files.return_value = []
    h._get_log_content("/logs/unknown", {})
    assert h._response_code == 404

  def test_returns_entries_from_log_file(self, tmp_path):
    log_file = tmp_path / "sma-ng.log"
    entry = {"level": "INFO", "message": "test", "job_id": 1}
    log_file.write_text(json.dumps(entry) + "\n")
    h = _make_handler()
    h.server.config_log_manager.get_all_log_files.return_value = [{"name": "sma-ng", "path": str(log_file)}]
    h._get_log_content("/logs/sma-ng", {})
    body = _get_response_body(h)
    assert len(body["entries"]) == 1
    assert body["entries"][0]["message"] == "test"

  def test_filters_by_job_id(self, tmp_path):
    log_file = tmp_path / "test.log"
    lines = [
      json.dumps({"level": "INFO", "message": "job1", "job_id": 1}),
      json.dumps({"level": "INFO", "message": "job2", "job_id": 2}),
    ]
    log_file.write_text("\n".join(lines) + "\n")
    h = _make_handler()
    h.server.config_log_manager.get_all_log_files.return_value = [{"name": "test", "path": str(log_file)}]
    h._get_log_content("/logs/test", {"job_id": ["1"]})
    body = _get_response_body(h)
    assert len(body["entries"]) == 1
    assert body["entries"][0]["job_id"] == 1

  def test_filters_by_level(self, tmp_path):
    log_file = tmp_path / "test.log"
    lines = [
      json.dumps({"level": "DEBUG", "message": "debug msg"}),
      json.dumps({"level": "ERROR", "message": "error msg"}),
    ]
    log_file.write_text("\n".join(lines) + "\n")
    h = _make_handler()
    h.server.config_log_manager.get_all_log_files.return_value = [{"name": "test", "path": str(log_file)}]
    h._get_log_content("/logs/test", {"level": ["ERROR"]})
    body = _get_response_body(h)
    assert len(body["entries"]) == 1
    assert body["entries"][0]["level"] == "ERROR"

  def test_tail_requires_offset(self, tmp_path):
    log_file = tmp_path / "test.log"
    log_file.write_text("")
    h = _make_handler()
    h.server.config_log_manager.get_all_log_files.return_value = [{"name": "test", "path": str(log_file)}]
    h._get_log_content("/logs/test/tail", {})
    assert h._response_code == 400

  def test_tail_returns_from_offset(self, tmp_path):
    log_file = tmp_path / "test.log"
    entry = json.dumps({"level": "INFO", "message": "tail msg"})
    log_file.write_text(entry + "\n")
    offset = 0
    h = _make_handler()
    h.server.config_log_manager.get_all_log_files.return_value = [{"name": "test", "path": str(log_file)}]
    h._get_log_content("/logs/test/tail", {"offset": [str(offset)]})
    body = _get_response_body(h)
    assert len(body["entries"]) == 1

  def test_tail_resets_offset_when_beyond_file_size(self, tmp_path):
    log_file = tmp_path / "test.log"
    entry = json.dumps({"level": "INFO", "message": "rotated"})
    log_file.write_text(entry + "\n")
    h = _make_handler()
    h.server.config_log_manager.get_all_log_files.return_value = [{"name": "test", "path": str(log_file)}]
    h._get_log_content("/logs/test/tail", {"offset": ["999999"]})
    body = _get_response_body(h)
    assert "entries" in body

  def test_returns_empty_entries_for_nonexistent_file(self):
    h = _make_handler()
    h.server.config_log_manager.get_all_log_files.return_value = [{"name": "gone", "path": "/nonexistent/gone.log"}]
    h._get_log_content("/logs/gone", {})
    body = _get_response_body(h)
    assert body["entries"] == []
    assert body["file_size"] == 0

  def test_skips_non_json_lines(self, tmp_path):
    log_file = tmp_path / "test.log"
    log_file.write_text("not json\n" + json.dumps({"level": "INFO", "message": "ok"}) + "\n")
    h = _make_handler()
    h.server.config_log_manager.get_all_log_files.return_value = [{"name": "test", "path": str(log_file)}]
    h._get_log_content("/logs/test", {})
    body = _get_response_body(h)
    assert len(body["entries"]) == 1


# ---------------------------------------------------------------------------
# TestGetScan
# ---------------------------------------------------------------------------


class TestGetScan:
  def test_returns_unscanned_paths(self):
    h = _make_handler()
    h.server.job_db.filter_unscanned.return_value = ["/a.mkv"]
    h._get_scan({"path": ["/a.mkv", "/b.mkv"]})
    body = _get_response_body(h)
    assert body["unscanned"] == ["/a.mkv"]
    assert body["total"] == 2
    assert body["already_scanned"] == 1

  def test_empty_path_list(self):
    h = _make_handler()
    h.server.job_db.filter_unscanned.return_value = []
    h._get_scan({})
    body = _get_response_body(h)
    assert body["total"] == 0


# ---------------------------------------------------------------------------
# TestGetStatus
# ---------------------------------------------------------------------------


class TestGetStatus:
  def test_returns_cluster_and_jobs(self):
    h = _make_handler()
    h.server.job_db.get_cluster_nodes.return_value = [{"host": "127.0.0.1", "port": 8585}]
    h._get_status()
    body = _get_response_body(h)
    assert "cluster" in body
    assert "jobs" in body

  def test_status_is_read_only(self):
    h = _make_handler()
    h._get_status()
    h.server.job_db.recover_stale_nodes.assert_not_called()
    h.server.notify_workers.assert_not_called()

  def test_replaces_bind_all_host(self):
    h = _make_handler()
    h.server.job_db.get_cluster_nodes.return_value = [{"host": "0.0.0.0", "port": 8585}]
    h.connection.getsockname.return_value = ("192.168.1.1", 8585)
    h._get_status()
    body = _get_response_body(h)
    assert body["cluster"][0]["host"] == "192.168.1.1"


# ---------------------------------------------------------------------------
# TestPostCleanup
# ---------------------------------------------------------------------------


class TestPostCleanup:
  def test_default_days_is_30(self):
    h = _make_handler(method="POST", path="/cleanup")
    h._post_cleanup({})
    h.server.job_db.cleanup_old_jobs.assert_called_once_with(30)

  def test_custom_days(self):
    h = _make_handler(method="POST", path="/cleanup")
    h._post_cleanup({"days": ["7"]})
    h.server.job_db.cleanup_old_jobs.assert_called_once_with(7)

  def test_returns_deleted_count(self):
    h = _make_handler(method="POST", path="/cleanup")
    h.server.job_db.cleanup_old_jobs.return_value = 15
    h._post_cleanup({})
    body = _get_response_body(h)
    assert body["deleted"] == 15

  def test_returns_200(self):
    h = _make_handler(method="POST")
    h._post_cleanup({})
    assert h._response_code == 200


# ---------------------------------------------------------------------------
# TestPostShutdown
# ---------------------------------------------------------------------------


class TestPostShutdown:
  def test_local_shutdown_returns_202(self):
    h = _make_handler(method="POST")
    with patch("threading.Thread"):
      h._post_shutdown("/shutdown", {})
    assert h._response_code == 202

  def test_local_shutdown_starts_thread(self):
    h = _make_handler(method="POST")
    with patch("threading.Thread") as mock_thread:
      h._post_shutdown("/shutdown", {})
    mock_thread.assert_called_once()

  def test_distributed_shutdown_with_node(self):
    h = _make_handler(method="POST", is_distributed=True)
    h.server.job_db.send_node_command.return_value = 1
    h._post_shutdown("/shutdown", {"node": ["node-2"]})
    h.server.job_db.send_node_command.assert_called_once_with("node-2", "shutdown", requested_by="api")
    assert h._response_code == 202

  def test_distributed_broadcast_shutdown(self):
    h = _make_handler(method="POST", is_distributed=True)
    h.server.job_db.send_node_command.return_value = 3
    h._post_shutdown("/shutdown", {})
    h.server.job_db.send_node_command.assert_called_once_with(None, "shutdown", requested_by="api")
    assert h._response_code == 202


# ---------------------------------------------------------------------------
# TestPostRestart
# ---------------------------------------------------------------------------


class TestPostRestart:
  def test_local_restart_returns_202(self):
    h = _make_handler(method="POST")
    with patch("threading.Thread"):
      h._post_restart("/restart", {})
    assert h._response_code == 202

  def test_distributed_restart_with_node(self):
    h = _make_handler(method="POST", is_distributed=True)
    h.server.job_db.send_node_command.return_value = 1
    h._post_restart("/restart", {"node": ["node-2"]})
    h.server.job_db.send_node_command.assert_called_once_with("node-2", "restart", requested_by="api")

  def test_distributed_broadcast_restart(self):
    h = _make_handler(method="POST", is_distributed=True)
    h.server.job_db.send_node_command.return_value = 2
    h._post_restart("/restart", {})
    h.server.job_db.send_node_command.assert_called_once_with(None, "restart", requested_by="api")


class TestPostAdminNodeAction:
  def test_approve_node_returns_200(self):
    body = json.dumps({"note": "trusted host"}).encode()
    h = _make_handler(
      method="POST",
      body=body,
      headers={"Content-Length": str(len(body)), "Content-Type": "application/json", "X-Actor": "qa-admin"},
    )
    h.server.job_db.set_node_approval.return_value = {"node_id": "node-1", "approval_status": "approved"}
    h._post_admin_node_action("/admin/nodes/node-1/approve")
    h.server.job_db.set_node_approval.assert_called_once_with(node_id="node-1", approved=True, actor="qa-admin", note="trusted host")
    assert h._response_code == 200

  def test_reject_node_returns_200(self):
    h = _make_handler(method="POST", headers={"Content-Length": "0"})
    h.server.job_db.set_node_approval.return_value = {"node_id": "node-1", "approval_status": "rejected"}
    h._post_admin_node_action("/admin/nodes/node-1/reject")
    h.server.job_db.set_node_approval.assert_called_once_with(node_id="node-1", approved=False, actor="admin-ui", note=None)
    assert h._response_code == 200

  def test_restart_node_command_returns_202(self):
    h = _make_handler(method="POST", headers={"X-Actor": "ops"})
    h.server.job_db.send_node_command.return_value = ["node-1"]
    h._post_admin_node_action("/admin/nodes/node-1/restart")
    h.server.job_db.send_node_command.assert_called_once_with("node-1", "restart", requested_by="ops")
    assert h._response_code == 202

  def test_delete_node_returns_200(self):
    h = _make_handler(method="POST")
    h.server.job_db.delete_node.return_value = True
    h._post_admin_node_action("/admin/nodes/node-1/delete")
    h.server.job_db.delete_node.assert_called_once_with("node-1")
    assert h._response_code == 200


# ---------------------------------------------------------------------------
# TestPostReload
# ---------------------------------------------------------------------------


class TestPostReload:
  def test_returns_200_with_reloading_status(self):
    h = _make_handler(method="POST")
    with patch("threading.Thread"):
      h._post_reload("/reload", {})
    assert h._response_code == 200
    body = _get_response_body(h)
    assert body["status"] == "reloading"

  def test_starts_reload_thread(self):
    h = _make_handler(method="POST")
    with patch("threading.Thread") as mock_thread:
      h._post_reload("/reload", {})
    mock_thread.assert_called_once()


# ---------------------------------------------------------------------------
# TestParseWebhookBody
# ---------------------------------------------------------------------------


class TestParseWebhookBody:
  def test_empty_body_returns_none_and_sends_400(self):
    h = _make_handler(body=b"", headers={"Content-Length": "0"})
    path, args, config, retries = h._parse_webhook_body()
    assert path is None
    assert h._response_code == 400

  def test_plain_text_body_returns_path(self):
    body = b"/mnt/media/movie.mkv"
    h = _make_handler(body=body, headers={"Content-Length": str(len(body)), "Content-Type": "text/plain"})
    path, args, config, retries = h._parse_webhook_body()
    assert path == "/mnt/media/movie.mkv"

  def test_json_body_with_path_key(self):
    data = json.dumps({"path": "/mnt/movie.mkv"}).encode()
    h = _make_handler(body=data, headers={"Content-Length": str(len(data)), "Content-Type": "application/json"})
    path, args, config, retries = h._parse_webhook_body()
    assert path == "/mnt/movie.mkv"

  def test_json_body_with_file_key(self):
    data = json.dumps({"file": "/mnt/movie.mkv"}).encode()
    h = _make_handler(body=data, headers={"Content-Length": str(len(data)), "Content-Type": "application/json"})
    path, args, config, retries = h._parse_webhook_body()
    assert path == "/mnt/movie.mkv"

  def test_json_body_with_extra_args(self):
    data = json.dumps({"path": "/mnt/movie.mkv", "args": ["-tmdb", "603"]}).encode()
    h = _make_handler(body=data, headers={"Content-Length": str(len(data)), "Content-Type": "application/json"})
    path, args, config, retries = h._parse_webhook_body()
    assert args == ["-tmdb", "603"]

  def test_json_body_with_config_override(self):
    data = json.dumps({"path": "/mnt/movie.mkv", "config": "/alt/config.ini"}).encode()
    h = _make_handler(body=data, headers={"Content-Length": str(len(data)), "Content-Type": "application/json"})
    path, args, config, retries = h._parse_webhook_body()
    assert config == "/alt/config.ini"

  def test_json_body_with_max_retries(self):
    data = json.dumps({"path": "/mnt/movie.mkv", "max_retries": 3}).encode()
    h = _make_handler(body=data, headers={"Content-Length": str(len(data)), "Content-Type": "application/json"})
    path, args, config, retries = h._parse_webhook_body()
    assert retries == 3

  def test_json_string_body(self):
    data = json.dumps("/mnt/movie.mkv").encode()
    h = _make_handler(body=data, headers={"Content-Length": str(len(data)), "Content-Type": "application/json"})
    path, args, config, retries = h._parse_webhook_body()
    assert path == "/mnt/movie.mkv"

  def test_no_path_in_json_returns_400(self):
    data = json.dumps({"args": ["-tmdb", "603"]}).encode()
    h = _make_handler(body=data, headers={"Content-Length": str(len(data)), "Content-Type": "application/json"})
    path, args, config, retries = h._parse_webhook_body()
    assert path is None
    assert h._response_code == 400

  def test_args_as_string_are_split(self):
    data = json.dumps({"path": "/movie.mkv", "args": "-tmdb 603"}).encode()
    h = _make_handler(body=data, headers={"Content-Length": str(len(data)), "Content-Type": "application/json"})
    path, args, config, retries = h._parse_webhook_body()
    assert args == ["-tmdb", "603"]

  def test_args_string_preserves_quoted_values(self):
    data = json.dumps({"path": "/movie.mkv", "args": '--label "Director Cut"'}).encode()
    h = _make_handler(body=data, headers={"Content-Length": str(len(data)), "Content-Type": "application/json"})
    path, args, config, retries = h._parse_webhook_body()
    assert args == ["--label", "Director Cut"]

  def test_invalid_quoted_args_returns_400(self):
    data = json.dumps({"path": "/movie.mkv", "args": '"unterminated'}).encode()
    h = _make_handler(body=data, headers={"Content-Length": str(len(data)), "Content-Type": "application/json"})
    path, args, config, retries = h._parse_webhook_body()
    assert path is None
    assert h._response_code == 400


# ---------------------------------------------------------------------------
# TestParseSonarrBody
# ---------------------------------------------------------------------------


class TestParseSonarrBody:
  def _sonarr_body(self, data):
    body = json.dumps(data).encode()
    h = _make_handler(
      body=body,
      headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
    )
    return h

  def test_empty_body_returns_400(self):
    h = _make_handler(headers={"Content-Length": "0"})
    path, args, profile, tag_ids = h._parse_sonarr_body()
    assert path is None
    assert h._response_code == 400

  def test_test_event_returns_200(self):
    h = self._sonarr_body({"eventType": "Test"})
    path, args, profile, tag_ids = h._parse_sonarr_body()
    assert path is None
    assert h._response_code == 200

  def test_unsupported_event_returns_400(self):
    h = self._sonarr_body({"eventType": "Grab"})
    path, args, profile, tag_ids = h._parse_sonarr_body()
    assert path is None
    assert h._response_code == 400

  def test_download_event_extracts_path(self):
    payload = {
      "eventType": "Download",
      "episodeFile": {"path": "/tv/Show/S01E01.mkv"},
      "series": {"tvdbId": 12345},
      "episodes": [{"seasonNumber": 1, "episodeNumber": 1}],
    }
    h = self._sonarr_body(payload)
    path, args, profile, tag_ids = h._parse_sonarr_body()
    assert path == "/tv/Show/S01E01.mkv"
    assert "--tv" in args
    assert "-tvdb" in args
    assert "12345" in args
    assert profile is None
    assert tag_ids == []

  def test_missing_episode_file_path_returns_400(self):
    payload = {
      "eventType": "Download",
      "episodeFile": {},
      "series": {},
      "episodes": [],
    }
    h = self._sonarr_body(payload)
    path, args, profile, tag_ids = h._parse_sonarr_body()
    assert path is None
    assert h._response_code == 400

  def test_uses_imdb_when_no_tvdb(self):
    payload = {
      "eventType": "Download",
      "episodeFile": {"path": "/tv/ep.mkv"},
      "series": {"imdbId": "tt0472308"},
      "episodes": [],
    }
    h = self._sonarr_body(payload)
    path, args, profile, tag_ids = h._parse_sonarr_body()
    assert "-imdb" in args
    assert "tt0472308" in args

  def test_extracts_tvdb_id_from_path_when_not_in_payload(self):
    payload = {
      "eventType": "Download",
      "episodeFile": {"path": "/tv/The Rookie (2018) {tvdb-350665}/S08E16.mkv"},
      "series": {},
      "episodes": [],
    }
    h = self._sonarr_body(payload)
    path, args, profile, tag_ids = h._parse_sonarr_body()
    assert "-tvdb" in args
    assert "350665" in args

  def test_path_tvdb_not_used_when_payload_has_tvdb(self):
    payload = {
      "eventType": "Download",
      "episodeFile": {"path": "/tv/Show {tvdb-999}/S01E01.mkv"},
      "series": {"tvdbId": 12345},
      "episodes": [],
    }
    h = self._sonarr_body(payload)
    path, args, profile, tag_ids = h._parse_sonarr_body()
    assert "12345" in args
    assert "999" not in args

  def test_includes_season_and_episode_numbers(self):
    payload = {
      "eventType": "Download",
      "episodeFile": {"path": "/tv/ep.mkv"},
      "series": {"tvdbId": 73871},
      "episodes": [{"seasonNumber": 3, "episodeNumber": 10}],
    }
    h = self._sonarr_body(payload)
    path, args, profile, tag_ids = h._parse_sonarr_body()
    assert "-s" in args
    assert "3" in args
    assert "-e" in args
    assert "10" in args

  def test_extracts_profile_override_from_tag_label(self):
    payload = {
      "eventType": "Download",
      "episodeFile": {"path": "/tv/ep.mkv"},
      "series": {"tags": ["sma-profile-lq"]},
      "episodes": [],
    }
    h = self._sonarr_body(payload)
    path, args, profile, tag_ids = h._parse_sonarr_body()
    assert path == "/tv/ep.mkv"
    assert profile == "lq"
    assert tag_ids == []

  def test_extracts_tag_ids_for_later_lookup(self):
    payload = {
      "eventType": "Download",
      "episodeFile": {"path": "/tv/ep.mkv"},
      "series": {"tags": [101, "202"]},
      "episodes": [],
    }
    h = self._sonarr_body(payload)
    path, args, profile, tag_ids = h._parse_sonarr_body()
    assert profile is None
    assert tag_ids == [101, 202]

  def test_invalid_json_returns_400(self):
    body = b"not json"
    h = _make_handler(body=body, headers={"Content-Length": str(len(body)), "Content-Type": "application/json"})
    path, args, profile, tag_ids = h._parse_sonarr_body()
    assert path is None
    assert h._response_code == 400


# ---------------------------------------------------------------------------
# TestParseRadarrBody
# ---------------------------------------------------------------------------


class TestParseRadarrBody:
  def _radarr_body(self, data):
    body = json.dumps(data).encode()
    h = _make_handler(
      body=body,
      headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
    )
    return h

  def test_empty_body_returns_400(self):
    h = _make_handler(headers={"Content-Length": "0"})
    path, args, profile, tag_ids = h._parse_radarr_body()
    assert path is None
    assert h._response_code == 400

  def test_test_event_returns_200(self):
    h = self._radarr_body({"eventType": "Test"})
    path, args, profile, tag_ids = h._parse_radarr_body()
    assert path is None
    assert h._response_code == 200

  def test_unsupported_event_returns_400(self):
    h = self._radarr_body({"eventType": "Grab"})
    path, args, profile, tag_ids = h._parse_radarr_body()
    assert path is None
    assert h._response_code == 400

  def test_download_event_extracts_path(self):
    payload = {
      "eventType": "Download",
      "movieFile": {"path": "/movies/Matrix.mkv"},
      "movie": {"tmdbId": 603},
    }
    h = self._radarr_body(payload)
    path, args, profile, tag_ids = h._parse_radarr_body()
    assert path == "/movies/Matrix.mkv"
    assert "--movie" in args
    assert "-tmdb" in args
    assert "603" in args
    assert profile is None
    assert tag_ids == []

  def test_missing_movie_file_path_returns_400(self):
    payload = {
      "eventType": "Download",
      "movieFile": {},
      "movie": {},
    }
    h = self._radarr_body(payload)
    path, args, profile, tag_ids = h._parse_radarr_body()
    assert path is None
    assert h._response_code == 400

  def test_uses_imdb_when_no_tmdb(self):
    payload = {
      "eventType": "Download",
      "movieFile": {"path": "/movies/film.mkv"},
      "movie": {"imdbId": "tt0133093"},
    }
    h = self._radarr_body(payload)
    path, args, profile, tag_ids = h._parse_radarr_body()
    assert "-imdb" in args
    assert "tt0133093" in args

  def test_extracts_tmdb_id_from_path_when_not_in_payload(self):
    payload = {
      "eventType": "Download",
      "movieFile": {"path": "/movies/The Matrix (1999) {tmdb-603}/Matrix.mkv"},
      "movie": {},
    }
    h = self._radarr_body(payload)
    path, args, profile, tag_ids = h._parse_radarr_body()
    assert "-tmdb" in args
    assert "603" in args

  def test_path_tmdb_not_used_when_payload_has_tmdb(self):
    payload = {
      "eventType": "Download",
      "movieFile": {"path": "/movies/Film {tmdb-999}/film.mkv"},
      "movie": {"tmdbId": 603},
    }
    h = self._radarr_body(payload)
    path, args, profile, tag_ids = h._parse_radarr_body()
    assert "603" in args
    assert "999" not in args

  def test_extracts_profile_override_from_movie_tag_label(self):
    payload = {
      "eventType": "Download",
      "movieFile": {"path": "/movies/film.mkv"},
      "movie": {"tags": ["sma-profile-rq"]},
    }
    h = self._radarr_body(payload)
    path, args, profile, tag_ids = h._parse_radarr_body()
    assert path == "/movies/film.mkv"
    assert profile == "rq"
    assert tag_ids == []

  def test_invalid_json_returns_400(self):
    body = b"not json"
    h = _make_handler(body=body, headers={"Content-Length": str(len(body)), "Content-Type": "application/json"})
    path, args, profile, tag_ids = h._parse_radarr_body()
    assert path is None
    assert h._response_code == 400


# ---------------------------------------------------------------------------
# TestDispatchPath
# ---------------------------------------------------------------------------


class TestDispatchPath:
  def test_nonexistent_path_returns_400(self):
    h = _make_handler()
    h._dispatch_path("/nonexistent/file.mkv", [])
    assert h._response_code == 400
    body = _get_response_body(h)
    assert "does not exist" in body["error"]

  def test_recycle_bin_path_returns_400(self):
    h = _make_handler()
    h.server.path_config_manager.is_recycle_bin_path.return_value = True
    with patch("os.path.exists", return_value=True):
      h._dispatch_path("/recycle/file.mkv", [])
    assert h._response_code == 400
    body = _get_response_body(h)
    assert "recycle" in body["error"]

  def test_queues_file_when_path_is_file(self, tmp_path):
    media_file = tmp_path / "movie.mkv"
    media_file.write_text("fake media")
    h = _make_handler()
    h.server.job_db.add_job.return_value = 1
    h._dispatch_path(str(media_file), [])
    assert h._response_code == 202

  def test_queues_directory_when_path_is_dir(self, tmp_path):
    (tmp_path / "movie.mkv").write_text("x")
    h = _make_handler()
    h._dispatch_path(str(tmp_path), [])
    # dir with media returns 202
    assert h._response_code == 202


# ---------------------------------------------------------------------------
# TestQueueFile
# ---------------------------------------------------------------------------


class TestQueueFile:
  def test_returns_202_with_job_id(self, tmp_path):
    f = tmp_path / "movie.mkv"
    f.write_text("x")
    h = _make_handler()
    h.server.job_db.add_job.return_value = 7
    h._queue_file(str(f), [], None)
    body = _get_response_body(h)
    assert body["job_id"] == 7
    assert body["status"] == "queued"

  def test_duplicate_returns_200(self, tmp_path):
    f = tmp_path / "movie.mkv"
    f.write_text("x")
    h = _make_handler()
    h.server.job_db.add_job.return_value = None
    h.server.job_db.find_active_job.return_value = {"id": 3}
    h._queue_file(str(f), [], None)
    body = _get_response_body(h)
    assert body["status"] == "duplicate"
    assert body["job_id"] == 3

  def test_notify_workers_called_on_success(self, tmp_path):
    f = tmp_path / "movie.mkv"
    f.write_text("x")
    h = _make_handler()
    h.server.job_db.add_job.return_value = 5
    h._queue_file(str(f), [], None)
    h.server.notify_workers.assert_called_once()

  def test_skips_when_extension_matches_output_no_force(self, tmp_path):
    f = tmp_path / "movie.mp4"
    f.write_text("x")
    h = _make_handler()
    h.server.path_config_manager.should_skip_same_extension.return_value = True
    h._queue_file(str(f), [], None)
    body = _get_response_body(h)
    assert body["status"] == "skipped"
    assert body["reason"] == "same_as_output_extension"
    h.server.job_db.add_job.assert_not_called()


# ---------------------------------------------------------------------------
# TestQueueDirectory
# ---------------------------------------------------------------------------


class TestQueueDirectory:
  def test_empty_directory_returns_empty_status(self, tmp_path):
    h = _make_handler()
    h._queue_directory(str(tmp_path), [], None)
    body = _get_response_body(h)
    assert body["status"] == "empty"

  def test_queues_media_files(self, tmp_path):
    (tmp_path / "movie.mkv").write_text("x")
    h = _make_handler()
    h.server.job_db.add_job.return_value = 1
    h._queue_directory(str(tmp_path), [], None)
    body = _get_response_body(h)
    assert body["status"] == "queued"
    assert len(body["queued"]) == 1

  def test_tracks_duplicates_when_job_already_exists(self, tmp_path):
    movie = tmp_path / "movie.mkv"
    movie.write_text("x")
    h = _make_handler()
    h.server.job_db.add_job.return_value = None
    h.server.job_db.find_active_job.return_value = {"id": 8}
    h._queue_directory(str(tmp_path), [], None)
    body = _get_response_body(h)
    assert body["queued_count"] == 0
    assert body["duplicate_count"] == 1
    assert body["duplicates"][0]["job_id"] == 8

  def test_skips_files_with_same_output_extension(self, tmp_path):
    """process-same-extensions: false → mp4 files in a submitted dir
    are not queued (matches the worker-time no-op detection)."""
    (tmp_path / "movie.mkv").write_text("x")  # should queue
    (tmp_path / "already.mp4").write_text("x")  # should skip
    h = _make_handler()
    h.server.job_db.add_job.return_value = 1
    # only mp4 paths should be reported as same-extension
    h.server.path_config_manager.should_skip_same_extension.side_effect = lambda p: p.lower().endswith(".mp4")
    h._queue_directory(str(tmp_path), [], None)
    body = _get_response_body(h)
    assert body["queued_count"] == 1
    assert body["skipped_count"] == 1
    assert body["skipped"][0]["path"].endswith("already.mp4")
    assert body["skipped"][0]["reason"] == "same_as_output_extension"
    # add_job called only for the mkv
    assert h.server.job_db.add_job.call_count == 1

  def test_does_not_skip_when_force_convert_true(self, tmp_path):
    """should_skip_same_extension returning False (e.g. force-convert: true)
    keeps the mp4 queued."""
    (tmp_path / "already.mp4").write_text("x")
    h = _make_handler()
    h.server.job_db.add_job.return_value = 9
    h.server.path_config_manager.should_skip_same_extension.return_value = False
    h._queue_directory(str(tmp_path), [], None)
    body = _get_response_body(h)
    assert body["queued_count"] == 1
    assert body["skipped_count"] == 0

  def test_resolve_config_uses_existing_override(self, tmp_path):
    cfg = tmp_path / "alt.ini"
    cfg.write_text("")
    h = _make_handler()
    resolved = h._resolve_config("/media/movie.mkv", str(cfg))
    assert resolved == str(cfg)
    h.server.path_config_manager.get_config_for_path.assert_not_called()

  def test_resolve_config_falls_back_when_override_missing(self):
    h = _make_handler()
    resolved = h._resolve_config("/media/movie.mkv", "/missing/config.ini")
    assert resolved == "/config/sma-ng.yml"
    h.server.path_config_manager.get_config_for_path.assert_called_once_with("/media/movie.mkv")


# ---------------------------------------------------------------------------
# TestBulkJobsAction
# ---------------------------------------------------------------------------


class TestBulkJobsAction:
  """``POST /jobs/bulk`` — used by the dashboard's multi-select toolbar."""

  def _make(self, body):
    payload = json.dumps(body).encode("utf-8")
    h = _make_handler(method="POST", path="/jobs/bulk", body=payload, headers={"Content-Length": str(len(payload))})
    return h

  def test_invalid_action_returns_400(self):
    h = self._make({"action": "explode", "ids": [1]})
    h._post_jobs_bulk()
    body = _get_response_body(h)
    assert h._response_code == 400
    assert "action must be one of" in body["error"]

  def test_empty_ids_returns_400(self):
    h = self._make({"action": "requeue", "ids": []})
    h._post_jobs_bulk()
    assert h._response_code == 400

  def test_non_integer_ids_returns_400(self):
    h = self._make({"action": "requeue", "ids": ["abc"]})
    h._post_jobs_bulk()
    assert h._response_code == 400

  def test_requeue_succeeds_and_notifies(self):
    h = self._make({"action": "requeue", "ids": [1, 2]})
    h.server.job_db.get_job.side_effect = lambda jid: {"id": jid, "status": "failed"}
    h.server.job_db.requeue_job.return_value = True
    h._post_jobs_bulk()
    body = _get_response_body(h)
    assert body["action"] == "requeue"
    assert body["succeeded"] == [1, 2]
    assert body["skipped"] == []
    h.server.notify_workers.assert_called_once()

  def test_requeue_skips_non_failed_jobs(self):
    h = self._make({"action": "requeue", "ids": [1, 2]})
    h.server.job_db.get_job.side_effect = lambda jid: {"id": jid, "status": "completed" if jid == 2 else "failed"}
    h.server.job_db.requeue_job.side_effect = lambda jid: jid == 1
    h._post_jobs_bulk()
    body = _get_response_body(h)
    assert body["succeeded"] == [1]
    assert body["skipped"] == [{"id": 2, "reason": "not_failed", "status": "completed"}]

  def test_requeue_not_found_marked(self):
    h = self._make({"action": "requeue", "ids": [99]})
    h.server.job_db.get_job.return_value = None
    h._post_jobs_bulk()
    body = _get_response_body(h)
    assert body["succeeded"] == []
    assert body["not_found"] == [99]

  def test_cancel_succeeds(self):
    h = self._make({"action": "cancel", "ids": [5]})
    h.server.job_db.get_job.return_value = {"id": 5, "status": "running"}
    h.server.cancel_job.return_value = True
    h._post_jobs_bulk()
    body = _get_response_body(h)
    assert body["succeeded"] == [5]

  def test_cancel_skips_completed(self):
    h = self._make({"action": "cancel", "ids": [5]})
    h.server.job_db.get_job.return_value = {"id": 5, "status": "completed"}
    h.server.cancel_job.return_value = False
    h._post_jobs_bulk()
    body = _get_response_body(h)
    assert body["skipped"] == [{"id": 5, "reason": "not_cancellable", "status": "completed"}]

  def test_delete_calls_db_and_returns_deleted_ids(self):
    h = self._make({"action": "delete", "ids": [1, 2, 3]})
    h.server.job_db.get_job.return_value = {"id": 1, "status": "completed"}
    h.server.job_db.delete_jobs.return_value = [1, 3]
    h._post_jobs_bulk()
    body = _get_response_body(h)
    assert body["action"] == "delete"
    assert body["succeeded"] == [1, 3]
    assert body["not_found"] == [2]

  def test_delete_cancels_running_jobs_first(self):
    """Running subprocesses must be cancelled before their rows disappear
    so we don't orphan the ffmpeg child process."""
    h = self._make({"action": "delete", "ids": [7]})
    h.server.job_db.get_job.return_value = {"id": 7, "status": "running"}
    h.server.job_db.delete_jobs.return_value = [7]
    h._post_jobs_bulk()
    h.server.cancel_job.assert_called_with(7)


# ---------------------------------------------------------------------------
# TestMergeArgs
# ---------------------------------------------------------------------------


class TestMergeArgs:
  def test_no_defaults_returns_extra_args(self):
    h = _make_handler()
    h.server.path_config_manager.get_args_for_path.return_value = []
    result = h._merge_args("/movie.mkv", ["-tmdb", "603"])
    assert result == ["-tmdb", "603"]

  def test_defaults_prepended_to_extra_args(self):
    h = _make_handler()
    h.server.path_config_manager.get_args_for_path.return_value = ["-movie"]
    result = h._merge_args("/movie.mkv", ["-tmdb", "603"])
    assert result == ["-movie", "-tmdb", "603"]

  def test_caller_flag_overrides_default(self):
    h = _make_handler()
    h.server.path_config_manager.get_args_for_path.return_value = ["-movie"]
    result = h._merge_args("/movie.mkv", ["-movie", "-tmdb", "603"])
    assert result.count("-movie") == 1


# ---------------------------------------------------------------------------
# TestPostJobRequeue
# ---------------------------------------------------------------------------


class TestPostJobRequeue:
  def test_requeues_job_and_returns_200(self):
    h = _make_handler(method="POST")
    h.server.job_db.requeue_job.return_value = True
    h._post_job_requeue("/jobs/5/requeue")
    body = _get_response_body(h)
    assert body["requeued"] is True
    assert body["job_id"] == 5
    h.server.notify_workers.assert_called_once()

  def test_job_not_found_returns_404(self):
    h = _make_handler(method="POST")
    h.server.job_db.requeue_job.return_value = False
    h.server.job_db.get_job.return_value = None
    h._post_job_requeue("/jobs/99/requeue")
    assert h._response_code == 404

  def test_non_requeue_status_returns_409(self):
    h = _make_handler(method="POST")
    h.server.job_db.requeue_job.return_value = False
    h.server.job_db.get_job.return_value = {"id": 5, "status": "running"}
    h._post_job_requeue("/jobs/5/requeue")
    assert h._response_code == 409

  def test_invalid_job_id_returns_400(self):
    h = _make_handler(method="POST")
    h._post_job_requeue("/jobs/abc/requeue")
    assert h._response_code == 400


# ---------------------------------------------------------------------------
# TestPostJobCancel
# ---------------------------------------------------------------------------


class TestPostJobCancel:
  def test_cancels_job_and_returns_200(self):
    h = _make_handler(method="POST")
    h.server.cancel_job.return_value = True
    h._post_job_cancel("/jobs/3/cancel")
    body = _get_response_body(h)
    assert body["cancelled"] is True
    assert body["job_id"] == 3

  def test_job_not_found_returns_404(self):
    h = _make_handler(method="POST")
    h.server.cancel_job.return_value = False
    h.server.job_db.get_job.return_value = None
    h._post_job_cancel("/jobs/99/cancel")
    assert h._response_code == 404

  def test_cannot_cancel_returns_409(self):
    h = _make_handler(method="POST")
    h.server.cancel_job.return_value = False
    h.server.job_db.get_job.return_value = {"id": 3, "status": "completed"}
    h._post_job_cancel("/jobs/3/cancel")
    assert h._response_code == 409


# ---------------------------------------------------------------------------
# TestPostJobPriority
# ---------------------------------------------------------------------------


class TestPostJobPriority:
  def _make_priority_handler(self, job_id, priority):
    body = json.dumps({"priority": priority}).encode()
    h = _make_handler(
      method="POST",
      path="/jobs/%d/priority" % job_id,
      body=body,
      headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
    )
    return h

  def test_sets_priority_and_returns_200(self):
    h = self._make_priority_handler(10, 5)
    h.server.job_db.set_job_priority.return_value = True
    h._post_job_priority("/jobs/10/priority")
    body = _get_response_body(h)
    assert body["job_id"] == 10
    assert body["priority"] == 5

  def test_missing_priority_field_returns_400(self):
    data = json.dumps({}).encode()
    h = _make_handler(
      method="POST",
      body=data,
      headers={"Content-Length": str(len(data)), "Content-Type": "application/json"},
    )
    h._post_job_priority("/jobs/10/priority")
    assert h._response_code == 400

  def test_non_integer_priority_returns_400(self):
    data = json.dumps({"priority": "high"}).encode()
    h = _make_handler(
      method="POST",
      body=data,
      headers={"Content-Length": str(len(data)), "Content-Type": "application/json"},
    )
    h._post_job_priority("/jobs/10/priority")
    assert h._response_code == 400

  def test_job_not_found_returns_404(self):
    h = self._make_priority_handler(99, 1)
    h.server.job_db.set_job_priority.return_value = False
    h.server.job_db.get_job.return_value = None
    h._post_job_priority("/jobs/99/priority")
    assert h._response_code == 404

  def test_non_pending_job_returns_409(self):
    h = self._make_priority_handler(5, 1)
    h.server.job_db.set_job_priority.return_value = False
    h.server.job_db.get_job.return_value = {"id": 5, "status": "running"}
    h._post_job_priority("/jobs/5/priority")
    assert h._response_code == 409

  def test_invalid_job_id_returns_400(self):
    data = json.dumps({"priority": 1}).encode()
    h = _make_handler(
      method="POST",
      body=data,
      headers={"Content-Length": str(len(data)), "Content-Type": "application/json"},
    )
    h._post_job_priority("/jobs/notanid/priority")
    assert h._response_code == 400


# ---------------------------------------------------------------------------
# TestPostJobAction
# ---------------------------------------------------------------------------


class TestPostJobAction:
  def test_unknown_action_returns_404(self):
    h = _make_handler(method="POST")
    h._post_job_action("/jobs/5/unknown")
    assert h._response_code == 404

  def test_routes_to_requeue(self):
    h = _make_handler(method="POST")
    h.server.job_db.requeue_job.return_value = True
    h._post_job_action("/jobs/5/requeue")
    assert h._response_code == 200

  def test_routes_to_cancel(self):
    h = _make_handler(method="POST")
    h.server.cancel_job.return_value = True
    h._post_job_action("/jobs/5/cancel")
    assert h._response_code == 200


# ---------------------------------------------------------------------------
# TestPostAdminEndpoints
# ---------------------------------------------------------------------------


class TestPostAdminEndpoints:
  def test_delete_failed_returns_count(self):
    h = _make_handler(method="POST")
    h.server.job_db.delete_failed_jobs.return_value = 4
    h._post_admin_delete_failed()
    body = _get_response_body(h)
    assert body["deleted"] == 4

  def test_delete_offline_nodes_returns_count(self):
    h = _make_handler(method="POST")
    h.server.job_db.delete_offline_nodes.return_value = 2
    h._post_admin_delete_offline_nodes()
    body = _get_response_body(h)
    assert body["deleted"] == 2

  def test_delete_all_jobs_returns_count(self):
    h = _make_handler(method="POST")
    h.server.job_db.delete_all_jobs.return_value = 50
    h._post_admin_delete_all_jobs()
    body = _get_response_body(h)
    assert body["deleted"] == 50


# ---------------------------------------------------------------------------
# TestPostJobsRequeueBulk
# ---------------------------------------------------------------------------


class TestPostJobsRequeueBulk:
  def test_requeues_all_failed(self):
    h = _make_handler(method="POST")
    h.server.job_db.requeue_failed_jobs.return_value = 5
    h._post_jobs_requeue_bulk({})
    body = _get_response_body(h)
    assert body["requeued"] == 5
    h.server.notify_workers.assert_called_once()

  def test_passes_config_filter(self):
    h = _make_handler(method="POST")
    h.server.job_db.requeue_failed_jobs.return_value = 0
    h._post_jobs_requeue_bulk({"config": ["/config/sma-ng.yml"]})
    h.server.job_db.requeue_failed_jobs.assert_called_once_with(config="/config/sma-ng.yml")

  def test_no_notify_when_zero_requeued(self):
    h = _make_handler(method="POST")
    h.server.job_db.requeue_failed_jobs.return_value = 0
    h._post_jobs_requeue_bulk({})
    h.server.notify_workers.assert_not_called()


# ---------------------------------------------------------------------------
# TestPostScanEndpoints
# ---------------------------------------------------------------------------


class TestPostScanFilter:
  def test_returns_unscanned_paths(self):
    body = json.dumps({"paths": ["/a.mkv", "/b.mkv"]}).encode()
    h = _make_handler(body=body, headers={"Content-Length": str(len(body)), "Content-Type": "application/json"})
    h.server.job_db.filter_unscanned.return_value = ["/a.mkv"]
    h._post_scan_filter()
    result = _get_response_body(h)
    assert result["unscanned"] == ["/a.mkv"]
    assert result["total"] == 2

  def test_invalid_json_returns_400(self):
    body = b"not json"
    h = _make_handler(body=body, headers={"Content-Length": str(len(body)), "Content-Type": "application/json"})
    h._post_scan_filter()
    assert h._response_code == 400

  def test_non_list_paths_returns_400(self):
    body = json.dumps({"paths": "not-a-list"}).encode()
    h = _make_handler(body=body, headers={"Content-Length": str(len(body)), "Content-Type": "application/json"})
    h._post_scan_filter()
    assert h._response_code == 400


class TestPostScanRecord:
  def test_records_paths(self):
    body = json.dumps({"paths": ["/a.mkv", "/b.mkv"]}).encode()
    h = _make_handler(body=body, headers={"Content-Length": str(len(body)), "Content-Type": "application/json"})
    h._post_scan_record()
    result = _get_response_body(h)
    assert result["recorded"] == 2
    h.server.job_db.record_scanned.assert_called_once_with(["/a.mkv", "/b.mkv"])


# ---------------------------------------------------------------------------
# TestDoHead
# ---------------------------------------------------------------------------


class TestDoHead:
  def test_returns_200(self):
    h = _make_handler(method="HEAD")
    h.do_HEAD()
    assert h._response_code == 200


# ---------------------------------------------------------------------------
# TestDoGetRouting
# ---------------------------------------------------------------------------


class TestDoGetRouting:
  def test_unknown_path_returns_404(self):
    h = _make_handler(path="/nonexistent")
    h.do_GET()
    body = _get_response_body(h)
    assert h._response_code == 404
    assert body["error"] == "Not found"

  def test_health_route(self):
    h = _make_handler(path="/health")
    h.do_GET()
    assert h._response_code == 200

  def test_stats_route(self):
    h = _make_handler(path="/stats")
    h.do_GET()
    assert h._response_code == 200

  def test_jobs_route(self):
    h = _make_handler(path="/jobs")
    h.do_GET()
    assert h._response_code == 200

  def test_jobs_slash_id_route(self):
    h = _make_handler(path="/jobs/42")
    h.server.job_db.get_job.return_value = {"id": 42, "path": "/f.mkv", "status": "pending"}
    h.do_GET()
    assert h._response_code == 200

  def test_auth_failure_blocks_protected_routes(self):
    h = _make_handler(path="/jobs", api_key="secret", headers={})
    h.do_GET()
    assert h._response_code == 401

  def test_health_is_accessible_without_auth(self):
    h = _make_handler(path="/health", api_key="secret", headers={})
    h.do_GET()
    assert h._response_code == 200

  def test_root_redirect(self):
    h = _make_handler(path="/")
    h.do_GET()
    assert h._response_code == 301
    assert h._response_headers.get("Location") == "/dashboard"


# ---------------------------------------------------------------------------
# TestDoPostRouting
# ---------------------------------------------------------------------------


class TestDoPostRouting:
  def test_unknown_path_returns_404(self):
    h = _make_handler(method="POST", path="/nonexistent")
    h.do_POST()
    body = _get_response_body(h)
    assert h._response_code == 404

  def test_auth_failure_blocks_post(self):
    h = _make_handler(method="POST", path="/cleanup", api_key="secret", headers={})
    h.do_POST()
    assert h._response_code == 401

  def test_cleanup_route(self):
    h = _make_handler(method="POST", path="/cleanup")
    h.do_POST()
    assert h._response_code == 200

  def test_jobs_job_id_action_prefix_route(self):
    h = _make_handler(method="POST", path="/jobs/5/requeue")
    h.server.job_db.requeue_job.return_value = True
    h.do_POST()
    assert h._response_code == 200

  def test_admin_delete_failed_route(self):
    h = _make_handler(method="POST", path="/admin/delete-failed")
    h.do_POST()
    assert h._response_code == 200


# ---------------------------------------------------------------------------
# TestTailLines
# ---------------------------------------------------------------------------


class TestTailLines:
  def test_returns_last_n_lines(self, tmp_path):
    f = tmp_path / "test.log"
    f.write_text("\n".join(str(i) for i in range(100)))
    lines = _tail_lines(str(f), 10)
    assert len(lines) == 10
    assert lines[-1] == "99"

  def test_returns_empty_for_empty_file(self, tmp_path):
    f = tmp_path / "empty.log"
    f.write_text("")
    lines = _tail_lines(str(f), 10)
    assert lines == []

  def test_returns_empty_for_nonexistent_file(self):
    lines = _tail_lines("/nonexistent/file.log", 10)
    assert lines == []

  def test_returns_all_lines_when_fewer_than_n(self, tmp_path):
    f = tmp_path / "small.log"
    f.write_text("line1\nline2\nline3")
    lines = _tail_lines(str(f), 100)
    assert "line1" in lines
    assert "line3" in lines


# ---------------------------------------------------------------------------
# TestReadFromOffset
# ---------------------------------------------------------------------------


class TestReadFromOffset:
  def test_reads_from_offset(self, tmp_path):
    f = tmp_path / "test.log"
    f.write_text("line1\nline2\nline3\n")
    # Skip first 6 bytes ("line1\n")
    lines = _read_from_offset(str(f), 6)
    assert "line2" in lines[0]

  def test_returns_empty_for_nonexistent_file(self):
    lines = _read_from_offset("/nonexistent/file.log", 0)
    assert lines == []

  def test_returns_empty_when_offset_at_end(self, tmp_path):
    f = tmp_path / "test.log"
    content = "line1\nline2\n"
    f.write_text(content)
    lines = _read_from_offset(str(f), len(content.encode()))
    assert lines == [] or all(l == "" for l in lines)


# ---------------------------------------------------------------------------
# TestWalkMediaFiles
# ---------------------------------------------------------------------------


class TestWalkMediaFiles:
  def test_yields_media_files(self, tmp_path):
    (tmp_path / "movie.mkv").write_text("x")
    (tmp_path / "other.txt").write_text("x")
    h = _make_handler()
    files = list(h._walk_media_files(str(tmp_path)))
    assert any("movie.mkv" in f for f in files)
    assert not any("other.txt" in f for f in files)

  def test_skips_hidden_files(self, tmp_path):
    (tmp_path / ".hidden.mkv").write_text("x")
    (tmp_path / "visible.mkv").write_text("x")
    h = _make_handler()
    files = list(h._walk_media_files(str(tmp_path)))
    assert not any(".hidden" in f for f in files)
    assert any("visible.mkv" in f for f in files)

  def test_recurses_into_subdirectories(self, tmp_path):
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "episode.mkv").write_text("x")
    h = _make_handler()
    files = list(h._walk_media_files(str(tmp_path)))
    assert any("episode.mkv" in f for f in files)

  def test_skips_hidden_subdirectories(self, tmp_path):
    hidden_sub = tmp_path / ".hidden_dir"
    hidden_sub.mkdir()
    (hidden_sub / "movie.mkv").write_text("x")
    h = _make_handler()
    files = list(h._walk_media_files(str(tmp_path)))
    assert not any("hidden_dir" in f for f in files)

  def test_handles_permission_error(self, tmp_path):
    h = _make_handler()
    with patch("os.scandir", side_effect=PermissionError):
      files = list(h._walk_media_files(str(tmp_path)))
    assert files == []


# ---------------------------------------------------------------------------
# TestGetBrowse
# ---------------------------------------------------------------------------


class TestGetBrowse:
  """/browse derives its allowed-roots set from daemon.routing[].match prefixes
  (and scan_paths). path_configs no longer exists."""

  def test_no_path_returns_configured_roots(self, tmp_path):
    h = _make_handler()
    h.server.path_config_manager.routing_match_paths.return_value = [str(tmp_path)]
    h._get_browse({})
    body = _get_response_body(h)
    assert "dirs" in body
    assert "files" in body

  def test_path_outside_allowed_roots_returns_403(self, tmp_path):
    h = _make_handler()
    h.server.path_config_manager.routing_match_paths.return_value = [str(tmp_path / "media")]
    h._get_browse({"path": ["/completely/other/path"]})
    assert h._response_code == 403

  def test_nonexistent_directory_returns_404(self, tmp_path):
    allowed = tmp_path / "media"
    allowed.mkdir()
    h = _make_handler()
    h.server.path_config_manager.routing_match_paths.return_value = [str(allowed)]
    h._get_browse({"path": [str(allowed / "nonexistent")]})
    assert h._response_code == 404

  def test_lists_dirs_and_media_files(self, tmp_path):
    allowed = tmp_path / "media"
    allowed.mkdir()
    sub = allowed / "subdir"
    sub.mkdir()
    (allowed / "movie.mkv").write_text("x")
    (allowed / "readme.txt").write_text("x")
    h = _make_handler()
    h.server.path_config_manager.routing_match_paths.return_value = [str(allowed)]
    h._get_browse({"path": [str(allowed)]})
    body = _get_response_body(h)
    assert any("subdir" in d for d in body["dirs"])
    assert any("movie.mkv" in f for f in body["files"])
    assert not any("readme.txt" in f for f in body["files"])


# ---------------------------------------------------------------------------
# TestHandleWebhook (integration-level)
# ---------------------------------------------------------------------------


class TestHandleWebhook:
  def test_empty_body_returns_400(self):
    h = _make_handler(method="POST", body=b"", headers={"Content-Length": "0"})
    h._handle_webhook()
    assert h._response_code == 400

  def test_nonexistent_path_returns_400(self):
    body = json.dumps({"path": "/nonexistent/file.mkv"}).encode()
    h = _make_handler(
      method="POST",
      body=body,
      headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
    )
    h._handle_webhook()
    assert h._response_code == 400

  def test_valid_file_queued(self, tmp_path):
    media = tmp_path / "movie.mkv"
    media.write_text("x")
    body = json.dumps({"path": str(media)}).encode()
    h = _make_handler(
      method="POST",
      body=body,
      headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
    )
    h.server.job_db.add_job.return_value = 1
    h._handle_webhook()
    assert h._response_code == 202


class TestHandleSonarrWebhook:
  def test_test_event_returns_200(self):
    body = json.dumps({"eventType": "Test"}).encode()
    h = _make_handler(
      method="POST",
      body=body,
      headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
    )
    h._handle_sonarr_webhook()
    assert h._response_code == 200

  def test_exception_returns_500(self):
    body = json.dumps({"eventType": "Download", "episodeFile": {"path": "/tv/ep.mkv"}, "series": {}, "episodes": []}).encode()
    h = _make_handler(
      method="POST",
      body=body,
      headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
    )
    h.server.path_config_manager.rewrite_path.side_effect = RuntimeError("boom")
    with patch("os.path.exists", return_value=True), patch("os.path.isdir", return_value=False):
      h._handle_sonarr_webhook()
    assert h._response_code == 500

  def test_applies_profile_override_from_arr_tag_lookup(self, tmp_path):
    media = tmp_path / "ep.mkv"
    media.write_text("x")
    body = json.dumps(
      {
        "eventType": "Download",
        "episodeFile": {"path": str(media)},
        "series": {"tags": [11]},
        "episodes": [],
      }
    ).encode()
    h = _make_handler(
      method="POST",
      body=body,
      headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
    )
    h.server.path_config_manager.get_services_for_path.return_value = ["sonarr.main"]
    h.server.path_config_manager.get_service_instance.return_value = {"url": "http://sonarr.local", "apikey": "secret"}
    h.server.path_config_manager.get_profile_for_path.return_value = "hq"
    h.server.job_db.add_job.return_value = 1

    with (
      patch("resources.daemon.handler.requests.get") as mock_get,
      patch("os.path.exists", return_value=True),
      patch("os.path.isdir", return_value=False),
    ):
      mock_get.return_value.raise_for_status.return_value = None
      mock_get.return_value.json.return_value = [{"id": 11, "label": "sma-profile-lq"}]
      h._handle_sonarr_webhook()

    queued_args = h.server.job_db.add_job.call_args[0][2]
    assert "--profile" in queued_args
    assert "lq" in queued_args
    assert "hq" not in queued_args


class TestHandleRadarrWebhook:
  def test_test_event_returns_200(self):
    body = json.dumps({"eventType": "Test"}).encode()
    h = _make_handler(
      method="POST",
      body=body,
      headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
    )
    h._handle_radarr_webhook()
    assert h._response_code == 200

  def test_exception_returns_500(self):
    body = json.dumps({"eventType": "Download", "movieFile": {"path": "/movies/film.mkv"}, "movie": {}}).encode()
    h = _make_handler(
      method="POST",
      body=body,
      headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
    )
    h.server.path_config_manager.rewrite_path.side_effect = RuntimeError("boom")
    with patch("os.path.exists", return_value=True), patch("os.path.isdir", return_value=False):
      h._handle_radarr_webhook()
    assert h._response_code == 500

  def test_applies_profile_override_from_movie_tag_label(self, tmp_path):
    media = tmp_path / "film.mkv"
    media.write_text("x")
    body = json.dumps(
      {
        "eventType": "Download",
        "movieFile": {"path": str(media)},
        "movie": {"tags": ["sma-profile-rq"]},
      }
    ).encode()
    h = _make_handler(
      method="POST",
      body=body,
      headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
    )
    h.server.path_config_manager.get_profile_for_path.return_value = "hq"
    h.server.job_db.add_job.return_value = 1

    with patch("os.path.exists", return_value=True), patch("os.path.isdir", return_value=False):
      h._handle_radarr_webhook()

    queued_args = h.server.job_db.add_job.call_args[0][2]
    assert "--profile" in queued_args
    assert "rq" in queued_args
    assert "hq" not in queued_args


# ---------------------------------------------------------------------------
# Cluster-only admin routes — exercise the is_distributed=False 503 path AND
# the happy path when distributed mode is enabled.
# ---------------------------------------------------------------------------


class TestClusterLogsRoute:
  def test_returns_503_when_not_distributed(self):
    h = _make_handler(is_distributed=False)
    h._get_cluster_logs("/cluster/logs", {})
    assert h._response_code == 503
    assert "distributed" in _get_response_body(h)["error"].lower()

  def test_returns_logs_in_distributed_mode(self):
    h = _make_handler(is_distributed=True)
    ts = datetime(2026, 5, 5, 10, 0, 0, tzinfo=UTC)
    h.server.job_db.get_logs.return_value = [
      {"timestamp": ts, "level": "INFO", "message": "hello", "node_id": "n1"},
    ]
    h._get_cluster_logs("/cluster/logs", {})
    assert h._response_code == 200
    body = _get_response_body(h)
    assert body["total"] == 1
    assert body["logs"][0]["timestamp"] == ts.isoformat()

  def test_filter_params_applied(self):
    h = _make_handler(is_distributed=True)
    h.server.job_db.get_logs.return_value = []
    h._get_cluster_logs(
      "/cluster/logs",
      {"node_id": ["n1"], "level": ["ERROR"], "limit": ["50"], "offset": ["10"]},
    )
    h.server.job_db.get_logs.assert_called_once_with(node_id="n1", level="ERROR", limit=50, offset=10)

  def test_limit_capped_at_500(self):
    h = _make_handler(is_distributed=True)
    h.server.job_db.get_logs.return_value = []
    h._get_cluster_logs("/cluster/logs", {"limit": ["999"]})
    _args, kw = h.server.job_db.get_logs.call_args
    assert kw["limit"] == 500


class TestAdminConfigRoutes:
  def test_get_admin_config_503_when_not_distributed(self):
    h = _make_handler(is_distributed=False)
    h._get_admin_config("/admin/config", {})
    assert h._response_code == 503

  def test_get_admin_config_returns_cluster_config(self):
    h = _make_handler(is_distributed=True)
    h.server.job_db.get_cluster_config.return_value = {"daemon": {"api_key": "x"}}
    h._get_admin_config("/admin/config", {})
    assert h._response_code == 200
    assert _get_response_body(h)["config"] == {"daemon": {"api_key": "x"}}

  def test_get_admin_config_handles_none_config(self):
    h = _make_handler(is_distributed=True)
    h.server.job_db.get_cluster_config.return_value = None
    h._get_admin_config("/admin/config", {})
    assert _get_response_body(h)["config"] == {}

  def test_post_admin_config_503_when_not_distributed(self):
    h = _make_handler(is_distributed=False)
    h._post_admin_config("/admin/config", {})
    assert h._response_code == 503

  def test_post_admin_config_invalid_json_returns_400(self):
    h = _make_handler(
      method="POST",
      body=b"not-json",
      headers={"Content-Length": "8"},
      is_distributed=True,
    )
    h._post_admin_config("/admin/config", {})
    assert h._response_code == 400

  def test_post_admin_config_non_dict_returns_400(self):
    body = json.dumps({"config": "not-a-dict"}).encode()
    h = _make_handler(
      method="POST",
      body=body,
      headers={"Content-Length": str(len(body))},
      is_distributed=True,
    )
    h._post_admin_config("/admin/config", {})
    assert h._response_code == 400

  def test_post_admin_config_strips_secrets_and_saves(self):
    body = json.dumps({"config": {"daemon": {"api_key": "leak"}}}).encode()
    h = _make_handler(
      method="POST",
      body=body,
      headers={"Content-Length": str(len(body)), "X-Actor": "ui-test"},
      is_distributed=True,
    )
    h._post_admin_config("/admin/config", {})
    assert h._response_code == 200
    h.server.job_db.set_cluster_config.assert_called_once()
    saved_config = h.server.job_db.set_cluster_config.call_args[0][0]
    # secrets stripping happens server-side; verify api_key is gone
    assert "api_key" not in saved_config.get("daemon", {})

  def test_post_admin_config_accepts_top_level_object(self):
    """Body without 'config' wrapper is treated as the config itself."""
    body = json.dumps({"converter": {"ffmpeg": "/usr/bin/ffmpeg"}}).encode()
    h = _make_handler(
      method="POST",
      body=body,
      headers={"Content-Length": str(len(body))},
      is_distributed=True,
    )
    h._post_admin_config("/admin/config", {})
    assert h._response_code == 200


class TestMetricsRoute:
  def test_returns_503_when_not_distributed(self):
    h = _make_handler(is_distributed=False)
    h._get_metrics_api("/metrics", {})
    assert h._response_code == 503
    body = _get_response_body(h)
    assert body["available"] is False
    assert "PostgreSQL" in body["reason"]

  def test_default_window_is_24h(self):
    h = _make_handler(is_distributed=True)
    h.server.job_db.get_metrics.return_value = {"jobs": []}
    h._get_metrics_api("/metrics", {})
    h.server.job_db.get_metrics.assert_called_once_with(window="24h")

  def test_unknown_window_falls_back_to_24h(self):
    h = _make_handler(is_distributed=True)
    h.server.job_db.get_metrics.return_value = {}
    h._get_metrics_api("/metrics", {"window": ["forever"]})
    h.server.job_db.get_metrics.assert_called_with(window="24h")

  @pytest.mark.parametrize("window", ["24h", "7d", "30d", "all"])
  def test_valid_windows(self, window):
    h = _make_handler(is_distributed=True)
    h.server.job_db.get_metrics.return_value = {}
    h._get_metrics_api("/metrics", {"window": [window]})
    h.server.job_db.get_metrics.assert_called_with(window=window)


# ---------------------------------------------------------------------------
# Admin node actions
# ---------------------------------------------------------------------------


class TestAdminNodeActions:
  def test_unknown_path_returns_404(self):
    h = _make_handler(method="POST", path="/admin/wat")
    h._post_admin_node_action("/admin/wat")
    assert h._response_code == 404

  def test_unknown_action_returns_404(self):
    h = _make_handler(method="POST", path="/admin/nodes/n1/banana")
    h._post_admin_node_action("/admin/nodes/n1/banana")
    assert h._response_code == 404

  def test_approve_with_valid_body(self):
    body = json.dumps({"note": "looks good"}).encode()
    h = _make_handler(
      method="POST",
      path="/admin/nodes/n1/approve",
      body=body,
      headers={"Content-Length": str(len(body))},
    )
    h._post_admin_node_action("/admin/nodes/n1/approve")
    assert h._response_code == 200
    body_out = _get_response_body(h)
    assert body_out["status"] == "approved"

  def test_approve_with_no_body(self):
    h = _make_handler(method="POST", path="/admin/nodes/n1/approve")
    h._post_admin_node_action("/admin/nodes/n1/approve")
    assert h._response_code == 200

  def test_approve_with_invalid_json_returns_400(self):
    h = _make_handler(
      method="POST",
      path="/admin/nodes/n1/approve",
      body=b"not-json",
      headers={"Content-Length": "8"},
    )
    h._post_admin_node_action("/admin/nodes/n1/approve")
    assert h._response_code == 400

  def test_reject_action(self):
    h = _make_handler(method="POST", path="/admin/nodes/n1/reject")
    h._post_admin_node_action("/admin/nodes/n1/reject")
    assert h._response_code == 200
    body = _get_response_body(h)
    assert body["status"] == "rejected"

  def test_approve_unknown_node_returns_404(self):
    h = _make_handler(method="POST", path="/admin/nodes/missing/approve")
    h.server.job_db.set_node_approval.return_value = None
    h._post_admin_node_action("/admin/nodes/missing/approve")
    assert h._response_code == 404

  @pytest.mark.parametrize("action", ["restart", "shutdown", "drain", "pause", "resume"])
  def test_lifecycle_actions(self, action):
    h = _make_handler(method="POST", path=f"/admin/nodes/n1/{action}")
    h.server.job_db.send_node_command.return_value = ["n1"]
    h._post_admin_node_action(f"/admin/nodes/n1/{action}")
    assert h._response_code == 202
    body = _get_response_body(h)
    assert body["status"] == f"{action}_requested"

  def test_lifecycle_action_for_unknown_node_returns_404(self):
    h = _make_handler(method="POST", path="/admin/nodes/missing/restart")
    h.server.job_db.send_node_command.return_value = []
    h._post_admin_node_action("/admin/nodes/missing/restart")
    assert h._response_code == 404

  def test_delete_action(self):
    h = _make_handler(method="POST", path="/admin/nodes/n1/delete")
    h.server.job_db.delete_node.return_value = True
    h._post_admin_node_action("/admin/nodes/n1/delete")
    assert h._response_code == 200
    body = _get_response_body(h)
    assert body["deleted"] is True

  def test_delete_unknown_node_returns_404(self):
    h = _make_handler(method="POST", path="/admin/nodes/missing/delete")
    h.server.job_db.delete_node.return_value = False
    h._post_admin_node_action("/admin/nodes/missing/delete")
    assert h._response_code == 404


# ---------------------------------------------------------------------------
# Bulk job actions
# ---------------------------------------------------------------------------


class TestBulkJobActions:
  def test_invalid_json_returns_400(self):
    h = _make_handler(
      method="POST",
      body=b"junk",
      headers={"Content-Length": "4"},
    )
    h._post_jobs_bulk()
    assert h._response_code == 400

  def test_missing_action_returns_400(self):
    body = json.dumps({"ids": [1]}).encode()
    h = _make_handler(method="POST", body=body, headers={"Content-Length": str(len(body))})
    h._post_jobs_bulk()
    assert h._response_code == 400

  def test_unknown_action_returns_400(self):
    body = json.dumps({"action": "magic", "ids": [1]}).encode()
    h = _make_handler(method="POST", body=body, headers={"Content-Length": str(len(body))})
    h._post_jobs_bulk()
    assert h._response_code == 400

  def test_empty_ids_returns_400(self):
    body = json.dumps({"action": "requeue", "ids": []}).encode()
    h = _make_handler(method="POST", body=body, headers={"Content-Length": str(len(body))})
    h._post_jobs_bulk()
    assert h._response_code == 400

  def test_non_integer_ids_returns_400(self):
    body = json.dumps({"action": "requeue", "ids": ["abc"]}).encode()
    h = _make_handler(method="POST", body=body, headers={"Content-Length": str(len(body))})
    h._post_jobs_bulk()
    assert h._response_code == 400

  def test_requeue_partitions_results(self):
    body = json.dumps({"action": "requeue", "ids": [1, 2, 3]}).encode()
    h = _make_handler(method="POST", body=body, headers={"Content-Length": str(len(body))})
    h.server.job_db.get_job.side_effect = [
      {"id": 1, "status": "failed"},
      {"id": 2, "status": "completed"},
      None,
    ]
    h.server.job_db.requeue_job.side_effect = [True, False]
    h._post_jobs_bulk()
    assert h._response_code == 200
    body = _get_response_body(h)
    assert 1 in body["succeeded"]
    assert any(s["id"] == 2 for s in body["skipped"])
    assert 3 in body["not_found"]

  def test_cancel_partitions_results(self):
    body = json.dumps({"action": "cancel", "ids": [1, 2]}).encode()
    h = _make_handler(method="POST", body=body, headers={"Content-Length": str(len(body))})
    h.server.job_db.get_job.side_effect = [
      {"id": 1, "status": "running"},
      {"id": 2, "status": "completed"},
    ]
    h.server.cancel_job.side_effect = [True, False]
    h._post_jobs_bulk()
    assert h._response_code == 200
    body = _get_response_body(h)
    assert 1 in body["succeeded"]
    assert any(s["id"] == 2 for s in body["skipped"])

  def test_delete_cancels_running_first(self):
    body = json.dumps({"action": "delete", "ids": [1, 2]}).encode()
    h = _make_handler(method="POST", body=body, headers={"Content-Length": str(len(body))})
    h.server.job_db.get_job.side_effect = [
      {"id": 1, "status": "running"},
      {"id": 1, "status": "running"},  # called twice in nested check
      {"id": 2, "status": "failed"},
      {"id": 2, "status": "failed"},
    ]
    h.server.job_db.delete_jobs.return_value = [1, 2]
    h._post_jobs_bulk()
    assert h._response_code == 200
    h.server.cancel_job.assert_called_with(1)


class TestPostJobPriority:
  def test_invalid_id_returns_400(self):
    h = _make_handler(method="POST", path="/jobs/abc/priority")
    h._post_job_priority("/jobs/abc/priority")
    assert h._response_code == 400

  def test_invalid_json_returns_400(self):
    h = _make_handler(
      method="POST",
      path="/jobs/1/priority",
      body=b"junk",
      headers={"Content-Length": "4"},
    )
    h._post_job_priority("/jobs/1/priority")
    assert h._response_code == 400

  def test_missing_priority_field_returns_400(self):
    body = json.dumps({}).encode()
    h = _make_handler(
      method="POST",
      path="/jobs/1/priority",
      body=body,
      headers={"Content-Length": str(len(body))},
    )
    h._post_job_priority("/jobs/1/priority")
    assert h._response_code == 400

  def test_non_integer_priority_returns_400(self):
    body = json.dumps({"priority": "high"}).encode()
    h = _make_handler(
      method="POST",
      path="/jobs/1/priority",
      body=body,
      headers={"Content-Length": str(len(body))},
    )
    h._post_job_priority("/jobs/1/priority")
    assert h._response_code == 400

  def test_unknown_job_returns_404(self):
    body = json.dumps({"priority": 5}).encode()
    h = _make_handler(
      method="POST",
      path="/jobs/9999/priority",
      body=body,
      headers={"Content-Length": str(len(body))},
    )
    h.server.job_db.set_job_priority.return_value = False
    h.server.job_db.get_job.return_value = None
    h._post_job_priority("/jobs/9999/priority")
    assert h._response_code == 404

  def test_non_pending_job_returns_409(self):
    body = json.dumps({"priority": 5}).encode()
    h = _make_handler(
      method="POST",
      path="/jobs/1/priority",
      body=body,
      headers={"Content-Length": str(len(body))},
    )
    h.server.job_db.set_job_priority.return_value = False
    h.server.job_db.get_job.return_value = {"id": 1, "status": "running"}
    h._post_job_priority("/jobs/1/priority")
    assert h._response_code == 409

  def test_happy_path(self):
    body = json.dumps({"priority": 7}).encode()
    h = _make_handler(
      method="POST",
      path="/jobs/1/priority",
      body=body,
      headers={"Content-Length": str(len(body))},
    )
    h.server.job_db.set_job_priority.return_value = True
    h._post_job_priority("/jobs/1/priority")
    assert h._response_code == 200
    assert _get_response_body(h)["priority"] == 7


class TestSimpleAdminRoutes:
  def test_post_admin_delete_failed(self):
    h = _make_handler(method="POST", path="/admin/jobs/failed")
    h._post_admin_delete_failed()
    assert h._response_code == 200
    assert _get_response_body(h)["deleted"] == 2

  def test_post_admin_delete_offline_nodes(self):
    h = _make_handler(method="POST", path="/admin/nodes/offline")
    h._post_admin_delete_offline_nodes()
    assert h._response_code == 200
    assert _get_response_body(h)["deleted"] == 1

  def test_post_admin_delete_all_jobs(self):
    h = _make_handler(method="POST", path="/admin/jobs")
    h._post_admin_delete_all_jobs()
    assert h._response_code == 200
    assert _get_response_body(h)["deleted"] == 10


class TestParseJobId:
  def test_invalid_id_sends_400(self):
    h = _make_handler(method="POST", path="/jobs/notanid/cancel")
    out = h._parse_job_id("/jobs/notanid/cancel")
    assert out is None
    assert h._response_code == 400

  def test_valid_id(self):
    h = _make_handler(method="POST", path="/jobs/42/cancel")
    assert h._parse_job_id("/jobs/42/cancel") == 42

  def test_segment_minus_1(self):
    h = _make_handler(method="POST", path="/jobs/42")
    assert h._parse_job_id("/jobs/42", segment=-1) == 42

  def test_index_error_returns_400(self):
    h = _make_handler(method="POST", path="/")
    assert h._parse_job_id("/", segment=-5) is None
    assert h._response_code == 400


class TestPostScanRoutes:
  def test_post_scan_filter_returns_unscanned(self):
    body = json.dumps({"paths": ["/a", "/b", "/c"]}).encode()
    h = _make_handler(method="POST", body=body, headers={"Content-Length": str(len(body))})
    h.server.job_db.filter_unscanned.return_value = ["/a"]
    h._post_scan_filter()
    assert h._response_code == 200
    body = _get_response_body(h)
    assert body["unscanned"] == ["/a"]
    assert body["already_scanned"] == 2

  def test_post_scan_record(self):
    body = json.dumps({"paths": ["/a", "/b"]}).encode()
    h = _make_handler(method="POST", body=body, headers={"Content-Length": str(len(body))})
    h._post_scan_record()
    assert h._response_code == 200
    assert _get_response_body(h)["recorded"] == 2


class TestRequeueBulkRoute:
  def test_requeue_with_no_config(self):
    h = _make_handler(method="POST", path="/jobs/requeue")
    h.server.job_db.requeue_failed_jobs.return_value = 7
    h._post_jobs_requeue_bulk({})
    assert h._response_code == 200
    assert _get_response_body(h)["requeued"] == 7
    h.server.notify_workers.assert_called_once()

  def test_requeue_with_no_jobs_skips_notify(self):
    h = _make_handler(method="POST", path="/jobs/requeue")
    h.server.job_db.requeue_failed_jobs.return_value = 0
    h._post_jobs_requeue_bulk({})
    h.server.notify_workers.assert_not_called()

  def test_requeue_with_config_filter(self):
    h = _make_handler(method="POST", path="/jobs/requeue")
    h.server.job_db.requeue_failed_jobs.return_value = 3
    h._post_jobs_requeue_bulk({"config": ["/config/sma-ng.yml"]})
    h.server.job_db.requeue_failed_jobs.assert_called_once_with(config="/config/sma-ng.yml")


# ---------------------------------------------------------------------------
# Library audit routes
# ---------------------------------------------------------------------------


class TestLibraryAuditRoutes:
  def test_get_library_audit_returns_runs(self):
    h = _make_handler()
    h.server.job_db.list_audit_runs.return_value = [{"id": 1, "status": "completed"}]
    h._get_library_audit({"limit": ["10"]})
    assert h._response_code == 200
    body = _get_response_body(h)
    assert body["count"] == 1
    assert body["limit"] == 10

  def test_get_library_audit_run_unknown_returns_404(self):
    h = _make_handler()
    h.server.job_db.get_audit_run.return_value = None
    h._get_library_audit_run("/library/audit/9999")
    assert h._response_code == 404

  def test_get_library_audit_run_returns_run(self):
    h = _make_handler()
    h.server.job_db.get_audit_run.return_value = {"id": 1, "status": "running"}
    h._get_library_audit_run("/library/audit/1")
    assert h._response_code == 200
    body = _get_response_body(h)
    assert body["id"] == 1

  def test_get_library_audit_run_invalid_id_returns_400(self):
    h = _make_handler()
    h._get_library_audit_run("/library/audit/abc")
    assert h._response_code == 400

  def test_get_library_findings_returns_list(self):
    h = _make_handler()
    h.server.job_db.get_findings.return_value = [
      {"id": 1, "kind": "ffprobe_failed", "path": "/x.mp4"},
    ]
    h._get_library_findings({"status": ["open"], "kind": ["ffprobe_failed"]})
    assert h._response_code == 200
    body = _get_response_body(h)
    assert body["count"] == 1

  def test_get_library_finding_unknown_returns_404(self):
    h = _make_handler()
    h.server.job_db.get_finding.return_value = None
    h._get_library_finding("/library/findings/9999")
    assert h._response_code == 404

  def test_get_library_finding_returns_finding(self):
    h = _make_handler()
    h.server.job_db.get_finding.return_value = {"id": 1, "kind": "ffprobe_failed"}
    h._get_library_finding("/library/findings/1")
    assert h._response_code == 200

  def test_get_library_finding_invalid_id_returns_400(self):
    h = _make_handler()
    h._get_library_finding("/library/findings/abc")
    assert h._response_code == 400

  def test_post_library_audit_no_paths_returns_400(self):
    h = _make_handler(method="POST")
    h.server.path_config_manager.audit_paths = []
    h._post_library_audit()
    assert h._response_code == 400

  def test_post_library_audit_invalid_json_returns_400(self):
    h = _make_handler(
      method="POST",
      body=b"junk",
      headers={"Content-Length": "4"},
    )
    h._post_library_audit()
    assert h._response_code == 400

  def test_post_library_audit_with_explicit_paths(self):
    body = json.dumps({"paths": ["/a", "/b"]}).encode()
    h = _make_handler(
      method="POST",
      body=body,
      headers={"Content-Length": str(len(body))},
    )
    h.server.job_db.create_audit_run.return_value = 42
    # The route fires off a background thread for enumerate; stub the
    # method on the handler so the thread is a no-op.
    with patch.object(WebhookHandler, "_run_audit_enumerate", lambda *_a, **_k: None):
      h._post_library_audit()
    assert h._response_code == 202
    body_out = _get_response_body(h)
    assert body_out["audit_id"] == 42

  def test_post_library_audit_uses_configured_paths(self):
    h = _make_handler(method="POST")
    h.server.path_config_manager.audit_paths = [
      {"path": "/configured/path", "enabled": True},
      {"path": "/disabled", "enabled": False},
    ]
    h.server.job_db.create_audit_run.return_value = 7
    with patch.object(WebhookHandler, "_run_audit_enumerate", lambda *_a, **_k: None):
      h._post_library_audit()
    assert h._response_code == 202
    body = _get_response_body(h)
    assert body["paths"] == ["/configured/path"]

  def test_post_library_finding_action_unknown_returns_404(self):
    h = _make_handler(method="POST")
    h.server.job_db.set_finding_status.return_value = 0
    h._post_library_finding_action("/library/findings/9999/dismiss", "dismissed")
    assert h._response_code == 404

  def test_post_library_finding_action_invalid_id_returns_400(self):
    h = _make_handler(method="POST")
    h._post_library_finding_action("/library/findings/abc/dismiss", "dismissed")
    assert h._response_code == 400

  def test_post_library_finding_action_happy_path(self):
    h = _make_handler(method="POST")
    h.server.job_db.set_finding_status.return_value = 1
    h._post_library_finding_action("/library/findings/5/dismiss", "dismissed")
    assert h._response_code == 200


class TestParseHelpers:
  def test_parse_audit_id_invalid(self):
    h = _make_handler()
    assert h._parse_audit_id("/library/audit/abc") is None
    assert h._response_code == 400

  def test_parse_finding_id_invalid(self):
    h = _make_handler()
    assert h._parse_finding_id("/library/findings/abc") is None
    assert h._response_code == 400


class TestPostReload:
  def test_post_reload_returns_202(self):
    h = _make_handler(method="POST")
    h._post_reload("/reload", {})
    assert h._response_code == 200
    assert _get_response_body(h)["status"] == "reloading"


class TestFavicon:
  def test_favicon_404_when_missing(self):
    h = _make_handler()
    with patch("builtins.open", side_effect=FileNotFoundError):
      h._get_favicon("/favicon.ico", {})
    assert h._response_code == 404

  def test_favicon_200_when_present(self):
    h = _make_handler()
    fake = io.BytesIO(b"\x89PNG fake")
    with patch("builtins.open", lambda *a, **k: fake):
      fake.read = lambda: b"\x89PNG fake"
      h._get_favicon("/favicon.ico", {})
    assert h._response_code == 200


class TestHTMLRoutes:
  def test_get_dashboard_injects_api_key(self):
    h = _make_handler(api_key="topsecret")
    with patch("resources.daemon.handler._load_dashboard_html", return_value="<html><head></head></html>"):
      h._get_dashboard("/dashboard", {})
    assert h._response_code == 200
    body = _get_response_bytes(h).decode()
    assert "topsecret" in body

  def test_get_admin_injects_api_key(self):
    h = _make_handler(api_key="topsecret")
    with patch("resources.daemon.handler._load_admin_html", return_value="<html><head></head></html>"):
      h._get_admin("/admin", {})
    assert h._response_code == 200

  def test_get_root_redirects_to_dashboard(self):
    h = _make_handler()
    h._get_root("/", {})
    assert h._response_code == 301
    assert h._response_headers.get("Location") == "/dashboard"
