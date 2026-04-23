"""Stamp service credentials into all *.ini configs on a remote host.

Usage: python3 ini_stamp_credentials.py <deploy_dir> <use_sudo> <services_b64>

  deploy_dir   - absolute path to the SMA deployment directory
  use_sudo     - 'true' or 'false'
  services_b64 - base64-encoded JSON services dict
"""

import base64
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

deploy_dir = sys.argv[1]
use_sudo = sys.argv[2] == "true" if len(sys.argv) > 2 else False
overrides = json.loads(base64.b64decode(sys.argv[3]).decode()) if len(sys.argv) > 3 else {}

# Build a map from config_file basename -> service section name so we can
# derive a per-service recycle-bin subdirectory for each *.ini config.
# e.g. config/autoProcess.tv.ini -> "Sonarr" -> recycle-bin = <base>/Sonarr
config_to_service = {}
for sec, keys in overrides.items():
  cf = keys.get("config_file", "").strip()
  if cf:
    config_to_service[os.path.basename(cf)] = sec

recycle_base = overrides.get("Converter", {}).get("recycle-bin", "").strip()

for ini_path in sorted(glob.glob(os.path.join(deploy_dir, "config", "*.ini"))):
  basename = os.path.basename(ini_path)
  service = config_to_service.get(basename)
  lines = open(ini_path).readlines()
  out, cur_sec, changed = [], None, False
  for line in lines:
    m = re.match(r"^\[(.+)\]", line.strip())
    if m:
      cur_sec = m.group(1)
      out.append(line)
      continue
    sec_overrides = overrides.get(cur_sec, {})
    if sec_overrides:
      m2 = re.match(r"^(\s*)(\S[^=]*?)\s*=\s*(.*)", line)
      if m2:
        indent, key, old_val = m2.groups()
        key = key.strip()
        new_val = sec_overrides.get(key)
        # recycle-bin gets a per-service subdirectory when the config
        # belongs to a known service and a base path is configured.
        if key == "recycle-bin" and cur_sec == "Converter" and recycle_base and service:
          new_val = f"{recycle_base}/{service}"
        if new_val is not None and new_val != old_val.strip():
          print(f"  [{cur_sec}] {key}: {old_val.strip()!r} -> {new_val!r}  ({basename})")
          line = f"{indent}{key} = {new_val}\n"
          changed = True
    out.append(line)
  if changed:
    if use_sudo:
      tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".ini")
      tmp.writelines(out)
      tmp.close()
      subprocess.run(["sudo", "mv", tmp.name, ini_path], check=True)
    else:
      open(ini_path, "w").writelines(out)
