"""Stamp service credentials into YAML/INI configs on a remote host.

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
from collections import defaultdict

from ruamel.yaml import YAML

deploy_dir = sys.argv[1]
use_sudo = sys.argv[2] == "true" if len(sys.argv) > 2 else False
overrides = json.loads(base64.b64decode(sys.argv[3]).decode()) if len(sys.argv) > 3 else {}

# Legacy INI support: config_file basename -> service section names.
config_to_services = defaultdict(list)
for sec, keys in overrides.items():
  cf = keys.get("config_file", "").strip()
  if cf:
    config_to_services[os.path.basename(cf)].append(sec)

recycle_base = overrides.get("Converter", {}).get("recycle-bin", "").strip()


def write_yaml(path, data):
  yaml = YAML(typ="rt")
  yaml.width = 120
  if use_sudo:
    tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml")
    yaml.dump(data, tmp)
    tmp.close()
    subprocess.run(["sudo", "mv", tmp.name, path], check=True)
  else:
    with open(path, "w") as f:
      yaml.dump(data, f)


yaml_path = os.path.join(deploy_dir, "config", "sma-ng.yml")
if os.path.exists(yaml_path):
  yaml = YAML(typ="rt")
  with open(yaml_path) as f:
    data = yaml.load(f) or {}
  changed = False
  service_sections = [sec for sec in overrides if sec.lower().startswith(("sonarr", "radarr"))]
  for service in service_sections:
    sec_overrides = overrides.get(service, {})
    section_data = data.setdefault(service, {})
    for key, new_val in sec_overrides.items():
      if key in ("config_file", "profile", "path"):
        continue
      if section_data.get(key) != new_val:
        print(f"  [{service}] {key}: {new_val!r}  (sma-ng.yml)")
        section_data[key] = new_val
        changed = True

    if recycle_base:
      recycle_val = f"{recycle_base}/{service}"
      if data.setdefault("Converter", {}).get("recycle-bin") != recycle_val and len(service_sections) == 1:
        print(f"  [Converter] recycle-bin: {recycle_val!r}  (sma-ng.yml)")
        data["Converter"]["recycle-bin"] = recycle_val
        changed = True

  if changed:
    write_yaml(yaml_path, data)


def write_ini_key(lines, section, key, value):
  out, cur, key_found = [], None, False
  section_found = False
  for line in lines:
    m = re.match(r"^\[(.+)\]", line.strip())
    if m:
      if cur == section and not key_found:
        out.append(f"{key} = {value}\n")
        key_found = True
      cur = m.group(1)
      if cur == section:
        section_found = True
      out.append(line)
      continue
    if cur == section and not key_found:
      m2 = re.match(r"^(\s*)(\S[^=]*?)\s*=\s*(.*)", line)
      if m2:
        indent, existing_key, old_val = m2.groups()
        if existing_key.strip() == key:
          if old_val.strip() != value:
            line = f"{indent}{existing_key.strip()} = {value}\n"
          key_found = True
    out.append(line)
  if cur == section and not key_found:
    out.append(f"{key} = {value}\n")
  elif not section_found:
    if out and out[-1].strip():
      out.append("\n")
    out.append(f"[{section}]\n")
    out.append(f"{key} = {value}\n")
  return out


for ini_path in sorted(glob.glob(os.path.join(deploy_dir, "config", "*.ini"))):
  basename = os.path.basename(ini_path)
  services = config_to_services.get(basename, [])
  if not services:
    continue
  lines = open(ini_path).readlines()
  changed = False

  for service in services:
    sec_overrides = overrides.get(service, {})
    for key, new_val in sec_overrides.items():
      if key in ("config_file", "profile"):
        continue
      new_lines = write_ini_key(lines, service, key, new_val)
      if new_lines != lines:
        print(f"  [{service}] {key}: {new_val!r}  ({basename})")
        lines = new_lines
        changed = True

  if recycle_base and len(services) == 1:
    service = services[0]
    new_val = f"{recycle_base}/{service}"
    new_lines = write_ini_key(lines, "Converter", "recycle-bin", new_val)
    if new_lines != lines:
      print(f"  [Converter] recycle-bin: {new_val!r}  ({basename})")
      lines = new_lines
      changed = True

  if changed:
    if use_sudo:
      tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".ini")
      tmp.writelines(lines)
      tmp.close()
      subprocess.run(["sudo", "mv", tmp.name, ini_path], check=True)
    else:
      open(ini_path, "w").writelines(lines)
