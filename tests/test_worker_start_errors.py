"""A DB error between claim and conversion start must release all worker state (SMANG-21)."""

import logging
from unittest import mock

import pytest

from resources.daemon import worker as worker_mod
from resources.daemon.config import ConfigLockManager
from resources.daemon.constants import STATUS_PENDING, STATUS_RUNNING
from resources.daemon.db import SQLiteJobDatabase
from resources.daemon.worker import ConversionWorker

from .test_db_job_kinds import _pg, _pg_db

LOG = logging.getLogger("test.worker.starterr")


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
  monkeypatch.setattr(worker_mod, "TERMINAL_RETRY_BACKOFF_SECONDS", (0, 0), raising=False)


def _worker(job_db):
  log_mgr = mock.MagicMock()
  log_mgr.get_logger.return_value = LOG
  log_mgr.get_log_file.return_value = "/tmp/test.log"
  return ConversionWorker(
    worker_id=1,
    job_db=job_db,
    path_config_manager=mock.MagicMock(),
    config_log_manager=log_mgr,
    config_lock_manager=ConfigLockManager(max_per_config=1),
    logger=LOG,
  )


def _fail_n(real, n):
  state = {"left": n}

  def wrapper(*a, **k):
    if state["left"] > 0:
      state["left"] -= 1
      raise RuntimeError("db down")
    return real(*a, **k)

  return wrapper


def _assert_clean(worker, config_file="/cfg.yml"):
  assert worker.current_job_id is None
  assert worker._claim_token is None
  # The config slot is free again (a leaked acquire would make this fail).
  sem = worker.config_lock_manager._get_sem(config_file)
  assert sem.acquire(blocking=False)
  sem.release()


class TestSQLite:
  def _setup(self, tmp_path):
    db = SQLiteJobDatabase("sqlite:///%s" % (tmp_path / "sma.db"))
    media = tmp_path / "m.mkv"
    media.write_bytes(b"")
    db.add_job(str(media), "/cfg.yml")
    worker = _worker(db)
    job = db.claim_next_job(worker.worker_id, worker.node_id)
    assert job["status"] == STATUS_RUNNING
    return db, worker, job

  def test_db_error_after_lock_acquire_requeues_and_frees_state(self, tmp_path):
    db, worker, job = self._setup(tmp_path)
    real = db.get_job
    calls = {"n": 0}

    def flaky(job_id):
      calls["n"] += 1
      if calls["n"] == 2:  # the post-lock cancel check
        raise RuntimeError("db down")
      return real(job_id)

    db.get_job = flaky
    worker._run_conversion = mock.MagicMock()
    worker.process_job(job)
    worker._run_conversion.assert_not_called()
    assert real(job["id"])["status"] == STATUS_PENDING
    _assert_clean(worker)

  def test_db_error_before_lock_requeues(self, tmp_path):
    db, worker, job = self._setup(tmp_path)
    real = db.get_job
    db.get_job = _fail_n(real, 1)
    worker.process_job(job)
    assert real(job["id"])["status"] == STATUS_PENDING
    _assert_clean(worker)

  def test_release_retries_through_transient_outage(self, tmp_path):
    db, worker, job = self._setup(tmp_path)
    real_get, real_requeue = db.get_job, db.requeue_interrupted_job
    db.get_job = _fail_n(real_get, 1)
    db.requeue_interrupted_job = _fail_n(real_requeue, 2)
    worker.process_job(job)
    assert real_get(job["id"])["status"] == STATUS_PENDING
    assert worker._pending_releases == {}
    _assert_clean(worker)

  def test_release_parked_when_db_keeps_failing_then_flushed(self, tmp_path, caplog):
    db, worker, job = self._setup(tmp_path)
    real_get, real_requeue = db.get_job, db.requeue_interrupted_job
    db.get_job = _fail_n(real_get, 1)
    db.requeue_interrupted_job = _fail_n(real_requeue, 100)
    with caplog.at_level(logging.INFO, logger="test.worker.starterr"):
      worker.process_job(job)
    _assert_clean(worker)
    assert set(worker._pending_releases) == {job["id"]}
    assert real_get(job["id"])["status"] == STATUS_RUNNING
    assert all("\n" not in r.getMessage() for r in caplog.records)
    # DB recovers: the next loop pass releases the row.
    db.requeue_interrupted_job = real_requeue
    worker._flush_pending_release()
    assert worker._pending_releases == {}
    assert real_get(job["id"])["status"] == STATUS_PENDING

  def test_flush_keeps_pending_while_db_still_failing(self, tmp_path):
    db, worker, job = self._setup(tmp_path)
    db.get_job = _fail_n(db.get_job, 1)
    db.requeue_interrupted_job = _fail_n(db.requeue_interrupted_job, 100)
    worker.process_job(job)
    worker._flush_pending_release()
    assert set(worker._pending_releases) == {job["id"]}

  def test_stale_owner_release_is_discarded(self, tmp_path):
    db, worker, job = self._setup(tmp_path)
    real_get = db.get_job
    db.get_job = _fail_n(real_get, 1)
    # Another claim took the row before the release landed: the owner guard must skip it.
    with db._conn() as conn:
      conn.execute("UPDATE jobs SET claim_token = 'someone-else' WHERE id = ?", (job["id"],))
    worker.process_job(job)
    assert real_get(job["id"])["status"] == STATUS_RUNNING


class TestPostgresFakeCursor:
  def _media(self, tmp_path):
    media = tmp_path / "m.mkv"
    media.write_bytes(b"")
    return {"id": 9, "path": str(media), "config": "/cfg.yml", "args": None, "claim_token": "tok"}

  def test_db_error_before_start_requeues_via_owner_guarded_update(self, tmp_path):
    cur, modules = _pg()
    cur.rowcount = 1
    db = _pg_db(cur, modules)
    state = {"fails": 1}

    def execute(sql, *a, **k):
      if sql.lstrip().upper().startswith("SELECT") and state["fails"]:
        state["fails"] -= 1
        raise RuntimeError("connection lost")

    cur.execute = mock.MagicMock(side_effect=execute)
    worker = _worker(db)
    worker._run_conversion = mock.MagicMock()
    worker.process_job(self._media(tmp_path))
    worker._run_conversion.assert_not_called()
    updates = [c for c in cur.execute.call_args_list if c.args and "UPDATE jobs" in c.args[0] and "status = %s" in c.args[0]]
    assert updates, "no release UPDATE issued"
    sql, params = updates[-1].args
    assert "claim_token" in sql
    assert params[0] == STATUS_PENDING and "tok" in params
    _assert_clean(worker)

  def test_pg_release_failure_parks_and_flushes(self, tmp_path):
    cur, modules = _pg()
    cur.rowcount = 1
    db = _pg_db(cur, modules)
    state = {"down": True}

    def execute(sql, *a, **k):
      if state["down"]:
        raise RuntimeError("connection lost")

    cur.execute = mock.MagicMock(side_effect=execute)
    worker = _worker(db)
    worker.process_job(self._media(tmp_path))
    assert set(worker._pending_releases) == {9}
    _assert_clean(worker)
    state["down"] = False
    worker._flush_pending_release()
    assert worker._pending_releases == {}


class TestPoisonJobCap:
  """A pre-start error that always repeats must not requeue forever (review finding 1)."""

  def test_job_whose_start_always_errors_is_failed_after_three_requeues(self, tmp_path):
    db = SQLiteJobDatabase("sqlite:///%s" % (tmp_path / "sma.db"))
    media = tmp_path / "m.mkv"
    media.write_bytes(b"")
    job_id = db.add_job(str(media), "/cfg.yml")
    worker = _worker(db)
    worker.config_lock_manager.acquire = mock.MagicMock(side_effect=RuntimeError("lock backend down"))
    worker._run_conversion = mock.MagicMock()
    for _ in range(worker_mod.MAX_PRE_START_REQUEUES):
      job = db.claim_next_job(worker.worker_id, worker.node_id)
      assert job is not None and job["id"] == job_id
      worker.process_job(job)
      assert db.get_job(job_id)["status"] == STATUS_PENDING
    job = db.claim_next_job(worker.worker_id, worker.node_id)
    worker.process_job(job)
    row = db.get_job(job_id)
    assert row["status"] == "failed"
    assert "%d times in a row" % (worker_mod.MAX_PRE_START_REQUEUES + 1) in row["error"]
    assert "\n" not in row["error"]
    assert db.claim_next_job(worker.worker_id, worker.node_id) is None
    worker._run_conversion.assert_not_called()
    assert job_id not in worker._pre_start_failures

  def test_successful_start_resets_the_count(self, tmp_path):
    db = SQLiteJobDatabase("sqlite:///%s" % (tmp_path / "sma.db"))
    media = tmp_path / "m.mkv"
    media.write_bytes(b"")
    job_id = db.add_job(str(media), "/cfg.yml")
    worker = _worker(db)
    worker._pre_start_failures[job_id] = 2
    worker._run_conversion = mock.MagicMock(side_effect=RuntimeError("boom after start"))
    worker.process_job(db.claim_next_job(worker.worker_id, worker.node_id))
    assert job_id not in worker._pre_start_failures

  def test_pool_workers_share_the_count(self):
    pool = mock.MagicMock()
    pool._pre_start_failures = {}
    a = ConversionWorker(1, mock.MagicMock(), mock.MagicMock(), mock.MagicMock(), ConfigLockManager(), LOG, pool=pool)
    b = ConversionWorker(2, mock.MagicMock(), mock.MagicMock(), mock.MagicMock(), ConfigLockManager(), LOG, pool=pool)
    assert a._pre_start_failures is b._pre_start_failures is pool._pre_start_failures


class TestParkedWrites:
  """Parked terminal writes are per job and flushed even while paused (review finding 2)."""

  def test_two_parked_writes_are_both_kept_and_flushed(self, tmp_path):
    db = SQLiteJobDatabase("sqlite:///%s" % (tmp_path / "sma.db"))
    worker = _worker(db)
    ids = []
    for name in ("a.mkv", "b.mkv"):
      media = tmp_path / name
      media.write_bytes(b"")
      ids.append(db.add_job(str(media), "/cfg.yml"))
    real_get, real_requeue = db.get_job, db.requeue_interrupted_job
    db.requeue_interrupted_job = _fail_n(real_requeue, 1000)
    for _ in ids:
      job = db.claim_next_job(worker.worker_id, worker.node_id)
      db.get_job = _fail_n(real_get, 1)
      worker.process_job(job)
      db.get_job = real_get
    assert set(worker._pending_releases) == set(ids)
    assert worker.held_job_ids() == set(ids)
    db.requeue_interrupted_job = real_requeue
    worker._flush_pending_release()
    assert worker._pending_releases == {}
    assert [real_get(i)["status"] for i in ids] == [STATUS_PENDING, STATUS_PENDING]

  def test_flush_runs_while_paused(self):
    import threading

    pool = mock.MagicMock()
    pool._pre_start_failures = {}
    pool._pause_mode = threading.Event()
    pool._pause_mode.set()
    pool._drain_mode = threading.Event()
    db = mock.MagicMock()
    db.is_distributed = False
    worker = ConversionWorker(1, db, mock.MagicMock(), mock.MagicMock(), ConfigLockManager(), LOG, pool=pool)
    write = mock.MagicMock(return_value=True)
    worker._pending_releases[7] = ("requeue", write)
    worker.job_event.set()
    worker._run_once()
    write.assert_called_once()
    assert worker._pending_releases == {}
    db.claim_next_job.assert_not_called()


class TestMinorFixes:
  def test_lock_release_error_still_decrements_in_flight_and_clears_state(self, tmp_path):
    import contextlib

    db = SQLiteJobDatabase("sqlite:///%s" % (tmp_path / "sma.db"))
    media = tmp_path / "m.mkv"
    media.write_bytes(b"")
    db.add_job(str(media), "/cfg.yml")
    worker = _worker(db)
    worker._run_conversion = mock.MagicMock(side_effect=RuntimeError("boom"))
    worker.config_lock_manager.release = mock.MagicMock(side_effect=RuntimeError("release failed"))
    gauge = mock.MagicMock()
    with mock.patch.object(worker_mod.metrics_prom, "in_flight_counter", return_value=gauge), contextlib.suppress(RuntimeError):
      worker.process_job(db.claim_next_job(worker.worker_id, worker.node_id))
    gauge.dec.assert_called_once()
    assert worker.current_job_id is None
    assert worker._claim_token is None

  def test_retries_keep_a_bounded_backoff_after_stop(self, monkeypatch):
    monkeypatch.setattr(worker_mod, "TERMINAL_RETRY_BACKOFF_SECONDS", (5.0, 5.0))
    worker = _worker(mock.MagicMock())
    worker.stop()
    sleeps = []
    monkeypatch.setattr(worker_mod.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError):
      worker._retry_terminal("requeue", mock.MagicMock(side_effect=RuntimeError("db down")), 1)
    assert len(sleeps) == 2
    assert all(0 < s <= worker_mod.SHUTDOWN_RETRY_BACKOFF_CAP_SECONDS for s in sleeps)

  def test_pre_start_requeue_is_not_counted_as_cancelled(self, tmp_path):
    db = SQLiteJobDatabase("sqlite:///%s" % (tmp_path / "sma.db"))
    media = tmp_path / "m.mkv"
    media.write_bytes(b"")
    db.add_job(str(media), "/cfg.yml")
    worker = _worker(db)
    job = db.claim_next_job(worker.worker_id, worker.node_id)
    db.get_job = _fail_n(db.get_job, 1)
    with mock.patch.object(worker_mod.metrics_prom, "record_job_terminal") as rec:
      worker.process_job(job)
    assert not [c for c in rec.call_args_list if c.args and c.args[0] == "cancelled"]


def _hb(job_db, held, node_id="node-a"):
  from resources.daemon.threads import HeartbeatThread

  server = mock.MagicMock()
  server.worker_pool.held_job_ids.return_value = set(held)
  return HeartbeatThread(job_db=job_db, node_id=node_id, host="h", worker_count=2, server=server, interval=5, stale_seconds=60, logger=LOG, started_at=None), server


class TestOrphanSweepSQLite:
  """Running rows on this node with no live local worker are requeued after a grace (review finding 2)."""

  def _row(self, db, tmp_path, name, node_id, age_seconds, claim="tok"):
    from datetime import UTC, datetime, timedelta

    media = tmp_path / name
    media.write_bytes(b"")
    job_id = db.add_job(str(media), "/cfg.yml")
    started = (datetime.now(UTC) - timedelta(seconds=age_seconds)).replace(microsecond=0).isoformat()
    with db._conn() as conn:
      conn.execute(
        "UPDATE jobs SET status = 'running', node_id = ?, worker_id = 1, started_at = ?, claim_token = ? WHERE id = ?",
        (node_id, started, claim, job_id),
      )
    return job_id

  def test_sweep_requeues_only_orphaned_local_rows_past_grace(self, tmp_path, caplog):
    from resources.daemon.threads import ORPHAN_SWEEP_GRACE_SECONDS

    db = SQLiteJobDatabase("sqlite:///%s" % (tmp_path / "sma.db"))
    orphan = self._row(db, tmp_path, "orphan.mkv", "node-a", ORPHAN_SWEEP_GRACE_SECONDS + 60)
    fresh = self._row(db, tmp_path, "fresh.mkv", "node-a", 10)
    held = self._row(db, tmp_path, "held.mkv", "node-a", ORPHAN_SWEEP_GRACE_SECONDS + 600)
    other = self._row(db, tmp_path, "other.mkv", "node-b", ORPHAN_SWEEP_GRACE_SECONDS + 600)
    ht, server = _hb(db, {held})
    with caplog.at_level(logging.INFO, logger="test.worker.starterr"):
      assert ht.sweep_orphaned_jobs() == 1
    assert db.get_job(orphan)["status"] == STATUS_PENDING
    assert db.get_job(orphan)["claim_token"] is None
    for job_id in (fresh, held, other):
      assert db.get_job(job_id)["status"] == STATUS_RUNNING
    assert db.get_job(other)["node_id"] == "node-b"
    swept = [r.getMessage() for r in caplog.records if "orphan" in r.getMessage().lower()]
    assert len(swept) == 1 and "\n" not in swept[0] and str(orphan) in swept[0]
    server.notify_workers.assert_called_once()

  def test_sweep_skips_row_reclaimed_after_select(self, tmp_path):
    from resources.daemon.threads import ORPHAN_SWEEP_GRACE_SECONDS

    db = SQLiteJobDatabase("sqlite:///%s" % (tmp_path / "sma.db"))
    job_id = self._row(db, tmp_path, "orphan.mkv", "node-a", ORPHAN_SWEEP_GRACE_SECONDS + 60, claim="old")
    real = db.find_orphaned_running_jobs

    def find_then_reclaim(*a, **k):
      rows = real(*a, **k)
      with db._conn() as conn:
        conn.execute("UPDATE jobs SET claim_token = 'new' WHERE id = ?", (job_id,))
      return rows

    db.find_orphaned_running_jobs = find_then_reclaim
    ht, _server = _hb(db, set())
    assert ht.sweep_orphaned_jobs() == 0
    assert db.get_job(job_id)["status"] == STATUS_RUNNING

  def test_sqlite_heartbeat_loop_runs_the_sweep(self, tmp_path):
    db = SQLiteJobDatabase("sqlite:///%s" % (tmp_path / "sma.db"))
    ht, _server = _hb(db, set())
    ht.sweep_orphaned_jobs = mock.MagicMock(return_value=0)

    def wait(timeout=None):
      ht.running = False
      return True

    ht._stop_event.wait = wait
    ht.run()
    ht.sweep_orphaned_jobs.assert_called_once()


class TestOrphanSweepPostgres:
  def test_find_orphaned_is_scoped_to_this_node_live_workers_and_grace(self):
    cur, modules = _pg(fetchall=[{"id": 5, "claim_token": "t5"}])
    db = _pg_db(cur, modules)
    rows = db.find_orphaned_running_jobs("node-a", {3, 4}, 120)
    assert rows == [{"id": 5, "claim_token": "t5"}]
    sql, params = cur.execute.call_args.args
    assert "status = %s" in sql and "node_id = %s" in sql
    assert "started_at < NOW() - make_interval(secs => %s)" in sql
    assert "id != ALL(%s)" in sql
    assert params[0] == STATUS_RUNNING and params[1] == "node-a" and 120 in params and sorted(params[3]) == [3, 4]

  def test_sweep_requeues_with_node_and_token_guard(self):
    cur, modules = _pg(fetchall=[{"id": 5, "claim_token": "t5"}])
    cur.rowcount = 1
    db = _pg_db(cur, modules)
    ht, server = _hb(db, {3})
    assert ht.sweep_orphaned_jobs() == 1
    updates = [c for c in cur.execute.call_args_list if c.args and "UPDATE jobs" in c.args[0]]
    sql, params = updates[0].args
    assert "claim_token = NULL" in sql and "node_id = %s" in sql and "claim_token = %s" in sql
    assert params[0] == STATUS_PENDING and 5 in params and "node-a" in params and "t5" in params
    server.notify_workers.assert_called_once()
