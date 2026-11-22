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
    assert worker._pending_release is None
    _assert_clean(worker)

  def test_release_parked_when_db_keeps_failing_then_flushed(self, tmp_path, caplog):
    db, worker, job = self._setup(tmp_path)
    real_get, real_requeue = db.get_job, db.requeue_interrupted_job
    db.get_job = _fail_n(real_get, 1)
    db.requeue_interrupted_job = _fail_n(real_requeue, 100)
    with caplog.at_level(logging.INFO, logger="test.worker.starterr"):
      worker.process_job(job)
    _assert_clean(worker)
    assert worker._pending_release is not None
    assert real_get(job["id"])["status"] == STATUS_RUNNING
    assert all("\n" not in r.getMessage() for r in caplog.records)
    # DB recovers: the next loop pass releases the row.
    db.requeue_interrupted_job = real_requeue
    worker._flush_pending_release()
    assert worker._pending_release is None
    assert real_get(job["id"])["status"] == STATUS_PENDING

  def test_flush_keeps_pending_while_db_still_failing(self, tmp_path):
    db, worker, job = self._setup(tmp_path)
    db.get_job = _fail_n(db.get_job, 1)
    db.requeue_interrupted_job = _fail_n(db.requeue_interrupted_job, 100)
    worker.process_job(job)
    worker._flush_pending_release()
    assert worker._pending_release is not None

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
    assert worker._pending_release is not None
    _assert_clean(worker)
    state["down"] = False
    worker._flush_pending_release()
    assert worker._pending_release is None
