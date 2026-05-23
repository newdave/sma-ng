import glob
import json
import os
import re as _re
import shutil
import subprocess
import sys
import threading
import time

from resources.daemon import metrics_prom
from resources.daemon.constants import FFMPEG_STDERR_DIR, SCRIPT_DIR, resolve_node_id
from resources.daemon.context import clear_job_id, set_job_id
from resources.log import getLogger
from resources.processor.failures import (
  WORKER_SENTINEL_DISK_PRESSURE,
  WORKER_SENTINEL_EXCEPTION,
  WORKER_SENTINEL_INVALID_ARGS,
  WORKER_SENTINEL_PATH_MISSING,
  WORKER_SENTINEL_PROCESS_FAILED,
  FailureCategory,
  categorize_failure,
)

log = getLogger("DAEMON")

# Compiled once at module load; reused for every line of every job's output.
_FFMPEG_DURATION_RE = _re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")
_FFMPEG_TIME_RE = _re.compile(r"time=(\d+:\d+:\d+)")
_FFMPEG_PROGRESS_RE = _re.compile(r"\bframe=\s*\d+\b")
_FFMPEG_FPS_RE = _re.compile(r"\bfps=\s*([\d.]+)")
_FFMPEG_SPEED_RE = _re.compile(r"\bspeed=\s*([\d.]+)x")
_FFMPEG_BITRATE_RE = _re.compile(r"\bbitrate=\s*([\d.]+)\s*kbits/s")
# Marker emitted by manual.py:processFile on success — value is the final
# output path after local rename, restoreFromOutput, and any arr rename.
_SMA_FINAL_OUTPUT_RE = _re.compile(r"SMA_FINAL_OUTPUT:\s*(.+)$")
_DEFAULT_PROGRESS_LOG_INTERVAL = 60  # seconds between progress log entries

# Pre-flight defer window: short enough to recover quickly after a janitor
# pass, long enough that we don't burn the worker chain-waking itself.
PREFLIGHT_DEFER_SECONDS = 300


def preflight_output_capacity(output_dir, output_dir_ratio, input_path, logger=None):
  """Decide whether the configured ``output_dir`` can hold the converted file.

  Returns a tuple ``(ok, needed_bytes, free_bytes)``. ``ok=True`` means the
  gate passes (or is inapplicable). False means the worker should defer.

  Fail-open paths: missing ``output_dir``, unstatable input, or unreachable
  output_dir (ENOENT/permission/OSError). The ratio defaults to 1.0 when
  unset/zero so a forgetful operator still gets an "output must fit input"
  gate at the daemon layer (CLI / MediaProcessor preserve no-op semantics).
  """
  if not output_dir:
    return True, None, None
  ratio = output_dir_ratio if output_dir_ratio else 1.0
  try:
    input_size = os.path.getsize(input_path)
  except OSError:
    if logger is not None:
      logger.warning("preflight_output_capacity: cannot stat input %s; failing open" % input_path)
    return True, None, None
  needed = int(input_size * ratio)
  try:
    free = shutil.disk_usage(output_dir).free
  except (FileNotFoundError, PermissionError, OSError) as err:
    if logger is not None:
      logger.warning("preflight_output_capacity: cannot stat output_dir %s (%s); failing open" % (output_dir, err))
    return True, None, None
  return free > needed, needed, free


def _load_settings_for_preflight(config_file, profile, logger):
  """Best-effort ReadSettings load for the preflight check.

  Returns None on any error so the caller can fail-open — preflight must
  never wedge the queue on a settings parse hiccup.
  """
  try:
    from resources.readsettings import ReadSettings
  except Exception:
    return None
  try:
    return ReadSettings(config_file, logger=logger, profile=profile)
  except (Exception, SystemExit):
    if logger is not None:
      logger.warning("preflight: ReadSettings(%s, profile=%s) failed; skipping capacity gate" % (config_file, profile))
    return None


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
    pool=None,
    fallback_counter_callback=None,
  ):
    super().__init__(daemon=True)
    self._fallback_counter_callback = fallback_counter_callback
    self.worker_id = worker_id
    self.node_id = resolve_node_id()
    self.pool = pool
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
    self._last_approval_log = 0.0

  def stop(self):
    """Signal worker to stop."""
    self.running = False
    self.job_event.set()

  def run(self):
    while self.running:
      # Wait for a wakeup on this worker's own event or periodic timeout.
      # Stagger the timeout per worker so timeout-driven polls don't all
      # hit claim_next_job() in the same instant — the worker with the
      # lowest id always won that race historically. 5s base + worker_id
      # * 50ms keeps total latency bounded while breaking the tie.
      self.job_event.wait(timeout=5.0 + (self.worker_id - 1) * 0.05)
      self.job_event.clear()

      if not self.running:
        break

      # Drain all available jobs before going back to sleep.
      while self.running:
        if getattr(self.job_db, "is_distributed", False) and hasattr(self.job_db, "is_node_approved"):
          approved = False
          try:
            approved = self.job_db.is_node_approved(self.node_id)
          except Exception:
            self.log.exception("Worker %d failed to check node approval status" % self.worker_id)
          if not approved:
            now = time.monotonic()
            if now - self._last_approval_log >= 60:
              self.log.info("Worker %d waiting for admin approval of node %s" % (self.worker_id, self.node_id))
              self._last_approval_log = now
            break

        # Pause gate: block workers until resumed
        if self.pool is not None and self.pool._pause_mode.is_set():
          self.pool._pause_mode.wait(timeout=5.0)
          continue

        # Drain gate: exit inner loop — worker goes idle but stays online
        if self.pool is not None and self.pool._drain_mode.is_set():
          break

        locked = self.config_lock_manager.get_locked_configs()
        job = self.job_db.claim_next_job(self.worker_id, self.node_id, exclude_configs=locked or None)
        if job:
          # Chain-wake: notify the next idle worker before starting heavy
          # work, so a burst of N arrivals fans across N workers instead
          # of this thread looping through all of them serially.
          if self.pool is not None:
            self.pool.notify_one()
          self.process_job(job)
        else:
          break

  def process_job(self, job):
    job_id = job["id"]
    self.current_job_id = job_id
    # Reset per-job state captured from the ffmpeg.attempts JSON line so
    # one job's classification doesn't leak into the next.
    self._last_attempt_failure_class: str | None = None
    self._last_encoder_name: str | None = None
    self._last_encoder_backend: str | None = None
    path = job["path"]
    config_file = job["config"]

    job_started_at = time.monotonic()
    in_flight = metrics_prom.in_flight_counter(self.node_id)
    in_flight.inc()
    try:
      args = json.loads(job["args"]) if job["args"] else []
    except (TypeError, ValueError) as e:
      self.log.error("Job %d has invalid args payload: %s" % (job_id, e))
      cat = categorize_failure(WORKER_SENTINEL_INVALID_ARGS).value
      self.job_db.fail_job(job_id, "Invalid job args", failure_category=cat, failure_cause=WORKER_SENTINEL_INVALID_ARGS)
      metrics_prom.record_job_terminal("failed", time.monotonic() - job_started_at)
      metrics_prom.record_failure(cat, WORKER_SENTINEL_INVALID_ARGS)
      in_flight.dec()
      self.current_job_id = None
      return

    if not os.path.exists(path):
      self.log.error("Job %d: Path does not exist: %s" % (job_id, path))
      cat = categorize_failure(WORKER_SENTINEL_PATH_MISSING).value
      self.job_db.fail_job(job_id, "Path does not exist", failure_category=cat, failure_cause=WORKER_SENTINEL_PATH_MISSING)
      metrics_prom.record_job_terminal("failed", time.monotonic() - job_started_at)
      metrics_prom.record_failure(cat, WORKER_SENTINEL_PATH_MISSING)
      in_flight.dec()
      self.current_job_id = None
      return

    # Job is already marked running by claim_next_job()

    # Check if job was cancelled before we even start (e.g. cancelled while pending)
    current = self.job_db.get_job(job_id)
    if current and current.get("status") == "cancelled":
      self.log.info("Job %d was cancelled before processing started" % job_id)
      metrics_prom.record_job_terminal("cancelled", time.monotonic() - job_started_at)
      in_flight.dec()
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
      metrics_prom.record_job_terminal("cancelled", time.monotonic() - job_started_at)
      in_flight.dec()
      self.current_job_id = None
      return

    try:
      profile = job.get("profile")
      if profile and "--profile" not in args and "-p" not in args:
        args = ["--profile", profile] + args
      input_size = None
      try:
        input_size = os.path.getsize(path)
      except OSError:
        self.log.warning("Could not stat input file for job %d: %s" % (job_id, path))
      # Pre-flight output-filesystem capacity gate. Refuses to start ffmpeg
      # when the configured output_dir cannot hold input_size * ratio,
      # deferring instead so the slot isn't burnt on a guaranteed ENOSPC.
      if self._preflight_disk_pressure(job_id, path, config_file, profile):
        metrics_prom.record_job_terminal("cancelled", time.monotonic() - job_started_at)
        return
      success, final_output, source_duration_secs = self._run_conversion(job_id, path, config_file, args)
      elapsed = time.monotonic() - job_started_at
      output_size = None
      if success:
        # Stat the produced output before any post-process moves it. The
        # `final_output` is the SMA-tagged terminal path written into the
        # log; fall back to the input path for in-place transcodes.
        stat_target = final_output or path
        try:
          output_size = os.path.getsize(stat_target)
        except OSError:
          self.log.warning("Could not stat output file for job %d: %s" % (job_id, stat_target))
        self.job_db.complete_job(
          job_id,
          input_size=input_size,
          output_size=output_size,
          source_duration_seconds=source_duration_secs,
          encoder_backend=self._last_encoder_backend,
          encoder_name=self._last_encoder_name,
        )
        metrics_prom.record_job_terminal("completed", elapsed)
        metrics_prom.record_job_savings(
          input_size,
          output_size,
          source_duration_secs,
          encoder_backend=self._last_encoder_backend,
        )
      else:
        # Don't overwrite a cancelled status set during conversion
        current = self.job_db.get_job(job_id)
        if current and current.get("status") == "cancelled":
          metrics_prom.record_job_terminal("cancelled", elapsed)
        else:
          # Prefer the precise classification from the last ffmpeg.attempts
          # record (captured by _record_fallback_event). Falls back to the
          # generic process_failed sentinel when the subprocess exited
          # without emitting the structured line.
          cause = self._last_attempt_failure_class or WORKER_SENTINEL_PROCESS_FAILED
          cat = categorize_failure(cause).value
          self.job_db.fail_job(job_id, "Conversion process failed", failure_category=cat, failure_cause=cause)
          self._ingest_ffmpeg_stderr_sidecars(job_id)
          metrics_prom.record_job_terminal("failed", elapsed)
          metrics_prom.record_failure(cat, cause)
    except Exception as e:
      self.log.exception("Job %d failed: %s" % (job_id, e))
      elapsed = time.monotonic() - job_started_at
      current = self.job_db.get_job(job_id)
      if current and current.get("status") == "cancelled":
        metrics_prom.record_job_terminal("cancelled", elapsed)
      else:
        cat = categorize_failure(WORKER_SENTINEL_EXCEPTION).value
        self.job_db.fail_job(job_id, str(e), failure_category=cat, failure_cause=WORKER_SENTINEL_EXCEPTION)
        self._ingest_ffmpeg_stderr_sidecars(job_id)
        metrics_prom.record_job_terminal("failed", elapsed)
        metrics_prom.record_failure(cat, WORKER_SENTINEL_EXCEPTION)
    finally:
      self.config_lock_manager.release(config_file, job_id)
      in_flight.dec()
      self.current_job_id = None

  def _preflight_disk_pressure(self, job_id, path, config_file, profile):
    """Run the output-filesystem capacity check before invoking the subprocess.

    Returns True when the job was deferred (caller must abort the work loop),
    False otherwise. Falls open on any unexpected error — a transient DB
    hiccup or misconfigured output_dir must not wedge the queue.
    """
    try:
      settings = _load_settings_for_preflight(config_file, profile, self.log)
      if settings is None:
        return False
      output_dir = getattr(settings, "output_dir", None)
      output_dir_ratio = getattr(settings, "output_dir_ratio", None)
      ok, needed, free = preflight_output_capacity(output_dir, output_dir_ratio, path, logger=self.log)
      if ok:
        return False
      try:
        self.job_db.defer_job(job_id, PREFLIGHT_DEFER_SECONDS, reason=WORKER_SENTINEL_DISK_PRESSURE)
      except Exception:
        self.log.exception("Failed to defer job %d after disk-pressure preflight; failing open" % job_id)
        return False
      try:
        metrics_prom.record_failure(FailureCategory.DISK.value, WORKER_SENTINEL_DISK_PRESSURE)
      except Exception:
        self.log.debug("metrics_prom.record_failure unavailable; skipping", exc_info=True)
      self.log.info(
        json.dumps(
          {
            "event": "worker.preflight",
            "result": "deferred",
            "cause": WORKER_SENTINEL_DISK_PRESSURE,
            "input_path": path,
            "output_dir": output_dir,
            "free_bytes": free,
            "needed_bytes": needed,
          },
          sort_keys=True,
        )
      )
      return True
    except (Exception, SystemExit):
      self.log.exception("Unexpected error in disk-pressure preflight for job %d; failing open" % job_id)
      return False

  def _ingest_ffmpeg_stderr_sidecars(self, job_id):
    """Read every ffmpeg.job<id>.*.stderr.log sidecar for *job_id*, write the
    concatenated payload (oldest first) into ``jobs.ffmpeg_stderr`` via the
    job DB, then delete the sidecar files.

    MediaProcessor writes one sidecar per failed tier of the fallback
    ladder, so a single job can produce multiple files; we concatenate by
    mtime so the operator reads them in the order they were emitted.
    Failures here are swallowed and logged: the daemon should never crash
    a worker because a diagnostic file is missing or unreadable.
    """
    if not hasattr(self.job_db, "update_job_ffmpeg_stderr"):
      return
    try:
      pattern = os.path.join(FFMPEG_STDERR_DIR, "ffmpeg.job%s.*.stderr.log" % job_id)
      paths = sorted(glob.glob(pattern), key=lambda p: os.path.getmtime(p))
      if not paths:
        return
      chunks = []
      for p in paths:
        try:
          with open(p, encoding="utf-8", errors="replace") as fh:
            chunks.append(fh.read())
        except OSError:
          self.log.debug("Could not read ffmpeg stderr sidecar: %s" % p, exc_info=True)
      payload = "\n".join(chunks)
      if payload:
        try:
          self.job_db.update_job_ffmpeg_stderr(job_id, payload)
        except Exception:
          self.log.exception("Failed to persist ffmpeg stderr to DB for job %d" % job_id)
          return
      for p in paths:
        try:
          os.unlink(p)
        except OSError:
          self.log.debug("Could not delete ffmpeg stderr sidecar: %s" % p, exc_info=True)
    except Exception:
      self.log.exception("Failed to ingest ffmpeg stderr sidecars for job %d" % job_id)

  def _run_conversion(self, job_id, path, config_file, extra_args):
    """Run the conversion process.

    Returns a ``(success: bool, final_output: str | None,
    source_duration_seconds: float | None)`` tuple so the caller can
    persist output size + source duration metrics without re-probing.
    """
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
    final_output: str | None = None

    try:
      process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
      )
      self._job_processes[job_id] = process

      if process.stdout is None:
        return None
      for line in process.stdout:
        line = line.strip()
        if not line:
          continue
        # Capture the final-output marker emitted by manual.py:processFile
        # so completion logs can show the post-rename output path instead
        # of the original input path. Last marker wins (covers batched
        # inputs that produce multiple outputs).
        marker = _SMA_FINAL_OUTPUT_RE.search(line)
        if marker:
          final_output = marker.group(1).strip()
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
        # Capture MediaProcessor's structured ffmpeg.attempts event so the
        # daemon's /health fallback counters reflect work done in worker
        # subprocesses. The event is single-line JSON per the logging
        # contract; we look for the event marker substring first to keep
        # the hot path cheap.
        if self._fallback_counter_callback is not None and '"event": "ffmpeg.attempts"' in line:
          self._record_fallback_event(line)

      try:
        timeout = self.job_timeout_seconds if self.job_timeout_seconds > 0 else None
        process.wait(timeout=timeout)
      except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        config_logger.error("Job %d timed out after %ds: %s" % (job_id, self.job_timeout_seconds, path))
        return False, final_output, total_duration_secs

      report_path = final_output or path
      if process.returncode == 0:
        config_logger.info("Job %d completed successfully: %s" % (job_id, report_path))
        return True, final_output, total_duration_secs
      else:
        config_logger.error("Job %d exited with code %d: %s" % (job_id, process.returncode, report_path))
        return False, final_output, total_duration_secs

    except Exception as e:
      config_logger.exception("Job %d failed: %s" % (job_id, e))
      return False, final_output, total_duration_secs
    finally:
      self._job_processes.pop(job_id, None)
      self._job_progress.pop(job_id, None)
      config_logger.info("")  # blank line separates jobs in the per-config log

  def _record_fallback_event(self, line: str) -> None:
    """Parse a single-line ffmpeg.attempts JSON record and bump daemon counters.

    Worker output lines pass through the daemon's log formatter before
    arriving here, so the JSON payload may be embedded in a prefix.
    Tolerate both the bare-JSON case and the wrapped case by locating the
    first ``{`` and parsing from there.
    """
    start = line.find("{")
    if start < 0:
      return
    try:
      payload = json.loads(line[start:])
    except (ValueError, json.JSONDecodeError):
      return
    if payload.get("event") != "ffmpeg.attempts":
      return
    attempts = payload.get("attempts") or []
    if not isinstance(attempts, list):
      return
    result = payload.get("result")
    # Stash the LAST attempt's failure_class so process_job can hand a
    # specific cause to fail_job/categorize_failure. On success this is
    # None; on failure it carries the precise ffmpeg classification.
    if attempts and isinstance(attempts[-1], dict):
      self._last_attempt_failure_class = attempts[-1].get("failure_class")
    # Capture the final encoder used (after any hw_alt / full_sw swaps)
    # so the worker can label Prometheus counters + persist the column.
    encoder_name = payload.get("encoder_name")
    encoder_backend = payload.get("encoder_backend")
    if isinstance(encoder_name, str) and encoder_name:
      self._last_encoder_name = encoder_name
    if isinstance(encoder_backend, str) and encoder_backend:
      self._last_encoder_backend = encoder_backend

    cb = self._fallback_counter_callback
    if cb is None:
      return
    # Each tier that failed produces one counter increment. The to_tier is
    # the next attempted tier, or "failed" if this was the last attempt and
    # the overall result is "failed".
    for idx, rec in enumerate(attempts):
      if not isinstance(rec, dict):
        continue
      failure_class = rec.get("failure_class")
      if not failure_class:
        continue
      from_tier = rec.get("tier") or "unknown"
      next_rec = attempts[idx + 1] if idx + 1 < len(attempts) else None
      if next_rec is not None and isinstance(next_rec, dict):
        to_tier = next_rec.get("tier") or "unknown"
      else:
        to_tier = "failed" if result == "failed" else "unknown"
      try:
        cb(from_tier, to_tier, failure_class)
      except Exception:
        self.log.exception("Failed to record fallback counter increment")

  def _build_progress_payload(self, line, time_match, total_duration_secs, start_time, now):
    elapsed_secs = now - start_time
    progress: dict = {"elapsed": _seconds_to_hms(elapsed_secs)}

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

    bitrate_m = _FFMPEG_BITRATE_RE.search(line)
    if bitrate_m:
      progress["bitrate"] = "%.1fk" % float(bitrate_m.group(1))

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
    fallback_counter_callback=None,
  ):
    self._workers = []
    self._fallback_counter_callback = fallback_counter_callback
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
    self._drain_mode = threading.Event()  # set = cluster drain (no new jobs, stay online)
    self._pause_mode = threading.Event()  # set = paused (workers block)
    # Round-robin notify cursor: notify_one() picks the next idle worker
    # in rotation rather than waking every worker at once. Without this,
    # all workers wake simultaneously, race for the same DB row, and the
    # same thread (worker 1) wins consistently — leaving 3+ workers idle
    # while the queue serializes through one of them.
    self._notify_cursor = 0
    self._notify_lock = threading.Lock()
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
        pool=self,
        fallback_counter_callback=self._fallback_counter_callback,
      )
      worker.start()
      self._workers.append(worker)
      self._logger.debug("Started worker thread %d" % (i + 1))

  def set_drain_mode(self) -> None:
    """Enter cluster drain mode: workers finish active jobs then go idle."""
    self._drain_mode.set()

  def clear_drain_mode(self) -> None:
    """Exit cluster drain mode: workers resume picking up new jobs."""
    self._drain_mode.clear()

  def set_paused(self) -> None:
    """Pause all workers: block new job pickup immediately."""
    self._pause_mode.set()

  def clear_paused(self) -> None:
    """Resume all workers: unblock job pickup and wake sleeping workers."""
    self._pause_mode.clear()
    # Wake all sleeping workers so they re-check their state
    for worker in self._workers:
      worker.job_event.set()

  def notify(self):
    """Wake one idle worker in round-robin rotation.

    Bursts of arrivals are handled by chain-wake: a worker that claims a
    job calls notify_one() before starting heavy work, so the next idle
    worker picks up the next queued job, and so on, fanning the burst
    across the pool instead of serializing through worker 1.
    """
    self.notify_one()

  def notify_one(self) -> None:
    """Wake the next idle worker in rotation. Falls back to waking the
    cursor's worker if every worker is busy — the wake is a no-op for a
    busy worker (its event-set is cleared at the top of its run loop)
    but keeps the cursor advancing so wake-ups stay fair."""
    if not self._workers:
      return
    with self._notify_lock:
      n = len(self._workers)
      # Try to find an idle worker first, walking the rotation.
      for offset in range(n):
        idx = (self._notify_cursor + offset) % n
        worker = self._workers[idx]
        if worker.current_job_id is None:
          worker.job_event.set()
          self._notify_cursor = (idx + 1) % n
          return
      # All busy — still advance the cursor and ping the next slot so
      # whichever worker frees up first sees a pending event.
      idx = self._notify_cursor % n
      self._workers[idx].job_event.set()
      self._notify_cursor = (idx + 1) % n

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
