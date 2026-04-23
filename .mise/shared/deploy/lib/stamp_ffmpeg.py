"""Apply FFMPEG_DIR to all *.ini configs on a remote host.

Usage: python3 stamp_ffmpeg.py <deploy_dir> <ffmpeg_dir> <use_sudo>

  deploy_dir - absolute path to the SMA deployment directory
  ffmpeg_dir - directory containing ffmpeg and ffprobe binaries
  use_sudo   - 'true' or 'false'
"""

import glob
import os
import re
import subprocess
import sys
import tempfile

deploy_dir = sys.argv[1]
ffmpeg_dir = sys.argv[2]
use_sudo = sys.argv[3] == "true" if len(sys.argv) > 3 else False

for ini_path in sorted(glob.glob(os.path.join(deploy_dir, "config", "*.ini"))):
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
    if use_sudo:
      tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".ini")
      tmp.writelines(out)
      tmp.close()
      subprocess.run(["sudo", "mv", tmp.name, ini_path], check=True)
    else:
      open(ini_path, "w").writelines(out)
