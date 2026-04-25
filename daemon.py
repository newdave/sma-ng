#!/opt/sma/venv/bin/python3
"""
SMA-NG Daemon - HTTP webhook server for triggering media conversions.

Listens for HTTP POST requests containing absolute file/directory paths
and spawns conversion processes using manual.py.

Features:
- Path-based configuration selection via sma-ng.yml Daemon section
- Per-config logging to separate files in logs/ directory
- Only one process per config runs at a time (others queue)
- PostgreSQL persistence for job queue (survives restarts)
- API key authentication for webhook endpoints

Usage:
    python daemon.py                    # Uses default settings
    python daemon.py --port 8585        # Override port
    python daemon.py --host 0.0.0.0     # Listen on all interfaces
    python daemon.py --api-key SECRET   # Require API key for requests
"""

import argparse
import os
import signal
import sys
import threading

from resources.daemon import (
  STATUS_COMPLETED,  # pyright: ignore[reportUnusedImport]  # re-exported for tests
  STATUS_FAILED,  # pyright: ignore[reportUnusedImport]
  STATUS_PENDING,  # pyright: ignore[reportUnusedImport]
  STATUS_RUNNING,  # pyright: ignore[reportUnusedImport]
  ConfigLockManager,
  ConfigLogManager,
  ConversionWorker,  # pyright: ignore[reportUnusedImport]
  DaemonServer,
  HeartbeatThread,  # pyright: ignore[reportUnusedImport]
  PathConfigManager,
  PostgreSQLJobDatabase,
  ScannerThread,  # pyright: ignore[reportUnusedImport]
  WebhookHandler,
  WorkerPool,  # pyright: ignore[reportUnusedImport]
  _inline,  # pyright: ignore[reportUnusedImport]
  _load_dashboard_html,  # pyright: ignore[reportUnusedImport]
  _render_markdown_to_html,  # pyright: ignore[reportUnusedImport]
  _StoppableThread,  # pyright: ignore[reportUnusedImport]
)
from resources.daemon.constants import LOGS_DIR, SCRIPT_DIR, resolve_node_id  # pyright: ignore[reportUnusedImport]
from resources.daemon.server import _validate_hwaccel
from resources.log import getLogger

# Main daemon logger
log = getLogger("DAEMON")

_SMOKE_TEST_FIXTURE = os.path.join(SCRIPT_DIR, "tests", "fixtures", "test1.mkv")
# Backward-compatible module attribute for older tests/extensions that patch it.
DEFAULT_DAEMON_CONFIG = os.path.join(SCRIPT_DIR, "config", "daemon.json")


def _build_db_url_from_env():
  """Construct a PostgreSQL URL from SMA_DAEMON_DB_* component env vars.

  Returns None if SMA_DAEMON_DB_HOST is not set (the minimum required field).
  """
  host = os.environ.get("SMA_DAEMON_DB_HOST", "")
  if not host:
    return None
  user = os.environ.get("SMA_DAEMON_DB_USER", "sma")
  password = os.environ.get("SMA_DAEMON_DB_PASSWORD", "")
  port = os.environ.get("SMA_DAEMON_DB_PORT", "5432")
  dbname = os.environ.get("SMA_DAEMON_DB_NAME", "sma")
  if password:
    return "postgresql://%s:%s@%s:%s/%s" % (user, password, host, port, dbname)
  return "postgresql://%s@%s:%s/%s" % (user, host, port, dbname)


def run_smoke_test(path_config_manager, ffmpeg_dir, logger):
  """Run a dry-run option-generation check against every configured autoProcess config.

  Uses MediaProcessor.jsonDump() which runs ffprobe on the fixture file and
  builds the full FFmpeg command string, but does not execute FFmpeg.  Exits
  with code 1 if any config fails so systemd can abort the start.

  Args:
      path_config_manager: PathConfigManager with the loaded daemon configuration.
      ffmpeg_dir: Optional directory to prepend to PATH for ffprobe.
      logger: Logger instance.
  """
  fixture = _SMOKE_TEST_FIXTURE
  if not os.path.exists(fixture):
    logger.warning("Smoke test fixture not found, skipping: %s" % fixture)
    return

  if ffmpeg_dir:
    os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

  from resources.mediaprocessor import MediaProcessor
  from resources.readsettings import ReadSettings

  configs = path_config_manager.get_all_configs()
  failed = []

  logger.info("Running startup smoke test against %d config(s)..." % len(configs))
  for config_path in sorted(configs):
    label = os.path.basename(config_path)
    if not os.path.exists(config_path):
      logger.warning("  [SKIP] %s — config file not found" % label)
      continue
    try:
      settings = ReadSettings(configFile=config_path)
      mp = MediaProcessor(settings, logger=logger)
      dump = mp.jsonDump(fixture)
      import json as _json

      parsed = _json.loads(dump)
      output = parsed.get("output", {})
      video_codec = output.get("video", {}).get("codec", "") if isinstance(output, dict) else ""
      logger.info("  [OK]   %s  (video -> %s)" % (label, video_codec or "copy/bypass"))
    except Exception:
      logger.exception("  [FAIL] %s — smoke test raised an exception" % label)
      failed.append(label)

  if failed:
    logger.error("Smoke test FAILED for %d config(s): %s" % (len(failed), ", ".join(failed)))
    sys.exit(1)

  logger.info("Smoke test passed.")


def main():
  """Parse CLI arguments, configure the daemon, and start the HTTP server.

  Resolves configuration from CLI flags, environment variables, and
  the ``Daemon`` config section (in that priority order). Initialises the job database
  PostgreSQL, sets up per-config logging and concurrency locks,
  and then serves requests until interrupted.
  """
  parser = argparse.ArgumentParser(description="SMA-NG Daemon - HTTP webhook server for media conversion")
  parser.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
  parser.add_argument("--port", type=int, default=8585, help="Port to listen on (default: 8585)")
  parser.add_argument("--workers", type=int, default=1, help="Number of worker threads (default: 1)")
  parser.add_argument("-d", "--daemon-config", help="Path to daemon config file (defaults to sma-ng.yml)")
  parser.add_argument("--logs-dir", default=LOGS_DIR, help="Directory for per-config log files (default: logs/)")
  parser.add_argument(
    "--ffmpeg-dir", help="Directory containing ffmpeg and ffprobe binaries. Prepended to PATH for each conversion subprocess. If omitted, relies on PATH already containing the binaries."
  )
  parser.add_argument("--heartbeat-interval", type=int, default=30, help="Seconds between cluster heartbeat updates (default: 30). Only used with PostgreSQL backend.")
  parser.add_argument(
    "--stale-seconds",
    type=int,
    default=120,
    help="Seconds without a heartbeat before a node is declared stale and its running jobs are requeued (default: 120). Only used with PostgreSQL backend.",
  )
  parser.add_argument("--api-key", help="API key for authentication (or set SMA_DAEMON_API_KEY env var)")
  parser.add_argument(
    "--smoke-test", action="store_true", help="Run a dry-run option-generation check against all configured autoProcess files at startup, then exit with 0 on success or 1 on failure"
  )
  parser.add_argument(
    "--job-timeout",
    type=int,
    default=0,
    metavar="SECONDS",
    help="Maximum seconds a conversion job may run before being killed (default: 0, no timeout). Can also be set via Daemon job_timeout_seconds.",
  )

  args = parser.parse_args()

  log.info("SMA-NG Daemon starting...")
  log.debug("Python %s" % sys.version)

  # Initialize managers
  config_log_manager = ConfigLogManager(args.logs_dir)
  config_lock_manager = ConfigLockManager(max_per_config=args.workers, logger=log)
  path_config_manager = PathConfigManager(args.daemon_config, logger=log)

  # Determine API key (priority: CLI arg > env var > config file)
  api_key = args.api_key or os.environ.get("SMA_DAEMON_API_KEY") or path_config_manager.api_key

  # Determine Basic Auth credentials (priority: env vars > config file)
  # Note: not accepted on CLI to prevent credentials appearing in ps output.
  _env_user = os.environ.get("SMA_DAEMON_USERNAME")
  _env_pass = os.environ.get("SMA_DAEMON_PASSWORD")
  basic_auth = (_env_user, _env_pass) if _env_user and _env_pass else path_config_manager.basic_auth

  # Determine FFmpeg directory (priority: CLI --ffmpeg-dir > env var > config file)
  ffmpeg_dir = args.ffmpeg_dir or os.environ.get("SMA_DAEMON_FFMPEG_DIR") or path_config_manager.ffmpeg_dir

  # Determine job timeout (priority: CLI --job-timeout > config; 0 means no timeout)
  job_timeout_seconds = args.job_timeout or path_config_manager.job_timeout_seconds
  progress_log_interval = path_config_manager.progress_log_interval

  # Run smoke test if requested via CLI or config.
  # --smoke-test on CLI: run check and exit (no DB/server needed — safe pre-flight).
  # smoke_test in config: run check then continue startup; exit 1 on failure.
  if args.smoke_test or path_config_manager.smoke_test:
    run_smoke_test(path_config_manager, ffmpeg_dir, log)
    if args.smoke_test:
      sys.exit(0)

  # Determine database (priority: env var > component env vars > config file)
  # Note: PostgreSQL URL is not accepted on the CLI to prevent credentials appearing in ps output.
  db_url = os.environ.get("SMA_DAEMON_DB_URL") or _build_db_url_from_env() or path_config_manager.db_url
  if not db_url:
    log.error("No database URL configured. Set SMA_DAEMON_DB_URL (or SMA_DAEMON_DB_HOST + SMA_DAEMON_DB_PASSWORD) or db_url in the Daemon config section")
    sys.exit(1)
  job_db = PostgreSQLJobDatabase(db_url, logger=log)
  db_label = "PostgreSQL: %s" % db_url

  log.info("Node: %s" % resolve_node_id())
  log.info("Database: %s" % db_label)
  if ffmpeg_dir:
    log.debug("FFmpeg/FFprobe directory: %s" % ffmpeg_dir)
  log.debug("Heartbeat interval: %ds (stale after %ds)" % (args.heartbeat_interval, args.stale_seconds))
  log.debug("Logs directory: %s" % config_log_manager.logs_dir)
  log.debug("Concurrency: One process per config (jobs for same config queue)")
  if job_timeout_seconds:
    log.debug("Job timeout: %ds" % job_timeout_seconds)
  else:
    log.debug("Job timeout: disabled")
  if api_key:
    log.info("Authentication: ENABLED (API key required)")
  elif basic_auth:
    log.info("Authentication: ENABLED (HTTP Basic Auth required)")
  else:
    log.info("Authentication: DISABLED (no credentials configured — suitable for use behind a reverse proxy)")

  # Show config mappings
  log.debug("Config to log file mappings:")
  for config_path in path_config_manager.get_all_configs():
    log_file = config_log_manager.get_log_file(config_path)
    exists = "OK" if os.path.exists(config_path) else "MISSING"
    log.debug("  %s [%s] -> %s" % (config_path, exists, log_file))

  server_address = (args.host, args.port)

  try:
    server = DaemonServer(
      server_address,
      WebhookHandler,
      job_db,
      path_config_manager,
      config_log_manager,
      config_lock_manager,
      log,
      worker_count=args.workers,
      api_key=api_key,
      basic_auth=basic_auth,
      heartbeat_interval=args.heartbeat_interval,
      stale_seconds=args.stale_seconds,
      ffmpeg_dir=ffmpeg_dir,
      cli_api_key=args.api_key,
      cli_basic_auth=None,  # basic_auth is env/config only — no CLI exposure
      cli_ffmpeg_dir=args.ffmpeg_dir,
      job_timeout_seconds=job_timeout_seconds,
      progress_log_interval=progress_log_interval,
    )

    log.info("Listening on http://%s:%d" % (args.host, args.port))
    log.debug("Worker threads: %d" % args.workers)
    if path_config_manager.scan_paths:
      log.debug("Scheduled scans: %d path(s)" % len(path_config_manager.scan_paths))
      for sp in path_config_manager.scan_paths:
        rw = (" -> " + sp["rewrite_to"]) if sp.get("rewrite_to") else ""
        log.debug("  %s (every %ds)%s" % (sp["path"], sp.get("interval", 3600), rw))
    else:
      log.debug("Scheduled scans: none configured")
    log.debug("Endpoints:")
    log.debug("  POST /webhook/generic - Submit conversion job")
    log.debug("  GET  /health       - Health check with job stats")
    log.debug("  GET  /jobs         - List jobs (?status=pending&limit=50)")
    log.debug("  GET  /jobs/<id>    - Get specific job (includes progress when running)")
    log.debug("  POST /jobs/<id>/cancel  - Cancel a pending or running job")
    log.debug("  GET  /configs      - Show config mappings and status")
    log.debug("  GET  /stats        - Job statistics")
    log.debug("  POST /cleanup      - Remove old jobs (?days=30)")
    log.debug("  GET  /scan         - Check unscanned paths (?path=... for small lists)")
    log.debug("  POST /scan/filter  - Check unscanned paths (JSON body for large lists)")
    log.debug("  POST /scan/record  - Record paths as scanned")
    log.debug("  POST /reload       - Reload daemon config without stopping workers")
    log.debug("  POST /shutdown     - Graceful shutdown (waits for active conversions)")
    log.debug("  POST /restart      - Graceful restart (drains workers, then re-execs)")
    log.info("Ready to accept connections.")

    server.detected_hwaccel = _validate_hwaccel(path_config_manager, ffmpeg_dir, log)

    def _shutdown(signum, frame):
      log.info("Received signal %d, shutting down..." % signum)
      # shutdown() is blocking — run in a thread so the signal handler returns
      threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    def _restart(signum, frame):
      log.info("Received SIGHUP, initiating graceful restart...")
      threading.Thread(target=server.graceful_restart, daemon=True).start()

    signal.signal(signal.SIGHUP, _restart)

    server.serve_forever()

  except Exception as e:
    log.exception("Server error: %s" % e)
    sys.exit(1)


if __name__ == "__main__":
  main()
