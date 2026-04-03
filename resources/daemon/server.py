import configparser
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from http.server import HTTPServer

from resources.daemon.threads import HeartbeatThread, ScannerThread
from resources.daemon.worker import WorkerPool
from resources.log import getLogger

log = getLogger("DAEMON")


class DaemonServer(HTTPServer):
    """HTTP server with job queue and worker threads."""

    def __init__(
        self,
        server_address,
        handler_class,
        job_db,
        path_config_manager,
        config_log_manager,
        config_lock_manager,
        logger,
        worker_count=2,
        api_key=None,
        heartbeat_interval=30,
        stale_seconds=120,
        ffmpeg_dir=None,
        cli_api_key=None,
        cli_ffmpeg_dir=None,
        job_timeout_seconds=0,
    ):
        super().__init__(server_address, handler_class)
        self.job_db = job_db
        self.path_config_manager = path_config_manager
        self.config_log_manager = config_log_manager
        self.config_lock_manager = config_lock_manager
        self.logger = logger
        self.worker_count = worker_count
        self.api_key = api_key
        self.stale_seconds = stale_seconds
        self.node_id = socket.gethostname()
        self.started_at = datetime.now(timezone.utc)
        self._cli_api_key = cli_api_key
        self._cli_ffmpeg_dir = cli_ffmpeg_dir
        self._job_processes = {}  # job_id -> Popen, for cancel support
        self._job_progress = {}  # job_id -> timecode string (e.g. "00:01:23")

        # Start worker threads via WorkerPool — each worker gets its own Event
        # so workers never race to clear a shared flag.
        self.worker_pool = WorkerPool(
            worker_count=worker_count,
            job_db=job_db,
            path_config_manager=path_config_manager,
            config_log_manager=config_log_manager,
            config_lock_manager=config_lock_manager,
            logger=logger,
            ffmpeg_dir=ffmpeg_dir,
            job_timeout_seconds=job_timeout_seconds,
            job_processes=self._job_processes,
            job_progress=self._job_progress,
        )

        # Wake all workers if there are jobs waiting from a previous run.
        pending = job_db.pending_count()
        if pending > 0:
            logger.info("Found %d pending jobs from previous run" % pending)
            self.notify_workers()

        # Start heartbeat thread (only does real work with PostgreSQL backend)
        self.heartbeat_thread = HeartbeatThread(
            job_db=job_db,
            node_id=self.node_id,
            host=server_address[0],
            worker_count=worker_count,
            server=self,
            interval=heartbeat_interval,
            stale_seconds=stale_seconds,
            logger=logger,
            started_at=self.started_at,
        )
        self.heartbeat_thread.start()
        logger.debug("Started heartbeat thread (interval: %ds, stale after: %ds)" % (heartbeat_interval, stale_seconds))

        # Start scanner thread if scan_paths are configured
        self.scanner_thread = ScannerThread(
            scan_paths=path_config_manager.scan_paths,
            job_db=job_db,
            server=self,
            path_config_manager=path_config_manager,
            logger=logger,
        )
        self.scanner_thread.start()

    def notify_workers(self):
        """Wake all worker threads by setting each worker's individual event."""
        self.worker_pool.notify()

    def cancel_job(self, job_id):
        """Cancel a job by terminating its process (if running) and updating the DB.

        Returns True if the job was cancelled (either by killing a running process
        or by marking a pending job as cancelled in the database).
        """
        # Terminate the subprocess if it is currently running
        process = self._job_processes.get(job_id)
        if process is not None:
            try:
                process.terminate()
            except Exception:
                pass
            # Also update DB status; the worker's finally block will clean up
            self.job_db.cancel_job(job_id)
            return True
        # Job is not actively running — mark it cancelled in DB if still pending
        return self.job_db.cancel_job(job_id)

    def reload_config(self):
        """Reload daemon.json in-place without stopping workers or active conversions."""
        if not self.path_config_manager._config_file:
            self.logger.warning("No daemon config file to reload.")
            return

        self.logger.info("Reloading configuration from %s..." % self.path_config_manager._config_file)

        # Reset mutable collections before re-loading so stale entries are cleared
        self.path_config_manager.path_configs = []
        self.path_config_manager.path_rewrites = []
        self.path_config_manager.scan_paths = []
        self.path_config_manager.load_config(self.path_config_manager._config_file)

        # Re-apply api_key priority: CLI arg > env var > config file
        self.api_key = self._cli_api_key or os.environ.get("SMA_DAEMON_API_KEY") or self.path_config_manager.api_key

        # Re-apply ffmpeg_dir priority: CLI arg > env var > config file
        new_ffmpeg_dir = self._cli_ffmpeg_dir or os.environ.get("SMA_DAEMON_FFMPEG_DIR") or self.path_config_manager.ffmpeg_dir
        for worker in self.worker_pool._workers:
            worker.ffmpeg_dir = new_ffmpeg_dir

        # Restart scanner thread with updated scan_paths
        self.scanner_thread.stop()
        self.scanner_thread.join(timeout=5)
        self.scanner_thread = ScannerThread(
            scan_paths=self.path_config_manager.scan_paths,
            job_db=self.job_db,
            server=self,
            path_config_manager=self.path_config_manager,
            logger=self.logger,
        )
        self.scanner_thread.start()

        self.logger.info("Configuration reloaded.")

    def graceful_restart(self):
        """Drain active conversions then re-exec the daemon process."""
        self.logger.info("Graceful restart — waiting for active conversions to finish...")

        self.worker_pool.stop()
        self.heartbeat_thread.stop()
        self.scanner_thread.stop()

        active = [w for w in self.worker_pool._workers if w.is_alive()]
        while active:
            names = [str(w.worker_id) for w in active if w.current_job_id]
            if names:
                self.logger.info("Waiting for worker(s) %s to finish..." % ", ".join(names))
            for w in active:
                w.join(timeout=10)
            active = [w for w in active if w.is_alive()]

        self.logger.info("All workers finished, restarting...")

        if self.job_db.is_distributed:
            try:
                self.job_db.mark_node_offline(self.node_id)
            except Exception:
                pass

        self.heartbeat_thread.join(timeout=5)
        self.scanner_thread.join(timeout=5)

        super().shutdown()

        os.execv(sys.executable, [sys.executable] + sys.argv)

    def shutdown(self):
        self.logger.info("Shutting down — waiting for active conversions to finish...")

        # Stop workers from picking up new jobs
        self.worker_pool.stop()
        self.heartbeat_thread.stop()
        self.scanner_thread.stop()

        # Wait for in-progress conversions to complete
        active = [w for w in self.worker_pool._workers if w.is_alive()]
        while active:
            names = [str(w.worker_id) for w in active if w.current_job_id]
            if names:
                self.logger.info("Waiting for worker(s) %s to finish..." % ", ".join(names))
            for w in active:
                w.join(timeout=10)
            active = [w for w in active if w.is_alive()]

        self.logger.info("All workers finished, shutting down.")

        # Mark this node offline in the cluster table on clean shutdown
        if self.job_db.is_distributed:
            try:
                self.job_db.mark_node_offline(self.node_id)
            except Exception:
                pass

        self.heartbeat_thread.join(timeout=5)
        self.scanner_thread.join(timeout=5)

        super().shutdown()


def _validate_hwaccel(path_config_manager, ffmpeg_dir, logger):
    """Probe hardware encoder availability for each unique config at startup.

    For each config that requests an hwaccel codec (nvenc, qsv, vaapi,
    videotoolbox), runs a quick ffmpeg null-encode and logs a warning if the
    encoder is not available. Does not block server startup.
    """
    _hwaccel_map = {
        "nvenc": "h264_nvenc",
        "qsv": "h264_qsv",
        "vaapi": "h264_vaapi",
        "videotoolbox": "h264_videotoolbox",
    }

    env = os.environ.copy()
    if ffmpeg_dir:
        env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")

    seen = set()
    for config_path in path_config_manager.get_all_configs():
        if not os.path.exists(config_path):
            continue
        try:
            cp = configparser.ConfigParser()
            cp.read(config_path)
            codec_val = cp.get("Video", "video-codec", fallback="").strip().lower()
        except Exception:
            continue

        for keyword, encoder in _hwaccel_map.items():
            if keyword in codec_val and encoder not in seen:
                seen.add(encoder)
                try:
                    result = subprocess.run(
                        ["ffmpeg", "-f", "lavfi", "-i", "nullsrc", "-t", "0.1", "-c:v", encoder, "-f", "null", "-", "-loglevel", "error"],
                        capture_output=True,
                        env=env,
                        timeout=15,
                    )
                    if result.returncode != 0:
                        logger.warning(
                            "Hardware encoder '%s' (from config %s) does not appear to be available. Conversions may fail. Check driver/SDK installation." % (encoder, os.path.basename(config_path))
                        )
                    else:
                        logger.info("Hardware encoder '%s' validated OK" % encoder)
                except FileNotFoundError:
                    logger.warning("ffmpeg not found in PATH — cannot validate hardware encoder '%s'" % encoder)
                except subprocess.TimeoutExpired:
                    logger.warning("Hardware encoder probe for '%s' timed out" % encoder)
                except Exception as exc:
                    logger.warning("Hardware encoder probe for '%s' failed: %s" % (encoder, exc))
