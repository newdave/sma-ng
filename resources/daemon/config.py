import copy
import logging
import os
import shlex
import socket
import threading
import uuid
from logging.handlers import RotatingFileHandler

from resources.config_loader import ConfigError, ConfigLoader
from resources.config_schema import SmaConfig
from resources.daemon.constants import DEFAULT_PROCESS_CONFIG, LOGS_DIR, SECRET_KEYS, SERVICE_SECRET_FIELDS
from resources.daemon.context import JobContextFilter
from resources.log import LOG_BACKUP_COUNT, LOG_MAX_BYTES, JSONFormatter, getLogger

log = getLogger("DAEMON")


def _write_node_id_to_yaml(config_file: str, node_id: str) -> None:
  """Persist node_id into the daemon section of sma-ng.yml using round-trip YAML to preserve comments.

  Failures are logged but not raised — the daemon will still run with the
  in-memory UUID. Surfacing the warning matters: if the config directory
  is read-only, every restart generates a new UUID, which spams
  ``cluster_nodes`` with new pending rows.
  """
  from ruamel.yaml import YAML

  from resources.yamlconfig import _load_with_dedup

  yaml = YAML(typ="rt")
  yaml.width = 120
  try:
    data = _load_with_dedup(config_file)
    if data is None:
      data = {}
    if "daemon" not in data:
      data["daemon"] = {}
    data["daemon"]["node_id"] = node_id
    tmp = config_file + ".tmp"
    with open(tmp, "w") as f:
      yaml.dump(data, f)
    os.replace(tmp, config_file)
  except Exception:
    log.warning(
      "Could not persist generated node_id to %s — daemon will keep its in-memory UUID this run, "
      "but a future restart will generate a new one and register a new pending node. "
      "Set daemon.node_id in sma-ng.yml to use a stable identity instead.",
      config_file,
      exc_info=True,
    )


def _strip_secrets(data: dict) -> dict:
  """Return a deep copy of data with all secret fields redacted.

  Redacts:
  - SECRET_KEYS from the top-level ``daemon:`` section (api_key, db_url,
    username, password, node_id)
  - SERVICE_SECRET_FIELDS from every ``services.<type>.<instance>`` map
    (apikey, token, password)
  """
  result = copy.deepcopy(data)

  daemon = result.get("daemon", {})
  if isinstance(daemon, dict):
    for key in list(daemon):
      if key in SECRET_KEYS:
        del daemon[key]

  services = result.get("services", {})
  if isinstance(services, dict):
    for instances in services.values():
      if not isinstance(instances, dict):
        continue
      for instance in instances.values():
        if not isinstance(instance, dict):
          continue
        for field in list(instance):
          if field in SERVICE_SECRET_FIELDS:
            del instance[field]
  return result


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
          from resources.log import SingleLineFormatter

          formatter = SingleLineFormatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
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
  """Loads sma-ng.yml and resolves daemon settings, path rewrites, and routing.

  Backed by ``resources.config_loader.ConfigLoader``: this class is a thin
  state holder that exposes the legacy attribute and method surface
  (``api_key``, ``db_url``, ``scan_paths``, ``rewrite_path``,
  ``get_config_for_path``, ``get_profile_for_path``, ``get_args_for_path``,
  ``get_recycle_bin``, ``is_recycle_bin_path``) that other daemon modules
  grep against.

  Under the four-bucket schema there is exactly one config file per
  daemon (the loaded ``sma-ng.yml``), so ``get_config_for_path`` always
  returns ``default_config``. ``get_profile_for_path`` walks
  ``daemon.routing`` via the loader and returns the matched profile name
  (or ``None`` for bare-base fallback). ``get_args_for_path`` returns
  ``daemon.default_args`` unconditionally — per-routing-rule
  ``default_args`` was dropped in the four-bucket cutover.
  """

  def __init__(self, config_file=None, logger=None):
    self.log = logger or log
    self.path_rewrites = []
    self.default_config = DEFAULT_PROCESS_CONFIG
    self.default_args = []
    self.api_key = None
    self.basic_auth = None
    self.db_url = None
    self.ffmpeg_dir = None
    self.workers = 1
    self.strict_routing = False
    self.job_timeout_seconds = 0
    self.progress_log_interval = 60
    self.smoke_test = False
    self.recycle_bin_max_age_days = 3
    self.recycle_bin_min_free_gb = 50.0
    self.media_extensions = frozenset([".mp4", ".mkv", ".avi", ".mov", ".ts"])
    self.scan_paths = []
    from resources.config_schema import AuditSettings, ConfigWatchSettings

    self.config_watch = ConfigWatchSettings()
    self.audit_settings = AuditSettings()
    self._config_file = None
    self._node_id = None
    self._log_ttl_days = 30
    self._node_expiry_days: int = 0
    self._log_archive_dir: str | None = None
    self._log_archive_after_days: int = 0
    self._log_delete_after_days: int = 0
    self._storage_janitor_interval_seconds: int = 900
    self._storage_janitor_max_age_seconds: int = 21600
    self._storage_clear_on_start: bool = True

    self._loader = ConfigLoader(logger=self.log)
    self._cfg: SmaConfig | None = None  # validated config tree, populated by load_config

    if not config_file:
      config_file = DEFAULT_PROCESS_CONFIG
    self._config_file = os.path.realpath(config_file) if os.path.exists(config_file) else None
    self.default_config = config_file
    if self._config_file:
      self.load_config(self._config_file)
    else:
      self.log.info("No config found, using defaults for all paths")

  def load_config(self, config_file, job_db=None):
    """Load and validate sma-ng.yml; merge cluster config from DB if distributed."""
    try:
      cfg = self._loader.load(config_file)
    except ConfigError as exc:
      self.log.error(str(exc))
      raise
    except Exception as e:
      self.log.exception("Error loading daemon config: %s" % e)
      raise

    self._cfg = cfg
    # Invalidate per-profile converter cache used by should_skip_same_extension.
    self._converter_cache = {}
    self._apply_smaconfig(cfg, config_file)

    # Merge DB-shared cluster config if distributed. DB provides shared
    # defaults; only keys explicitly set in the local YAML override DB
    # values. We use raw dict merging on the daemon section so we can
    # detect "explicitly set" via key presence in the local YAML.
    if job_db is not None and getattr(job_db, "is_distributed", False):
      try:
        db_raw = job_db.get_cluster_config() or {}
        db_daemon = (db_raw or {}).get("daemon", {})
        local_daemon = self._raw_local_daemon_section(config_file)
        if db_daemon and local_daemon is not None:
          merged_daemon = {**db_daemon, **local_daemon}
          merged_full = cfg.model_dump(by_alias=True)
          merged_full["daemon"] = merged_daemon
          merged_cfg = SmaConfig.model_validate(merged_full)
          self._cfg = merged_cfg
          self._apply_smaconfig(merged_cfg, config_file)
      except Exception:
        self.log.warning("Failed to fetch cluster config from DB; using local only")

    self.log.debug("Loaded config from %s" % config_file)
    self.log.debug("Default config: %s" % self.default_config)
    self.log.debug("Routing rules (%d):" % len(self._cfg.daemon.routing if self._cfg else []))
    if self._cfg:
      for rule in self._cfg.daemon.routing:
        self.log.debug("  %s -> profile=%s services=%s" % (rule.match, rule.profile, rule.services))
    self._ensure_node_id(config_file)
    return self._cfg

  @staticmethod
  def _raw_local_daemon_section(config_file: str) -> dict | None:
    """Read just the ``daemon:`` section from disk (raw, pre-validation).

    Used to detect which keys the local user explicitly set, so the DB
    cluster-config merge knows which keys to let win locally.
    """
    try:
      from resources.yamlconfig import load as _yaml_load

      data = _yaml_load(config_file) or {}
      daemon_section = data.get("daemon")
      return daemon_section if isinstance(daemon_section, dict) else {}
    except Exception:
      return None

  def _apply_smaconfig(self, cfg: SmaConfig, config_file: str) -> None:
    """Project the validated SmaConfig.daemon section onto manager attributes."""
    d = cfg.daemon
    self.default_config = config_file
    self.api_key = d.api_key
    self.basic_auth = (d.username, d.password) if d.username and d.password else None
    self.db_url = d.db_url
    self.ffmpeg_dir = d.ffmpeg_dir
    self.workers = d.workers
    self.strict_routing = bool(d.strict_routing)
    self.job_timeout_seconds = d.job_timeout_seconds
    self.progress_log_interval = d.progress_log_interval
    self.smoke_test = d.smoke_test
    self.recycle_bin_max_age_days = d.recycle_bin_max_age_days
    self.recycle_bin_min_free_gb = float(d.recycle_bin_min_free_gb)
    self.media_extensions = frozenset(("." + e.lower().lstrip(".")) for e in (d.media_extensions or []) if e)
    self.default_args = self._parse_args_list(d.default_args)
    self.path_rewrites = sorted(
      (
        {
          "from": os.path.normpath(r.from_.rstrip("/")),
          "to": os.path.normpath(r.to.rstrip("/")),
        }
        for r in d.path_rewrites
        if r.from_ and r.to
      ),
      key=lambda x: len(x["from"]),
      reverse=True,
    )
    self.scan_paths = [{"path": s.path, "interval": s.interval, "enabled": s.enabled, "rewrite_from": s.rewrite_from, "rewrite_to": s.rewrite_to} for s in d.scan_paths]
    self.config_watch = d.config_watch
    self.audit_settings = d.audit
    self._node_id = d.node_id
    self._log_ttl_days = d.log_ttl_days
    self._node_expiry_days = d.node_expiry_days
    self._log_archive_dir = d.log_archive_dir or None
    self._log_archive_after_days = d.log_archive_after_days
    self._log_delete_after_days = d.log_delete_after_days
    # Treat null/None as "disabled" — surface as 0 to the consumers so the
    # janitor thread can use a single `> 0` check.
    self._storage_janitor_interval_seconds = int(d.storage_janitor_interval_seconds or 0)
    self._storage_janitor_max_age_seconds = int(d.storage_janitor_max_age_seconds or 0)
    self._storage_clear_on_start = bool(d.storage_clear_on_start)
    if self.path_rewrites:
      self.log.debug("Path rewrites (%d):" % len(self.path_rewrites))
      for rewrite in self.path_rewrites:
        self.log.debug("  %s -> %s" % (rewrite["from"], rewrite["to"]))

  def _ensure_node_id(self, config_file):
    """Resolve and cache the cluster node identity.

    Priority order:

    1. ``daemon.node_id`` already persisted in ``sma-ng.yml`` — preserves
       any UUID an earlier daemon generated so existing approved rows
       in ``cluster_nodes`` stay attached to this node.
    2. Generate a fresh UUID and try to persist it to ``sma-ng.yml``.
    """
    from resources.daemon.constants import set_node_id_cache

    if self._node_id:
      node_id = self._node_id
    else:
      node_id = str(uuid.uuid4())
      if config_file:
        _write_node_id_to_yaml(config_file, node_id)
    # Keep self._node_id in sync with the resolved identity so the
    # ``node_id`` property and resolve_node_id() agree.
    self._node_id = node_id
    set_node_id_cache(node_id)

  @property
  def audit_paths(self) -> list[dict]:
    """Return audit_paths as plain dicts (mirrors scan_paths shape)."""
    return [{"path": a.path, "enabled": a.enabled, "rewrite_from": a.rewrite_from, "rewrite_to": a.rewrite_to} for a in self.audit_settings.paths]

  @property
  def node_id(self) -> str:
    """Return the UUID node identity, falling back to hostname if not yet set."""
    return self._node_id or socket.gethostname()

  @property
  def log_ttl_days(self) -> int:
    """Return the number of days to retain cluster log entries in PostgreSQL."""
    return self._log_ttl_days

  @property
  def node_expiry_days(self) -> int:
    """Return days after which offline nodes are hard-deleted (0 = disabled)."""
    return self._node_expiry_days

  @property
  def log_archive_dir(self) -> str | None:
    """Return the directory for archived log files, or None if disabled."""
    return self._log_archive_dir

  @property
  def log_archive_after_days(self) -> int:
    """Return days after which DB logs are archived to filesystem (0 = disabled)."""
    return self._log_archive_after_days

  @property
  def log_delete_after_days(self) -> int:
    """Return days after which archived log files are deleted (0 = disabled)."""
    return self._log_delete_after_days

  @property
  def storage_janitor_interval_seconds(self) -> int:
    """Return the janitor sweep cadence in seconds (0 = disabled)."""
    return self._storage_janitor_interval_seconds

  @property
  def storage_janitor_max_age_seconds(self) -> int:
    """Return the minimum file-mtime age before the janitor reaps (0 = disabled)."""
    return self._storage_janitor_max_age_seconds

  @property
  def storage_clear_on_start(self) -> bool:
    """Return whether the daemon should wipe ``output_directory`` at startup."""
    return self._storage_clear_on_start

  @property
  def output_directory(self) -> str:
    """Return ``base.converter.output_directory`` (empty string when unset)."""
    if self._cfg is None:
      return ""
    return self._cfg.base.converter.output_directory or ""

  @property
  def temp_extension(self) -> str:
    """Return the configured temp-extension (without leading dot), or empty."""
    if self._cfg is None:
      return ""
    return (self._cfg.base.converter.temp_extension or "").lstrip(".")

  @staticmethod
  def _parse_args_list(raw_args):
    if isinstance(raw_args, str):
      return shlex.split(raw_args)
    return list(raw_args or [])

  def _normalize_match_path(self, path):
    """Return *path* normalized for rewrite-aware config matching."""
    return os.path.normpath(self.rewrite_path(os.path.abspath(path)))

  def get_config_for_path(self, file_path):
    """Return the active config file. Single-config-per-daemon model.

    Under the four-bucket schema there is only one config file (the
    daemon's loaded ``sma-ng.yml``). This method is preserved for
    backward compatibility with callers that still pass a path through.
    """
    return self.default_config

  def get_profile_for_path(self, file_path):
    """Return the profile name for the routing rule that matches *file_path*.

    Walks ``daemon.routing`` longest-prefix; returns the matched rule's
    profile (which may itself be ``None`` for bare-base) or ``None`` when
    no rule matches. Emits a WARNING when no rule matches so silent
    bare-base fallback surfaces in the daemon log.
    """
    if self._cfg is None:
      return None
    res = self._loader.resolve_routing(self._cfg, file_path)
    if not res.matched:
      self.log.warning(
        "No routing rule matched %s — using bare base config. Add a daemon.routing entry or a path-rewrite, or set daemon.strict-routing=true to refuse such jobs [routing-miss]." % file_path,
      )
    return res.profile

  def has_routing_match(self, file_path) -> bool:
    """Return True iff a ``daemon.routing`` rule matched *file_path*."""
    if self._cfg is None:
      return False
    return self._loader.resolve_routing(self._cfg, file_path).matched

  def should_skip_same_extension(self, file_path: str) -> bool:
    """Return True if *file_path* would be a no-op conversion under the
    resolved profile — i.e. its extension already matches the configured
    output extension AND the operator hasn't opted into reprocessing via
    ``process-same-extensions: true`` or ``force-convert: true``.

    Matches the runtime check in
    :py:meth:`resources.mediaprocessor.MediaProcessor.process` (around
    line 2650) so directory submissions don't queue files the worker will
    immediately discard.

    Profile-aware: a routing rule pointing at a profile that flips
    ``process-same-extensions`` back on for a given path tree will return
    False here. Per-profile merged converter settings are cached.
    """
    if self._cfg is None:
      return False
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    if not ext:
      return False
    conv = self._converter_for_path(file_path)
    if conv is None:
      return False
    if conv.force_convert or conv.process_same_extensions:
      return False
    target_ext = (conv.output_extension or "mp4").lower().lstrip(".")
    return ext == target_ext

  def _converter_for_path(self, file_path: str):
    """Resolve the merged ``base.converter`` for *file_path*'s profile.

    Caches per-profile-name to avoid re-running the overlay merge for
    every file in a large directory submission.
    """
    if self._cfg is None:
      return None
    profile_name = None
    try:
      res = self._loader.resolve_routing(self._cfg, file_path)
      profile_name = res.profile
    except Exception:
      profile_name = None
    cache = getattr(self, "_converter_cache", None)
    if cache is None:
      cache = {}
      self._converter_cache = cache
    if profile_name in cache:
      return cache[profile_name]
    try:
      merged = self._loader.apply_profile(self._cfg, profile_name)
      cache[profile_name] = merged.converter
    except Exception:
      cache[profile_name] = self._cfg.base.converter
    return cache[profile_name]

  def profile_concurrency_caps(self) -> dict[str, int]:
    """Return ``{profile_name: max_concurrent}`` for every profile with a
    positive cap. Profiles with ``max_concurrent`` None / <=0 are omitted
    so the caller can treat absence as "unlimited".

    Read by ``claim_next_job`` to skip pending jobs whose profile already
    has its cap-many running peers across the cluster.
    """
    if self._cfg is None or self._cfg.profiles is None:
      return {}
    caps: dict[str, int] = {}
    for name, overlay in self._cfg.profiles.items():
      cap = getattr(overlay, "max_concurrent", None)
      if cap and cap > 0:
        caps[name] = int(cap)
    return caps

  def profile_concurrency_costs(self) -> dict[str, int]:
    """Return ``{profile_name: concurrency_cost}`` for every profile.

    Always includes every named profile (default cost = 1) so the caller
    can sum costs of every running job without needing to fall back to
    1 for missing keys. Pairs with :attr:`concurrency_budget` to express
    a weighted-capacity scheduler: ``Σ running.cost ≤ budget``.
    """
    if self._cfg is None or self._cfg.profiles is None:
      return {}
    costs: dict[str, int] = {}
    for name, overlay in self._cfg.profiles.items():
      raw = getattr(overlay, "concurrency_cost", 1)
      costs[name] = int(raw) if raw and raw > 0 else 1
    return costs

  @property
  def concurrency_budget(self) -> int:
    """Return the per-node encoder-capacity budget.

    Resolves ``daemon.concurrency_budget`` when set positive, else
    falls back to ``daemon.workers`` so a zero-config install behaves
    identically to the pre-budget code (every job costs 1 against a
    budget of ``workers``).
    """
    if self._cfg is None:
      return 0
    raw = self._cfg.daemon.concurrency_budget
    if raw and raw > 0:
      return int(raw)
    return int(self._cfg.daemon.workers or 0)

  def get_args_for_path(self, file_path):
    """Return the global default args list.

    Per-routing-rule ``default_args`` was dropped in the four-bucket
    cutover; only the global ``daemon.default_args`` survives. Callers
    unchanged for backward compatibility.
    """
    return list(self.default_args)

  def routing_match_paths(self) -> list[str]:
    """Return cleaned ``match`` prefixes for every ``daemon.routing`` rule.

    Used by the admin dashboard's directory browser to compute the allowed
    root prefixes the user can navigate into. Trailing ``/**`` or ``/*`` is
    stripped; an empty match (degenerate "match-all" rule) is skipped.
    """
    if self._cfg is None:
      return []
    out = []
    for rule in self._cfg.daemon.routing:
      p = rule.match.rstrip("/")
      while p.endswith("*"):
        p = p.rstrip("*").rstrip("/")
      if p:
        out.append(os.path.normpath(p))
    return out

  def routing_rules_admin(self) -> list[dict]:
    """Return ``daemon.routing`` rules as plain dicts for the /configs admin endpoint."""
    if self._cfg is None:
      return []
    return [{"match": r.match, "profile": r.profile, "services": list(r.services)} for r in self._cfg.daemon.routing]

  def get_services_for_path(self, file_path):
    """Return the list of (service_type, instance_name) tuples to notify
    for *file_path*, per ``daemon.routing`` longest-prefix match.

    New API surface in the four-bucket cutover. Empty list on no match
    or when the matching rule omits ``services:``.
    """
    if self._cfg is None:
      return []
    res = self._loader.resolve_routing(self._cfg, file_path)
    return list(res.services)

  def get_service_instance(self, service_type: str, instance_name: str):
    """Return one ``services.<type>.<name>`` instance as a plain dict.

    Used by daemon webhook handlers for service-specific API lookups
    (e.g. Sonarr/Radarr tag label fetches).
    """
    if self._cfg is None:
      return None
    service_map = getattr(self._cfg.services, service_type, None)
    if not isinstance(service_map, dict):
      return None
    instance = service_map.get(instance_name)
    if instance is None:
      return None
    try:
      return instance.model_dump(by_alias=True)
    except Exception:
      return None

  def rewrite_path(self, path):
    """Apply the first matching path_rewrites prefix substitution, or return path unchanged."""
    path = os.path.normpath(path)
    for r in self.path_rewrites:
      prefix = r["from"]
      if path == prefix or path.startswith(prefix + "/"):
        return r["to"] + path[len(prefix) :]
    return path

  def get_all_configs(self):
    """Return a single-element list containing the daemon's config file.

    Multi-config support (per-path config file selection) was removed in
    the four-bucket cutover; this method is kept as a one-entry list for
    backward compatibility with callers like ``daemon.run_smoke_test``.
    """
    return [self.default_config]

  def get_recycle_bin(self, config_path):
    """Return the recycle-bin path from a sma-ng.yml config, or None.

    Reads ``base.converter.recycle-bin`` from the YAML. INI fallback was
    removed in the four-bucket cutover.
    """
    try:
      if not config_path.endswith((".yaml", ".yml")):
        return None
      from resources.yamlconfig import load as _yaml_load

      data = _yaml_load(config_path) or {}
      base = data.get("base") or {}
      converter = base.get("converter") if isinstance(base, dict) else None
      if not isinstance(converter, dict):
        return None
      val = str(converter.get("recycle-bin", "")).strip()
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
