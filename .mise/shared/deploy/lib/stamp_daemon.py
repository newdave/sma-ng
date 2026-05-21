"""Stamp daemon settings, routing rules, and service credentials into
``config/sma-ng.yml`` and ``config/daemon.env``.

Usage::

    python3 stamp_daemon.py <deploy_dir> <api_key_b64> <db_url_b64>
        <ffmpeg_dir_b64> <node_name_b64> <db_user_b64> <db_pw_b64>
        <db_name_b64> <services_b64> [<base_overrides_b64>
        [<profiles_overrides_b64> [<workers_b64>]]]

``workers_b64`` is the resolved per-host or deploy-wide worker count
from setup/local.yml (``hosts.<label>.workers`` overrides
``deploy.workers``). When non-empty and parseable as a positive
integer, it is written to ``daemon.workers`` in sma-ng.yml so the
daemon picks it up without requiring a CLI flag or env var.

All credential arguments are base64-encoded to safely handle special
characters; pass an empty string for unused arguments.
``base_overrides`` and ``profiles_overrides`` are JSON-encoded blocks
from ``setup/local.yml`` (emitted by ``scripts/local-section-json.py``)
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
  "autoscan": {"ignore-certs", "enabled"},
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
workers_raw = _b64arg(12)
daemon_overrides = json.loads(base64.b64decode(sys.argv[13]).decode()) if len(sys.argv) > 13 and sys.argv[13] else {}


def _parse_positive_int(raw):
  """Return *raw* as an int when it parses to a positive integer, else None."""
  if not raw or not raw.strip():
    return None
  try:
    val = int(raw.strip())
  except (TypeError, ValueError):
    return None
  return val if val > 0 else None


workers = _parse_positive_int(workers_raw)


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
  yaml.allow_duplicate_keys = True
  # Use the dedup-aware loader so any pre-existing duplicate top-level
  # keys are merged before we modify and write back; otherwise we'd
  # update the first copy and leave duplicate keys in place.
  sys.path.insert(0, deploy_dir)
  from resources.yamlconfig import _load_with_dedup

  root = _load_with_dedup(yaml_path) or {}

  changed = False

  # base overrides from local.yml (deep-merge, scalars/lists overwrite)
  if base_overrides:
    base_block = root.setdefault("base", {})
    for path_, old, new in _deep_merge(base_block, base_overrides):
      print(f"  sma-ng.yml base.{path_}: {old!r} -> {new!r}")
      changed = True

  # profiles overrides from local.yml — local.yml is *authoritative*:
  # any profile present in the stamped file but absent from local.yml
  # is reaped, and any profile present in local.yml replaces the
  # deployed profile wholesale. This prevents the upstream sample's
  # placeholder profile fields (e.g. profiles.rq.video.max-bitrate: 8000)
  # from sticking around when the operator's local.yml.profiles.rq
  # doesn't override them and intends to inherit from base. Mirrors
  # the authoritative-mode behaviour ConfigLoader/show-config already
  # apply on the synthesize path (commit b291338).
  #
  # Per-field merge *within* a profile section is still a deep-merge so
  # operator-only fields under a profile section that the sample doesn't
  # carry are preserved.
  if profiles_overrides:
    profiles_block = root.setdefault("profiles", {})
    existing_profiles = set(profiles_block.keys()) if isinstance(profiles_block, dict) else set()
    incoming_profiles = set(profiles_overrides.keys())
    for stale_profile in sorted(existing_profiles - incoming_profiles):
      print(f"  sma-ng.yml profiles.{stale_profile}: removing (not in local.yml)")
      del profiles_block[stale_profile]
      changed = True
    # For profiles present in BOTH the stamped file and local.yml,
    # replace the profile wholesale rather than deep-merging. The
    # local.yml form is the operator's complete intent for that profile.
    for prof_name, prof_data in profiles_overrides.items():
      if prof_name in profiles_block and profiles_block.get(prof_name) != prof_data:
        print(f"  sma-ng.yml profiles.{prof_name}: replacing (local.yml is authoritative)")
        profiles_block[prof_name] = prof_data
        changed = True
      elif prof_name not in profiles_block:
        print(f"  sma-ng.yml profiles.{prof_name}: adding from local.yml")
        profiles_block[prof_name] = prof_data
        changed = True

  # daemon overrides from local.yml (deep-merge before credential
  # stamping so the credentials/workers blocks below still win, and so
  # routing is rebuilt from services further down). Carries through
  # arbitrary daemon-section keys like path-rewrites, strict-routing,
  # config-watch, scan-paths, etc. that don't have dedicated stamping.
  daemon_block = root.setdefault("daemon", {})
  if daemon_overrides:
    # Routing is rebuilt below from services — don't let local.yml's
    # daemon.routing overwrite that. Same for credential fields we
    # explicitly stamp from local.yml.daemon below.
    filtered = {k: v for k, v in daemon_overrides.items() if k not in ("routing",)}
    for path_, old, new in _deep_merge(daemon_block, filtered):
      print(f"  sma-ng.yml daemon.{path_}: {old!r} -> {new!r}")
      changed = True

  # daemon credentials + cluster identity. node-id pins the stable
  # cluster identifier to the host alias from setup/local.yml so the
  # cluster_nodes row matches across redeploys.
  for field, val in (
    ("api-key", api_key),
    ("db-url", db_url),
    ("ffmpeg-dir", ffmpeg_dir),
    ("node-id", node_name),
  ):
    if val and daemon_block.get(field) != val:
      print(f"  sma-ng.yml daemon.{field}: {daemon_block.get(field)!r} -> {val!r}")
      daemon_block[field] = val
      changed = True

  # daemon.workers — global default from setup/local.yml.deploy.workers,
  # overridable per host via setup/local.yml.hosts.<label>.workers. The
  # roll script resolves the right value (host > deploy fallback) before
  # invoking the stamper.
  if workers is not None and daemon_block.get("workers") != workers:
    print(f"  sma-ng.yml daemon.workers: {daemon_block.get('workers')!r} -> {workers!r}")
    daemon_block["workers"] = workers
    changed = True

  # service credentials (skip routing-only keys).
  #
  # `services.<type>` is *authoritative* against setup/local.yml: any
  # instance present in the deployed config but absent from local.yml
  # is reaped here before the merge. This prevents stale instances
  # (sample-seeded or left over from earlier configs) from masking the
  # selection logic in resources/readsettings.py (e.g. a ghost
  # `services.plex.main` with an empty token would hijack the
  # `get("main") or first` selector and silently break Plex refresh
  # for the operator's real `plex.davetv` instance). Per-field merge
  # within an instance remains additive — sample-defaulted fields like
  # `plexmatch` survive when local.yml omits them.
  if services:
    services_block = root.setdefault("services", {})
    for stype, instances in services.items():
      type_block = services_block.setdefault(stype, {})
      existing_insts = set(type_block.keys())
      incoming_insts = set(instances.keys())
      for stale_inst in sorted(existing_insts - incoming_insts):
        print(f"  sma-ng.yml services.{stype}.{stale_inst}: removing (not in local.yml)")
        del type_block[stale_inst]
        changed = True
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
  #
  # Fan-out services: any instance whose type is in FANOUT_TYPES that
  # carries no path/profile is considered "global" — its ref is appended
  # to every routing rule's services list so it fires alongside the
  # per-path service. Currently used for autoscan, where one Autoscan
  # daemon typically fans library scans out across every managed path.
  # Plex/Jellyfin/Emby refresh paths are also fan-out: one server
  # serves every managed path, so their refs append to every routing
  # rule rather than carrying their own path/profile.
  FANOUT_TYPES = {"autoscan", "plex", "jellyfin", "emby"}
  routing_entries = []
  fanout_refs = []
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
      elif stype in FANOUT_TYPES and not path and not profile:
        fanout_refs.append(f"{stype}.{inst_name}")
  if fanout_refs:
    for entry in routing_entries:
      entry["services"].extend(fanout_refs)
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


# daemon.env is intentionally not stamped with runtime overrides. Daemon runtime
# settings live in config/sma-ng.yml.
