"""Submit commands to a daemon over HTTP and follow the resulting jobs."""

from __future__ import annotations

import json
import time

from resources.cli import routing, throwaway
from resources.cli.errors import UsageError

KINDS = {"convert": "convert", "preview": "preview", "repair": "repair", "audit": "audit", "rename": "rename"}
TERMINAL = {"completed", "failed", "cancelled"}
POLL_SECONDS = 1.0


def split(command, args, first_index, facts):
  """Return ([abspath, ...], tool_args) using the same path rules as throwaway runs."""
  spec = throwaway.SPECS[command]
  paths, rest, leading = [], [], True
  for offset, arg in enumerate(args):
    if arg in throwaway.FORBIDDEN_TOOL_ARGS or arg.split("=", 1)[0] in throwaway.FORBIDDEN_TOOL_ARGS:
      raise UsageError("sma: %s is set by sma and cannot be passed to %s" % (arg, command))
    if not arg.startswith("-") and (spec.all_positional_paths or leading):
      paths.append(facts.stat_for(first_index + offset).abspath)
    else:
      leading = False
      rest.append(arg)
  if not paths:
    raise UsageError("sma: %s needs a PATH (see 'sma help')" % command)
  if len(paths) > 1 and not spec.multi:
    raise UsageError("sma: %s takes one PATH" % command)
  return paths, rest


def _job_ids(response):
  """Job ids from a POST /jobs response (one job, or a directory of conversions)."""
  if isinstance(response.get("job_id"), int):
    return [response["job_id"]]
  ids = []
  for key in ("queued", "duplicates"):
    for item in response.get(key) or []:
      if isinstance(item, dict) and isinstance(item.get("job_id"), int):
        ids.append(item["job_id"])
  return ids


def submit(command, args, first_index, facts, resolved, client, err, quiet=False):
  """Map, validate and submit every path; return [(job_id, path)]."""
  paths, tool_args = split(command, args, first_index, facts)
  target = resolved.target
  mapped = [routing.map_path(p, target.mappings) for p in paths]
  roots = client.get_paths()
  for original, path in zip(paths, mapped):
    if not routing.under_roots(path, roots):
      raise UsageError("sma: %s is not under any path target %s serves (%s)" % (path if path == original else "%s (%s)" % (original, path), target.name, ", ".join(roots)))
  jobs = []
  for path in mapped:
    response = client.submit(KINDS[command], path, tool_args)
    status = response.get("status")
    ids = _job_ids(response)
    if status in ("empty", "skipped") or not ids:
      err.write("sma: %s: %s\n" % (path, response.get("message") or response.get("reason") or status or "nothing queued"))
      continue
    if not quiet:
      word = "already queued as" if status == "duplicate" else "queued"
      err.write("sma: %s %s job%s %s on %s\n" % (path, word, "s" if len(ids) > 1 else "", ", ".join(str(i) for i in ids), target.name))
    jobs.extend((job_id, path) for job_id in ids)
  return jobs


def follow(client, job_id, out, sleep=time.sleep, stream=True):
  """Stream a job's output until it finishes; return the final job dict."""
  offset = 0
  while True:
    job = client.get_job(job_id)
    text, offset = client.get_output(job_id, offset)
    if stream and text:
      out.write(text)
      out.flush()
    if job.get("status") in TERMINAL:
      return job
    sleep(POLL_SECONDS)


def run(command, args, first_index, facts, resolved, client, out, err, wait=True, as_json=False, quiet=False, sleep=time.sleep):
  jobs = submit(command, args, first_index, facts, resolved, client, err, quiet=quiet or as_json)
  if not wait:
    for job_id, path in jobs:
      if as_json:
        out.write(json.dumps({"job_id": job_id, "kind": KINDS[command], "path": path, "status": "queued"}) + "\n")
      else:
        out.write("%d\n" % job_id)
    return 0
  rc = 0
  current = None
  try:
    for job_id, path in jobs:
      current = job_id
      job = follow(client, job_id, out, sleep=sleep, stream=not as_json)
      status = job.get("status")
      if status != "completed":
        rc = 1
        if not as_json:
          err.write("sma: job %d %s: %s\n" % (job_id, status, job.get("error") or path))
      if as_json:
        out.write(json.dumps({"job_id": job_id, "kind": KINDS[command], "path": path, "status": status, "error": job.get("error")}) + "\n")
  except KeyboardInterrupt:
    err.write("\nsma: detached: job %d (sma jobs show %d)\n" % (current, current))
    return 130
  return rc
