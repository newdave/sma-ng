"""Conversions interrupted by a signal or daemon shutdown are requeued, not failed (SMANG-6)."""

import logging
import os
import signal
import subprocess
import sys
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

import manual
from converter.ffmpeg import FFMpeg, TerminatedBySignal
from resources.daemon.constants import STATUS_PENDING, STATUS_RUNNING
from resources.daemon.db import NO_CLAIM_TOKEN, PostgreSQLJobDatabase, SQLiteJobDatabase
from resources.daemon.worker import ABORT_CANCELLED, ConversionWorker

from .test_db_job_kinds import _pg, _pg_db


def _make_worker(job_db=None):
  log_mgr = mock.MagicMock()
  log_mgr.get_logger.return_value = logging.getLogger("test.worker.interrupt")
  log_mgr.get_log_file.return_value = "/tmp/test.log"
  return ConversionWorker(
    worker_id=1,
    job_db=job_db or mock.MagicMock(),
    path_config_manager=mock.MagicMock(),
    config_log_manager=log_mgr,
    config_lock_manager=mock.MagicMock(),
    logger=logging.getLogger("test.worker.interrupt"),
  )


class TestWorkerClassification:
  def _run(self, tmp_path, returncode, stopping=False, reason=None, success=False):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"")
    db = mock.MagicMock()
    db.get_job.return_value = {"status": "running"}
    worker = _make_worker(db)
    if stopping:
      worker.stop()

    def fake_run(job_id, path, config, args):
      worker._last_returncode = returncode
      worker._abort_reason = reason
      return (success, None, None)

    worker._run_conversion = fake_run
    worker._preflight_disk_pressure = mock.MagicMock(return_value=False)
    worker.process_job({"id": 31, "path": str(media), "config": "/cfg.ini", "args": None})
    return worker, db

  def test_sigterm_exit_requeues_without_failing(self, tmp_path):
    worker, db = self._run(tmp_path, -signal.SIGTERM)
    db.requeue_interrupted_job.assert_called_once_with(31, "Interrupted by SIGTERM (exit code -15); requeued", node_id=worker.node_id, claim_token=NO_CLAIM_TOKEN)
    db.fail_job.assert_not_called()
    db.complete_job.assert_not_called()

  @pytest.mark.parametrize("rc", [-9, -11])
  def test_other_signal_on_running_worker_fails_via_normal_path(self, tmp_path, rc):
    _worker, db = self._run(tmp_path, rc)
    db.requeue_interrupted_job.assert_not_called()
    message = db.fail_job.call_args.args[1]
    assert "killed by signal %d (exit code %d)" % (-rc, rc) in message

  def test_sigint_on_running_worker_requeues(self, tmp_path):
    worker, db = self._run(tmp_path, -signal.SIGINT)
    db.requeue_interrupted_job.assert_called_once_with(31, "Interrupted by SIGINT (exit code -2); requeued", node_id=worker.node_id, claim_token=NO_CLAIM_TOKEN)
    db.fail_job.assert_not_called()

  @pytest.mark.parametrize("rc", [128 + signal.SIGTERM, 128 + signal.SIGINT])
  def test_shell_style_signal_exit_on_running_worker_requeues(self, tmp_path, rc):
    # manual.py's fallback exits 128 + signum (143, 130) when it handled the signal itself.
    _worker, db = self._run(tmp_path, rc)
    db.requeue_interrupted_job.assert_called_once()
    db.fail_job.assert_not_called()

  @pytest.mark.parametrize("rc", [-9, -11, 1])
  def test_any_rc_while_stopping_requeues(self, tmp_path, rc):
    _worker, db = self._run(tmp_path, rc, stopping=True)
    db.requeue_interrupted_job.assert_called_once()
    db.fail_job.assert_not_called()

  def test_stopping_worker_requeues_ordinary_failure(self, tmp_path):
    worker, db = self._run(tmp_path, 1, stopping=True)
    db.requeue_interrupted_job.assert_called_once_with(31, "Interrupted by daemon shutdown (exit code 1); requeued", node_id=worker.node_id, claim_token=NO_CLAIM_TOKEN)
    db.fail_job.assert_not_called()

  def test_real_failure_is_failed_with_exit_code(self, tmp_path):
    _worker, db = self._run(tmp_path, 1)
    db.requeue_interrupted_job.assert_not_called()
    assert db.fail_job.call_args.args[1] == "Conversion process failed (exit code 1)"

  def test_user_cancel_is_not_requeued(self, tmp_path):
    _worker, db = self._run(tmp_path, -signal.SIGTERM, reason=ABORT_CANCELLED)
    db.requeue_interrupted_job.assert_not_called()
    db.fail_job.assert_not_called()

  def test_success_with_stopping_worker_still_completes(self, tmp_path):
    _worker, db = self._run(tmp_path, 0, stopping=True, success=True)
    db.complete_job.assert_called_once()
    db.requeue_interrupted_job.assert_not_called()

  def test_requeue_error_is_logged_not_raised(self, tmp_path, caplog):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"")
    db = mock.MagicMock()
    db.get_job.return_value = {"status": "running"}
    db.requeue_interrupted_job.side_effect = RuntimeError("db down")
    worker = _make_worker(db)

    def fake_run(*_a, **_k):
      worker._last_returncode = -15
      return (False, None, None)

    worker._run_conversion = fake_run
    worker._preflight_disk_pressure = mock.MagicMock(return_value=False)
    with caplog.at_level(logging.ERROR, logger="test.worker.interrupt"):
      worker.process_job({"id": 31, "path": str(media), "config": "/cfg.ini", "args": None})
    db.fail_job.assert_not_called()
    assert any("Failed to requeue interrupted job 31" in r.getMessage() for r in caplog.records)

  @pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
  def test_real_subprocess_killed_by_signal_records_negative_returncode(self, tmp_path):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"")
    script = tmp_path / "fake_manual.py"
    script.write_text("import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n")
    worker = _make_worker()
    worker.script_path = str(script)
    success, _out, _dur = worker._run_conversion_inner(32, str(media), "/cfg.ini", [])
    assert success is False
    assert worker._last_returncode == -signal.SIGTERM

  @pytest.mark.parametrize(
    "code,text",
    [(-15, "SIGTERM (exit code -15)"), (-9, "SIGKILL (exit code -9)"), (-250, "signal 250 (exit code -250)"), (None, "daemon shutdown; requeued")],
  )
  def test_interrupt_reason_text(self, code, text):
    assert text in ConversionWorker._interrupt_reason(code)


class TestDatabaseRequeue:
  def test_sqlite_requeues_running_job_without_retry_or_failure(self, tmp_path):
    db = SQLiteJobDatabase("sqlite:///%s" % (tmp_path / "x.db"))
    jid = db.add_job("/m/a.mkv", "/c.yml", max_retries=0)
    db.claim_next_job(worker_id=1, node_id="n1")
    assert db.get_job(jid)["status"] == STATUS_RUNNING
    assert db.requeue_interrupted_job(jid, "Interrupted by SIGTERM (exit code -15); requeued", node_id="n1") is True
    row = db.get_job(jid)
    assert row["status"] == STATUS_PENDING
    assert row["error"] == "Interrupted by SIGTERM (exit code -15); requeued"
    assert row["retry_count"] == 0
    assert row["node_id"] is None and row["worker_id"] is None
    # claimable again straight away
    assert db.claim_next_job(worker_id=1, node_id="n1")["id"] == jid

  def test_sqlite_ignores_other_node_and_non_running(self, tmp_path):
    db = SQLiteJobDatabase("sqlite:///%s" % (tmp_path / "x.db"))
    jid = db.add_job("/m/a.mkv", "/c.yml")
    assert db.requeue_interrupted_job(jid, "r", node_id="n1") is False  # still pending
    db.claim_next_job(worker_id=1, node_id="n1")
    assert db.requeue_interrupted_job(jid, "r", node_id="other") is False
    assert db.get_job(jid)["status"] == STATUS_RUNNING

  def test_postgres_requeue_statement_and_params(self):
    cur, modules = _pg()
    cur.rowcount = 1
    db = _pg_db(cur, modules)
    assert db.requeue_interrupted_job(7, "Interrupted by SIGTERM (exit code -15); requeued", node_id="n1") is True
    sql, params = next(c.args for c in cur.execute.call_args_list if c.args and "UPDATE jobs" in c.args[0] and "status = %s" in c.args[0])
    assert "WHERE id = %s AND status = %s" in sql and "node_id IS NULL OR node_id = %s" in sql
    assert "retry_count" not in sql
    assert params == (STATUS_PENDING, "Interrupted by SIGTERM (exit code -15); requeued", 7, STATUS_RUNNING, "n1")

  def test_postgres_returns_false_when_no_row_updated(self):
    cur, modules = _pg()
    cur.rowcount = 0
    db = _pg_db(cur, modules)
    assert db.requeue_interrupted_job(7, "r") is False
    assert isinstance(db, PostgreSQLJobDatabase)


class TestDrainTimeout:
  def _server(self, grace, job_timeout=7200):
    from resources.daemon.server import DaemonServer

    server = DaemonServer.__new__(DaemonServer)
    server.path_config_manager = MagicMock(shutdown_grace_seconds=grace)
    server.worker_pool = MagicMock(_workers=[MagicMock(job_timeout_seconds=job_timeout)])
    return server

  def test_grace_caps_the_drain(self):
    assert self._server(100)._drain_deadline_seconds() == 100

  def test_zero_grace_falls_back_to_job_timeout_plus_grace(self):
    assert self._server(0)._drain_deadline_seconds() == 7200 + 300

  def test_zero_grace_and_no_timeout_is_24_hours(self):
    assert self._server(0, job_timeout=0)._drain_deadline_seconds() == 24 * 3600

  def test_drain_deadline_kills_in_flight_with_shutdown_reason(self):
    from resources.daemon.server import ABORT_SHUTDOWN

    server = self._server(100)
    worker = MagicMock()
    worker.is_alive.return_value = True
    worker.current_job_id = 5
    server.worker_pool._workers = [worker]
    server.logger = MagicMock()
    server._drain_workers(0)
    server.worker_pool.abort_all.assert_called_once_with(ABORT_SHUTDOWN)

  def test_heartbeat_keeps_running_until_workers_drained(self):
    from resources.daemon.server import DaemonServer

    server = self._server(100)
    server.logger = MagicMock()
    server.job_db = MagicMock(is_distributed=False)
    order = MagicMock()
    for name in ("heartbeat_thread", "scanner_thread", "recycle_cleaner_thread", "storage_janitor_thread", "library_audit_thread", "library_audit_worker_thread"):
      setattr(server, name, getattr(order, name))
    server.config_watcher_thread = None
    server.worker_pool = order.worker_pool
    server.worker_pool._workers = []
    with patch.object(DaemonServer, "_drain_workers", side_effect=lambda *_a: order.drained()):
      server._stop_and_drain("shutting down")
    names = [c[0] for c in order.mock_calls]
    assert names.index("drained") < names.index("heartbeat_thread.stop")


class TestManualSigterm:
  def test_handler_raises_terminated(self):
    with pytest.raises(TerminatedBySignal):
      manual._on_sigterm(signal.SIGTERM, None)

  def test_run_installs_handler_and_resignals_on_terminate(self):
    with (
      patch.object(manual.signal, "signal") as sig,
      patch.object(manual, "main", side_effect=TerminatedBySignal(15)),
      patch.object(manual.os, "kill") as kill,
      patch.object(manual.sys, "exit") as exit_,
    ):
      manual.run()
    assert sig.call_args_list[0].args == (signal.SIGTERM, manual._on_sigterm)
    assert sig.call_args_list[1].args == (signal.SIGTERM, signal.SIG_DFL)
    kill.assert_called_once_with(os.getpid(), signal.SIGTERM)
    exit_.assert_called_once_with(128 + signal.SIGTERM)

  @pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
  def test_real_process_dies_by_sigterm_after_handler(self):
    code = ("import sys, time\nsys.path.insert(0, %r)\nimport manual\ndef fake_main():\n  print('ready', flush=True)\n  time.sleep(60)\nmanual.main = fake_main\nmanual.run()\n") % os.getcwd()
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
    assert proc.stdout.readline().strip() == b"ready"
    proc.terminate()
    assert proc.wait(timeout=30) == -signal.SIGTERM


class TestFfmpegInterrupt:
  def _ffmpeg(self):
    ff = FFMpeg.__new__(FFMpeg)
    ff.ffmpeg_path = "ffmpeg"
    ff.ffprobe_path = "ffprobe"
    return ff

  def test_interrupt_stops_child_and_carries_stderr(self, tmp_path):
    src = tmp_path / "in.mkv"
    src.write_bytes(b"x")
    proc = MagicMock()
    proc.poll.return_value = None
    proc.stderr.read1.side_effect = [b"Input #0 banner\r", TerminatedBySignal(15)]
    ff = self._ffmpeg()
    with patch.object(ff, "_spawn", return_value=proc), patch.object(ff, "generateCommands", return_value=["ffmpeg", "-i", str(src), "out.mp4"]):
      gen = ff.convert("out.mp4", ["-i", str(src)], timeout=0)
      next(gen)
      with pytest.raises(TerminatedBySignal) as info:
        for _ in gen:
          pass
    proc.terminate.assert_called_once()
    assert info.value.output == "Input #0 banner\r"
    assert "ffmpeg" in info.value.cmd

  def test_stop_child_escalates_to_kill(self):
    proc = MagicMock()
    proc.poll.return_value = None
    proc.wait.side_effect = [subprocess.TimeoutExpired("ffmpeg", 5), None]
    FFMpeg._stop_child(proc, grace=0)
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


class TestMediaprocessorInterruptCleanup:
  def test_interrupt_removes_partial_output_and_writes_sidecar(self, tmp_path):
    from tests.test_mediaprocessor import TestConvert

    mp = TestConvert()._make_mp(tmp_path)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"x")
    out = tmp_path / "input.mp4"
    options = {"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": []}

    def fake_convert(outputfile, opts, timeout=None, preopts=None, postopts=None, strip_metadata=False, info=None):
      out.write_bytes(b"partial")
      yield None, ["ffmpeg"]
      err = TerminatedBySignal(15)
      err.cmd, err.output = "ffmpeg -i x", "stderr so far"
      raise err

    mp.converter.convert = fake_convert
    mp.setPermissions = MagicMock()
    mp._write_ffmpeg_stderr_sidecar = MagicMock()
    with pytest.raises(TerminatedBySignal):
      mp.convert(options, [], [], False, None)
    assert not out.exists()
    assert src.exists()
    mp._write_ffmpeg_stderr_sidecar.assert_called_once()


class TestShutdownGraceSetting:
  def test_default_is_below_compose_stop_grace_period(self):
    from resources.config_schema import DaemonConfig

    assert DaemonConfig().shutdown_grace_seconds == 100 < 120

  def test_kebab_case_yaml_key_and_projection(self):
    from resources.config_schema import DaemonConfig

    assert DaemonConfig.model_validate({"shutdown-grace-seconds": 45}).shutdown_grace_seconds == 45
    with pytest.raises(ValueError):
      DaemonConfig.model_validate({"shutdown-grace-seconds": -1})


class TestShutdownGraceProjection:
  def test_yaml_value_reaches_path_config_manager(self, tmp_path):
    import yaml

    from resources.daemon.config import PathConfigManager

    cfg = tmp_path / "sma-ng.yml"
    cfg.write_text(yaml.safe_dump({"daemon": {"shutdown-grace-seconds": 45}}))
    assert PathConfigManager(str(cfg)).shutdown_grace_seconds == 45

  def test_init_default_matches_schema(self):
    from resources.config_schema import DaemonConfig
    from resources.daemon.config import PathConfigManager

    assert PathConfigManager().shutdown_grace_seconds == DaemonConfig().shutdown_grace_seconds
