"""Tests for shared trigger shell helpers (media filtering, submit counting)."""

import os
import shutil
import stat
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMON = os.path.join(ROOT, "triggers", "lib", "common.sh")

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")


def _fake_curl(bindir, exit_code):
  path = os.path.join(bindir, "curl")
  with open(path, "w") as f:
    f.write("#!/bin/sh\nexit %d\n" % exit_code)
  os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


def _run(snippet, bindir, extra_env=None):
  env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"])
  env.update(extra_env or {})
  return subprocess.run(
    ["bash", "-c", 'set -euo pipefail; . "$1"; sma_init_daemon; ' + snippet, "bash", COMMON],
    capture_output=True,
    text=True,
    env=env,
    check=False,
  )


def test_only_media_files_submitted(tmp_path):
  bindir = str(tmp_path / "bin")
  os.makedirs(bindir)
  _fake_curl(bindir, 0)
  media = tmp_path / "dl"
  media.mkdir()
  (media / "a.mkv").write_text("x")
  (media / "b.MP4").write_text("x")
  (media / "c.nfo").write_text("x")
  (media / "d.txt").write_text("x")
  r = _run('sma_submit_path "$2"'.replace("$2", str(media)), bindir)
  assert r.stdout.strip() == "2 0"


def test_failed_submissions_counted(tmp_path):
  bindir = str(tmp_path / "bin")
  os.makedirs(bindir)
  _fake_curl(bindir, 22)
  media = tmp_path / "dl"
  media.mkdir()
  (media / "a.mkv").write_text("x")
  r = _run('sma_submit_path "%s"' % media, bindir)
  assert r.stdout.strip() == "0 1"
  assert "Failed to submit" in r.stderr


def test_no_media_reports_zero(tmp_path):
  bindir = str(tmp_path / "bin")
  os.makedirs(bindir)
  _fake_curl(bindir, 0)
  media = tmp_path / "dl"
  media.mkdir()
  (media / "readme.nfo").write_text("x")
  r = _run('sma_submit_path "%s"' % media, bindir)
  assert r.stdout.strip() == "0 0"
