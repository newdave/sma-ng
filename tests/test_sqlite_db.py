from resources.daemon.constants import STATUS_COMPLETED, STATUS_FAILED, STATUS_PENDING, STATUS_RUNNING, set_node_id_cache
from resources.daemon.db import SQLiteJobDatabase


def _db(tmp_path):
  return SQLiteJobDatabase(f"sqlite:///{tmp_path / 'sma-ng.db'}")


class TestSQLiteJobDatabase:
  def test_add_claim_complete_job_persists_to_file(self, tmp_path):
    db = _db(tmp_path)
    job_id = db.add_job("/mnt/media/movie.mkv", "/config/sma-ng.yml", ["--profile", "rq"])
    assert job_id == 1
    assert db.add_job("/mnt/media/movie.mkv", "/config/sma-ng.yml") is None

    job = db.claim_next_job(worker_id=1, node_id="node-a")
    assert job["id"] == job_id
    assert job["status"] == STATUS_RUNNING
    assert job["args"] == '["--profile", "rq"]'

    db.complete_job(job_id, input_size=100, output_size=60)
    assert db.get_job(job_id)["status"] == STATUS_COMPLETED
    db.close()

    reopened = _db(tmp_path)
    assert reopened.get_job(job_id)["status"] == STATUS_COMPLETED
    assert reopened.get_stats()["total"] == 1
    reopened.close()

  def test_failed_jobs_can_be_requeued_cancelled_and_deleted(self, tmp_path):
    db = _db(tmp_path)
    failed_id = db.add_job("/mnt/media/bad.mkv", "/config/sma-ng.yml")
    db.claim_next_job(worker_id=1, node_id="node-a")
    db.fail_job(failed_id, "boom")
    assert db.get_job(failed_id)["status"] == STATUS_FAILED

    assert db.requeue_job(failed_id) is True
    assert db.get_job(failed_id)["status"] == STATUS_PENDING
    assert db.cancel_job(failed_id) is True
    assert db.get_job(failed_id)["status"] == "cancelled"

    failed_id_2 = db.add_job("/mnt/media/bad2.mkv", "/config/sma-ng.yml")
    db.claim_next_job(worker_id=1, node_id="node-a")
    db.fail_job(failed_id_2, "boom")
    assert db.delete_failed_jobs() == 1
    assert db.get_job(failed_id_2) is None
    db.close()

  def test_scanner_state_filters_recorded_paths(self, tmp_path):
    db = _db(tmp_path)
    paths = ["/mnt/media/a.mkv", "/mnt/media/b.mkv"]
    assert db.filter_unscanned(paths) == paths
    db.record_scanned([paths[0]])
    assert db.filter_unscanned(paths) == [paths[1]]
    db.close()

  def test_jobs_table_has_ffmpeg_stderr_column(self, tmp_path):
    db = _db(tmp_path)
    with db._conn() as conn:
      cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "ffmpeg_stderr" in cols
    db.close()

  def test_migrates_existing_jobs_table_to_add_ffmpeg_stderr(self, tmp_path):
    """Pre-existing deployments have a jobs table without ffmpeg_stderr.
    Reopening with the current code must add the column idempotently."""
    import sqlite3

    db_path = tmp_path / "sma-ng.db"
    raw = sqlite3.connect(str(db_path))
    raw.execute(
      "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, "
      "config TEXT NOT NULL, args TEXT, status TEXT, worker_id INTEGER, node_id TEXT, "
      "error TEXT, created_at TEXT, started_at TEXT, completed_at TEXT)"
    )
    raw.commit()
    raw.close()

    db = SQLiteJobDatabase(f"sqlite:///{db_path}")
    with db._conn() as conn:
      cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "ffmpeg_stderr" in cols
    db.close()

  def test_update_job_ffmpeg_stderr_persists_and_truncates(self, tmp_path):
    from resources.daemon import db as db_mod

    db = _db(tmp_path)
    job_id = db.add_job("/mnt/media/movie.mkv", "/config/sma-ng.yml")
    db.update_job_ffmpeg_stderr(job_id, "first failure\nstderr line")
    assert db.get_job(job_id)["ffmpeg_stderr"] == "first failure\nstderr line"

    # Overwrites
    db.update_job_ffmpeg_stderr(job_id, "second")
    assert db.get_job(job_id)["ffmpeg_stderr"] == "second"

    # Truncates to last _FFMPEG_STDERR_MAX_BYTES, tail preserved
    big = ("x" * db_mod._FFMPEG_STDERR_MAX_BYTES) + "TAIL"
    db.update_job_ffmpeg_stderr(job_id, big)
    stored = db.get_job(job_id)["ffmpeg_stderr"]
    assert len(stored.encode("utf-8")) <= db_mod._FFMPEG_STDERR_MAX_BYTES
    assert stored.endswith("TAIL")

    # None is a no-op
    db.update_job_ffmpeg_stderr(job_id, None)
    assert db.get_job(job_id)["ffmpeg_stderr"] == stored
    db.close()

  def test_running_jobs_reset_on_reopen_for_same_node(self, tmp_path, monkeypatch):
    set_node_id_cache("node-a")
    db = _db(tmp_path)
    job_id = db.add_job("/mnt/media/movie.mkv", "/config/sma-ng.yml")
    db.claim_next_job(worker_id=1, node_id="node-a")
    db.close()

    reopened = _db(tmp_path)
    assert reopened.get_job(job_id)["status"] == STATUS_PENDING
    reopened.close()
    set_node_id_cache("")
