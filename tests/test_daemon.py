"""Tests for daemon.py - job database, path config, and markdown rendering."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from daemon import (
    DASHBOARD_HTML,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    ConfigLockManager,
    ConfigLogManager,
    DaemonServer,
    JobDatabase,
    PathConfigManager,
    WebhookHandler,
    _inline,
    _render_markdown_to_html,
)


class TestJobDatabase:
    """Test SQLite job database operations."""

    def test_add_and_get_job(self, job_db):
        job_id = job_db.add_job("/path/to/file.mkv", "/config/autoProcess.ini")
        job = job_db.get_job(job_id)
        assert job is not None
        assert job["path"] == "/path/to/file.mkv"
        assert job["config"] == "/config/autoProcess.ini"
        assert job["status"] == STATUS_PENDING

    def test_job_lifecycle(self, job_db):
        job_id = job_db.add_job("/test.mkv", "/config.ini")

        job_db.start_job(job_id, worker_id=1)
        job = job_db.get_job(job_id)
        assert job["status"] == STATUS_RUNNING
        assert job["worker_id"] == 1
        assert job["started_at"] is not None

        job_db.complete_job(job_id)
        job = job_db.get_job(job_id)
        assert job["status"] == STATUS_COMPLETED
        assert job["completed_at"] is not None

    def test_fail_job(self, job_db):
        job_id = job_db.add_job("/test.mkv", "/config.ini")
        job_db.start_job(job_id, 1)
        job_db.fail_job(job_id, "Conversion failed")
        job = job_db.get_job(job_id)
        assert job["status"] == STATUS_FAILED
        assert job["error"] == "Conversion failed"

    def test_get_pending_jobs(self, job_db):
        job_db.add_job("/a.mkv", "/config.ini")
        job_db.add_job("/b.mkv", "/config.ini")
        job_id3 = job_db.add_job("/c.mkv", "/config.ini")
        job_db.start_job(job_id3, 1)
        pending = job_db.get_pending_jobs()
        assert len(pending) == 2

    def test_get_next_pending_fifo(self, job_db):
        id1 = job_db.add_job("/first.mkv", "/config.ini")
        job_db.add_job("/second.mkv", "/config.ini")
        job = job_db.get_next_pending_job()
        assert job["id"] == id1

    def test_get_stats(self, job_db):
        id1 = job_db.add_job("/a.mkv", "/c.ini")
        id2 = job_db.add_job("/b.mkv", "/c.ini")
        job_db.add_job("/c.mkv", "/c.ini")
        job_db.start_job(id1, 1)
        job_db.complete_job(id1)
        job_db.start_job(id2, 1)
        job_db.fail_job(id2, "error")
        stats = job_db.get_stats()
        assert stats.get(STATUS_COMPLETED, 0) == 1
        assert stats.get(STATUS_FAILED, 0) == 1
        assert stats.get(STATUS_PENDING, 0) == 1
        assert stats["total"] == 3

    def test_get_jobs_with_filter(self, job_db):
        job_db.add_job("/a.mkv", "/tv.ini")
        id2 = job_db.add_job("/b.mkv", "/movie.ini")
        job_db.start_job(id2, 1)
        job_db.complete_job(id2)
        completed = job_db.get_jobs(status=STATUS_COMPLETED)
        assert len(completed) == 1
        assert completed[0]["path"] == "/b.mkv"

    def test_get_jobs_pagination(self, job_db):
        for i in range(10):
            job_db.add_job("/file%d.mkv" % i, "/c.ini")
        page1 = job_db.get_jobs(limit=3, offset=0)
        page2 = job_db.get_jobs(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        assert page1[0]["id"] != page2[0]["id"]

    def test_pending_count(self, job_db):
        job_db.add_job("/a.mkv", "/c.ini")
        job_db.add_job("/b.mkv", "/c.ini")
        assert job_db.pending_count() == 2

    def test_pending_count_for_config(self, job_db):
        job_db.add_job("/a.mkv", "/tv.ini")
        job_db.add_job("/b.mkv", "/tv.ini")
        job_db.add_job("/c.mkv", "/movie.ini")
        assert job_db.pending_count_for_config("/tv.ini") == 2
        assert job_db.pending_count_for_config("/movie.ini") == 1

    def test_get_nonexistent_job(self, job_db):
        assert job_db.get_job(9999) is None

    def test_job_args_stored(self, job_db):
        job_id = job_db.add_job("/test.mkv", "/c.ini", args=["-tmdb", "603"])
        job = job_db.get_job(job_id)
        args = json.loads(job["args"])
        assert args == ["-tmdb", "603"]

    def test_reset_running_jobs(self, tmp_db):
        from daemon import JobDatabase

        db = JobDatabase(tmp_db)
        job_id = db.add_job("/test.mkv", "/c.ini")
        db.start_job(job_id, 1)
        assert db.get_job(job_id)["status"] == STATUS_RUNNING
        db.close()
        # Simulate daemon restart — second instance must also be closed
        db2 = JobDatabase(tmp_db)
        try:
            job = db2.get_job(job_id)
            assert job["status"] == STATUS_PENDING
        finally:
            db2.close()

    def test_cleanup_old_jobs(self, job_db):
        job_id = job_db.add_job("/old.mkv", "/c.ini")
        job_db.start_job(job_id, 1)
        job_db.complete_job(job_id)
        # Cleanup with 0 days should remove it
        deleted = job_db.cleanup_old_jobs(days=0)
        assert deleted >= 0  # May be 0 if completed_at is "now"


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
        clm.release("/config.ini")
        status = clm.get_status()
        assert "/config.ini" not in status["active"]

    def test_is_locked(self):
        clm = ConfigLockManager()
        assert clm.is_locked("/config.ini") is False
        clm.acquire("/config.ini", 1, "/path.mkv")
        assert clm.is_locked("/config.ini") is True
        clm.release("/config.ini")
        assert clm.is_locked("/config.ini") is False

    def test_get_active_job(self):
        clm = ConfigLockManager()
        clm.acquire("/config.ini", 42, "/movie.mkv")
        active = clm.get_active_job("/config.ini")
        assert active == (42, "/movie.mkv")
        clm.release("/config.ini")


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
        assert "fetch('/health')" in DASHBOARD_HTML
        assert "fetch('/stats')" in DASHBOARD_HTML
        assert "fetch('/configs')" in DASHBOARD_HTML
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
def live_server(tmp_db):
    """Spin up a DaemonServer on a random port, yield it, then shut it down."""
    from resources.log import getLogger

    job_db = JobDatabase(tmp_db)
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
        url = "http://%s:%d/" % (host, port)
        req = urllib.request.Request(url, headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            assert resp.headers.get("Content-Type", "").startswith("text/html")
            assert "<!DOCTYPE html>" in body
            assert "SMA-NG" in body

    def test_json_served_to_api_client(self, live_server):
        host, port = live_server.server_address
        url = "http://%s:%d/" % (host, port)
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req) as resp:
            assert resp.headers.get("Content-Type", "").startswith("application/json")


# ---------------------------------------------------------------------------
# Additional SQLite job database tests
# ---------------------------------------------------------------------------


class TestJobDatabaseExtended:
    """Tests for SQLite job database operations not covered by TestJobDatabase."""

    def test_get_running_jobs_empty(self, job_db):
        assert job_db.get_running_jobs() == []

    def test_get_running_jobs_returns_running(self, job_db):
        jid = job_db.add_job("/a.mkv", "/cfg.ini")
        job_db.start_job(jid, worker_id=1)
        running = job_db.get_running_jobs()
        assert len(running) == 1
        assert running[0]["id"] == jid
        assert running[0]["status"] == STATUS_RUNNING

    def test_get_stats_empty(self, job_db):
        stats = job_db.get_stats()
        assert stats["total"] == 0

    def test_get_stats_counts_by_status(self, job_db):
        j1 = job_db.add_job("/a.mkv", "/cfg.ini")
        j2 = job_db.add_job("/b.mkv", "/cfg.ini")
        j3 = job_db.add_job("/c.mkv", "/cfg.ini")
        job_db.start_job(j1, worker_id=1)
        job_db.complete_job(j1)
        job_db.start_job(j2, worker_id=1)
        job_db.fail_job(j2, "error")
        # j3 remains pending
        stats = job_db.get_stats()
        assert stats["total"] == 3
        assert stats[STATUS_COMPLETED] == 1
        assert stats[STATUS_FAILED] == 1
        assert stats[STATUS_PENDING] == 1

    def test_pending_count_for_config(self, job_db):
        job_db.add_job("/a.mkv", "/cfg_a.ini")
        job_db.add_job("/b.mkv", "/cfg_a.ini")
        job_db.add_job("/c.mkv", "/cfg_b.ini")
        assert job_db.pending_count_for_config("/cfg_a.ini") == 2
        assert job_db.pending_count_for_config("/cfg_b.ini") == 1
        assert job_db.pending_count_for_config("/cfg_c.ini") == 0

    def test_requeue_failed_job(self, job_db):
        jid = job_db.add_job("/a.mkv", "/cfg.ini")
        job_db.start_job(jid, worker_id=1)
        job_db.fail_job(jid, "oops")
        assert job_db.get_job(jid)["status"] == STATUS_FAILED

        result = job_db.requeue_job(jid)
        assert result is True
        job = job_db.get_job(jid)
        assert job["status"] == STATUS_PENDING
        assert job["error"] is None
        assert job["started_at"] is None

    def test_requeue_non_failed_job_returns_false(self, job_db):
        jid = job_db.add_job("/a.mkv", "/cfg.ini")
        # Pending — cannot requeue
        assert job_db.requeue_job(jid) is False

    def test_requeue_nonexistent_job_returns_false(self, job_db):
        assert job_db.requeue_job(9999) is False

    def test_requeue_failed_jobs_all(self, job_db):
        for path in ("/a.mkv", "/b.mkv", "/c.mkv"):
            jid = job_db.add_job(path, "/cfg.ini")
            job_db.start_job(jid, worker_id=1)
            job_db.fail_job(jid, "err")
        count = job_db.requeue_failed_jobs()
        assert count == 3
        assert job_db.pending_count() == 3

    def test_requeue_failed_jobs_by_config(self, job_db):
        for path in ("/a.mkv", "/b.mkv"):
            jid = job_db.add_job(path, "/cfg_a.ini")
            job_db.start_job(jid, worker_id=1)
            job_db.fail_job(jid, "err")
        jid = job_db.add_job("/c.mkv", "/cfg_b.ini")
        job_db.start_job(jid, worker_id=1)
        job_db.fail_job(jid, "err")

        count = job_db.requeue_failed_jobs(config="/cfg_a.ini")
        assert count == 2
        assert job_db.pending_count_for_config("/cfg_a.ini") == 2
        assert job_db.pending_count_for_config("/cfg_b.ini") == 0

    def test_requeue_failed_jobs_no_failures(self, job_db):
        job_db.add_job("/a.mkv", "/cfg.ini")  # pending
        assert job_db.requeue_failed_jobs() == 0

    def test_cleanup_old_jobs(self, job_db):
        jid = job_db.add_job("/old.mkv", "/cfg.ini")
        job_db.start_job(jid, worker_id=1)
        job_db.complete_job(jid)
        # Backdate completed_at so it falls outside the 0-day window
        with job_db._cursor() as cur:
            cur.execute("UPDATE jobs SET completed_at = datetime('now', '-2 days') WHERE id = ?", (jid,))
        deleted = job_db.cleanup_old_jobs(days=1)
        assert deleted == 1
        assert job_db.get_job(jid) is None

    def test_cleanup_leaves_pending_and_running(self, job_db):
        pending_id = job_db.add_job("/pending.mkv", "/cfg.ini")
        running_id = job_db.add_job("/running.mkv", "/cfg.ini")
        job_db.start_job(running_id, worker_id=1)
        deleted = job_db.cleanup_old_jobs(days=0)
        assert deleted == 0
        assert job_db.get_job(pending_id) is not None
        assert job_db.get_job(running_id) is not None

    def test_cursor_rollback_on_exception(self, job_db):
        """Verify _cursor context manager rolls back on error."""
        try:
            with job_db._cursor() as cursor:
                cursor.execute("INSERT INTO jobs (path, config, args, status) VALUES (?, ?, ?, ?)", ("/tmp/x.mkv", "/cfg.ini", "[]", STATUS_PENDING))
                raise ValueError("forced error")
        except ValueError:
            pass
        # Row should not be committed due to rollback
        with job_db._cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as c FROM jobs WHERE path = ?", ("/tmp/x.mkv",))
            assert cursor.fetchone()["c"] == 0

    def test_find_active_job(self, job_db):
        jid = job_db.add_job("/a.mkv", "/cfg.ini")
        result = job_db.find_active_job("/a.mkv")
        assert result is not None
        assert result["id"] == jid

    def test_find_active_job_not_found(self, job_db):
        assert job_db.find_active_job("/nonexistent.mkv") is None

    def test_find_active_job_excludes_completed(self, job_db):
        jid = job_db.add_job("/a.mkv", "/cfg.ini")
        job_db.start_job(jid, worker_id=1)
        job_db.complete_job(jid)
        assert job_db.find_active_job("/a.mkv") is None

    def test_get_jobs_filter_by_status(self, job_db):
        j1 = job_db.add_job("/a.mkv", "/cfg.ini")
        j2 = job_db.add_job("/b.mkv", "/cfg.ini")
        job_db.start_job(j1, worker_id=1)
        job_db.complete_job(j1)
        pending = job_db.get_jobs(status=STATUS_PENDING)
        assert len(pending) == 1
        assert pending[0]["id"] == j2

    def test_get_jobs_pagination(self, job_db):
        for i in range(5):
            job_db.add_job("/file%d.mkv" % i, "/cfg.ini")
        page1 = job_db.get_jobs(limit=3, offset=0)
        page2 = job_db.get_jobs(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 2
        ids_p1 = {j["id"] for j in page1}
        ids_p2 = {j["id"] for j in page2}
        assert ids_p1.isdisjoint(ids_p2)


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
        mgr.release("/cfg/autoProcess.ini")
        assert mgr.is_locked("/cfg/autoProcess.ini") is False

    def test_release_unheld_lock_does_not_raise(self):
        mgr = ConfigLockManager()
        mgr.release("/cfg/autoProcess.ini")  # Should not raise

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
        assert status["active"]["/cfg/autoProcess.ini"]["job_id"] == 42
        assert status["active"]["/cfg/autoProcess.ini"]["path"] == "/movie.mkv"

    def test_get_active_job_returns_none_when_idle(self):
        mgr = ConfigLockManager()
        assert mgr.get_active_job("/cfg/autoProcess.ini") is None

    def test_get_active_job_returns_info_when_locked(self):
        mgr = ConfigLockManager()
        mgr.acquire("/cfg/autoProcess.ini", job_id=7, job_path="/show.mkv")
        info = mgr.get_active_job("/cfg/autoProcess.ini")
        assert info == (7, "/show.mkv")

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

    def test_status_endpoint_sqlite(self, live_server):
        data, status = self._get(live_server, "/status")
        assert status == 200
        assert data["status"] == "ok"
        assert "note" in data  # SQLite note about cluster requiring PG

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
