from resources.daemon.constants import STATUS_COMPLETED, STATUS_FAILED, STATUS_PENDING, STATUS_RUNNING, set_node_id_cache
from resources.daemon.db import _METRICS_EXPANSION_COLUMNS, SQLiteJobDatabase


def _db(tmp_path):
  return SQLiteJobDatabase(f"sqlite:///{tmp_path / 'sma-ng.db'}")


class TestSQLiteJobDatabase:
  def test_add_claim_complete_job_persists_to_file(self, tmp_path):
    db = _db(tmp_path)
    job_id = db.add_job("/mnt/unionfs/Media/movie.mkv", "/config/sma-ng.yml", ["--profile", "rq"])
    assert job_id == 1
    assert db.add_job("/mnt/unionfs/Media/movie.mkv", "/config/sma-ng.yml") is None

    job = db.claim_next_job(worker_id=1, node_id="node-a")
    assert job is not None
    assert job["id"] == job_id
    assert job["status"] == STATUS_RUNNING
    assert job["args"] == '["--profile", "rq"]'

    db.complete_job(job_id, input_size=100, output_size=60)
    _row = db.get_job(job_id)
    assert _row is not None
    assert _row["status"] == STATUS_COMPLETED
    db.close()

    reopened = _db(tmp_path)
    _row = reopened.get_job(job_id)
    assert _row is not None
    assert _row["status"] == STATUS_COMPLETED
    assert reopened.get_stats()["total"] == 1
    reopened.close()

  def test_failed_jobs_can_be_requeued_cancelled_and_deleted(self, tmp_path):
    db = _db(tmp_path)
    failed_id = db.add_job("/mnt/unionfs/Media/bad.mkv", "/config/sma-ng.yml")
    db.claim_next_job(worker_id=1, node_id="node-a")
    db.fail_job(failed_id, "boom")
    _row = db.get_job(failed_id)
    assert _row is not None
    assert _row["status"] == STATUS_FAILED

    assert db.requeue_job(failed_id) is True
    _row = db.get_job(failed_id)
    assert _row is not None
    assert _row["status"] == STATUS_PENDING
    assert db.cancel_job(failed_id) is True
    _row = db.get_job(failed_id)
    assert _row is not None
    assert _row["status"] == "cancelled"

    failed_id_2 = db.add_job("/mnt/unionfs/Media/bad2.mkv", "/config/sma-ng.yml")
    db.claim_next_job(worker_id=1, node_id="node-a")
    db.fail_job(failed_id_2, "boom")
    assert db.delete_failed_jobs() == 1
    assert db.get_job(failed_id_2) is None
    db.close()

  def test_scanner_state_filters_recorded_paths(self, tmp_path):
    db = _db(tmp_path)
    paths = ["/mnt/unionfs/Media/a.mkv", "/mnt/unionfs/Media/b.mkv"]
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
    job_id = db.add_job("/mnt/unionfs/Media/movie.mkv", "/config/sma-ng.yml")
    assert job_id is not None
    db.update_job_ffmpeg_stderr(job_id, "first failure\nstderr line")
    _row = db.get_job(job_id)
    assert _row is not None
    assert _row["ffmpeg_stderr"] == "first failure\nstderr line"

    # Overwrites
    db.update_job_ffmpeg_stderr(job_id, "second")
    _row = db.get_job(job_id)
    assert _row is not None
    assert _row["ffmpeg_stderr"] == "second"

    # Truncates to last _FFMPEG_STDERR_MAX_BYTES, tail preserved
    big = ("x" * db_mod._FFMPEG_STDERR_MAX_BYTES) + "TAIL"
    db.update_job_ffmpeg_stderr(job_id, big)
    _row = db.get_job(job_id)
    assert _row is not None
    stored = _row["ffmpeg_stderr"]
    assert len(stored.encode("utf-8")) <= db_mod._FFMPEG_STDERR_MAX_BYTES
    assert stored.endswith("TAIL")

    # None is a no-op
    db.update_job_ffmpeg_stderr(job_id, None)
    _row = db.get_job(job_id)
    assert _row is not None
    assert _row["ffmpeg_stderr"] == stored
    db.close()

  def test_metrics_expansion_columns_present_on_fresh_db(self, tmp_path):
    """Every column declared in _METRICS_EXPANSION_COLUMNS exists on a freshly created jobs table."""
    db = _db(tmp_path)
    expected = {name for name, _, _ in _METRICS_EXPANSION_COLUMNS}
    with db._conn() as conn:
      actual = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    missing = expected - actual
    assert not missing, "metrics-expansion columns missing on fresh DB: %r" % missing
    db.close()

  def test_metrics_expansion_columns_added_idempotently_on_upgrade(self, tmp_path):
    """Old database lacking the new columns gets them via the upgrade migrator without data loss."""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    # Simulate an old-shape jobs table: pre-metrics-expansion columns only.
    with sqlite3.connect(str(db_path)) as raw:
      raw.execute("""
        CREATE TABLE jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL,
          config TEXT NOT NULL,
          args TEXT DEFAULT '[]',
          status TEXT DEFAULT 'pending',
          worker_id INTEGER,
          node_id TEXT,
          error TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          started_at TEXT,
          completed_at TEXT,
          retry_count INTEGER DEFAULT 0,
          max_retries INTEGER DEFAULT 0,
          next_attempt_at TEXT,
          priority INTEGER DEFAULT 0,
          input_size_bytes INTEGER,
          output_size_bytes INTEGER
        )
      """)
      raw.execute(
        "INSERT INTO jobs (path, config, status) VALUES (?, ?, ?)",
        ("/legacy.mkv", "/cfg.yml", STATUS_COMPLETED),
      )
      raw.commit()

    db = SQLiteJobDatabase(f"sqlite:///{db_path}")
    expected = {name for name, _, _ in _METRICS_EXPANSION_COLUMNS}
    with db._conn() as conn:
      actual = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
      assert expected.issubset(actual)
      # Pre-existing row survives the migration.
      row = conn.execute("SELECT path, status, encoder_backend FROM jobs WHERE id=1").fetchone()
    assert row is not None
    assert row["path"] == "/legacy.mkv"
    assert row["status"] == STATUS_COMPLETED
    assert row["encoder_backend"] is None
    db.close()

  def test_add_job_persists_request_source_and_profile(self, tmp_path):
    db = _db(tmp_path)
    job_id = db.add_job(
      "/mnt/Media/show.mkv",
      "/cfg.yml",
      [],
      request_source="sonarr",
      request_profile="1080p",
    )
    row = db.get_job(job_id)
    assert row is not None
    assert row["request_source"] == "sonarr"
    assert row["request_profile"] == "1080p"
    db.close()

  def test_add_job_request_attribution_defaults_to_none(self, tmp_path):
    db = _db(tmp_path)
    job_id = db.add_job("/mnt/Media/other.mkv", "/cfg.yml", [])
    row = db.get_job(job_id)
    assert row is not None
    assert row["request_source"] is None
    assert row["request_profile"] is None
    db.close()

  def test_running_jobs_reset_on_reopen_for_same_node(self, tmp_path, monkeypatch):
    set_node_id_cache("node-a")
    db = _db(tmp_path)
    job_id = db.add_job("/mnt/unionfs/Media/movie.mkv", "/config/sma-ng.yml")
    db.claim_next_job(worker_id=1, node_id="node-a")
    db.close()

    reopened = _db(tmp_path)
    _row = reopened.get_job(job_id)
    assert _row is not None
    assert _row["status"] == STATUS_PENDING
    reopened.close()
    set_node_id_cache("")
