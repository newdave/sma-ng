import glob
import json
import logging
import os
import re as _re
import shutil
import signal
import subprocess
import sys
import threading
import time
from functools import partial

from resources.daemon import job_kinds, metrics_prom
from resources.daemon.constants import FFMPEG_STDERR_DIR, SCRIPT_DIR, resolve_node_id
from resources.daemon.context import clear_job_id, set_job_id
from resources.daemon.db import NO_CLAIM_TOKEN, TELEMETRY_COLUMNS
from resources.log import LEVEL_TOKEN_RE, getLogger
from resources.processor.failures import (
  OUTPUT_VERIFY_FAILED,
  WORKER_SENTINEL_DISK_PRESSURE,
  WORKER_SENTINEL_EXCEPTION,
  WORKER_SENTINEL_INVALID_ARGS,
  WORKER_SENTINEL_PATH_MISSING,
  WORKER_SENTINEL_PROCESS_FAILED,
  WORKER_SENTINEL_TIMEOUT,
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

# How often the per-job watchdog re-reads the job row to notice a cancel or
# requeue issued from another node (local cancels kill the process directly).
JOB_STATUS_POLL_SECONDS = 10.0
# Grace between SIGTERM and SIGKILL when tearing down a conversion tree.
KILL_GRACE_SECONDS = 5.0
# Back-off after an unexpected error in the worker loop (e.g. DB outage).
LOOP_ERROR_BACKOFF_SECONDS = 5.0
# Delays between retries of a terminal job write (fail/requeue) after a DB error.
TERMINAL_RETRY_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
# After stop() the stop event no longer waits; keep a short real pause so the
# retries still space out without delaying shutdown past its grace.
SHUTDOWN_RETRY_BACKOFF_CAP_SECONDS = 0.25
# Exit codes of a conversion stopped by SIGTERM/SIGINT: killed by the signal
# (-15, -2) or exited through manual.py's 128 + signum fallback (143, 130).
INTERRUPT_RETURNCODES = (-signal.SIGTERM, -signal.SIGINT, 128 + signal.SIGTERM, 128 + signal.SIGINT)
# Returned by _terminal_write when every attempt failed and the write was parked.
_PARKED = object()
# Consecutive pre-start errors (claim, then DB/lock error before the conversion
# starts) that requeue a job; the next one fails it so a poison job cannot loop.
MAX_PRE_START_REQUEUES = 3

# Reasons a running conversion can be aborted by the daemon.
ABORT_TIMEOUT = "timeout"
ABORT_CANCELLED = "cancelled"
ABORT_REASSIGNED = "reassigned"
ABORT_SHUTDOWN = "shutdown"
# failure_cause recorded on jobs killed by the job timeout watchdog.
WORKER_CAUSE_TIMEOUT = WORKER_SENTINEL_TIMEOUT


def kill_process_tree(process, grace=None, own_group=False):
  """Terminate *process* and every process in its process group.

  Conversions are launched with ``start_new_session=True`` so manual.py and
  the ffmpeg it spawns share a dedicated process group; signalling the group
  ensures a hung ffmpeg that holds the stdout pipe dies with its parent.
  Pass *own_group=True* for children started with ``start_new_session``
  (their pgid is their pid) so the group is still signalled after the
  leader exits while a grandchild keeps running.
  Sends SIGTERM, waits up to *grace* seconds, then SIGKILL. Never raises.
  """
  if grace is None:
    grace = KILL_GRACE_SECONDS
  pgid = None
  try:
    pid = process.pid
    if isinstance(pid, int) and hasattr(os, "killpg"):
      candidate = pid if own_group else os.getpgid(pid)
      # Only signal the group when the child leads its own group; never
      # signal the daemon's own process group.
      if candidate == pid and candidate != os.getpgrp():
        pgid = candidate
  except Exception:
    pgid = None

  def _send(sig, fallback):
    if pgid is not None:
      try:
        os.killpg(pgid, sig)
        return
      except ProcessLookupError:
        return
      except OSError:
        pass
    try:
      fallback()
    except Exception:
      pass

  _send(signal.SIGTERM, process.terminate)
  try:
    process.wait(timeout=grace)
  except Exception:
    pass
  _send(getattr(signal, "SIGKILL", signal.SIGTERM), process.kill)


class _OutputBuffer:
  """Collect a job's stdout lines and store them via ``append_job_output``.

  Flushes at most every FLUSH_SECONDS while the tool runs, and once more at
  exit, so ``GET /jobs/{id}/output`` can follow a running job.
  """

  FLUSH_SECONDS = 2.0

  def __init__(self, job_db, job_id, clock=time.monotonic):
    self._append = getattr(job_db, "append_job_output", None)
    self._job_id = job_id
    self._clock = clock
    self._lines = []
    self._last = clock()

  def add(self, line):
    if self._append is None:
      return
    self._lines.append(line)
    if self._clock() - self._last >= self.FLUSH_SECONDS:
      self.flush()

  def flush(self):
    self._last = self._clock()
    if self._append is None or not self._lines:
      return
    text = "\n".join(self._lines) + "\n"
    self._lines = []
    try:
      self._append(self._job_id, text)
    except Exception:
      log.warning("Could not store output for job %d" % self._job_id, exc_info=True)


class _JobWatchdog(threading.Thread):
  """Enforces the job timeout and cross-node cancel for one conversion.

  Runs alongside the blocking stdout read in ``_run_conversion_inner``. On
  deadline, explicit abort, or a job row that is no longer running on this
  node, it records the abort reason on the worker and kills the process
  tree, which closes the pipe and unblocks the reader.
  """

  def __init__(self, worker, job_id, process, timeout_seconds, poll_seconds=None):
    super().__init__(daemon=True, name="job-watchdog-%s" % job_id)
    self.worker = worker
    self.job_id = job_id
    self.process = process
    self.timeout_seconds = timeout_seconds if timeout_seconds and timeout_seconds > 0 else 0
    self.poll_seconds = JOB_STATUS_POLL_SECONDS if poll_seconds is None else poll_seconds
    self.wake = threading.Event()
    self._done = threading.Event()
    self.fired = False

  def finish(self):
    self._done.set()
    self.wake.set()

  def run(self):
    start = time.monotonic()
    deadline = start + self.timeout_seconds if self.timeout_seconds else None
    next_poll = start + self.poll_seconds
    while not self._done.is_set():
      # Clear before checking so an abort_job() wake-up is never lost.
      self.wake.clear()
      reason = self.worker._abort_reason
      now = time.monotonic()
      if reason is None and deadline is not None and now >= deadline:
        reason = self.worker._set_abort_reason(ABORT_TIMEOUT)
      if reason is None and now >= next_poll:
        reason = self.worker._check_job_ownership(self.job_id)
        next_poll = now + self.poll_seconds
      if reason is not None:
        if self._done.is_set():
          return
        self.fired = True
        kill_process_tree(self.process, own_group=(os.name == "posix"))
        return
      target = next_poll if deadline is None else min(next_poll, deadline)
      self.wake.wait(timeout=max(0.05, target - time.monotonic()))


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


def _parse_event(line: str, event: str):
  """Return the JSON object of the *event* record embedded in a log *line*, or None.

  The daemon log formatter may prefix the record (the prefix can itself hold
  braces), so every ``{`` is tried as the start of the payload.
  """
  decoder = json.JSONDecoder()
  start = line.find("{")
  while start >= 0:
    try:
      payload, _end = decoder.raw_decode(line, start)
    except ValueError:
      payload = None
    if isinstance(payload, dict) and payload.get("event") == event:
      return payload
    start = line.find("{", start + 1)
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
    self._claim_token = None  # token of the claim for the job this worker is running
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
    self._job_telemetry: dict = {}
    self._last_encoder_name: str | None = None
    self._last_encoder_backend: str | None = None
    self._last_approval_log = 0.0
    self._stop_event = threading.Event()
    # Abort bookkeeping for the job currently being converted. The reason
    # is set by the watchdog (timeout / remote cancel) or by abort_job()
    # (local cancel / shutdown deadline) and read by process_job.
    self._abort_lock = threading.Lock()
    self._abort_reason = None
    self._watchdog = None
    # Terminal writes parked after a DB outage, keyed by job id: (what, write).
    self._pending_releases = {}
    self._pending_lock = threading.Lock()
    # Consecutive pre-start errors per job id, shared across the pool's workers.
    shared = getattr(pool, "_pre_start_failures", None)
    self._pre_start_failures = shared if isinstance(shared, dict) else {}

  def stop(self):
    """Signal worker to stop."""
    self.running = False
    self._stop_event.set()
    self.job_event.set()

  def _set_abort_reason(self, reason):
    """Record *reason* unless another abort reason won the race; return the winner."""
    with self._abort_lock:
      if self._abort_reason is None:
        self._abort_reason = reason
      return self._abort_reason

  def abort_job(self, job_id, reason):
    """Ask the watchdog to kill the conversion for *job_id* if this worker runs it.

    Returns True when *job_id* is this worker's current job.
    """
    with self._abort_lock:
      if job_id is None or self.current_job_id != job_id:
        return False
      if self._abort_reason is None:
        self._abort_reason = reason
      watchdog = self._watchdog
    if watchdog is not None:
      watchdog.wake.set()
    return True

  def _owner_kwargs(self):
    """Ownership arguments for fail/complete/requeue/defer: node plus the claim token (NO_CLAIM_TOKEN when this run holds none)."""
    return {"node_id": self.node_id, "claim_token": self._claim_token or NO_CLAIM_TOKEN}

  def _check_job_ownership(self, job_id):
    """Return an abort reason when the job row no longer belongs to this run.

    Catches cancels and requeues issued from another node (or the admin
    API on a different process). DB errors are ignored — a transient
    outage must not kill a healthy conversion.
    """
    try:
      row = self.job_db.get_job(job_id)
    except Exception:
      self.log.debug("Job %d status poll failed; will retry" % job_id, exc_info=True)
      return None
    if row is None:
      return self._set_abort_reason(ABORT_CANCELLED)
    if not isinstance(row, dict):
      return None
    status = row.get("status")
    if status == "cancelled":
      return self._set_abort_reason(ABORT_CANCELLED)
    owner = row.get("node_id")
    if status in ("pending", "completed", "failed") or (status == "running" and isinstance(owner, str) and owner and owner != self.node_id):
      return self._set_abort_reason(ABORT_REASSIGNED)
    # Same node, new claim: the job was requeued and another worker took it.
    if status == "running" and "claim_token" in row and (row["claim_token"] or None) != (self._claim_token or None):
      return self._set_abort_reason(ABORT_REASSIGNED)
    return None

  def run(self):
    while self.running:
      try:
        self._run_once()
      except Exception as e:
        # A DB outage (claim_next_job, complete_job, fail_job, ...) must not
        # kill the thread: log, back off, and retry. WorkerPool also
        # respawns threads that die anyway.
        self.log.exception("Worker %d loop error (%s); retrying in %ds" % (self.worker_id, type(e).__name__, LOOP_ERROR_BACKOFF_SECONDS))
        self._stop_event.wait(timeout=LOOP_ERROR_BACKOFF_SECONDS)

  def _run_once(self):
    """One wait-then-drain cycle of the worker loop."""
    # Wait for a wakeup on this worker's own event or periodic timeout.
    # Stagger the timeout per worker so timeout-driven polls don't all
    # hit claim_next_job() in the same instant — the worker with the
    # lowest id always won that race historically. 5s base + worker_id
    # * 50ms keeps total latency bounded while breaking the tie.
    self.job_event.wait(timeout=5.0 + (self.worker_id - 1) * 0.05)
    self.job_event.clear()

    # Before the pause/drain/approval gates, so a paused or draining worker still releases rows.
    self._flush_pending_release()

    if not self.running:
      return

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
            self.log.info("Worker %d waiting for admin approval of this node" % self.worker_id)
            self._last_approval_log = now
          break

      # Pause gate: go back to the outer wait (5s timeout, or woken by
      # clear_paused) instead of spinning while paused.
      if self.pool is not None and self.pool._pause_mode.is_set():
        break

      # Drain gate: exit inner loop — worker goes idle but stays online
      if self.pool is not None and self.pool._drain_mode.is_set():
        break

      locked = self.config_lock_manager.get_locked_configs()
      profile_caps = None
      profile_costs = None
      profile_weights = None
      budget = None
      try:
        profile_caps = self.path_config_manager.profile_concurrency_caps()
        profile_costs = self.path_config_manager.profile_concurrency_costs()
        profile_weights = self.path_config_manager.profile_priority_weights()
        budget = self.path_config_manager.concurrency_budget
      except Exception:
        self.log.debug("profile cap/cost/weight/budget unavailable; ignoring", exc_info=True)
      job = self.job_db.claim_next_job(
        self.worker_id,
        self.node_id,
        exclude_configs=locked or None,
        profile_caps=profile_caps or None,
        profile_costs=profile_costs or None,
        profile_weights=profile_weights or None,
        concurrency_budget=budget or None,
        kinds=job_kinds.SUPPORTED_KINDS,
      )
      if job:
        # Chain-wake: notify the next idle worker before starting heavy
        # work, so a burst of N arrivals fans across N workers instead
        # of this thread looping through all of them serially.
        if self.pool is not None:
          self.pool.notify_one()
        self.process_job(job)
        # A write parked by that job must land before the next claim, not after the next conversion.
        self._flush_pending_release()
      else:
        break

  def process_job(self, job):
    job_id = job["id"]
    self._claim_token = job.get("claim_token")
    with self._abort_lock:
      self.current_job_id = job_id
      self._abort_reason = None
      self._watchdog = None
    # Reset per-job state captured from the ffmpeg.attempts JSON line so
    # one job's classification doesn't leak into the next.
    self._last_attempt_failure_class: str | None = None
    self._last_verify_reason: str | None = None
    self._last_encoder_name: str | None = None
    self._last_encoder_backend: str | None = None
    self._job_telemetry: dict = {}
    self._last_returncode: int | None = None
    path = job["path"]
    config_file = job["config"]
    try:
      kind = job_kinds.get(job.get("kind"))
    except KeyError:
      kind = None

    job_started_at = time.monotonic()
    in_flight = metrics_prom.in_flight_counter(self.node_id)
    in_flight.inc()
    lock_held = False
    started = False
    try:
      try:
        args = json.loads(job["args"]) if job["args"] else []
      except (TypeError, ValueError) as e:
        self.log.error("Job %d has invalid args payload: %s" % (job_id, e))
        cat = categorize_failure(WORKER_SENTINEL_INVALID_ARGS).value
        self.job_db.fail_job(job_id, "Invalid job args", failure_category=cat, failure_cause=WORKER_SENTINEL_INVALID_ARGS, **self._owner_kwargs())
        metrics_prom.record_job_terminal("failed", time.monotonic() - job_started_at)
        metrics_prom.record_failure(cat, WORKER_SENTINEL_INVALID_ARGS)
        return

      if kind is None:
        self.log.error("Job %d has unsupported kind %r" % (job_id, job.get("kind")))
        cat = categorize_failure(WORKER_SENTINEL_INVALID_ARGS).value
        self.job_db.fail_job(job_id, "Unsupported job kind %r" % job.get("kind"), failure_category=cat, failure_cause=WORKER_SENTINEL_INVALID_ARGS, **self._owner_kwargs())
        metrics_prom.record_job_terminal("failed", time.monotonic() - job_started_at)
        metrics_prom.record_failure(cat, WORKER_SENTINEL_INVALID_ARGS)
        return
      is_convert = kind.name == job_kinds.DEFAULT_KIND

      if not os.path.exists(path):
        self.log.error("Job %d: Path does not exist: %s" % (job_id, path))
        cat = categorize_failure(WORKER_SENTINEL_PATH_MISSING).value
        self.job_db.fail_job(job_id, "Path does not exist", failure_category=cat, failure_cause=WORKER_SENTINEL_PATH_MISSING, **self._owner_kwargs())
        metrics_prom.record_job_terminal("failed", time.monotonic() - job_started_at)
        metrics_prom.record_failure(cat, WORKER_SENTINEL_PATH_MISSING)
        return

      # Job is already marked running by claim_next_job()

      # Check if job was cancelled before we even start (e.g. cancelled while pending)
      current = self.job_db.get_job(job_id)
      if current and current.get("status") == "cancelled":
        self.log.info("Job %d was cancelled before processing started" % job_id)
        metrics_prom.record_job_terminal("cancelled", time.monotonic() - job_started_at)
        return

      # Acquire lock for this config (blocks if another job is using it)
      self.log.debug("Worker %d acquiring lock for job %d: %s" % (self.worker_id, job_id, os.path.basename(config_file)))
      self.config_lock_manager.acquire(config_file, job_id, path)
      lock_held = True

      # Check again after acquiring lock (may have been cancelled while waiting)
      current = self.job_db.get_job(job_id)
      if current and current.get("status") == "cancelled":
        self.log.info("Job %d was cancelled while waiting for lock" % job_id)
        metrics_prom.record_job_terminal("cancelled", time.monotonic() - job_started_at)
        return
      profile = job.get("profile")
      if kind.uses_profile and profile and "--profile" not in args and "-p" not in args:
        args = ["--profile", profile] + args
      input_size = None
      if is_convert:
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
      # Conversions keep their original call shape; other kinds name themselves.
      kind_kwargs = {} if is_convert else {"kind": kind.name}
      started = True
      self._pre_start_failures.pop(job_id, None)
      success, final_output, source_duration_secs = self._run_conversion(job_id, path, config_file, args, **kind_kwargs)
      elapsed = time.monotonic() - job_started_at
      output_size = None
      # A conversion that exited 0 before the kill landed is still a
      # success; the DB guard discards it if the row was cancelled.
      abort_reason = None if success else self._abort_reason
      returncode = self._last_returncode
      if abort_reason is None and not success and (not self.running or returncode in INTERRUPT_RETURNCODES):
        # A conversion that ended while this worker is stopping, or that died
        # from SIGTERM/SIGINT (the daemon's shutdown signal reaches manual.py
        # and ffmpeg directly; manual.py's fallback exits 128 + signum), was
        # interrupted, not broken. Other signals
        # (SIGKILL/OOM, SIGSEGV) take the normal retry/backoff failure path so
        # a deterministic crash cannot requeue forever.
        abort_reason = ABORT_SHUTDOWN
      if abort_reason == ABORT_SHUTDOWN:
        # Interrupted by shutdown / a signal: put the job back to pending
        # without burning a retry or recording a failure. If this write is
        # lost the row stays 'running' and mark_node_offline (cluster) or the
        # next startup (SQLite) requeues it.
        self.log.warning("Job %d interrupted by daemon shutdown or signal (exit code %s); requeuing: %s" % (job_id, returncode, path))
        try:
          requeued = self.job_db.requeue_interrupted_job(job_id, self._interrupt_reason(returncode), **self._owner_kwargs())
          if requeued is False:
            self._log_discarded(job_id, False, "requeue")
        except Exception:
          self.log.exception("Failed to requeue interrupted job %d; leaving it for node recovery" % job_id)
        self._ingest_ffmpeg_stderr_sidecars(job_id)
        metrics_prom.record_job_terminal("cancelled", elapsed)
      elif abort_reason in (ABORT_CANCELLED, ABORT_REASSIGNED):
        self.log.info("Job %d stopped: job was %s while converting: %s" % (job_id, "cancelled" if abort_reason == ABORT_CANCELLED else "requeued or claimed elsewhere", path))
        metrics_prom.record_job_terminal("cancelled", elapsed)
      elif abort_reason == ABORT_TIMEOUT:
        cat = categorize_failure(WORKER_CAUSE_TIMEOUT).value
        updated = self._terminal_write(
          job_id,
          "timeout failure",
          partial(
            self.job_db.fail_job,
            job_id,
            "Job timed out after %ds" % self.job_timeout_seconds,
            failure_category=cat,
            failure_cause=WORKER_CAUSE_TIMEOUT,
            **self._owner_kwargs(),
            telemetry=self._job_telemetry,
            source_duration_seconds=source_duration_secs,
          ),
        )
        self._log_discarded(job_id, updated, "timeout failure")
        self._ingest_ffmpeg_stderr_sidecars(job_id)
        metrics_prom.record_job_terminal("failed", elapsed)
        metrics_prom.record_failure(cat, WORKER_CAUSE_TIMEOUT)
      elif success:
        # Stat the produced output before any post-process moves it. The
        # `final_output` is the SMA-tagged terminal path written into the
        # log; fall back to the input path for in-place transcodes.
        stat_target = final_output or path
        if is_convert:
          try:
            output_size = os.path.getsize(stat_target)
          except OSError:
            self.log.warning("Could not stat output file for job %d: %s" % (job_id, stat_target))
        # Retried and parked, never raised: a DB error here must not fail or re-run a finished conversion.
        updated = self._terminal_write(
          job_id,
          "completion",
          partial(
            self.job_db.complete_job,
            job_id,
            input_size=input_size,
            output_size=output_size,
            source_duration_seconds=source_duration_secs,
            encoder_backend=self._last_encoder_backend,
            encoder_name=self._last_encoder_name,
            **self._owner_kwargs(),
            telemetry=self._job_telemetry,
          ),
        )
        if updated is False:
          self._log_discarded(job_id, updated, "completion")
          metrics_prom.record_job_terminal("cancelled", elapsed)
          return
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
          message = "Conversion process failed" if is_convert else "%s process failed" % kind.name.capitalize()
          if cause == OUTPUT_VERIFY_FAILED and self._last_verify_reason:
            message += ": output verification failed (%s)" % self._last_verify_reason
          if returncode is not None and returncode < 0:
            message += ": killed by signal %d (exit code %d)" % (-returncode, returncode)
          elif returncode is not None:
            message += " (exit code %d)" % returncode
          updated = self._terminal_write(
            job_id,
            "failure",
            partial(
              self.job_db.fail_job,
              job_id,
              message,
              failure_category=cat,
              failure_cause=cause,
              **self._owner_kwargs(),
              telemetry=self._job_telemetry,
              source_duration_seconds=source_duration_secs,
            ),
          )
          self._log_discarded(job_id, updated, "failure")
          self._ingest_ffmpeg_stderr_sidecars(job_id)
          metrics_prom.record_job_terminal("failed", elapsed)
          metrics_prom.record_failure(cat, cause)
    except Exception as e:
      self.log.exception("Job %d failed: %s" % (job_id, e))
      self._handle_job_error(job_id, e, started, job_started_at)
    finally:
      try:
        if lock_held:
          self.config_lock_manager.release(config_file, job_id)
      finally:
        in_flight.dec()
        with self._abort_lock:
          self.current_job_id = None
          self._watchdog = None
          self._claim_token = None

  def _handle_job_error(self, job_id, error, started, job_started_at):
    """Release the claim after an exception in process_job; never raises.

    Before the conversion starts (a DB error after the claim) the job goes
    back to pending without burning a retry, up to MAX_PRE_START_REQUEUES in a
    row; the next pre-start error fails it through fail_job. After the start
    it is failed. Writes are owner-guarded and retried with backoff; if the DB
    still refuses, the write is parked for the worker loop and the orphan
    sweep / node recovery is the backstop.
    """
    elapsed = time.monotonic() - job_started_at
    owner = self._owner_kwargs()
    try:
      current = self._retry_terminal("status read", lambda: self.job_db.get_job(job_id), job_id)
    except Exception:
      current = None
    if current and current.get("status") == "cancelled":
      self._pre_start_failures.pop(job_id, None)
      metrics_prom.record_job_terminal("cancelled", elapsed)
      return
    failed = started
    if not started:
      count = self._pre_start_failures.get(job_id, 0) + 1
      if count > MAX_PRE_START_REQUEUES:
        self._pre_start_failures.pop(job_id, None)
        failed = True
        cat = categorize_failure(WORKER_SENTINEL_EXCEPTION).value
        reason = "Job start failed %d times in a row (%s: %s); giving up" % (count, type(error).__name__, " ".join(str(error).split()))
        self.log.error("Job %d start failed %d times in a row (%s); failing it instead of requeuing" % (job_id, count, type(error).__name__))
        write = lambda: self.job_db.fail_job(job_id, reason, failure_category=cat, failure_cause=WORKER_SENTINEL_EXCEPTION, **owner)  # noqa: E731
        terminal = "failure"
      else:
        self._pre_start_failures[job_id] = count
        reason = "Interrupted by %s before the job started; requeued" % type(error).__name__
        write = lambda: self.job_db.requeue_interrupted_job(job_id, reason, **owner)  # noqa: E731
        terminal = "requeue"
    else:
      cat = categorize_failure(WORKER_SENTINEL_EXCEPTION).value
      write = lambda: self.job_db.fail_job(job_id, str(error), failure_category=cat, failure_cause=WORKER_SENTINEL_EXCEPTION, **owner)  # noqa: E731
      terminal = "failure"
    updated = self._terminal_write(job_id, terminal, write)
    if updated is _PARKED:
      return
    self._log_discarded(job_id, updated, terminal)
    if failed:
      if started:
        self._ingest_ffmpeg_stderr_sidecars(job_id)
      metrics_prom.record_job_terminal("failed", elapsed)
      metrics_prom.record_failure(cat, WORKER_SENTINEL_EXCEPTION)
    # A pre-start requeue is not terminal: the job runs again, so sma_jobs_total is left alone.

  def _terminal_write(self, job_id, what, write):
    """Run a terminal write with retries; park it for the worker loop when every attempt fails.

    Returns the write's result, or _PARKED. Never raises, so a DB error on the
    terminal write cannot replace the job's real outcome.
    """
    try:
      return self._retry_terminal(what, write, job_id)
    except Exception:
      with self._pending_lock:
        self._pending_releases[job_id] = (what, write)
      return _PARKED

  def _retry_terminal(self, what, write, job_id):
    """Call *write*, retrying with backoff; re-raise the last error when every attempt fails."""
    delays = list(TERMINAL_RETRY_BACKOFF_SECONDS)
    for attempt in range(len(delays) + 1):
      try:
        return write()
      except Exception as e:
        if attempt == len(delays):
          self.log.error("Job %d %s write failed after %d attempts (%s); leaving it for retry or node recovery" % (job_id, what, attempt + 1, type(e).__name__))
          raise
        if self._stop_event.is_set():
          # stop() makes the event wait return at once; keep a short bounded pause instead.
          time.sleep(min(delays[attempt], SHUTDOWN_RETRY_BACKOFF_CAP_SECONDS))
        else:
          self._stop_event.wait(timeout=delays[attempt])

  def _flush_pending_release(self):
    """Retry every parked terminal write from an earlier DB outage (one attempt each per loop pass)."""
    with self._pending_lock:
      pending = list(self._pending_releases.items())
    for job_id, (what, write) in pending:
      try:
        updated = write()
      except Exception as e:
        self.log.warning("Job %d pending %s still failing (%s); will retry" % (job_id, what, type(e).__name__))
        continue
      with self._pending_lock:
        if self._pending_releases.get(job_id, (None, None))[1] is write:
          del self._pending_releases[job_id]
      self._log_discarded(job_id, updated, what)

  def held_job_ids(self):
    """Job ids this worker still owns: the running job plus rows with a parked terminal write."""
    # Read current_job_id first: process_job parks a write before it clears current_job_id,
    # so a job moving from one to the other is always seen in at least one of them.
    current = self.current_job_id
    with self._pending_lock:
      held = set(self._pending_releases)
    if current is not None:
      held.add(current)
    return held

  @staticmethod
  def _signal_name(returncode):
    """Human name for a negative (signal) return code: 'SIGTERM' or 'signal 250'."""
    try:
      return signal.Signals(-returncode).name
    except ValueError:
      return "signal %d" % -returncode

  @staticmethod
  def _interrupt_reason(returncode):
    """Operator-facing jobs.error text for a conversion interrupted by shutdown or a signal."""
    if returncode is not None and returncode < 0:
      name = ConversionWorker._signal_name(returncode)
      return "Interrupted by %s (exit code %d); requeued" % (name, returncode)
    if returncode is None:
      return "Interrupted by daemon shutdown; requeued"
    return "Interrupted by daemon shutdown (exit code %d); requeued" % returncode

  def _log_discarded(self, job_id, updated, what):
    """Log when complete_job/fail_job reported that no row was updated.

    ``updated is False`` means the row was no longer 'running' on this node
    (cancelled, requeued by stale-node recovery, or claimed elsewhere), so
    this worker's result was intentionally dropped.
    """
    if updated is False:
      self.log.info("Job %d %s discarded: job is no longer running on this node (cancelled or requeued)" % (job_id, what))

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
        if self.job_db.defer_job(job_id, PREFLIGHT_DEFER_SECONDS, reason=WORKER_SENTINEL_DISK_PRESSURE, **self._owner_kwargs()) is False:
          self.log.warning("Job %d no longer owned by this run; disk-pressure deferral discarded" % job_id)
          return False
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

  def _run_conversion(self, job_id, path, config_file, extra_args, kind=job_kinds.DEFAULT_KIND):
    """Run the conversion process.

    Returns a ``(success: bool, final_output: str | None,
    source_duration_seconds: float | None)`` tuple so the caller can
    persist output size + source duration metrics without re-probing.
    """
    token = set_job_id(job_id)
    try:
      return self._run_conversion_inner(job_id, path, config_file, extra_args, kind=kind)
    finally:
      clear_job_id(token)

  def _run_conversion_inner(self, job_id, path, config_file, extra_args, kind=job_kinds.DEFAULT_KIND):
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

    # --job-id crosses the process boundary that the _job_id contextvar
    # cannot: without it the MediaProcessor subprocess logs "job:-" and
    # names its ffmpeg-stderr sidecar "ffmpeg.job-.*", which the
    # _ingest_ffmpeg_stderr_sidecars glob ("ffmpeg.job<id>.*") never
    # matches — so jobs.ffmpeg_stderr stayed empty for every failure.
    job_kind = job_kinds.get(kind)
    cmd = job_kind.argv(
      os.path.dirname(self.script_path),
      path,
      config_file,
      job_id,
      extra_args,
      python=sys.executable,
      script_path=self.script_path if job_kind.script == "manual.py" else None,
    )
    output = _OutputBuffer(self.job_db, job_id)

    env = os.environ.copy()
    if self.ffmpeg_dir:
      env["PATH"] = self.ffmpeg_dir + os.pathsep + env.get("PATH", "")

    start_time = time.monotonic()
    total_duration_secs = None
    _last_progress_log: float = 0.0
    final_output: str | None = None
    line_level = logging.INFO

    watchdog = None
    try:
      process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        # Own process group so timeout/cancel can kill manual.py *and*
        # the ffmpeg it spawns (which inherits and holds the stdout pipe).
        start_new_session=(os.name == "posix"),
      )
      self._job_processes[job_id] = process

      # The stdout loop below blocks until EOF, so the timeout and remote
      # cancel are enforced from a separate watchdog thread that kills
      # the process tree (closing the pipe) when they trigger.
      watchdog = _JobWatchdog(self, job_id, process, self.job_timeout_seconds)
      with self._abort_lock:
        self._watchdog = watchdog
      watchdog.start()

      if process.stdout is None:
        return None
      for line in process.stdout:
        line = line.strip()
        if not line:
          continue
        # Child records carry "SMA_LEVEL=<LEVEL> "; strip it and keep the
        # level. Traceback continuation lines inherit the previous level;
        # anything else (raw ffmpeg output) stays INFO.
        level_match = LEVEL_TOKEN_RE.match(line)
        if level_match:
          line_level = getattr(logging, level_match.group(1))
          line = line[level_match.end() :].strip()
          if not line:
            continue
        elif line.startswith("| ") and line_level > logging.INFO:
          pass
        else:
          line_level = logging.INFO
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
          config_logger.log(line_level, line)
          output.add(line)
        # Capture MediaProcessor's structured ffmpeg.attempts event so the
        # daemon's /health fallback counters reflect work done in worker
        # subprocesses. The event is single-line JSON per the logging
        # contract and SingleLineFormatter compacts it (no space after the
        # colon), so match the bare event name, not a whitespace-exact
        # key/value pair. Always parse: the failure class feeds
        # jobs.failure_category even when no metrics callback is registered
        # (the callback is gated inside _record_fallback_event).
        if "ffmpeg.attempts" in line:
          self._record_fallback_event(line)
        # manual.py's sma.job.summary event carries the source/output media
        # facts and the source duration (ffmpeg's own "Duration:" banner is
        # never printed to this pipe, so it is the only progress-percent
        # denominator).
        if "sma.job.summary" in line:
          self._record_job_summary(line)
          if total_duration_secs is None:
            total_duration_secs = self._job_telemetry.get("source_duration_seconds")
        if "output.verify" in line:
          self._record_output_verify_event(line)

      # EOF reached; the watchdog still guards this wait.
      process.wait()

      report_path = final_output or path
      self._last_returncode = process.returncode
      reason = self._abort_reason
      if reason == ABORT_TIMEOUT and process.returncode != 0:
        msg = "Job %d timed out after %ds; killed conversion process tree: %s" % (job_id, self.job_timeout_seconds, path)
        config_logger.error(msg)
        self.log.error(msg)
        return False, final_output, total_duration_secs
      if reason in (ABORT_CANCELLED, ABORT_REASSIGNED, ABORT_SHUTDOWN) and process.returncode != 0:
        config_logger.warning("Job %d conversion killed (%s): %s" % (job_id, reason, path))
        return False, final_output, total_duration_secs
      if job_kind.succeeded(process.returncode):
        config_logger.info("Job %d completed successfully: %s" % (job_id, report_path))
        return True, final_output, total_duration_secs
      else:
        config_logger.error("Job %d exited with code %d: %s" % (job_id, process.returncode, report_path))
        return False, final_output, total_duration_secs

    except Exception as e:
      config_logger.exception("Job %d failed: %s" % (job_id, e))
      return False, final_output, total_duration_secs
    finally:
      output.flush()
      if watchdog is not None:
        watchdog.finish()
      self._job_processes.pop(job_id, None)
      self._job_progress.pop(job_id, None)

  def _record_job_summary(self, line: str) -> None:
    """Merge a single-line ``sma.job.summary`` JSON record into ``_job_telemetry``.

    The record may carry ``source``, ``dest`` (dicts of video_codec,
    video_width, video_height, audio_codec, audio_channels, hdr) and
    ``duration_seconds``. Several records per job merge; later non-null
    values win. Malformed records are ignored.
    """
    payload = _parse_event(line, "sma.job.summary")
    if payload is None:
      return
    # A job where no video encoder ran (stream copy, bypassed conversion)
    # never emits ffmpeg.attempts, so manual.py names the encoder here. An
    # encoder reported by the ladder wins.
    name = payload.get("encoder_name")
    backend = payload.get("encoder_backend")
    if isinstance(name, str) and name and self._last_encoder_name is None:
      self._last_encoder_name = name
    if isinstance(backend, str) and backend and self._last_encoder_backend is None:
      self._last_encoder_backend = backend
    for prefix in ("source", "dest"):
      facts = payload.get(prefix)
      if not isinstance(facts, dict):
        continue
      for key, value in facts.items():
        column = "%s_%s" % (prefix, key)
        if value is not None and column in TELEMETRY_COLUMNS:
          self._job_telemetry[column] = value
    duration = payload.get("duration_seconds")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0:
      self._job_telemetry["source_duration_seconds"] = float(duration)

  def _record_output_verify_event(self, line: str) -> None:
    """Record the output.verify JSON line the subprocess logs when a finished
    conversion fails the duration / video-stream check, so the job fails with
    the output_verify_failed cause and the short reason."""
    payload = _parse_event(line, "output.verify")
    if payload is None:
      return
    self._last_attempt_failure_class = OUTPUT_VERIFY_FAILED
    reason = payload.get("reason")
    if isinstance(reason, str) and reason:
      self._last_verify_reason = reason

  def _record_fallback_event(self, line: str) -> None:
    """Parse a single-line ffmpeg.attempts JSON record and bump daemon counters.

    Worker output lines pass through the daemon's log formatter before
    arriving here, so the JSON payload may be embedded in a prefix.
    Tolerate both the bare-JSON case and the wrapped case by locating the
    first ``{`` and parsing from there.
    """
    payload = _parse_event(line, "ffmpeg.attempts")
    if payload is None:
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
    self._stopped = False
    # Consecutive pre-start errors per job id, shared so a poison job cannot hop between workers.
    self._pre_start_failures = {}
    self._start_workers()

  def _make_worker(self, worker_id, template=None):
    """Build (but do not start) a worker; *template* carries hot-reloaded settings."""
    return ConversionWorker(
      worker_id=worker_id,
      job_db=self._job_db,
      path_config_manager=self._path_config_manager,
      config_log_manager=self._config_log_manager,
      config_lock_manager=self._config_lock_manager,
      logger=self._logger,
      ffmpeg_dir=template.ffmpeg_dir if template is not None else self._ffmpeg_dir,
      job_timeout_seconds=template.job_timeout_seconds if template is not None else self._job_timeout_seconds,
      progress_log_interval=template.progress_log_interval if template is not None else self._progress_log_interval,
      job_processes=self._job_processes,
      job_progress=self._job_progress,
      pool=self,
      fallback_counter_callback=self._fallback_counter_callback,
    )

  def _start_workers(self):
    self._stopped = False
    for i in range(self._worker_count):
      worker = self._make_worker(i + 1)
      worker.start()
      self._workers.append(worker)
      self._logger.debug("Started worker thread %d" % (i + 1))

  def ensure_workers_alive(self):
    """Respawn worker threads that died unexpectedly. Returns the respawn count.

    ConversionWorker.run() already survives loop errors, so this is a last
    line of defence (e.g. a BaseException escaping the loop). No-op once
    the pool has been stopped for shutdown/restart.
    """
    if self._stopped:
      return 0
    respawned = 0
    with self._notify_lock:
      for idx, worker in enumerate(list(self._workers)):
        if worker.is_alive() or not worker.running or worker.ident is None:
          continue
        replacement = self._make_worker(worker.worker_id, template=worker)
        replacement.start()
        self._workers[idx] = replacement
        respawned += 1
        self._logger.warning("Worker %d thread died unexpectedly; respawned" % worker.worker_id)
    return respawned

  def abort_job(self, job_id, reason):
    """Ask the worker running *job_id* to kill its conversion. Returns True if found."""
    for worker in list(self._workers):
      if worker.abort_job(job_id, reason):
        return True
    return False

  def held_job_ids(self):
    """Job ids a live local worker or ffmpeg process still owns; the orphan sweep never touches them."""
    held = set(self._job_processes)
    for worker in list(self._workers):
      held |= worker.held_job_ids()
    return held

  def abort_all(self, reason):
    """Kill every in-flight conversion (used at the shutdown deadline)."""
    aborted = 0
    for worker in list(self._workers):
      if worker.abort_job(worker.current_job_id, reason):
        aborted += 1
    return aborted

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
    self._stopped = True
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
