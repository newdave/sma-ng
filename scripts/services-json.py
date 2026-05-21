#!/usr/bin/env python3
"""scripts/services-json.py <local-yml>

Read the nested ``services`` block from setup/local.yml and print it as
JSON, mirroring the four-bucket sma-ng.yml schema:

    services:
      sonarr:
        main:
          url: ...
          apikey: ...
          path: ...
          profile: rq

becomes::

    {"sonarr": {"main": {"url": "...", "apikey": "...",
                          "path": "...", "profile": "rq"}}, ...}

Only known service types are emitted (sonarr, radarr, plex, jellyfin, emby).
Empty instances are dropped so downstream stampers can rely on truthiness
checks. Booleans are stringified ("true"/"false") to keep JSON consumers
that expect string values working.
"""

import json
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


KNOWN_TYPES = ("sonarr", "radarr", "plex", "jellyfin", "emby", "autoscan")


def _stringify(v):
  if isinstance(v, bool):
    return "true" if v else "false"
  return str(v)


def _normalise_instance(inst):
  if not isinstance(inst, dict):
    return {}
  return {k: _stringify(v) for k, v in inst.items() if v is not None and str(v) != ""}


def _apply_defaults(instances):
  """Pop ``_defaults`` from a service-type dict and merge it onto siblings.

  Mirrors ``Services._apply_service_defaults`` in resources/config_schema.py
  so deploy stamping (which reads local.yml directly without going through
  the pydantic schema) sees the same cascaded shape.
  """
  if not isinstance(instances, dict):
    return instances
  defaults = instances.pop("_defaults", None)
  if not isinstance(defaults, dict):
    return instances
  for inst_name, inst_data in list(instances.items()):
    if not isinstance(inst_data, dict):
      continue
    instances[inst_name] = {**defaults, **inst_data}
  return instances


path = sys.argv[1] if len(sys.argv) > 1 else "setup/local.yml"

out = {}
try:
  data = _load(path)
  services = data.get("services") or {}
  for stype, instances in services.items():
    if stype.lower() not in KNOWN_TYPES:
      continue
    if not isinstance(instances, dict):
      continue
    instances = _apply_defaults(instances)
    type_out = {}
    for inst_name, inst_data in instances.items():
      norm = _normalise_instance(inst_data)
      if norm:
        type_out[inst_name] = norm
    if type_out:
      out[stype.lower()] = type_out
except FileNotFoundError:
  pass

print(json.dumps(out))
