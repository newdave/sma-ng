"""Job retention (cleanup_old_jobs), explicit /jobs columns and the /jobs limit cap."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from resources.daemon.constants import STATUS_COMPLETED
from resources.daemon.db import SQLiteJobDatabase
from tests.test_daemon import _make_db_with_mock_pool
from tests.test_handler import _get_response_body, _make_handler, _make_server


def _db(tmp_path):
  return SQLiteJobDatabase("sqlite:///%s" % (tmp_path / "jobs.db"))


def _finish(db, path, status, age_seconds):
  jid = db.add_job(path, "/c.yml")
  db.start_job(jid, worker_id=1)
  stamp = (datetime.now(UTC) - timedelta(seconds=age_seconds)).replace(microsecond=0).isoformat()
  with db._conn() as conn:
    conn.execute("UPDATE jobs SET status = ?, completed_at = ? WHERE id = ?", (status, stamp, jid))
  return jid


class TestSQLiteRetention:
  def test_cancelled_rows_are_pruned(self, tmp_path):
    db = _db(tmp_path)
    old = _finish(db, "/a.mkv", "cancelled", 40 * 86400)
    fresh = _finish(db, "/b.mkv", "cancelled", 3600)
    assert db.cleanup_old_jobs(days=30) == 1
    assert db.get_job(old) is None
    assert db.get_job(fresh) is not None

  def test_completed_inside_dedupe_window_survives_zero_day_cleanup(self, tmp_path):
    db = _db(tmp_path)
    young = _finish(db, "/y.mkv", STATUS_COMPLETED, 600)
    old = _finish(db, "/o.mkv", STATUS_COMPLETED, 7200)
    assert db.cleanup_old_jobs(days=0, protect_seconds=3600) == 1
    assert db.get_job(young) is not None
    assert db.get_job(old) is None

  def test_scanned_files_are_never_pruned(self, tmp_path):
    db = _db(tmp_path)
    with db._conn() as conn:
      conn.execute("INSERT INTO scanned_files (path, scanned_at) VALUES ('/old', datetime('now', '-40 days'))")
      conn.execute("INSERT INTO scanned_files (path, scanned_at) VALUES ('/new', datetime('now', '-1 days'))")
    db.cleanup_old_jobs(days=30)
    with db._conn() as conn:
      paths = sorted(r["path"] for r in conn.execute("SELECT path FROM scanned_files").fetchall())
    assert paths == ["/new", "/old"]

  def test_get_jobs_omits_blob_columns(self, tmp_path):
    db = _db(tmp_path)
    jid = db.add_job("/a.mkv", "/c.yml")
    db.update_job_ffmpeg_stderr(jid, "x" * 1000)
    row = db.get_jobs()[0]
    assert "ffmpeg_stderr" not in row and "output" not in row and "output_dropped" not in row
    assert row["id"] == jid and row["path"] == "/a.mkv" and row["kind"] == "convert"
    assert "request_profile" in row and "retry_count" in row
    assert "ffmpeg_stderr" in db.get_job(jid)


class TestPostgresRetention:
  def test_cleanup_includes_cancelled_protects_window_and_keeps_scanned(self):
    cur = MagicMock()
    cur.rowcount = 2
    db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
    assert db.cleanup_old_jobs(days=0, protect_seconds=3600) == 2
    calls = cur.execute.call_args_list
    job_sql, job_params = calls[0][0]
    assert "DELETE FROM jobs" in job_sql
    assert job_params == ("completed", "failed", "cancelled", 3600)
    assert "make_interval(secs" in job_sql
    assert len(calls) == 1
    assert not any("scanned_files" in c[0][0] for c in calls)

  def test_get_jobs_selects_explicit_columns(self):
    cur = MagicMock()
    cur.fetchall.return_value = []
    db, _, _, _ = _make_db_with_mock_pool(mock_cursor=cur)
    db.get_jobs()
    sql = cur.execute.call_args[0][0]
    assert "SELECT *" not in sql
    columns = [c.strip() for c in sql.split(" FROM ")[0][len("SELECT ") :].split(",")]
    assert "ffmpeg_stderr" not in columns and "output" not in columns and "output_dropped" not in columns
    assert "request_profile" in columns


@pytest.fixture
def server():
  return _make_server(api_key="k")


def _get(server, path):
  h = _make_handler("GET", path, headers={"X-API-Key": "k"}, server=server)
  h.do_GET()
  return h


class TestJobsEndpoint:
  def test_limit_is_capped(self, server):
    h = _get(server, "/jobs?limit=100000")
    assert _get_response_body(h)["limit"] == 500
    assert server.job_db.get_jobs.call_args.kwargs["limit"] == 500

  def test_search_limit_is_capped(self, server):
    h = _get(server, "/jobs?search=x&limit=100000")
    assert _get_response_body(h)["limit"] == 500

  def test_stderr_blob_not_returned(self, server):
    server.job_db.get_jobs.return_value = [{"id": 1, "path": "/a", "config": "", "ffmpeg_stderr": "big"}]
    body = _get_response_body(_get(server, "/jobs"))
    assert "ffmpeg_stderr" not in body["jobs"][0]

  def test_cleanup_protects_dedupe_window(self, server):
    server.path_config_manager.job_dedupe_window_seconds = 21600
    server.job_db.cleanup_old_jobs.return_value = 0
    h = _make_handler("POST", "/cleanup?days=0", headers={"X-API-Key": "k", "Content-Length": "0"}, server=server)
    h.do_POST()
    server.job_db.cleanup_old_jobs.assert_called_once_with(0, protect_seconds=21600)
