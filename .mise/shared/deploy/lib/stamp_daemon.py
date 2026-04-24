"""Stamp daemon credentials into sma-ng.yml Daemon section and config/daemon.env.

Usage: python3 stamp_daemon.py <deploy_dir> <api_key_b64> <db_url_b64>
           <ffmpeg_dir_b64> <node_name_b64> <db_user_b64> <db_pw_b64>
           <db_name_b64> <services_b64>

All credential arguments are base64-encoded to safely handle special characters.
Pass an empty base64 value ("") for unused arguments.

  deploy_dir    - absolute path to the SMA deployment directory
  api_key_b64   - base64(daemon API key)
  db_url_b64    - base64(PostgreSQL connection URL)
  ffmpeg_dir_b64 - base64(ffmpeg binary directory)
  node_name_b64 - base64(cluster node identifier / SMA_NODE_NAME)
  db_user_b64   - base64(PostgreSQL username, for -pg profiles)
  db_pw_b64     - base64(PostgreSQL password, for -pg profiles)
  db_name_b64   - base64(PostgreSQL database name, for -pg profiles)
  services_b64  - base64(JSON services dict from services-json.py)
"""

import base64
import json
import os
import re
import sys

from ruamel.yaml import YAML


def _b64arg(n, default=""):
  if len(sys.argv) > n and sys.argv[n]:
    try:
      return base64.b64decode(sys.argv[n]).decode()
    except Exception:
      return default
  return default


deploy_dir = sys.argv[1]
api_key = _b64arg(2)
db_url = _b64arg(3)
ffmpeg_dir = _b64arg(4)
node_name = _b64arg(5)
db_user = _b64arg(6)
db_pw = _b64arg(7)
db_name = _b64arg(8)
services = json.loads(base64.b64decode(sys.argv[9]).decode()) if len(sys.argv) > 9 else {}

# ── sma-ng.yml Daemon section ─────────────────────────────────────────────
yaml_path = os.path.join(deploy_dir, "config", "sma-ng.yml")
if os.path.exists(yaml_path):
  yaml = YAML(typ="rt")
  yaml.width = 120
  with open(yaml_path) as f:
    root = yaml.load(f) or {}
  cfg = root.setdefault("Daemon", {})
  changed = False
  for field, val in [("api_key", api_key), ("db_url", db_url), ("ffmpeg_dir", ffmpeg_dir)]:
    if val and cfg.get(field) != val:
      print(f"  sma-ng.yml Daemon.{field}: {cfg.get(field)!r} -> {val!r}")
      cfg[field] = val
      changed = True

  # Rebuild path_configs from service sections that have path + profile.
  # Entries are sorted longest-path-first so the daemon's prefix matching works
  # correctly (more specific paths take priority).
  path_entries = []
  for sec, keys in services.items():
    path = keys.get("path", "").strip()
    profile = keys.get("profile", "").strip()
    config_file = keys.get("config_file", "").strip()
    if path and (profile or config_file):
      entry = {"path": path}
      if profile:
        entry["profile"] = profile
      else:
        basename = os.path.basename(config_file)
        if basename == "autoProcess.lq.ini":
          entry["profile"] = "lq"
        elif basename == "autoProcess.rq.ini":
          entry["profile"] = "rq"
        else:
          entry["config"] = config_file.replace(".ini", ".yaml")
      path_entries.append(entry)
  if path_entries:
    path_entries.sort(key=lambda e: len(e["path"]), reverse=True)
    old = cfg.get("path_configs", [])
    if old != path_entries:
      print(f"  sma-ng.yml Daemon.path_configs: rebuilding {len(path_entries)} entries")
      for e in path_entries:
        print(f"    {e['path']} -> {e.get('config') or e.get('profile')}")
      cfg["path_configs"] = path_entries
      changed = True

  if changed:
    with open(yaml_path, "w") as f:
      yaml.dump(root, f)
else:
  print("  WARNING: config/sma-ng.yml not found, skipping daemon YAML stamping")

# ── daemon.env ────────────────────────────────────────────────────────────
env_path = os.path.join(deploy_dir, "config", "daemon.env")
if os.path.exists(env_path):
  env_vars = {
    "SMA_NODE_NAME": node_name,
    "SMA_DAEMON_API_KEY": api_key,
    "SMA_DAEMON_DB_URL": db_url,
    "SMA_DAEMON_DB_USER": db_user,
    "SMA_DAEMON_DB_PASSWORD": db_pw,
    "SMA_DAEMON_DB_NAME": db_name,
    "SMA_DAEMON_FFMPEG_DIR": ffmpeg_dir,
  }
  with open(env_path) as f:
    lines = f.readlines()
  out = []
  seen = set()
  for line in lines:
    m = re.match(r"^#?\s*((?:SMA_DAEMON_\w+)|SMA_NODE_NAME)\s*=", line)
    if m:
      var = m.group(1)
      val = env_vars.get(var, "")
      if val and var not in seen:
        old = line.rstrip()
        line = f"{var}={val}\n"
        if old != line.rstrip():
          print(f"  daemon.env {var}: updated")
        seen.add(var)
    out.append(line)
  # append any vars not already present in the file
  for var, val in env_vars.items():
    if val and var not in seen:
      print(f"  daemon.env {var}: added")
      out.append(f"{var}={val}\n")
  with open(env_path, "w") as f:
    f.writelines(out)
else:
  print("  WARNING: config/daemon.env not found, skipping")
