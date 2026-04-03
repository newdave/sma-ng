import json
import os
import re as _re
import socket
import subprocess
import sys
import threading
from datetime import datetime

from resources.daemon.constants import SCRIPT_DIR
from resources.log import getLogger

log = getLogger("DAEMON")


class ConversionWorker(threading.Thread):
    """Background worker thread that processes conversion jobs from the database."""

    def __init__(self, worker_id, job_db, path_config_manager, config_log_manager, config_lock_manager, logger, ffmpeg_dir=None, job_timeout_seconds=0, job_processes=None, job_progress=None):
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self.node_id = socket.gethostname()
        self.job_db = job_db
        self.job_event = threading.Event()  # per-worker event; set by notify_workers()
        self.path_config_manager = path_config_manager
        self.config_log_manager = config_log_manager
        self.config_lock_manager = config_lock_manager
        self.log = logger
        self.script_path = os.path.join(SCRIPT_DIR, "manual.py")
        self.ffmpeg_dir = ffmpeg_dir
        self.job_timeout_seconds = job_timeout_seconds  # 0 means no timeout
        self.running = True
        self.current_job_id = None
        self._job_processes = job_processes if job_processes is not None else {}
        self._job_progress = job_progress if job_progress is not None else {}

    def stop(self):
        """Signal worker to stop."""
        self.running = False
        self.job_event.set()

    def run(self):
        while self.running:
            # Wait for a wakeup on this worker's own event or periodic timeout.
            self.job_event.wait(timeout=5.0)
            self.job_event.clear()

            if not self.running:
                break

            # Drain all available jobs before going back to sleep.
            while self.running:
                locked = self.config_lock_manager.get_locked_configs()
                job = self.job_db.claim_next_job(self.worker_id, self.node_id, exclude_configs=locked or None)
                if job:
                    self.process_job(job)
                else:
                    break

    def process_job(self, job):
        job_id = job["id"]
        self.current_job_id = job_id
        path = job["path"]
        args = json.loads(job["args"]) if job["args"] else []
        config_file = job["config"]

        if not os.path.exists(path):
            self.log.error("Job %d: Path does not exist: %s" % (job_id, path))
            self.job_db.fail_job(job_id, "Path does not exist")
            return

        # Job is already marked running by claim_next_job()

        # Check if job was cancelled before we even start (e.g. cancelled while pending)
        current = self.job_db.get_job(job_id)
        if current and current.get("status") == "cancelled":
            self.log.info("Job %d was cancelled before processing started" % job_id)
            self.current_job_id = None
            return

        # Acquire lock for this config (blocks if another job is using it)
        self.log.info("Worker %d acquiring lock for job %d: %s" % (self.worker_id, job_id, os.path.basename(config_file)))
        self.config_lock_manager.acquire(config_file, job_id, path)

        # Check again after acquiring lock (may have been cancelled while waiting)
        current = self.job_db.get_job(job_id)
        if current and current.get("status") == "cancelled":
            self.log.info("Job %d was cancelled while waiting for lock" % job_id)
            self.config_lock_manager.release(config_file, job_id)
            self.current_job_id = None
            return

        try:
            success = self._run_conversion(job_id, path, config_file, args)
            if success:
                self.job_db.complete_job(job_id)
            else:
                # Don't overwrite a cancelled status set during conversion
                current = self.job_db.get_job(job_id)
                if current and current.get("status") != "cancelled":
                    self.job_db.fail_job(job_id, "Conversion process failed")
        except Exception as e:
            self.log.exception("Job %d failed: %s" % (job_id, e))
            current = self.job_db.get_job(job_id)
            if current and current.get("status") != "cancelled":
                self.job_db.fail_job(job_id, str(e))
        finally:
            self.config_lock_manager.release(config_file, job_id)
            self.current_job_id = None

    def _run_conversion(self, job_id, path, config_file, extra_args):
        """Run the actual conversion process. Returns True on success."""
        config_logger = self.config_log_manager.get_logger(config_file)
        log_file = self.config_log_manager.get_log_file(config_file)

        self.log.info("Worker %d processing job %d: %s" % (self.worker_id, job_id, path))
        self.log.info("Using config: %s (log: %s)" % (config_file, log_file))

        config_logger.info("=" * 60)
        config_logger.info("Job %d started: %s" % (job_id, path))
        config_logger.info("Config: %s" % config_file)
        config_logger.info("Worker: %d" % self.worker_id)
        config_logger.info("Timestamp: %s" % datetime.now().isoformat())
        config_logger.info("=" * 60)

        cmd = [sys.executable, self.script_path, "-a", "-i", path, "-c", config_file] + extra_args

        env = os.environ.copy()
        if self.ffmpeg_dir:
            env["PATH"] = self.ffmpeg_dir + os.pathsep + env.get("PATH", "")

        _ffmpeg_time_re = _re.compile(r"time=(\d+:\d+:\d+)")

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            self._job_processes[job_id] = process

            for line in process.stdout:
                line = line.strip()
                if line:
                    config_logger.info(line)
                    self.log.info("[%s] %s" % (os.path.basename(config_file), line))
                    m = _ffmpeg_time_re.search(line)
                    if m:
                        self._job_progress[job_id] = m.group(1)

            try:
                timeout = self.job_timeout_seconds if self.job_timeout_seconds > 0 else None
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                msg = "Job %d timed out after %ds: %s" % (job_id, self.job_timeout_seconds, path)
                self.log.error(msg)
                config_logger.error(msg)
                return False

            if process.returncode == 0:
                msg = "Job %d completed successfully: %s" % (job_id, path)
                self.log.info(msg)
                config_logger.info(msg)
                return True
            else:
                msg = "Job %d exited with code %d: %s" % (job_id, process.returncode, path)
                self.log.error(msg)
                config_logger.error(msg)
                return False

        except Exception as e:
            msg = "Job %d failed: %s" % (job_id, e)
            self.log.exception(msg)
            config_logger.exception(msg)
            return False
        finally:
            self._job_processes.pop(job_id, None)
            self._job_progress.pop(job_id, None)
            config_logger.info("Job %d finished: %s" % (job_id, path))
            config_logger.info("")


class WorkerPool:
    """Manages a pool of ConversionWorker threads."""

    def __init__(self, worker_count, job_db, path_config_manager, config_log_manager, config_lock_manager, logger, ffmpeg_dir=None, job_timeout_seconds=0, job_processes=None, job_progress=None):
        self._workers = []
        self._worker_count = worker_count
        self._job_db = job_db
        self._path_config_manager = path_config_manager
        self._config_log_manager = config_log_manager
        self._config_lock_manager = config_lock_manager
        self._logger = logger
        self._ffmpeg_dir = ffmpeg_dir
        self._job_timeout_seconds = job_timeout_seconds
        self._job_processes = job_processes if job_processes is not None else {}
        self._job_progress = job_progress if job_progress is not None else {}
        self._start_workers()

    def _start_workers(self):
        for i in range(self._worker_count):
            worker = ConversionWorker(
                worker_id=i + 1,
                job_db=self._job_db,
                path_config_manager=self._path_config_manager,
                config_log_manager=self._config_log_manager,
                config_lock_manager=self._config_lock_manager,
                logger=self._logger,
                ffmpeg_dir=self._ffmpeg_dir,
                job_timeout_seconds=self._job_timeout_seconds,
                job_processes=self._job_processes,
                job_progress=self._job_progress,
            )
            worker.start()
            self._workers.append(worker)
            self._logger.debug("Started worker thread %d" % (i + 1))

    def notify(self):
        """Wake all workers."""
        for worker in self._workers:
            worker.job_event.set()

    def stop(self):
        """Signal all workers to stop."""
        for worker in self._workers:
            worker.stop()

    def drain(self, timeout=None):
        """Wait for all workers to finish (used during shutdown/restart)."""
        for worker in self._workers:
            worker.join(timeout=timeout)

    def restart(self, ffmpeg_dir=None, job_timeout_seconds=None):
        """Stop all workers and start fresh ones."""
        if ffmpeg_dir is not None:
            self._ffmpeg_dir = ffmpeg_dir
        if job_timeout_seconds is not None:
            self._job_timeout_seconds = job_timeout_seconds
        self.stop()
        self._workers = []
        self._start_workers()
