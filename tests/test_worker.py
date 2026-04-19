"""Tests for resources/daemon/worker.py - HMS helpers and ConversionWorker logic."""

import json
import logging
import unittest.mock as mock

import pytest

from resources.daemon.worker import (
    ConversionWorker,
    WorkerPool,
    _hms_to_seconds,
    _seconds_to_hms,
)

# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


class TestHmsToSeconds:
    """Tests for _hms_to_seconds(h, m, s)."""

    def test_one_hour(self):
        assert _hms_to_seconds(1, 0, 0) == 3600.0

    def test_one_minute(self):
        assert _hms_to_seconds(0, 1, 0) == 60.0

    def test_thirty_seconds(self):
        assert _hms_to_seconds(0, 0, 30) == 30.0

    def test_fractional_seconds(self):
        assert _hms_to_seconds(0, 0, "30.5") == pytest.approx(30.5)

    def test_combined(self):
        # 1h 2m 3s = 3723 seconds
        assert _hms_to_seconds(1, 2, 3) == pytest.approx(3723.0)

    def test_string_inputs(self):
        assert _hms_to_seconds("01", "02", "03") == pytest.approx(3723.0)

    def test_zero(self):
        assert _hms_to_seconds(0, 0, 0) == 0.0

    def test_large_hours(self):
        # 99 hours
        assert _hms_to_seconds(99, 0, 0) == 99 * 3600.0

    def test_ffmpeg_style_duration(self):
        # Duration line from FFmpeg: "01:23:45"
        h, m, s = "01", "23", "45"
        assert _hms_to_seconds(h, m, s) == pytest.approx(5025.0)


class TestSecondsToHms:
    """Tests for _seconds_to_hms(secs)."""

    def test_one_hour(self):
        assert _seconds_to_hms(3600) == "01:00:00"

    def test_one_minute(self):
        assert _seconds_to_hms(60) == "00:01:00"

    def test_one_second(self):
        assert _seconds_to_hms(1) == "00:00:01"

    def test_zero(self):
        assert _seconds_to_hms(0) == "00:00:00"

    def test_negative_clamps_to_zero(self):
        assert _seconds_to_hms(-99) == "00:00:00"

    def test_mixed(self):
        # 1h 30m 5s = 5405 seconds
        assert _seconds_to_hms(5405) == "01:30:05"

    def test_more_than_24_hours(self):
        # 25 hours = 90000 seconds
        assert _seconds_to_hms(90000) == "25:00:00"

    def test_fractional_truncates(self):
        assert _seconds_to_hms(3661.9) == "01:01:01"

    def test_padding(self):
        # single-digit h/m/s must be zero-padded
        assert _seconds_to_hms(3661) == "01:01:01"


class TestHmsRoundtrip:
    """Round-trip: _seconds_to_hms(_hms_to_seconds(h, m, s)) == original."""

    @pytest.mark.parametrize(
        "hms",
        ["00:00:00", "00:01:30", "01:23:45", "10:00:00", "99:59:59"],
    )
    def test_roundtrip(self, hms):
        h, m, s = hms.split(":")
        assert _seconds_to_hms(_hms_to_seconds(h, m, s)) == hms


# ---------------------------------------------------------------------------
# ConversionWorker helpers
# ---------------------------------------------------------------------------


def _make_worker(job_db=None, lock_mgr=None):
    """Build a ConversionWorker with all dependencies mocked out."""
    if job_db is None:
        job_db = mock.MagicMock()
    if lock_mgr is None:
        lock_mgr = mock.MagicMock()
    path_cfg = mock.MagicMock()
    log_mgr = mock.MagicMock()
    # get_logger must return something with .info/.error/.exception/.debug
    log_mgr.get_logger.return_value = logging.getLogger("test.worker")
    log_mgr.get_log_file.return_value = "/tmp/test.log"
    logger = logging.getLogger("test.worker")
    return ConversionWorker(
        worker_id=1,
        job_db=job_db,
        path_config_manager=path_cfg,
        config_log_manager=log_mgr,
        config_lock_manager=lock_mgr,
        logger=logger,
    )


class TestConversionWorkerProcessJob:
    """Unit tests for ConversionWorker.process_job() — no real subprocess."""

    def test_fails_when_path_missing(self, tmp_path):
        db = mock.MagicMock()
        worker = _make_worker(job_db=db)
        job = {"id": 1, "path": str(tmp_path / "missing.mkv"), "config": "/cfg.ini", "args": None}
        worker.process_job(job)
        db.fail_job.assert_called_once_with(1, "Path does not exist")

    def test_skips_cancelled_job_before_start(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        db = mock.MagicMock()
        db.get_job.return_value = {"status": "cancelled"}
        lock_mgr = mock.MagicMock()
        worker = _make_worker(job_db=db, lock_mgr=lock_mgr)
        job = {"id": 2, "path": str(media), "config": "/cfg.ini", "args": None}
        worker.process_job(job)
        db.fail_job.assert_not_called()
        db.complete_job.assert_not_called()
        # Lock must never be acquired if the job was already cancelled
        lock_mgr.acquire.assert_not_called()

    def test_skips_cancelled_job_after_lock_acquired(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        db = mock.MagicMock()
        # First get_job (before lock): running; second (after lock): cancelled
        db.get_job.side_effect = [{"status": "running"}, {"status": "cancelled"}]
        lock_mgr = mock.MagicMock()
        worker = _make_worker(job_db=db, lock_mgr=lock_mgr)
        job = {"id": 3, "path": str(media), "config": "/cfg.ini", "args": None}
        worker.process_job(job)
        db.fail_job.assert_not_called()
        db.complete_job.assert_not_called()
        lock_mgr.release.assert_called_once()

    def test_completes_job_on_successful_conversion(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        db = mock.MagicMock()
        db.get_job.return_value = {"status": "running"}
        worker = _make_worker(job_db=db)
        worker._run_conversion = mock.MagicMock(return_value=True)
        job = {"id": 4, "path": str(media), "config": "/cfg.ini", "args": None}
        worker.process_job(job)
        db.complete_job.assert_called_once_with(4)
        db.fail_job.assert_not_called()

    def test_fails_job_on_failed_conversion(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        db = mock.MagicMock()
        db.get_job.return_value = {"status": "running"}
        worker = _make_worker(job_db=db)
        worker._run_conversion = mock.MagicMock(return_value=False)
        job = {"id": 5, "path": str(media), "config": "/cfg.ini", "args": None}
        worker.process_job(job)
        db.fail_job.assert_called_once_with(5, "Conversion process failed")
        db.complete_job.assert_not_called()

    def test_does_not_overwrite_cancelled_status_after_failed_conversion(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        db = mock.MagicMock()
        # pre-lock and post-lock get_job: running; post-conversion: cancelled
        db.get_job.side_effect = [
            {"status": "running"},
            {"status": "running"},
            {"status": "cancelled"},
        ]
        worker = _make_worker(job_db=db)
        worker._run_conversion = mock.MagicMock(return_value=False)
        job = {"id": 6, "path": str(media), "config": "/cfg.ini", "args": None}
        worker.process_job(job)
        db.fail_job.assert_not_called()
        db.complete_job.assert_not_called()

    def test_lock_always_released_on_exception(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        db = mock.MagicMock()
        db.get_job.return_value = {"status": "running"}
        lock_mgr = mock.MagicMock()
        worker = _make_worker(job_db=db, lock_mgr=lock_mgr)
        worker._run_conversion = mock.MagicMock(side_effect=RuntimeError("boom"))
        job = {"id": 7, "path": str(media), "config": "/cfg.ini", "args": None}
        worker.process_job(job)
        lock_mgr.release.assert_called_once()
        db.fail_job.assert_called_once()

    def test_parses_json_args(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        db = mock.MagicMock()
        db.get_job.return_value = {"status": "running"}
        worker = _make_worker(job_db=db)
        captured = {}

        def fake_run(job_id, path, config, args):
            captured["args"] = args
            return True

        worker._run_conversion = fake_run
        job = {"id": 8, "path": str(media), "config": "/cfg.ini", "args": '["-tmdb", "603"]'}
        worker.process_job(job)
        assert captured["args"] == ["-tmdb", "603"]

    def test_null_args_becomes_empty_list(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        db = mock.MagicMock()
        db.get_job.return_value = {"status": "running"}
        worker = _make_worker(job_db=db)
        captured = {}

        def fake_run(job_id, path, config, args):
            captured["args"] = args
            return True

        worker._run_conversion = fake_run
        job = {"id": 9, "path": str(media), "config": "/cfg.ini", "args": None}
        worker.process_job(job)
        assert captured["args"] == []

    def test_current_job_id_cleared_after_completion(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        db = mock.MagicMock()
        db.get_job.return_value = {"status": "running"}
        worker = _make_worker(job_db=db)
        worker._run_conversion = mock.MagicMock(return_value=True)
        job = {"id": 10, "path": str(media), "config": "/cfg.ini", "args": None}
        worker.process_job(job)
        assert worker.current_job_id is None

    def test_current_job_id_cleared_after_failure(self, tmp_path):
        db = mock.MagicMock()
        worker = _make_worker(job_db=db)
        job = {"id": 11, "path": "/nonexistent/file.mkv", "config": "/cfg.ini", "args": None}
        worker.process_job(job)
        assert worker.current_job_id is None


# ---------------------------------------------------------------------------
# ConversionWorker.stop
# ---------------------------------------------------------------------------


class TestConversionWorkerStop:
    def test_stop_sets_running_false(self):
        worker = _make_worker()
        worker.stop()
        assert worker.running is False

    def test_stop_sets_job_event(self):
        worker = _make_worker()
        worker.stop()
        assert worker.job_event.is_set()


# ---------------------------------------------------------------------------
# ConversionWorker._run_conversion_inner
# ---------------------------------------------------------------------------


def _make_fake_process(stdout_lines, returncode=0):
    proc = mock.MagicMock()
    proc.stdout = iter(line + "\n" for line in stdout_lines)
    proc.returncode = returncode
    proc.wait.return_value = None
    return proc


class TestRunConversionInner:
    def test_returns_true_on_zero_exit(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        worker = _make_worker()
        worker.job_timeout_seconds = 0
        proc = _make_fake_process([], returncode=0)
        with mock.patch("subprocess.Popen", return_value=proc):
            result = worker._run_conversion_inner(1, str(media), "/cfg.ini", [])
        assert result is True

    def test_returns_false_on_nonzero_exit(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        worker = _make_worker()
        proc = _make_fake_process([], returncode=1)
        with mock.patch("subprocess.Popen", return_value=proc):
            result = worker._run_conversion_inner(1, str(media), "/cfg.ini", [])
        assert result is False

    def test_returns_false_on_timeout(self, tmp_path):
        import subprocess as _subprocess

        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        worker = _make_worker()
        worker.job_timeout_seconds = 10
        proc = _make_fake_process([], returncode=0)
        proc.wait.side_effect = _subprocess.TimeoutExpired(cmd="manual.py", timeout=10)
        with mock.patch("subprocess.Popen", return_value=proc):
            result = worker._run_conversion_inner(1, str(media), "/cfg.ini", [])
        assert result is False
        proc.kill.assert_called_once()

    def test_returns_false_on_popen_exception(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        worker = _make_worker()
        with mock.patch("subprocess.Popen", side_effect=OSError("no such file")):
            result = worker._run_conversion_inner(1, str(media), "/cfg.ini", [])
        assert result is False

    def test_prepends_ffmpeg_dir_to_path(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        worker = _make_worker()
        worker.ffmpeg_dir = "/custom/ffmpeg"
        proc = _make_fake_process([], returncode=0)
        captured_env = {}

        def fake_popen(cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return proc

        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            worker._run_conversion_inner(1, str(media), "/cfg.ini", [])

        assert captured_env["PATH"].startswith("/custom/ffmpeg")

    def test_parses_ffmpeg_duration_line(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        worker = _make_worker()
        worker.progress_log_interval = 0
        duration_line = "  Duration: 01:30:00, start: 0.000000, bitrate: 5000 kb/s"
        progress_line = "frame=  100 fps= 25 speed=1.00x time=00:01:00 size=  5000kB"
        proc = _make_fake_process([duration_line, progress_line], returncode=0)
        with mock.patch("subprocess.Popen", return_value=proc):
            result = worker._run_conversion_inner(1, str(media), "/cfg.ini", [])
        assert result is True

    def test_empty_lines_are_skipped(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        worker = _make_worker()
        proc = _make_fake_process(["", "   ", ""], returncode=0)
        with mock.patch("subprocess.Popen", return_value=proc):
            result = worker._run_conversion_inner(1, str(media), "/cfg.ini", [])
        assert result is True

    def test_cleans_up_job_processes_on_completion(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        job_procs = {}
        worker = _make_worker()
        worker._job_processes = job_procs
        proc = _make_fake_process([], returncode=0)
        with mock.patch("subprocess.Popen", return_value=proc):
            worker._run_conversion_inner(1, str(media), "/cfg.ini", [])
        assert 1 not in job_procs

    def test_cleans_up_job_progress_on_completion(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        job_progress = {}
        worker = _make_worker()
        worker._job_progress = job_progress
        progress_line = "frame=   1 fps=25 time=00:00:01"
        proc = _make_fake_process([progress_line], returncode=0)
        with mock.patch("subprocess.Popen", return_value=proc):
            worker._run_conversion_inner(1, str(media), "/cfg.ini", [])
        assert 1 not in job_progress

    def test_extra_args_appended_to_command(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        worker = _make_worker()
        captured_cmd = []

        proc = _make_fake_process([], returncode=0)

        def fake_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return proc

        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            worker._run_conversion_inner(1, str(media), "/cfg.ini", ["-tmdb", "603"])

        assert "-tmdb" in captured_cmd
        assert "603" in captured_cmd


# ---------------------------------------------------------------------------
# ConversionWorker.run (loop)
# ---------------------------------------------------------------------------


class TestConversionWorkerRun:
    def test_run_exits_when_stopped_before_first_job(self):
        db = mock.MagicMock()
        db.claim_next_job.return_value = None
        worker = _make_worker(job_db=db)
        worker.running = False
        worker.job_event.set()  # pre-signal so wait returns immediately
        worker.run()  # must return, not block

    def test_run_processes_available_job(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"")
        db = mock.MagicMock()
        lock_mgr = mock.MagicMock()
        lock_mgr.get_locked_configs.return_value = []
        job = {"id": 99, "path": str(media), "config": "/cfg.ini", "args": None}
        # Return job once, then None to stop inner loop, then set running=False
        call_count = [0]

        def claim_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return job
            worker.running = False
            return None

        db.claim_next_job.side_effect = claim_side_effect
        db.get_job.return_value = {"status": "running"}
        worker = _make_worker(job_db=db, lock_mgr=lock_mgr)
        worker._run_conversion = mock.MagicMock(return_value=True)
        worker.job_event.set()
        worker.run()
        db.complete_job.assert_called_once_with(99)


# ---------------------------------------------------------------------------
# WorkerPool.restart
# ---------------------------------------------------------------------------


def _make_pool_worker(worker_count=2):
    mock_workers = [mock.MagicMock() for _ in range(worker_count)]
    for w in mock_workers:
        w.is_alive.return_value = False
        w.job_event = mock.MagicMock()
        w.running = True

    with mock.patch("resources.daemon.worker.ConversionWorker") as MockWorker:
        MockWorker.side_effect = list(mock_workers)
        pool = WorkerPool(
            worker_count=worker_count,
            job_db=mock.MagicMock(),
            path_config_manager=mock.MagicMock(),
            config_log_manager=mock.MagicMock(),
            config_lock_manager=mock.MagicMock(),
            logger=mock.MagicMock(),
        )
    pool._workers = mock_workers
    return pool, mock_workers


class TestWorkerPoolRestart:
    def test_restart_replaces_workers(self):
        pool, _ = _make_pool_worker(worker_count=2)
        old_workers = list(pool._workers)
        with mock.patch("resources.daemon.worker.ConversionWorker") as MockWorker:
            new_mock = mock.MagicMock()
            MockWorker.return_value = new_mock
            pool.restart()
        for w in old_workers:
            w.stop.assert_called_once()

    def test_restart_updates_ffmpeg_dir(self):
        pool, _ = _make_pool_worker(worker_count=1)
        with mock.patch("resources.daemon.worker.ConversionWorker") as MockWorker:
            MockWorker.return_value = mock.MagicMock()
            pool.restart(ffmpeg_dir="/new/ffmpeg")
        assert pool._ffmpeg_dir == "/new/ffmpeg"

    def test_restart_updates_job_timeout(self):
        pool, _ = _make_pool_worker(worker_count=1)
        with mock.patch("resources.daemon.worker.ConversionWorker") as MockWorker:
            MockWorker.return_value = mock.MagicMock()
            pool.restart(job_timeout_seconds=3600)
        assert pool._job_timeout_seconds == 3600

    def test_restart_does_not_change_ffmpeg_dir_when_not_provided(self):
        pool, _ = _make_pool_worker(worker_count=1)
        pool._ffmpeg_dir = "/existing/ffmpeg"
        with mock.patch("resources.daemon.worker.ConversionWorker") as MockWorker:
            MockWorker.return_value = mock.MagicMock()
            pool.restart()
        assert pool._ffmpeg_dir == "/existing/ffmpeg"
