import json
import os
import re as _re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING
from urllib.parse import unquote

import requests

from resources.daemon.config import _strip_secrets
from resources.daemon.constants import SCRIPT_DIR
from resources.daemon.context import clear_job_id, set_job_id
from resources.daemon.db import STATUS_RUNNING, SQLiteJobDatabase
from resources.daemon.docs_ui import DOCS_DIR, _inline, _load_admin_html, _load_dashboard_html, _load_docs_template, _load_metrics_html, _render_markdown_to_html

__all__ = ["WebhookHandler", "_inline"]
from resources.daemon.routes import dispatch_get, dispatch_post, dispatch_post_job_action
from resources.daemon.webhook_parsing import parse_generic_webhook_body, parse_radarr_body, parse_sonarr_body

if TYPE_CHECKING:
  from resources.daemon.server import DaemonServer

_LOCAL_TIMEZONE = datetime.now().astimezone().tzinfo


def _local_now():
  return datetime.now(_LOCAL_TIMEZONE)


def _json_default(value):
  """Serialize datetimes in the daemon host's local timezone."""
  if isinstance(value, datetime):
    if value.tzinfo is None:
      return value.isoformat(timespec="seconds")
    return value.astimezone(_LOCAL_TIMEZONE).isoformat(timespec="seconds")
  raise TypeError("Object of type %s is not JSON serializable" % type(value).__name__)


def _is_explicitly_local_db(job_db):
  return isinstance(job_db, SQLiteJobDatabase)


def _is_not_distributed(job_db):
  return getattr(job_db, "is_distributed", None) is False


class WebhookHandler(BaseHTTPRequestHandler):
  """HTTP request handler for webhook endpoints."""

  if TYPE_CHECKING:
    server: "DaemonServer"

  # Endpoints that don't require authentication (prefix-matched for /docs/*)
  PUBLIC_ENDPOINTS = ["/", "/dashboard", "/admin", "/metrics", "/health", "/status", "/docs", "/favicon.png"]

  def log_message(self, format, *args):
    self.server.logger.debug("%s - %s" % (self.address_string(), format % args))

  def send_json_response(self, status_code, data):
    self.send_response(status_code)
    self.send_header("Content-Type", "application/json")
    self.end_headers()
    self.wfile.write(json.dumps(data, default=_json_default).encode("utf-8"))

  def send_html_response(self, status_code, html):
    self.send_response(status_code)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    # HTML is rendered from disk on every request; tell browsers not to cache
    # so dashboard/admin/docs UI updates after a deploy show on next load.
    self.send_header("Cache-Control", "no-store, must-revalidate")
    self.end_headers()
    self.wfile.write(html.encode("utf-8"))

  def wants_html(self):
    """Check if client prefers HTML (browser) over JSON (API)."""
    accept = self.headers.get("Accept", "")
    return "text/html" in accept and "application/json" not in accept

  def check_auth(self):
    """
    Check if request is authenticated.
    Returns True if authenticated or no API key is configured.
    Returns False and sends 401 response if authentication fails.
    """
    api_key = self.server.api_key
    basic_auth = self.server.basic_auth  # (username, password) tuple or None

    if not api_key and not basic_auth:
      # No authentication configured, allow all requests
      return True

    # Check X-API-Key header
    request_key = self.headers.get("X-API-Key")

    # Also check Authorization header (Bearer token or Basic auth)
    if not request_key:
      auth_header = self.headers.get("Authorization", "")
      if auth_header.startswith("Bearer "):
        request_key = auth_header[7:]
      elif auth_header.startswith("Basic ") and basic_auth:
        import base64

        try:
          decoded = base64.b64decode(auth_header[6:]).decode("utf-8", errors="replace")
          colon = decoded.index(":")
          req_user = decoded[:colon]
          req_pass = decoded[colon + 1 :]
          if req_user == basic_auth[0] and req_pass == basic_auth[1]:
            return True
        except Exception:
          pass

    if api_key and request_key == api_key:
      return True

    # Authentication failed
    self.server.logger.warning("Unauthorized request from %s" % self.address_string())
    self.send_response(401)
    self.send_header("Content-Type", "application/json")
    self.send_header("WWW-Authenticate", "Bearer")
    self.end_headers()
    self.wfile.write(json.dumps({"error": "Unauthorized", "message": "Valid API key required"}).encode("utf-8"))
    return False

  def is_public_endpoint(self, path):
    """Check if the endpoint is public (doesn't require auth)."""
    return path in self.PUBLIC_ENDPOINTS or path.startswith("/docs/")

  def _read_json_paths(self):
    """Read a JSON body of the form {"paths": [...]} and return the list.

    Returns the paths list on success.  On parse failure, sends a 400
    response and returns None — callers must check for None and return.
    """
    content_length = int(self.headers.get("Content-Length", 0))
    body = self.rfile.read(content_length) if content_length else b"{}"
    try:
      data = json.loads(body)
      paths = data.get("paths", [])
      if not isinstance(paths, list):
        raise ValueError("paths must be a list")
      return paths
    except (json.JSONDecodeError, ValueError) as e:
      self.send_json_response(400, {"error": str(e)})
      return None

  # ------------------------------------------------------------------
  # GET route handlers
  # ------------------------------------------------------------------

  def _get_health(self):
    lock_status = self.server.config_lock_manager.get_status()
    stats = self.server.job_db.get_stats()
    now = _local_now()
    uptime = int((now - self.server.started_at).total_seconds())
    hw_caps = getattr(self.server, "hw_capabilities", None)
    if not isinstance(hw_caps, dict):
      hw_caps = {}
    fallback_summary_fn = getattr(self.server, "fallback_summary", None)
    fallback = fallback_summary_fn() if callable(fallback_summary_fn) else []
    if not isinstance(fallback, list):
      fallback = []
    payload = {
      "status": "ok",
      "node": self.server.node_id,
      "started_at": self.server.started_at,
      "uptime_seconds": uptime,
      "workers": self.server.worker_count,
      "jobs": stats,
      "active": lock_status["active"],
      "waiting": lock_status["waiting"],
      # New top-level fields (additive — consumers MUST ignore unknown keys
      # per docs/daemon.md). Operators query these for GPU health and
      # per-tier fallback counters without grepping logs.
      "gpu_status": hw_caps.get("gpu_status", "unknown"),
      "selected_backend": hw_caps.get("selected_backend", "software"),
      "capabilities": hw_caps.get("capabilities", {}) if isinstance(hw_caps.get("capabilities"), dict) else {},
      "fallback": fallback,
    }
    self.send_json_response(200, payload)

  def _get_status(self):
    nodes = self.server.job_db.get_cluster_nodes()
    display_host = None
    try:
      display_host = self.connection.getsockname()[0]
    except Exception:
      display_host = None

    if display_host:
      normalized_nodes = []
      for node in nodes:
        normalized = dict(node)
        if normalized.get("host") in {"0.0.0.0", "::", ""}:
          normalized["host"] = display_host
        normalized_nodes.append(normalized)
      nodes = normalized_nodes

    stats = self.server.job_db.get_stats()
    self.send_json_response(200, {"cluster": nodes, "jobs": stats})

  def _get_jobs(self, query):
    status = query.get("status", [None])[0]
    config = query.get("config", [None])[0]
    search = query.get("search", [None])[0]
    limit = int(query.get("limit", [100])[0])
    offset = int(query.get("offset", [0])[0])
    if search:
      offset = 0
      limit = max(limit, 200)
    jobs = self.server.job_db.get_jobs(status=status, config=config, path=search, limit=limit, offset=offset)
    for job in jobs:
      if job.get("config"):
        job["log_name"] = os.path.splitext(os.path.basename(job["config"]))[0]
    self.send_json_response(200, {"jobs": jobs, "count": len(jobs), "limit": limit, "offset": offset})

  def _get_job(self, path):
    try:
      job_id = int(path.split("/")[-1])
      job = self.server.job_db.get_job(job_id)
      if job:
        if job.get("status") == STATUS_RUNNING:
          job["progress"] = self.server._job_progress.get(job_id)
        self.send_json_response(200, job)
      else:
        self.send_json_response(404, {"error": "Job not found"})
    except ValueError:
      self.send_json_response(400, {"error": "Invalid job ID"})

  def _get_job_ffmpeg_stderr(self, path):
    """Return the stored ffmpeg stderr blob for a job as plain text.

    Path: /jobs/<id>/ffmpeg-stderr. 404 if the job doesn't exist or has
    no stored stderr; the daemon worker ingests sidecars on failure, so
    a successful or in-progress job will normally return 404.
    """
    parts = path.strip("/").split("/")
    # Expect ["jobs", "<id>", "ffmpeg-stderr"]
    if len(parts) != 3 or parts[0] != "jobs" or parts[2] != "ffmpeg-stderr":
      self.send_json_response(404, {"error": "Not found"})
      return
    try:
      job_id = int(parts[1])
    except ValueError:
      self.send_json_response(400, {"error": "Invalid job ID"})
      return
    job = self.server.job_db.get_job(job_id)
    if not job:
      self.send_json_response(404, {"error": "Job not found"})
      return
    stderr = job.get("ffmpeg_stderr")
    if not stderr:
      self.send_json_response(404, {"error": "No ffmpeg stderr stored for this job", "job_id": job_id})
      return
    body = stderr.encode("utf-8")
    self.send_response(200)
    self.send_header("Content-Type", "text/plain; charset=utf-8")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def _get_browse(self, query):
    """List directories and media files under a path, constrained to configured roots."""
    path = query.get("path", [""])[0].strip()
    pcm = self.server.path_config_manager

    # Sources of valid root prefixes: routing-rule match paths and scan-path
    # entries. Add every ancestor directory of each so navigation from "/"
    # down through the tree works.
    base_roots = list(pcm.routing_match_paths())
    base_roots.extend(sp["path"] for sp in pcm.scan_paths if sp.get("path"))

    allowed_roots = set()
    for raw in base_roots:
      p = os.path.normpath(raw)
      allowed_roots.add(p)
      parts = p.rstrip("/").split("/")
      for i in range(1, len(parts)):
        allowed_roots.add("/".join(parts[:i]) or "/")

    def is_allowed(check_path):
      check_path = os.path.normpath(check_path)
      for root in allowed_roots:
        root_norm = os.path.normpath(root)
        if check_path == root_norm or check_path.startswith(root_norm + os.sep):
          return True
      return False

    if not path:
      # Return the top-level configured path prefixes as starting points
      dirs = sorted({os.path.normpath(r) for r in base_roots if os.path.isdir(r)})
      return self.send_json_response(200, {"dirs": dirs, "files": []})

    path = os.path.normpath(path)

    if not is_allowed(path):
      return self.send_json_response(403, {"error": "Path is outside configured media roots"})

    if not os.path.isdir(path):
      return self.send_json_response(404, {"error": "Directory not found"})

    try:
      dirs, files = [], []
      with os.scandir(path) as it:
        for entry in sorted(it, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())):
          if entry.name.startswith("."):
            continue
          if entry.is_dir(follow_symlinks=False):
            dirs.append(os.path.join(path, entry.name))
          elif entry.is_file(follow_symlinks=False):
            ext = os.path.splitext(entry.name)[1].lower()
            if ext in pcm.media_extensions:
              files.append(os.path.join(path, entry.name))
      self.send_json_response(200, {"dirs": dirs, "files": files})
    except PermissionError:
      self.send_json_response(403, {"error": "Permission denied"})

  def _get_configs(self):
    """Admin endpoint surfacing daemon-side routing + per-config status.

    Under the four-bucket schema there is exactly one config file per
    daemon. The dashboard receives the loaded config's status (active /
    pending jobs, log file) once, plus the full ``daemon.routing`` rule
    list with each rule's match / profile / services fields.
    """
    pcm = self.server.path_config_manager
    default_config = pcm.default_config
    self.send_json_response(
      200,
      {
        "default_config": default_config,
        "default_args": pcm.default_args,
        "default_log": self.server.config_log_manager.get_log_file(default_config),
        "default_log_name": os.path.splitext(os.path.basename(default_config))[0],
        "default_active_jobs": self.server.config_lock_manager.get_active_jobs(default_config),
        "default_pending_jobs": self.server.job_db.pending_count_for_config(default_config),
        "routing": pcm.routing_rules_admin(),
        "logs_directory": self.server.config_log_manager.logs_dir,
      },
    )

  def _get_logs(self):
    """List all known log files with metadata."""
    log_files = self.server.config_log_manager.get_all_log_files()
    result = []
    for entry in log_files:
      info = {"name": entry["name"], "file": entry["path"]}
      try:
        st = os.stat(entry["path"])
        info["size"] = st.st_size
        info["mtime"] = datetime.fromtimestamp(st.st_mtime, tz=_LOCAL_TIMEZONE).isoformat(timespec="seconds")
      except OSError:
        info["size"] = 0
        info["mtime"] = None
      result.append(info)
    self.send_json_response(200, result)

  def _get_log_content(self, path, query):
    """Fetch log content for a specific log file.

    URL patterns:
      GET /logs/<logname>           — last N lines, with optional filters
      GET /logs/<logname>/tail      — lines after byte offset (for polling)

    Query params:
      job_id=<int>    filter to this job only
      level=INFO|...  minimum level filter (ERROR > WARNING > INFO > DEBUG)
      lines=<n>       last N lines (default 500, max 2000; ignored if offset given)
      offset=<bytes>  return content starting at this byte offset
    """
    _LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}

    # Parse the logname from path, handling /logs/<name> and /logs/<name>/tail
    # path starts with "/logs/"
    remainder = path[len("/logs/") :]
    is_tail = remainder.endswith("/tail")
    if is_tail:
      remainder = remainder[: -len("/tail")]
    logname = remainder.strip("/")

    # Validate against whitelist — prevent path traversal
    known = {e["name"]: e["path"] for e in self.server.config_log_manager.get_all_log_files()}
    if not logname or logname not in known:
      self.send_json_response(404, {"error": "Log not found"})
      return

    log_path = known[logname]

    # Parse query params
    job_id_filter = query.get("job_id", [None])[0]
    level_filter = (query.get("level", [None])[0] or "").upper() or None
    lines_param = int(query.get("lines", [500])[0])
    lines_param = min(lines_param, 2000)
    offset_param = query.get("offset", [None])[0]
    if offset_param is not None:
      offset_param = int(offset_param)

    # Get current file size for tail polling
    try:
      file_size = os.path.getsize(log_path)
    except OSError:
      self.send_json_response(200, {"entries": [], "file_size": 0})
      return

    if is_tail:
      if offset_param is None:
        self.send_json_response(400, {"error": "offset param required for /tail"})
        return
      # Handle log rotation: if offset > file_size, reset to 0
      if offset_param > file_size:
        offset_param = 0
      raw_lines = self._read_from_offset(log_path, offset_param)
    elif offset_param is not None:
      raw_lines = self._read_from_offset(log_path, offset_param)
    else:
      raw_lines = self._tail_lines(log_path, lines_param)

    entries = []
    for line in raw_lines:
      line = line.strip()
      if not line:
        continue
      try:
        entry = json.loads(line)
      except (ValueError, KeyError):
        continue
      # Apply job_id filter
      if job_id_filter is not None and str(entry.get("job_id", "")) != str(job_id_filter):
        continue
      # Apply level filter
      if level_filter:
        entry_level = (entry.get("level") or "").upper()
        if _LEVEL_ORDER.get(entry_level, 0) < _LEVEL_ORDER.get(level_filter, 0):
          continue
      entries.append(entry)

    self.send_json_response(200, {"entries": entries, "file_size": file_size})

  @staticmethod
  def _tail_lines(filepath, n):
    """Read last n lines from a file without loading the whole file."""
    try:
      with open(filepath, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        if size == 0:
          return []
        block = min(size, max(n * 250, 65536))
        f.seek(-block, 2)
        data = f.read()
    except OSError:
      return []
    return data.decode("utf-8", errors="replace").splitlines()[-n:]

  @staticmethod
  def _read_from_offset(filepath, offset):
    """Read lines from a file starting at byte offset."""
    try:
      with open(filepath, "rb") as f:
        f.seek(offset)
        data = f.read()
    except OSError:
      return []
    return data.decode("utf-8", errors="replace").splitlines()

  def _get_scan(self, query):
    # Filter a list of paths to those not yet recorded as scanned.
    # Usage: GET /scan?path=/a/b.mkv&path=/c/d.mkv
    # For large path lists use POST /scan/filter instead.
    paths = query.get("path", [])
    unscanned = self.server.job_db.filter_unscanned(paths)
    self.send_json_response(200, {"unscanned": unscanned, "total": len(paths), "already_scanned": len(paths) - len(unscanned)})

  def _get_cluster_logs(self, _path, query):
    """Return cluster log entries from the database.

    Query params:
      node_id=<str>   filter to a specific node (optional)
      level=<str>     filter to a specific log level (optional)
      limit=<int>     max entries to return (default 100, max 500)
      offset=<int>    pagination offset (default 0)
    """
    if _is_not_distributed(self.server.job_db):
      self.send_json_response(503, {"error": "Cluster logs are only available in distributed (PostgreSQL) mode"})
      return

    node_id = query.get("node_id", [None])[0] or None
    level = query.get("level", [None])[0] or None
    limit = min(int(query.get("limit", [100])[0]), 500)
    offset = int(query.get("offset", [0])[0])

    logs = self.server.job_db.get_logs(node_id=node_id, level=level, limit=limit, offset=offset)

    serialized = []
    for entry in logs:
      row = dict(entry)
      ts = row.get("timestamp")
      if ts is not None and hasattr(ts, "isoformat"):
        row["timestamp"] = ts.isoformat()
      serialized.append(row)

    self.send_json_response(200, {"logs": serialized, "total": len(serialized)})

  def _get_admin_config(self, _path, _query):
    """Return the cluster-wide base config from the database."""
    if _is_not_distributed(self.server.job_db):
      self.send_json_response(503, {"error": "Cluster config is only available in distributed (PostgreSQL) mode"})
      return
    raw = self.server.job_db.get_cluster_config()
    self.send_json_response(200, {"config": raw or {}})

  def _post_admin_config(self, _path, _query):
    """Store a cluster-wide base config into the database (secrets are stripped)."""
    if _is_not_distributed(self.server.job_db):
      self.send_json_response(503, {"error": "Cluster config is only available in distributed (PostgreSQL) mode"})
      return
    actor = self.headers.get("X-Actor", "admin-ui")
    content_length = int(self.headers.get("Content-Length", 0))
    try:
      body = json.loads(self.rfile.read(content_length) if content_length else b"{}")
    except (json.JSONDecodeError, ValueError):
      self.send_json_response(400, {"error": "Invalid JSON body"})
      return
    config_dict = body.get("config", body)
    if not isinstance(config_dict, dict):
      self.send_json_response(400, {"error": "'config' must be a JSON object"})
      return
    clean = _strip_secrets(config_dict)
    self.server.job_db.set_cluster_config(clean, updated_by=actor)
    self.send_json_response(200, {"status": "saved"})

  def do_HEAD(self):
    """Respond to HEAD requests (used by browsers and health-check tools)."""
    self.send_response(200)
    self.send_header("Content-Type", "application/json")
    self.end_headers()

  def _get_root(self, _path, _query):
    self.send_response(301)
    self.send_header("Location", "/dashboard")
    self.end_headers()

  def _get_dashboard(self, _path, _query):
    api_key = self.server.api_key or ""
    key_script = "<script>window.SMA_API_KEY=%s;</script>" % json.dumps(api_key)
    self.send_html_response(200, _load_dashboard_html().replace("</head>", key_script + "</head>", 1))

  def _get_admin(self, _path, _query):
    api_key = self.server.api_key or ""
    key_script = "<script>window.SMA_API_KEY=%s;</script>" % json.dumps(api_key)
    self.send_html_response(200, _load_admin_html().replace("</head>", key_script + "</head>", 1))

  def _get_docs(self, path, _query):
    # Resolve slug: /docs → index, /docs/daemon → daemon
    # Sanitise to word chars and hyphens only to prevent path traversal
    raw_slug = path[len("/docs") :].lstrip("/") or "index"
    slug = _re.sub(r"[^\w\-]", "", raw_slug) or "index"
    md_file = os.path.join(DOCS_DIR, "README.md" if slug == "index" else slug + ".md")
    if not os.path.abspath(md_file).startswith(os.path.abspath(DOCS_DIR) + os.sep) and slug != "index":
      self.send_json_response(404, {"error": "Not found"})
      return
    try:
      with open(md_file, "r", encoding="utf-8") as f:
        md_content = f.read()
      self.send_html_response(200, _load_docs_template(slug) % _render_markdown_to_html(md_content))
    except FileNotFoundError:
      self.send_html_response(404, "<h1>Page not found</h1><p>%s</p>" % md_file)

  def _get_stats(self, _path, _query):
    self.send_json_response(200, self.server.job_db.get_stats())

  def _get_metrics_api(self, _path, query):
    if _is_not_distributed(self.server.job_db):
      self.send_json_response(
        503,
        {
          "available": False,
          "reason": "Cluster metrics are only available in distributed (PostgreSQL) mode. Set daemon.db_url in sma-ng.yml to enable.",
          "docs_url": "/docs/daemon",
        },
      )
      return
    window = query.get("window", ["24h"])[0]
    if window not in {"24h", "7d", "30d", "all"}:
      window = "24h"
    self.send_json_response(200, self.server.job_db.get_metrics(window=window))

  def _get_metrics_page(self, _path, _query):
    api_key = self.server.api_key or ""
    key_script = "<script>window.SMA_API_KEY=%s;</script>" % json.dumps(api_key)
    self.send_html_response(200, _load_metrics_html().replace("</head>", key_script + "</head>", 1))

  def _get_favicon(self, _path, _query):
    favicon = os.path.join(SCRIPT_DIR, "logo.png")
    try:
      with open(favicon, "rb") as f:
        data = f.read()
      self.send_response(200)
      self.send_header("Content-Type", "image/png")
      self.send_header("Content-Length", str(len(data)))
      self.send_header("Cache-Control", "public, max-age=86400")
      self.end_headers()
      self.wfile.write(data)
    except FileNotFoundError:
      self.send_json_response(404, {"error": "favicon not found"})

  def do_GET(self):
    dispatch_get(self)

  # ------------------------------------------------------------------
  # POST route handlers
  # ------------------------------------------------------------------

  def _post_cleanup(self, query):
    days = int(query.get("days", [30])[0])
    deleted = self.server.job_db.cleanup_old_jobs(days)
    self.send_json_response(200, {"deleted": deleted, "days": days})

  def _parse_job_id(self, path, segment=-2):
    """Extract an integer job ID from a URL path segment.

    Returns the job ID on success, or sends a 400 response and returns None.
    segment=-2 extracts the ID from /jobs/<id>/action; segment=-1 from /jobs/<id>.
    """
    try:
      return int(path.split("/")[segment])
    except (ValueError, IndexError):
      self.send_json_response(400, {"error": "Invalid job ID"})
      return None

  def _post_admin_delete_failed(self):
    deleted = self.server.job_db.delete_failed_jobs()
    self.send_json_response(200, {"deleted": deleted})

  def _post_admin_delete_offline_nodes(self):
    deleted = self.server.job_db.delete_offline_nodes()
    self.send_json_response(200, {"deleted": deleted})

  def _post_admin_delete_all_jobs(self):
    deleted = self.server.job_db.delete_all_jobs()
    self.send_json_response(200, {"deleted": deleted})

  def _post_admin_node_action(self, path):
    """Handle POST /admin/nodes/<node_id>/<action>.

    Supported actions: approve, reject, restart, shutdown, delete
    """
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "admin" or parts[1] != "nodes":
      self.send_json_response(404, {"error": "Not found"})
      return

    node_id = unquote(parts[2])
    action = parts[3]
    actor = self.headers.get("X-Actor", "admin-ui")

    if action in {"approve", "reject"}:
      content_length = int(self.headers.get("Content-Length", 0))
      note = None
      if content_length:
        try:
          body = json.loads(self.rfile.read(content_length))
          note = body.get("note")
        except (json.JSONDecodeError, ValueError):
          self.send_json_response(400, {"error": "Invalid JSON body"})
          return

      updated = self.server.job_db.set_node_approval(
        node_id=node_id,
        approved=(action == "approve"),
        actor=actor,
        note=note,
      )
      if not updated:
        self.send_json_response(404, {"error": "Node not found", "node": node_id})
        return

      self.send_json_response(
        200,
        {
          "status": "approved" if action == "approve" else "rejected",
          "node": updated,
        },
      )
      return

    if action == "push-config":
      from resources.yamlconfig import load as _yaml_load

      cfg_file = self.server.path_config_manager._config_file
      if not cfg_file:
        self.send_json_response(503, {"error": "No config file loaded on this node"})
        return
      data = _yaml_load(cfg_file) or {}
      clean = _strip_secrets(data)
      self.server.job_db.set_cluster_config(clean, updated_by=actor)
      self.send_json_response(200, {"status": "pushed", "node": node_id})
      return

    if action in {"restart", "shutdown", "drain", "pause", "resume"}:
      targeted = self.server.job_db.send_node_command(node_id, action, requested_by=actor)
      if not targeted:
        self.send_json_response(404, {"error": "Node not found", "node": node_id})
        return
      self.send_json_response(202, {"status": f"{action}_requested", "nodes": targeted})
      return

    if action == "delete":
      deleted = self.server.job_db.delete_node(node_id)
      if not deleted:
        self.send_json_response(404, {"error": "Node not found", "node": node_id})
        return
      self.send_json_response(200, {"deleted": True, "node": node_id})
      return

    self.send_json_response(404, {"error": "Unknown node action"})

  def _post_jobs_requeue_bulk(self, query):
    config = query.get("config", [None])[0]
    count = self.server.job_db.requeue_failed_jobs(config=config)
    if count > 0:
      self.server.notify_workers()
    self.send_json_response(200, {"requeued": count})

  _BULK_ACTIONS = ("requeue", "cancel", "delete")

  def _post_jobs_bulk(self):
    """Apply *action* to every job id listed in the request body.

    Body schema::

        {"action": "requeue|cancel|delete", "ids": [1, 2, 3]}

    Returns a per-id breakdown so the UI can flag partial failures
    (e.g. attempting to cancel a completed job)::

        {"action": "...", "succeeded": [...], "skipped": [...], "not_found": [...]}
    """
    content_length = int(self.headers.get("Content-Length", 0))
    try:
      body = json.loads(self.rfile.read(content_length) if content_length else b"{}")
    except (json.JSONDecodeError, ValueError):
      self.send_json_response(400, {"error": "Invalid JSON body"})
      return
    action = body.get("action") if isinstance(body, dict) else None
    raw_ids = body.get("ids") if isinstance(body, dict) else None
    if action not in self._BULK_ACTIONS:
      self.send_json_response(400, {"error": "action must be one of %s" % ", ".join(self._BULK_ACTIONS)})
      return
    if not isinstance(raw_ids, list) or not raw_ids:
      self.send_json_response(400, {"error": "ids must be a non-empty list of integers"})
      return
    try:
      ids = [int(j) for j in raw_ids]
    except (TypeError, ValueError):
      self.send_json_response(400, {"error": "ids must be integers"})
      return

    if action == "delete":
      # Cancel any running subprocesses first so we don't orphan ffmpeg
      # children when their job rows disappear.
      for jid in ids:
        if self.server.job_db.get_job(jid) and self.server.job_db.get_job(jid).get("status") == "running":
          try:
            self.server.cancel_job(jid)
          except Exception:
            self.server.logger.exception("Bulk delete: failed to cancel running job %d before delete" % jid)
      deleted = self.server.job_db.delete_jobs(ids)
      not_found = [j for j in ids if j not in deleted]
      self.server.logger.info("Bulk delete: %d/%d jobs deleted" % (len(deleted), len(ids)))
      self.send_json_response(200, {"action": action, "succeeded": deleted, "skipped": [], "not_found": not_found})
      return

    succeeded, skipped, not_found = [], [], []
    for jid in ids:
      job = self.server.job_db.get_job(jid)
      if job is None:
        not_found.append(jid)
        continue
      if action == "requeue":
        if self.server.job_db.requeue_job(jid):
          succeeded.append(jid)
        else:
          skipped.append({"id": jid, "reason": "not_failed", "status": job.get("status")})
      else:  # cancel
        if self.server.cancel_job(jid):
          succeeded.append(jid)
        else:
          skipped.append({"id": jid, "reason": "not_cancellable", "status": job.get("status")})

    if action == "requeue" and succeeded:
      self.server.notify_workers()
    self.server.logger.info("Bulk %s: %d succeeded, %d skipped, %d not found" % (action, len(succeeded), len(skipped), len(not_found)))
    self.send_json_response(200, {"action": action, "succeeded": succeeded, "skipped": skipped, "not_found": not_found})

  def _post_job_requeue(self, path):
    job_id = self._parse_job_id(path)
    if job_id is None:
      return
    requeued = self.server.job_db.requeue_job(job_id)
    if requeued:
      self.server.notify_workers()
      self.send_json_response(200, {"requeued": True, "job_id": job_id})
    else:
      job = self.server.job_db.get_job(job_id)
      if job is None:
        self.send_json_response(404, {"error": "Job not found"})
      else:
        self.send_json_response(409, {"error": "Job cannot be requeued", "status": job["status"], "note": "Only failed jobs can be requeued"})

  def _post_job_cancel(self, path):
    job_id = self._parse_job_id(path)
    if job_id is None:
      return
    cancelled = self.server.cancel_job(job_id)
    if cancelled:
      self.send_json_response(200, {"cancelled": True, "job_id": job_id})
    else:
      job = self.server.job_db.get_job(job_id)
      if job is None:
        self.send_json_response(404, {"error": "Job not found"})
      else:
        self.send_json_response(409, {"error": "Job cannot be cancelled", "status": job["status"], "note": "Only pending or running jobs can be cancelled"})

  def _post_job_priority(self, path):
    job_id = self._parse_job_id(path)
    if job_id is None:
      return
    content_length = int(self.headers.get("Content-Length", 0))
    try:
      body = json.loads(self.rfile.read(content_length) if content_length else b"{}")
    except (json.JSONDecodeError, ValueError):
      self.send_json_response(400, {"error": "Invalid JSON body"})
      return
    if "priority" not in body:
      self.send_json_response(400, {"error": "Missing 'priority' field"})
      return
    try:
      priority = int(body["priority"])
    except (TypeError, ValueError):
      self.send_json_response(400, {"error": "'priority' must be an integer"})
      return
    updated = self.server.job_db.set_job_priority(job_id, priority)
    if updated:
      self.send_json_response(200, {"job_id": job_id, "priority": priority})
    else:
      job = self.server.job_db.get_job(job_id)
      if job is None:
        self.send_json_response(404, {"error": "Job not found"})
      else:
        self.send_json_response(409, {"error": "Priority can only be set on pending jobs", "status": job["status"]})

  def _post_scan_filter(self):
    paths = self._read_json_paths()
    if paths is None:
      return
    unscanned = self.server.job_db.filter_unscanned(paths)
    self.send_json_response(200, {"unscanned": unscanned, "total": len(paths), "already_scanned": len(paths) - len(unscanned)})

  def _post_scan_record(self):
    paths = self._read_json_paths()
    if paths is None:
      return
    self.server.job_db.record_scanned(paths)
    self.send_json_response(200, {"recorded": len(paths)})

  def _post_shutdown(self, _path, query):
    node_id = query.get("node", [None])[0]
    actor = self.headers.get("X-Actor", "api")
    if node_id and self.server.job_db.is_distributed:
      # Remote: write command to DB; target node acts on next heartbeat
      targeted = self.server.job_db.send_node_command(node_id, "shutdown", requested_by=actor)
      self.send_json_response(202, {"status": "shutdown_requested", "nodes": targeted})
      return
    if not node_id and self.server.job_db.is_distributed:
      # Broadcast to all online nodes (including self)
      targeted = self.server.job_db.send_node_command(None, "shutdown", requested_by=actor)
      self.send_json_response(202, {"status": "shutdown_requested", "nodes": targeted})
      return
    # Local shutdown
    active = self.server.config_lock_manager.get_status()["active"]
    count = sum(len(v) for v in active.values())
    self.send_json_response(202, {"status": "shutting_down", "active_jobs": count})
    self.wfile.flush()
    threading.Thread(target=self.server.shutdown, daemon=True).start()

  def _post_restart(self, _path, query):
    node_id = query.get("node", [None])[0]
    actor = self.headers.get("X-Actor", "api")
    if node_id and self.server.job_db.is_distributed:
      # Remote: write command to DB; target node acts on next heartbeat
      targeted = self.server.job_db.send_node_command(node_id, "restart", requested_by=actor)
      self.send_json_response(202, {"status": "restart_requested", "nodes": targeted})
      return
    if not node_id and self.server.job_db.is_distributed:
      # Broadcast to all online nodes (including self)
      targeted = self.server.job_db.send_node_command(None, "restart", requested_by=actor)
      self.send_json_response(202, {"status": "restart_requested", "nodes": targeted})
      return
    # Local restart
    active = self.server.config_lock_manager.get_status()["active"]
    count = sum(len(v) for v in active.values())
    self.send_json_response(202, {"status": "restarting", "active_jobs": count})
    self.wfile.flush()
    threading.Thread(target=self.server.graceful_restart, daemon=True).start()

  def _post_reload(self, _path, _query):
    threading.Thread(target=self.server.reload_config, daemon=True).start()
    self.send_json_response(200, {"status": "reloading"})

  def _post_job_action(self, path):
    dispatch_post_job_action(self, path)

  # ------------------------------------------------------------------
  # Library audit
  # ------------------------------------------------------------------

  def _get_library_audit(self, query):
    if _is_explicitly_local_db(self.server.job_db):
      self.send_json_response(
        200,
        {
          "runs": [],
          "count": 0,
          "limit": int(query.get("limit", [50])[0]),
          "offset": int(query.get("offset", [0])[0]),
          "available": False,
          "reason": "Library audit requires PostgreSQL cluster mode.",
        },
      )
      return
    limit = int(query.get("limit", [50])[0])
    offset = int(query.get("offset", [0])[0])
    runs = self.server.job_db.list_audit_runs(limit=limit, offset=offset)
    self.send_json_response(200, {"runs": runs, "count": len(runs), "limit": limit, "offset": offset})

  def _get_library_audit_run(self, path):
    audit_id = self._parse_audit_id(path)
    if audit_id is None:
      return
    run = self.server.job_db.get_audit_run(audit_id)
    if run is None:
      self.send_json_response(404, {"error": "Audit run not found"})
      return
    self.send_json_response(200, run)

  def _get_library_findings(self, query):
    if _is_explicitly_local_db(self.server.job_db):
      self.send_json_response(
        200,
        {
          "findings": [],
          "count": 0,
          "limit": int(query.get("limit", [50])[0]),
          "offset": int(query.get("offset", [0])[0]),
          "available": False,
          "reason": "Library audit requires PostgreSQL cluster mode.",
        },
      )
      return
    status = query.get("status", [None])[0]
    kind = query.get("kind", [None])[0]
    path_q = query.get("path", [None])[0]
    limit = int(query.get("limit", [50])[0])
    offset = int(query.get("offset", [0])[0])
    findings = self.server.job_db.get_findings(status=status, kind=kind, path=path_q, limit=limit, offset=offset)
    self.send_json_response(200, {"findings": findings, "count": len(findings), "limit": limit, "offset": offset})

  def _get_library_finding(self, path):
    finding_id = self._parse_finding_id(path)
    if finding_id is None:
      return
    finding = self.server.job_db.get_finding(finding_id)
    if finding is None:
      self.send_json_response(404, {"error": "Finding not found"})
      return
    self.send_json_response(200, finding)

  def _post_library_audit(self):
    """Trigger an on-demand audit run (202 + detached enumerator thread).

    Body: {"paths": [...], "dry_run": bool} — both optional. Defaults to
    daemon.audit.paths and daemon.audit.dry_run.
    """
    if _is_explicitly_local_db(self.server.job_db):
      self.send_json_response(409, {"error": "Library audit requires PostgreSQL cluster mode."})
      return
    body = {}
    content_length = int(self.headers.get("Content-Length", 0))
    if content_length:
      try:
        body = json.loads(self.rfile.read(content_length))
      except (json.JSONDecodeError, ValueError) as exc:
        self.send_json_response(400, {"error": str(exc)})
        return
    pcm = self.server.path_config_manager
    paths = body.get("paths") if isinstance(body, dict) else None
    if not paths:
      paths = [a["path"] for a in pcm.audit_paths if a.get("enabled", True) and a.get("path")]
    if not paths:
      self.send_json_response(400, {"error": "No audit paths configured (daemon.audit.paths is empty)"})
      return
    audit_id = self.server.job_db.create_audit_run(paths, "api:%s" % self.server.node_id)
    self.send_json_response(202, {"status": "queued", "audit_id": audit_id, "paths": paths})
    try:
      self.wfile.flush()
    except Exception:
      pass
    threading.Thread(
      target=self._run_audit_enumerate,
      args=(audit_id, paths),
      daemon=True,
    ).start()

  def _run_audit_enumerate(self, audit_id, paths):
    """Enumerate paths into the audit queue. Runs detached so the HTTP request returns immediately."""
    from resources.library_audit.enumerator import enumerate_paths

    pcm = self.server.path_config_manager
    settings = pcm.audit_settings
    is_recycle_bin_path = getattr(pcm, "is_recycle_bin_path", None)
    try:
      batch = []
      total = 0
      for p, hint in enumerate_paths(paths, skip_dirs=list(settings.skip_dirs), is_recycle_bin_path=is_recycle_bin_path):
        batch.append((p, hint))
        if len(batch) >= 500:
          self.server.job_db.enqueue_audit_units(audit_id, batch)
          total += len(batch)
          batch = []
      if batch:
        self.server.job_db.enqueue_audit_units(audit_id, batch)
        total += len(batch)
      self.server.logger.info("Library audit run %d enumerated %d unit(s) (api trigger)" % (audit_id, total))
      if total == 0:
        self.server.job_db.set_audit_run_status(audit_id, "completed")
    except Exception:
      self.server.logger.exception("Library audit enumerate failed for run %d" % audit_id)
      try:
        self.server.job_db.set_audit_run_status(audit_id, "failed", error="enumerate raised")
      except Exception:
        pass

  def _post_library_finding_action(self, path, target_status):
    if _is_explicitly_local_db(self.server.job_db):
      self.send_json_response(409, {"error": "Library audit requires PostgreSQL cluster mode."})
      return
    finding_id = self._parse_finding_id(path)
    if finding_id is None:
      return
    rows = self.server.job_db.set_finding_status(finding_id, target_status)
    if rows == 0:
      self.send_json_response(404, {"error": "Finding not found"})
      return
    self.send_json_response(200, {"status": target_status, "finding_id": finding_id})

  def _parse_audit_id(self, path):
    """Extract integer audit id from /library/audit/<id>."""
    try:
      return int(path.rstrip("/").split("/")[-1])
    except (ValueError, IndexError):
      self.send_json_response(400, {"error": "Invalid audit id"})
      return None

  def _parse_finding_id(self, path):
    """Extract integer finding id from /library/findings/<id>[/action]."""
    parts = path.rstrip("/").split("/")
    # /library/findings/<id> → last segment is id
    # /library/findings/<id>/ack → second-to-last segment is id
    candidate = parts[-1] if parts[-1].isdigit() else (parts[-2] if len(parts) >= 2 else "")
    try:
      return int(candidate)
    except (ValueError, IndexError):
      self.send_json_response(400, {"error": "Invalid finding id"})
      return None

  def do_POST(self):
    dispatch_post(self)

  def _walk_media_files(self, directory):
    """Yield media file paths lazily using os.scandir.

    Uses an explicit stack instead of os.walk so files are emitted one at
    a time without buffering the entire tree.  This keeps startup latency
    and peak memory low on large or slow (e.g. unionfs/NFS) mounts.
    Hidden directories and dotfiles (names starting with '.') are skipped.
    """
    allowed = self.server.path_config_manager.media_extensions
    stack = [directory]
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
            elif os.path.splitext(entry.name)[1].lower() in allowed:
              yield entry.path
          # Extend in reverse so leftmost subdirectory is visited first
          stack.extend(reversed(subdirs))
      except (PermissionError, OSError):
        pass

  def _parse_webhook_body(self):
    """Parse request body into (path, extra_args, config_override, max_retries).

    Returns (None, [], None, 0) with an error response already sent on failure.
    """
    content_length = int(self.headers.get("Content-Length", 0))
    body = self.rfile.read(content_length).decode("utf-8").strip()
    return parse_generic_webhook_body(body, self.headers.get("Content-Type", ""), self.send_json_response)

  def _resolve_config(self, path, config_override):
    """Return the config file to use for path, respecting any override."""
    if config_override and os.path.exists(config_override):
      return config_override
    return self.server.path_config_manager.get_config_for_path(path)

  def _resolve_profile(self, path, config_override):
    """Return the profile to apply for path, unless an explicit config override is used."""
    if config_override:
      return None
    getter = getattr(self.server.path_config_manager, "get_profile_for_path", None)
    profile = getter(path) if callable(getter) else None
    return profile if isinstance(profile, str) and profile else None

  def _merge_args(self, path, extra_args):
    """Merge per-path default_args with request args.

    Default args are prepended; request args are appended. If a flag
    appears in both, the request arg takes precedence (default is dropped).
    This also handles the --tv/--movie mutual exclusivity.
    """
    default_args = self.server.path_config_manager.get_args_for_path(path)
    if not default_args:
      return list(extra_args)

    # Flags the caller explicitly provided — strip leading dashes for comparison
    caller_flags = {a.lstrip("-") for a in extra_args if a.startswith("-")}

    # Filter out any default flags already covered by the caller
    filtered_defaults = [a for a in default_args if not a.startswith("-") or a.lstrip("-") not in caller_flags]

    return filtered_defaults + list(extra_args)

  def _merge_profile_arg(self, args, profile):
    """Append a path profile unless the caller already supplied one."""
    merged = list(args)
    if profile and "--profile" not in merged and "-p" not in merged:
      merged.extend(["--profile", profile])
    return merged

  @staticmethod
  def _extract_profile_from_tag_labels(tag_labels):
    """Return ``sma-profile-<name>`` override from tag labels, or ``None``."""
    prefix = "sma-profile-"
    for label in tag_labels or []:
      raw = str(label).strip()
      lowered = raw.lower()
      if lowered.startswith(prefix) and len(lowered) > len(prefix):
        profile = raw[len(prefix) :].strip()
        if profile:
          return profile
    return None

  def _resolve_arr_tag_profile(self, arr_type, path, tag_ids):
    """Resolve profile override from ARR tag IDs via service API lookup.

    Returns profile name (without ``sma-profile-`` prefix) or ``None``.
    """
    if not tag_ids:
      return None
    pcm = self.server.path_config_manager
    get_services = getattr(pcm, "get_services_for_path", None)
    if not callable(get_services):
      return None

    service_refs = get_services(path)
    if not isinstance(service_refs, (list, tuple)):
      service_refs = []
    get_service_instance = getattr(pcm, "get_service_instance", None)
    if not callable(get_service_instance):
      return None

    for ref in service_refs:
      if isinstance(ref, str):
        if "." not in ref:
          continue
        service_type, instance_name = ref.split(".", 1)
      elif isinstance(ref, (list, tuple)) and len(ref) == 2:
        service_type, instance_name = ref
      else:
        continue

      if service_type != arr_type:
        continue

      service = get_service_instance(service_type, instance_name)
      if not isinstance(service, dict):
        continue
      base_url = (service.get("url") or "").strip().rstrip("/")
      api_key = (service.get("apikey") or "").strip()
      if not base_url or not api_key:
        continue

      try:
        response = requests.get(
          base_url + "/api/v3/tag",
          headers={"X-Api-Key": api_key},
          timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
      except Exception:
        self.server.logger.warning("Unable to fetch %s tag labels for profile override; using routing profile." % arr_type)
        continue

      labels_by_id = {}
      if isinstance(payload, list):
        for row in payload:
          if isinstance(row, dict) and isinstance(row.get("id"), int) and isinstance(row.get("label"), str):
            labels_by_id[row["id"]] = row["label"]

      labels = [labels_by_id[x] for x in tag_ids if x in labels_by_id]
      profile = self._extract_profile_from_tag_labels(labels)
      if profile:
        self.server.logger.info("Applying %s profile override from ARR tag: %s" % (arr_type, profile))
        return profile
    return None

  def _queue_directory(self, path, extra_args, config_override, max_retries=0):
    """Expand directory to media files, queue each, respond.

    Files are discovered and queued lazily via _walk_media_files so the
    first job is submitted as soon as the first file is found — no full
    tree buffering before work begins.

    The directory is walked at its original (submitted) path; path rewrites
    are applied to each discovered file individually so jobs are created
    with the rewritten (e.g. union-mount) paths.

    Files whose extension already matches the resolved profile's
    ``output-extension`` are skipped at queue time when neither
    ``process-same-extensions`` nor ``force-convert`` is enabled — this
    matches the runtime no-op detection in MediaProcessor.process() so
    we don't pollute the queue with jobs the worker would immediately
    discard.
    """
    pcm = self.server.path_config_manager
    skip_same_ext = getattr(pcm, "should_skip_same_extension", None)
    has_match = getattr(pcm, "has_routing_match", None)
    strict = getattr(pcm, "strict_routing", False) and not config_override
    queued, duplicates, skipped = [], [], []
    for filepath in self._walk_media_files(path):
      job_path = pcm.rewrite_path(filepath)
      if callable(skip_same_ext) and skip_same_ext(job_path):
        skipped.append({"path": job_path, "reason": "same_as_output_extension"})
        continue
      if strict and callable(has_match) and not has_match(job_path):
        skipped.append({"path": job_path, "reason": "no_routing_match"})
        continue
      resolved_config = self._resolve_config(job_path, config_override)
      profile = self._resolve_profile(job_path, config_override)
      job_args = self._merge_profile_arg(self._merge_args(job_path, extra_args), profile)
      job_id = self.server.job_db.add_job(job_path, resolved_config, job_args, max_retries=max_retries)
      if job_id is None:
        existing = self.server.job_db.find_active_job(job_path)
        duplicates.append({"path": job_path, "job_id": existing["id"] if existing else None})
      else:
        queued.append({"job_id": job_id, "path": job_path, "config": resolved_config, "profile": profile})

    if not queued and not duplicates and not skipped:
      self.send_json_response(200, {"status": "empty", "path": path, "message": "No media files found in directory"})
      return

    if queued:
      self.server.notify_workers()
    self.server.logger.info(
      "Directory %s: queued %d, duplicates %d, skipped %d" % (path, len(queued), len(duplicates), len(skipped)),
    )

    self.send_json_response(
      202,
      {
        "status": "queued",
        "directory": path,
        "queued": queued,
        "duplicates": duplicates,
        "skipped": skipped,
        "queued_count": len(queued),
        "duplicate_count": len(duplicates),
        "skipped_count": len(skipped),
      },
    )

  def _queue_file(self, path, extra_args, config_override, max_retries=0):
    """Queue a single file job and respond."""
    pcm = self.server.path_config_manager
    job_path = pcm.rewrite_path(path)
    skip_same_ext = getattr(pcm, "should_skip_same_extension", None)
    if callable(skip_same_ext) and skip_same_ext(job_path):
      self.server.logger.info("Skipped queue: %s (same as output extension)" % job_path)
      self.send_json_response(
        200,
        {
          "status": "skipped",
          "path": job_path,
          "reason": "same_as_output_extension",
          "message": "process-same-extensions is false and force-convert is false; file already matches output extension",
        },
      )
      return
    if getattr(pcm, "strict_routing", False) and not config_override:
      has_match = getattr(pcm, "has_routing_match", None)
      if callable(has_match) and not has_match(job_path):
        self.server.logger.warning("Refused queue under strict-routing: %s did not match any daemon.routing rule [routing-miss]." % job_path)
        self.send_json_response(
          400,
          {
            "status": "refused",
            "path": job_path,
            "reason": "no_routing_match",
            "message": "daemon.strict-routing=true and no daemon.routing rule matched this path. Add a rule or set strict-routing=false.",
          },
        )
        return
    resolved_config = self._resolve_config(job_path, config_override)
    profile = self._resolve_profile(job_path, config_override)
    job_args = self._merge_profile_arg(self._merge_args(job_path, extra_args), profile)
    job_id = self.server.job_db.add_job(job_path, resolved_config, job_args, max_retries=max_retries)

    if job_id is None:
      existing = self.server.job_db.find_active_job(job_path)
      self.server.logger.info("Duplicate job submission for: %s" % job_path)
      self.send_json_response(200, {"status": "duplicate", "job_id": existing["id"] if existing else None, "path": job_path, "config": resolved_config})
      return

    self.server.notify_workers()
    log_file = self.server.config_log_manager.get_log_file(resolved_config)
    config_busy = self.server.config_lock_manager.is_locked(resolved_config)
    pending = self.server.job_db.pending_count_for_config(resolved_config)
    token = set_job_id(job_id)
    try:
      self.server.logger.info(
        "Queued job %d: %s (config: %s)" % (job_id, job_path, resolved_config),
        extra={"job_id": job_id, "path": job_path, "config": os.path.basename(resolved_config)},
      )
    finally:
      clear_job_id(token)
    self.send_json_response(
      202,
      {"status": "queued", "job_id": job_id, "path": job_path, "config": resolved_config, "profile": profile, "log_file": log_file, "config_busy": config_busy, "pending_jobs": pending},
    )

  def _parse_sonarr_body(self):
    """Parse a Sonarr-native webhook payload.

    Returns (path, extra_args, profile_override, tag_ids) on success, or (None, [], None, []) with an error
    response already sent on failure.  Test events return (None, []) with
    a 200 response already sent.

    Expected payload shape (Sonarr Connection → Webhook, On Download/Upgrade):
    {
      "eventType": "Download",
      "series":      { "tvdbId": 73871, "imdbId": "tt0472308" },
      "episodes":    [ { "seasonNumber": 3, "episodeNumber": 10 } ],
      "episodeFile": { "path": "/mnt/unionfs/Media/TV/Show/S03E10.mkv" }
    }
    """
    content_length = int(self.headers.get("Content-Length", 0))
    body = self.rfile.read(content_length).decode("utf-8").strip()
    return parse_sonarr_body(body, self.send_json_response)

  def _parse_radarr_body(self):
    """Parse a Radarr-native webhook payload.

    Returns (path, extra_args, profile_override, tag_ids) on success, or (None, [], None, []) with an error
    response already sent on failure.  Test events return (None, []) with
    a 200 response already sent.

    Expected payload shape (Radarr Connection → Webhook, On Download/Upgrade):
    {
      "eventType": "Download",
      "movie":     { "tmdbId": 603, "imdbId": "tt0133093" },
      "movieFile": { "path": "/mnt/unionfs/Media/Movies/The Matrix.mkv" }
    }
    """
    content_length = int(self.headers.get("Content-Length", 0))
    body = self.rfile.read(content_length).decode("utf-8").strip()
    return parse_radarr_body(body, self.send_json_response)

  def _dispatch_path(self, path, extra_args, config_override=None, max_retries=0):
    """Shared tail: validate path, queue file or directory.

    Path rewrites are deferred to job-creation time so that a directory
    submission walks the original (local) path while each discovered file
    is queued under its rewritten (e.g. union-mount) path.
    """
    path = os.path.abspath(path)
    if not os.path.exists(path):
      self.send_json_response(400, {"error": "Path does not exist", "path": path})
      return
    # Recycle-bin check uses the rewritten path because recycle dirs are
    # configured using the canonical (post-rewrite) filesystem view.
    if self.server.path_config_manager.is_recycle_bin_path(self.server.path_config_manager.rewrite_path(path)):
      self.server.logger.warning("Rejected recycle-bin path: %s" % path)
      self.send_json_response(400, {"error": "Path is inside a recycle-bin directory", "path": path})
      return
    if os.path.isdir(path):
      self._queue_directory(path, extra_args, config_override, max_retries)
    else:
      self._queue_file(path, extra_args, config_override, max_retries)

  def _handle_sonarr_webhook(self):
    try:
      path, extra_args, profile_override, tag_ids = self._parse_sonarr_body()
      if path is None:
        return
      if not profile_override:
        profile_override = self._resolve_arr_tag_profile("sonarr", path, tag_ids)
      if profile_override:
        extra_args = self._merge_profile_arg(extra_args, profile_override)
      self._dispatch_path(path, extra_args)
    except Exception as e:
      self.server.logger.exception("Error handling Sonarr webhook: %s" % e)
      self.send_json_response(500, {"error": str(e)})

  def _handle_radarr_webhook(self):
    try:
      path, extra_args, profile_override, tag_ids = self._parse_radarr_body()
      if path is None:
        return
      if not profile_override:
        profile_override = self._resolve_arr_tag_profile("radarr", path, tag_ids)
      if profile_override:
        extra_args = self._merge_profile_arg(extra_args, profile_override)
      self._dispatch_path(path, extra_args)
    except Exception as e:
      self.server.logger.exception("Error handling Radarr webhook: %s" % e)
      self.send_json_response(500, {"error": str(e)})

  def _handle_webhook(self):
    try:
      path, extra_args, config_override, max_retries = self._parse_webhook_body()
      if path is None:
        return
      self._dispatch_path(path, extra_args, config_override, max_retries)
    except Exception as e:
      self.server.logger.exception("Error handling request: %s" % e)
      self.send_json_response(500, {"error": str(e)})
