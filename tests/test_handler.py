"""Unit tests for resources/daemon/handler.py - WebhookHandler."""

import io
import json
import os
import threading
from datetime import datetime, timezone
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
    server.started_at = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
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
    server.job_db.record_scanned.return_value = None
    server.job_db.is_distributed = is_distributed

    # config_lock_manager
    server.config_lock_manager.get_status.return_value = {"active": {}, "waiting": {}}
    server.config_lock_manager.get_active_jobs.return_value = []
    server.config_lock_manager.is_locked.return_value = False

    # config_log_manager
    server.config_log_manager.get_log_file.return_value = "/logs/autoProcess.log"
    server.config_log_manager.get_all_log_files.return_value = []
    server.config_log_manager.logs_dir = "/logs"

    # path_config_manager
    server.path_config_manager.path_configs = []
    server.path_config_manager.default_config = "/config/autoProcess.ini"
    server.path_config_manager.default_args = []
    server.path_config_manager.media_extensions = {".mkv", ".mp4", ".avi"}
    server.path_config_manager.get_config_for_path.return_value = "/config/autoProcess.ini"
    server.path_config_manager.rewrite_path.side_effect = lambda p: p
    server.path_config_manager.is_recycle_bin_path.return_value = False
    server.path_config_manager.get_args_for_path.return_value = []

    # server methods
    server.notify_workers.return_value = None
    server.cancel_job.return_value = False
    server.shutdown.return_value = None
    server.graceful_restart.return_value = None
    server.reload_config.return_value = None

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
        h.server.job_db.get_jobs.return_value = [{"id": 1, "path": "/foo.mkv", "config": "/config/autoProcess.ini"}]
        h._get_jobs({})
        body = _get_response_body(h)
        assert body["jobs"][0]["log_name"] == "autoProcess"

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


# ---------------------------------------------------------------------------
# TestGetConfigs
# ---------------------------------------------------------------------------


class TestGetConfigs:
    def test_returns_default_config(self):
        h = _make_handler()
        h._get_configs()
        body = _get_response_body(h)
        assert body["default_config"] == "/config/autoProcess.ini"

    def test_returns_path_configs(self):
        h = _make_handler()
        h._get_configs()
        body = _get_response_body(h)
        assert "path_configs" in body
        assert isinstance(body["path_configs"], list)

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
        log_file = tmp_path / "autoProcess.log"
        log_file.write_text('{"level": "INFO", "message": "test"}\n')
        h = _make_handler()
        h.server.config_log_manager.get_all_log_files.return_value = [{"name": "autoProcess", "path": str(log_file)}]
        h._get_logs()
        body = _get_response_body(h)
        assert len(body) == 1
        assert body[0]["name"] == "autoProcess"
        assert body[0]["size"] > 0

    def test_handles_missing_log_file(self):
        h = _make_handler()
        h.server.config_log_manager.get_all_log_files.return_value = [{"name": "missing", "path": "/nonexistent/log.log"}]
        h._get_logs()
        body = _get_response_body(h)
        assert body[0]["size"] == 0
        assert body[0]["mtime"] is None


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
        log_file = tmp_path / "autoProcess.log"
        entry = {"level": "INFO", "message": "test", "job_id": 1}
        log_file.write_text(json.dumps(entry) + "\n")
        h = _make_handler()
        h.server.config_log_manager.get_all_log_files.return_value = [{"name": "autoProcess", "path": str(log_file)}]
        h._get_log_content("/logs/autoProcess", {})
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
        h.server.job_db.send_node_command.assert_called_once_with("node-2", "shutdown")
        assert h._response_code == 202

    def test_distributed_broadcast_shutdown(self):
        h = _make_handler(method="POST", is_distributed=True)
        h.server.job_db.send_node_command.return_value = 3
        h._post_shutdown("/shutdown", {})
        h.server.job_db.send_node_command.assert_called_once_with(None, "shutdown")
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
        h.server.job_db.send_node_command.assert_called_once_with("node-2", "restart")

    def test_distributed_broadcast_restart(self):
        h = _make_handler(method="POST", is_distributed=True)
        h.server.job_db.send_node_command.return_value = 2
        h._post_restart("/restart", {})
        h.server.job_db.send_node_command.assert_called_once_with(None, "restart")


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
        path, args = h._parse_sonarr_body()
        assert path is None
        assert h._response_code == 400

    def test_test_event_returns_200(self):
        h = self._sonarr_body({"eventType": "Test"})
        path, args = h._parse_sonarr_body()
        assert path is None
        assert h._response_code == 200

    def test_unsupported_event_returns_400(self):
        h = self._sonarr_body({"eventType": "Grab"})
        path, args = h._parse_sonarr_body()
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
        path, args = h._parse_sonarr_body()
        assert path == "/tv/Show/S01E01.mkv"
        assert "--tv" in args
        assert "-tvdb" in args
        assert "12345" in args

    def test_missing_episode_file_path_returns_400(self):
        payload = {
            "eventType": "Download",
            "episodeFile": {},
            "series": {},
            "episodes": [],
        }
        h = self._sonarr_body(payload)
        path, args = h._parse_sonarr_body()
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
        path, args = h._parse_sonarr_body()
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
        path, args = h._parse_sonarr_body()
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
        path, args = h._parse_sonarr_body()
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
        path, args = h._parse_sonarr_body()
        assert "-s" in args
        assert "3" in args
        assert "-e" in args
        assert "10" in args

    def test_invalid_json_returns_400(self):
        body = b"not json"
        h = _make_handler(body=body, headers={"Content-Length": str(len(body)), "Content-Type": "application/json"})
        path, args = h._parse_sonarr_body()
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
        path, args = h._parse_radarr_body()
        assert path is None
        assert h._response_code == 400

    def test_test_event_returns_200(self):
        h = self._radarr_body({"eventType": "Test"})
        path, args = h._parse_radarr_body()
        assert path is None
        assert h._response_code == 200

    def test_unsupported_event_returns_400(self):
        h = self._radarr_body({"eventType": "Grab"})
        path, args = h._parse_radarr_body()
        assert path is None
        assert h._response_code == 400

    def test_download_event_extracts_path(self):
        payload = {
            "eventType": "Download",
            "movieFile": {"path": "/movies/Matrix.mkv"},
            "movie": {"tmdbId": 603},
        }
        h = self._radarr_body(payload)
        path, args = h._parse_radarr_body()
        assert path == "/movies/Matrix.mkv"
        assert "--movie" in args
        assert "-tmdb" in args
        assert "603" in args

    def test_missing_movie_file_path_returns_400(self):
        payload = {
            "eventType": "Download",
            "movieFile": {},
            "movie": {},
        }
        h = self._radarr_body(payload)
        path, args = h._parse_radarr_body()
        assert path is None
        assert h._response_code == 400

    def test_uses_imdb_when_no_tmdb(self):
        payload = {
            "eventType": "Download",
            "movieFile": {"path": "/movies/film.mkv"},
            "movie": {"imdbId": "tt0133093"},
        }
        h = self._radarr_body(payload)
        path, args = h._parse_radarr_body()
        assert "-imdb" in args
        assert "tt0133093" in args

    def test_extracts_tmdb_id_from_path_when_not_in_payload(self):
        payload = {
            "eventType": "Download",
            "movieFile": {"path": "/movies/The Matrix (1999) {tmdb-603}/Matrix.mkv"},
            "movie": {},
        }
        h = self._radarr_body(payload)
        path, args = h._parse_radarr_body()
        assert "-tmdb" in args
        assert "603" in args

    def test_path_tmdb_not_used_when_payload_has_tmdb(self):
        payload = {
            "eventType": "Download",
            "movieFile": {"path": "/movies/Film {tmdb-999}/film.mkv"},
            "movie": {"tmdbId": 603},
        }
        h = self._radarr_body(payload)
        path, args = h._parse_radarr_body()
        assert "603" in args
        assert "999" not in args

    def test_invalid_json_returns_400(self):
        body = b"not json"
        h = _make_handler(body=body, headers={"Content-Length": str(len(body)), "Content-Type": "application/json"})
        path, args = h._parse_radarr_body()
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
        h._post_jobs_requeue_bulk({"config": ["/config/autoProcess.ini"]})
        h.server.job_db.requeue_failed_jobs.assert_called_once_with(config="/config/autoProcess.ini")

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
    def test_no_path_returns_configured_roots(self, tmp_path):
        h = _make_handler()
        h.server.path_config_manager.path_configs = [{"path": str(tmp_path)}]
        h._get_browse({})
        body = _get_response_body(h)
        assert "dirs" in body
        assert "files" in body

    def test_path_outside_allowed_roots_returns_403(self, tmp_path):
        h = _make_handler()
        h.server.path_config_manager.path_configs = [{"path": str(tmp_path / "media")}]
        h._get_browse({"path": ["/completely/other/path"]})
        assert h._response_code == 403

    def test_nonexistent_directory_returns_404(self, tmp_path):
        allowed = tmp_path / "media"
        allowed.mkdir()
        h = _make_handler()
        h.server.path_config_manager.path_configs = [{"path": str(allowed)}]
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
        h.server.path_config_manager.path_configs = [{"path": str(allowed)}]
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
        h._handle_sonarr_webhook()
        assert h._response_code == 500


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
        h._handle_radarr_webhook()
        assert h._response_code == 500
