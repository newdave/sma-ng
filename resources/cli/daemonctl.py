"""`sma daemon up|down|restart|status|logs|upgrade`: the long-running daemon container.

A daemon started by Docker Compose (it carries compose labels) is driven
through its own compose project. Otherwise sma manages a plain container
named sma-ng: ``docker run -d --restart unless-stopped`` with the host data
directory and the media roots mounted at the same paths. The container keeps
the image's default user, because the entrypoint starts as root to fix GPU
groups before dropping privileges.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from resources.cli import clientconfig, routing
from resources.cli.errors import Unreachable, UsageError
from resources.cli.plan import Plan

DEFAULT_NAME = "sma-ng"
DEFAULT_PORT = 8585
HEALTH_TIMEOUT = 60
VERBS = ("up", "down", "restart", "status", "logs", "upgrade")
OPTIONAL_ROOTS = ("/transcode", "/recycle")


def compose_info(container):
  """(project, working_dir, [config files], service) for a compose-managed container, else None."""
  if not container:
    return None
  labels = (container.get("Config") or {}).get("Labels") or {}
  project = labels.get("com.docker.compose.project")
  service = labels.get("com.docker.compose.service")
  if not project or not service:
    return None
  files = [f for f in (labels.get("com.docker.compose.project.config_files") or "").split(",") if f]
  return project, labels.get("com.docker.compose.project.working_dir") or "", files, service


def _compose(info, *verb):
  project, workdir, files, service = info
  args = ["-p", project]
  if workdir:
    args += ["--project-directory", workdir]
  for f in files:
    args += ["-f", f]
  return args + list(verb) + [service]


def _parse(args):
  opts = {"media": [], "port": None}
  i = 0
  while i < len(args):
    arg = args[i]
    if arg in ("--media", "--port"):
      if i + 1 >= len(args):
        raise UsageError("sma: %s needs a value" % arg)
      if arg == "--media":
        opts["media"].append(i + 1)
      else:
        try:
          opts["port"] = int(args[i + 1])
        except ValueError:
          raise UsageError("sma: --port needs a number") from None
      i += 2
      continue
    raise UsageError("sma: unknown daemon option %s" % arg)
  return opts


def _z(facts):
  return ":z" if facts.selinux else ""


def _plain_up(plan, facts, name, port, roots_from_args, saved_roots):
  if not facts.config_present:
    raise UsageError("sma: %s/config does not exist; run the installer first" % facts.sma_home)
  if facts.daemon_container is not None:
    plan.step("rm", "-f", name)
  step = plan.step("run", "-d", "--name", name, "--restart", "unless-stopped", "-p", "%d:8585" % port)
  for sub in ("config", "logs", "data"):
    step.arg("-v", "%s/%s:/%s%s" % (facts.sma_home, sub, sub, _z(facts)))
  for root in roots_from_args:
    step.arg("-v", "%s:%s%s" % (root, root, _z(facts)))
  for root in saved_roots:
    if root not in roots_from_args:
      step.optmount(root)
  for root in OPTIONAL_ROOTS:
    if root not in roots_from_args and root not in saved_roots:
      step.optmount(root)
  if facts.dri_gids:
    step.arg("--device", "/dev/dri")
    for gid in facts.dri_gids:
      step.arg("--group-add", str(gid))
  if facts.nvidia:
    step.arg("--gpus", "all")
  step.arg(facts.image)
  plan.step("exec", name, "python", "-m", "resources.cli", "_wait-health", "http://127.0.0.1:8585/health", str(HEALTH_TIMEOUT))


def build(verb, args, first_index, facts, cfg, env):
  """Plan for *verb*; returns None for status (handled without docker)."""
  if verb not in VERBS:
    raise UsageError("sma: unknown daemon command %r (%s)" % (verb, ", ".join(VERBS)))
  opts = _parse(args)
  name = env.get("SMA_DAEMON_NAME") or DEFAULT_NAME
  info = compose_info(facts.daemon_container)
  plan = Plan()
  if info is not None:
    if opts["media"] or opts["port"]:
      raise UsageError("sma: %s is managed by Docker Compose; change mounts and ports in its compose file" % name)
    verbs = {"up": [("up", "-d")], "down": [("stop",)], "restart": [("restart",)], "logs": [("logs", "-f", "--tail", "200")], "upgrade": [("pull",), ("up", "-d")]}
    for verb_args in verbs[verb]:
      plan.step("compose", *_compose(info, *verb_args))
    return plan

  settings = cfg.daemon
  if verb in ("down", "restart", "logs") and facts.daemon_container is None:
    raise UsageError("sma: no %s container on this host (sma daemon up --media PATH)" % name)
  if verb == "down":
    plan.step("stop", name)
    plan.step("rm", name)
    return plan
  if verb == "restart":
    plan.step("restart", name)
    return plan
  if verb == "logs":
    plan.step("logs", "-f", "--tail", "200", name)
    return plan

  from_args = []
  for index in opts["media"]:
    stat = facts.stat_for(first_index + index)
    if stat.kind != "d":
      raise UsageError("sma: --media %s: not a directory on this host" % stat.abspath)
    if stat.abspath == "/" or ":" in stat.abspath:
      raise UsageError("sma: --media %s cannot be mounted" % stat.abspath)
    from_args.append(stat.abspath)
  saved = [r for r in settings.get("media-roots") or [] if isinstance(r, str)]
  if not from_args and not saved:
    raise UsageError("sma: sma daemon up needs the media it serves: sma daemon up --media /path/to/media")
  port = opts["port"] or int(settings.get("port") or DEFAULT_PORT)
  if from_args or opts["port"]:
    cfg.daemon = dict(settings, **({"media-roots": sorted(set(saved) | set(from_args))} if from_args else {}), **({"port": port} if opts["port"] else {}))
    clientconfig.save(cfg, env)
  if verb == "upgrade":
    plan.step("pull", facts.image)
  _plain_up(plan, facts, name, port, from_args, saved)
  return plan


def status(facts, cfg, env, out, as_json=False):
  """Container state plus the daemon's /health; exit 0 when it answers, else 3."""
  name = env.get("SMA_DAEMON_NAME") or DEFAULT_NAME
  container = facts.daemon_container
  info = compose_info(container)
  state = ((container or {}).get("State") or {}).get("Status") if container else None
  local = routing.resolve_target(cfg, clientconfig.LOCAL_TARGET).target
  client = routing.client_for(local, facts, timeout=2.0)
  healthy = client.healthy(timeout=2.0)
  result = {
    "container": name if container else None,
    "state": state,
    "managed_by": "compose" if info else ("sma" if container else None),
    "compose_project": info[0] if info else None,
    "compose_service": info[3] if info else None,
    "url": local.url,
    "healthy": healthy,
  }
  if as_json:
    out.write(json.dumps(result) + "\n")
  else:
    if container:
      how = "compose project %s, service %s" % (info[0], info[3]) if info else "managed by sma"
      out.write("container: %s %s (%s)\n" % (name, state or "unknown", how))
    else:
      out.write("container: none (sma daemon up --media PATH)\n")
    out.write("daemon:    %s at %s\n" % ("answering" if healthy else "not answering", local.url))
  return 0 if healthy else 3


def wait_health(url, seconds, opener=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic):
  """Poll *url* until it answers 200 or *seconds* pass (run inside the daemon container)."""
  deadline = clock() + seconds
  while True:
    try:
      with opener(url, timeout=2) as resp:
        if resp.status == 200:
          return 0
    except (urllib.error.URLError, OSError):
      pass
    if clock() >= deadline:
      raise Unreachable("sma: the daemon did not become healthy within %ds; see sma daemon logs" % seconds)
    sleep(1)
