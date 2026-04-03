import os
import threading
import time

from resources.log import getLogger

log = getLogger("DAEMON")


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

    Only active when using PostgreSQLJobDatabase (no-op for SQLite).
    """

    def __init__(self, job_db, node_id, host, worker_count, server, interval, stale_seconds, logger, started_at):
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

    def run(self):
        if not self.job_db.is_distributed:
            return  # Heartbeat only meaningful for the shared PG backend
        while self.running:
            try:
                command = self.job_db.heartbeat(self.node_id, self.host, self.worker_count, self.started_at)
                if command == "restart":
                    self.log.info("Received remote restart command via cluster DB")
                    threading.Thread(target=self.server.graceful_restart, daemon=True).start()
                    return
                elif command == "shutdown":
                    self.log.info("Received remote shutdown command via cluster DB")
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                recovered = self.job_db.recover_stale_nodes(self.stale_seconds)
                for stale_id, job_count in recovered:
                    self.log.warning("Recovered %d jobs from stale node %s" % (job_count, stale_id))
                if any(job_count > 0 for _, job_count in recovered):
                    self.server.notify_workers()  # Wake workers to pick up requeued jobs
            except Exception:
                self.log.exception("Heartbeat error")
            self._stop_event.wait(timeout=self.interval)


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
        self.scan_paths = scan_paths  # list of dicts from daemon.json
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
        # Skip already-converted files; scanning .mp4 files serves no purpose since
        # SMA converts *to* mp4 — any .mp4 present is either already processed or
        # a non-SMA file that would just be re-queued on every scan.
        skip_extensions = frozenset([".mp4"])
        candidates = []
        for root, dirs, files in os.walk(scan_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if fname.startswith("."):
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext in allowed and ext not in skip_extensions:
                    candidates.append(os.path.join(root, fname))

        if not candidates:
            self.log.debug("Scanner: no media files found in %s" % scan_dir)
            return 0

        # Filter to only files not yet recorded as scanned
        unscanned = self.job_db.filter_unscanned(candidates)
        if not unscanned:
            self.log.debug("Scanner: all %d file(s) in %s already scanned" % (len(candidates), scan_dir))
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
