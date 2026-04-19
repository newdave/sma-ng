"""Tests for _validate_hwaccel, DaemonServer.cancel_job, and WorkerPool."""

import subprocess
from unittest.mock import MagicMock, Mock, patch

import pytest

from resources.daemon.server import DaemonServer, _validate_hwaccel
from resources.daemon.worker import ConversionWorker, WorkerPool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pcm(configs):
    """Return a mock PathConfigManager whose get_all_configs() returns configs."""
    pcm = MagicMock()
    pcm.get_all_configs.return_value = list(configs)
    return pcm


def _make_server():
    """Construct a bare DaemonServer without calling __init__ (avoids threads)."""
    server = object.__new__(DaemonServer)
    server._job_processes = {}
    server.job_db = MagicMock()
    return server


def _make_pool(worker_count=3):
    """Create a WorkerPool with ConversionWorker patched so no real threads start."""
    mock_workers = [MagicMock() for _ in range(worker_count)]
    for w in mock_workers:
        w.is_alive.return_value = False
        w.job_event = MagicMock()
        w.running = True

    with patch("resources.daemon.worker.ConversionWorker") as MockWorker:
        MockWorker.side_effect = list(mock_workers)
        pool = WorkerPool(
            worker_count=worker_count,
            job_db=MagicMock(),
            path_config_manager=MagicMock(),
            config_log_manager=MagicMock(),
            config_lock_manager=MagicMock(),
            logger=MagicMock(),
        )
    # pool._workers was populated inside _start_workers; replace with our mocks
    # so that any attribute access is predictable.
    pool._workers = mock_workers
    return pool, mock_workers


# ---------------------------------------------------------------------------
# TestValidateHwaccel
# ---------------------------------------------------------------------------


class TestValidateHwaccel:
    """Tests for _validate_hwaccel()."""

    def test_does_nothing_when_no_configs(self):
        pcm = _make_pcm([])
        logger = MagicMock()
        with patch("subprocess.run") as mock_run:
            _validate_hwaccel(pcm, None, logger)
        mock_run.assert_not_called()
        logger.warning.assert_not_called()

    def test_does_nothing_when_no_video_section(self, tmp_path):
        cfg = tmp_path / "autoProcess.ini"
        cfg.write_text("[Converter]\ndelete-original = False\n")
        pcm = _make_pcm([str(cfg)])
        logger = MagicMock()
        with patch("subprocess.run") as mock_run:
            _validate_hwaccel(pcm, None, logger)
        mock_run.assert_not_called()

    def test_does_nothing_when_no_video_codec_key(self, tmp_path):
        cfg = tmp_path / "autoProcess.ini"
        cfg.write_text("[Video]\nsome-other-key = value\n")
        pcm = _make_pcm([str(cfg)])
        logger = MagicMock()
        with patch("subprocess.run") as mock_run:
            _validate_hwaccel(pcm, None, logger)
        mock_run.assert_not_called()

    def test_warns_when_ffmpeg_returns_nonzero(self, tmp_path):
        cfg = tmp_path / "autoProcess.ini"
        cfg.write_text("[Video]\nvideo-codec = h264, nvenc\n")
        pcm = _make_pcm([str(cfg)])
        logger = MagicMock()
        with patch("subprocess.run", return_value=Mock(returncode=1)) as mock_run:
            _validate_hwaccel(pcm, None, logger)
        mock_run.assert_called_once()
        logger.warning.assert_called_once()
        assert "h264_nvenc" in logger.warning.call_args[0][0]

    def test_logs_info_validated_ok_when_ffmpeg_returns_zero(self, tmp_path):
        cfg = tmp_path / "autoProcess.ini"
        cfg.write_text("[Video]\nvideo-codec = h264, nvenc\n")
        pcm = _make_pcm([str(cfg)])
        logger = MagicMock()
        with patch("subprocess.run", return_value=Mock(returncode=0)):
            _validate_hwaccel(pcm, None, logger)
        info_msgs = [str(c) for c in logger.info.call_args_list]
        assert any("validated OK" in m for m in info_msgs)

    def test_handles_file_not_found_gracefully(self, tmp_path):
        cfg = tmp_path / "autoProcess.ini"
        cfg.write_text("[Video]\nvideo-codec = h264, nvenc\n")
        pcm = _make_pcm([str(cfg)])
        logger = MagicMock()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            _validate_hwaccel(pcm, None, logger)  # must not raise
        logger.warning.assert_called_once()
        assert "not found" in logger.warning.call_args[0][0]

    def test_handles_timeout_expired_gracefully(self, tmp_path):
        cfg = tmp_path / "autoProcess.ini"
        cfg.write_text("[Video]\nvideo-codec = h264, nvenc\n")
        pcm = _make_pcm([str(cfg)])
        logger = MagicMock()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=15)):
            _validate_hwaccel(pcm, None, logger)  # must not raise
        logger.warning.assert_called_once()
        assert "timed out" in logger.warning.call_args[0][0]

    def test_only_validates_each_encoder_once(self, tmp_path):
        cfg1 = tmp_path / "a.ini"
        cfg2 = tmp_path / "b.ini"
        for c in [cfg1, cfg2]:
            c.write_text("[Video]\nvideo-codec = h264, nvenc\n")
        pcm = _make_pcm([str(cfg1), str(cfg2)])
        logger = MagicMock()
        with patch("subprocess.run", return_value=Mock(returncode=0)) as mock_run:
            _validate_hwaccel(pcm, None, logger)
        mock_run.assert_called_once()

    def test_appends_ffmpeg_dir_to_path_env(self, tmp_path):
        cfg = tmp_path / "autoProcess.ini"
        cfg.write_text("[Video]\nvideo-codec = h264, nvenc\n")
        pcm = _make_pcm([str(cfg)])
        logger = MagicMock()
        with patch("subprocess.run", return_value=Mock(returncode=0)) as mock_run:
            _validate_hwaccel(pcm, "/custom/ffmpeg/bin", logger)
        _, kwargs = mock_run.call_args
        assert kwargs["env"]["PATH"].startswith("/custom/ffmpeg/bin")

    def test_skips_software_codec(self, tmp_path):
        cfg = tmp_path / "autoProcess.ini"
        cfg.write_text("[Video]\nvideo-codec = h264\n")
        pcm = _make_pcm([str(cfg)])
        logger = MagicMock()
        with patch("subprocess.run") as mock_run:
            _validate_hwaccel(pcm, None, logger)
        mock_run.assert_not_called()

    def test_skips_copy_codec(self, tmp_path):
        cfg = tmp_path / "autoProcess.ini"
        cfg.write_text("[Video]\nvideo-codec = copy\n")
        pcm = _make_pcm([str(cfg)])
        logger = MagicMock()
        with patch("subprocess.run") as mock_run:
            _validate_hwaccel(pcm, None, logger)
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# TestDaemonServerCancelJob
# ---------------------------------------------------------------------------


class TestDaemonServerCancelJob:
    """Tests for DaemonServer.cancel_job."""

    def test_terminates_process_and_returns_true(self):
        server = _make_server()
        proc = MagicMock()
        server._job_processes[42] = proc
        server.job_db.cancel_job.return_value = True

        result = server.cancel_job(42)

        proc.terminate.assert_called_once()
        server.job_db.cancel_job.assert_called_once_with(42)
        assert result is True

    def test_returns_db_result_when_no_active_process_false(self):
        server = _make_server()
        server.job_db.cancel_job.return_value = False

        result = server.cancel_job(99)

        server.job_db.cancel_job.assert_called_once_with(99)
        assert result is False

    def test_returns_db_result_when_no_active_process_true(self):
        server = _make_server()
        server.job_db.cancel_job.return_value = True

        result = server.cancel_job(99)

        assert result is True

    def test_handles_terminate_exception_without_crashing(self):
        server = _make_server()
        proc = MagicMock()
        proc.terminate.side_effect = OSError("Permission denied")
        server._job_processes[7] = proc
        server.job_db.cancel_job.return_value = True

        result = server.cancel_job(7)  # must not raise
        assert result is True

    def test_calls_db_cancel_after_terminate_exception(self):
        server = _make_server()
        proc = MagicMock()
        proc.terminate.side_effect = RuntimeError("boom")
        server._job_processes[5] = proc
        server.job_db.cancel_job.return_value = True

        server.cancel_job(5)

        server.job_db.cancel_job.assert_called_once_with(5)


# ---------------------------------------------------------------------------
# TestWorkerPool
# ---------------------------------------------------------------------------


class TestWorkerPool:
    """Tests for WorkerPool."""

    def test_creates_correct_number_of_workers(self):
        pool, workers = _make_pool(worker_count=3)
        assert len(pool._workers) == 3

    def test_creates_four_workers_when_requested(self):
        pool, workers = _make_pool(worker_count=4)
        assert len(pool._workers) == 4

    def test_notify_sets_job_event_on_each_worker(self):
        pool, workers = _make_pool(worker_count=3)
        pool.notify()
        for w in workers:
            w.job_event.set.assert_called_once()

    def test_stop_calls_stop_on_each_worker(self):
        pool, workers = _make_pool(worker_count=3)
        pool.stop()
        for w in workers:
            w.stop.assert_called_once()

    def test_drain_joins_each_worker_thread(self):
        pool, workers = _make_pool(worker_count=3)
        pool.drain()
        for w in workers:
            w.join.assert_called_once()

    def test_drain_passes_timeout_to_join(self):
        pool, workers = _make_pool(worker_count=2)
        pool.drain(timeout=10)
        for w in workers:
            w.join.assert_called_once_with(timeout=10)
