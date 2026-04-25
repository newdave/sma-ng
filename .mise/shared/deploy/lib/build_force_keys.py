"""Build --force-keys argument from managed_keys in .local.yml.

Each entry under managed_keys is "SectionName: key1, key2, ..."; this converts
them to the "Section.key1,Section.key2,..." format that ini_merge.py expects.

Usage: python3 build_force_keys.py <local-yml-path>
"""

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


path = sys.argv[1]
try:
  data = _load(path)
except FileNotFoundError:
  sys.exit(0)

managed = data.get("managed_keys") or {}
pairs = []
for section, keys_val in managed.items():
  for k in str(keys_val).split(","):
    k = k.strip()
    if k:
      pairs.append(f"{section}.{k}")

print(",".join(pairs))
