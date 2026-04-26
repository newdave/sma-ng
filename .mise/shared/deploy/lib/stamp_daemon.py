"""Stamp daemon settings, routing rules, and service credentials into
``config/sma-ng.yml`` and ``config/daemon.env``.

Usage::

    python3 stamp_daemon.py <deploy_dir> <api_key_b64> <db_url_b64>
        <ffmpeg_dir_b64> <node_name_b64> <db_user_b64> <db_pw_b64>
        <db_name_b64> <services_b64> [<base_overrides_b64>
        [<profiles_overrides_b64>]]

All credential arguments are base64-encoded to safely handle special
characters; pass an empty string for unused arguments.
``base_overrides`` and ``profiles_overrides`` are JSON-encoded blocks
from ``setup/.local.yml`` (emitted by ``scripts/local-section-json.py``)
and are deep-merged into ``base:`` and ``profiles:`` respectively on
every roll, so per-deployment defaults like ``base.video.gpu`` and
quality-profile overlays like ``profiles.rq.video.crf-profiles``
survive subsequent re-rolls.

The four-bucket sma-ng.yml schema lives under lowercase top-level keys
(``daemon`` / ``base`` / ``profiles`` / ``services``) with kebab-case
field names. ``services_b64`` is the JSON emitted by
``scripts/services-json.py`` — a nested ``{type: {instance: {fields}}}``
mapping that mirrors ``services:`` in sma-ng.yml exactly. We:

* write daemon credentials into ``daemon.api-key`` / ``daemon.db-url`` /
  ``daemon.ffmpeg-dir``;
* rebuild ``daemon.routing`` from every instance that carries both a
  ``path`` and a ``profile`` (longest-match-first so the daemon's
  prefix matcher picks the most specific rule);
* stamp service credentials into ``services.<type>.<instance>``,
  filtering out routing-only metadata (``path``, ``profile``) which
  belongs in routing rules, not the service block.
"""

import base64
import json
import os
import sys

from ruamel.yaml import YAML


def _b64arg(n, default=""):
  if len(sys.argv) > n and sys.argv[n]:
    try:
      return base64.b64decode(sys.argv[n]).decode()
    except Exception:
      return default
  return default


# Keys that describe routing, not service identity — never stamped into
# services.<type>.<instance>.
ROUTING_ONLY_KEYS = {"path", "profile"}

# Boolean fields per service type (used to coerce JSON string values back
# to YAML bools so the schema validator doesn't complain).
BOOLEAN_FIELDS = {
  "sonarr": {"force-rename", "rescan", "in-progress-check", "block-reprocess"},
  "radarr": {"force-rename", "rescan", "in-progress-check", "block-reprocess"},
  "plex": {"refresh", "ignore-certs", "plexmatch"},
}


def _coerce(stype, key, raw):
  if key in BOOLEAN_FIELDS.get(stype, set()):
    return raw.lower() in ("1", "true", "yes", "on")
  return raw


def _kebab(key):
  return key.replace("_", "-")


deploy_dir = sys.argv[1]
api_key = _b64arg(2)
db_url = _b64arg(3)
ffmpeg_dir = _b64arg(4)
node_name = _b64arg(5)
db_user = _b64arg(6)
db_pw = _b64arg(7)
db_name = _b64arg(8)
services = json.loads(base64.b64decode(sys.argv[9]).decode()) if len(sys.argv) > 9 else {}
base_overrides = json.loads(base64.b64decode(sys.argv[10]).decode()) if len(sys.argv) > 10 and sys.argv[10] else {}
profiles_overrides = json.loads(base64.b64decode(sys.argv[11]).decode()) if len(sys.argv) > 11 and sys.argv[11] else {}


def _deep_merge(dst, src, path=""):
  """Shallow-recursive merge of ``src`` into ``dst``. Lists and scalars
  in ``src`` overwrite ``dst``; dicts recurse. Returns the list of
  ``(path, old, new)`` triples for changed leaves so the caller can log."""
  changes = []
  for key, new_val in src.items():
    here = f"{path}.{key}" if path else key
    if isinstance(new_val, dict) and isinstance(dst.get(key), dict):
      changes.extend(_deep_merge(dst[key], new_val, here))
    else:
      old_val = dst.get(key)
      if old_val != new_val:
        changes.append((here, old_val, new_val))
        dst[key] = new_val
  return changes


# ── sma-ng.yml ────────────────────────────────────────────────────────────
yaml_path = os.path.join(deploy_dir, "config", "sma-ng.yml")
if os.path.exists(yaml_path):
  yaml = YAML(typ="rt")
  yaml.width = 120
  with open(yaml_path) as f:
    root = yaml.load(f) or {}

  changed = False

  # base overrides from .local.yml (deep-merge, scalars/lists overwrite)
  if base_overrides:
    base_block = root.setdefault("base", {})
    for path_, old, new in _deep_merge(base_block, base_overrides):
      print(f"  sma-ng.yml base.{path_}: {old!r} -> {new!r}")
      changed = True

  # profiles overrides from .local.yml (same merge semantics)
  if profiles_overrides:
    profiles_block = root.setdefault("profiles", {})
    for path_, old, new in _deep_merge(profiles_block, profiles_overrides):
      print(f"  sma-ng.yml profiles.{path_}: {old!r} -> {new!r}")
      changed = True

  # daemon credentials
  daemon_block = root.setdefault("daemon", {})
  for field, val in (("api-key", api_key), ("db-url", db_url), ("ffmpeg-dir", ffmpeg_dir)):
    if val and daemon_block.get(field) != val:
      print(f"  sma-ng.yml daemon.{field}: {daemon_block.get(field)!r} -> {val!r}")
      daemon_block[field] = val
      changed = True

  # service credentials (skip routing-only keys)
  if services:
    services_block = root.setdefault("services", {})
    for stype, instances in services.items():
      type_block = services_block.setdefault(stype, {})
      for inst_name, fields in instances.items():
        inst_block = type_block.setdefault(inst_name, {})
        for raw_key, raw_val in fields.items():
          if raw_key in ROUTING_ONLY_KEYS:
            continue
          yaml_key = _kebab(raw_key)
          new_val = _coerce(stype, yaml_key, raw_val)
          if inst_block.get(yaml_key) != new_val:
            print(f"  sma-ng.yml services.{stype}.{inst_name}.{yaml_key}: {new_val!r}")
            inst_block[yaml_key] = new_val
            changed = True

  # routing rules built from every instance carrying path + profile,
  # sorted longest-match-first.
  routing_entries = []
  for stype, instances in services.items():
    for inst_name, fields in instances.items():
      path = fields.get("path", "").strip()
      profile = fields.get("profile", "").strip()
      if path and profile:
        routing_entries.append(
          {
            "match": path,
            "profile": profile,
            "services": [f"{stype}.{inst_name}"],
          }
        )
  if routing_entries:
    routing_entries.sort(key=lambda e: len(e["match"]), reverse=True)
    if daemon_block.get("routing") != routing_entries:
      print(f"  sma-ng.yml daemon.routing: rebuilding {len(routing_entries)} rules")
      for e in routing_entries:
        print(f"    {e['match']} -> profile={e['profile']} services={e['services']}")
      daemon_block["routing"] = routing_entries
      changed = True

  if changed:
    with open(yaml_path, "w") as f:
      yaml.dump(root, f)
else:
  print("  WARNING: config/sma-ng.yml not found, skipping daemon YAML stamping")


# ── daemon.env ────────────────────────────────────────────────────────────
env_path = os.path.join(deploy_dir, "config", "daemon.env")
if os.path.exists(env_path):
  env_vars = {
    "SMA_NODE_NAME": node_name,
    "SMA_DAEMON_API_KEY": api_key,
    "SMA_DAEMON_DB_URL": db_url,
    "SMA_DAEMON_DB_USER": db_user,
    "SMA_DAEMON_DB_PASSWORD": db_pw,
    "SMA_DAEMON_DB_NAME": db_name,
    "SMA_DAEMON_FFMPEG_DIR": ffmpeg_dir,
  }
  with open(env_path) as f:
    lines = f.readlines()

  import re

  out = []
  seen = set()
  for line in lines:
    m = re.match(r"^#?\s*((?:SMA_DAEMON_\w+)|SMA_NODE_NAME)\s*=", line)
    if m:
      var = m.group(1)
      val = env_vars.get(var, "")
      if val and var not in seen:
        old = line.rstrip()
        line = f"{var}={val}\n"
        if old != line.rstrip():
          print(f"  daemon.env {var}: updated")
        seen.add(var)
    out.append(line)
  for var, val in env_vars.items():
    if val and var not in seen:
      print(f"  daemon.env {var}: added")
      out.append(f"{var}={val}\n")
  with open(env_path, "w") as f:
    f.writelines(out)
else:
  print("  WARNING: config/daemon.env not found, skipping")
