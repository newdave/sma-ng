import json
import os
import re as _re
import socket
import subprocess
import sys
import threading
import time

from resources.daemon.constants import SCRIPT_DIR
from resources.daemon.context import clear_job_id, set_job_id
from resources.log import getLogger

log = getLogger("DAEMON")

# Compiled once at module load; reused for every line of every job's output.
_FFMPEG_DURATION_RE = _re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")
_FFMPEG_TIME_RE = _re.compile(r"time=(\d+:\d+:\d+)")
_FFMPEG_PROGRESS_RE = _re.compile(r"\bframe=\s*\d+\b")
_FFMPEG_FPS_RE = _re.compile(r"\bfps=\s*([\d.]+)")
_FFMPEG_SPEED_RE = _re.compile(r"\bspeed=\s*([\d.]+)x")
_DEFAULT_PROGRESS_LOG_INTERVAL = 60  # seconds between progress log entries


def _hms_to_seconds(h, m, s):
    return int(h) * 3600 + int(m) * 60 + float(s)


def _seconds_to_hms(secs):
    secs = max(0, int(secs))
    h, rem = divmod(secs, 3600)
    m, sec = divmod(rem, 60)
    return "%02d:%02d:%02d" % (h, m, sec)


class ConversionWorker(threading.Thread):
    """Background worker thread that processes conversion jobs from the database."""

    def __init__(
        self,
        worker_id,
        job_db,
        path_config_manager,
        config_log_manager,
        config_lock_manager,
        logger,
        ffmpeg_dir=None,
        job_timeout_seconds=0,
        progress_log_interval=_DEFAULT_PROGRESS_LOG_INTERVAL,
        job_processes=None,
        job_progress=None,
    ):
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
        self.progress_log_interval = progress_log_interval
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
        config_file = job["config"]

        try:
            args = json.loads(job["args"]) if job["args"] else []
        except (TypeError, ValueError) as e:
            self.log.error("Job %d has invalid args payload: %s" % (job_id, e))
            self.job_db.fail_job(job_id, "Invalid job args")
            self.current_job_id = None
            return

        if not os.path.exists(path):
            self.log.error("Job %d: Path does not exist: %s" % (job_id, path))
            self.job_db.fail_job(job_id, "Path does not exist")
            self.current_job_id = None
            return

        # Job is already marked running by claim_next_job()

        # Check if job was cancelled before we even start (e.g. cancelled while pending)
        current = self.job_db.get_job(job_id)
        if current and current.get("status") == "cancelled":
            self.log.info("Job %d was cancelled before processing started" % job_id)
            self.current_job_id = None
            return

        # Acquire lock for this config (blocks if another job is using it)
        self.log.debug("Worker %d acquiring lock for job %d: %s" % (self.worker_id, job_id, os.path.basename(config_file)))
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
        token = set_job_id(job_id)
        try:
            return self._run_conversion_inner(job_id, path, config_file, extra_args)
        finally:
            clear_job_id(token)

    def _run_conversion_inner(self, job_id, path, config_file, extra_args):
        """Inner conversion logic — job_id context is already set by _run_conversion."""
        config_logger = self.config_log_manager.get_logger(config_file)
        log_file = self.config_log_manager.get_log_file(config_file)

        self.log.info(
            "Worker %d processing job %d: %s" % (self.worker_id, job_id, path),
            extra={"worker_id": self.worker_id, "path": path, "config": os.path.basename(config_file)},
        )
        self.log.debug("Using config: %s (log: %s)" % (config_file, log_file))

        config_logger.info(
            "Job %d started" % job_id,
            extra={"job_id": job_id, "path": path, "config": config_file, "worker_id": self.worker_id},
        )

        cmd = [sys.executable, self.script_path, "-a", "-i", path, "-c", config_file] + extra_args

        env = os.environ.copy()
        if self.ffmpeg_dir:
            env["PATH"] = self.ffmpeg_dir + os.pathsep + env.get("PATH", "")

        start_time = time.monotonic()
        total_duration_secs = None
        _last_progress_log: float = 0.0

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
                if not line:
                    continue
                # Parse total duration from FFmpeg's initial output line
                if total_duration_secs is None:
                    dm = _FFMPEG_DURATION_RE.search(line)
                    if dm:
                        total_duration_secs = _hms_to_seconds(dm.group(1), dm.group(2), dm.group(3))
                tm = _FFMPEG_TIME_RE.search(line)
                # Throttle FFmpeg progress lines; log all other output immediately.
                if _FFMPEG_PROGRESS_RE.search(line):
                    now = time.monotonic()
                    progress = self._build_progress_payload(line, tm, total_duration_secs, start_time, now)
                    if progress:
                        self._job_progress[job_id] = progress
                    if now - _last_progress_log >= self.progress_log_interval:
                        if progress:
                            config_logger.info("Progress: %s" % json.dumps(progress))
                        _last_progress_log = now
                else:
                    config_logger.info(line)

            try:
                timeout = self.job_timeout_seconds if self.job_timeout_seconds > 0 else None
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                config_logger.error("Job %d timed out after %ds: %s" % (job_id, self.job_timeout_seconds, path))
                return False

            if process.returncode == 0:
                config_logger.info("Job %d completed successfully: %s" % (job_id, path))
                return True
            else:
                config_logger.error("Job %d exited with code %d: %s" % (job_id, process.returncode, path))
                return False

        except Exception as e:
            config_logger.exception("Job %d failed: %s" % (job_id, e))
            return False
        finally:
            self._job_processes.pop(job_id, None)
            self._job_progress.pop(job_id, None)
            config_logger.info("Job %d finished: %s" % (job_id, path))
            config_logger.info("")

    def _build_progress_payload(self, line, time_match, total_duration_secs, start_time, now):
        elapsed_secs = now - start_time
        progress = {"elapsed": _seconds_to_hms(elapsed_secs)}

        if time_match:
            timecode = time_match.group(1)
            progress["timecode"] = timecode
        else:
            timecode = None

        fps_m = _FFMPEG_FPS_RE.search(line)
        if fps_m:
            progress["fps"] = round(float(fps_m.group(1)), 1)

        speed_m = _FFMPEG_SPEED_RE.search(line)
        speed = float(speed_m.group(1)) if speed_m else None
        if speed and speed > 0:
            progress["speed"] = "%.2fx" % speed

        if timecode and total_duration_secs and total_duration_secs > 0:
            parts = timecode.split(":")
            current_secs = _hms_to_seconds(parts[0], parts[1], parts[2])
            pct = min(100.0, current_secs / total_duration_secs * 100.0)
            progress["percent"] = round(pct, 1)

            if speed and speed > 0:
                remaining_secs = (total_duration_secs - current_secs) / speed
            elif elapsed_secs > 0 and pct > 0:
                remaining_secs = elapsed_secs * (100.0 - pct) / pct
            else:
                remaining_secs = None

            if remaining_secs is not None:
                progress["remaining"] = _seconds_to_hms(remaining_secs)

        return progress


class WorkerPool:
    """Manages a pool of ConversionWorker threads."""

    def __init__(
        self,
        worker_count,
        job_db,
        path_config_manager,
        config_log_manager,
        config_lock_manager,
        logger,
        ffmpeg_dir=None,
        job_timeout_seconds=0,
        progress_log_interval=_DEFAULT_PROGRESS_LOG_INTERVAL,
        job_processes=None,
        job_progress=None,
    ):
        self._workers = []
        self._worker_count = worker_count
        self._job_db = job_db
        self._path_config_manager = path_config_manager
        self._config_log_manager = config_log_manager
        self._config_lock_manager = config_lock_manager
        self._logger = logger
        self._ffmpeg_dir = ffmpeg_dir
        self._job_timeout_seconds = job_timeout_seconds
        self._progress_log_interval = progress_log_interval
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
                progress_log_interval=self._progress_log_interval,
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
