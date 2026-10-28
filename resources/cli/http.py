"""Minimal HTTP client for the SMA-NG daemon API (stdlib only).

The API key travels in the X-API-Key header and is never printed or logged.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from resources.cli.errors import AuthError, CliError, Unreachable, UsageError


class Conflict(CliError):
  """The daemon refused the job for a reason the user must fix (exit 1)."""


class DaemonClient:
  def __init__(self, url, api_key=None, timeout=10.0, opener=None):
    self.url = url.rstrip("/")
    self._api_key = api_key
    self.timeout = timeout
    self._open = opener or urllib.request.urlopen

  def _request(self, method, path, body=None, timeout=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(self.url + path, data=data, method=method)
    if data is not None:
      req.add_header("Content-Type", "application/json")
    if self._api_key:
      req.add_header("X-API-Key", self._api_key)
    try:
      resp = self._open(req, timeout=timeout or self.timeout)
      with resp:
        return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
      payload = exc.read()
      exc.close()
      return exc.code, dict(exc.headers.items()) if exc.headers else {}, payload
    except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
      reason = getattr(exc, "reason", exc)
      raise Unreachable("sma: cannot reach the daemon at %s: %s" % (self.url, reason)) from None

  def _json(self, method, path, body=None, ok=(200,)):
    status, _headers, payload = self._request(method, path, body)
    try:
      data = json.loads(payload.decode("utf-8")) if payload else {}
    except ValueError:
      data = {}
    if status == 401:
      raise AuthError("sma: the daemon at %s rejected the API key (sma target add NAME URL --api-key-stdin)" % self.url)
    if status in ok:
      return status, data
    message = data.get("error") if isinstance(data, dict) else None
    if status == 409:
      raise Conflict("sma: %s" % (message or "the daemon refused the job"))
    if status in (400, 404):
      raise UsageError("sma: %s" % (message or "request rejected (%d)" % status))
    raise CliError("sma: daemon error %d: %s" % (status, message or "unexpected response"))

  def healthy(self, timeout=1.0):
    try:
      status, _h, _b = self._request("GET", "/health", timeout=timeout)
    except Unreachable:
      return False
    return status == 200

  def submit(self, kind, path, args):
    _status, data = self._json("POST", "/jobs", {"kind": kind, "path": path, "args": list(args)}, ok=(200, 202))
    return data

  def get_job(self, job_id):
    return self._json("GET", "/jobs/%d" % job_id)[1]

  def list_jobs(self, status=None, limit=20):
    query = {"limit": str(limit)}
    if status:
      query["status"] = status
    return self._json("GET", "/jobs?" + urllib.parse.urlencode(query))[1].get("jobs", [])

  def cancel(self, job_id):
    return self._json("POST", "/jobs/%d/cancel" % job_id)[1]

  def get_paths(self):
    """Roots the daemon serves; [] when the daemon predates GET /paths."""
    status, _h, payload = self._request("GET", "/paths")
    if status == 401:
      raise AuthError("sma: the daemon at %s rejected the API key (sma target add NAME URL --api-key-stdin)" % self.url)
    if status != 200:
      return []
    try:
      return list(json.loads(payload.decode("utf-8")).get("roots", []))
    except (ValueError, AttributeError):
      return []

  def get_output(self, job_id, offset=0):
    """Return (text, next_offset) of the job's output from *offset*."""
    status, headers, payload = self._request("GET", "/jobs/%d/output?offset=%d" % (job_id, offset))
    if status == 401:
      raise AuthError("sma: the daemon at %s rejected the API key" % self.url)
    if status != 200:
      return "", offset
    lowered = {k.lower(): v for k, v in headers.items()}
    try:
      next_offset = int(lowered.get("x-next-offset", offset + len(payload)))
    except ValueError:
      next_offset = offset + len(payload)
    return payload.decode("utf-8", errors="replace"), next_offset
