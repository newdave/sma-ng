"""Reliability tests for resources/daemon/worker.py: watchdog timeout, cross-node
cancel, guarded completion, loop resilience, pause handling and respawn."""

import logging
import os
import sys
import threading
import time
import unittest.mock as mock

import pytest

from resources.daemon import worker as worker_mod
from resources.daemon.worker import (
  ABORT_CANCELLED,
  ABORT_REASSIGNED,
  ABORT_SHUTDOWN,
  ABORT_TIMEOUT,
  ConversionWorker,
  WorkerPool,
  _JobWatchdog,
  kill_process_tree,
)

posix_only = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")


def _make_worker(job_db=None, pool=None):
  if job_db is None:
    job_db = mock.MagicMock()
  log_mgr = mock.MagicMock()
  log_mgr.get_logger.return_value = logging.getLogger("test.worker.reliability")
  log_mgr.get_log_file.return_value = "/tmp/test.log"
  return ConversionWorker(
    worker_id=1,
    job_db=job_db,
    path_config_manager=mock.MagicMock(),
    config_log_manager=log_mgr,
    config_lock_manager=mock.MagicMock(),
    logger=logging.getLogger("test.worker.reliability"),
    pool=pool,
  )


def _write_script(tmp_path, body):
  script = tmp_path / "fake_manual.py"
  script.write_text(body)
  return str(script)


# A manual.py stand-in that exits immediately but leaves a grandchild holding
# the stdout pipe open — exactly the hung-ffmpeg shape that used to block the
# worker forever.
_ORPHAN_HOLDS_PIPE = "import subprocess, sys\nprint('started', flush=True)\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\nsys.exit(3)\n"

_HUNG_PARENT = "import time\nprint('started', flush=True)\ntime.sleep(120)\n"


@posix_only
class TestWatchdogTimeout:
  def test_orphaned_child_holding_pipe_is_killed_at_timeout(self, tmp_path):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"")
    worker = _make_worker()
    worker.job_timeout_seconds = 1
    worker.script_path = _write_script(tmp_path, _ORPHAN_HOLDS_PIPE)
    started = time.monotonic()
    success, _out, _dur = worker._run_conversion_inner(11, str(media), "/cfg.ini", [])
    assert success is False
    assert worker._abort_reason == ABORT_TIMEOUT
    assert time.monotonic() - started < 20
    assert 11 not in worker._job_processes

  def test_timeout_logs_single_line_message(self, tmp_path, caplog):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"")
    worker = _make_worker()
    worker.job_timeout_seconds = 1
    worker.script_path = _write_script(tmp_path, _HUNG_PARENT)
    with caplog.at_level(logging.ERROR, logger="test.worker.reliability"):
      worker._run_conversion_inner(12, str(media), "/cfg.ini", [])
    msgs = [r.getMessage() for r in caplog.records if "timed out" in r.getMessage()]
    assert msgs
    assert all("\n" not in m for m in msgs)
    assert "Job 12 timed out after 1s" in msgs[0]

  def test_no_timeout_when_disabled_and_process_finishes(self, tmp_path):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"")
    worker = _make_worker()
    worker.job_timeout_seconds = 0
    worker.script_path = _write_script(tmp_path, "print('SMA_FINAL_OUTPUT: /out.mp4')\n")
    success, final_output, _ = worker._run_conversion_inner(13, str(media), "/cfg.ini", [])
    assert success is True
    assert final_output == "/out.mp4"
    assert worker._abort_reason is None


@posix_only
class TestCrossNodeCancel:
  def test_remote_cancel_detected_by_poll_kills_process(self, tmp_path):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"")
    db = mock.MagicMock()
    db.get_job.return_value = {"status": "cancelled", "node_id": None}
    worker = _make_worker(job_db=db)
    worker.job_timeout_seconds = 0
    worker.script_path = _write_script(tmp_path, _HUNG_PARENT)
    with mock.patch.object(worker_mod, "JOB_STATUS_POLL_SECONDS", 0.2):
      started = time.monotonic()
      success, _, _ = worker._run_conversion_inner(14, str(media), "/cfg.ini", [])
    assert success is False
    assert worker._abort_reason == ABORT_CANCELLED
    assert time.monotonic() - started < 20

  def test_local_abort_job_kills_running_conversion(self, tmp_path):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"")
    worker = _make_worker()
    worker.job_timeout_seconds = 0
    worker.script_path = _write_script(tmp_path, _HUNG_PARENT)
    worker.current_job_id = 15
    result = {}

    def _run():
      result["value"] = worker._run_conversion_inner(15, str(media), "/cfg.ini", [])

    t = threading.Thread(target=_run)
    t.start()
    deadline = time.monotonic() + 10
    while worker._watchdog is None and time.monotonic() < deadline:
      time.sleep(0.05)
    assert worker.abort_job(15, ABORT_CANCELLED) is True
    t.join(timeout=20)
    assert not t.is_alive()
    assert result["value"][0] is False


class TestCheckJobOwnership:
  def test_running_on_this_node_is_fine(self):
    db = mock.MagicMock()
    worker = _make_worker(job_db=db)
    db.get_job.return_value = {"status": "running", "node_id": worker.node_id}
    assert worker._check_job_ownership(1) is None

  def test_cancelled(self):
    db = mock.MagicMock()
    db.get_job.return_value = {"status": "cancelled"}
    worker = _make_worker(job_db=db)
    assert worker._check_job_ownership(1) == ABORT_CANCELLED

  def test_deleted_row_counts_as_cancelled(self):
    db = mock.MagicMock()
    db.get_job.return_value = None
    worker = _make_worker(job_db=db)
    assert worker._check_job_ownership(1) == ABORT_CANCELLED

  @pytest.mark.parametrize("status", ["pending", "completed", "failed"])
  def test_requeued_or_finished_elsewhere(self, status):
    db = mock.MagicMock()
    db.get_job.return_value = {"status": status}
    worker = _make_worker(job_db=db)
    assert worker._check_job_ownership(1) == ABORT_REASSIGNED

  def test_claimed_by_other_node(self):
    db = mock.MagicMock()
    db.get_job.return_value = {"status": "running", "node_id": "someone-else"}
    worker = _make_worker(job_db=db)
    assert worker._check_job_ownership(1) == ABORT_REASSIGNED

  def test_db_error_is_ignored(self):
    db = mock.MagicMock()
    db.get_job.side_effect = RuntimeError("db down")
    worker = _make_worker(job_db=db)
    assert worker._check_job_ownership(1) is None
    assert worker._abort_reason is None

  def test_non_dict_row_is_ignored(self):
    worker = _make_worker()  # MagicMock get_job returns a MagicMock
    assert worker._check_job_ownership(1) is None

  def test_first_reason_wins(self):
    worker = _make_worker()
    assert worker._set_abort_reason(ABORT_TIMEOUT) == ABORT_TIMEOUT
    assert worker._set_abort_reason(ABORT_CANCELLED) == ABORT_TIMEOUT


class TestWatchdogUnit:
  def test_finish_before_deadline_does_not_kill(self):
    worker = _make_worker()
    proc = mock.MagicMock()
    wd = _JobWatchdog(worker, 1, proc, timeout_seconds=60, poll_seconds=60)
    wd.start()
    wd.finish()
    wd.join(timeout=5)
    assert not wd.fired
    proc.terminate.assert_not_called()

  def test_pre_set_abort_reason_kills_immediately(self):
    worker = _make_worker()
    worker._abort_reason = ABORT_SHUTDOWN
    proc = mock.MagicMock()
    wd = _JobWatchdog(worker, 1, proc, timeout_seconds=0, poll_seconds=60)
    wd.start()
    wd.join(timeout=5)
    assert wd.fired
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


class TestKillProcessTree:
  def test_fallback_for_non_int_pid(self):
    proc = mock.MagicMock()
    kill_process_tree(proc, grace=0)
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once_with(timeout=0)
    proc.kill.assert_called_once()

  def test_never_raises(self):
    proc = mock.MagicMock()
    proc.terminate.side_effect = OSError("gone")
    proc.wait.side_effect = RuntimeError("boom")
    proc.kill.side_effect = OSError("gone")
    kill_process_tree(proc, grace=0)  # must not raise

  @posix_only
  def test_does_not_signal_own_process_group(self):
    proc = mock.MagicMock()
    proc.pid = os.getpgrp()
    with mock.patch("os.killpg") as killpg:
      kill_process_tree(proc, grace=0, own_group=True)
    killpg.assert_not_called()
    proc.terminate.assert_called_once()


class TestProcessJobAbortOutcomes:
  def _run(self, tmp_path, reason, success=False, db=None):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"")
    db = db or mock.MagicMock()
    db.get_job.return_value = {"status": "running"}
    worker = _make_worker(job_db=db)
    worker.job_timeout_seconds = 42

    def fake_run(job_id, path, config, args):
      worker._abort_reason = reason
      return (success, None, None)

    worker._run_conversion = fake_run
    worker._preflight_disk_pressure = mock.MagicMock(return_value=False)
    worker.process_job({"id": 21, "path": str(media), "config": "/cfg.ini", "args": None})
    return worker, db

  def test_timeout_fails_job_with_timeout_cause(self, tmp_path):
    worker, db = self._run(tmp_path, ABORT_TIMEOUT)
    db.fail_job.assert_called_once_with(21, "Job timed out after 42s", failure_category="system", failure_cause="timeout", node_id=worker.node_id)
    db.complete_job.assert_not_called()

  def test_shutdown_leaves_row_for_requeue(self, tmp_path):
    _worker, db = self._run(tmp_path, ABORT_SHUTDOWN)
    db.fail_job.assert_not_called()
    db.complete_job.assert_not_called()

  @pytest.mark.parametrize("reason", [ABORT_CANCELLED, ABORT_REASSIGNED])
  def test_cancel_or_reassign_writes_nothing(self, tmp_path, reason):
    _worker, db = self._run(tmp_path, reason)
    db.fail_job.assert_not_called()
    db.complete_job.assert_not_called()

  def test_success_wins_over_late_abort(self, tmp_path):
    _worker, db = self._run(tmp_path, ABORT_TIMEOUT, success=True)
    db.complete_job.assert_called_once()
    db.fail_job.assert_not_called()

  def test_discarded_completion_is_logged(self, tmp_path, caplog):
    db = mock.MagicMock()
    db.complete_job.return_value = False
    with caplog.at_level(logging.INFO, logger="test.worker.reliability"):
      worker, _ = self._run(tmp_path, None, success=True, db=db)
    assert any("completion discarded" in r.getMessage() for r in caplog.records)
    assert worker.current_job_id is None

  def test_discarded_failure_is_logged(self, tmp_path, caplog):
    db = mock.MagicMock()
    db.fail_job.return_value = False
    with caplog.at_level(logging.INFO, logger="test.worker.reliability"):
      self._run(tmp_path, None, success=False, db=db)
    assert any("failure discarded" in r.getMessage() for r in caplog.records)

  def test_complete_job_receives_node_id(self, tmp_path):
    worker, db = self._run(tmp_path, None, success=True)
    assert db.complete_job.call_args.kwargs["node_id"] == worker.node_id


class TestRunLoopResilience:
  def test_db_error_does_not_kill_thread(self):
    db = mock.MagicMock()
    db.is_distributed = False
    calls = {"n": 0}

    def claim(*a, **kw):
      calls["n"] += 1
      if calls["n"] == 1:
        raise RuntimeError("connection refused")
      worker.stop()
      return None

    db.claim_next_job.side_effect = claim
    worker = _make_worker(job_db=db)
    worker.config_lock_manager.get_locked_configs.return_value = set()
    worker.job_event.set()
    with mock.patch.object(worker_mod, "LOOP_ERROR_BACKOFF_SECONDS", 0.05):
      worker.start()
      deadline = time.monotonic() + 15
      while worker.running and time.monotonic() < deadline:
        worker.job_event.set()
        time.sleep(0.05)
      worker.join(timeout=10)
    assert calls["n"] >= 2
    assert not worker.is_alive()

  def test_paused_worker_does_not_spin(self):
    db = mock.MagicMock()
    db.is_distributed = False
    pool = mock.MagicMock()
    pool._pause_mode = threading.Event()
    pool._pause_mode.set()
    pool._drain_mode = threading.Event()
    worker = _make_worker(job_db=db, pool=pool)
    worker.job_event.set()
    worker._run_once()  # must return promptly instead of looping
    db.claim_next_job.assert_not_called()

  def test_stop_interrupts_error_backoff(self):
    worker = _make_worker()
    worker.stop()
    assert worker._stop_event.is_set()


def _pool(count=2):
  with mock.patch.object(ConversionWorker, "start"):
    pool = WorkerPool(
      worker_count=count,
      job_db=mock.MagicMock(),
      path_config_manager=mock.MagicMock(),
      config_log_manager=mock.MagicMock(),
      config_lock_manager=mock.MagicMock(),
      logger=logging.getLogger("test.worker.reliability"),
    )
  return pool


class TestWorkerPoolLiveness:
  def test_dead_worker_is_respawned_with_its_settings(self):
    pool = _pool(2)
    dead = pool._workers[0]
    dead.ffmpeg_dir = "/reloaded/ffmpeg"
    dead.job_timeout_seconds = 99
    with mock.patch.object(ConversionWorker, "is_alive", return_value=False), mock.patch.object(ConversionWorker, "ident", new=123, create=True), mock.patch.object(ConversionWorker, "start") as start:
      respawned = pool.ensure_workers_alive()
    assert respawned == 2
    assert start.call_count == 2
    new = pool._workers[0]
    assert new is not dead
    assert new.worker_id == 1
    assert new.ffmpeg_dir == "/reloaded/ffmpeg"
    assert new.job_timeout_seconds == 99

  def test_never_started_worker_is_not_respawned(self):
    pool = _pool(1)
    with mock.patch.object(ConversionWorker, "start") as start:
      assert pool.ensure_workers_alive() == 0
    start.assert_not_called()

  def test_no_respawn_after_stop(self):
    pool = _pool(1)
    pool.stop()
    with mock.patch.object(ConversionWorker, "is_alive", return_value=False), mock.patch.object(ConversionWorker, "start") as start:
      assert pool.ensure_workers_alive() == 0
    start.assert_not_called()

  def test_abort_job_routes_to_owner(self):
    pool = _pool(2)
    pool._workers[1].current_job_id = 77
    assert pool.abort_job(77, ABORT_CANCELLED) is True
    assert pool._workers[1]._abort_reason == ABORT_CANCELLED
    assert pool._workers[0]._abort_reason is None
    assert pool.abort_job(78, ABORT_CANCELLED) is False

  def test_abort_all_only_hits_busy_workers(self):
    pool = _pool(3)
    pool._workers[0].current_job_id = 1
    pool._workers[2].current_job_id = 3
    assert pool.abort_all(ABORT_SHUTDOWN) == 2
    assert pool._workers[1]._abort_reason is None
