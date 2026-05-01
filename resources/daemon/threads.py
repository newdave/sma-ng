import os
import threading
import time

from resources.daemon.log_archiver import LogArchiver
from resources.library_audit.engine import AuditEngine
from resources.library_audit.enumerator import enumerate_paths
from resources.log import getLogger

log = getLogger("DAEMON")

_MEDIA_EXTENSIONS = frozenset([".mp4", ".mkv", ".avi", ".mov", ".ts", ".m4v", ".m2ts", ".wmv", ".flv", ".webm"])


class _StoppableThread(threading.Thread):
  """Base class for daemon threads that support a cooperative stop() method."""

  def __init__(self):
    super().__init__(daemon=True)
    self.running = True
    self._stop_event = threading.Event()

  def stop(self):
    self.running = False
    self._stop_event.set()


class HeartbeatThread(_StoppableThread):
  """Periodically updates this node's heartbeat in the cluster_nodes table
  and recovers jobs from nodes that have gone stale.
  """

  def __init__(self, job_db, node_id, host, worker_count, server, interval, stale_seconds, logger, started_at, version="", hwaccel="", log_ttl_days=30, node_name=None):
    super().__init__()
    self.job_db = job_db
    self.node_id = node_id
    self.host = host
    self.worker_count = worker_count
    self.server = server
    self.interval = interval
    self.stale_seconds = stale_seconds
    self.log = logger
    self.started_at = started_at
    self.version = version
    self.hwaccel = hwaccel
    self.log_ttl_days = log_ttl_days
    self.node_name = node_name or None

  def run(self):
    if not self.job_db.is_distributed:
      return  # Heartbeat only meaningful for the shared PG backend
    while self.running:
      try:
        self.job_db.heartbeat(self.node_id, self.host, self.worker_count, self.started_at, version=self.version, hwaccel=self.hwaccel, node_name=self.node_name)
        cmd = None
        if self.job_db.is_distributed:
          cmd = self.job_db.poll_node_command(self.node_id)
        if cmd:
          should_exit = self._execute_command(cmd)
          if should_exit:
            return
        recovered = self.job_db.recover_stale_nodes(self.stale_seconds)
        for stale_id, job_count in recovered:
          self.log.warning("Recovered %d jobs from stale node %s" % (job_count, stale_id))
        if any(job_count > 0 for _, job_count in recovered):
          self.server.notify_workers()  # Wake workers to pick up requeued jobs
        if self.log_ttl_days > 0:
          self.job_db.cleanup_old_logs(self.log_ttl_days)
        if self.job_db.is_distributed:
          expiry_days = self.server.path_config_manager.node_expiry_days
          if expiry_days > 0:
            expired = self.job_db.expire_offline_nodes(expiry_days)
            for nid in expired:
              self.log.info("Expired offline node: %s" % nid)
        if self.job_db.is_distributed:
          archive_dir = self.server.path_config_manager.log_archive_dir
          archive_after = self.server.path_config_manager.log_archive_after_days
          delete_after = self.server.path_config_manager.log_delete_after_days
          if archive_dir and archive_after > 0:
            archiver = LogArchiver(archive_dir, archive_after, delete_after, self.log)
            archiver.run(self.job_db)
      except Exception:
        self.log.exception("Heartbeat error")
      self._stop_event.wait(timeout=self.interval)

  def _execute_command(self, cmd: dict) -> bool:
    """Execute a cluster command. Returns True if the heartbeat loop should exit."""
    cmd_id = cmd["id"]
    command = cmd["command"]
    try:
      if command == "drain":
        self.server.worker_pool.set_drain_mode()
        self.job_db.set_node_status(self.node_id, "draining")
      elif command == "pause":
        self.server.worker_pool.set_paused()
        self.job_db.set_node_status(self.node_id, "paused")
      elif command == "resume":
        self.server.worker_pool.clear_paused()
        self.server.worker_pool.clear_drain_mode()
        self.job_db.set_node_status(self.node_id, "online")
      elif command == "restart":
        # Mark the row so the dashboard shows the transition; the new
        # process's heartbeat will flip the status back to 'online'.
        self.job_db.set_node_status(self.node_id, "restarting")
        self.job_db.ack_node_command(cmd_id, "done")
        threading.Thread(target=self.server.graceful_restart, daemon=True).start()
        return True
      elif command == "shutdown":
        # No subsequent heartbeats will run, so set 'offline' explicitly
        # rather than waiting for the stale-node recovery to flip it.
        self.job_db.set_node_status(self.node_id, "offline")
        self.job_db.ack_node_command(cmd_id, "done")
        threading.Thread(target=self.server.shutdown, daemon=True).start()
        return True
      else:
        self.log.warning("Unknown cluster command: %s", command)
      self.job_db.ack_node_command(cmd_id, "done")
    except Exception:
      self.log.exception("Failed to execute cluster command %s", command)
      try:
        self.job_db.ack_node_command(cmd_id, "failed")
      except Exception:
        pass
    return False


class ScannerThread(_StoppableThread):
  """Periodically scans configured directories for new media files and queues them.

  Each entry in scan_paths may specify:
    - path       (required) directory to scan
    - interval   seconds between scans (default: 3600)
    - rewrite_from / rewrite_to   path prefix substitution applied before
                 submitting jobs, e.g. scan /mnt/local/Media but submit
                 paths as /mnt/unionfs/Media so config matching works.
  """

  def __init__(self, scan_paths, job_db, server, path_config_manager, logger):
    super().__init__()
    self.scan_paths = scan_paths  # list of dicts from sma-ng.yml
    self.job_db = job_db
    self.server = server
    self.path_config_manager = path_config_manager
    self.log = logger
    # Per-entry next-run timestamps so each path has its own schedule.
    self._next_run = {}

  def run(self):
    if not self.scan_paths:
      return
    self.log.info("Scanner started — %d path(s) configured" % len(self.scan_paths))
    while self.running:
      now = time.monotonic()
      next_wake = now + 60  # re-evaluate at least every minute
      for entry in self.scan_paths:
        path = entry.get("path", "")
        interval = int(entry.get("interval", 3600))
        due = self._next_run.get(path, 0)
        if now >= due:
          try:
            queued = self._scan(entry)
            if queued:
              self.server.notify_workers()
          except Exception:
            self.log.exception("Scanner error for path: %s" % path)
          self._next_run[path] = time.monotonic() + interval
        next_wake = min(next_wake, self._next_run[path])
      sleep_for = max(0, next_wake - time.monotonic())
      self._stop_event.wait(timeout=sleep_for)

  def _scan(self, entry):
    if not entry.get("enabled", True):
      self.log.debug("Scanner: skipping disabled path %s" % entry.get("path", ""))
      return 0

    scan_dir = entry.get("path", "")
    rewrite_from = entry.get("rewrite_from", "")
    rewrite_to = entry.get("rewrite_to", "")

    if not scan_dir or not os.path.isdir(scan_dir):
      self.log.warning("Scanner: path does not exist or is not a directory: %s" % scan_dir)
      return 0

    allowed = self.path_config_manager.media_extensions

    # Walk lazily via os.scandir; filter_unscanned is called in batches so
    # we never hold the entire tree in memory at once.
    _BATCH = 500
    total_seen = 0
    unscanned = []
    batch = []

    stack = [scan_dir]
    while stack:
      current = stack.pop()
      try:
        with os.scandir(current) as it:
          subdirs = []
          for entry in it:
            if entry.name.startswith("."):
              continue
            try:
              is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
              continue
            if is_dir:
              subdirs.append(entry.path)
            else:
              ext = os.path.splitext(entry.name)[1].lower()
              if ext in allowed:
                batch.append(entry.path)
                total_seen += 1
                if len(batch) >= _BATCH:
                  unscanned.extend(self.job_db.filter_unscanned(batch))
                  batch = []
          stack.extend(reversed(subdirs))
      except (PermissionError, OSError):
        pass

    if batch:
      unscanned.extend(self.job_db.filter_unscanned(batch))

    if total_seen == 0:
      self.log.debug("Scanner: no media files found in %s" % scan_dir)
      return 0

    if not unscanned:
      self.log.debug("Scanner: all %d file(s) in %s already scanned" % (total_seen, scan_dir))
      return 0

    self.log.info("Scanner: found %d new file(s) in %s" % (len(unscanned), scan_dir))
    queued = 0
    for filepath in unscanned:
      # Apply path rewrite before config resolution and job submission
      submit_path = filepath
      if rewrite_from and rewrite_to and filepath.startswith(rewrite_from):
        submit_path = rewrite_to + filepath[len(rewrite_from) :]

      resolved_config = self.path_config_manager.get_config_for_path(submit_path)
      job_id = self.job_db.add_job(submit_path, resolved_config, [])
      if job_id is not None:
        self.log.info("Scanner queued job %d: %s" % (job_id, submit_path))
        queued += 1

    # Record all candidates (including already-queued ones) as scanned so
    # we don't re-evaluate them on the next pass.
    self.job_db.record_scanned(unscanned)

    if queued:
      self.log.info("Scanner: queued %d new job(s) from %s" % (queued, scan_dir))
    return queued


class RecycleBinCleanerThread(_StoppableThread):
  """Periodically purges old media files from recycle-bin directories.

  Two eviction triggers (either condition removes a file):
    1. Age: files older than ``max_age_days`` days are deleted.
    2. Space pressure: when the free space on the recycle-bin mount point
       drops below ``min_free_gb`` GiB, the oldest files are deleted first
       until the threshold is satisfied or the directory is empty.

  Only files with recognised media extensions are touched; other files (e.g.
  NFO, artwork) are left in place so nothing important is silently removed.

  ``recycle_bins`` is a list of directory paths to clean.  It is re-read from
  ``path_config_manager`` on every wake cycle so hot-reloading sma-ng.yml
  picks up changes without a restart.
  """

  CHECK_INTERVAL = 3600  # seconds between sweeps

  def __init__(self, path_config_manager, max_age_days, min_free_gb, logger):
    super().__init__()
    self.path_config_manager = path_config_manager
    self.max_age_days = max_age_days
    self.min_free_gb = min_free_gb
    self.log = logger

  # ------------------------------------------------------------------
  # Internal helpers
  # ------------------------------------------------------------------

  def _free_gb(self, path):
    """Return free space in GiB for the mount point that contains *path*."""
    try:
      st = os.statvfs(path)
      return (st.f_bavail * st.f_frsize) / (1024**3)
    except OSError:
      return None

  def _list_media_files(self, directory):
    """Return a list of (mtime, path) tuples for media files in *directory*."""
    results = []
    try:
      for fname in os.listdir(directory):
        fpath = os.path.join(directory, fname)
        if not os.path.isfile(fpath):
          continue
        if os.path.splitext(fname)[1].lower() not in _MEDIA_EXTENSIONS:
          continue
        try:
          results.append((os.path.getmtime(fpath), fpath))
        except OSError:
          pass
    except OSError:
      self.log.warning("RecycleCleaner: cannot list directory %s" % directory)
    return results

  def _delete_file(self, path):
    try:
      os.remove(path)
      self.log.info("RecycleCleaner: deleted %s" % path)
      return True
    except OSError:
      self.log.warning("RecycleCleaner: failed to delete %s" % path)
      return False

  def _clean_directory(self, directory):
    """Run one eviction pass on *directory*. Returns count of deleted files."""
    if not os.path.isdir(directory):
      return 0

    files = sorted(self._list_media_files(directory))  # oldest first
    now = time.time()
    deleted = 0

    # Pass 1 — age eviction
    if self.max_age_days > 0:
      cutoff = now - self.max_age_days * 86400
      for mtime, path in files:
        if mtime < cutoff:
          if self._delete_file(path):
            deleted += 1

    # Re-build list after age pass before checking free space
    files = sorted(self._list_media_files(directory))  # oldest first

    # Pass 2 — space-pressure eviction
    if self.min_free_gb > 0:
      free = self._free_gb(directory)
      if free is None:
        self.log.warning("RecycleCleaner: could not determine free space for %s, skipping space check" % directory)
      else:
        for mtime, path in files:
          if free >= self.min_free_gb:
            break
          if self._delete_file(path):
            deleted += 1
            # Re-query free space so we stop as soon as we've freed enough
            free = self._free_gb(directory) or 0.0

    return deleted

  # ------------------------------------------------------------------
  # Thread entry point
  # ------------------------------------------------------------------

  def run(self):
    if not self.max_age_days and not self.min_free_gb:
      self.log.debug("RecycleCleaner: disabled (max_age_days=0, min_free_gb=0)")
      return
    self.log.info("RecycleCleaner started (max_age_days=%s, min_free_gb=%s)" % (self.max_age_days if self.max_age_days else "disabled", self.min_free_gb if self.min_free_gb else "disabled"))
    while self.running:
      recycle_bins = [b for b in (self.path_config_manager.get_recycle_bin(c) for c in self.path_config_manager.get_all_configs()) if b]
      for directory in set(recycle_bins):
        try:
          deleted = self._clean_directory(directory)
          if deleted:
            self.log.info("RecycleCleaner: removed %d file(s) from %s" % (deleted, directory))
          else:
            self.log.debug("RecycleCleaner: nothing to remove in %s" % directory)
        except Exception:
          self.log.exception("RecycleCleaner: error cleaning %s" % directory)
      self._stop_event.wait(timeout=self.CHECK_INTERVAL)


class ConfigWatcherThread(_StoppableThread):
  """Polls the active sma-ng.yml and triggers DaemonServer.reload_config() on detected changes.

  Watches ``path_config_manager._config_file`` for ``(mtime_ns, size)``
  changes and, after a debounce window with no further change, calls
  ``server.reload_config()`` (the same code path as ``POST /reload``).

  Failure modes are non-fatal: a missing file is logged once and
  polling continues; a failed reload logs a WARNING and does not
  retry until the file changes again.

  Tunable via ``daemon.config_watch.{enabled,interval_seconds,debounce_seconds}``.
  Disabled when the daemon is launched without a resolvable config
  file or when ``enabled: false`` / ``interval_seconds: 0``.
  """

  def __init__(self, server, path_config_manager, settings, logger):
    super().__init__()
    self.server = server
    self.pcm = path_config_manager
    self.interval = max(1, int(settings.interval_seconds))
    self.debounce = max(0, int(settings.debounce_seconds))
    self.log = logger
    self._missing_logged = False

  def _stat_tuple(self):
    path = self.pcm._config_file
    if not path:
      return None
    try:
      st = os.stat(path)
      return (st.st_mtime_ns or int(st.st_mtime), st.st_size)
    except FileNotFoundError:
      return None
    except OSError:
      return None

  def run(self):
    last = self._stat_tuple()
    path = self.pcm._config_file
    self.log.info("Config watcher started: file=%s interval=%ds debounce=%ds" % (path, self.interval, self.debounce))
    while self.running:
      self._stop_event.wait(timeout=self.interval)
      if not self.running:
        return
      current = self._stat_tuple()
      if current is None:
        if not self._missing_logged:
          self.log.debug("Config watcher: %s is currently unreadable; will retry." % self.pcm._config_file)
          self._missing_logged = True
        continue
      self._missing_logged = False
      if current == last:
        continue
      self.log.info("Config change detected at %s — reloading after %ds debounce." % (path, self.debounce))
      # Debounce: wait until the file stops changing for `debounce` seconds.
      stable = current
      settle_deadline = time.monotonic() + self.debounce
      while self.running and time.monotonic() < settle_deadline:
        self._stop_event.wait(timeout=min(0.5, max(0.05, self.debounce / 4 or 0.05)))
        if not self.running:
          return
        latest = self._stat_tuple()
        if latest is None:
          continue
        if latest != stable:
          stable = latest
          settle_deadline = time.monotonic() + self.debounce
      try:
        ok = self.server.reload_config()
        if ok is False:
          self.log.warning("Config reload failed; will retry on next change.")
      except Exception:
        self.log.exception("Config reload raised; will retry on next change.")
      # Record the post-reload tuple regardless of success so we don't
      # spin on the same change.
      last = self._stat_tuple() or stable


class LibraryAuditThread(_StoppableThread):
  """Schedules library-audit runs and acts as the cluster's enumerator.

  One node at a time holds the advisory enumeration lock; that node is
  responsible for (a) creating a new ``library_audit_runs`` row when no
  audit is in flight, (b) walking the configured ``audit_paths`` and
  inserting per-file work units, and (c) flipping completed runs to
  ``completed`` once every queue row is done. Probing itself is performed
  by every node's :class:`LibraryAuditWorkerThread` (workload distribution).

  The thread re-reads ``path_config_manager.audit_settings`` on every cycle
  so a config reload picks up new paths/intervals/skip-dirs without a
  daemon restart.
  """

  def __init__(self, job_db, path_config_manager, server, node_id, logger):
    super().__init__()
    self.job_db = job_db
    self.pcm = path_config_manager
    self.server = server
    self.node_id = node_id
    self.log = logger

  def _settings(self):
    return self.pcm.audit_settings

  def _next_interval(self):
    s = self._settings()
    return max(60, int(s.interval_seconds))

  def _scope_paths(self):
    return [a["path"] for a in self.pcm.audit_paths if a.get("enabled", True) and a.get("path")]

  def run(self):
    if not self.job_db.is_distributed:
      return
    self.log.info("Library audit thread started — interval %ds" % self._next_interval())
    while self.running:
      try:
        self._cycle()
      except Exception:
        self.log.exception("Library audit cycle failed")
      self._stop_event.wait(timeout=self._next_interval())

  def _cycle(self):
    settings = self._settings()
    if not settings.enabled:
      self.log.debug("Library audit disabled — skipping cycle")
      return
    self.job_db.release_stale_audit_claims(settings.claim_stale_seconds)
    completed = self.job_db.complete_finished_audit_runs()
    if completed:
      self.log.info("Library audit completed run(s): %s" % completed)
      self._rollup_completed(completed, settings)
    paths = self._scope_paths()
    if not paths:
      self.log.debug("Library audit has no enabled paths — skipping enumerate")
      return
    if self.job_db.list_active_audit_runs():
      return  # another run is still in progress; let workers drain it
    lock_conn = self.job_db.try_acquire_audit_enumerate_lock()
    if lock_conn is None:
      self.log.debug("Library audit: another node holds the enumerate lock")
      return
    try:
      audit_id = self.job_db.create_audit_run(paths, "scheduled:%s" % self.node_id)
      total = self._enumerate_into_queue(audit_id, paths, settings)
      self.log.info("Library audit run %d enumerated %d work unit(s)" % (audit_id, total))
      if total == 0:
        self.job_db.set_audit_run_status(audit_id, "completed")
    finally:
      self.job_db.release_audit_enumerate_lock(lock_conn)

  def _enumerate_into_queue(self, audit_id, paths, settings):
    is_recycle_bin_path = getattr(self.pcm, "is_recycle_bin_path", None)
    batch = []
    total = 0
    for path, hint in enumerate_paths(paths, skip_dirs=list(settings.skip_dirs), is_recycle_bin_path=is_recycle_bin_path):
      batch.append((path, hint))
      if len(batch) >= 500:
        self.job_db.enqueue_audit_units(audit_id, batch)
        total += len(batch)
        batch = []
        if not self.running:
          return total
    if batch:
      self.job_db.enqueue_audit_units(audit_id, batch)
      total += len(batch)
    return total

  def _rollup_completed(self, audit_ids, settings):
    """For each just-completed run, write DUPLICATE_ID findings then purge media-id scratch rows."""
    engine = AuditEngine(
      self.job_db,
      self.pcm,
      self.log,
      ffmpeg_dir=getattr(self.pcm, "ffmpeg_dir", None),
      dry_run=settings.dry_run,
      auto_fix=settings.auto_fix,
    )
    for audit_id in audit_ids:
      try:
        written = engine.rollup_duplicate_ids(audit_id)
        if written:
          self.log.info("Library audit run %d: wrote %d duplicate-id finding(s)" % (audit_id, written))
      except Exception:
        self.log.exception("Library audit duplicate-id rollup failed for run %d" % audit_id)


class LibraryAuditWorkerThread(_StoppableThread):
  """Per-node probe worker. Claims units and writes findings.

  Self-balancing across the cluster — uses ``FOR UPDATE SKIP LOCKED`` so
  whichever node is fastest claims more units. Concurrency-capped via a
  semaphore so the audit never spawns more ffprobe subprocesses than
  ``audit.concurrency`` per node.
  """

  IDLE_SLEEP_SECONDS = 5

  def __init__(self, job_db, path_config_manager, server, node_id, logger):
    super().__init__()
    self.job_db = job_db
    self.pcm = path_config_manager
    self.server = server
    self.node_id = node_id
    self.log = logger

  def _settings(self):
    return self.pcm.audit_settings

  def run(self):
    if not self.job_db.is_distributed:
      return
    try:
      self.job_db.requeue_audit_claims_for_node(self.node_id)
    except Exception:
      self.log.exception("Library audit worker: requeue-on-startup failed")
    self.log.info("Library audit worker started on node %s" % self.node_id)
    while self.running:
      try:
        progressed = self._tick()
      except Exception:
        self.log.exception("Library audit worker tick failed")
        progressed = False
      if not progressed:
        self._stop_event.wait(timeout=self.IDLE_SLEEP_SECONDS)

  def _tick(self) -> bool:
    settings = self._settings()
    if not settings.enabled:
      return False
    runs = self.job_db.list_active_audit_runs()
    if not runs:
      return False
    engine = AuditEngine(
      self.job_db,
      self.pcm,
      self.log,
      ffmpeg_dir=getattr(self.pcm, "ffmpeg_dir", None),
      dry_run=settings.dry_run,
      auto_fix=settings.auto_fix,
    )
    sem = threading.Semaphore(max(1, int(settings.concurrency)))
    progressed = False
    for run in runs:
      if not self.running:
        break
      units = self.job_db.claim_audit_units(self.node_id, run["id"], batch=int(settings.batch_size))
      if not units:
        continue
      progressed = True
      threads = []
      for unit in units:
        if not self.running:
          break
        sem.acquire()
        t = threading.Thread(target=self._process_unit, args=(unit, run["id"], engine, sem), daemon=True)
        t.start()
        threads.append(t)
      for t in threads:
        t.join()
      # If a conversion was queued, wake conversion workers.
      try:
        self.server.notify_workers()
      except Exception:
        pass
    return progressed

  def _process_unit(self, unit, run_id, engine: AuditEngine, sem):
    try:
      finding = engine.probe_one(unit)
      if finding is not None:
        engine.upsert(finding, run_id)
        action = engine.maybe_auto_fix(finding)
        if action != "skipped" and action != "dry_run":
          self.log.info(
            "Library audit %s: %s (%s)" % (finding.kind.value, finding.path, action),
            extra={"audit_id": run_id, "kind": finding.kind.value, "action": action, "path": finding.path},
          )
      self.job_db.mark_audit_unit_done(unit["id"])
    except Exception as exc:
      try:
        self.job_db.mark_audit_unit_done(unit["id"], error=str(exc)[:512])
      except Exception:
        self.log.exception("Library audit: failed to mark unit %s as error" % unit["id"])
      self.log.exception("Library audit probe error for %s" % unit.get("path"))
    finally:
      sem.release()
