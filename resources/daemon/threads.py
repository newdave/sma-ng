import os
import threading
import time

from resources.daemon.log_archiver import LogArchiver
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
