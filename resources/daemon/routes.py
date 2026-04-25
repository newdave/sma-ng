from urllib.parse import parse_qs, urlparse


def dispatch_get(handler):
  parsed = urlparse(handler.path)
  query = parse_qs(parsed.query)

  if not handler.is_public_endpoint(parsed.path) and not handler.check_auth():
    return

  route = _get_routes().get(parsed.path)
  if route is not None:
    route(handler, parsed.path, query)
    return

  for prefix, route in _get_prefix_routes():
    if parsed.path.startswith(prefix):
      route(handler, parsed.path, query)
      return

  handler.send_json_response(404, {"error": "Not found"})


def dispatch_post(handler):
  parsed = urlparse(handler.path)
  query = parse_qs(parsed.query)

  if not handler.check_auth():
    return

  route = _post_routes().get(parsed.path)
  if route is not None:
    route(handler, parsed.path, query)
    return

  for prefix, route in _post_prefix_routes():
    if parsed.path.startswith(prefix):
      route(handler, parsed.path, query)
      return

  handler.send_json_response(404, {"error": "Not found"})


def dispatch_post_job_action(handler, path):
  if path.endswith("/requeue"):
    handler._post_job_requeue(path)
  elif path.endswith("/cancel"):
    handler._post_job_cancel(path)
  elif path.endswith("/priority"):
    handler._post_job_priority(path)
  else:
    handler.send_json_response(404, {"error": "Not found"})


def _get_routes():
  return {
    "/": lambda handler, path, query: handler._get_root(path, query),
    "/dashboard": lambda handler, path, query: handler._get_dashboard(path, query),
    "/admin": lambda handler, path, query: handler._get_admin(path, query),
    "/docs": lambda handler, path, query: handler._get_docs(path, query),
    "/health": lambda handler, path, query: handler._get_health(),
    "/status": lambda handler, path, query: handler._get_status(),
    "/jobs": lambda handler, path, query: handler._get_jobs(query),
    "/configs": lambda handler, path, query: handler._get_configs(),
    "/stats": lambda handler, path, query: handler._get_stats(path, query),
    "/scan": lambda handler, path, query: handler._get_scan(query),
    "/browse": lambda handler, path, query: handler._get_browse(query),
    "/logs": lambda handler, path, query: handler._get_logs(),
    "/cluster/logs": lambda handler, path, query: handler._get_cluster_logs(path, query),
    "/admin/config": lambda handler, path, query: handler._get_admin_config(path, query),
    "/favicon.png": lambda handler, path, query: handler._get_favicon(path, query),
  }


def _get_prefix_routes():
  return [
    ("/docs/", lambda handler, path, query: handler._get_docs(path, query)),
    ("/jobs/", lambda handler, path, query: handler._get_job(path)),
    ("/logs/", lambda handler, path, query: handler._get_log_content(path, query)),
  ]


def _post_routes():
  return {
    "/": lambda handler, path, query: handler._handle_webhook(),
    "/webhook/generic": lambda handler, path, query: handler._handle_webhook(),
    "/webhook/sonarr": lambda handler, path, query: handler._handle_sonarr_webhook(),
    "/webhook/radarr": lambda handler, path, query: handler._handle_radarr_webhook(),
    "/convert": lambda handler, path, query: handler._handle_webhook(),
    "/admin/delete-failed": lambda handler, path, query: handler._post_admin_delete_failed(),
    "/admin/delete-offline-nodes": lambda handler, path, query: handler._post_admin_delete_offline_nodes(),
    "/admin/delete-all-jobs": lambda handler, path, query: handler._post_admin_delete_all_jobs(),
    "/shutdown": lambda handler, path, query: handler._post_shutdown(path, query),
    "/restart": lambda handler, path, query: handler._post_restart(path, query),
    "/reload": lambda handler, path, query: handler._post_reload(),
    "/cleanup": lambda handler, path, query: handler._post_cleanup(query),
    "/jobs/requeue": lambda handler, path, query: handler._post_jobs_requeue_bulk(query),
    "/scan/filter": lambda handler, path, query: handler._post_scan_filter(),
    "/scan/record": lambda handler, path, query: handler._post_scan_record(),
    "/admin/config": lambda handler, path, query: handler._post_admin_config(path, query),
  }


def _post_prefix_routes():
  return [
    ("/jobs/", lambda handler, path, query: dispatch_post_job_action(handler, path)),
    ("/admin/nodes/", lambda handler, path, query: handler._post_admin_node_action(path)),
  ]
