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


# ---------------------------------------------------------------------------
# TestDaemonServerInit
# ---------------------------------------------------------------------------


def _make_full_server(worker_count=1, pending_count=0, api_key=None):
    """Build a DaemonServer with all threads and WorkerPool patched."""
    job_db = MagicMock()
    job_db.pending_count.return_value = pending_count
    job_db.is_distributed = False
    pcm = MagicMock()
    pcm.scan_paths = []
    pcm.recycle_bin_max_age_days = 0
    pcm.recycle_bin_min_free_gb = 0
    logger = MagicMock()

    with (
        patch("resources.daemon.server.WorkerPool") as MockPool,
        patch("resources.daemon.server.HeartbeatThread") as MockHB,
        patch("resources.daemon.server.ScannerThread") as MockScan,
        patch("resources.daemon.server.RecycleBinCleanerThread") as MockRBC,
        patch("http.server.HTTPServer.__init__", return_value=None),
    ):
        mock_pool = MagicMock()
        MockPool.return_value = mock_pool
        mock_hb = MagicMock()
        MockHB.return_value = mock_hb
        mock_scan = MagicMock()
        MockScan.return_value = mock_scan
        mock_rbc = MagicMock()
        MockRBC.return_value = mock_rbc

        server = DaemonServer(
            server_address=("127.0.0.1", 8585),
            handler_class=MagicMock(),
            job_db=job_db,
            path_config_manager=pcm,
            config_log_manager=MagicMock(),
            config_lock_manager=MagicMock(),
            logger=logger,
            worker_count=worker_count,
            api_key=api_key,
        )

    return server, mock_pool, mock_hb, mock_scan, mock_rbc, job_db, logger


class TestDaemonServerInit:
    def test_worker_pool_created(self):
        server, pool, *_ = _make_full_server(worker_count=2)
        assert server.worker_pool is pool

    def test_heartbeat_thread_started(self):
        server, _, hb, *_ = _make_full_server()
        hb.start.assert_called_once()

    def test_scanner_thread_started(self):
        server, _, _, scan, *_ = _make_full_server()
        scan.start.assert_called_once()

    def test_recycle_cleaner_thread_started(self):
        server, _, _, _, rbc, *_ = _make_full_server()
        rbc.start.assert_called_once()

    def test_api_key_stored(self):
        server, *_ = _make_full_server(api_key="secret")
        assert server.api_key == "secret"

    def test_notifies_workers_when_pending_jobs_exist(self):
        server, pool, *_ = _make_full_server(pending_count=5)
        pool.notify.assert_called_once()

    def test_no_notify_when_no_pending_jobs(self):
        server, pool, *_ = _make_full_server(pending_count=0)
        pool.notify.assert_not_called()


# ---------------------------------------------------------------------------
# TestDaemonServerNotifyWorkers
# ---------------------------------------------------------------------------


class TestDaemonServerNotifyWorkers:
    def test_notify_workers_delegates_to_pool(self):
        server = _make_server()
        server.worker_pool = MagicMock()
        server.notify_workers()
        server.worker_pool.notify.assert_called_once()


# ---------------------------------------------------------------------------
# TestDaemonServerReloadConfig
# ---------------------------------------------------------------------------


class TestDaemonServerReloadConfig:
    def _make_reloadable_server(self, tmp_path):
        cfg = tmp_path / "daemon.json"
        cfg.write_text('{"default_config": "config/autoProcess.ini"}')

        job_db = MagicMock()
        job_db.pending_count.return_value = 0
        job_db.is_distributed = False
        pcm = MagicMock()
        pcm.scan_paths = []
        pcm.recycle_bin_max_age_days = 0
        pcm.recycle_bin_min_free_gb = 0
        pcm._config_file = str(cfg)
        pcm.api_key = None
        pcm.basic_auth = None
        pcm.ffmpeg_dir = None
        logger = MagicMock()

        with (
            patch("resources.daemon.server.WorkerPool") as MockPool,
            patch("resources.daemon.server.HeartbeatThread") as MockHB,
            patch("resources.daemon.server.ScannerThread") as MockScan,
            patch("resources.daemon.server.RecycleBinCleanerThread") as MockRBC,
            patch("http.server.HTTPServer.__init__", return_value=None),
        ):
            MockPool.return_value = MagicMock()
            mock_hb = MagicMock()
            MockHB.return_value = mock_hb
            mock_scan = MagicMock()
            MockScan.return_value = mock_scan
            mock_rbc = MagicMock()
            MockRBC.return_value = mock_rbc

            server = DaemonServer(
                server_address=("127.0.0.1", 8585),
                handler_class=MagicMock(),
                job_db=job_db,
                path_config_manager=pcm,
                config_log_manager=MagicMock(),
                config_lock_manager=MagicMock(),
                logger=logger,
            )
            # capture mocks created during init for verification
            server._init_scan = mock_scan
            server._init_rbc = mock_rbc

        return server, pcm, logger

    def test_reload_logs_warning_when_no_config_file(self, tmp_path):
        server, pcm, logger = self._make_reloadable_server(tmp_path)
        pcm._config_file = None
        server.reload_config()
        logger.warning.assert_called()

    def test_reload_calls_load_config(self, tmp_path):
        server, pcm, _ = self._make_reloadable_server(tmp_path)
        with patch("resources.daemon.server.ScannerThread"), patch("resources.daemon.server.RecycleBinCleanerThread"):
            server.reload_config()
        pcm.load_config.assert_called_once()

    def test_reload_restarts_scanner_thread(self, tmp_path):
        server, pcm, _ = self._make_reloadable_server(tmp_path)
        old_scanner = server.scanner_thread
        with patch("resources.daemon.server.ScannerThread") as MockScan, patch("resources.daemon.server.RecycleBinCleanerThread"):
            new_scanner = MagicMock()
            MockScan.return_value = new_scanner
            server.reload_config()
        old_scanner.stop.assert_called_once()
        new_scanner.start.assert_called_once()
        assert server.scanner_thread is new_scanner

    def test_reload_restarts_recycle_cleaner_thread(self, tmp_path):
        server, pcm, _ = self._make_reloadable_server(tmp_path)
        old_cleaner = server.recycle_cleaner_thread
        with patch("resources.daemon.server.ScannerThread"), patch("resources.daemon.server.RecycleBinCleanerThread") as MockRBC:
            new_cleaner = MagicMock()
            MockRBC.return_value = new_cleaner
            server.reload_config()
        old_cleaner.stop.assert_called_once()
        new_cleaner.start.assert_called_once()

    def test_reload_applies_cli_api_key_over_config(self, tmp_path):
        server, pcm, _ = self._make_reloadable_server(tmp_path)
        server._cli_api_key = "cli-key"
        pcm.api_key = "config-key"
        with patch("resources.daemon.server.ScannerThread"), patch("resources.daemon.server.RecycleBinCleanerThread"), patch.dict("os.environ", {}, clear=True):
            server.reload_config()
        assert server.api_key == "cli-key"

    def test_reload_applies_env_api_key_when_no_cli_key(self, tmp_path):
        server, pcm, _ = self._make_reloadable_server(tmp_path)
        server._cli_api_key = None
        pcm.api_key = "config-key"
        with patch("resources.daemon.server.ScannerThread"), patch("resources.daemon.server.RecycleBinCleanerThread"), patch.dict("os.environ", {"SMA_DAEMON_API_KEY": "env-key"}):
            server.reload_config()
        assert server.api_key == "env-key"


# ---------------------------------------------------------------------------
# TestDaemonServerShutdown
# ---------------------------------------------------------------------------


class TestDaemonServerShutdown:
    def _make_shutdown_server(self):
        server = _make_server()
        mock_pool = MagicMock()
        mock_pool._workers = []
        server.worker_pool = mock_pool
        server.heartbeat_thread = MagicMock()
        server.scanner_thread = MagicMock()
        server.recycle_cleaner_thread = MagicMock()
        server.logger = MagicMock()
        return server

    def test_shutdown_stops_worker_pool(self):
        server = self._make_shutdown_server()
        with patch("http.server.HTTPServer.shutdown"):
            server.shutdown()
        server.worker_pool.stop.assert_called_once()

    def test_shutdown_stops_heartbeat_thread(self):
        server = self._make_shutdown_server()
        with patch("http.server.HTTPServer.shutdown"):
            server.shutdown()
        server.heartbeat_thread.stop.assert_called_once()

    def test_shutdown_stops_scanner_thread(self):
        server = self._make_shutdown_server()
        with patch("http.server.HTTPServer.shutdown"):
            server.shutdown()
        server.scanner_thread.stop.assert_called_once()

    def test_shutdown_joins_heartbeat_and_scanner(self):
        server = self._make_shutdown_server()
        with patch("http.server.HTTPServer.shutdown"):
            server.shutdown()
        server.heartbeat_thread.join.assert_called()
        server.scanner_thread.join.assert_called()

    def test_shutdown_marks_node_offline_when_distributed(self):
        server = self._make_shutdown_server()
        server.job_db.is_distributed = True
        server.node_id = "mynode"
        with patch("http.server.HTTPServer.shutdown"):
            server.shutdown()
        server.job_db.mark_node_offline.assert_called_once_with("mynode")

    def test_shutdown_skips_mark_offline_when_not_distributed(self):
        server = self._make_shutdown_server()
        server.job_db.is_distributed = False
        with patch("http.server.HTTPServer.shutdown"):
            server.shutdown()
        server.job_db.mark_node_offline.assert_not_called()

    def test_shutdown_handles_mark_offline_exception(self):
        server = self._make_shutdown_server()
        server.job_db.is_distributed = True
        server.node_id = "node"
        server.job_db.mark_node_offline.side_effect = RuntimeError("db gone")
        with patch("http.server.HTTPServer.shutdown"):
            server.shutdown()  # must not raise

    def test_shutdown_waits_for_active_workers(self):
        server = self._make_shutdown_server()
        alive_worker = MagicMock()
        alive_worker.is_alive.side_effect = [True, False]
        alive_worker.current_job_id = 42
        server.worker_pool._workers = [alive_worker]
        with patch("http.server.HTTPServer.shutdown"):
            server.shutdown()
        alive_worker.join.assert_called()


# ---------------------------------------------------------------------------
# TestDaemonServerGracefulRestart
# ---------------------------------------------------------------------------


class TestDaemonServerGracefulRestart:
    def _make_restart_server(self):
        server = _make_server()
        mock_pool = MagicMock()
        mock_pool._workers = []
        server.worker_pool = mock_pool
        server.heartbeat_thread = MagicMock()
        server.scanner_thread = MagicMock()
        server.recycle_cleaner_thread = MagicMock()
        server.logger = MagicMock()
        server.node_id = "node"
        return server

    def test_graceful_restart_stops_all_components(self):
        server = self._make_restart_server()
        with patch("http.server.HTTPServer.shutdown"), patch("os.execv"):
            server.graceful_restart()
        server.worker_pool.stop.assert_called_once()
        server.heartbeat_thread.stop.assert_called_once()
        server.scanner_thread.stop.assert_called_once()

    def test_graceful_restart_calls_execv(self):
        server = self._make_restart_server()
        with patch("http.server.HTTPServer.shutdown"), patch("os.execv") as mock_execv, patch("sys.executable", "/usr/bin/python3"), patch("sys.argv", ["daemon.py", "--port", "8585"]):
            server.graceful_restart()
        mock_execv.assert_called_once()
        args = mock_execv.call_args[0]
        assert args[0] == "/usr/bin/python3"

    def test_graceful_restart_marks_node_offline_when_distributed(self):
        server = self._make_restart_server()
        server.job_db.is_distributed = True
        with patch("http.server.HTTPServer.shutdown"), patch("os.execv"):
            server.graceful_restart()
        server.job_db.mark_node_offline.assert_called_once_with("node")

    def test_graceful_restart_handles_mark_offline_exception(self):
        server = self._make_restart_server()
        server.job_db.is_distributed = True
        server.job_db.mark_node_offline.side_effect = RuntimeError("db gone")
        with patch("http.server.HTTPServer.shutdown"), patch("os.execv"):
            server.graceful_restart()  # must not raise


# ---------------------------------------------------------------------------
# TestValidateHwaccel — additional edge cases
# ---------------------------------------------------------------------------


class TestValidateHwaccelExtra:
    def test_handles_generic_exception_gracefully(self, tmp_path):
        cfg = tmp_path / "autoProcess.ini"
        cfg.write_text("[Video]\nvideo-codec = h264, nvenc\n")
        pcm = _make_pcm([str(cfg)])
        logger = MagicMock()
        with patch("subprocess.run", side_effect=ValueError("unexpected")):
            _validate_hwaccel(pcm, None, logger)  # must not raise
        logger.warning.assert_called_once()
        assert "failed" in logger.warning.call_args[0][0]

    def test_validates_vaapi_encoder(self, tmp_path):
        cfg = tmp_path / "autoProcess.ini"
        cfg.write_text("[Video]\nvideo-codec = h264, vaapi\n")
        pcm = _make_pcm([str(cfg)])
        logger = MagicMock()
        with patch("subprocess.run", return_value=Mock(returncode=0)) as mock_run:
            _validate_hwaccel(pcm, None, logger)
        called_args = mock_run.call_args[0][0]
        assert "h264_vaapi" in called_args

    def test_validates_qsv_encoder(self, tmp_path):
        cfg = tmp_path / "autoProcess.ini"
        cfg.write_text("[Video]\nvideo-codec = h264, qsv\n")
        pcm = _make_pcm([str(cfg)])
        logger = MagicMock()
        with patch("subprocess.run", return_value=Mock(returncode=0)) as mock_run:
            _validate_hwaccel(pcm, None, logger)
        called_args = mock_run.call_args[0][0]
        assert "h264_qsv" in called_args

    def test_validates_videotoolbox_encoder(self, tmp_path):
        cfg = tmp_path / "autoProcess.ini"
        cfg.write_text("[Video]\nvideo-codec = h264, videotoolbox\n")
        pcm = _make_pcm([str(cfg)])
        logger = MagicMock()
        with patch("subprocess.run", return_value=Mock(returncode=0)) as mock_run:
            _validate_hwaccel(pcm, None, logger)
        called_args = mock_run.call_args[0][0]
        assert "h264_videotoolbox" in called_args

    def test_skips_nonexistent_config_files(self, tmp_path):
        pcm = _make_pcm(["/nonexistent/config.ini"])
        logger = MagicMock()
        with patch("subprocess.run") as mock_run:
            _validate_hwaccel(pcm, None, logger)
        mock_run.assert_not_called()

    def test_skips_unreadable_config_gracefully(self, tmp_path):
        cfg = tmp_path / "bad.ini"
        cfg.write_text("not valid ini [[[")
        pcm = _make_pcm([str(cfg)])
        logger = MagicMock()
        with patch("subprocess.run") as mock_run:
            _validate_hwaccel(pcm, None, logger)
        mock_run.assert_not_called()
