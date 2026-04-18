#!/opt/sma/venv/bin/python3
"""
SMA-NG Daemon - HTTP webhook server for triggering media conversions.

Listens for HTTP POST requests containing absolute file/directory paths
and spawns conversion processes using manual.py.

Features:
- Path-based configuration selection via config/daemon.json
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
import socket
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
from resources.daemon.constants import DEFAULT_DAEMON_CONFIG, LOGS_DIR, SCRIPT_DIR  # pyright: ignore[reportUnusedImport]
from resources.daemon.server import _validate_hwaccel
from resources.log import getLogger

# Main daemon logger
log = getLogger("DAEMON")

_SMOKE_TEST_FIXTURE = os.path.join(SCRIPT_DIR, "tests", "fixtures", "test1.mkv")


def run_smoke_test(path_config_manager, ffmpeg_dir, logger):
    """Run a dry-run option-generation check against every configured autoProcess.ini.

    Uses MediaProcessor.jsonDump() which runs ffprobe on the fixture file and
    builds the full FFmpeg command string, but does not execute FFmpeg.  Exits
    with code 1 if any config fails so systemd can abort the start.

    Args:
        path_config_manager: PathConfigManager with the loaded daemon.json.
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
    ``daemon.json`` (in that priority order). Initialises the job database
    PostgreSQL, sets up per-config logging and concurrency locks,
    and then serves requests until interrupted.
    """
    parser = argparse.ArgumentParser(description="SMA-NG Daemon - HTTP webhook server for media conversion")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8585, help="Port to listen on (default: 8585)")
    parser.add_argument("--workers", type=int, default=1, help="Number of worker threads (default: 1)")
    parser.add_argument("-d", "--daemon-config", help="Path to daemon.json config file (path mappings)")
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
        "--smoke-test", action="store_true", help="Run a dry-run option-generation check against all configured autoProcess.ini files at startup, then exit with 0 on success or 1 on failure"
    )
    parser.add_argument(
        "--job-timeout",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Maximum seconds a conversion job may run before being killed (default: 0, no timeout). Can also be set via daemon.json job_timeout_seconds.",
    )

    args = parser.parse_args()

    log.info("SMA-NG Daemon starting...")
    log.info("Python %s" % sys.version)

    # Initialize managers
    config_log_manager = ConfigLogManager(args.logs_dir)
    config_lock_manager = ConfigLockManager(max_per_config=args.workers, logger=log)
    path_config_manager = PathConfigManager(args.daemon_config, logger=log)

    # Determine API key (priority: CLI arg > env var > config file)
    api_key = args.api_key or os.environ.get("SMA_DAEMON_API_KEY") or path_config_manager.api_key

    # Determine FFmpeg directory (priority: CLI --ffmpeg-dir > env var > config file)
    ffmpeg_dir = args.ffmpeg_dir or os.environ.get("SMA_DAEMON_FFMPEG_DIR") or path_config_manager.ffmpeg_dir

    # Determine job timeout (priority: CLI --job-timeout > daemon.json; 0 means no timeout)
    job_timeout_seconds = args.job_timeout or path_config_manager.job_timeout_seconds

    # Run smoke test if requested via CLI or daemon.json.
    # --smoke-test on CLI: run check and exit (no DB/server needed — safe pre-flight).
    # smoke_test in daemon.json: run check then continue startup; exit 1 on failure.
    if args.smoke_test or path_config_manager.smoke_test:
        run_smoke_test(path_config_manager, ffmpeg_dir, log)
        if args.smoke_test:
            sys.exit(0)

    # Determine database (priority: env var > config file)
    # Note: PostgreSQL URL is not accepted on the CLI to prevent credentials appearing in ps output.
    db_url = os.environ.get("SMA_DAEMON_DB_URL") or path_config_manager.db_url
    if not db_url:
        log.error("No database URL configured. Set SMA_DAEMON_DB_URL or db_url in daemon.json")
        sys.exit(1)
    job_db = PostgreSQLJobDatabase(db_url, logger=log)
    db_label = "PostgreSQL: %s" % db_url

    log.info("Node: %s" % socket.gethostname())
    log.info("Database: %s" % db_label)
    if ffmpeg_dir:
        log.info("FFmpeg/FFprobe directory: %s" % ffmpeg_dir)
    log.info("Heartbeat interval: %ds (stale after %ds)" % (args.heartbeat_interval, args.stale_seconds))
    log.info("Logs directory: %s" % config_log_manager.logs_dir)
    log.info("Concurrency: One process per config (jobs for same config queue)")
    if job_timeout_seconds:
        log.info("Job timeout: %ds" % job_timeout_seconds)
    else:
        log.info("Job timeout: disabled")
    if api_key:
        log.info("Authentication: ENABLED (API key required)")
    else:
        log.info("Authentication: DISABLED (no API key configured)")

    # Show config mappings
    log.info("Config to log file mappings:")
    for config_path in path_config_manager.get_all_configs():
        log_file = config_log_manager.get_log_file(config_path)
        exists = "OK" if os.path.exists(config_path) else "MISSING"
        log.info("  %s [%s] -> %s" % (config_path, exists, log_file))

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
            heartbeat_interval=args.heartbeat_interval,
            stale_seconds=args.stale_seconds,
            ffmpeg_dir=ffmpeg_dir,
            cli_api_key=args.api_key,
            cli_ffmpeg_dir=args.ffmpeg_dir,
            job_timeout_seconds=job_timeout_seconds,
        )

        log.info("Listening on http://%s:%d" % (args.host, args.port))
        log.info("Worker threads: %d" % args.workers)
        if path_config_manager.scan_paths:
            log.info("Scheduled scans: %d path(s)" % len(path_config_manager.scan_paths))
            for sp in path_config_manager.scan_paths:
                rw = (" -> " + sp["rewrite_to"]) if sp.get("rewrite_to") else ""
                log.info("  %s (every %ds)%s" % (sp["path"], sp.get("interval", 3600), rw))
        else:
            log.info("Scheduled scans: none configured")
        log.info("Endpoints:")
        log.info("  POST /webhook      - Submit conversion job")
        log.info("  GET  /health       - Health check with job stats")
        log.info("  GET  /jobs         - List jobs (?status=pending&limit=50)")
        log.info("  GET  /jobs/<id>    - Get specific job (includes progress when running)")
        log.info("  POST /jobs/<id>/cancel  - Cancel a pending or running job")
        log.info("  GET  /configs      - Show config mappings and status")
        log.info("  GET  /stats        - Job statistics")
        log.info("  POST /cleanup      - Remove old jobs (?days=30)")
        log.info("  GET  /scan         - Check unscanned paths (?path=... for small lists)")
        log.info("  POST /scan/filter  - Check unscanned paths (JSON body for large lists)")
        log.info("  POST /scan/record  - Record paths as scanned")
        log.info("  POST /reload       - Reload daemon.json config without stopping workers")
        log.info("  POST /shutdown     - Graceful shutdown (waits for active conversions)")
        log.info("  POST /restart      - Graceful restart (drains workers, then re-execs)")
        log.info("")
        log.info("Ready to accept connections.")

        _validate_hwaccel(path_config_manager, ffmpeg_dir, log)

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
