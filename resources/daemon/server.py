import configparser
import ipaddress
import os
import socket
import subprocess
import sys
from datetime import datetime
from http.server import ThreadingHTTPServer

from resources.daemon.constants import resolve_node_id
from resources.daemon.threads import HeartbeatThread, RecycleBinCleanerThread, ScannerThread
from resources.daemon.worker import WorkerPool


def _is_public_ip_address(value):
  """Return True when *value* is a concrete non-loopback IP address."""
  try:
    ip = ipaddress.ip_address(value)
  except ValueError:
    return False
  return not (ip.is_unspecified or ip.is_loopback)


def _resolve_advertised_host(bind_host, node_id, prefer_node_id=False):
  """Return the host value that should be advertised in cluster status.

  Prefer the configured bind host when it is already a concrete address.
  When the daemon binds to all interfaces (for example ``0.0.0.0`` inside
  Docker), resolve the node hostname to an address and fall back to the
  outbound interface address before ultimately falling back to the hostname.
  When ``prefer_node_id`` is True, return ``node_id`` directly. This is used
  when ``SMA_NODE_NAME`` is configured so cluster host identity remains stable
  and human-readable across restarts.
  """
  if prefer_node_id:
    return node_id

  bind_host = (bind_host or "").strip()
  if bind_host and _is_public_ip_address(bind_host):
    return bind_host

  try:
    resolved_host = socket.gethostbyname(node_id)
    if _is_public_ip_address(resolved_host):
      return resolved_host
  except OSError:
    pass

  try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
      sock.connect(("8.8.8.8", 80))
      outbound_host = sock.getsockname()[0]
    if _is_public_ip_address(outbound_host):
      return outbound_host
  except OSError:
    pass

  return node_id


class DaemonServer(ThreadingHTTPServer):
  """HTTP server with job queue and worker threads."""

  daemon_threads = True
  request_queue_size = 128

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
    basic_auth=None,
    heartbeat_interval=30,
    stale_seconds=120,
    ffmpeg_dir=None,
    cli_api_key=None,
    cli_ffmpeg_dir=None,
    cli_basic_auth=None,
    job_timeout_seconds=0,
    progress_log_interval=60,
  ):
    super().__init__(server_address, handler_class)
    self.job_db = job_db
    self.path_config_manager = path_config_manager
    self.config_log_manager = config_log_manager
    self.config_lock_manager = config_lock_manager
    self.logger = logger
    self.worker_count = worker_count
    self.api_key = api_key
    self.basic_auth = basic_auth  # (username, password) tuple or None
    self.stale_seconds = stale_seconds
    self.node_id = resolve_node_id()
    self.detected_hwaccel: str = ""
    self.started_at = datetime.now().astimezone()
    self._cli_api_key = cli_api_key
    self._cli_basic_auth = cli_basic_auth
    self._cli_ffmpeg_dir = cli_ffmpeg_dir
    self._job_processes = {}  # job_id -> Popen, for cancel support
    self._job_progress = {}  # job_id -> structured progress payload from FFmpeg output

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
      progress_log_interval=progress_log_interval,
      job_processes=self._job_processes,
      job_progress=self._job_progress,
    )

    # Wake all workers if there are jobs waiting from a previous run.
    pending = job_db.pending_count()
    if pending > 0:
      logger.info("Found %d pending jobs from previous run" % pending)
      self.notify_workers()

    # Start heartbeat thread (only does real work with PostgreSQL backend)
    configured_node_name = os.environ.get("SMA_NODE_NAME", "").strip()
    self.heartbeat_thread = HeartbeatThread(
      job_db=job_db,
      node_id=self.node_id,
      host=_resolve_advertised_host(
        server_address[0],
        self.node_id,
        prefer_node_id=bool(configured_node_name),
      ),
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

    # Start recycle-bin cleaner thread
    self.recycle_cleaner_thread = RecycleBinCleanerThread(
      path_config_manager=path_config_manager,
      max_age_days=path_config_manager.recycle_bin_max_age_days,
      min_free_gb=path_config_manager.recycle_bin_min_free_gb,
      logger=logger,
    )
    self.recycle_cleaner_thread.start()

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
      return False

    self.logger.info("Reloading configuration from %s..." % self.path_config_manager._config_file)
    try:
      self.path_config_manager.load_config(self.path_config_manager._config_file)
    except Exception:
      self.logger.error("Configuration reload failed; keeping previous runtime settings.")
      return False

    # Re-apply api_key priority: CLI arg > env var > config file
    self.api_key = self._cli_api_key or os.environ.get("SMA_DAEMON_API_KEY") or self.path_config_manager.api_key

    # Re-apply basic_auth priority: CLI arg > env vars > config file
    env_user = os.environ.get("SMA_DAEMON_USERNAME")
    env_pass = os.environ.get("SMA_DAEMON_PASSWORD")
    env_basic = (env_user, env_pass) if env_user and env_pass else None
    self.basic_auth = self._cli_basic_auth or env_basic or self.path_config_manager.basic_auth

    # Re-apply ffmpeg_dir priority: CLI arg > env var > config file
    new_ffmpeg_dir = self._cli_ffmpeg_dir or os.environ.get("SMA_DAEMON_FFMPEG_DIR") or self.path_config_manager.ffmpeg_dir
    for worker in self.worker_pool._workers:
      worker.ffmpeg_dir = new_ffmpeg_dir
      worker.job_timeout_seconds = self.path_config_manager.job_timeout_seconds
      worker.progress_log_interval = self.path_config_manager.progress_log_interval

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

    # Restart recycle-bin cleaner with updated settings
    self.recycle_cleaner_thread.stop()
    self.recycle_cleaner_thread.join(timeout=5)
    self.recycle_cleaner_thread = RecycleBinCleanerThread(
      path_config_manager=self.path_config_manager,
      max_age_days=self.path_config_manager.recycle_bin_max_age_days,
      min_free_gb=self.path_config_manager.recycle_bin_min_free_gb,
      logger=self.logger,
    )
    self.recycle_cleaner_thread.start()

    self.logger.info("Configuration reloaded.")
    return True

  def graceful_restart(self):
    """Drain active conversions then re-exec the daemon process."""
    self.logger.info("Graceful restart — waiting for active conversions to finish...")

    self.worker_pool.stop()
    self.heartbeat_thread.stop()
    self.scanner_thread.stop()
    self.recycle_cleaner_thread.stop()

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
        self.job_db.mark_node_offline(self.node_id, remove=True)
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
    self.recycle_cleaner_thread.stop()

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
        self.job_db.mark_node_offline(self.node_id, remove=True)
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
  detected_keyword = ""

  def _parse_qsv_device(config_parser):
    """Resolve QSV device from [Converter] hwdevices, falling back to renderD128."""
    default_device = "/dev/dri/renderD128"
    try:
      raw = config_parser.get("Converter", "hwdevices", fallback="").strip()
    except Exception:
      return default_device
    if not raw:
      return default_device

    for item in raw.split(","):
      split = item.split(":", 1)
      if len(split) != 2:
        continue
      key = split[0].strip().lower()
      value = split[1].strip()
      if key == "qsv" and value:
        return value
    return default_device

  def _probe_qsv_init(config_path, config_parser):
    """Run a dedicated QSV device-init probe and log actionable diagnostics."""
    device = _parse_qsv_device(config_parser)
    try:
      result = subprocess.run(
        [
          "ffmpeg",
          "-loglevel",
          "error",
          "-qsv_device",
          device,
          "-f",
          "lavfi",
          "-i",
          "nullsrc",
          "-t",
          "0.1",
          "-c:v",
          "h264_qsv",
          "-f",
          "null",
          "-",
        ],
        capture_output=True,
        env=env,
        timeout=15,
        text=True,
      )
      if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown ffmpeg error").strip()
        logger.warning(
          "QSV initialization self-check failed for device '%s' (config %s). ffmpeg reported: %s. "
          "If running in Docker, ensure /dev/dri is mounted and verify VAAPI in-container with 'vainfo'." % (device, os.path.basename(config_path), detail)
        )
      else:
        logger.info("QSV initialization self-check passed for device '%s'" % device)
    except FileNotFoundError:
      logger.warning("ffmpeg not found in PATH — cannot run QSV initialization self-check")
    except subprocess.TimeoutExpired:
      logger.warning("QSV initialization self-check timed out for device '%s'" % device)
    except Exception as exc:
      logger.warning("QSV initialization self-check failed unexpectedly for device '%s': %s" % (device, exc))

  for config_path in path_config_manager.get_all_configs():
    if not os.path.exists(config_path):
      continue
    try:
      cp = configparser.ConfigParser()
      cp.read(config_path)
      codec_val = cp.get("Video", "codec", fallback="").strip().lower()
      if not codec_val:
        codec_val = cp.get("Video", "video-codec", fallback="").strip().lower()
        if codec_val:
          logger.warning("Config %s uses legacy [Video] video-codec; use [Video] codec instead." % os.path.basename(config_path))
    except Exception:
      continue

    for keyword, encoder in _hwaccel_map.items():
      if keyword in codec_val and encoder not in seen:
        seen.add(encoder)
        if not detected_keyword:
          detected_keyword = keyword
        try:
          result = subprocess.run(
            ["ffmpeg", "-f", "lavfi", "-i", "nullsrc", "-t", "0.1", "-c:v", encoder, "-f", "null", "-", "-loglevel", "error"],
            capture_output=True,
            env=env,
            timeout=15,
          )
          if result.returncode != 0:
            logger.warning("Hardware encoder '%s' (from config %s) does not appear to be available. Conversions may fail. Check driver/SDK installation." % (encoder, os.path.basename(config_path)))
          else:
            logger.info("Hardware encoder '%s' validated OK" % encoder)

          if keyword == "qsv":
            _probe_qsv_init(config_path, cp)
        except FileNotFoundError:
          logger.warning("ffmpeg not found in PATH — cannot validate hardware encoder '%s'" % encoder)
        except subprocess.TimeoutExpired:
          logger.warning("Hardware encoder probe for '%s' timed out" % encoder)
        except Exception as exc:
          logger.warning("Hardware encoder probe for '%s' failed: %s" % (encoder, exc))

  return detected_keyword
