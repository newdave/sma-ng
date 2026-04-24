"""Apply FFMPEG_DIR to all YAML/INI configs on a remote host.

Usage: python3 stamp_ffmpeg.py <deploy_dir> <ffmpeg_dir> <use_sudo>

  deploy_dir - absolute path to the SMA deployment directory
  ffmpeg_dir - directory containing ffmpeg and ffprobe binaries
  use_sudo   - 'true' or 'false'
"""

import os
import re
import subprocess
import sys
import tempfile

from ruamel.yaml import YAML

deploy_dir = sys.argv[1]
ffmpeg_dir = sys.argv[2]
use_sudo = sys.argv[3] == "true" if len(sys.argv) > 3 else False


def write_output(path, content, suffix):
  if use_sudo:
    tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=suffix)
    tmp.write(content)
    tmp.close()
    subprocess.run(["sudo", "mv", tmp.name, path], check=True)
  else:
    with open(path, "w") as f:
      f.write(content)


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


config_dir = os.path.join(deploy_dir, "config")

for yaml_path in sorted([os.path.join(config_dir, f) for f in os.listdir(config_dir) if f.endswith((".yaml", ".yml"))]):
  yaml = YAML(typ="rt")
  with open(yaml_path) as f:
    data = yaml.load(f) or {}
  converter = data.setdefault("Converter", {})
  changed = False
  for key, value in [("ffmpeg", f"{ffmpeg_dir}/ffmpeg"), ("ffprobe", f"{ffmpeg_dir}/ffprobe")]:
    if converter.get(key) != value:
      converter[key] = value
      changed = True
  if changed:
    write_yaml(yaml_path, data)

for ini_path in sorted([os.path.join(config_dir, f) for f in os.listdir(config_dir) if f.endswith(".ini")]):
  lines = open(ini_path).readlines()
  out = []
  changed = False
  for line in lines:
    if re.match(r"^ffmpeg *=", line):
      line = f"ffmpeg = {ffmpeg_dir}/ffmpeg\n"
      changed = True
    elif re.match(r"^ffprobe *=", line):
      line = f"ffprobe = {ffmpeg_dir}/ffprobe\n"
      changed = True
    out.append(line)
  if changed:
    write_output(ini_path, "".join(out), ".ini")
