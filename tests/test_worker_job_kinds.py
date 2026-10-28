"""Worker behaviour for non-convert job kinds and captured output."""

import logging
import sys
from unittest import mock

import pytest

from resources.daemon import job_kinds
from resources.daemon import worker as worker_mod
from resources.daemon.worker import ConversionWorker, _OutputBuffer


def _worker(db):
  log_mgr = mock.MagicMock()
  log_mgr.get_logger.return_value = logging.getLogger("test.worker.kinds")
  log_mgr.get_log_file.return_value = "/tmp/test.log"
  return ConversionWorker(
    worker_id=1,
    job_db=db,
    path_config_manager=mock.MagicMock(),
    config_log_manager=log_mgr,
    config_lock_manager=mock.MagicMock(),
    logger=logging.getLogger("test.worker.kinds"),
  )


def _proc(lines, rc):
  proc = mock.MagicMock()
  proc.stdout = iter(line + "\n" for line in lines)
  proc.returncode = rc
  return proc


def _db():
  db = mock.MagicMock()
  db.get_job.return_value = {"status": "running"}
  return db


class TestProcessJobKinds:
  def test_non_convert_skips_preflight_and_sizes(self, tmp_path, monkeypatch):
    target = tmp_path / "Show"
    target.mkdir()
    db = _db()
    worker = _worker(db)
    preflight = mock.MagicMock(return_value=False)
    monkeypatch.setattr(worker, "_preflight_disk_pressure", preflight)
    worker._run_conversion = mock.MagicMock(return_value=(True, None, None))
    worker.process_job({"id": 5, "path": str(target), "config": "/cfg.yml", "args": '["-n"]', "kind": "repair", "profile": "rq"})
    preflight.assert_not_called()
    worker._run_conversion.assert_called_once_with(5, str(target), "/cfg.yml", ["-n"], kind="repair")
    kwargs = db.complete_job.call_args.kwargs
    assert kwargs["input_size"] is None and kwargs["output_size"] is None

  def test_preview_gets_profile(self, tmp_path):
    media = tmp_path / "a.mkv"
    media.write_text("x")
    worker = _worker(_db())
    worker._run_conversion = mock.MagicMock(return_value=(True, None, None))
    worker.process_job({"id": 6, "path": str(media), "config": "/c", "args": "[]", "kind": "preview", "profile": "rq"})
    assert worker._run_conversion.call_args.args[3] == ["--profile", "rq"]

  def test_missing_path_fails_for_any_kind(self, tmp_path):
    db = _db()
    worker = _worker(db)
    worker.process_job({"id": 7, "path": str(tmp_path / "gone"), "config": "/c", "args": None, "kind": "audit"})
    assert db.fail_job.call_args.args[:2] == (7, "Path does not exist")

  def test_unknown_kind_fails(self, tmp_path):
    db = _db()
    worker = _worker(db)
    worker.process_job({"id": 8, "path": str(tmp_path), "config": "/c", "args": None, "kind": "transcode"})
    assert db.fail_job.call_args.args[:2] == (8, "Unsupported job kind 'transcode'")

  def test_failed_repair_message(self, tmp_path):
    db = _db()
    worker = _worker(db)
    worker._run_conversion = mock.MagicMock(return_value=(False, None, None))
    worker.process_job({"id": 9, "path": str(tmp_path), "config": "/c", "args": None, "kind": "repair"})
    assert db.fail_job.call_args.args[:2] == (9, "Repair process failed")

  def test_convert_call_shape_unchanged(self, tmp_path, monkeypatch):
    media = tmp_path / "a.mkv"
    media.write_text("x")
    worker = _worker(_db())
    monkeypatch.setattr(worker, "_preflight_disk_pressure", lambda *a: False)
    worker._run_conversion = mock.MagicMock(return_value=(True, None, None))
    worker.process_job({"id": 10, "path": str(media), "config": "/c", "args": None})
    worker._run_conversion.assert_called_once_with(10, str(media), "/c", [])


class TestRunInnerKinds:
  @pytest.mark.parametrize(
    "kind,rc,ok",
    [("audit", 1, True), ("audit", 2, False), ("repair", 1, False), ("repair", 0, True), ("convert", 1, False)],
  )
  def test_success_codes(self, tmp_path, kind, rc, ok):
    worker = _worker(_db())
    with mock.patch("subprocess.Popen", return_value=_proc(["done"], rc)):
      success, _out, _dur = worker._run_conversion_inner(1, str(tmp_path), "/c", [], kind=kind)
    assert success is ok

  @pytest.mark.parametrize("kind", job_kinds.SUPPORTED_KINDS)
  def test_argv_per_kind(self, tmp_path, kind):
    worker = _worker(_db())
    with mock.patch("subprocess.Popen", return_value=_proc([], 0)) as popen:
      worker._run_conversion_inner(3, str(tmp_path), "/c", ["-x"], kind=kind)
    expected = job_kinds.get(kind).argv(
      worker_mod.os.path.dirname(worker.script_path),
      str(tmp_path),
      "/c",
      3,
      ["-x"],
      python=sys.executable,
      script_path=worker.script_path if job_kinds.get(kind).script == "manual.py" else None,
    )
    assert popen.call_args.args[0] == expected

  def test_convert_argv_byte_identical(self, tmp_path):
    worker = _worker(_db())
    with mock.patch("subprocess.Popen", return_value=_proc([], 0)) as popen:
      worker._run_conversion_inner(4, str(tmp_path), "/c", ["-tmdb", "603"])
    assert popen.call_args.args[0] == [sys.executable, worker.script_path, "-a", "-i", str(tmp_path), "-c", "/c", "--job-id", "4", "-tmdb", "603"]

  def test_output_captured_without_progress_lines(self, tmp_path):
    db = _db()
    worker = _worker(db)
    lines = ["Repairing /x", "frame=  100 fps=25 time=00:00:04.00 speed=1x", "Repair finished: 1 repaired"]
    with mock.patch("subprocess.Popen", return_value=_proc(lines, 0)):
      worker._run_conversion_inner(2, str(tmp_path), "/c", [], kind="repair")
    stored = "".join(call.args[1] for call in db.append_job_output.call_args_list)
    assert stored == "Repairing /x\nRepair finished: 1 repaired\n"


class TestOutputBuffer:
  def test_flushes_on_interval_and_at_end(self):
    db = mock.MagicMock()
    now = [0.0]
    buf = _OutputBuffer(db, 7, clock=lambda: now[0])
    buf.add("a")
    db.append_job_output.assert_not_called()
    now[0] = 2.5
    buf.add("b")
    db.append_job_output.assert_called_once_with(7, "a\nb\n")
    buf.add("c")
    buf.flush()
    assert db.append_job_output.call_args.args == (7, "c\n")
    buf.flush()
    assert db.append_job_output.call_count == 2

  def test_db_without_output_support(self):
    db = object()
    buf = _OutputBuffer(db, 1)
    buf.add("x")
    buf.flush()

  def test_storage_error_is_logged_not_raised(self, monkeypatch):
    db = mock.MagicMock()
    db.append_job_output.side_effect = RuntimeError("db down")
    fake_log = mock.MagicMock()
    monkeypatch.setattr(worker_mod, "log", fake_log)
    buf = _OutputBuffer(db, 3)
    buf.add("x")
    buf.flush()
    assert fake_log.warning.call_args.args[0] == "Could not store output for job 3"


def test_worker_claims_supported_kinds():
  db = mock.MagicMock()
  db.claim_next_job.return_value = None
  db.is_distributed = False
  worker = _worker(db)
  worker.running = True
  worker.job_event = mock.MagicMock()
  worker._run_once()
  assert db.claim_next_job.call_args.kwargs["kinds"] == job_kinds.SUPPORTED_KINDS
