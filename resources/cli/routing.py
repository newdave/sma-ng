"""Where a command runs: a daemon target over HTTP, or a throwaway container."""

from __future__ import annotations

import posixpath
import urllib.parse
from dataclasses import dataclass

from resources.cli.clientconfig import LOCAL_TARGET, LOCAL_URL, Target
from resources.cli.errors import Unreachable, UsageError
from resources.cli.http import DaemonClient

DOCKER_DESKTOP_HOST = "host.docker.internal"


@dataclass(frozen=True)
class Resolved:
  target: Target
  explicit: bool  # chosen with --target, SMA_TARGET or default-target


def host_url(url, facts):
  """Docker Desktop has no host network: loopback URLs go through host.docker.internal."""
  if facts.os != "Darwin":
    return url
  parts = urllib.parse.urlsplit(url)
  if parts.hostname in ("127.0.0.1", "localhost", "::1"):
    netloc = DOCKER_DESKTOP_HOST + (":%d" % parts.port if parts.port else "")
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc))
  return url


def _local_url(cfg):
  """The local daemon's URL, on the port `sma daemon up --port` saved (default 8585)."""
  port = (getattr(cfg, "daemon", None) or {}).get("port")
  return "http://127.0.0.1:%d" % int(port) if port else LOCAL_URL


def resolve_target(cfg, flag=None, env_target=None):
  name = flag or env_target or cfg.default_target
  if not name:
    return Resolved(cfg.targets.get(LOCAL_TARGET) or Target(LOCAL_TARGET, _local_url(cfg)), explicit=False)
  if name in cfg.targets:
    return Resolved(cfg.targets[name], explicit=True)
  if name == LOCAL_TARGET:
    return Resolved(Target(LOCAL_TARGET, _local_url(cfg)), explicit=True)
  raise UsageError("sma: unknown target %r (sma target list)" % name)


def client_for(target, facts, timeout=10.0):
  return DaemonClient(host_url(target.url, facts), target.api_key, timeout=timeout)


def map_path(path, mappings):
  """Rewrite *path* through the longest matching local prefix; unchanged when none match."""
  best = None
  for local, remote in mappings:
    local_n = posixpath.normpath(local)
    if path == local_n or path.startswith(local_n.rstrip("/") + "/"):
      if best is None or len(local_n) > len(best[0]):
        best = (local_n, posixpath.normpath(remote))
  if best is None:
    return path
  local_n, remote = best
  rest = path[len(local_n) :].lstrip("/")
  return posixpath.join(remote, rest) if rest else remote


def under_roots(path, roots):
  """True when *path* is a root or below one; an empty list means the daemon did not say."""
  if not roots:
    return True
  for root in roots:
    base = root.rstrip("/")
    if not base or path == base or path.startswith(base + "/"):
      return True
  return False


def decide(resolved, facts, local=False, warn=None, client=None):
  """Return a DaemonClient to submit to, or None to run in a throwaway container."""
  client = client or client_for(resolved.target, facts)
  if local:
    if warn is not None and client.healthy():
      warn("sma: a daemon is running at %s; --local runs alongside it and competes for the GPU and files" % resolved.target.url)
    return None
  if client.healthy():
    return client
  if resolved.explicit:
    raise Unreachable("sma: target %s (%s) is not reachable" % (resolved.target.name, resolved.target.url))
  return None
