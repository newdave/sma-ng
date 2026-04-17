"""Tests for daemon.py - job database, path config, and markdown rendering."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from daemon import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    ConfigLockManager,
    ConfigLogManager,
    DaemonServer,
    PathConfigManager,
    PostgreSQLJobDatabase,
    WebhookHandler,
    _inline,
    _load_dashboard_html,
    _render_markdown_to_html,
)

DASHBOARD_HTML = _load_dashboard_html()


class TestPathConfigManager:
    """Test path-to-config matching."""

    def test_exact_match(self, tmp_path):
        config_file = str(tmp_path / "daemon.json")
        ini_file = str(tmp_path / "autoProcess.ini")
        tv_ini = str(tmp_path / "tv.ini")
        for f in [ini_file, tv_ini]:
            open(f, "w").close()
        with open(config_file, "w") as f:
            json.dump({"default_config": ini_file, "path_configs": [{"path": "/mnt/media/TV", "config": tv_ini}]}, f)
        pcm = PathConfigManager(config_file)
        assert pcm.get_config_for_path("/mnt/media/TV/show/ep.mkv") == tv_ini

    def test_longest_prefix_wins(self, tmp_path):
        config_file = str(tmp_path / "daemon.json")
        ini_file = str(tmp_path / "default.ini")
        movies_ini = str(tmp_path / "movies.ini")
        movies4k_ini = str(tmp_path / "movies4k.ini")
        for f in [ini_file, movies_ini, movies4k_ini]:
            open(f, "w").close()
        with open(config_file, "w") as f:
            json.dump(
                {
                    "default_config": ini_file,
                    "path_configs": [
                        {"path": "/mnt/media/Movies", "config": movies_ini},
                        {"path": "/mnt/media/Movies/4K", "config": movies4k_ini},
                    ],
                },
                f,
            )
        pcm = PathConfigManager(config_file)
        assert pcm.get_config_for_path("/mnt/media/Movies/4K/film.mkv") == movies4k_ini
        assert pcm.get_config_for_path("/mnt/media/Movies/regular.mkv") == movies_ini

    def test_no_match_uses_default(self, tmp_path):
        config_file = str(tmp_path / "daemon.json")
        ini_file = str(tmp_path / "default.ini")
        open(ini_file, "w").close()
        with open(config_file, "w") as f:
            json.dump({"default_config": ini_file, "path_configs": [{"path": "/mnt/media/TV", "config": str(tmp_path / "tv.ini")}]}, f)
        open(str(tmp_path / "tv.ini"), "w").close()
        pcm = PathConfigManager(config_file)
        assert pcm.get_config_for_path("/completely/different/path.mkv") == ini_file

    def test_get_all_configs(self, tmp_path):
        config_file = str(tmp_path / "daemon.json")
        ini_file = str(tmp_path / "default.ini")
        tv_ini = str(tmp_path / "tv.ini")
        for f in [ini_file, tv_ini]:
            open(f, "w").close()
        with open(config_file, "w") as f:
            json.dump({"default_config": ini_file, "path_configs": [{"path": "/tv", "config": tv_ini}]}, f)
        pcm = PathConfigManager(config_file)
        all_configs = pcm.get_all_configs()
        assert ini_file in all_configs
        assert tv_ini in all_configs


class TestConfigLockManager:
    """Test per-config locking."""

    def test_acquire_and_release(self):
        clm = ConfigLockManager()
        clm.acquire("/config.ini", 1, "/path.mkv")
        status = clm.get_status()
        assert "/config.ini" in status["active"]
        clm.release("/config.ini", 1)
        status = clm.get_status()
        assert "/config.ini" not in status["active"]

    def test_is_locked(self):
        clm = ConfigLockManager()
        assert clm.is_locked("/config.ini") is False
        clm.acquire("/config.ini", 1, "/path.mkv")
        assert clm.is_locked("/config.ini") is True
        clm.release("/config.ini", 1)
        assert clm.is_locked("/config.ini") is False

    def test_get_active_job(self):
        clm = ConfigLockManager()
        clm.acquire("/config.ini", 42, "/movie.mkv")
        active = clm.get_active_jobs("/config.ini")
        assert len(active) == 1
        assert active[0]["job_id"] == 42
        assert active[0]["path"] == "/movie.mkv"
        clm.release("/config.ini", 42)


class TestMarkdownRendering:
    """Test the minimal Markdown to HTML renderer."""

    def test_heading_h1(self):
        html = _render_markdown_to_html("# Hello")
        assert "<h1" in html
        assert "Hello" in html

    def test_heading_h3(self):
        html = _render_markdown_to_html("### Section")
        assert "<h3" in html

    def test_code_block(self):
        md = '```python\nprint("hello")\n```'
        html = _render_markdown_to_html(md)
        assert "<pre" in html
        assert "<code" in html
        assert "print" in html

    def test_code_block_escapes_html(self):
        md = '```\n<script>alert("xss")</script>\n```'
        html = _render_markdown_to_html(md)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_table(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        html = _render_markdown_to_html(md)
        assert "<table" in html
        assert "<th" in html
        assert "<td" in html

    def test_unordered_list(self):
        md = "- item 1\n- item 2"
        html = _render_markdown_to_html(md)
        assert "<ul" in html
        assert "<li>" in html

    def test_ordered_list(self):
        md = "1. first\n2. second"
        html = _render_markdown_to_html(md)
        assert "<ol" in html
        assert "<li>" in html

    def test_paragraph(self):
        html = _render_markdown_to_html("Just a paragraph.")
        assert "<p" in html

    def test_horizontal_rule(self):
        html = _render_markdown_to_html("---")
        assert "<hr" in html


class TestInlineFormatting:
    """Test inline Markdown formatting."""

    def test_bold(self):
        html = _inline("**bold text**")
        assert "<strong" in html
        assert "bold text" in html

    def test_italic(self):
        html = _inline("*italic text*")
        assert "<em>" in html

    def test_inline_code(self):
        html = _inline("use `pip install`")
        assert "<code" in html
        assert "pip install" in html

    def test_link(self):
        html = _inline("[text](http://example.com)")
        assert 'href="http://example.com"' in html
        assert ">text<" in html

    def test_html_escaping(self):
        html = _inline('<script>alert("xss")</script>')
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_mixed_formatting(self):
        html = _inline("**bold** and `code` and *italic*")
        assert "<strong" in html
        assert "<code" in html
        assert "<em>" in html


class TestDashboardHTML:
    """Test DASHBOARD_HTML template structure and content."""

    def test_valid_html_structure(self):
        assert "<!DOCTYPE html>" in DASHBOARD_HTML
        assert "<html" in DASHBOARD_HTML
        assert "<head>" in DASHBOARD_HTML
        assert "<body" in DASHBOARD_HTML
        assert "</html>" in DASHBOARD_HTML

    def test_title(self):
        assert "<title>SMA-NG Daemon</title>" in DASHBOARD_HTML

    def test_alpine_js_loaded(self):
        assert "alpinejs" in DASHBOARD_HTML

    def test_alpine_component_attributes(self):
        assert 'x-data="dashboard()"' in DASHBOARD_HTML
        assert 'x-init="init()"' in DASHBOARD_HTML
        assert "x-cloak" in DASHBOARD_HTML

    def test_dashboard_function_defined(self):
        assert "function dashboard()" in DASHBOARD_HTML

    def test_api_endpoints_referenced(self):
        assert "fetch('/health'" in DASHBOARD_HTML
        assert "fetch('/stats'" in DASHBOARD_HTML
        assert "fetch('/configs'" in DASHBOARD_HTML
        assert "fetch('/jobs" in DASHBOARD_HTML
        assert "fetch('/webhook'" in DASHBOARD_HTML

    def test_stat_keys_present(self):
        for key in ("total", "pending", "running", "completed", "failed"):
            assert key in DASHBOARD_HTML

    def test_uptime_display(self):
        assert "fmtUptime" in DASHBOARD_HTML
        assert "uptime_seconds" in DASHBOARD_HTML

    def test_node_display(self):
        assert "health.node" in DASHBOARD_HTML

    def test_fmt_uptime_handles_ranges(self):
        """fmtUptime JS logic is present for all time ranges."""
        assert "86400" in DASHBOARD_HTML  # days threshold
        assert "3600" in DASHBOARD_HTML  # hours threshold


class TestWantsHtml:
    """Test wants_html() Accept header detection."""

    def _make_handler(self, accept):
        handler = WebhookHandler.__new__(WebhookHandler)
        handler.headers = {"Accept": accept}
        return handler

    def test_browser_request(self):
        h = self._make_handler("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        assert h.wants_html() is True

    def test_json_api_request(self):
        h = self._make_handler("application/json")
        assert h.wants_html() is False

    def test_no_accept_header(self):
        h = self._make_handler("")
        assert h.wants_html() is False

    def test_html_plus_json_prefers_json(self):
        # JSON in Accept means API client, not browser
        h = self._make_handler("text/html, application/json")
        assert h.wants_html() is False


@pytest.fixture
def live_server():
    """Spin up a DaemonServer on a random port, yield it, then shut it down."""
    import os

    db_url = os.environ.get("TEST_DB_URL")
    if not db_url:
        pytest.skip("TEST_DB_URL not set")
    from resources.log import getLogger

    job_db = PostgreSQLJobDatabase(db_url)
    server = DaemonServer(
        ("127.0.0.1", 0),
        WebhookHandler,
        job_db,
        PathConfigManager(),
        ConfigLogManager("/tmp"),
        ConfigLockManager(),
        getLogger("TEST"),
        worker_count=1,
    )
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server
    server.shutdown()
    server.server_close()
    job_db.close()


class TestHealthEndpoint:
    """Test /health endpoint response fields."""

    def _get(self, server, path):
        host, port = server.server_address
        url = "http://%s:%d%s" % (host, port, path)
        with urllib.request.urlopen(url) as resp:
            return json.loads(resp.read())

    def test_health_ok(self, live_server):
        data = self._get(live_server, "/health")
        assert data["status"] == "ok"

    def test_health_includes_started_at(self, live_server):
        data = self._get(live_server, "/health")
        assert "started_at" in data
        # Should be an ISO 8601 string
        assert "T" in data["started_at"]

    def test_health_includes_uptime_seconds(self, live_server):
        data = self._get(live_server, "/health")
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], int)
        assert data["uptime_seconds"] >= 0

    def test_health_includes_node(self, live_server):
        data = self._get(live_server, "/health")
        assert "node" in data
        assert data["node"]  # non-empty

    def test_health_includes_workers(self, live_server):
        data = self._get(live_server, "/health")
        assert data["workers"] == 1

    def test_dashboard_served_to_browser(self, live_server):
        host, port = live_server.server_address
        url = "http://%s:%d/dashboard" % (host, port)
        req = urllib.request.Request(url, headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            assert resp.headers.get("Content-Type", "").startswith("text/html")
            assert "<!DOCTYPE html>" in body
            assert "SMA-NG" in body

    def test_root_redirects_to_dashboard(self, live_server):
        host, port = live_server.server_address
        url = "http://%s:%d/" % (host, port)
        req = urllib.request.Request(url)
        # Do not follow redirects so we can assert the 301
        opener = urllib.request.OpenerDirector()
        opener.add_handler(urllib.request.UnknownHandler())
        opener.add_handler(urllib.request.HTTPHandler())
        with urllib.request.urlopen(req) as resp:
            assert resp.url.endswith("/dashboard")

    def test_json_served_to_api_client(self, live_server):
        host, port = live_server.server_address
        url = "http://%s:%d/health" % (host, port)
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req) as resp:
            assert resp.headers.get("Content-Type", "").startswith("application/json")


# ---------------------------------------------------------------------------
# ConfigLockManager extended tests
# ---------------------------------------------------------------------------


class TestConfigLockManagerExtended:
    """Additional tests for ConfigLockManager per-config serialization."""

    def test_is_locked_initially_false(self):
        mgr = ConfigLockManager()
        assert mgr.is_locked("/cfg/autoProcess.ini") is False

    def test_is_locked_after_acquire(self):
        mgr = ConfigLockManager()
        mgr.acquire("/cfg/autoProcess.ini", job_id=1, job_path="/a.mkv")
        assert mgr.is_locked("/cfg/autoProcess.ini") is True

    def test_is_locked_false_after_release(self):
        mgr = ConfigLockManager()
        mgr.acquire("/cfg/autoProcess.ini", job_id=1, job_path="/a.mkv")
        mgr.release("/cfg/autoProcess.ini", job_id=1)
        assert mgr.is_locked("/cfg/autoProcess.ini") is False

    def test_release_unheld_lock_does_not_raise(self):
        mgr = ConfigLockManager()
        mgr.release("/cfg/autoProcess.ini", job_id=99)  # Should not raise

    def test_get_status_empty(self):
        mgr = ConfigLockManager()
        status = mgr.get_status()
        assert status["active"] == {}
        assert status["waiting"] == {}

    def test_get_status_shows_active(self):
        mgr = ConfigLockManager()
        mgr.acquire("/cfg/autoProcess.ini", job_id=42, job_path="/movie.mkv")
        status = mgr.get_status()
        assert "/cfg/autoProcess.ini" in status["active"]
        # get_status returns a list of active job dicts
        active_jobs = status["active"]["/cfg/autoProcess.ini"]
        assert any(j["job_id"] == 42 and j["path"] == "/movie.mkv" for j in active_jobs)

    def test_get_active_job_returns_empty_when_idle(self):
        mgr = ConfigLockManager()
        assert mgr.get_active_jobs("/cfg/autoProcess.ini") == []

    def test_get_active_job_returns_info_when_locked(self):
        mgr = ConfigLockManager()
        mgr.acquire("/cfg/autoProcess.ini", job_id=7, job_path="/show.mkv")
        jobs = mgr.get_active_jobs("/cfg/autoProcess.ini")
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == 7
        assert jobs[0]["path"] == "/show.mkv"

    def test_separate_configs_independent(self):
        mgr = ConfigLockManager()
        mgr.acquire("/cfg/tv.ini", job_id=1, job_path="/tv.mkv")
        assert mgr.is_locked("/cfg/tv.ini") is True
        assert mgr.is_locked("/cfg/movies.ini") is False


# ---------------------------------------------------------------------------
# ConfigLogManager tests
# ---------------------------------------------------------------------------


class TestConfigLogManager:
    """Test ConfigLogManager log file path derivation."""

    def test_get_log_file_basename(self, tmp_path):
        mgr = ConfigLogManager(str(tmp_path))
        log_file = mgr.get_log_file("/config/autoProcess.ini")
        assert log_file == str(tmp_path / "autoProcess.log")

    def test_get_log_file_different_configs(self, tmp_path):
        mgr = ConfigLogManager(str(tmp_path))
        assert mgr.get_log_file("/cfg/autoProcess.tv.ini").endswith("autoProcess.tv.log")
        assert mgr.get_log_file("/cfg/autoProcess.movies.ini").endswith("autoProcess.movies.log")

    def test_get_logger_creates_logger(self, tmp_path):
        mgr = ConfigLogManager(str(tmp_path))
        logger = mgr.get_logger("/cfg/autoProcess.ini")
        assert logger is not None

    def test_get_logger_returns_same_instance(self, tmp_path):
        mgr = ConfigLogManager(str(tmp_path))
        l1 = mgr.get_logger("/cfg/autoProcess.ini")
        l2 = mgr.get_logger("/cfg/autoProcess.ini")
        assert l1 is l2


# ---------------------------------------------------------------------------
# PathConfigManager extended tests (db_url, ffmpeg_dir from JSON)
# ---------------------------------------------------------------------------


class TestPathConfigManagerExtended:
    """Test PathConfigManager reads db_url and ffmpeg_dir from daemon.json."""

    def test_db_url_from_json(self, tmp_path):
        cfg = tmp_path / "daemon.json"
        cfg.write_text(
            json.dumps(
                {
                    "default_config": "config/autoProcess.ini",
                    "db_url": "postgresql://sma:pw@db/sma",
                }
            )
        )
        mgr = PathConfigManager(str(cfg))
        assert mgr.db_url == "postgresql://sma:pw@db/sma"

    def test_ffmpeg_dir_from_json(self, tmp_path):
        cfg = tmp_path / "daemon.json"
        cfg.write_text(
            json.dumps(
                {
                    "default_config": "config/autoProcess.ini",
                    "ffmpeg_dir": "/usr/local/bin",
                }
            )
        )
        mgr = PathConfigManager(str(cfg))
        assert mgr.ffmpeg_dir == "/usr/local/bin"

    def test_api_key_from_json(self, tmp_path):
        cfg = tmp_path / "daemon.json"
        cfg.write_text(
            json.dumps(
                {
                    "default_config": "config/autoProcess.ini",
                    "api_key": "supersecret",
                }
            )
        )
        mgr = PathConfigManager(str(cfg))
        assert mgr.api_key == "supersecret"

    def test_null_values_remain_none(self, tmp_path):
        cfg = tmp_path / "daemon.json"
        cfg.write_text(
            json.dumps(
                {
                    "default_config": "config/autoProcess.ini",
                    "db_url": None,
                    "ffmpeg_dir": None,
                    "api_key": None,
                }
            )
        )
        mgr = PathConfigManager(str(cfg))
        assert mgr.db_url is None
        assert mgr.ffmpeg_dir is None
        assert mgr.api_key is None

    def test_no_config_file_defaults(self):
        from unittest.mock import patch

        with patch("daemon.DEFAULT_DAEMON_CONFIG", "/nonexistent/default.json"):
            mgr = PathConfigManager("/nonexistent/daemon.json")
        assert mgr.db_url is None
        assert mgr.ffmpeg_dir is None
        assert mgr.api_key is None


# ---------------------------------------------------------------------------
# PostgreSQL import guard test
# ---------------------------------------------------------------------------


class TestPostgresImportGuard:
    """Test PostgreSQLJobDatabase raises ImportError when psycopg2 unavailable."""

    def test_import_error_without_psycopg2(self):
        import sys

        from daemon import PostgreSQLJobDatabase

        blocked = {"psycopg2": None, "psycopg2.pool": None, "psycopg2.extras": None}
        with pytest.raises(ImportError, match="psycopg2 is required"):
            with pytest.MonkeyPatch().context() as mp:
                for mod, val in blocked.items():
                    mp.setitem(sys.modules, mod, val)
                PostgreSQLJobDatabase("postgresql://localhost/test")


# ---------------------------------------------------------------------------
# HTTP endpoint tests via live server
# ---------------------------------------------------------------------------


class TestHTTPEndpoints:
    """Test daemon HTTP endpoints using a live in-process server."""

    def _get(self, server, path):
        host, port = server.server_address
        url = "http://%s:%d%s" % (host, port, path)
        try:
            with urllib.request.urlopen(url) as resp:
                return json.loads(resp.read()), resp.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read()), e.code

    def _post(self, server, path, data=None, content_type="application/json"):
        host, port = server.server_address
        url = "http://%s:%d%s" % (host, port, path)
        body = json.dumps(data).encode() if data is not None else b""
        req = urllib.request.Request(url, data=body, method="POST")
        if body:
            req.add_header("Content-Type", content_type)
            req.add_header("Content-Length", str(len(body)))
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read()), resp.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read()), e.code

    def test_status_endpoint(self, live_server):
        data, status = self._get(live_server, "/status")
        assert status == 200
        assert "cluster" in data
        assert "jobs" in data

    def test_jobs_endpoint_empty(self, live_server):
        data, status = self._get(live_server, "/jobs")
        assert status == 200
        assert data["jobs"] == []
        assert data["count"] == 0

    def test_jobs_endpoint_with_jobs(self, live_server):
        live_server.job_db.add_job("/a.mkv", "/cfg.ini")
        live_server.job_db.add_job("/b.mkv", "/cfg.ini")
        data, status = self._get(live_server, "/jobs")
        assert status == 200
        assert data["count"] == 2

    def test_jobs_filter_by_status(self, live_server):
        jid = live_server.job_db.add_job("/a.mkv", "/cfg.ini")
        live_server.job_db.start_job(jid, worker_id=1)
        live_server.job_db.complete_job(jid)
        live_server.job_db.add_job("/b.mkv", "/cfg.ini")

        data, _ = self._get(live_server, "/jobs?status=pending")
        assert data["count"] == 1
        assert data["jobs"][0]["path"] == "/b.mkv"

    def test_jobs_pagination(self, live_server):
        for i in range(5):
            live_server.job_db.add_job("/file%d.mkv" % i, "/cfg.ini")
        data, _ = self._get(live_server, "/jobs?limit=3&offset=0")
        assert len(data["jobs"]) == 3
        assert data["limit"] == 3
        assert data["offset"] == 0

    def test_job_by_id_found(self, live_server):
        jid = live_server.job_db.add_job("/movie.mkv", "/cfg.ini")
        data, status = self._get(live_server, "/jobs/%d" % jid)
        assert status == 200
        assert data["id"] == jid
        assert data["path"] == "/movie.mkv"

    def test_job_by_id_not_found(self, live_server):
        data, status = self._get(live_server, "/jobs/9999")
        assert status == 404

    def test_job_by_id_invalid(self, live_server):
        data, status = self._get(live_server, "/jobs/notanumber")
        assert status == 400

    def test_stats_endpoint(self, live_server):
        live_server.job_db.add_job("/a.mkv", "/cfg.ini")
        data, status = self._get(live_server, "/stats")
        assert status == 200
        assert data["total"] == 1

    def test_configs_endpoint(self, live_server):
        data, status = self._get(live_server, "/configs")
        assert status == 200
        assert "default_config" in data
        assert "path_configs" in data

    def test_not_found_returns_404(self, live_server):
        data, status = self._get(live_server, "/nonexistent")
        assert status == 404

    def test_webhook_empty_body(self, live_server):
        data, status = self._post(live_server, "/webhook", data=None)
        assert status == 400

    def test_webhook_nonexistent_path(self, live_server):
        data, status = self._post(live_server, "/webhook", data={"path": "/nonexistent/file.mkv"})
        assert status == 400
        assert "does not exist" in data["error"]

    def test_webhook_valid_path_json(self, live_server, tmp_path):
        f = tmp_path / "movie.mkv"
        f.touch()
        data, status = self._post(live_server, "/webhook", data={"path": str(f)})
        assert status == 202
        assert "job_id" in data
        assert data["job_id"] is not None

    def test_webhook_plain_text_body(self, live_server, tmp_path):
        f = tmp_path / "movie.mkv"
        f.touch()
        host, port = live_server.server_address
        url = "http://%s:%d/webhook" % (host, port)
        body = str(f).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Length", str(len(body)))
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        assert "job_id" in data

    def test_webhook_duplicate_submission(self, live_server, tmp_path):
        f = tmp_path / "movie.mkv"
        f.touch()
        data1, _ = self._post(live_server, "/webhook", data={"path": str(f)})
        data2, status2 = self._post(live_server, "/webhook", data={"path": str(f)})
        assert status2 == 200
        assert data2["status"] == "duplicate"

    def test_webhook_json_string_arg(self, live_server, tmp_path):
        """JSON body where args is a string gets split into a list."""
        f = tmp_path / "movie.mkv"
        f.touch()
        data, status = self._post(live_server, "/webhook", data={"path": str(f), "args": "-tmdb 603"})
        assert status == 202

    def test_cleanup_endpoint(self, live_server):
        jid = live_server.job_db.add_job("/old.mkv", "/cfg.ini")
        live_server.job_db.start_job(jid, worker_id=1)
        live_server.job_db.complete_job(jid)
        with live_server.job_db._cursor() as cur:
            cur.execute("UPDATE jobs SET completed_at = datetime('now', '-2 days') WHERE id = ?", (jid,))
        data, status = self._post(live_server, "/cleanup?days=1")
        assert status == 200
        assert data["deleted"] == 1

    def test_requeue_all_failed(self, live_server):
        for path in ("/a.mkv", "/b.mkv"):
            jid = live_server.job_db.add_job(path, "/cfg.ini")
            live_server.job_db.start_job(jid, worker_id=1)
            live_server.job_db.fail_job(jid, "err")
        data, status = self._post(live_server, "/jobs/requeue")
        assert status == 200
        assert data["requeued"] == 2

    def test_requeue_single_failed_job(self, live_server):
        jid = live_server.job_db.add_job("/a.mkv", "/cfg.ini")
        live_server.job_db.start_job(jid, worker_id=1)
        live_server.job_db.fail_job(jid, "err")
        data, status = self._post(live_server, "/jobs/%d/requeue" % jid)
        assert status == 200
        assert data["requeued"] is True

    def test_requeue_single_non_failed_job(self, live_server):
        jid = live_server.job_db.add_job("/a.mkv", "/cfg.ini")
        data, status = self._post(live_server, "/jobs/%d/requeue" % jid)
        assert status == 409

    def test_requeue_nonexistent_job(self, live_server):
        data, status = self._post(live_server, "/jobs/9999/requeue")
        assert status == 404

    def test_post_not_found(self, live_server):
        data, status = self._post(live_server, "/nonexistent")
        assert status == 404

    def test_webhook_rejects_recycle_bin_path(self, live_server, tmp_path):
        recycle = tmp_path / "recycle"
        recycle.mkdir()
        ini = tmp_path / "autoProcess.ini"
        ini.write_text("[Converter]\nrecycle-bin = %s\n" % recycle)
        live_server.path_config_manager.default_config = str(ini)

        f = recycle / "movie.mkv"
        f.touch()
        data, status = self._post(live_server, "/webhook", data={"path": str(f)})
        assert status == 400
        assert "recycle-bin" in data["error"]

    def test_webhook_rejects_recycle_bin_subdirectory(self, live_server, tmp_path):
        recycle = tmp_path / "recycle"
        subdir = recycle / "Movies"
        subdir.mkdir(parents=True)
        ini = tmp_path / "autoProcess.ini"
        ini.write_text("[Converter]\nrecycle-bin = %s\n" % recycle)
        live_server.path_config_manager.default_config = str(ini)

        f = subdir / "movie.mkv"
        f.touch()
        data, status = self._post(live_server, "/webhook", data={"path": str(f)})
        assert status == 400
        assert "recycle-bin" in data["error"]

    def test_webhook_allows_path_outside_recycle_bin(self, live_server, tmp_path):
        recycle = tmp_path / "recycle"
        recycle.mkdir()
        media = tmp_path / "media"
        media.mkdir()
        ini = tmp_path / "autoProcess.ini"
        ini.write_text("[Converter]\nrecycle-bin = %s\n" % recycle)
        live_server.path_config_manager.default_config = str(ini)

        f = media / "movie.mkv"
        f.touch()
        data, status = self._post(live_server, "/webhook", data={"path": str(f)})
        assert status == 202


class TestRecycleBinDetection:
    """Unit tests for PathConfigManager.is_recycle_bin_path."""

    def _make_mgr(self, tmp_path, recycle_bin=None):
        ini = tmp_path / "autoProcess.ini"
        content = "[Converter]\n"
        if recycle_bin:
            content += "recycle-bin = %s\n" % recycle_bin
        ini.write_text(content)
        mgr = PathConfigManager()
        mgr.default_config = str(ini)
        return mgr

    def test_path_inside_recycle_bin(self, tmp_path):
        recycle = tmp_path / "recycle"
        recycle.mkdir()
        mgr = self._make_mgr(tmp_path, recycle_bin=str(recycle))
        assert mgr.is_recycle_bin_path(str(recycle / "movie.mkv")) is True

    def test_recycle_bin_root_itself(self, tmp_path):
        recycle = tmp_path / "recycle"
        recycle.mkdir()
        mgr = self._make_mgr(tmp_path, recycle_bin=str(recycle))
        assert mgr.is_recycle_bin_path(str(recycle)) is True

    def test_path_outside_recycle_bin(self, tmp_path):
        recycle = tmp_path / "recycle"
        recycle.mkdir()
        mgr = self._make_mgr(tmp_path, recycle_bin=str(recycle))
        assert mgr.is_recycle_bin_path(str(tmp_path / "media" / "movie.mkv")) is False

    def test_no_recycle_bin_configured(self, tmp_path):
        mgr = self._make_mgr(tmp_path, recycle_bin=None)
        assert mgr.is_recycle_bin_path(str(tmp_path / "anything.mkv")) is False

    def test_prefix_match_does_not_false_positive(self, tmp_path):
        # /tmp/recycle should not match /tmp/recycle-extra/movie.mkv
        recycle = tmp_path / "recycle"
        recycle.mkdir()
        other = tmp_path / "recycle-extra"
        other.mkdir()
        mgr = self._make_mgr(tmp_path, recycle_bin=str(recycle))
        assert mgr.is_recycle_bin_path(str(other / "movie.mkv")) is False


# ---------------------------------------------------------------------------
# Job cancel and priority (db-level)
# ---------------------------------------------------------------------------


class TestJobCancelAndPriority:
    """Tests for cancel_job and set_job_priority."""

    def test_cancel_pending_job(self, job_db):
        jid = job_db.add_job("/a.mkv", "/cfg.ini")
        result = job_db.cancel_job(jid)
        assert result is True
        job = job_db.get_job(jid)
        assert job["status"] == "cancelled"
        assert job["error"] == "Cancelled by user"

    def test_cancel_running_job(self, job_db):
        jid = job_db.add_job("/a.mkv", "/cfg.ini")
        job_db.start_job(jid, worker_id=1)
        result = job_db.cancel_job(jid)
        assert result is True
        assert job_db.get_job(jid)["status"] == "cancelled"

    def test_cancel_completed_job_returns_false(self, job_db):
        jid = job_db.add_job("/a.mkv", "/cfg.ini")
        job_db.start_job(jid, worker_id=1)
        job_db.complete_job(jid)
        result = job_db.cancel_job(jid)
        assert result is False
        assert job_db.get_job(jid)["status"] == STATUS_COMPLETED

    def test_cancel_nonexistent_job_returns_false(self, job_db):
        assert job_db.cancel_job(9999) is False

    def test_set_priority_pending_job(self, job_db):
        jid = job_db.add_job("/a.mkv", "/cfg.ini")
        result = job_db.set_job_priority(jid, 10)
        assert result is True
        job = job_db.get_job(jid)
        assert job["priority"] == 10

    def test_set_priority_running_job_returns_false(self, job_db):
        jid = job_db.add_job("/a.mkv", "/cfg.ini")
        job_db.start_job(jid, worker_id=1)
        result = job_db.set_job_priority(jid, 5)
        assert result is False

    def test_set_priority_nonexistent_job_returns_false(self, job_db):
        assert job_db.set_job_priority(9999, 1) is False

    def test_priority_affects_claim_order(self, job_db):
        """Higher priority jobs should be claimed before lower priority ones."""
        jid_low = job_db.add_job("/low.mkv", "/cfg.ini")
        jid_high = job_db.add_job("/high.mkv", "/cfg.ini")
        job_db.set_job_priority(jid_high, 10)
        claimed = job_db.claim_next_job(worker_id=1, node_id="node1")
        assert claimed is not None
        assert claimed["id"] == jid_high


# ---------------------------------------------------------------------------
# Scan operations (db-level)
# ---------------------------------------------------------------------------


class TestScanOperations:
    """Tests for filter_unscanned and record_scanned."""

    def test_filter_unscanned_empty_input(self, job_db):
        assert job_db.filter_unscanned([]) == []

    def test_filter_unscanned_all_new(self, job_db):
        paths = ["/a.mkv", "/b.mkv"]
        result = job_db.filter_unscanned(paths)
        assert set(result) == set(paths)

    def test_record_and_filter_scanned(self, job_db):
        paths = ["/a.mkv", "/b.mkv", "/c.mkv"]
        job_db.record_scanned(["/a.mkv", "/b.mkv"])
        unscanned = job_db.filter_unscanned(paths)
        assert unscanned == ["/c.mkv"]

    def test_record_scanned_idempotent(self, job_db):
        job_db.record_scanned(["/a.mkv"])
        job_db.record_scanned(["/a.mkv"])  # Should not raise
        assert job_db.filter_unscanned(["/a.mkv"]) == []

    def test_record_scanned_empty_is_noop(self, job_db):
        job_db.record_scanned([])  # Should not raise


# ---------------------------------------------------------------------------
# PathConfigManager path utilities
# ---------------------------------------------------------------------------


class TestPathConfigManagerUtils:
    """Tests for get_args_for_path, rewrite_path."""

    def _make_mgr(self, tmp_path):
        cfg = tmp_path / "daemon.json"
        import json as _json

        cfg.write_text(
            _json.dumps(
                {
                    "default_config": "config/autoProcess.ini",
                    "default_args": ["-nt"],
                    "path_configs": [
                        {
                            "path": str(tmp_path / "TV"),
                            "config": "config/autoProcess.ini",
                            "default_args": ["-tvdb", "1234"],
                        },
                        {
                            "path": str(tmp_path / "Movies"),
                            "config": "config/autoProcess.ini",
                        },
                    ],
                    "path_rewrites": [
                        {"from": "/mnt/local", "to": "/mnt/union"},
                    ],
                }
            )
        )
        return PathConfigManager(str(cfg))

    def test_get_args_for_matched_path(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        args = mgr.get_args_for_path(str(tmp_path / "TV" / "show.mkv"))
        assert args == ["-tvdb", "1234"]

    def test_get_args_for_unmatched_path_returns_default(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        args = mgr.get_args_for_path("/unrelated/movie.mkv")
        assert args == ["-nt"]

    def test_get_args_for_path_without_default_args(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        args = mgr.get_args_for_path(str(tmp_path / "Movies" / "film.mkv"))
        assert args == []

    def test_rewrite_path_matching_prefix(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        result = mgr.rewrite_path("/mnt/local/Media/show.mkv")
        assert result == "/mnt/union/Media/show.mkv"

    def test_rewrite_path_exact_prefix(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        result = mgr.rewrite_path("/mnt/local")
        assert result == "/mnt/union"

    def test_rewrite_path_no_match(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        result = mgr.rewrite_path("/other/path/file.mkv")
        assert result == "/other/path/file.mkv"

    def test_rewrite_path_no_false_partial_prefix_match(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        # /mnt/local2 should NOT match /mnt/local rewrite
        result = mgr.rewrite_path("/mnt/local2/file.mkv")
        assert result == "/mnt/local2/file.mkv"


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------


class TestAuthentication:
    """Tests for check_auth and API key enforcement."""

    def _get(self, server, path, api_key=None):
        host, port = server.server_address
        url = "http://%s:%d%s" % (host, port, path)
        req = urllib.request.Request(url)
        if api_key:
            req.add_header("X-API-Key", api_key)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read()), resp.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read()), e.code

    def _post(self, server, path, data=None, api_key=None, bearer=None):
        host, port = server.server_address
        url = "http://%s:%d%s" % (host, port, path)
        body = json.dumps(data).encode() if data is not None else b""
        req = urllib.request.Request(url, data=body, method="POST")
        if body:
            req.add_header("Content-Type", "application/json")
            req.add_header("Content-Length", str(len(body)))
        if api_key:
            req.add_header("X-API-Key", api_key)
        if bearer:
            req.add_header("Authorization", "Bearer %s" % bearer)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read()), resp.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read()), e.code

    def _make_server(self, db_url, api_key=None):
        from resources.log import getLogger

        job_db = PostgreSQLJobDatabase(db_url)
        server = DaemonServer(
            ("127.0.0.1", 0),
            WebhookHandler,
            job_db,
            PathConfigManager(),
            ConfigLogManager("/tmp"),
            ConfigLockManager(),
            getLogger("TEST"),
            worker_count=1,
            api_key=api_key,
        )
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server, job_db

    def _get_db_url(self):
        import os

        db_url = os.environ.get("TEST_DB_URL")
        if not db_url:
            pytest.skip("TEST_DB_URL not set")
        return db_url

    def test_no_api_key_allows_all(self):
        server, job_db = self._make_server(self._get_db_url(), api_key=None)
        try:
            data, status = self._get(server, "/health")
            assert status == 200
            data, status = self._get(server, "/jobs")
            assert status == 200
        finally:
            server.shutdown()
            server.server_close()
            job_db.close()

    def test_api_key_required_when_set(self):
        server, job_db = self._make_server(self._get_db_url(), api_key="secret123")
        try:
            data, status = self._get(server, "/jobs")  # no key
            assert status == 401
            assert "Unauthorized" in data["error"]
        finally:
            server.shutdown()
            server.server_close()
            job_db.close()

    def test_x_api_key_header_accepted(self):
        server, job_db = self._make_server(self._get_db_url(), api_key="secret123")
        try:
            data, status = self._get(server, "/jobs", api_key="secret123")
            assert status == 200
        finally:
            server.shutdown()
            server.server_close()
            job_db.close()

    def test_bearer_token_accepted(self):
        server, job_db = self._make_server(self._get_db_url(), api_key="secret123")
        try:
            data, status = self._get(server, "/jobs")  # no key -> 401
            assert status == 401
            # Now with bearer
            host, port = server.server_address
            url = "http://%s:%d/jobs" % (host, port)
            req = urllib.request.Request(url, headers={"Authorization": "Bearer secret123"})
            with urllib.request.urlopen(req) as resp:
                assert resp.status == 200
        finally:
            server.shutdown()
            server.server_close()
            job_db.close()

    def test_wrong_key_returns_401(self):
        server, job_db = self._make_server(self._get_db_url(), api_key="secret123")
        try:
            data, status = self._get(server, "/jobs", api_key="wrongkey")
            assert status == 401
        finally:
            server.shutdown()
            server.server_close()
            job_db.close()

    def test_public_endpoints_accessible_without_key(self):
        server, job_db = self._make_server(self._get_db_url(), api_key="secret123")
        try:
            # /health is public
            data, status = self._get(server, "/health")
            assert status == 200
        finally:
            server.shutdown()
            server.server_close()
            job_db.close()

    def test_post_requires_auth(self):
        server, job_db = self._make_server(self._get_db_url(), api_key="secret123")
        try:
            data, status = self._post(server, "/cleanup")
            assert status == 401
        finally:
            server.shutdown()
            server.server_close()
            job_db.close()


# ---------------------------------------------------------------------------
# Job cancel and priority HTTP endpoints
# ---------------------------------------------------------------------------


class TestJobCancelPriorityHTTP:
    """Test /jobs/<id>/cancel and /jobs/<id>/priority endpoints."""

    def _post(self, server, path, data=None):
        host, port = server.server_address
        url = "http://%s:%d%s" % (host, port, path)
        body = json.dumps(data).encode() if data is not None else b""
        req = urllib.request.Request(url, data=body, method="POST")
        if body:
            req.add_header("Content-Type", "application/json")
            req.add_header("Content-Length", str(len(body)))
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read()), resp.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read()), e.code

    def test_cancel_pending_job(self, live_server):
        jid = live_server.job_db.add_job("/movie.mkv", "/cfg.ini")
        data, status = self._post(live_server, "/jobs/%d/cancel" % jid)
        assert status == 200
        assert data["cancelled"] is True
        assert data["job_id"] == jid

    def test_cancel_completed_job_returns_409(self, live_server):
        jid = live_server.job_db.add_job("/movie.mkv", "/cfg.ini")
        live_server.job_db.start_job(jid, worker_id=1)
        live_server.job_db.complete_job(jid)
        data, status = self._post(live_server, "/jobs/%d/cancel" % jid)
        assert status == 409

    def test_cancel_nonexistent_job_returns_404(self, live_server):
        data, status = self._post(live_server, "/jobs/9999/cancel")
        assert status == 404

    def test_cancel_invalid_job_id_returns_400(self, live_server):
        data, status = self._post(live_server, "/jobs/notanumber/cancel")
        assert status == 400

    def test_set_priority_pending_job(self, live_server):
        jid = live_server.job_db.add_job("/movie.mkv", "/cfg.ini")
        data, status = self._post(live_server, "/jobs/%d/priority" % jid, data={"priority": 5})
        assert status == 200
        assert data["job_id"] == jid
        assert data["priority"] == 5

    def test_set_priority_running_job_returns_409(self, live_server):
        jid = live_server.job_db.add_job("/movie.mkv", "/cfg.ini")
        live_server.job_db.start_job(jid, worker_id=1)
        data, status = self._post(live_server, "/jobs/%d/priority" % jid, data={"priority": 5})
        assert status == 409

    def test_set_priority_nonexistent_job_returns_404(self, live_server):
        data, status = self._post(live_server, "/jobs/9999/priority", data={"priority": 5})
        assert status == 404

    def test_set_priority_missing_field_returns_400(self, live_server):
        jid = live_server.job_db.add_job("/movie.mkv", "/cfg.ini")
        data, status = self._post(live_server, "/jobs/%d/priority" % jid, data={})
        assert status == 400
        assert "priority" in data["error"]

    def test_set_priority_invalid_value_returns_400(self, live_server):
        jid = live_server.job_db.add_job("/movie.mkv", "/cfg.ini")
        data, status = self._post(live_server, "/jobs/%d/priority" % jid, data={"priority": "high"})
        assert status == 400


# ---------------------------------------------------------------------------
# Scan filter/record HTTP endpoints
# ---------------------------------------------------------------------------


class TestScanHTTPEndpoints:
    """Test GET /scan, POST /scan/filter, POST /scan/record."""

    def _get(self, server, path):
        host, port = server.server_address
        url = "http://%s:%d%s" % (host, port, path)
        try:
            with urllib.request.urlopen(url) as resp:
                return json.loads(resp.read()), resp.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read()), e.code

    def _post(self, server, path, data=None):
        host, port = server.server_address
        url = "http://%s:%d%s" % (host, port, path)
        body = json.dumps(data).encode() if data is not None else b"{}"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Content-Length", str(len(body)))
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read()), resp.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read()), e.code

    def test_get_scan_empty(self, live_server):
        data, status = self._get(live_server, "/scan")
        assert status == 200
        assert data["unscanned"] == []
        assert data["total"] == 0

    def test_get_scan_with_paths(self, live_server):
        data, status = self._get(live_server, "/scan?path=/a.mkv&path=/b.mkv")
        assert status == 200
        assert set(data["unscanned"]) == {"/a.mkv", "/b.mkv"}
        assert data["total"] == 2

    def test_post_scan_filter(self, live_server):
        live_server.job_db.record_scanned(["/a.mkv"])
        data, status = self._post(live_server, "/scan/filter", data={"paths": ["/a.mkv", "/b.mkv"]})
        assert status == 200
        assert data["unscanned"] == ["/b.mkv"]
        assert data["total"] == 2
        assert data["already_scanned"] == 1

    def test_post_scan_record(self, live_server):
        data, status = self._post(live_server, "/scan/record", data={"paths": ["/a.mkv", "/b.mkv"]})
        assert status == 200
        assert data["recorded"] == 2
        # Verify they are now marked scanned
        assert live_server.job_db.filter_unscanned(["/a.mkv", "/b.mkv"]) == []

    def test_post_scan_filter_empty(self, live_server):
        data, status = self._post(live_server, "/scan/filter", data={"paths": []})
        assert status == 200
        assert data["unscanned"] == []

    def test_post_scan_record_empty(self, live_server):
        data, status = self._post(live_server, "/scan/record", data={"paths": []})
        assert status == 200
        assert data["recorded"] == 0


# ---------------------------------------------------------------------------
# Reload endpoint
# ---------------------------------------------------------------------------


class TestReloadEndpoint:
    """Test POST /reload."""

    def _post(self, server, path):
        host, port = server.server_address
        url = "http://%s:%d%s" % (host, port, path)
        req = urllib.request.Request(url, data=b"", method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read()), resp.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read()), e.code

    def test_reload_returns_200(self, live_server):
        data, status = self._post(live_server, "/reload")
        assert status == 200
        assert data["status"] == "reloading"


# ---------------------------------------------------------------------------
# Docs endpoint
# ---------------------------------------------------------------------------


class TestDocsEndpoint:
    """Test GET /docs and /docs/<slug>."""

    def _get_html(self, server, path):
        host, port = server.server_address
        url = "http://%s:%d%s" % (host, port, path)
        req = urllib.request.Request(url, headers={"Accept": "text/html"})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.read().decode(), resp.status
        except urllib.error.HTTPError as e:
            return e.read().decode(), e.code

    def test_docs_index_returns_200(self, live_server):
        html, status = self._get_html(live_server, "/docs")
        assert status == 200
        assert "<html" in html.lower()

    def test_docs_subpage_returns_200(self, live_server):
        html, status = self._get_html(live_server, "/docs/getting-started")
        assert status == 200
        assert "Getting Started" in html

    def test_docs_nonexistent_page_returns_404(self, live_server):
        html, status = self._get_html(live_server, "/docs/nonexistent-page")
        assert status == 404

    def test_docs_index_contains_nav(self, live_server):
        html, status = self._get_html(live_server, "/docs")
        assert status == 200
        assert "Getting Started" in html  # Nav item from DOC_PAGES

    def test_docs_is_public_endpoint(self, live_server):
        """Docs endpoints should be accessible without auth even when API key is set."""
        live_server.api_key = "secret"
        try:
            html, status = self._get_html(live_server, "/docs")
            assert status == 200
        finally:
            live_server.api_key = None


# ---------------------------------------------------------------------------
# do_HEAD endpoint
# ---------------------------------------------------------------------------


class TestHeadEndpoint:
    """Test do_HEAD handler."""

    def test_head_request_returns_200(self, live_server):
        host, port = live_server.server_address
        url = "http://%s:%d/health" % (host, port)
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type", "").startswith("application/json")
