"""Apply ffmpeg/ffprobe paths to the four-bucket sma-ng.yml.

Usage::

    python3 stamp_ffmpeg.py <deploy_dir> <ffmpeg_dir> <use_sudo>

Writes ``base.converter.ffmpeg`` and ``base.converter.ffprobe`` in
``config/sma-ng.yml`` (and any other ``*.yml`` / ``*.yaml`` in the
config dir) so ``mise run config:roll`` keeps the binary location in
sync with each host's ffmpeg_dir.
"""

import os
import subprocess
import sys
import tempfile

from ruamel.yaml import YAML

deploy_dir = sys.argv[1]
ffmpeg_dir = sys.argv[2]
use_sudo = sys.argv[3] == "true" if len(sys.argv) > 3 else False


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
yaml_files = sorted(os.path.join(config_dir, f) for f in os.listdir(config_dir) if f.endswith((".yaml", ".yml")))

for yaml_path in yaml_files:
  yaml = YAML(typ="rt")
  with open(yaml_path) as f:
    data = yaml.load(f) or {}
  base = data.setdefault("base", {})
  converter = base.setdefault("converter", {})
  changed = False
  for key, value in (("ffmpeg", f"{ffmpeg_dir}/ffmpeg"), ("ffprobe", f"{ffmpeg_dir}/ffprobe")):
    if converter.get(key) != value:
      converter[key] = value
      changed = True
  if changed:
    write_yaml(yaml_path, data)
