import json
import os
import re as _re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from resources.daemon.constants import SCRIPT_DIR
from resources.daemon.context import clear_job_id, set_job_id
from resources.daemon.db import STATUS_RUNNING

DOCS_DIR = os.path.join(SCRIPT_DIR, "docs")
DOCS_TEMPLATE_PATH = os.path.join(SCRIPT_DIR, "resources", "docs.html")
DASHBOARD_HTML_PATH = os.path.join(SCRIPT_DIR, "resources", "dashboard.html")
ADMIN_HTML_PATH = os.path.join(SCRIPT_DIR, "resources", "admin.html")

# Ordered list of doc pages: (slug, title).  The slug maps to docs/<slug>.md.
# "index" maps to docs/README.md.
DOC_PAGES = [
    ("index", "Overview"),
    ("getting-started", "Getting Started"),
    ("configuration", "Configuration"),
    ("daemon", "Daemon Mode"),
    ("integrations", "Integrations"),
    ("hardware-acceleration", "Hardware Acceleration"),
    ("deployment", "Deployment"),
    ("troubleshooting", "Troubleshooting"),
]


def _render_markdown_to_html(md_text):
    """Minimal Markdown to HTML renderer for documentation display."""
    lines = md_text.split("\n")
    html_parts = []
    in_code = False
    in_table = False
    in_list = False
    list_type = None

    for line in lines:
        # Fenced code blocks
        if line.strip().startswith("```"):
            if in_code:
                html_parts.append("</code></pre>")
                in_code = False
            else:
                lang = line.strip()[3:].strip()
                html_parts.append('<pre class="bg-gray-800 rounded-lg p-4 overflow-x-auto my-4 border border-gray-700"><code class="text-sm text-green-300">')
                in_code = True
            continue
        if in_code:
            html_parts.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            continue

        # Close table if line doesn't look like table
        if in_table and not line.strip().startswith("|"):
            html_parts.append("</tbody></table></div>")
            in_table = False

        # Close list if line doesn't continue it
        if in_list and line.strip() and not _re.match(r"^(\s*[-*]\s|^\s*\d+\.\s)", line):
            html_parts.append("</%s>" % list_type)
            in_list = False

        stripped = line.strip()

        # Blank line
        if not stripped:
            if in_list:
                html_parts.append("</%s>" % list_type)
                in_list = False
            continue

        # Headings
        hm = _re.match(r"^(#{1,6})\s+(.*)", stripped)
        if hm:
            level = len(hm.group(1))
            text = _inline(hm.group(2))
            slug = _re.sub(r"[^\w-]", "", hm.group(2).lower().replace(" ", "-"))
            sizes = {1: "text-3xl", 2: "text-2xl", 3: "text-xl", 4: "text-lg", 5: "text-base", 6: "text-sm"}
            mt = "mt-10" if level <= 2 else "mt-6"
            html_parts.append('<h%d id="%s" class="%s %s font-bold text-white mb-3">%s</h%d>' % (level, slug, sizes.get(level, "text-base"), mt, text, level))
            continue

        # Horizontal rule
        if _re.match(r"^-{3,}$", stripped):
            html_parts.append('<hr class="border-gray-700 my-8">')
            continue

        # Table
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if all(_re.match(r"^[-:]+$", c) for c in cells):
                continue  # separator row
            if not in_table:
                in_table = True
                html_parts.append('<div class="overflow-x-auto my-4"><table class="w-full text-sm"><thead><tr class="border-b border-gray-700">')
                for c in cells:
                    html_parts.append('<th class="text-left py-2 px-3 text-gray-400">%s</th>' % _inline(c))
                html_parts.append('</tr></thead><tbody class="divide-y divide-gray-700/50">')
            else:
                html_parts.append('<tr class="hover:bg-gray-800/50">')
                for c in cells:
                    html_parts.append('<td class="py-2 px-3 text-gray-300">%s</td>' % _inline(c))
                html_parts.append("</tr>")
            continue

        # Unordered list
        lm = _re.match(r"^(\s*)[-*]\s+(.*)", line)
        if lm:
            if not in_list:
                in_list = True
                list_type = "ul"
                html_parts.append('<ul class="list-disc list-inside space-y-1 my-3 text-gray-300">')
            html_parts.append("<li>%s</li>" % _inline(lm.group(2)))
            continue

        # Ordered list
        lm = _re.match(r"^(\s*)\d+\.\s+(.*)", line)
        if lm:
            if not in_list:
                in_list = True
                list_type = "ol"
                html_parts.append('<ol class="list-decimal list-inside space-y-1 my-3 text-gray-300">')
            html_parts.append("<li>%s</li>" % _inline(lm.group(2)))
            continue

        # Paragraph
        html_parts.append('<p class="text-gray-300 my-2 leading-relaxed">%s</p>' % _inline(stripped))

    # Close open blocks
    if in_code:
        html_parts.append("</code></pre>")
    if in_table:
        html_parts.append("</tbody></table></div>")
    if in_list:
        html_parts.append("</%s>" % list_type)

    return "\n".join(html_parts)


def _inline(text):
    """Process inline Markdown formatting."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Bold
    text = _re.sub(r"\*\*(.+?)\*\*", r'<strong class="text-white">\1</strong>', text)
    # Italic
    text = _re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Inline code
    text = _re.sub(r"`([^`]+)`", r'<code class="bg-gray-800 text-blue-300 px-1.5 py-0.5 rounded text-xs">\1</code>', text)
    # Links
    text = _re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" class="text-blue-400 hover:underline">\1</a>', text)
    return text


def _load_dashboard_html():
    with open(DASHBOARD_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _load_admin_html():
    with open(ADMIN_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _load_docs_template(active_slug="index"):
    with open(DOCS_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        tmpl = f.read()
    # Build sidebar nav HTML and inject as %NAV% placeholder
    nav_items = []
    for slug, title in DOC_PAGES:
        href = "/docs" if slug == "index" else "/docs/" + slug
        active = ' class="bg-gray-700 text-white"' if slug == active_slug else ' class="text-gray-300 hover:text-white"'
        nav_items.append('<a href="%s"%s>%s</a>' % (href, active, title))
    nav_html = "\n".join(nav_items)
    return tmpl.replace("%NAV%", nav_html)


class WebhookHandler(BaseHTTPRequestHandler):
    """HTTP request handler for webhook endpoints."""

    # Endpoints that don't require authentication (prefix-matched for /docs/*)
    PUBLIC_ENDPOINTS = ["/", "/dashboard", "/admin", "/health", "/status", "/docs", "/favicon.png"]

    def log_message(self, format, *args):
        self.server.logger.debug("%s - %s" % (self.address_string(), format % args))

    def send_json_response(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode("utf-8"))

    def send_html_response(self, status_code, html):
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
        if not api_key:
            # No API key configured, allow all requests
            return True

        # Check X-API-Key header
        request_key = self.headers.get("X-API-Key")

        # Also check Authorization header (Bearer token)
        if not request_key:
            auth_header = self.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                request_key = auth_header[7:]

        if request_key == api_key:
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
        now = datetime.now(timezone.utc)
        uptime = int((now - self.server.started_at).total_seconds())
        self.send_json_response(
            200,
            {
                "status": "ok",
                "node": self.server.node_id,
                "started_at": self.server.started_at.isoformat(),
                "uptime_seconds": uptime,
                "workers": self.server.worker_count,
                "jobs": stats,
                "active": lock_status["active"],
                "waiting": lock_status["waiting"],
            },
        )

    def _get_status(self):
        # Cluster-wide status — only meaningful with PostgreSQL backend
        if self.server.job_db.is_distributed:
            # Run staleness check on every status request so the response
            # reflects current reality rather than waiting for the next
            # heartbeat cycle.
            recovered = self.server.job_db.recover_stale_nodes(self.server.stale_seconds)
            for stale_id, job_count in recovered:
                self.server.logger.warning("Status check: recovered %d jobs from stale node %s" % (job_count, stale_id))
            if any(job_count > 0 for _, job_count in recovered):
                self.server.notify_workers()
            nodes = self.server.job_db.get_cluster_nodes()
            stats = self.server.job_db.get_stats()
            # Replace 0.0.0.0 (bind-all address) with the IP the client actually
            # connected to, so the UI can display a useful address.
            local_ip = self.connection.getsockname()[0]
            for node in nodes:
                if node.get("host") == "0.0.0.0":
                    node["host"] = local_ip
            self.send_json_response(200, {"cluster": nodes, "jobs": stats})
        else:
            # SQLite single-node — return local health with explanatory note
            lock_status = self.server.config_lock_manager.get_status()
            stats = self.server.job_db.get_stats()
            self.send_json_response(
                200,
                {
                    "status": "ok",
                    "node": self.server.node_id,
                    "note": "Cluster status requires PostgreSQL backend (set SMA_DAEMON_DB_URL)",
                    "workers": self.server.worker_count,
                    "jobs": stats,
                    "active": lock_status["active"],
                    "waiting": lock_status["waiting"],
                },
            )

    def _get_jobs(self, query):
        status = query.get("status", [None])[0]
        config = query.get("config", [None])[0]
        limit = int(query.get("limit", [100])[0])
        offset = int(query.get("offset", [0])[0])
        jobs = self.server.job_db.get_jobs(status=status, config=config, limit=limit, offset=offset)
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

    def _get_browse(self, query):
        """List directories and media files under a path, constrained to configured roots."""
        path = query.get("path", [""])[0].strip()
        pcm = self.server.path_config_manager

        # Collect valid root prefixes: the configured path_config paths themselves,
        # plus every ancestor directory of each, so navigation down from "/" works.
        allowed_roots = set()
        for entry in pcm.path_configs:
            p = entry["path"]
            allowed_roots.add(p)
            # Add all parent directories so the user can navigate into the root
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
            dirs = sorted(set(os.path.normpath(e["path"]) for e in pcm.path_configs if os.path.isdir(e["path"])))
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
        configs_with_status = [
            {
                "path": entry["path"],
                "config": entry["config"],
                "default_args": entry.get("default_args", []),
                "log_file": self.server.config_log_manager.get_log_file(entry["config"]),
                "active_jobs": self.server.config_lock_manager.get_active_jobs(entry["config"]),
                "pending_jobs": self.server.job_db.pending_count_for_config(entry["config"]),
            }
            for entry in self.server.path_config_manager.path_configs
        ]
        pcm = self.server.path_config_manager
        default_config = pcm.default_config
        self.send_json_response(
            200,
            {
                "default_config": default_config,
                "default_args": pcm.default_args,
                "default_log": self.server.config_log_manager.get_log_file(default_config),
                "default_active_jobs": self.server.config_lock_manager.get_active_jobs(default_config),
                "default_pending_jobs": self.server.job_db.pending_count_for_config(default_config),
                "path_configs": configs_with_status,
                "logs_directory": self.server.config_log_manager.logs_dir,
            },
        )

    def _get_scan(self, query):
        # Filter a list of paths to those not yet recorded as scanned.
        # Usage: GET /scan?path=/a/b.mkv&path=/c/d.mkv
        # For large path lists use POST /scan/filter instead.
        paths = query.get("path", [])
        unscanned = self.server.job_db.filter_unscanned(paths)
        self.send_json_response(200, {"unscanned": unscanned, "total": len(paths), "already_scanned": len(paths) - len(unscanned)})

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

    _GET_ROUTES = {
        "/": lambda self, p, q: self._get_root(p, q),
        "/dashboard": lambda self, p, q: self._get_dashboard(p, q),
        "/admin": lambda self, p, q: self._get_admin(p, q),
        "/docs": lambda self, p, q: self._get_docs(p, q),
        "/health": lambda self, p, q: self._get_health(),
        "/status": lambda self, p, q: self._get_status(),
        "/jobs": lambda self, p, q: self._get_jobs(q),
        "/configs": lambda self, p, q: self._get_configs(),
        "/stats": lambda self, p, q: self._get_stats(p, q),
        "/scan": lambda self, p, q: self._get_scan(q),
        "/browse": lambda self, p, q: self._get_browse(q),
        "/favicon.png": lambda self, p, q: self._get_favicon(p, q),
    }

    _GET_PREFIX_ROUTES = [
        ("/docs/", lambda self, p, q: self._get_docs(p, q)),
        ("/jobs/", lambda self, p, q: self._get_job(p)),
    ]

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        # Check authentication for non-public endpoints
        if not self.is_public_endpoint(parsed.path) and not self.check_auth():
            return

        handler = self._GET_ROUTES.get(parsed.path)
        if handler is not None:
            handler(self, parsed.path, query)
            return
        for prefix, handler in self._GET_PREFIX_ROUTES:
            if parsed.path.startswith(prefix):
                handler(self, parsed.path, query)
                return
        self.send_json_response(404, {"error": "Not found"})

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

    def _post_jobs_requeue_bulk(self, query):
        config = query.get("config", [None])[0]
        count = self.server.job_db.requeue_failed_jobs(config=config)
        if count > 0:
            self.server.notify_workers()
        self.send_json_response(200, {"requeued": count})

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
        if node_id and self.server.job_db.is_distributed:
            # Remote: write command to DB; target node acts on next heartbeat
            targeted = self.server.job_db.send_node_command(node_id, "shutdown")
            self.send_json_response(202, {"status": "shutdown_requested", "nodes": targeted})
            return
        if not node_id and self.server.job_db.is_distributed:
            # Broadcast to all online nodes (including self)
            targeted = self.server.job_db.send_node_command(None, "shutdown")
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
        if node_id and self.server.job_db.is_distributed:
            # Remote: write command to DB; target node acts on next heartbeat
            targeted = self.server.job_db.send_node_command(node_id, "restart")
            self.send_json_response(202, {"status": "restart_requested", "nodes": targeted})
            return
        if not node_id and self.server.job_db.is_distributed:
            # Broadcast to all online nodes (including self)
            targeted = self.server.job_db.send_node_command(None, "restart")
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

    _POST_ROUTES = {
        "/": lambda self, p, q: self._handle_webhook(),
        "/webhook": lambda self, p, q: self._handle_webhook(),
        "/convert": lambda self, p, q: self._handle_webhook(),
        "/admin/delete-failed": lambda self, p, q: self._post_admin_delete_failed(),
        "/admin/delete-offline-nodes": lambda self, p, q: self._post_admin_delete_offline_nodes(),
        "/admin/delete-all-jobs": lambda self, p, q: self._post_admin_delete_all_jobs(),
        "/shutdown": lambda self, p, q: self._post_shutdown(p, q),
        "/restart": lambda self, p, q: self._post_restart(p, q),
        "/reload": lambda self, p, q: self._post_reload(),
        "/cleanup": lambda self, p, q: self._post_cleanup(q),
        "/jobs/requeue": lambda self, p, q: self._post_jobs_requeue_bulk(q),
        "/scan/filter": lambda self, p, q: self._post_scan_filter(),
        "/scan/record": lambda self, p, q: self._post_scan_record(),
    }

    _POST_PREFIX_ROUTES = [
        ("/jobs/", lambda self, p, q: self._post_job_action(p)),
    ]

    def _post_job_action(self, path):
        if path.endswith("/requeue"):
            self._post_job_requeue(path)
        elif path.endswith("/cancel"):
            self._post_job_cancel(path)
        elif path.endswith("/priority"):
            self._post_job_priority(path)
        else:
            self.send_json_response(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        # All POST endpoints require authentication
        if not self.check_auth():
            return

        handler = self._POST_ROUTES.get(parsed.path)
        if handler is not None:
            handler(self, parsed.path, query)
            return
        for prefix, handler in self._POST_PREFIX_ROUTES:
            if parsed.path.startswith(prefix):
                handler(self, parsed.path, query)
                return
        self.send_json_response(404, {"error": "Not found"})

    def _collect_media_files(self, directory):
        """Recursively collect media files from a directory.

        Only files whose extension is in path_config_manager.media_extensions
        are included. Hidden directories and dotfiles are skipped.
        ffprobe validation happens later inside manual.py when each job runs.
        """
        allowed = self.server.path_config_manager.media_extensions
        candidates = []
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if fname.startswith("."):
                    continue
                if os.path.splitext(fname)[1].lower() in allowed:
                    candidates.append(os.path.join(root, fname))
        return sorted(candidates)

    def _parse_webhook_body(self):
        """Parse request body into (path, extra_args, config_override, max_retries).

        Returns (None, [], None, 0) with an error response already sent on failure.
        """
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_json_response(400, {"error": "Empty request body"})
            return None, [], None, 0

        body = self.rfile.read(content_length).decode("utf-8").strip()
        path = None
        extra_args = []
        config_override = None
        max_retries = 0

        if "application/json" in self.headers.get("Content-Type", ""):
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    path = data.get("path") or data.get("file") or data.get("input")
                    extra_args = data.get("args", [])
                    config_override = data.get("config")
                    max_retries = int(data.get("max_retries", 0))
                    if isinstance(extra_args, str):
                        extra_args = extra_args.split()
                elif isinstance(data, str):
                    path = data
            except (json.JSONDecodeError, ValueError, TypeError):
                path = body
        else:
            path = body

        if not path:
            self.send_json_response(400, {"error": "No path provided"})
            return None, [], None, 0

        return path, extra_args, config_override, max_retries

    def _resolve_config(self, path, config_override):
        """Return the config file to use for path, respecting any override."""
        if config_override and os.path.exists(config_override):
            return config_override
        return self.server.path_config_manager.get_config_for_path(path)

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

    def _queue_directory(self, path, extra_args, config_override, max_retries=0):
        """Expand directory to media files, queue each, respond."""
        files = self._collect_media_files(path)
        if not files:
            self.send_json_response(200, {"status": "empty", "path": path, "message": "No media files found in directory"})
            return

        queued, duplicates = [], []
        for filepath in files:
            resolved_config = self._resolve_config(filepath, config_override)
            job_id = self.server.job_db.add_job(filepath, resolved_config, self._merge_args(filepath, extra_args), max_retries=max_retries)
            if job_id is None:
                existing = self.server.job_db.find_active_job(filepath)
                duplicates.append({"path": filepath, "job_id": existing["id"] if existing else None})
            else:
                queued.append({"job_id": job_id, "path": filepath, "config": resolved_config})

        if queued:
            self.server.notify_workers()
            self.server.logger.info("Directory %s: queued %d files, %d duplicates" % (path, len(queued), len(duplicates)))

        self.send_json_response(202, {"status": "queued", "directory": path, "queued": queued, "duplicates": duplicates, "queued_count": len(queued), "duplicate_count": len(duplicates)})

    def _queue_file(self, path, extra_args, config_override, max_retries=0):
        """Queue a single file job and respond."""
        resolved_config = self._resolve_config(path, config_override)
        job_id = self.server.job_db.add_job(path, resolved_config, self._merge_args(path, extra_args), max_retries=max_retries)

        if job_id is None:
            existing = self.server.job_db.find_active_job(path)
            self.server.logger.info("Duplicate job submission for: %s" % path)
            self.send_json_response(200, {"status": "duplicate", "job_id": existing["id"] if existing else None, "path": path, "config": resolved_config})
            return

        self.server.notify_workers()
        log_file = self.server.config_log_manager.get_log_file(resolved_config)
        config_busy = self.server.config_lock_manager.is_locked(resolved_config)
        pending = self.server.job_db.pending_count_for_config(resolved_config)
        token = set_job_id(job_id)
        try:
            self.server.logger.info(
                "Queued job %d: %s (config: %s)" % (job_id, path, resolved_config),
                extra={"job_id": job_id, "path": path, "config": os.path.basename(resolved_config)},
            )
        finally:
            clear_job_id(token)
        self.send_json_response(202, {"status": "queued", "job_id": job_id, "path": path, "config": resolved_config, "log_file": log_file, "config_busy": config_busy, "pending_jobs": pending})

    def _handle_webhook(self):
        try:
            path, extra_args, config_override, max_retries = self._parse_webhook_body()
            if path is None:
                return

            path = os.path.abspath(path)
            path = self.server.path_config_manager.rewrite_path(path)
            if not os.path.exists(path):
                self.send_json_response(400, {"error": "Path does not exist", "path": path})
                return

            if self.server.path_config_manager.is_recycle_bin_path(path):
                self.server.logger.warning("Rejected recycle-bin path: %s" % path)
                self.send_json_response(400, {"error": "Path is inside a recycle-bin directory", "path": path})
                return

            if os.path.isdir(path):
                self._queue_directory(path, extra_args, config_override, max_retries)
            else:
                self._queue_file(path, extra_args, config_override, max_retries)

        except Exception as e:
            self.server.logger.exception("Error handling request: %s" % e)
            self.send_json_response(500, {"error": str(e)})
