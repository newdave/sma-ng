"""Create missing per-service autoProcess.*.ini files on a remote host.

Reads config_file entries from [Sonarr*] and [Radarr*] sections in services
JSON, creates each missing file from the sample, and stamps credentials.

Usage: python3 ini_ensure_services.py <deploy_dir> <gpu> <use_sudo> <services_b64>

  deploy_dir   - absolute path to the SMA deployment directory
  gpu          - detected GPU name (e.g. nvenc, vaapi, software)
  use_sudo     - 'true' or 'false'
  services_b64 - base64-encoded JSON services dict
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile

deploy_dir = sys.argv[1]
gpu = sys.argv[2] if len(sys.argv) > 2 else ""
use_sudo = sys.argv[3] == "true" if len(sys.argv) > 3 else False
services = json.loads(base64.b64decode(sys.argv[4]).decode()) if len(sys.argv) > 4 else {}

SERVICE_CREDENTIAL_KEYS = ("host", "port", "ssl", "apikey", "webroot")
sample_path = os.path.join(deploy_dir, "setup", "autoProcess.ini.sample")


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
        _, k, old_val = m2.groups()
        if k.strip() == key:
          if old_val.strip() != value:
            line = f"{k.strip()} = {value}\n"
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


recycle_base = services.get("Converter", {}).get("recycle-bin", "").strip()

for sec_name, keys in services.items():
  is_sonarr = re.match(r"^Sonarr", sec_name, re.IGNORECASE)
  is_radarr = re.match(r"^Radarr", sec_name, re.IGNORECASE)
  if not is_sonarr and not is_radarr:
    continue

  config_file = keys.get("config_file", "").strip()
  if not config_file:
    continue

  ini_path = os.path.join(deploy_dir, config_file)
  ini_section = sec_name

  if not os.path.exists(ini_path):
    os.makedirs(os.path.dirname(ini_path), exist_ok=True)
    with open(sample_path) as f:
      content = f.read()
    if gpu and gpu != "software":
      content = re.sub(r"(?m)^gpu *=.*", f"gpu = {gpu}", content)
    if use_sudo:
      tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".ini")
      tmp.write(content)
      tmp.close()
      subprocess.run(["sudo", "mv", tmp.name, ini_path], check=True)
    else:
      with open(ini_path, "w") as f:
        f.write(content)
    print(f"  created: {config_file}")

  credentials = {k: v for k in SERVICE_CREDENTIAL_KEYS if (v := keys.get(k, "").strip())}
  lines = open(ini_path).readlines()
  changed = False

  for key, value in credentials.items():
    new_lines = write_ini_key(lines, ini_section, key, value)
    if new_lines != lines:
      print(f"  [{ini_section}] {key} = {value!r}  ({os.path.basename(ini_path)})")
      lines = new_lines
      changed = True

  if recycle_base and sec_name:
    new_val = f"{recycle_base}/{sec_name}"
    new_lines = write_ini_key(lines, "Converter", "recycle-bin", new_val)
    if new_lines != lines:
      print(f"  [Converter] recycle-bin = {new_val!r}  ({os.path.basename(ini_path)})")
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
