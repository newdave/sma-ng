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

  def test_profile_cap_skips_pending_when_running_count_reached(self, tmp_path):
    db = _db(tmp_path)
    # Two hq jobs queued; cap=1.
    a = db.add_job("/m/4k/a.mkv", "/cfg.yml", [], request_profile="hq")
    b = db.add_job("/m/4k/b.mkv", "/cfg.yml", [], request_profile="hq")
    # First claim runs hq, leaving the second pending.
    first = db.claim_next_job(worker_id=1, node_id="node-a", profile_caps={"hq": 1})
    assert first is not None
    assert first["id"] == a
    # Second claim from a different worker must be blocked by the cap.
    second = db.claim_next_job(worker_id=2, node_id="node-a", profile_caps={"hq": 1})
    assert second is None
    # An rq job sneaks through even while hq is at cap.
    rq_id = db.add_job("/m/rq.mkv", "/cfg.yml", [], request_profile="rq")
    third = db.claim_next_job(worker_id=2, node_id="node-a", profile_caps={"hq": 1})
    assert third is not None
    assert third["id"] == rq_id
    # When the running hq finishes, the second hq is claimable.
    db.complete_job(a)
    fourth = db.claim_next_job(worker_id=3, node_id="node-a", profile_caps={"hq": 1})
    assert fourth is not None
    assert fourth["id"] == b
    db.close()

  def test_no_profile_caps_disables_gating(self, tmp_path):
    db = _db(tmp_path)
    a = db.add_job("/m/a.mkv", "/cfg.yml", [], request_profile="hq")
    b = db.add_job("/m/b.mkv", "/cfg.yml", [], request_profile="hq")
    assert db.claim_next_job(worker_id=1, node_id="node-a")["id"] == a
    # No caps passed → second claim succeeds (default unlimited).
    assert db.claim_next_job(worker_id=2, node_id="node-a")["id"] == b
    db.close()

  def test_profile_cap_is_serialised_within_single_writer(self, tmp_path):
    """SQLite serialises writers via the connection lock; even with the
    same caps dict passed twice, the second call sees the first claim."""
    db = _db(tmp_path)
    a = db.add_job("/m/a.mkv", "/cfg.yml", [], request_profile="hq")
    b = db.add_job("/m/b.mkv", "/cfg.yml", [], request_profile="hq")
    caps = {"hq": 1}
    first = db.claim_next_job(worker_id=1, node_id="n", profile_caps=caps)
    second = db.claim_next_job(worker_id=2, node_id="n", profile_caps=caps)
    assert first["id"] == a
    assert second is None
    db.close()

  def test_legacy_request_profile_null_is_backfilled_from_args(self, tmp_path):
    """Jobs queued before request_profile existed must still respect the cap.
    The backfill in _init_db parses --profile from args and updates the column."""
    db = _db(tmp_path)
    # Simulate a legacy row: insert directly with NULL request_profile.
    with db._conn() as conn:
      conn.execute(
        "INSERT INTO jobs (path, config, args, status, max_retries) VALUES (?, ?, ?, ?, ?)",
        ("/m/legacy.mkv", "/cfg.yml", '["--profile", "hq"]', "pending", 0),
      )
    db.close()
    # Reopen — _init_db backfills.
    db = _db(tmp_path)
    row = db.get_next_pending_job()
    assert row["request_profile"] == "hq"
    db.close()

  def test_cap_counts_running_jobs_via_args_when_column_null(self, tmp_path):
    """Even before backfill runs (within a single session), the cap must
    correctly count running jobs whose request_profile is NULL but whose
    args carry --profile."""
    from resources.daemon.db import _profile_from_args, _profiles_at_cap

    db = _db(tmp_path)
    # Manually create a running row with NULL request_profile.
    with db._conn() as conn:
      conn.execute(
        "INSERT INTO jobs (path, config, args, status, worker_id) VALUES (?, ?, ?, ?, ?)",
        ("/m/running.mkv", "/cfg.yml", '["--profile", "hq"]', "running", 1),
      )
    # Helper sanity.
    assert _profile_from_args('["--profile", "hq"]') == "hq"
    # _profiles_at_cap must see the running hq via args parsing.
    with db._conn() as conn:
      over = _profiles_at_cap(conn, {"hq": 1}, is_sqlite=True)
    assert over == {"hq"}
    db.close()

  def test_requeue_failed_job_backfills_request_profile(self, tmp_path):
    """A failed legacy job (request_profile=NULL) must get its column
    populated when requeued, so the next claim's cap query sees it."""
    db = _db(tmp_path)
    with db._conn() as conn:
      conn.execute(
        "INSERT INTO jobs (path, config, args, status) VALUES (?, ?, ?, ?)",
        ("/m/old.mkv", "/cfg.yml", '["--profile", "hq"]', "failed"),
      )
      job_id = conn.execute("SELECT id FROM jobs WHERE path = ?", ("/m/old.mkv",)).fetchone()["id"]
    # Wipe the column so we can prove requeue heals it.
    with db._conn() as conn:
      conn.execute("UPDATE jobs SET request_profile = NULL WHERE id = ?", (job_id,))
    assert db.requeue_job(job_id) is True
    row = db.get_job(job_id)
    assert row["request_profile"] == "hq"
    assert row["status"] == "pending"
    db.close()

  def test_fail_job_retry_branch_backfills_request_profile(self, tmp_path):
    """The retry path inside fail_job also leaves the row pending; the
    cap depends on request_profile being populated before the next claim."""
    db = _db(tmp_path)
    job_id = db.add_job("/m/retry.mkv", "/cfg.yml", ["--profile", "hq"], max_retries=2)
    # Clear request_profile to simulate the legacy state.
    with db._conn() as conn:
      conn.execute("UPDATE jobs SET request_profile = NULL WHERE id = ?", (job_id,))
      conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job_id,))
    db.fail_job(job_id, "transient")
    row = db.get_job(job_id)
    assert row["status"] == "pending"
    assert row["request_profile"] == "hq"
    db.close()

  def test_requeue_failed_jobs_bulk_backfills(self, tmp_path):
    db = _db(tmp_path)
    with db._conn() as conn:
      for path in ("/m/a.mkv", "/m/b.mkv"):
        conn.execute(
          "INSERT INTO jobs (path, config, args, status, request_profile) VALUES (?, ?, ?, ?, ?)",
          (path, "/cfg.yml", '["--profile", "hq"]', "failed", None),
        )
    assert db.requeue_failed_jobs() == 2
    rows = db.get_pending_jobs()
    assert all(r["request_profile"] == "hq" for r in rows)
    db.close()

  def test_budget_blocks_second_claim_when_cost_exhausts(self, tmp_path):
    """One hq costs 6; budget 6 → second hq blocked (budget over), and
    so is any other profile because its cost would also push over."""
    db = _db(tmp_path)
    a = db.add_job("/m/hq-a.mkv", "/cfg.yml", ["--profile", "hq"], request_profile="hq")
    db.add_job("/m/hq-b.mkv", "/cfg.yml", ["--profile", "hq"], request_profile="hq")
    db.add_job("/m/rq.mkv", "/cfg.yml", ["--profile", "rq"], request_profile="rq")
    costs = {"hq": 6, "rq": 2, "lq": 1}
    budget = 6
    first = db.claim_next_job(worker_id=1, node_id="n", profile_costs=costs, concurrency_budget=budget)
    assert first["id"] == a
    second = db.claim_next_job(worker_id=2, node_id="n", profile_costs=costs, concurrency_budget=budget)
    assert second is None
    db.close()

  def test_budget_allows_mixed_within_ceiling(self, tmp_path):
    """1 rq (cost 2) leaves 4 of a 6-budget — 4 lq (cost 1 each) all fit."""
    db = _db(tmp_path)
    rq = db.add_job("/m/rq.mkv", "/cfg.yml", ["--profile", "rq"], request_profile="rq")
    lqs = [db.add_job(f"/m/lq-{i}.mkv", "/cfg.yml", ["--profile", "lq"], request_profile="lq") for i in range(5)]
    costs = {"hq": 6, "rq": 2, "lq": 1}
    budget = 6
    claimed = []
    for w in range(1, 6):
      job = db.claim_next_job(worker_id=w, node_id="n", profile_costs=costs, concurrency_budget=budget)
      if job is None:
        break
      claimed.append(job["id"])
    assert claimed[0] == rq  # rq is queued first
    # 5 claims fit: rq(2) + 4*lq(1) = 6. 6th lq blocked.
    assert len(claimed) == 5
    assert lqs[4] not in claimed
    db.close()

  def test_budget_default_unset_is_byte_identical(self, tmp_path):
    """With no profile_costs / budget passed, two hq jobs both claim
    (matches today's pre-budget behaviour)."""
    db = _db(tmp_path)
    a = db.add_job("/m/a.mkv", "/cfg.yml", ["--profile", "hq"], request_profile="hq")
    b = db.add_job("/m/b.mkv", "/cfg.yml", ["--profile", "hq"], request_profile="hq")
    assert db.claim_next_job(worker_id=1, node_id="n")["id"] == a
    assert db.claim_next_job(worker_id=2, node_id="n")["id"] == b
    db.close()

  def test_budget_counts_legacy_null_request_profile_via_args(self, tmp_path):
    """A running job with request_profile NULL but args=['--profile','hq']
    still counts toward the cost sum — same heuristic as the cap path."""
    from resources.daemon.db import _budget_exhausted_profiles

    db = _db(tmp_path)
    with db._conn() as conn:
      conn.execute(
        "INSERT INTO jobs (path, config, args, status, worker_id) VALUES (?, ?, ?, ?, ?)",
        ("/m/legacy-hq.mkv", "/cfg.yml", '["--profile", "hq"]', "running", 1),
      )
    costs = {"hq": 6, "rq": 2, "lq": 1}
    with db._conn() as conn:
      over = _budget_exhausted_profiles(conn, costs, 6, is_sqlite=True)
    # 6 already running → every profile (cost ≥ 1) exceeds remaining 0.
    assert over == {"hq", "rq", "lq"}
    db.close()

  def test_budget_zero_disables_gating(self, tmp_path):
    """budget=None / 0 must be a no-op (returns empty set)."""
    from resources.daemon.db import _budget_exhausted_profiles

    db = _db(tmp_path)
    with db._conn() as conn:
      assert _budget_exhausted_profiles(conn, {"hq": 6}, 0, is_sqlite=True) == set()
      assert _budget_exhausted_profiles(conn, {"hq": 6}, None, is_sqlite=True) == set()
      assert _budget_exhausted_profiles(conn, {}, 6, is_sqlite=True) == set()
    db.close()

  def test_cap_and_budget_compose_tightest_wins(self, tmp_path):
    """hq.max-concurrent=1 fires even when the budget would still allow
    another hq — both gates apply, the tightest wins."""
    db = _db(tmp_path)
    a = db.add_job("/m/a.mkv", "/cfg.yml", ["--profile", "hq"], request_profile="hq")
    db.add_job("/m/b.mkv", "/cfg.yml", ["--profile", "hq"], request_profile="hq")
    # Budget allows 2 hq (cost 6 each, budget 12). But cap is 1.
    caps = {"hq": 1}
    costs = {"hq": 6}
    first = db.claim_next_job(worker_id=1, node_id="n", profile_caps=caps, profile_costs=costs, concurrency_budget=12)
    assert first["id"] == a
    second = db.claim_next_job(worker_id=2, node_id="n", profile_caps=caps, profile_costs=costs, concurrency_budget=12)
    assert second is None  # max-concurrent fires before budget would
    db.close()
