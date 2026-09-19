"""Tests for resources/version.py and the --version CLI flags."""

import os
import subprocess
import sys
import tomllib

from resources import version

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pyproject_version():
  with open(os.path.join(PROJECT_ROOT, "pyproject.toml"), "rb") as f:
    return tomllib.load(f)["project"]["version"]


class TestGetVersion:
  def test_matches_pyproject(self):
    assert version.get_version() == _pyproject_version()

  def test_never_unknown_in_checkout(self):
    # A source checkout always carries pyproject.toml, so version metadata
    # must resolve even when the package is not pip-installed.
    assert version.get_version() != "unknown"


class TestGetCommit:
  def test_env_override(self, monkeypatch):
    monkeypatch.setenv("SMA_GIT_SHA", "abc123def456")
    assert version.get_commit() == "abc123def456"

  def test_unknown_when_unset(self, monkeypatch):
    monkeypatch.delenv("SMA_GIT_SHA", raising=False)
    assert version.get_commit() == "unknown"

  def test_unknown_when_blank(self, monkeypatch):
    monkeypatch.setenv("SMA_GIT_SHA", "  ")
    assert version.get_commit() == "unknown"


class TestVersionString:
  def test_format(self, monkeypatch):
    monkeypatch.setenv("SMA_GIT_SHA", "0123456789abcdef0123")
    assert version.version_string() == "sma-ng %s (commit 0123456789ab)" % _pyproject_version()

  def test_unknown_commit_not_truncated(self, monkeypatch):
    monkeypatch.delenv("SMA_GIT_SHA", raising=False)
    assert version.version_string().endswith("(commit unknown)")


class TestCliVersionFlags:
  def _run(self, script):
    return subprocess.run(
      [sys.executable, os.path.join(PROJECT_ROOT, script), "--version"],
      capture_output=True,
      text=True,
      timeout=120,
      cwd=PROJECT_ROOT,
    )

  def test_manual_py_version(self):
    result = self._run("manual.py")
    assert result.returncode == 0
    assert result.stdout.strip() == version.version_string()

  def test_daemon_py_version(self):
    result = self._run("daemon.py")
    assert result.returncode == 0
    assert result.stdout.strip() == version.version_string()
