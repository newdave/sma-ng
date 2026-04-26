#!/usr/bin/env python3
"""scripts/local-config.py <file> <section> <key> [default]

Read a key from setup/local.yml.

Sections resolve as:

  daemon          → daemon[key]
  deploy          → deploy[key]
  sonarr|radarr|plex|jellyfin|emby
                  → services[type][instance][key]
                    (instance defaults to ``main``; falls back to the
                    first instance defined)
  <anything else> → host-specific lookup: hosts[section][key]
                    falling back to deploy[key]

The instance fallback exists so callers that just want "the Plex token"
or "the Sonarr API key" continue to work without naming an instance.
For multi-instance lookups, pass ``<type>.<instance>`` as the section
(e.g. ``sonarr.kids``).
"""

import re
import sys

try:
  from ruamel.yaml import YAML as _RuamelYAML

  def _load(path):
    y = _RuamelYAML()
    # Tolerate duplicate keys (later-wins) so manually-edited configs with
    # accidental dup top-level sections still resolve, matching PyYAML.
    y.allow_duplicate_keys = True
    with open(path) as f:
      return y.load(f) or {}

except ImportError:
  import yaml

  def _load(path):
    with open(path) as f:
      return yaml.safe_load(f) or {}


KNOWN_SERVICE_TYPES = ("sonarr", "radarr", "plex", "jellyfin", "emby")
_SERVICE_RE = re.compile(rf"^({'|'.join(KNOWN_SERVICE_TYPES)})(\.(.+))?$", re.IGNORECASE)


def _str(v):
  if v is None:
    return ""
  if isinstance(v, bool):
    return "true" if v else "false"
  if isinstance(v, list):
    return " ".join(str(x) for x in v)
  return str(v)


def _iget(d, key):
  """Case-insensitive dict get."""
  if not isinstance(d, dict):
    return None
  if key in d:
    return d[key]
  for variant in (key.lower(), key.upper()):
    if variant in d:
      return d[variant]
  return None


def _resolve_service(services, stype, instance, key):
  type_block = _iget(services, stype)
  if not isinstance(type_block, dict):
    return ""
  if instance:
    inst = _iget(type_block, instance)
    return _str(_iget(inst, key)) if isinstance(inst, dict) else ""
  # No instance specified — prefer "main", else first defined.
  ordered = []
  if "main" in type_block:
    ordered.append("main")
  ordered.extend(name for name in type_block if name not in ordered)
  for name in ordered:
    inst = type_block.get(name)
    if isinstance(inst, dict):
      val = _iget(inst, key)
      if val not in (None, ""):
        return _str(val)
  return ""


def resolve(data, section, key, default):
  if section == "daemon":
    return _str(_iget(data.get("daemon", {}), key)) or default
  service_match = _SERVICE_RE.match(section)
  if service_match:
    stype = service_match.group(1).lower()
    instance = service_match.group(3)
    services = data.get("services") or {}
    return _resolve_service(services, stype, instance, key) or default
  if section == "deploy":
    return _str(_iget(data.get("deploy") or {}, key)) or default
  # Host-specific: host override > deploy default
  deploy_val = _str(_iget(data.get("deploy") or {}, key))
  host_val = _str(_iget((data.get("hosts") or {}).get(section, {}), key))
  if host_val:
    return host_val
  if deploy_val:
    return deploy_val
  return default


if __name__ == "__main__":
  if len(sys.argv) < 4:
    print(f"Usage: {sys.argv[0]} <file> <section> <key> [default]", file=sys.stderr)
    sys.exit(1)

  file_path = sys.argv[1]
  section = sys.argv[2]
  key = sys.argv[3]
  default = sys.argv[4] if len(sys.argv) > 4 else ""

  try:
    data = _load(file_path)
  except FileNotFoundError:
    print(default, end="")
    sys.exit(0)

  print(resolve(data, section, key, default), end="")
