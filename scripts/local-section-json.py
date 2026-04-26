#!/usr/bin/env python3
"""scripts/local-section-json.py <local-yml> <section>

Emit a top-level section of setup/local.yml as JSON, suitable for
base64-encoding and passing to remote stamping helpers.

Returns ``{}`` for a missing file or section.
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


def _normalise(value):
  """Recursively convert ruamel.yaml types to plain Python primitives so
  ``json.dumps`` accepts them."""
  if isinstance(value, dict):
    return {str(k): _normalise(v) for k, v in value.items()}
  if isinstance(value, (list, tuple)):
    return [_normalise(v) for v in value]
  if isinstance(value, bool):
    return value
  if isinstance(value, (int, float)):
    return value
  if value is None:
    return None
  return str(value)


if len(sys.argv) < 3:
  print(f"Usage: {sys.argv[0]} <local-yml> <section>", file=sys.stderr)
  sys.exit(1)

path, section = sys.argv[1], sys.argv[2]
try:
  data = _load(path)
except FileNotFoundError:
  print("{}")
  sys.exit(0)

print(json.dumps(_normalise(data.get(section) or {})))
