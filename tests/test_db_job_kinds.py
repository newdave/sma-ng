"""Job kinds, captured output and node capabilities in both DB backends.

SQLite runs for real; PostgreSQL is checked against the SQL it executes
(the repo's mocked-pool pattern), since CI has no database.
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from resources.daemon import db as dbmod
from resources.daemon.constants import STATUS_PENDING
from resources.daemon.db import _JOB_KIND_COLUMNS, _METRICS_EXPANSION_COLUMNS, PostgreSQLJobDatabase, SQLiteJobDatabase


def _db(tmp_path):
  return SQLiteJobDatabase(f"sqlite:///{tmp_path / 'sma-ng.db'}")


def _cols(path, table):
  with sqlite3.connect(str(path)) as raw:
    return {row[1] for row in raw.execute("PRAGMA table_info(%s)" % table)}


class TestSQLiteMigration:
  def test_legacy_db_gains_columns_once(self, tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(str(path)) as raw:
      raw.execute(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, config TEXT NOT NULL, args TEXT DEFAULT '[]', status TEXT DEFAULT 'pending', worker_id INTEGER, node_id TEXT, error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, started_at TEXT, completed_at TEXT, retry_count INTEGER DEFAULT 0, max_retries INTEGER DEFAULT 0, next_attempt_at TEXT, priority INTEGER DEFAULT 0)"
      )
      raw.execute(
        "CREATE TABLE cluster_nodes (node_id TEXT PRIMARY KEY, host TEXT NOT NULL, workers INTEGER NOT NULL DEFAULT 0, last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL DEFAULT 'online', running_jobs INTEGER NOT NULL DEFAULT 0, pending_jobs INTEGER NOT NULL DEFAULT 0, version TEXT, hwaccel TEXT, node_name TEXT)"
      )
      raw.execute("INSERT INTO jobs (path, config) VALUES ('/old.mkv', '/c.yml')")
    for _ in range(2):
      SQLiteJobDatabase(f"sqlite:///{path}").close()
    assert {"kind", "output", "output_dropped"} <= _cols(path, "jobs")
    assert "capabilities" in _cols(path, "cluster_nodes")
    db = SQLiteJobDatabase(f"sqlite:///{path}")
    job = db.get_job(1)
    assert job["kind"] == "convert" and job["output_dropped"] == 0

  def test_metrics_tuple_untouched(self):
    assert not {name for name, _s, _p in _JOB_KIND_COLUMNS} & {name for name, _s, _p in _METRICS_EXPANSION_COLUMNS}


class TestSQLiteKinds:
  def test_same_path_two_kinds_and_duplicates(self, tmp_path):
    db = _db(tmp_path)
    convert = db.add_job("/m/a.mkv", "/c.yml")
    repair = db.add_job("/m/a.mkv", "/c.yml", ["-n"], kind="repair")
    assert convert and repair and convert != repair
    assert db.add_job("/m/a.mkv", "/c.yml", kind="repair") is None
    assert db.add_job("/m/a.mkv", "/c.yml") is None
    assert db.get_job(repair)["kind"] == "repair"
    assert db.find_active_job("/m/a.mkv")["id"] == convert
    assert db.find_active_job("/m/a.mkv", kind="repair")["id"] == repair
    assert db.find_active_job("/m/a.mkv", kind="audit") is None

  def test_priority_stored(self, tmp_path):
    db = _db(tmp_path)
    job = db.add_job("/m/a.mkv", "/c.yml", kind="preview", priority=100)
    assert db.get_job(job)["priority"] == 100

  def test_claim_filters_kinds(self, tmp_path):
    db = _db(tmp_path)
    db.add_job("/m/a.mkv", "/c.yml", kind="repair")
    assert db.claim_next_job(1, "n", kinds=["convert"]) is None
    claimed = db.claim_next_job(1, "n", kinds=["convert", "repair"])
    assert claimed["kind"] == "repair"

  def test_claim_without_kinds_takes_any(self, tmp_path):
    db = _db(tmp_path)
    db.add_job("/m/a.mkv", "/c.yml", kind="audit")
    assert db.claim_next_job(1, "n")["kind"] == "audit"


class TestOutputTail:
  def test_append_and_read(self, tmp_path):
    db = _db(tmp_path)
    job = db.add_job("/m/a.mkv", "/c.yml", kind="repair")
    assert db.get_job_output(job) == ("", 0)
    db.append_job_output(job, "line 1\n")
    db.append_job_output(job, "")
    db.append_job_output(job, "line 2 é\n")
    assert db.get_job_output(job) == ("line 1\nline 2 é\n", 0)
    assert db.get_job_output(9999) is None
    db.append_job_output(9999, "ignored")

  def test_tail_kept_and_dropped_counted(self, tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "_JOB_OUTPUT_MAX_BYTES", 10)
    db = _db(tmp_path)
    job = db.add_job("/m/a.mkv", "/c.yml", kind="repair")
    db.append_job_output(job, "0123456789")
    db.append_job_output(job, "abcd")
    assert db.get_job_output(job) == ("456789abcd", 4)

  def test_tail_cut_on_character_boundary(self, monkeypatch):
    monkeypatch.setattr(dbmod, "_JOB_OUTPUT_MAX_BYTES", 5)
    kept, dropped = dbmod._append_output_tail("", "aé€b", 0)  # 1 + 2 + 3 + 1 bytes
    assert kept == "€b"
    assert dropped == 3
    assert len(kept.encode("utf-8")) + dropped == 7

  def test_cleanup_purges_output_with_row(self, tmp_path):
    db = _db(tmp_path)
    job = db.add_job("/m/a.mkv", "/c.yml", kind="repair")
    db.append_job_output(job, "x")
    db.claim_next_job(1, "n")
    db.complete_job(job)
    with db._conn() as conn:
      conn.execute("UPDATE jobs SET completed_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (job,))
    assert db.cleanup_old_jobs(days=1) == 1
    assert db.get_job_output(job) is None


class TestSQLiteCapabilities:
  def test_heartbeat_stores_capabilities_and_keeps_them(self, tmp_path):
    db = _db(tmp_path)
    db.heartbeat("node-a", "host", 2, "2026-01-01T00:00:00", capabilities="job-kinds-v1")
    db.heartbeat("node-a", "host", 2, "2026-01-01T00:00:00")
    with db._conn() as conn:
      row = conn.execute("SELECT capabilities FROM cluster_nodes WHERE node_id = 'node-a'").fetchone()
    assert row["capabilities"] == "job-kinds-v1"
    assert db.nodes_missing_capability("job-kinds-v1") == []


def _pg(fetchone=None, fetchall=None):
  cur = MagicMock()
  cur.fetchone.return_value = fetchone
  cur.fetchall.return_value = fetchall or []
  cur.rowcount = 0
  conn = MagicMock()
  conn.cursor.return_value.__enter__ = lambda _s: cur
  conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
  psycopg2 = MagicMock()
  pool = MagicMock()
  psycopg2.pool.ThreadedConnectionPool.return_value = pool
  psycopg2.extras.RealDictCursor = object()
  pool.getconn.return_value = conn
  modules = {"psycopg2": psycopg2, "psycopg2.pool": psycopg2.pool, "psycopg2.extras": psycopg2.extras}
  return cur, modules


def _sql(cur):
  return [call.args[0] for call in cur.execute.call_args_list if call.args]


def _pg_db(cur, modules):
  with patch.dict("sys.modules", modules):
    with patch.object(PostgreSQLJobDatabase, "_init_db"), patch.object(PostgreSQLJobDatabase, "_reset_running_jobs"):
      return PostgreSQLJobDatabase("postgresql://mock/test")


class TestPostgreSQL:
  def test_init_adds_columns_if_not_exists(self):
    cur, modules = _pg()
    with patch.dict("sys.modules", modules), patch.object(PostgreSQLJobDatabase, "_reset_running_jobs"):
      PostgreSQLJobDatabase("postgresql://mock/test")
    statements = _sql(cur)
    for name, _s, pg_type in _JOB_KIND_COLUMNS:
      assert any("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS %s %s" % (name, pg_type) in s for s in statements), name
    assert any("ALTER TABLE cluster_nodes ADD COLUMN IF NOT EXISTS capabilities TEXT" in s for s in statements)

  def test_add_job_dedupes_per_kind_and_inserts_kind(self):
    cur, modules = _pg(fetchone={"id": 5})
    db = _pg_db(cur, modules)
    cur.fetchone.side_effect = [None, {"id": 6}]
    assert db.add_job("/m/a.mkv", "/c.yml", ["-n"], kind="repair", priority=100) == 6
    select, insert = [c for c in cur.execute.call_args_list if c.args and "jobs" in c.args[0]][:2]
    assert "COALESCE(kind, 'convert') = %s" in select.args[0] and select.args[1][1] == "repair"
    assert "kind, priority" in insert.args[0] and insert.args[1][-2:] == ("repair", 100)

  def test_add_job_duplicate_returns_none(self):
    cur, modules = _pg()
    db = _pg_db(cur, modules)
    cur.fetchone.return_value = {"id": 3}
    assert db.add_job("/m/a.mkv", "/c.yml") is None

  def test_find_active_job_filters_kind(self):
    cur, modules = _pg()
    db = _pg_db(cur, modules)
    db.find_active_job("/m/a.mkv", kind="audit")
    call = cur.execute.call_args_list[-1]
    assert "COALESCE(kind, 'convert') = %s" in call.args[0] and call.args[1][1] == "audit"

  def test_claim_filters_kinds(self):
    cur, modules = _pg()
    db = _pg_db(cur, modules)
    assert db.claim_next_job(1, "n", kinds=["convert", "repair"]) is None
    select = next(c for c in cur.execute.call_args_list if c.args and "FOR UPDATE SKIP LOCKED" in c.args[0])
    assert "COALESCE(kind, 'convert') = ANY(%s)" in select.args[0]
    assert ["convert", "repair"] in list(select.args[1])

  def test_append_and_get_output(self):
    cur, modules = _pg()
    db = _pg_db(cur, modules)
    cur.fetchone.return_value = {"output": "a", "output_dropped": 2}
    db.append_job_output(4, "b")
    update = cur.execute.call_args_list[-1]
    assert update.args[0].startswith("UPDATE jobs SET output") and update.args[1] == ("ab", 2, 4)
    assert db.get_job_output(4) == ("a", 2)
    cur.fetchone.return_value = None
    assert db.get_job_output(4) is None
    db.append_job_output(4, "c")
    db.append_job_output(4, "")

  def test_nodes_missing_capability(self):
    cur, modules = _pg(fetchall=[{"name": "sma-slave0"}])
    db = _pg_db(cur, modules)
    assert db.nodes_missing_capability("job-kinds-v1", online_within_seconds=90) == ["sma-slave0"]
    call = cur.execute.call_args_list[-1]
    assert "string_to_array" in call.args[0] and call.args[1] == (90, "job-kinds-v1")

  def test_heartbeat_passes_capabilities(self):
    cur, modules = _pg()
    db = _pg_db(cur, modules)
    db.heartbeat("n", "h", 1, "2026-01-01", capabilities="job-kinds-v1")
    call = cur.execute.call_args_list[-1]
    assert "capabilities" in call.args[0] and call.args[1][-1] == "job-kinds-v1"


@pytest.mark.parametrize("status", [STATUS_PENDING])
def test_kind_default_for_old_callers(tmp_path, status):
  db = _db(tmp_path)
  job = db.add_job("/m/a.mkv", "/c.yml")
  assert db.get_job(job)["kind"] == "convert" and db.get_job(job)["status"] == status
