"""Tests for resources/cli/daemonctl.py (`sma daemon ...`)."""

import dataclasses
import io
import json
import urllib.error

import pytest

from resources.cli import clientconfig, commands, daemonctl, routing
from resources.cli.clientconfig import ClientConfig
from resources.cli.errors import Unreachable, UsageError
from resources.cli.hostfacts import ArgStat, HostFacts

COMPOSE = {
  "Name": "/sma-ng",
  "State": {"Status": "running"},
  "Config": {
    "Labels": {
      "com.docker.compose.project": "docker",
      "com.docker.compose.project.working_dir": "/opt/sma/docker",
      "com.docker.compose.project.config_files": "/opt/sma/docker/docker-compose.yml,/opt/sma/docker/override.yml",
      "com.docker.compose.service": "sma-intel",
    }
  },
}
PLAIN = {"Name": "/sma-ng", "State": {"Status": "exited"}, "Config": {"Labels": {}}}


def _facts(container=None, argstat=(), **kw):
  base = HostFacts(
    pwd="/home/dave",
    uid=1000,
    gid=1000,
    os="Linux",
    home="/home/dave",
    sma_home="/opt/sma",
    image="img:3",
    bootstrap_version="dev",
    tty=False,
    stdin=False,
    dri_gids=(),
    nvidia=False,
    rootless=False,
    selinux=False,
    logs_writable=True,
    config_present=True,
    argstat=tuple(argstat),
    daemon_container=container,
  )
  return dataclasses.replace(base, **kw)


@pytest.fixture
def env(tmp_path):
  (tmp_path / "home").mkdir()
  return {"SMA_CLIENT_HOME": str(tmp_path / "home")}


def _steps(plan):
  return [[v if k == "arg" else ("optmount", v) for k, v in step.items] for step in plan.steps]


class TestCompose:
  BASE = ["compose", "-p", "docker", "--project-directory", "/opt/sma/docker", "-f", "/opt/sma/docker/docker-compose.yml", "-f", "/opt/sma/docker/override.yml"]

  @pytest.mark.parametrize(
    "verb,verbs",
    [
      ("up", [["up", "-d"]]),
      ("down", [["stop"]]),
      ("restart", [["restart"]]),
      ("logs", [["logs", "-f", "--tail", "200"]]),
      ("upgrade", [["pull"], ["up", "-d"]]),
    ],
  )
  def test_verbs(self, env, verb, verbs):
    plan = daemonctl.build(verb, [], 2, _facts(COMPOSE), ClientConfig(), env)
    assert _steps(plan) == [self.BASE + v + ["sma-intel"] for v in verbs]

  def test_media_refused_for_compose(self, env):
    with pytest.raises(UsageError, match="managed by Docker Compose"):
      daemonctl.build("up", ["--port", "9000"], 2, _facts(COMPOSE), ClientConfig(), env)

  def test_compose_info_none_without_labels(self):
    assert daemonctl.compose_info(None) is None
    assert daemonctl.compose_info(PLAIN) is None


class TestPlain:
  def test_up_with_media_saves_roots(self, env):
    facts = _facts(None, [ArgStat(3, "d", "/mnt/media")], dri_gids=(44,), nvidia=True)
    plan = daemonctl.build("up", ["--media", "/mnt/media"], 2, facts, ClientConfig(), env)
    run, wait = _steps(plan)
    assert run[:9] == ["run", "-d", "--name", "sma-ng", "--restart", "unless-stopped", "-p", "8585:8585", "-v"]
    assert "/opt/sma/config:/config" in run and "/opt/sma/logs:/logs" in run and "/opt/sma/data:/data" in run
    assert "/mnt/media:/mnt/media" in run
    assert ("optmount", "/transcode") in run and ("optmount", "/recycle") in run
    assert run[run.index("--device") : run.index("--device") + 6] == ["--device", "/dev/dri", "--group-add", "44", "--gpus", "all"]
    assert run[-1] == "img:3" and "--user" not in run
    assert wait == ["exec", "sma-ng", "python", "-m", "resources.cli", "_wait-health", "http://127.0.0.1:8585/health", "60"]
    assert clientconfig.load(env).daemon == {"media-roots": ["/mnt/media"]}

  def test_up_replaces_stale_plain_container_and_uses_saved_roots(self, env):
    cfg = ClientConfig(daemon={"media-roots": ["/mnt/saved"], "port": 9000})
    plan = daemonctl.build("up", [], 2, _facts(PLAIN, selinux=True), cfg, env)
    rm, run, _wait = _steps(plan)
    assert rm == ["rm", "-f", "sma-ng"]
    assert "9000:8585" in run and ("optmount", "/mnt/saved") in run
    assert "/opt/sma/config:/config:z" in run

  def test_up_without_roots_exit_2(self, env):
    with pytest.raises(UsageError, match="needs the media it serves") as exc:
      daemonctl.build("up", [], 2, _facts(), ClientConfig(), env)
    assert exc.value.exit_code == 2

  def test_port_saved(self, env):
    facts = _facts(None, [ArgStat(3, "d", "/m")])
    daemonctl.build("up", ["--media", "/m", "--port", "8686"], 2, facts, ClientConfig(), env)
    cfg = clientconfig.load(env)
    assert cfg.daemon == {"media-roots": ["/m"], "port": 8686}
    assert routing.resolve_target(cfg).target.url == "http://127.0.0.1:8686"

  @pytest.mark.parametrize(
    "args,stats,match",
    [
      (["--media", "/nope"], [ArgStat(3, "-", "/nope")], "not a directory"),
      (["--media", "/"], [ArgStat(3, "d", "/")], "cannot be mounted"),
      (["--media"], [], "needs a value"),
      (["--port", "x"], [], "needs a number"),
      (["--bogus"], [], "unknown daemon option"),
    ],
  )
  def test_up_errors(self, env, args, stats, match):
    with pytest.raises(UsageError, match=match):
      daemonctl.build("up", args, 2, _facts(None, stats), ClientConfig(), env)

  def test_up_needs_config_dir(self, env):
    with pytest.raises(UsageError, match="run the installer"):
      daemonctl.build("up", [], 2, _facts(None, config_present=False), ClientConfig(daemon={"media-roots": ["/m"]}), env)

  def test_down_restart_logs_upgrade(self, env):
    cfg = ClientConfig(daemon={"media-roots": ["/m"]})
    assert _steps(daemonctl.build("down", [], 2, _facts(PLAIN), cfg, env)) == [["stop", "sma-ng"], ["rm", "sma-ng"]]
    assert _steps(daemonctl.build("restart", [], 2, _facts(PLAIN), cfg, env)) == [["restart", "sma-ng"]]
    assert _steps(daemonctl.build("logs", [], 2, _facts(PLAIN), cfg, env)) == [["logs", "-f", "--tail", "200", "sma-ng"]]
    steps = _steps(daemonctl.build("upgrade", [], 2, _facts(PLAIN), cfg, env))
    assert steps[0] == ["pull", "img:3"] and steps[1] == ["rm", "-f", "sma-ng"] and steps[2][0] == "run"

  @pytest.mark.parametrize("verb", ["down", "restart", "logs"])
  def test_no_container(self, env, verb):
    with pytest.raises(UsageError, match="no sma-ng container"):
      daemonctl.build(verb, [], 2, _facts(None), ClientConfig(), env)

  def test_container_name_from_env(self, env):
    env["SMA_DAEMON_NAME"] = "sma-4k"
    assert _steps(daemonctl.build("restart", [], 2, _facts(PLAIN), ClientConfig(), env)) == [["restart", "sma-4k"]]

  def test_unknown_verb(self, env):
    with pytest.raises(UsageError, match="unknown daemon command"):
      daemonctl.build("frob", [], 2, _facts(), ClientConfig(), env)


class TestStatus:
  def _status(self, monkeypatch, env, container, healthy, as_json=False):
    monkeypatch.setattr("resources.cli.http.DaemonClient.healthy", lambda self, timeout=1.0: healthy)
    out = io.StringIO()
    rc = daemonctl.status(_facts(container), ClientConfig(), env, out, as_json=as_json)
    return rc, out.getvalue()

  def test_compose_running(self, monkeypatch, env):
    rc, out = self._status(monkeypatch, env, COMPOSE, True)
    assert rc == 0
    assert "sma-ng running (compose project docker, service sma-intel)" in out and "answering at http://127.0.0.1:8585" in out

  def test_none_and_json(self, monkeypatch, env):
    rc, out = self._status(monkeypatch, env, None, False)
    assert rc == 3 and "container: none" in out and "not answering" in out
    rc, out = self._status(monkeypatch, env, PLAIN, False, as_json=True)
    data = json.loads(out)
    assert data["managed_by"] == "sma" and data["state"] == "exited" and data["healthy"] is False


class _Resp:
  def __init__(self, status):
    self.status = status

  def __enter__(self):
    return self

  def __exit__(self, *a):
    return False


class TestWaitHealth:
  def test_succeeds_after_retries(self):
    calls = []

    def opener(url, timeout):
      calls.append(url)
      if len(calls) < 3:
        raise urllib.error.URLError("refused")
      return _Resp(200)

    assert daemonctl.wait_health("http://x/health", 60, opener=opener, sleep=lambda s: None) == 0
    assert len(calls) == 3

  def test_times_out_with_exit_3(self):
    now = [0.0]

    def sleep(s):
      now[0] += s

    def opener(url, timeout):
      return _Resp(503)

    with pytest.raises(Unreachable, match="did not become healthy within 5s") as exc:
      daemonctl.wait_health("http://x/health", 5, opener=opener, sleep=sleep, clock=lambda: now[0])
    assert exc.value.exit_code == 3


class TestCommands:
  @pytest.fixture
  def cenv(self, tmp_path):
    plan = tmp_path / "plan"
    plan.mkdir()
    (plan / "argstat").write_text("0\t-\t/x\n1\t-\t/x\n2\t-\t/x\n3\td\t/mnt/media\n")
    (tmp_path / "home").mkdir()
    return {
      "SMA_PLAN_DIR": str(plan),
      "SMA_CLIENT_HOME": str(tmp_path / "home"),
      "SMA_HOST_PWD": "/home/dave",
      "SMA_HOST_UID": "1000",
      "SMA_HOST_GID": "1000",
      "SMA_HOME": "/opt/sma",
      "SMA_IMAGE": "img:3",
      "SMA_HOST_CONFIG": "1",
    }

  def test_daemon_up_writes_plan(self, cenv):
    assert commands.run(["daemon", "up", "--media", "/mnt/media"], env=cenv, out=io.StringIO(), err=io.StringIO()) == 0
    text = open(cenv["SMA_PLAN_DIR"] + "/plan").read()
    assert "arg /mnt/media:/mnt/media" in text and "arg _wait-health" in text

  def test_logs_alias(self, cenv):
    with open(cenv["SMA_PLAN_DIR"] + "/daemon.json", "w") as f:
      json.dump([PLAIN], f)
    assert commands.run(["logs"], env=cenv, out=io.StringIO(), err=io.StringIO()) == 0
    assert "arg logs" in open(cenv["SMA_PLAN_DIR"] + "/plan").read()

  def test_status_default(self, cenv, monkeypatch):
    monkeypatch.setattr("resources.cli.http.DaemonClient.healthy", lambda self, timeout=1.0: True)
    out = io.StringIO()
    assert commands.run(["daemon"], env=cenv, out=out, err=io.StringIO()) == 0
    assert "answering" in out.getvalue()

  def test_wait_health_command(self, monkeypatch):
    monkeypatch.setattr(daemonctl, "wait_health", lambda url, secs: 0 if (url, secs) == ("http://h", 7) else 9)
    assert commands.run(["_wait-health", "http://h", "7"], env={}, out=io.StringIO(), err=io.StringIO()) == 0
    with pytest.raises(UsageError):
      commands.run(["_wait-health"], env={}, out=io.StringIO(), err=io.StringIO())
