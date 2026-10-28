"""`sma jobs`, `sma target` and `sma map`: daemon jobs and client settings."""

from __future__ import annotations

import json
import time

from resources.cli import clientconfig, remote, routing
from resources.cli.clientconfig import Target
from resources.cli.errors import Unreachable, UsageError


def _job_id(value):
  try:
    return int(value)
  except (TypeError, ValueError):
    raise UsageError("sma: job id must be a number, got %r" % value) from None


def jobs(args, cfg, facts, opts, out, err, sleep=time.sleep):
  """sma jobs [list [--status S]] | show ID | logs ID [-f] | cancel ID"""
  resolved = routing.resolve_target(cfg, opts.get("target"), opts.get("env_target"))
  client = routing.client_for(resolved.target, facts)
  if not client.healthy():
    raise Unreachable("sma: no daemon at %s (target %s)" % (resolved.target.url, resolved.target.name))
  sub = args[0] if args else "list"
  rest = args[1:]
  as_json = opts.get("json")
  if sub == "list":
    status = None
    if rest[:1] == ["--status"] and len(rest) > 1:
      status = rest[1]
    rows = client.list_jobs(status=status)
    if as_json:
      out.write(json.dumps(rows) + "\n")
      return 0
    for job in rows:
      out.write("%-7s %-8s %-10s %s\n" % (job.get("id"), job.get("kind") or "convert", job.get("status"), job.get("path")))
    return 0
  if sub in ("show", "logs", "cancel"):
    if not rest:
      raise UsageError("sma: jobs %s needs a job id" % sub)
    job_id = _job_id(rest[0])
    if sub == "show":
      job = client.get_job(job_id)
      if as_json:
        out.write(json.dumps(job) + "\n")
        return 0
      for key in ("id", "kind", "status", "path", "config", "node_id", "created_at", "started_at", "completed_at", "error"):
        if job.get(key) not in (None, ""):
          out.write("%-13s %s\n" % (key + ":", job[key]))
      return 0
    if sub == "logs":
      if "-f" in rest[1:] or "--follow" in rest[1:]:
        job = remote.follow(client, job_id, out, sleep=sleep)
        return 0 if job.get("status") == "completed" else 1
      text, _ = client.get_output(job_id, 0)
      out.write(text)
      return 0
    client.cancel(job_id)
    err.write("sma: cancelled job %d\n" % job_id)
    return 0
  raise UsageError("sma: unknown jobs command %r (list, show, logs, cancel)" % sub)


def target(args, cfg, env, out, err, stdin=None):
  """sma target add NAME URL [--api-key-stdin] | list | use NAME | rm NAME"""
  sub = args[0] if args else "list"
  rest = args[1:]
  if sub == "list":
    default = cfg.default_target or clientconfig.LOCAL_TARGET
    names = sorted(set(cfg.targets) | {clientconfig.LOCAL_TARGET})
    for name in names:
      t = cfg.targets.get(name) or Target(name, clientconfig.LOCAL_URL)
      flags = []
      if t.api_key:
        flags.append("api key set")
      if t.mappings:
        flags.append("%d mapping%s" % (len(t.mappings), "s" if len(t.mappings) != 1 else ""))
      out.write("%s %-12s %s%s\n" % ("*" if name == default else " ", name, t.url, ("  (" + ", ".join(flags) + ")") if flags else ""))
    return 0
  if sub == "add":
    unknown = [a for a in rest if a.startswith("--") and a != "--api-key-stdin"]
    if unknown:
      raise UsageError("sma: unknown option %s (API keys are read with --api-key-stdin, never from the command line)" % unknown[0])
    positional = [a for a in rest if not a.startswith("--")]
    if len(positional) != 2:
      raise UsageError("sma: target add NAME URL [--api-key-stdin]")
    name, url = positional
    if not url.startswith(("http://", "https://")):
      raise UsageError("sma: target URL must start with http:// or https://")
    key = None
    if "--api-key-stdin" in rest:
      key = (stdin.readline() if stdin is not None else "").strip()
      if not key:
        raise UsageError("sma: --api-key-stdin read no key from stdin")
    existing = cfg.targets.get(name)
    cfg.targets[name] = Target(name, url.rstrip("/"), key or (existing.api_key if existing else None), existing.mappings if existing else [])
    clientconfig.save(cfg, env)
    err.write("sma: target %s -> %s%s\n" % (name, url, " (api key stored)" if key else ""))
    return 0
  if sub == "use":
    if len(rest) != 1:
      raise UsageError("sma: target use NAME")
    if rest[0] not in cfg.targets and rest[0] != clientconfig.LOCAL_TARGET:
      raise UsageError("sma: unknown target %r (sma target list)" % rest[0])
    cfg.default_target = None if rest[0] == clientconfig.LOCAL_TARGET else rest[0]
    clientconfig.save(cfg, env)
    return 0
  if sub == "rm":
    if len(rest) != 1 or rest[0] not in cfg.targets:
      raise UsageError("sma: target rm NAME (an existing target)")
    del cfg.targets[rest[0]]
    if cfg.default_target == rest[0]:
      cfg.default_target = None
    clientconfig.save(cfg, env)
    return 0
  raise UsageError("sma: unknown target command %r (add, list, use, rm)" % sub)


def mapping(args, cfg, env, out, err):
  """sma map add TARGET LOCAL REMOTE | list [TARGET] | rm TARGET LOCAL"""
  sub = args[0] if args else "list"
  rest = args[1:]
  if sub == "list":
    for name, t in sorted(cfg.targets.items()):
      if rest and name != rest[0]:
        continue
      for local, remote_path in t.mappings:
        out.write("%-12s %s -> %s\n" % (name, local, remote_path))
    return 0
  if sub in ("add", "rm"):
    need = 3 if sub == "add" else 2
    if len(rest) != need:
      raise UsageError("sma: map add TARGET LOCAL REMOTE" if sub == "add" else "sma: map rm TARGET LOCAL")
    t = cfg.targets.get(rest[0])
    if t is None:
      raise UsageError("sma: unknown target %r; add it first with sma target add" % rest[0])
    for p in rest[1:]:
      if not p.startswith("/"):
        raise UsageError("sma: mapping paths must be absolute: %s" % p)
    local = rest[1].rstrip("/") or "/"
    t.mappings = [(lp, rp) for lp, rp in t.mappings if lp.rstrip("/") != local]
    if sub == "add":
      t.mappings.append((local, rest[2].rstrip("/") or "/"))
    clientconfig.save(cfg, env)
    return 0
  raise UsageError("sma: unknown map command %r (add, list, rm)" % sub)
