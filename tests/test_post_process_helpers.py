"""Post-process helper safety: private helpers are never run as hooks,
and the sample submitter scripts target a route the daemon serves."""

import glob
import os

from resources import postprocess as postprocess_module
from resources.postprocess import PostProcessor

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_gather_scripts_skips_underscore_helpers(tmp_path, monkeypatch):
  (tmp_path / "resources").mkdir()
  pp_dir = tmp_path / "post_process"
  pp_dir.mkdir()
  for name in ("hook.py", "_json_helper.py", "notes.md"):
    (pp_dir / name).write_text("#!/bin/sh\n")
  monkeypatch.setattr(postprocess_module, "__file__", str(tmp_path / "resources" / "postprocess.py"))
  scripts = PostProcessor(["/file.mp4"]).scripts
  assert [os.path.basename(s) for s in scripts] == ["hook.py"]


def test_sample_submitters_post_to_served_webhook_route():
  scripts = glob.glob(os.path.join(PROJECT_ROOT, "setup", "post_process", "*_post_process.sh"))
  assert scripts
  for path in scripts:
    with open(path) as f:
      text = f.read()
    assert '"${DAEMON_BASE}/webhook"' not in text, path
    assert "${DAEMON_BASE}/webhook/generic" in text, path
