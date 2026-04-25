#!/usr/bin/env python3
"""scripts/services-json.py <local-yml>

Read all service entries (Sonarr*, Radarr*, Plex*, Converter) from setup/.local.yml
and print a JSON object mapping section name -> {key: value, ...}.
Only sections whose names match the service pattern are included.
Only non-empty values are emitted (blank values are skipped).
"""

import json
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


SERVICE_PATTERN = re.compile(r"^(Sonarr|Radarr|Plex|Converter)", re.IGNORECASE)

path = sys.argv[1] if len(sys.argv) > 1 else "setup/.local.yml"

services = {}
try:
  data = _load(path)
  for name, keys in (data.get("services") or {}).items():
    if not SERVICE_PATTERN.match(name):
      continue
    if not isinstance(keys, dict):
      continue

    def _sv(v):
      if isinstance(v, bool):
        return "true" if v else "false"
      return str(v)

    entry = {k: _sv(v) for k, v in keys.items() if v is not None and str(v)}
    if entry:
      services[name] = entry
except FileNotFoundError:
  pass

print(json.dumps(services))
