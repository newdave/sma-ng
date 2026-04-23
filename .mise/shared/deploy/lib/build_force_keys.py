"""Build --force-keys argument from [managed_keys] in .local.ini.

Each option in [managed_keys] is "Section = key1, key2, ..."; this converts
them to the "Section.key1,Section.key2,..." format that ini_merge.py expects.

Usage: python3 build_force_keys.py <local-ini-path>
"""

import re
import sys

path = sys.argv[1]
try:
  lines = open(path).readlines()
except FileNotFoundError:
  sys.exit(0)

in_section = False
pairs = []
for line in lines:
  s = line.strip()
  if re.match(r"^\[managed_keys\]", s):
    in_section = True
    continue
  if in_section and re.match(r"^\[", s):
    break
  if in_section and "=" in s and not s.startswith("#"):
    sec, _, keys_str = s.partition("=")
    sec = sec.strip()
    for k in keys_str.split(","):
      k = k.strip()
      if k:
        pairs.append(f"{sec}.{k}")

print(",".join(pairs))
