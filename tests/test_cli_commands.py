"""Tests for resources/cli/commands.py (command surface and exit codes)."""

import io

import pytest

from resources.cli import commands
from resources.cli.errors import AuthError, PlanError, Unreachable, UsageError


@pytest.fixture(autouse=True)
def no_daemon(monkeypatch):
  # Never probe a real daemon on this machine: these tests cover throwaway runs.
  monkeypatch.setattr("resources.cli.http.DaemonClient.healthy", lambda self, timeout=1.0: False)


@pytest.fixture
def env(tmp_path):
  plan_dir = tmp_path / "plan"
  plan_dir.mkdir()
  config_dir = tmp_path / "config"
  config_dir.mkdir()
  return {
    "SMA_PLAN_DIR": str(plan_dir),
    "SMA_CONFIG_DIR": str(config_dir),
    "SMA_CLIENT_HOME": str(tmp_path / "home-config"),
    "SMA_HOST_PWD": "/home/dave",
    "SMA_HOST_UID": "1000",
    "SMA_HOST_GID": "1000",
    "SMA_HOME": "/opt/sma",
    "SMA_IMAGE": "img:1",
    "SMA_HOST_CONFIG": "1",
  }


def _argstat(env, *lines):
  with open(env["SMA_PLAN_DIR"] + "/argstat", "w") as f:
    f.write("".join(line + "\n" for line in lines))


def _plan(env):
  with open(env["SMA_PLAN_DIR"] + "/plan") as f:
    return f.read().splitlines()


def test_repair_writes_plan(env):
  _argstat(env, "0\t-\t/home/dave/repair", "1\td\t/mnt/tv")
  assert commands.main(["repair", "/mnt/tv"], env=env) == 0
  lines = _plan(env)
  assert lines[0] == "sma-plan 1" and lines[-1] == "end"
  assert lines[-3:-1] == ["arg repair.py", "arg /mnt/tv"]


def test_global_flags_before_command(env, tmp_path):
  (tmp_path / "home-config").mkdir()
  (tmp_path / "home-config" / "config.yml").write_text("extra-mounts: [/srv/extra]\n")
  _argstat(env, "0\t-\t/home/dave/--local", "1\t-\t/home/dave/--mount", "2\td\t/srv/m", "3\t-\t/home/dave/-q", "4\t-\t/x", "5\td\t/mnt/tv")
  assert commands.main(["--local", "--mount", "/srv/m", "-q", "repair", "/mnt/tv"], env=env) == 0
  lines = _plan(env)
  assert "optmount /srv/m" in lines and "optmount /srv/extra" in lines
  assert "arg /mnt/tv" in lines


@pytest.mark.parametrize("command", ["convert", "preview", "audit", "rename"])
def test_path_commands_dispatch(env, command):
  _argstat(env, "0\t-\t/x", "1\tf\t/m/a.mkv")
  assert commands.main([command, "/m/a.mkv"], env=env) == 0
  assert "arg /m/a.mkv" in _plan(env)


@pytest.mark.parametrize("command", ["codecs", "shell"])
def test_pathless_commands_dispatch(env, command):
  _argstat(env, "0\t-\t/x")
  assert commands.main([command], env=env) == 0
  assert _plan(env)[0] == "sma-plan 1"


def test_upgrade_reaching_client_is_usage(env, capsys):
  assert commands.main(["upgrade"], env=env) == 2


def test_version(env, capsys):
  env["SMA_NO_UPDATE_CHECK"] = "1"
  assert commands.main(["version"], env=env) == 0
  out = capsys.readouterr().out
  assert out.startswith("sma image: ")
  assert "latest:    skipped" in out


def _release(version="3.2.35"):
  return {"version": version, "tag": "sma-ng-v" + version, "url": "https://x", "published": "2026-10-28"}


@pytest.mark.parametrize(
  "image,daemon,latest,expect,absent",
  [
    ("3.2.34", None, "3.2.35", ["sma image: 3.2.34 (commit c) - out of date", "latest:    3.2.35 (released 2026-10-28)", "run sma upgrade"], ["up to date"]),
    ("3.2.35", None, "3.2.35", ["sma: up to date"], ["out of date"]),
    ("3.2.35", "3.2.34", "3.2.35", ["daemon:    3.2.34 (commit abcdef0) at http://127.0.0.1:8585 - out of date", "run sma daemon upgrade"], ["up to date"]),
    ("dev", None, "3.2.35", ["sma image: dev (commit c)\n"], ["out of date", "up to date"]),
  ],
)
def test_version_against_latest_release(env, monkeypatch, image, daemon, latest, expect, absent):
  monkeypatch.setattr(commands, "get_version", lambda: image)
  monkeypatch.setattr(commands, "get_commit", lambda: "c")
  health = {"version": daemon, "commit": "abcdef0123"} if daemon else None
  monkeypatch.setattr("resources.cli.http.DaemonClient.health", lambda self, timeout=2.0: health)
  monkeypatch.setattr("resources.cli.releases.latest_release", lambda repo=None: _release(latest))
  out = io.StringIO()
  assert commands.run(["version"], env=env, out=out, err=io.StringIO()) == 0
  text = out.getvalue()
  for line in expect:
    assert line in text
  for line in absent:
    assert line not in text


def test_version_release_check_failure_is_not_fatal(env, monkeypatch):
  from resources.cli import releases

  def boom(repo=None):
    raise releases.ReleaseCheckError("timed out")

  monkeypatch.setattr("resources.cli.releases.latest_release", boom)
  monkeypatch.setattr("resources.cli.http.DaemonClient.health", lambda self, timeout=2.0: None)
  out = io.StringIO()
  assert commands.run(["version"], env=env, out=out, err=io.StringIO()) == 0
  assert "latest:    unknown (timed out)" in out.getvalue()


def test_version_json(env, monkeypatch):
  import json

  monkeypatch.setattr(commands, "get_version", lambda: "3.2.34")
  monkeypatch.setattr("resources.cli.http.DaemonClient.health", lambda self, timeout=2.0: {"version": "3.2.35", "commit": "abc"})
  monkeypatch.setattr("resources.cli.releases.latest_release", lambda repo=None: _release())
  out = io.StringIO()
  assert commands.run(["--json", "version"], env=env, out=out, err=io.StringIO()) == 0
  data = json.loads(out.getvalue())
  assert data["image"]["outdated"] is True and data["daemon"]["outdated"] is False and data["latest"]["version"] == "3.2.35"


def test_status_is_daemon_status(env, monkeypatch):
  seen = {}

  def fake_status(facts, cfg, e, out, as_json=False):
    seen["json"] = as_json
    return 0

  monkeypatch.setattr("resources.cli.daemonctl.status", fake_status)
  assert commands.run(["--json", "status"], env=env, out=io.StringIO(), err=io.StringIO()) == 0
  assert seen == {"json": True}


def test_help_and_usage(env):
  out = io.StringIO()
  assert commands.run(["help"], env=env, out=out) == 0
  assert "convert PATH" in out.getvalue()
  assert commands.run(["--help"], env=env, out=io.StringIO()) == 0
  assert commands.run([], env=env, out=io.StringIO()) == 2
  assert commands.run(["--local"], env=env, out=io.StringIO()) == 2


@pytest.mark.parametrize("argv,match", [(["--bogus", "repair"], "unknown option"), (["--mount"], "needs a value"), (["nope"], "unknown command")])
def test_parse_errors(env, capsys, argv, match):
  assert commands.main(argv, env=env) == 2
  assert match in capsys.readouterr().err


def test_missing_path_exits_2_with_one_line(env, capsys):
  _argstat(env, "0\t-\t/x", "1\t-\t/home/dave/gone.mkv")
  assert commands.main(["convert", "gone.mkv"], env=env) == 2
  err = capsys.readouterr().err
  assert err == "sma: gone.mkv: no such file or directory\n"


def test_missing_host_facts_exit_5(env, capsys):
  del env["SMA_IMAGE"]
  assert commands.main(["codecs"], env=env) == 5
  assert "SMA_IMAGE" in capsys.readouterr().err


@pytest.mark.parametrize("exc,code", [(UsageError, 2), (Unreachable, 3), (AuthError, 4), (PlanError, 5)])
def test_error_exit_codes(monkeypatch, exc, code):
  def boom(argv, env=None):
    raise exc("sma: boom")

  monkeypatch.setattr(commands, "run", boom)
  assert commands.main(["x"]) == code


@pytest.mark.parametrize(
  "text,match",
  [("extra-mounts: /not/a/list\n", "must be a list"), ("- a\n- b\n", "YAML mapping"), ("a: [unclosed\n", "cannot read")],
)
def test_bad_client_config(env, tmp_path, capsys, text, match):
  (tmp_path / "home-config").mkdir()
  (tmp_path / "home-config" / "config.yml").write_text(text)
  _argstat(env, "0\t-\t/x")
  assert commands.main(["codecs"], env=env) == 2
  assert match in capsys.readouterr().err


def test_python_dash_m_entrypoint():
  import os
  import subprocess
  import sys

  root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
  env = {**os.environ, "SMA_NO_UPDATE_CHECK": "1"}  # no live GitHub call from the test suite
  r = subprocess.run([sys.executable, "-m", "resources.cli", "version"], cwd=root, env=env, capture_output=True, text=True, check=False)
  assert r.returncode == 0, r.stderr
  assert r.stdout.startswith("sma image: ")
