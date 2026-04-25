#!/usr/bin/env python3
"""scripts/local-config.py <file> <section> <key> [default]

Read a key from setup/.local.yml.

Values are resolved in two passes:
  1. deploy section — provides project-wide defaults
  2. hosts[section] — host-specific overrides win over deploy

The daemon section is looked up directly under daemon[key].
Service sections (Sonarr*, Radarr*, Plex, Jellyfin, Emby, Converter)
are looked up under services[section][key].
"""

import re
import sys

try:
  from ruamel.yaml import YAML as _RuamelYAML

  def _load(path):
    y = _RuamelYAML()
    with open(path) as f:
      return y.load(f) or {}

except ImportError:
  import yaml

  def _load(path):
    with open(path) as f:
      return yaml.safe_load(f) or {}


_SERVICE_RE = re.compile(r"^(sonarr|radarr|plex|jellyfin|emby|converter)", re.IGNORECASE)


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
  return d.get(key) if key in d else d.get(key.upper()) if key.upper() in d else d.get(key.lower())


def resolve(data, section, key, default):
  if section == "daemon":
    return _str(_iget(data.get("daemon", {}), key)) or default
  if _SERVICE_RE.match(section):
    services = data.get("services") or {}
    val = _iget(services.get(section) or services.get(section.lower()) or {}, key)
    return _str(val) or default
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
