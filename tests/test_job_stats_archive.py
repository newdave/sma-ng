"""Lifetime job totals survive job deletion (job_stats_archive, SMANG-45)."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from resources.daemon.db import SQLiteJobDatabase
from tests.test_daemon import _make_db_with_mock_pool
from tests.test_handler import _get_response_body, _make_handler, _make_server

# ---------------------------------------------------------------------------
# SQLite: real database
# ---------------------------------------------------------------------------


def _db(tmp_path):
  return SQLiteJobDatabase("sqlite:///%s" % (tmp_path / "jobs.db"))


def _job(db, path, status, age_seconds=40 * 86400):
  jid = db.add_job(path, "/c.yml")
  stamp = (datetime.now(UTC) - timedelta(seconds=age_seconds)).replace(microsecond=0).isoformat()
  with db._conn() as conn:
    conn.execute("UPDATE jobs SET status = ?, completed_at = ? WHERE id = ?", (status, stamp, jid))
  return jid


def _seed_mix(db):
  """Two completed, one failed, one cancelled, one pending, one running."""
  ids = {
    "completed": [_job(db, "/c1.mkv", "completed"), _job(db, "/c2.mkv", "completed")],
    "failed": [_job(db, "/f1.mkv", "failed")],
    "cancelled": [_job(db, "/x1.mkv", "cancelled")],
    "pending": [_job(db, "/p1.mkv", "pending")],
    "running": [_job(db, "/r1.mkv", "running")],
  }
  return ids


def _archive(db):
  with db._conn() as conn:
    return {r["status"]: r["count"] for r in conn.execute("SELECT status, count FROM job_stats_archive").fetchall()}


class TestSQLiteArchiveSchema:
  def test_table_created_and_init_is_idempotent(self, tmp_path):
    db = _db(tmp_path)
    db.add_archived_counts({"completed": 3})
    db.close()
    reopened = _db(tmp_path)  # second _init_db on an existing database
    assert _archive(reopened) == {"completed": 3}
    reopened.close()


class TestSQLiteDeletionPreservesTotals:
  def test_cleanup_old_jobs_preserves_totals(self, tmp_path):
    db = _db(tmp_path)
    _seed_mix(db)
    before = db.get_stats()
    assert db.cleanup_old_jobs(days=30) == 4
    after = db.get_stats()
    assert after == before
    assert _archive(db) == {"completed": 2, "failed": 1, "cancelled": 1}

  def test_delete_failed_jobs_preserves_totals(self, tmp_path):
    db = _db(tmp_path)
    _seed_mix(db)
    before = db.get_stats()
    assert db.delete_failed_jobs() == 1
    assert db.get_stats() == before
    assert _archive(db) == {"failed": 1}

  def test_delete_jobs_by_id_archives_finished_only(self, tmp_path):
    db = _db(tmp_path)
    ids = _seed_mix(db)
    before = db.get_stats()
    targets = ids["completed"][:1] + ids["pending"] + ids["running"]
    assert sorted(db.delete_jobs(targets + [999])) == sorted(targets)
    after = db.get_stats()
    assert _archive(db) == {"completed": 1}
    assert after["completed"] == before["completed"]
    assert after.get("pending", 0) == 0 and after.get("running", 0) == 0
    assert after["total"] == before["total"] - 2

  def test_delete_all_jobs_drops_pending_and_running(self, tmp_path):
    db = _db(tmp_path)
    _seed_mix(db)
    before = db.get_stats()
    assert db.delete_all_jobs() == 6
    after = db.get_stats()
    assert _archive(db) == {"completed": 2, "failed": 1, "cancelled": 1}
    assert after == {"completed": 2, "failed": 1, "cancelled": 1, "total": before["total"] - 2}

  def test_repeated_deletions_accumulate(self, tmp_path):
    db = _db(tmp_path)
    _job(db, "/a.mkv", "completed")
    db.delete_all_jobs()
    _job(db, "/b.mkv", "completed")
    db.delete_all_jobs()
    assert _archive(db) == {"completed": 2}


class TestSQLiteGetStats:
  def test_get_stats_adds_archive_to_live_counts(self, tmp_path):
    db = _db(tmp_path)
    _job(db, "/a.mkv", "completed")
    _job(db, "/p.mkv", "pending")
    db.add_archived_counts({"completed": 19000, "failed": 40, "cancelled": 0})
    stats = db.get_stats()
    assert stats["completed"] == 19001
    assert stats["failed"] == 40
    assert stats["pending"] == 1
    assert stats["total"] == 19042

  def test_add_archived_counts_accumulates(self, tmp_path):
    db = _db(tmp_path)
    db.add_archived_counts({"completed": 5})
    db.add_archived_counts({"completed": 2, "failed": 1})
    assert _archive(db) == {"completed": 7, "failed": 1}


# ---------------------------------------------------------------------------
# PostgreSQL: fake cursor
# ---------------------------------------------------------------------------


def _pg_sql(cur):
  return [c.args[0] for c in cur.execute.call_args_list if c.args]


def _assert_archiving_delete(sql):
  """DELETE and archive upsert run as one statement, so they share a transaction."""
  assert "DELETE FROM jobs" in sql
  assert "INSERT INTO job_stats_archive" in sql
  assert "ON CONFLICT (status) DO UPDATE" in sql
  assert "'completed', 'failed', 'cancelled'" in sql


class TestPostgresArchive:
  def test_init_db_creates_archive_table(self):
    from unittest.mock import patch

    from resources.daemon.db import PostgreSQLJobDatabase

    psycopg2_mock = MagicMock()
    pool_mock = MagicMock()
    psycopg2_mock.pool.ThreadedConnectionPool.return_value = pool_mock
    psycopg2_mock.extras.RealDictCursor = object()
    cur = MagicMock()
    cur.fetchone.return_value = None
    cur.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda _s: cur
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    pool_mock.getconn.return_value = conn
    modules = {"psycopg2": psycopg2_mock, "psycopg2.pool": psycopg2_mock.pool, "psycopg2.extras": psycopg2_mock.extras}
    with patch.dict("sys.modules", modules), patch.object(PostgreSQLJobDatabase, "_reset_running_jobs"):
      PostgreSQLJobDatabase("postgresql://mock/test")
    create = [s for s in _pg_sql(cur) if "job_stats_archive" in s]
    assert len(create) == 1
    assert "CREATE TABLE IF NOT EXISTS job_stats_archive" in create[0]
    assert "BIGINT NOT NULL DEFAULT 0" in create[0]

  def test_cleanup_archives_in_same_statement(self):
    db, _, _, cur = _make_db_with_mock_pool()
    cur.rowcount = 3
    assert db.cleanup_old_jobs(days=30) == 3
    (sql,) = _pg_sql(cur)
    _assert_archiving_delete(sql)
    assert "completed_at < NOW()" in sql

  def test_delete_failed_archives_in_same_statement(self):
    db, _, _, cur = _make_db_with_mock_pool()
    cur.rowcount = 4
    assert db.delete_failed_jobs() == 4
    (sql,) = _pg_sql(cur)
    _assert_archiving_delete(sql)

  def test_delete_jobs_archives_in_same_statement(self):
    db, _, _, cur = _make_db_with_mock_pool()
    cur.fetchall.return_value = [{"id": 1}, {"id": 2}]
    assert db.delete_jobs([1, 2, 9]) == [1, 2]
    (sql,) = _pg_sql(cur)
    _assert_archiving_delete(sql)
    assert "id = ANY(%s)" in sql

  def test_delete_all_archives_finished_only(self):
    db, _, _, cur = _make_db_with_mock_pool()
    cur.rowcount = 10
    assert db.delete_all_jobs() == 10
    (sql,) = _pg_sql(cur)
    _assert_archiving_delete(sql)
    # pending/running rows are deleted but filtered out of the archive insert
    insert = sql.split("INSERT INTO job_stats_archive", 1)[1]
    assert "WHERE status IN ('completed', 'failed', 'cancelled')" in insert

  def test_get_stats_reads_archive(self):
    db, _, _, cur = _make_db_with_mock_pool()
    cur.fetchall.return_value = [{"status": "completed", "count": 19005}]
    cur.fetchone.return_value = {"total": 19005}
    stats = db.get_stats()
    assert stats == {"completed": 19005, "total": 19005}
    assert all("job_stats_archive" in s for s in _pg_sql(cur))

  def test_add_archived_counts_upserts(self):
    db, _, _, cur = _make_db_with_mock_pool()
    db.add_archived_counts({"completed": 19000, "failed": 3})
    sql = _pg_sql(cur)
    assert len(sql) == 2
    assert all("ON CONFLICT (status) DO UPDATE" in s for s in sql)
    params = [c.args[1] for c in cur.execute.call_args_list]
    assert params == [("completed", 19000), ("failed", 3)]


def _metrics_cursor(cur, wdef):
  snap = {"pending": 1, "running": 1, "total": 12, "archived_completed": 19000, "archived_failed": 50, "archived_cancelled": 2}
  kpi = {
    "completed": 9,
    "failed": 1,
    "cancelled": 0,
    "failure_rate_pct": 10.0,
    "avg_duration_seconds": None,
    "p95_duration_seconds": None,
    "avg_compression_pct": None,
    "bytes_saved_total": 0,
    "bytes_grown_total": 0,
    "minutes_transcoded_total": 0,
  }
  cur.fetchone.side_effect = [snap, kpi]
  cur.fetchall.side_effect = [[], [], [], []] if wdef else [[], [], []]


class TestPostgresMetricsArchive:
  def test_window_all_includes_archive(self):
    db, _, _, cur = _make_db_with_mock_pool()
    _metrics_cursor(cur, wdef=False)
    kpis = db.get_metrics(window="all")["kpis"]
    assert kpis["completed"] == 19009
    assert kpis["failed"] == 51
    assert kpis["cancelled"] == 2
    assert kpis["total"] == 12 + 19052
    assert kpis["failure_rate_pct"] == round(51 / 19060 * 100, 2)
    assert "job_stats_archive" in _pg_sql(cur)[0]

  def test_window_24h_stays_live_only(self):
    db, _, _, cur = _make_db_with_mock_pool()
    _metrics_cursor(cur, wdef=True)
    kpis = db.get_metrics(window="24h")["kpis"]
    assert kpis["completed"] == 9
    assert kpis["failed"] == 1
    assert kpis["cancelled"] == 0
    assert kpis["total"] == 12
    assert kpis["failure_rate_pct"] == 10.0


# ---------------------------------------------------------------------------
# POST /stats/archive
# ---------------------------------------------------------------------------


def _post_archive(payload, api_key="secret", headers=None, server=None):
  body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
  hdrs = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
  if headers is not None:
    hdrs.update(headers)
  h = _make_handler(method="POST", path="/stats/archive", body=body, headers=hdrs, api_key=api_key, server=server)
  h.do_POST()
  return h


class TestSeedEndpoint:
  def test_adds_counts_to_real_db(self, tmp_path):
    server = _make_server(api_key="secret")
    server.job_db = _db(tmp_path)
    h = _post_archive({"completed": 19000, "failed": 40, "cancelled": 3}, headers={"X-API-Key": "secret"}, server=server)
    assert h._response_code == 200
    body = _get_response_body(h)
    assert body["added"] == {"completed": 19000, "failed": 40, "cancelled": 3}
    assert body["stats"]["completed"] == 19000
    assert body["stats"]["total"] == 19043
    assert _archive(server.job_db) == {"completed": 19000, "failed": 40, "cancelled": 3}

  def test_requires_api_key(self):
    h = _post_archive({"completed": 1})
    assert h._response_code == 401
    h.server.job_db.add_archived_counts.assert_not_called()

  @pytest.mark.parametrize(
    "payload",
    [
      {"completed": -1},
      {"completed": 1.5},
      {"completed": "5"},
      {"completed": True},
      {"pending": 3},
      {},
      [1, 2],
      b"not json",
    ],
  )
  def test_rejects_bad_input(self, payload):
    h = _post_archive(payload, headers={"X-API-Key": "secret"})
    assert h._response_code == 400
    h.server.job_db.add_archived_counts.assert_not_called()

  def test_api_key_never_logged(self):
    server = _make_server(api_key="secret")
    server.job_db.add_archived_counts.return_value = {"completed": 2}
    h = _post_archive({"completed": 2}, headers={"X-API-Key": "secret"}, server=server)
    assert h._response_code == 200
    server.job_db.add_archived_counts.assert_called_once_with({"completed": 2})
    logged = " ".join(str(c) for m in (h.server.logger.info, h.server.logger.warning) for c in m.call_args_list)
    assert "secret" not in logged
