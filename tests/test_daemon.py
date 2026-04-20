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
        assert "fetch('/webhook/generic'" in DASHBOARD_HTML

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


class TestLogEndpoints:
    """Test /logs and /logs/<name> endpoints."""

    def _get(self, server, path):
        host, port = server.server_address
        url = "http://%s:%d%s" % (host, port, path)
        with urllib.request.urlopen(url) as resp:
            return json.loads(resp.read()), resp.status

    def _get_404(self, server, path):
        host, port = server.server_address
        url = "http://%s:%d%s" % (host, port, path)
        try:
            urllib.request.urlopen(url)
            return None  # no error raised
        except urllib.error.HTTPError as e:
            return e.code

    def test_logs_list_returns_array(self, live_server):
        data, status = self._get(live_server, "/logs")
        assert status == 200
        assert isinstance(data, list)

    def test_logs_list_entries_have_required_fields(self, live_server):
        # Register a config logger so the list is non-empty
        live_server.config_log_manager.get_logger("/tmp/autoProcess.test.ini")
        data, _ = self._get(live_server, "/logs")
        for entry in data:
            assert "name" in entry
            assert "file" in entry
            assert "size" in entry

    def test_logs_unknown_name_returns_404(self, live_server):
        code = self._get_404(live_server, "/logs/nonexistent-log-abc123")
        assert code == 404

    def test_logs_path_traversal_returns_404(self, live_server):
        code = self._get_404(live_server, "/logs/../../etc/passwd")
        assert code == 404

    def test_logs_content_returns_entries_and_file_size(self, live_server, tmp_path):
        import os

        # Write a test log file in the manager's logs_dir
        log_name = "autoProcess.testlog"
        log_path = os.path.join(live_server.config_log_manager.logs_dir, log_name + ".log")
        with open(log_path, "w") as f:
            f.write('{"timestamp":"2026-01-01 00:00:00","level":"INFO","job_id":"1","message":"hello"}\n')
            f.write('{"timestamp":"2026-01-01 00:00:01","level":"ERROR","job_id":"2","message":"oops"}\n')
        # Register the logger so it appears in get_all_log_files
        live_server.config_log_manager.get_logger("/tmp/" + log_name + ".ini")

        data, status = self._get(live_server, "/logs/" + log_name + "?lines=100")
        assert status == 200
        assert "entries" in data
        assert "file_size" in data
        assert len(data["entries"]) == 2
        assert data["file_size"] > 0

    def test_logs_content_job_id_filter(self, live_server, tmp_path):
        import os

        log_name = "autoProcess.filterjob"
        log_path = os.path.join(live_server.config_log_manager.logs_dir, log_name + ".log")
        with open(log_path, "w") as f:
            f.write('{"timestamp":"2026-01-01 00:00:00","level":"INFO","job_id":"10","message":"job10"}\n')
            f.write('{"timestamp":"2026-01-01 00:00:01","level":"INFO","job_id":"20","message":"job20"}\n')
        live_server.config_log_manager.get_logger("/tmp/" + log_name + ".ini")

        data, _ = self._get(live_server, "/logs/" + log_name + "?job_id=10")
        assert all(e["job_id"] == "10" for e in data["entries"])
        assert len(data["entries"]) == 1

    def test_logs_tail_requires_offset(self, live_server):
        # Register a logger first
        log_name = "autoProcess.tailtest"
        live_server.config_log_manager.get_logger("/tmp/" + log_name + ".ini")
        host, port = live_server.server_address
        url = "http://%s:%d/logs/%s/tail" % (host, port, log_name)
        try:
            urllib.request.urlopen(url)
            assert False, "Expected 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400

    def test_logs_tail_returns_new_content(self, live_server):
        import os

        log_name = "autoProcess.tailpoll"
        log_path = os.path.join(live_server.config_log_manager.logs_dir, log_name + ".log")
        with open(log_path, "w") as f:
            f.write('{"timestamp":"2026-01-01 00:00:00","level":"INFO","job_id":"5","message":"first"}\n')
        live_server.config_log_manager.get_logger("/tmp/" + log_name + ".ini")

        # First fetch to get file_size
        data, _ = self._get(live_server, "/logs/" + log_name + "?lines=100")
        offset = data["file_size"]

        # Append a new line
        with open(log_path, "a") as f:
            f.write('{"timestamp":"2026-01-01 00:00:01","level":"INFO","job_id":"5","message":"second"}\n')

        tail, status = self._get(live_server, "/logs/%s/tail?offset=%d" % (log_name, offset))
        assert status == 200
        assert len(tail["entries"]) == 1
        assert tail["entries"][0]["message"] == "second"


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

    def test_get_all_log_files_empty(self, tmp_path):
        mgr = ConfigLogManager(str(tmp_path))
        assert mgr.get_all_log_files() == []

    def test_get_all_log_files_after_get_logger(self, tmp_path):
        mgr = ConfigLogManager(str(tmp_path))
        mgr.get_logger("/cfg/autoProcess.ini")
        mgr.get_logger("/cfg/autoProcess.tv.ini")
        result = mgr.get_all_log_files()
        names = {e["name"] for e in result}
        assert names == {"autoProcess", "autoProcess.tv"}
        for e in result:
            assert e["path"].endswith(e["name"] + ".log")

    def test_get_all_log_files_deduplicates(self, tmp_path):
        mgr = ConfigLogManager(str(tmp_path))
        mgr.get_logger("/cfg/autoProcess.ini")
        mgr.get_logger("/cfg/autoProcess.ini")  # same config twice
        assert len(mgr.get_all_log_files()) == 1


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


# ---------------------------------------------------------------------------
# PostgreSQLJobDatabase unit tests (mocked psycopg2 — no real DB required)
# ---------------------------------------------------------------------------


def _make_fake_psycopg2():
    """Return a MagicMock tree that mimics psycopg2's pool / extras interface."""
    from unittest.mock import MagicMock, patch

    pool_mod = MagicMock()
    extras_mod = MagicMock()
    psycopg2_mod = MagicMock()

    # cursor_factory sentinel so RealDictCursor can be referenced
    extras_mod.RealDictCursor = object()
    psycopg2_mod.extras = extras_mod
    psycopg2_mod.pool = pool_mod

    return psycopg2_mod, pool_mod, extras_mod


def _make_db_with_mock_pool(mock_conn=None, mock_cursor=None):
    """
    Construct a PostgreSQLJobDatabase whose pool and _init_db/_reset_running_jobs
    are fully mocked so the constructor completes without touching Postgres.
    Returns (db, pool_mock, conn_mock, cursor_mock).
    """
    from unittest.mock import MagicMock, patch

    psycopg2_mock = MagicMock()
    pool_mock = MagicMock()
    psycopg2_mock.pool.ThreadedConnectionPool.return_value = pool_mock
    psycopg2_mock.extras.RealDictCursor = object()

    if mock_cursor is None:
        mock_cursor = MagicMock()
        # Default: fetchone returns None
        mock_cursor.fetchone.return_value = None
        mock_cursor.fetchall.return_value = []
        mock_cursor.rowcount = 0
    if mock_conn is None:
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    pool_mock.getconn.return_value = mock_conn

    with patch.dict("sys.modules", {"psycopg2": psycopg2_mock, "psycopg2.pool": psycopg2_mock.pool, "psycopg2.extras": psycopg2_mock.extras}):
        with patch.object(PostgreSQLJobDatabase, "_init_db"):
            with patch.object(PostgreSQLJobDatabase, "_reset_running_jobs"):
                db = PostgreSQLJobDatabase.__new__(PostgreSQLJobDatabase)
                db.db_url = "postgresql://mock/test"
                db._node_id = "testnode"
                db._pool = pool_mock
                from resources.log import getLogger

                db.log = getLogger("TEST")

    return db, pool_mock, mock_conn, mock_cursor


class TestPostgreSQLJobDatabase:
    """Unit tests for PostgreSQLJobDatabase with mocked psycopg2."""

    # ------------------------------------------------------------------
    # Constructor / import guard
    # ------------------------------------------------------------------

    def test_constructor_raises_when_psycopg2_missing(self):
        import sys

        blocked = {"psycopg2": None, "psycopg2.pool": None, "psycopg2.extras": None}
        with pytest.raises(ImportError, match="psycopg2 is required"):
            with pytest.MonkeyPatch().context() as mp:
                for mod, val in blocked.items():
                    mp.setitem(sys.modules, mod, val)
                PostgreSQLJobDatabase("postgresql://localhost/test")

    def test_constructor_creates_pool_and_inits_db(self):
        from unittest.mock import MagicMock, patch

        psycopg2_mock = MagicMock()
        psycopg2_mock.extras.RealDictCursor = object()
        pool_mock = MagicMock()
        psycopg2_mock.pool.ThreadedConnectionPool.return_value = pool_mock

        with patch.dict("sys.modules", {"psycopg2": psycopg2_mock, "psycopg2.pool": psycopg2_mock.pool, "psycopg2.extras": psycopg2_mock.extras}):
            with patch.object(PostgreSQLJobDatabase, "_init_db") as init_db:
                with patch.object(PostgreSQLJobDatabase, "_reset_running_jobs"):
                    db = PostgreSQLJobDatabase("postgresql://mock/test", max_connections=5)
                    assert db._pool is pool_mock
                    assert db.db_url == "postgresql://mock/test"
                    # _init_db is called by __init__; _reset_running_jobs is called inside _init_db
                    init_db.assert_called_once()

    # ------------------------------------------------------------------
    # close()
    # ------------------------------------------------------------------

    def test_close_calls_closeall(self):
        db, pool_mock, _, _ = _make_db_with_mock_pool()
        db.close()
        pool_mock.closeall.assert_called_once()

    # ------------------------------------------------------------------
    # _conn context manager
    # ------------------------------------------------------------------

    def test_conn_commits_on_success(self):
        from unittest.mock import MagicMock

        db, pool_mock, conn_mock, _ = _make_db_with_mock_pool()
        with db._conn():
            pass
        conn_mock.commit.assert_called_once()
        pool_mock.putconn.assert_called_once_with(conn_mock)

    def test_conn_rolls_back_and_reraises_on_exception(self):
        from unittest.mock import MagicMock

        db, pool_mock, conn_mock, _ = _make_db_with_mock_pool()
        with pytest.raises(ValueError):
            with db._conn():
                raise ValueError("boom")
        conn_mock.rollback.assert_called_once()
        pool_mock.putconn.assert_called_once_with(conn_mock)

    # ------------------------------------------------------------------
    # _requeue_running_jobs_for_node
    # ------------------------------------------------------------------

    def test_requeue_running_jobs_returns_rowcount(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.rowcount = 3
        cur.fetchone.return_value = None
        cur.fetchall.return_value = []
        db, pool_mock, conn_mock, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db._requeue_running_jobs_for_node("node1")
        assert result == 3

    def test_reset_running_jobs_logs_when_interrupted(self):
        from unittest.mock import MagicMock, patch

        db, _, _, _ = _make_db_with_mock_pool()
        with patch.object(db, "_requeue_running_jobs_for_node", return_value=2) as mock_req:
            with patch.object(db.log, "info") as mock_log:
                db._reset_running_jobs()
                mock_req.assert_called_once_with(db._node_id)
                mock_log.assert_called_once()

    def test_reset_running_jobs_no_log_when_zero(self):
        from unittest.mock import MagicMock, patch

        db, _, _, _ = _make_db_with_mock_pool()
        with patch.object(db, "_requeue_running_jobs_for_node", return_value=0):
            with patch.object(db.log, "info") as mock_log:
                db._reset_running_jobs()
                mock_log.assert_not_called()

    # ------------------------------------------------------------------
    # add_job
    # ------------------------------------------------------------------

    def test_add_job_returns_id_when_no_duplicate(self):
        from unittest.mock import MagicMock, call

        cur = MagicMock()
        # First fetchone: no existing job; second fetchone: new id
        cur.fetchone.side_effect = [None, {"id": 42}]
        cur.fetchall.return_value = []
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.add_job("/path/movie.mkv", "/cfg/autoProcess.ini")
        assert result == 42

    def test_add_job_returns_none_on_duplicate(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 10}
        cur.fetchall.return_value = []
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.add_job("/path/movie.mkv", "/cfg/autoProcess.ini")
        assert result is None

    def test_add_job_passes_args_as_json(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.side_effect = [None, {"id": 99}]
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        db.add_job("/path/ep.mkv", "/cfg/tv.ini", args=["-tmdb", "123"], max_retries=2)
        # Second execute call should include JSON-encoded args
        calls = cur.execute.call_args_list
        insert_call = calls[1]
        assert '"-tmdb"' in insert_call[0][1][2]

    # ------------------------------------------------------------------
    # find_active_job
    # ------------------------------------------------------------------

    def test_find_active_job_returns_dict_when_found(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 5, "path": "/a.mkv", "status": "pending"}
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.find_active_job("/a.mkv")
        assert result == {"id": 5, "path": "/a.mkv", "status": "pending"}

    def test_find_active_job_returns_none_when_not_found(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = None
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.find_active_job("/missing.mkv")
        assert result is None

    # ------------------------------------------------------------------
    # claim_next_job
    # ------------------------------------------------------------------

    def test_claim_next_job_returns_none_when_no_pending(self):
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        cur.fetchone.return_value = None
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.claim_next_job(worker_id=1, node_id="node1")
        assert result is None

    def test_claim_next_job_returns_job_when_available(self):
        from unittest.mock import MagicMock, patch

        pending_row = {"id": 7, "path": "/m.mkv", "config": "/c.ini", "args": "[]"}
        full_row = {"id": 7, "path": "/m.mkv", "config": "/c.ini", "args": "[]", "status": "running"}
        cur = MagicMock()
        cur.fetchone.return_value = pending_row
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        with patch.object(db, "get_job", return_value=full_row) as mock_get:
            result = db.claim_next_job(worker_id=1, node_id="node1")
            mock_get.assert_called_once_with(7)
            assert result == full_row

    def test_claim_next_job_uses_exclude_configs(self):
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        cur.fetchone.return_value = None
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        db.claim_next_job(worker_id=1, node_id="node1", exclude_configs={"/cfg/tv.ini"})
        # The SQL should include the exclude clause
        sql_called = cur.execute.call_args[0][0]
        assert "ALL" in sql_called or "!=" in sql_called

    # ------------------------------------------------------------------
    # get_job
    # ------------------------------------------------------------------

    def test_get_job_returns_dict_when_found(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 3, "path": "/x.mkv"}
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.get_job(3)
        assert result == {"id": 3, "path": "/x.mkv"}

    def test_get_job_returns_none_when_not_found(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = None
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.get_job(9999)
        assert result is None

    # ------------------------------------------------------------------
    # get_jobs / get_pending_jobs / get_next_pending_job / get_running_jobs
    # ------------------------------------------------------------------

    def test_get_jobs_returns_list(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = [{"id": 1, "status": "pending"}, {"id": 2, "status": "pending"}]
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.get_jobs()
        assert len(result) == 2

    def test_get_jobs_with_status_filter(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = [{"id": 1, "status": "running"}]
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.get_jobs(status="running")
        assert result[0]["status"] == "running"
        sql = cur.execute.call_args[0][0]
        assert "status" in sql

    def test_get_jobs_with_config_filter(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = []
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        db.get_jobs(config="/cfg/tv.ini")
        sql = cur.execute.call_args[0][0]
        assert "config" in sql

    def test_get_pending_jobs_returns_list(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = [{"id": 1, "status": "pending"}]
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.get_pending_jobs()
        assert len(result) == 1

    def test_get_next_pending_job_returns_dict_when_found(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 4, "status": "pending"}
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.get_next_pending_job()
        assert result == {"id": 4, "status": "pending"}

    def test_get_next_pending_job_returns_none_when_empty(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = None
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.get_next_pending_job()
        assert result is None

    def test_get_running_jobs_returns_list(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = [{"id": 2, "status": "running"}]
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.get_running_jobs()
        assert len(result) == 1
        assert result[0]["status"] == "running"

    # ------------------------------------------------------------------
    # start_job / complete_job
    # ------------------------------------------------------------------

    def test_start_job_executes_update(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        db.start_job(job_id=5, worker_id=2)
        cur.execute.assert_called_once()
        sql, params = cur.execute.call_args[0]
        assert "UPDATE" in sql.upper()
        assert 5 in params

    def test_complete_job_sets_completed_status(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        db.complete_job(job_id=8)
        cur.execute.assert_called_once()
        sql, params = cur.execute.call_args[0]
        assert STATUS_COMPLETED in params
        assert 8 in params

    # ------------------------------------------------------------------
    # fail_job
    # ------------------------------------------------------------------

    def test_fail_job_marks_failed_when_no_retries(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = {"retry_count": 0, "max_retries": 0}
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        db.fail_job(job_id=11, error="oops")
        # Second execute should mark as failed
        last_call = cur.execute.call_args_list[-1]
        sql, params = last_call[0]
        assert STATUS_FAILED in params
        assert "oops" in params

    def test_fail_job_requeues_with_backoff_when_retries_remain(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = {"retry_count": 0, "max_retries": 3}
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        db.fail_job(job_id=12, error="transient")
        last_call = cur.execute.call_args_list[-1]
        sql, params = last_call[0]
        # Should use STATUS_PENDING and include retry_count=1
        assert STATUS_PENDING in params
        assert 1 in params  # retry_count

    def test_fail_job_no_row_still_marks_failed(self):
        """If the job row can't be found, fail gracefully — just mark failed."""
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = None
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        db.fail_job(job_id=99, error="not found")
        # Ensure we still try to mark as failed
        last_call = cur.execute.call_args_list[-1]
        sql, params = last_call[0]
        assert STATUS_FAILED in params

    # ------------------------------------------------------------------
    # get_stats
    # ------------------------------------------------------------------

    def test_get_stats_returns_dict_with_total(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = [{"status": "pending", "count": 3}, {"status": "completed", "count": 10}]
        cur.fetchone.return_value = {"total": 13}
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        stats = db.get_stats()
        assert stats["pending"] == 3
        assert stats["completed"] == 10
        assert stats["total"] == 13

    def test_get_stats_empty_db(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = []
        cur.fetchone.return_value = {"total": 0}
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        stats = db.get_stats()
        assert stats["total"] == 0

    # ------------------------------------------------------------------
    # cleanup_old_jobs
    # ------------------------------------------------------------------

    def test_cleanup_old_jobs_returns_deleted_count(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.rowcount = 5
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.cleanup_old_jobs(days=7)
        assert result == 5

    def test_cleanup_old_jobs_zero_deleted_no_log(self):
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        cur.rowcount = 0
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        with patch.object(db.log, "info") as mock_log:
            result = db.cleanup_old_jobs(days=30)
            assert result == 0
            mock_log.assert_not_called()

    # ------------------------------------------------------------------
    # pending_count / pending_count_for_config
    # ------------------------------------------------------------------

    def test_pending_count_returns_integer(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = {"count": 7}
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        assert db.pending_count() == 7

    def test_pending_count_for_config_returns_integer(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = {"count": 2}
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        assert db.pending_count_for_config("/cfg/tv.ini") == 2

    # ------------------------------------------------------------------
    # heartbeat
    # ------------------------------------------------------------------

    def test_heartbeat_returns_none_when_no_command(self):
        from datetime import datetime
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = {"pending_command": None}
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.heartbeat("node1", "host1", 4, datetime.utcnow())
        assert result is None

    def test_heartbeat_returns_command_and_clears_it(self):
        from datetime import datetime
        from unittest.mock import MagicMock, call

        cur = MagicMock()
        cur.fetchone.return_value = {"pending_command": "shutdown"}
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.heartbeat("node1", "host1", 4, datetime.utcnow())
        assert result == "shutdown"
        # Should clear the command
        clear_call = cur.execute.call_args_list[-1]
        assert "NULL" in clear_call[0][0]

    def test_heartbeat_no_row_returns_none(self):
        from datetime import datetime
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = None
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.heartbeat("node1", "host1", 2, datetime.utcnow())
        assert result is None

    # ------------------------------------------------------------------
    # get_cluster_nodes
    # ------------------------------------------------------------------

    def test_get_cluster_nodes_returns_empty_list_when_no_nodes(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = []
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.get_cluster_nodes()
        assert result == []

    def test_get_cluster_nodes_attaches_active_jobs(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        nodes = [{"node_id": "n1", "host": "h1", "workers": 2, "uptime_seconds": 100}]
        jobs = [{"node_id": "n1", "job_id": 5, "path": "/m.mkv", "config": "/c.ini"}]
        cur.fetchall.side_effect = [nodes, jobs]
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.get_cluster_nodes()
        assert len(result) == 1
        assert len(result[0]["active_jobs"]) == 1
        assert result[0]["active_jobs"][0]["job_id"] == 5

    def test_get_cluster_nodes_empty_active_jobs_when_no_running(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        nodes = [{"node_id": "n1", "host": "h1", "workers": 2, "uptime_seconds": 50}]
        cur.fetchall.side_effect = [nodes, []]
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.get_cluster_nodes()
        assert result[0]["active_jobs"] == []

    # ------------------------------------------------------------------
    # recover_stale_nodes
    # ------------------------------------------------------------------

    def test_recover_stale_nodes_returns_empty_when_no_stale(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = []
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.recover_stale_nodes()
        assert result == []

    def test_recover_stale_nodes_requeues_and_marks_offline(self):
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        cur.fetchall.return_value = [{"node_id": "dead_node"}]
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        with patch.object(db, "_requeue_running_jobs_for_node", return_value=2) as mock_req:
            with patch.object(db.log, "warning") as mock_warn:
                result = db.recover_stale_nodes(stale_seconds=60)
                mock_req.assert_called_once_with("dead_node")
                mock_warn.assert_called_once()
                assert result == [("dead_node", 2)]

    # ------------------------------------------------------------------
    # mark_node_offline
    # ------------------------------------------------------------------

    def test_mark_node_offline_requeues_and_updates_status(self):
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        with patch.object(db, "_requeue_running_jobs_for_node", return_value=1) as mock_req:
            with patch.object(db.log, "info") as mock_log:
                db.mark_node_offline("node1")
                mock_req.assert_called_once_with("node1")
                mock_log.assert_called_once()

    def test_mark_node_offline_no_log_when_none_requeued(self):
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        with patch.object(db, "_requeue_running_jobs_for_node", return_value=0):
            with patch.object(db.log, "info") as mock_log:
                db.mark_node_offline("node1")
                mock_log.assert_not_called()

    # ------------------------------------------------------------------
    # send_node_command
    # ------------------------------------------------------------------

    def test_send_node_command_targets_specific_node(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = [{"node_id": "n1"}]
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.send_node_command("n1", "shutdown")
        assert result == ["n1"]
        sql = cur.execute.call_args[0][0]
        assert "node_id" in sql

    def test_send_node_command_broadcasts_when_node_id_is_none(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = [{"node_id": "n1"}, {"node_id": "n2"}]
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.send_node_command(None, "restart")
        assert len(result) == 2
        sql = cur.execute.call_args[0][0]
        assert "online" in sql

    # ------------------------------------------------------------------
    # requeue_job / requeue_failed_jobs
    # ------------------------------------------------------------------

    def test_requeue_job_returns_true_when_requeued(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 4}
        cur.rowcount = 1
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.requeue_job(4)
        assert result is True

    def test_requeue_job_returns_false_when_not_failed(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = None
        cur.rowcount = 0
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.requeue_job(99)
        assert result is False

    def test_requeue_failed_jobs_returns_count(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.rowcount = 3
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.requeue_failed_jobs()
        assert result == 3

    def test_requeue_failed_jobs_with_config_filter(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.rowcount = 1
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.requeue_failed_jobs(config="/cfg/tv.ini")
        assert result == 1
        sql = cur.execute.call_args[0][0]
        assert "config" in sql

    # ------------------------------------------------------------------
    # cancel_job
    # ------------------------------------------------------------------

    def test_cancel_job_returns_true_when_cancelled(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 6}
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.cancel_job(6)
        assert result is True

    def test_cancel_job_returns_false_when_not_cancellable(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = None
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.cancel_job(99)
        assert result is False

    # ------------------------------------------------------------------
    # set_job_priority
    # ------------------------------------------------------------------

    def test_set_job_priority_returns_true_when_updated(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 3}
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.set_job_priority(3, 10)
        assert result is True

    def test_set_job_priority_returns_false_when_not_pending(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchone.return_value = None
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.set_job_priority(3, 10)
        assert result is False

    # ------------------------------------------------------------------
    # delete_failed_jobs / delete_offline_nodes / delete_all_jobs
    # ------------------------------------------------------------------

    def test_delete_failed_jobs_returns_count(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.rowcount = 4
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.delete_failed_jobs()
        assert result == 4

    def test_delete_failed_jobs_zero_no_log(self):
        from unittest.mock import MagicMock, patch

        cur = MagicMock()
        cur.rowcount = 0
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        with patch.object(db.log, "info") as mock_log:
            result = db.delete_failed_jobs()
            assert result == 0
            mock_log.assert_not_called()

    def test_delete_offline_nodes_returns_count(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.rowcount = 2
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.delete_offline_nodes()
        assert result == 2

    def test_delete_all_jobs_returns_count(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.rowcount = 15
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.delete_all_jobs()
        assert result == 15

    # ------------------------------------------------------------------
    # filter_unscanned / record_scanned
    # ------------------------------------------------------------------

    def test_filter_unscanned_returns_empty_for_empty_input(self):
        db, _, _, _ = _make_db_with_mock_pool()
        result = db.filter_unscanned([])
        assert result == []

    def test_filter_unscanned_returns_new_paths(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = [{"path": "/a.mkv"}]
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        result = db.filter_unscanned(["/a.mkv", "/b.mkv"])
        assert "/b.mkv" in result
        assert "/a.mkv" not in result

    def test_record_scanned_noop_for_empty_input(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        db.record_scanned([])
        cur.executemany.assert_not_called()

    def test_record_scanned_inserts_paths(self):
        from unittest.mock import MagicMock

        cur = MagicMock()
        db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
        db.record_scanned(["/x.mkv", "/y.mkv"])
        cur.executemany.assert_called_once()
        args = cur.executemany.call_args[0]
        assert len(args[1]) == 2

    # ------------------------------------------------------------------
    # is_distributed flag
    # ------------------------------------------------------------------

    def test_is_distributed_true(self):
        assert PostgreSQLJobDatabase.is_distributed is True


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
        data, status = self._post(live_server, "/webhook/generic", data=None)
        assert status == 400

    def test_webhook_nonexistent_path(self, live_server):
        data, status = self._post(live_server, "/webhook/generic", data={"path": "/nonexistent/file.mkv"})
        assert status == 400
        assert "does not exist" in data["error"]

    def test_webhook_valid_path_json(self, live_server, tmp_path):
        f = tmp_path / "movie.mkv"
        f.touch()
        data, status = self._post(live_server, "/webhook/generic", data={"path": str(f)})
        assert status == 202
        assert "job_id" in data
        assert data["job_id"] is not None

    def test_webhook_plain_text_body(self, live_server, tmp_path):
        f = tmp_path / "movie.mkv"
        f.touch()
        host, port = live_server.server_address
        url = "http://%s:%d/webhook/generic" % (host, port)
        body = str(f).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Length", str(len(body)))
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        assert "job_id" in data

    def test_webhook_duplicate_submission(self, live_server, tmp_path):
        f = tmp_path / "movie.mkv"
        f.touch()
        data1, _ = self._post(live_server, "/webhook/generic", data={"path": str(f)})
        data2, status2 = self._post(live_server, "/webhook/generic", data={"path": str(f)})
        assert status2 == 200
        assert data2["status"] == "duplicate"

    def test_webhook_json_string_arg(self, live_server, tmp_path):
        """JSON body where args is a string gets split into a list."""
        f = tmp_path / "movie.mkv"
        f.touch()
        data, status = self._post(live_server, "/webhook/generic", data={"path": str(f), "args": "-tmdb 603"})
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
        data, status = self._post(live_server, "/webhook/generic", data={"path": str(f)})
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
        data, status = self._post(live_server, "/webhook/generic", data={"path": str(f)})
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
        data, status = self._post(live_server, "/webhook/generic", data={"path": str(f)})
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


# ---------------------------------------------------------------------------
# Native Sonarr / Radarr webhook parser (unit tests — no live server needed)
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Collects send_json_response calls for assertion."""

    def __init__(self):
        self.status = None
        self.data = None

    def capture(self, status, data):
        self.status = status
        self.data = data


def _make_media_handler(payload: dict) -> "tuple[WebhookHandler, _FakeResponse]":
    """Build a bare WebhookHandler with rfile set to *payload* JSON bytes."""
    import io

    body = json.dumps(payload).encode()
    handler = WebhookHandler.__new__(WebhookHandler)
    handler.headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
    handler.rfile = io.BytesIO(body)
    resp = _FakeResponse()
    handler.send_json_response = resp.capture
    return handler, resp


class TestParseSonarrBody:
    """Unit tests for WebhookHandler._parse_sonarr_body."""

    def test_download_event_returns_path_and_args(self):
        payload = {
            "eventType": "Download",
            "episodeFile": {"path": "/mnt/TV/Show/S01E01.mkv"},
            "series": {"tvdbId": 73871},
            "episodes": [{"seasonNumber": 1, "episodeNumber": 1}],
        }
        handler, _ = _make_media_handler(payload)
        path, args = handler._parse_sonarr_body()
        assert path == "/mnt/TV/Show/S01E01.mkv"
        assert "-tv" in args
        assert "-tvdb" in args
        assert "73871" in args
        assert "-s" in args
        assert "1" in args
        assert "-e" in args

    def test_multi_episode_appends_all_episode_numbers(self):
        payload = {
            "eventType": "Download",
            "episodeFile": {"path": "/mnt/TV/Show/S02E01E02.mkv"},
            "series": {"tvdbId": 1234},
            "episodes": [
                {"seasonNumber": 2, "episodeNumber": 1},
                {"seasonNumber": 2, "episodeNumber": 2},
            ],
        }
        handler, _ = _make_media_handler(payload)
        path, args = handler._parse_sonarr_body()
        assert path == "/mnt/TV/Show/S02E01E02.mkv"
        assert args.count("-e") == 2

    def test_imdb_fallback_when_no_tvdb(self):
        payload = {
            "eventType": "Download",
            "episodeFile": {"path": "/mnt/TV/Show/ep.mkv"},
            "series": {"imdbId": "tt0306414"},
            "episodes": [],
        }
        handler, _ = _make_media_handler(payload)
        path, args = handler._parse_sonarr_body()
        assert path == "/mnt/TV/Show/ep.mkv"
        assert "-imdb" in args
        assert "tt0306414" in args

    def test_test_event_returns_none_and_200(self):
        payload = {"eventType": "Test"}
        handler, resp = _make_media_handler(payload)
        path, args = handler._parse_sonarr_body()
        assert path is None
        assert args == []
        assert resp.status == 200

    def test_unsupported_event_type_returns_none_and_400(self):
        payload = {"eventType": "Grab"}
        handler, resp = _make_media_handler(payload)
        path, args = handler._parse_sonarr_body()
        assert path is None
        assert resp.status == 400

    def test_missing_path_returns_none_and_400(self):
        payload = {
            "eventType": "Download",
            "episodeFile": {},
            "series": {"tvdbId": 1},
            "episodes": [],
        }
        handler, resp = _make_media_handler(payload)
        path, args = handler._parse_sonarr_body()
        assert path is None
        assert resp.status == 400

    def test_empty_body_returns_none_and_400(self):
        import io

        handler = WebhookHandler.__new__(WebhookHandler)
        handler.headers = {"Content-Length": "0"}
        handler.rfile = io.BytesIO(b"")
        resp = _FakeResponse()
        handler.send_json_response = resp.capture
        path, args = handler._parse_sonarr_body()
        assert path is None
        assert resp.status == 400


class TestParseRadarrBody:
    """Unit tests for WebhookHandler._parse_radarr_body."""

    def test_download_event_returns_path_and_tmdb(self):
        payload = {
            "eventType": "Download",
            "movieFile": {"path": "/mnt/Movies/The Matrix (1999).mkv"},
            "movie": {"tmdbId": 603},
        }
        handler, _ = _make_media_handler(payload)
        path, args = handler._parse_radarr_body()
        assert path == "/mnt/Movies/The Matrix (1999).mkv"
        assert "-movie" in args
        assert "-tmdb" in args
        assert "603" in args

    def test_imdb_fallback_when_no_tmdb(self):
        payload = {
            "eventType": "Download",
            "movieFile": {"path": "/mnt/Movies/film.mkv"},
            "movie": {"imdbId": "tt0133093"},
        }
        handler, _ = _make_media_handler(payload)
        path, args = handler._parse_radarr_body()
        assert path == "/mnt/Movies/film.mkv"
        assert "-imdb" in args
        assert "tt0133093" in args
        assert "-tmdb" not in args

    def test_no_id_still_queues_with_movie_flag(self):
        payload = {
            "eventType": "Download",
            "movieFile": {"path": "/mnt/Movies/film.mkv"},
            "movie": {},
        }
        handler, _ = _make_media_handler(payload)
        path, args = handler._parse_radarr_body()
        assert path == "/mnt/Movies/film.mkv"
        assert "-movie" in args
        assert "-tmdb" not in args
        assert "-imdb" not in args

    def test_test_event_returns_none_and_200(self):
        payload = {"eventType": "Test"}
        handler, resp = _make_media_handler(payload)
        path, args = handler._parse_radarr_body()
        assert path is None
        assert args == []
        assert resp.status == 200

    def test_unsupported_event_type_returns_none_and_400(self):
        payload = {"eventType": "Grab"}
        handler, resp = _make_media_handler(payload)
        path, args = handler._parse_radarr_body()
        assert path is None
        assert resp.status == 400

    def test_missing_path_returns_none_and_400(self):
        payload = {
            "eventType": "Download",
            "movieFile": {},
            "movie": {"tmdbId": 1},
        }
        handler, resp = _make_media_handler(payload)
        path, args = handler._parse_radarr_body()
        assert path is None
        assert resp.status == 400

    def test_empty_body_returns_none_and_400(self):
        import io

        handler = WebhookHandler.__new__(WebhookHandler)
        handler.headers = {"Content-Length": "0"}
        handler.rfile = io.BytesIO(b"")
        resp = _FakeResponse()
        handler.send_json_response = resp.capture
        path, args = handler._parse_radarr_body()
        assert path is None
        assert resp.status == 400
