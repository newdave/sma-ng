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


_SERVICE_RE = re.compile(r"^(Sonarr|Radarr|Plex|Jellyfin|Emby|Converter)", re.IGNORECASE)


def _str(v):
  if v is None:
    return ""
  if isinstance(v, bool):
    return "true" if v else "false"
  return str(v)


def resolve(data, section, key, default):
  if section == "daemon":
    return _str(data.get("daemon", {}).get(key)) or default
  if _SERVICE_RE.match(section):
    return _str((data.get("services") or {}).get(section, {}).get(key)) or default
  if section == "deploy":
    return _str((data.get("deploy") or {}).get(key)) or default
  # Host-specific: host override > deploy default
  deploy_val = _str((data.get("deploy") or {}).get(key))
  host_val = _str((data.get("hosts") or {}).get(section, {}).get(key))
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
