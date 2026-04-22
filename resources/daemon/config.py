import configparser
import json
import logging
import os
import shlex
import threading
from logging.handlers import RotatingFileHandler

from resources.daemon.constants import DEFAULT_DAEMON_CONFIG, DEFAULT_PROCESS_CONFIG, LOGS_DIR, SCRIPT_DIR
from resources.daemon.context import JobContextFilter
from resources.log import LOG_BACKUP_COUNT, LOG_MAX_BYTES, JSONFormatter, getLogger

log = getLogger("DAEMON")


class ConfigLockManager:
    """
    Manages per-config concurrency using semaphores.

    Up to `max_per_config` jobs for the same config can run simultaneously.
    Jobs for different configs can always run in parallel (up to worker count).

    Locking strategy:
    - `_master_lock` protects `_config_sems` and `_active_configs` dict mutations.
    - Semaphore acquisition happens *outside* `_master_lock` to avoid deadlock;
      the waiting count is therefore advisory (used only for logging).
    - `_active_configs[config_path]` is a dict keyed by job_id for O(1) insert/remove.
    """

    def __init__(self, max_per_config=1, logger=None):
        self.log = logger or log
        self.max_per_config = max_per_config
        self._master_lock = threading.Lock()
        self._config_sems = {}  # config_path -> Semaphore
        self._active_configs = {}  # config_path -> {job_id: job_path}
        self._waiting_counts = {}  # config_path -> advisory waiting count (for logging)

    def _get_sem(self, config_path):
        """Get or create a semaphore for a config (thread-safe)."""
        with self._master_lock:
            if config_path not in self._config_sems:
                self._config_sems[config_path] = threading.Semaphore(self.max_per_config)
                self._waiting_counts[config_path] = 0
                self._active_configs[config_path] = {}
            return self._config_sems[config_path]

    def acquire(self, config_path, job_id, job_path):
        """
        Acquire a slot for a config. Blocks until a slot is available.
        Returns True when acquired.
        """
        sem = self._get_sem(config_path)

        with self._master_lock:
            self._waiting_counts[config_path] = self._waiting_counts.get(config_path, 0) + 1
            active = self._active_configs.get(config_path, {})
            if len(active) >= self.max_per_config:
                self.log.info("Job %d waiting for config slot: %s (%d/%d slots in use)" % (job_id, os.path.basename(config_path), len(active), self.max_per_config))

        sem.acquire()

        with self._master_lock:
            self._waiting_counts[config_path] -= 1
            self._active_configs.setdefault(config_path, {})[job_id] = job_path

        self.log.debug("Job %d acquired slot for config: %s" % (job_id, os.path.basename(config_path)))
        return True

    def release(self, config_path, job_id):
        """Release a slot for a config."""
        sem = self._get_sem(config_path)

        with self._master_lock:
            self._active_configs.get(config_path, {}).pop(job_id, None)

        sem.release()
        self.log.debug("Job %d released slot for config: %s" % (job_id, os.path.basename(config_path)))

    def get_status(self):
        """Get current lock status for all configs."""
        with self._master_lock:
            active = {}
            for config, jobs in self._active_configs.items():
                if jobs:
                    active[config] = [{"job_id": jid, "path": p} for jid, p in jobs.items()]
            return {"active": active, "waiting": {k: v for k, v in self._waiting_counts.items() if v > 0}}

    def is_locked(self, config_path):
        """Check if a config has any active jobs."""
        with self._master_lock:
            return bool(self._active_configs.get(config_path))

    def get_locked_configs(self):
        """Return config paths where all concurrency slots are full."""
        with self._master_lock:
            return {c for c, jobs in self._active_configs.items() if len(jobs) >= self.max_per_config}

    def get_active_jobs(self, config_path):
        """Get active jobs for a config as a list of dicts."""
        with self._master_lock:
            return [{"job_id": jid, "path": p} for jid, p in self._active_configs.get(config_path, {}).items()]


class ConfigLogManager:
    """Manages separate log files for each configuration."""

    def __init__(self, logs_dir=LOGS_DIR):
        self.logs_dir = logs_dir
        self.loggers = {}
        self.lock = threading.Lock()

        # Ensure logs directory exists
        if not os.path.isdir(self.logs_dir):
            os.makedirs(self.logs_dir)

    def _config_to_logname(self, config_path):
        """Convert config path to log filename."""
        basename = os.path.basename(config_path)
        name, _ = os.path.splitext(basename)
        return name

    def get_logger(self, config_path):
        """Get or create a logger for a specific config file."""
        with self.lock:
            if config_path in self.loggers:
                return self.loggers[config_path]

            log_name = self._config_to_logname(config_path)
            log_file = os.path.join(self.logs_dir, f"{log_name}.log")

            # Use DAEMON.{log_name} so Python's logger hierarchy propagates records
            # up into the DAEMON logger (and its daemon.log handler) automatically.
            logger = logging.getLogger(f"DAEMON.{log_name}")
            logger.setLevel(logging.DEBUG)

            existing_paths = {getattr(handler, "baseFilename", None) for handler in logger.handlers}
            if log_file not in existing_paths:
                file_handler = RotatingFileHandler(log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8")
                file_handler.setLevel(logging.DEBUG)
                file_handler.addFilter(JobContextFilter())
                if JSONFormatter is not None:
                    file_handler.setFormatter(JSONFormatter())
                else:
                    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
                    file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
            # propagate=True (Python default) — records also flow to the DAEMON handler

            self.loggers[config_path] = logger
            return logger

    def get_log_file(self, config_path):
        """Get the log file path for a config."""
        log_name = self._config_to_logname(config_path)
        return os.path.join(self.logs_dir, f"{log_name}.log")

    def get_all_log_files(self):
        """Return list of {name, path} for every top-level .log file present on disk."""
        with self.lock:
            try:
                entries = sorted(os.scandir(self.logs_dir), key=lambda e: e.name)
            except OSError:
                return []

            result = []
            for entry in entries:
                if not entry.is_file():
                    continue
                if not entry.name.endswith(".log"):
                    continue
                result.append({"name": os.path.splitext(entry.name)[0], "path": entry.path})
            return result


class PathConfigManager:
    """Manages path-to-config mappings for different media directories."""

    def __init__(self, config_file=None, logger=None):
        self.log = logger or log
        self.path_configs = []
        self.path_rewrites = []  # Can be set from daemon.json
        self.default_config = DEFAULT_PROCESS_CONFIG
        self.default_args = []  # Top-level default args for the default config
        self.api_key = None  # Can be set from daemon.json
        self.basic_auth = None  # (username, password) tuple; can be set from daemon.json
        self.db_url = None  # Can be set from daemon.json
        self.ffmpeg_dir = None  # Can be set from daemon.json
        self.job_timeout_seconds = 0  # Can be set from daemon.json (0 = no timeout)
        self.progress_log_interval = 60  # seconds between progress log entries
        self.smoke_test = False  # Run startup smoke test against all configs
        self.recycle_bin_max_age_days = 3  # Delete recycle-bin files older than N days (0 = disabled)
        self.recycle_bin_min_free_gb = 50  # Delete oldest files when free space < N GiB (0 = disabled)
        self.media_extensions = frozenset([".mp4", ".mkv", ".avi", ".mov", ".ts"])
        self.scan_paths = []  # Can be set from daemon.json
        self._config_file = None  # Resolved path of loaded config file

        # Look up DEFAULT_DAEMON_CONFIG at call time from the daemon module so that
        # tests patching daemon.DEFAULT_DAEMON_CONFIG take effect.
        try:
            import daemon as _daemon_mod

            _default_daemon_cfg = _daemon_mod.DEFAULT_DAEMON_CONFIG
        except (ImportError, AttributeError):
            _default_daemon_cfg = DEFAULT_DAEMON_CONFIG

        if config_file and os.path.exists(config_file):
            self._config_file = config_file
            self.load_config(config_file)
        elif os.path.exists(_default_daemon_cfg):
            self._config_file = _default_daemon_cfg
            self.load_config(self._config_file)
        else:
            self.log.info("No daemon config found, using default autoProcess.ini for all paths")

    def load_config(self, config_file):
        """Load path mappings from daemon.json config file."""
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
            parsed = self._parse_config_data(config)
            self._apply_config_data(parsed)

            self.log.debug("Loaded daemon config from %s" % config_file)
            self.log.debug("Default config: %s" % self.default_config)
            self.log.debug("Path mappings (%d):" % len(self.path_configs))
            for entry in self.path_configs:
                self.log.debug("  %s -> %s" % (entry["path"], entry["config"]))
            return parsed

        except Exception as e:
            self.log.exception("Error loading daemon config: %s" % e)
            raise

    @staticmethod
    def _parse_args_list(raw_args):
        if isinstance(raw_args, str):
            return shlex.split(raw_args)
        return list(raw_args or [])

    def _parse_config_data(self, config):
        default_config = config.get("default_config", DEFAULT_PROCESS_CONFIG)
        if not os.path.isabs(default_config):
            default_config = os.path.join(SCRIPT_DIR, default_config)

        username = config.get("username") or None
        password = config.get("password") or None

        media_extensions = self.media_extensions
        raw_exts = config.get("media_extensions")
        if raw_exts is not None:
            media_extensions = frozenset(("." + e.lower().lstrip(".")) for e in raw_exts if e)

        path_rewrites = [{"from": r["from"].rstrip("/"), "to": r["to"].rstrip("/")} for r in config.get("path_rewrites", []) if r.get("from") and r.get("to")]

        path_configs = []
        for entry in config.get("path_configs", []):
            path = entry.get("path", "").rstrip("/")
            config_path = entry.get("config", "")
            if not path or not config_path:
                continue
            if not os.path.isabs(config_path):
                config_path = os.path.join(SCRIPT_DIR, config_path)
            path_configs.append(
                {
                    "path": os.path.normpath(path),
                    "config": config_path,
                    "default_args": self._parse_args_list(entry.get("default_args", [])),
                }
            )
        path_configs.sort(key=lambda x: len(x["path"]), reverse=True)

        return {
            "default_config": default_config,
            "api_key": config.get("api_key"),
            "basic_auth": (username, password) if username and password else None,
            "db_url": config.get("db_url"),
            "ffmpeg_dir": config.get("ffmpeg_dir"),
            "job_timeout_seconds": int(config.get("job_timeout_seconds", 0) or 0),
            "progress_log_interval": int(config.get("progress_log_interval", 60) or 60),
            "smoke_test": bool(config.get("smoke_test", False)),
            "recycle_bin_max_age_days": int(config.get("recycle_bin_max_age_days", 3) or 3),
            "recycle_bin_min_free_gb": float(config.get("recycle_bin_min_free_gb", 50) or 50),
            "media_extensions": media_extensions,
            "default_args": self._parse_args_list(config.get("default_args", [])),
            "path_rewrites": path_rewrites,
            "scan_paths": list(config.get("scan_paths", [])),
            "path_configs": path_configs,
        }

    def _apply_config_data(self, parsed):
        self.default_config = parsed["default_config"]
        self.api_key = parsed["api_key"]
        self.basic_auth = parsed["basic_auth"]
        self.db_url = parsed["db_url"]
        self.ffmpeg_dir = parsed["ffmpeg_dir"]
        self.job_timeout_seconds = parsed["job_timeout_seconds"]
        self.progress_log_interval = parsed["progress_log_interval"]
        self.smoke_test = parsed["smoke_test"]
        self.recycle_bin_max_age_days = parsed["recycle_bin_max_age_days"]
        self.recycle_bin_min_free_gb = parsed["recycle_bin_min_free_gb"]
        self.media_extensions = parsed["media_extensions"]
        self.default_args = parsed["default_args"]
        self.path_rewrites = parsed["path_rewrites"]
        self.scan_paths = parsed["scan_paths"]
        self.path_configs = parsed["path_configs"]
        if self.path_rewrites:
            self.log.debug("Path rewrites (%d):" % len(self.path_rewrites))
            for rewrite in self.path_rewrites:
                self.log.debug("  %s -> %s" % (rewrite["from"], rewrite["to"]))

    def get_config_for_path(self, file_path):
        """Get the appropriate config file for a given file path."""
        file_path = self.rewrite_path(os.path.abspath(file_path))

        for entry in self.path_configs:
            if file_path.startswith(entry["path"] + "/") or file_path == entry["path"]:
                config_path = entry["config"]
                if os.path.exists(config_path):
                    self.log.debug("Path %s matched %s -> %s" % (file_path, entry["path"], config_path))
                    return config_path
                else:
                    self.log.warning("Config file not found: %s, using default" % config_path)

        self.log.debug("Path %s using default config: %s" % (file_path, self.default_config))
        return self.default_config

    def get_args_for_path(self, file_path):
        """Get the default args list for a given file path based on path_configs."""
        file_path = self.rewrite_path(os.path.abspath(file_path))

        for entry in self.path_configs:
            if file_path.startswith(entry["path"] + "/") or file_path == entry["path"]:
                return list(entry.get("default_args", []))

        return list(self.default_args)

    def rewrite_path(self, path):
        """Apply the first matching path_rewrites prefix substitution, or return path unchanged."""
        for r in self.path_rewrites:
            prefix = r["from"]
            if path == prefix or path.startswith(prefix + "/"):
                return r["to"] + path[len(prefix) :]
        return path

    def get_all_configs(self):
        """Return list of all unique config files."""
        configs = {self.default_config}
        for entry in self.path_configs:
            configs.add(entry["config"])
        return list(configs)

    def get_recycle_bin(self, config_path):
        """Return the recycle-bin path from an autoProcess.ini, or None."""
        try:
            cp = configparser.ConfigParser()
            cp.read(config_path)
            val = cp.get("Converter", "recycle-bin", fallback="").strip()
            return os.path.abspath(val) if val else None
        except Exception:
            return None

    def is_recycle_bin_path(self, path):
        """Return True if path is inside any configured recycle-bin directory."""
        path = os.path.normpath(os.path.abspath(path))
        for config_path in self.get_all_configs():
            recycle_bin = self.get_recycle_bin(config_path)
            if recycle_bin and (path == recycle_bin or path.startswith(recycle_bin + os.sep)):
                return True
        return False
