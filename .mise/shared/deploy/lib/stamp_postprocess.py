"""Copy and stamp post-process scripts; update shebangs in root entry points.

Usage: python3 stamp_postprocess.py <deploy_dir>
           <plex_token_b64> <plex_username_b64> <plex_servername_b64>
           <jellyfin_url_b64> <jellyfin_token_b64>
           <emby_url_b64> <emby_apikey_b64>

All credential arguments are base64-encoded to safely handle special characters.
Pass an empty base64 value ("") for unused arguments.

  deploy_dir        - absolute path to the SMA deployment directory
  plex_token_b64    - base64(Plex token)
  plex_username_b64 - base64(Plex username)
  plex_servername_b64 - base64(Plex server name)
  jellyfin_url_b64  - base64(Jellyfin URL)
  jellyfin_token_b64 - base64(Jellyfin token)
  emby_url_b64      - base64(Emby URL)
  emby_apikey_b64   - base64(Emby API key)
"""

import base64
import glob
import os
import re
import sys


def _b64arg(n, default=""):
  if len(sys.argv) > n and sys.argv[n]:
    try:
      return base64.b64decode(sys.argv[n]).decode()
    except Exception:
      return default
  return default


deploy_dir = sys.argv[1]
plex_token = _b64arg(2)
plex_username = _b64arg(3)
plex_servername = _b64arg(4)
jellyfin_url = _b64arg(5)
jellyfin_token = _b64arg(6)
emby_url = _b64arg(7)
emby_apikey = _b64arg(8)

shebang = f"#!/{deploy_dir}/venv/bin/python3"


def stamp_shebang(content):
  """Replace the first shebang line with the venv-relative one."""
  if content.startswith("#!"):
    return re.sub(r"^#!.*", shebang, content, count=1)
  return shebang + "\n" + content


def _repl(value):
  """Return a re.sub callable that preserves the captured prefix group."""
  return lambda m: m.group(1) + f'"{value}"'


# ── stamp shebang in root-level entry-point scripts ──────────────────────
entry_points = glob.glob(os.path.join(deploy_dir, "*.py"))
for path in sorted(entry_points):
  with open(path) as f:
    content = f.read()
  new_content = stamp_shebang(content)
  if new_content != content:
    with open(path, "w") as f:
      f.write(new_content)
    print(f"  shebang updated: {os.path.basename(path)}")

# ── copy and stamp post-process scripts ──────────────────────────────────
src_dir = os.path.join(deploy_dir, "setup", "post_process")
dst_dir = os.path.join(deploy_dir, "post_process")
os.makedirs(dst_dir, exist_ok=True)

# Credentials keyed by script basename; callables preserve the captured prefix group.
STAMP_RULES = {
  "plex.py": [
    (r'^(TOKEN\s*=\s*)["\'].*["\']', _repl(plex_token)),
    (r'^(USERNAME\s*=\s*)["\'].*["\']', _repl(plex_username)),
    (r'^(SERVERNAME\s*=\s*)["\'].*["\']', _repl(plex_servername)),
  ],
  "jellyfin.py": [
    (r'^(TOKEN\s*=\s*)["\'].*["\']', _repl(jellyfin_token)),
    (r'^(url\s*=\s*)["\'].*["\']', _repl(jellyfin_url)),
  ],
  "emby.py": [
    (r'^(BASEURL\s*=\s*)["\'].*["\']', _repl(emby_url)),
    (r'^(APIKEY\s*=\s*)["\'].*["\']', _repl(emby_apikey)),
  ],
}

for src in sorted(glob.glob(os.path.join(src_dir, "*.py"))):
  name = os.path.basename(src)
  dst = os.path.join(dst_dir, name)
  with open(src) as f:
    content = f.read()
  content = stamp_shebang(content)
  rules = STAMP_RULES.get(name, [])
  changed = False
  for pattern, replacement in rules:
    new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
    if new_content != content:
      changed = True
      content = new_content
  with open(dst, "w") as f:
    f.write(content)
  os.chmod(dst, 0o755)
  status = "stamped" if changed else "copied"
  print(f"  {status}: post_process/{name}")
