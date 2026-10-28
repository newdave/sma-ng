"""Client side of daemon targets: config, HTTP client, routing, submit/follow, jobs/target/map."""

import dataclasses
import io
import json
import os
import stat
import urllib.error

import pytest

from resources.cli import clientconfig, commands, manage, remote, routing
from resources.cli.clientconfig import ClientConfig, Target
from resources.cli.errors import AuthError, CliError, Unreachable, UsageError
from resources.cli.hostfacts import ArgStat, HostFacts
from resources.cli.http import Conflict, DaemonClient


def _facts(argstat=(), os_name="Linux"):
  return HostFacts(
    pwd="/home/dave",
    uid=1000,
    gid=1000,
    os=os_name,
    home="/home/dave",
    sma_home="/opt/sma",
    image="img:1",
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
    daemon_container=None,
  )


class FakeDaemon:
  """Stands in for DaemonClient: scripted job states and output."""

  def __init__(self, roots=(), healthy=True, final="completed", output=("line 1\n", "line 2\n"), submit_response=None):
    self.roots = list(roots)
    self._healthy = healthy
    self.final = final
    self.output = list(output)
    self.submitted = []
    self.cancelled = []
    self.submit_response = submit_response
    self.polls = {}
    self.url = "http://fake:8585"

  def healthy(self, timeout=1.0):
    return self._healthy

  def get_paths(self):
    return self.roots

  def submit(self, kind, path, args):
    self.submitted.append((kind, path, list(args)))
    if self.submit_response is not None:
      return self.submit_response
    return {"status": "queued", "job_id": 40 + len(self.submitted), "kind": kind, "path": path}

  def get_job(self, job_id):
    n = self.polls.get(job_id, 0)
    self.polls[job_id] = n + 1
    status = self.final if n >= len(self.output) else "running"
    return {"id": job_id, "status": status, "kind": "repair", "path": "/p", "error": "boom" if status == "failed" else None}

  def get_output(self, job_id, offset):
    if offset < len(self.output):
      return self.output[offset], offset + 1
    return "", offset

  def list_jobs(self, status=None, limit=20):
    return [{"id": 1, "kind": "repair", "status": status or "completed", "path": "/m/a"}]

  def cancel(self, job_id):
    self.cancelled.append(job_id)
    return {}


# ---------------------------------------------------------------- clientconfig


def _write_cfg(tmp_path, text, mode=0o600):
  d = tmp_path / "home"
  d.mkdir(exist_ok=True)
  f = d / "config.yml"
  f.write_text(text)
  f.chmod(mode)
  return {"SMA_CLIENT_HOME": str(d)}


class TestClientConfig:
  def test_load_targets_and_mappings(self, tmp_path):
    env = _write_cfg(
      tmp_path,
      "default-target: nas\ntargets:\n  nas:\n    url: http://nas:8585/\n    api-key: s3cret\n    mappings:\n      - {local: /Volumes/M, remote: /mnt/M}\nextra-mounts: [/srv]\nkeep-me: 1\n",
    )
    cfg = clientconfig.load(env)
    assert cfg.default_target == "nas" and cfg.extra_mounts == ["/srv"]
    assert cfg.targets["nas"] == Target("nas", "http://nas:8585", "s3cret", [("/Volumes/M", "/mnt/M")])

  def test_keys_in_readable_file_refused(self, tmp_path):
    env = _write_cfg(tmp_path, "targets:\n  nas: {url: 'http://n', api-key: k}\n", mode=0o644)
    with pytest.raises(UsageError, match="chmod 600") as exc:
      clientconfig.load(env)
    assert exc.value.exit_code == 2

  def test_readable_file_without_keys_ok(self, tmp_path):
    env = _write_cfg(tmp_path, "targets:\n  nas: {url: 'http://n'}\n", mode=0o644)
    assert clientconfig.load(env).targets["nas"].api_key is None

  @pytest.mark.parametrize(
    "text,match",
    [
      ("targets: [a]\n", "targets must be a mapping"),
      ("targets:\n  nas: {}\n", "needs a url"),
      ("targets:\n  nas: {url: 'http://n', api-key: 5}\n", "api-key"),
      ("targets:\n  nas: {url: 'http://n', mappings: [{local: /a}]}\n", "mappings"),
    ],
  )
  def test_invalid(self, tmp_path, text, match):
    with pytest.raises(UsageError, match=match):
      clientconfig.load(_write_cfg(tmp_path, text))

  def test_save_is_0600_and_keeps_unknown_keys(self, tmp_path):
    env = _write_cfg(tmp_path, "keep-me: 1\n", mode=0o644)
    cfg = clientconfig.load(env)
    cfg.targets["nas"] = Target("nas", "http://nas:8585", "k", [("/a", "/b")])
    cfg.default_target = "nas"
    clientconfig.save(cfg, env)
    path = clientconfig.config_path(env)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    again = clientconfig.load(env)
    assert again.raw["keep-me"] == 1 and again.targets["nas"].mappings == [("/a", "/b")] and again.default_target == "nas"
    again.targets.clear()
    again.default_target = None
    clientconfig.save(again, env)
    assert "targets" not in clientconfig.load(env).raw


# ---------------------------------------------------------------- http


class _Resp(io.BytesIO):
  def __init__(self, status, body=b"", headers=None):
    super().__init__(body)
    self.status = status
    self.headers = headers or {}


def _opener(responses, seen):
  def opener(req, timeout=None):
    seen.append(req)
    status, body, headers = responses.pop(0)
    if isinstance(status, Exception):
      raise status
    if status >= 400:
      raise urllib.error.HTTPError(req.full_url, status, "err", headers or {}, io.BytesIO(body))
    return _Resp(status, body, headers)

  return opener


class TestDaemonClient:
  def test_submit_sends_key_and_json(self):
    seen = []
    c = DaemonClient("http://d:8585/", "k", opener=_opener([(202, b'{"status": "queued", "job_id": 3}', None)], seen))
    assert c.submit("repair", "/m", ["-n"])["job_id"] == 3
    req = seen[0]
    assert req.full_url == "http://d:8585/jobs" and req.get_method() == "POST"
    assert req.get_header("X-api-key") == "k"
    assert json.loads(req.data) == {"kind": "repair", "path": "/m", "args": ["-n"]}

  @pytest.mark.parametrize(
    "status,exc,match",
    [(401, AuthError, "rejected the API key"), (409, Conflict, "upgrade"), (400, UsageError, "Path does not exist"), (500, CliError, "daemon error 500")],
  )
  def test_errors(self, status, exc, match):
    body = json.dumps({"error": "Path does not exist" if status == 400 else "upgrade all nodes"}).encode()
    c = DaemonClient("http://d", opener=_opener([(status, body, None)], []))
    with pytest.raises(exc, match=match):
      c.submit("repair", "/m", [])

  def test_unreachable(self):
    c = DaemonClient("http://d", opener=_opener([(urllib.error.URLError("refused"), b"", None)], []))
    with pytest.raises(Unreachable, match="cannot reach"):
      c.get_job(1)
    c = DaemonClient("http://d", opener=_opener([(ConnectionRefusedError(), b"", None)], []))
    assert c.healthy() is False

  def test_healthy_paths_output_list_cancel(self):
    seen = []
    c = DaemonClient(
      "http://d",
      opener=_opener(
        [
          (200, b"{}", None),
          (200, b'{"roots": ["/mnt"]}', None),
          (200, "é\n".encode(), {"X-Next-Offset": "12"}),
          (200, b'{"jobs": [{"id": 1}]}', None),
          (200, b"{}", None),
          (404, b"", None),
          (200, b"x", {"X-Next-Offset": "bad"}),
          (404, b"", None),
        ],
        seen,
      ),
    )
    assert c.healthy() is True
    assert c.get_paths() == ["/mnt"]
    assert c.get_output(4, 9) == ("é\n", 12)
    assert c.list_jobs(status="failed", limit=5) == [{"id": 1}]
    assert "status=failed" in seen[3].full_url and "limit=5" in seen[3].full_url
    c.cancel(4)
    assert seen[4].full_url.endswith("/jobs/4/cancel") and seen[4].get_method() == "POST"
    assert c.get_paths() == []  # daemon predating /paths
    assert c.get_output(4, 0) == ("x", 1)
    assert c.get_output(4, 7) == ("", 7)

  def test_paths_and_output_auth(self):
    for method in ("get_paths", "get_output"):
      c = DaemonClient("http://d", opener=_opener([(401, b"", None)], []))
      with pytest.raises(AuthError):
        getattr(c, method)(*(() if method == "get_paths" else (1, 0)))


# ---------------------------------------------------------------- routing


class TestRouting:
  def test_resolution_precedence(self):
    cfg = ClientConfig(default_target="nas", targets={"nas": Target("nas", "http://nas"), "box": Target("box", "http://box")})
    assert routing.resolve_target(cfg, flag="box", env_target="nas").target.name == "box"
    assert routing.resolve_target(cfg, env_target="box").target.name == "box"
    assert routing.resolve_target(cfg).target.name == "nas"
    implicit = routing.resolve_target(ClientConfig())
    assert implicit.target.url == "http://127.0.0.1:8585" and not implicit.explicit
    assert routing.resolve_target(ClientConfig(), flag="local").explicit
    with pytest.raises(UsageError, match="unknown target"):
      routing.resolve_target(cfg, flag="nope")

  def test_local_target_uses_daemon_api_key(self, tmp_path, monkeypatch):
    (tmp_path / "sma-ng.yml").write_text("daemon:\n  api-key: secret\n")
    monkeypatch.setenv("SMA_CONFIG_DIR", str(tmp_path))
    assert routing.resolve_target(ClientConfig()).target.api_key == "secret"
    assert routing.resolve_target(ClientConfig(), flag="local").target.api_key == "secret"
    saved = ClientConfig(targets={"local": Target("local", "http://127.0.0.1:8585", "mine")})
    assert routing.resolve_target(saved).target.api_key == "mine"
    nas = ClientConfig(targets={"nas": Target("nas", "http://nas")})
    assert routing.resolve_target(nas, flag="nas").target.api_key is None

  def test_local_target_without_daemon_config(self, tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_CONFIG_DIR", str(tmp_path))
    assert routing.resolve_target(ClientConfig()).target.api_key is None
    (tmp_path / "sma-ng.yml").write_text("daemon: [not, a, mapping]\n")
    assert routing.resolve_target(ClientConfig()).target.api_key is None

  @pytest.mark.parametrize(
    "url,os_name,expected",
    [
      ("http://127.0.0.1:8585", "Darwin", "http://host.docker.internal:8585"),
      ("http://localhost", "Darwin", "http://host.docker.internal"),
      ("http://127.0.0.1:8585", "Linux", "http://127.0.0.1:8585"),
      ("http://nas:8585", "Darwin", "http://nas:8585"),
    ],
  )
  def test_host_url(self, url, os_name, expected):
    assert routing.host_url(url, _facts(os_name=os_name)) == expected

  def test_map_path_longest_prefix(self):
    maps = [("/Volumes/Media", "/mnt/media"), ("/Volumes/Media/TV", "/mnt/tv/")]
    assert routing.map_path("/Volumes/Media/TV/Show/e.mkv", maps) == "/mnt/tv/Show/e.mkv"
    assert routing.map_path("/Volumes/Media/Film.mkv", maps) == "/mnt/media/Film.mkv"
    assert routing.map_path("/Volumes/Media", maps) == "/mnt/media"
    assert routing.map_path("/Volumes/MediaX/a", maps) == "/Volumes/MediaX/a"
    assert routing.map_path("/mnt/media/a", maps) == "/mnt/media/a"

  def test_under_roots(self):
    assert routing.under_roots("/x", [])
    assert routing.under_roots("/mnt/m/a", ["/mnt/m"]) and routing.under_roots("/mnt/m", ["/mnt/m/"])
    assert not routing.under_roots("/mnt/mx", ["/mnt/m"])
    assert routing.under_roots("/anything", ["/"])

  def test_decide(self):
    resolved = routing.Resolved(Target("local", "http://127.0.0.1:8585"), explicit=False)
    up, down = FakeDaemon(), FakeDaemon(healthy=False)
    assert routing.decide(resolved, _facts(), client=up) is up
    assert routing.decide(resolved, _facts(), client=down) is None
    warned = []
    assert routing.decide(resolved, _facts(), local=True, warn=warned.append, client=up) is None
    assert "competes for the GPU" in warned[0]
    with pytest.raises(Unreachable, match="target nas"):
      routing.decide(routing.Resolved(Target("nas", "http://nas"), explicit=True), _facts(), client=down)

  def test_client_for_rewrites_darwin_loopback(self):
    c = routing.client_for(Target("local", "http://127.0.0.1:8585", "k"), _facts(os_name="Darwin"))
    assert c.url == "http://host.docker.internal:8585"


# ---------------------------------------------------------------- remote


def _resolved(mappings=()):
  return routing.Resolved(Target("nas", "http://nas", "k", list(mappings)), explicit=True)


class TestRemote:
  def test_submit_maps_and_follows(self):
    facts = _facts([ArgStat(1, "-", "/Volumes/M/TV")])
    daemon = FakeDaemon(roots=["/mnt/M"])
    out, err = io.StringIO(), io.StringIO()
    rc = remote.run("repair", ["/Volumes/M/TV", "-n"], 1, facts, _resolved([("/Volumes/M", "/mnt/M")]), daemon, out, err, sleep=lambda s: None)
    assert rc == 0
    assert daemon.submitted == [("repair", "/mnt/M/TV", ["-n"])]
    assert out.getvalue() == "line 1\nline 2\n"
    assert "queued job 41 on nas" in err.getvalue()

  def test_path_outside_roots_exit_2(self):
    facts = _facts([ArgStat(1, "-", "/home/dave/x.mkv")])
    with pytest.raises(UsageError, match="not under any path target nas serves") as exc:
      remote.run("convert", ["x.mkv"], 1, facts, _resolved(), FakeDaemon(roots=["/mnt"]), io.StringIO(), io.StringIO())
    assert exc.value.exit_code == 2

  def test_failed_job_exit_1(self):
    facts = _facts([ArgStat(1, "-", "/mnt/a")])
    err = io.StringIO()
    rc = remote.run("audit", ["/mnt/a"], 1, facts, _resolved(), FakeDaemon(final="failed"), io.StringIO(), err, sleep=lambda s: None)
    assert rc == 1 and "job 41 failed: boom" in err.getvalue()

  def test_no_wait_and_json(self):
    facts = _facts([ArgStat(1, "-", "/mnt/a"), ArgStat(2, "-", "/mnt/b")])
    out = io.StringIO()
    assert remote.run("convert", ["/mnt/a", "/mnt/b"], 1, facts, _resolved(), FakeDaemon(), out, io.StringIO(), wait=False) == 0
    assert out.getvalue() == "41\n42\n"
    out = io.StringIO()
    remote.run("convert", ["/mnt/a"], 1, facts, _resolved(), FakeDaemon(), out, io.StringIO(), wait=False, as_json=True)
    assert json.loads(out.getvalue()) == {"job_id": 41, "kind": "convert", "path": "/mnt/a", "status": "queued"}
    out = io.StringIO()
    remote.run("repair", ["/mnt/a"], 1, facts, _resolved(), FakeDaemon(), out, io.StringIO(), as_json=True, sleep=lambda s: None)
    line = json.loads(out.getvalue())
    assert line["status"] == "completed" and "line 1" not in out.getvalue()

  def test_ctrl_c_detaches(self):
    facts = _facts([ArgStat(1, "-", "/mnt/a")])

    def interrupt(_s):
      raise KeyboardInterrupt

    err = io.StringIO()
    rc = remote.run("repair", ["/mnt/a"], 1, facts, _resolved(), FakeDaemon(), io.StringIO(), err, sleep=interrupt)
    assert rc == 130
    assert "detached: job 41 (sma jobs show 41)" in err.getvalue()

  def test_directory_convert_and_empty(self):
    facts = _facts([ArgStat(1, "-", "/mnt/tv")])
    daemon = FakeDaemon(submit_response={"status": "queued", "queued": [{"job_id": 7}, {"job_id": 8}], "duplicates": [{"job_id": 9}]})
    out = io.StringIO()
    remote.run("convert", ["/mnt/tv"], 1, facts, _resolved(), daemon, out, io.StringIO(), wait=False)
    assert out.getvalue() == "7\n8\n9\n"
    err = io.StringIO()
    daemon = FakeDaemon(submit_response={"status": "empty", "message": "No media files found in directory"})
    assert remote.run("convert", ["/mnt/tv"], 1, facts, _resolved(), daemon, io.StringIO(), err) == 0
    assert "No media files found" in err.getvalue()

  def test_duplicate_is_followed(self):
    facts = _facts([ArgStat(1, "-", "/mnt/a")])
    daemon = FakeDaemon(submit_response={"status": "duplicate", "job_id": 5})
    err = io.StringIO()
    assert remote.run("repair", ["/mnt/a"], 1, facts, _resolved(), daemon, io.StringIO(), err, sleep=lambda s: None) == 0
    assert "already queued as job 5" in err.getvalue()

  @pytest.mark.parametrize(
    "command,args,match",
    [("repair", ["-n"], "needs a PATH"), ("preview", ["/a", "/b"], "takes one PATH"), ("repair", ["/a", "--config=x"], "is set by sma")],
  )
  def test_split_errors(self, command, args, match):
    facts = _facts([ArgStat(i, "-", "/x%d" % i) for i in range(1, 4)])
    with pytest.raises(UsageError, match=match):
      remote.split(command, args, 1, facts)


# ---------------------------------------------------------------- manage


def _cfg_env(tmp_path):
  d = tmp_path / "home"
  d.mkdir(exist_ok=True)
  return {"SMA_CLIENT_HOME": str(d)}


class TestTargetAndMap:
  def test_add_with_key_from_stdin_never_printed(self, tmp_path):
    env = _cfg_env(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    rc = manage.target(["add", "nas", "http://nas:8585", "--api-key-stdin"], clientconfig.load(env), env, out, err, stdin=io.StringIO("s3cret\n"))
    assert rc == 0
    assert clientconfig.load(env).targets["nas"].api_key == "s3cret"
    assert "s3cret" not in out.getvalue() + err.getvalue()
    out = io.StringIO()
    manage.target(["list"], clientconfig.load(env), env, out, io.StringIO())
    assert "api key set" in out.getvalue() and "s3cret" not in out.getvalue()
    assert stat.S_IMODE(os.stat(clientconfig.config_path(env)).st_mode) == 0o600

  def test_add_keeps_existing_key_and_mappings(self, tmp_path):
    env = _cfg_env(tmp_path)
    manage.target(["add", "nas", "http://a", "--api-key-stdin"], clientconfig.load(env), env, io.StringIO(), io.StringIO(), stdin=io.StringIO("k\n"))
    manage.mapping(["add", "nas", "/Volumes/M/", "/mnt/M"], clientconfig.load(env), env, io.StringIO(), io.StringIO())
    manage.target(["add", "nas", "http://b"], clientconfig.load(env), env, io.StringIO(), io.StringIO())
    t = clientconfig.load(env).targets["nas"]
    assert (t.url, t.api_key, t.mappings) == ("http://b", "k", [("/Volumes/M", "/mnt/M")])

  @pytest.mark.parametrize(
    "args,stdin,match",
    [
      (["add", "nas"], "", "target add NAME URL"),
      (["add", "nas", "ftp://x"], "", "http:// or https://"),
      (["add", "nas", "http://x", "--api-key", "k"], "", "never from the command line"),
      (["add", "nas", "http://x", "--api-key-stdin"], "\n", "read no key"),
      (["use", "nope"], "", "unknown target"),
      (["use"], "", "target use NAME"),
      (["rm", "nope"], "", "target rm NAME"),
      (["frob"], "", "unknown target command"),
    ],
  )
  def test_target_usage_errors(self, tmp_path, args, stdin, match):
    env = _cfg_env(tmp_path)
    with pytest.raises(UsageError, match=match):
      manage.target(args, clientconfig.load(env), env, io.StringIO(), io.StringIO(), stdin=io.StringIO(stdin))

  def test_use_and_rm(self, tmp_path):
    env = _cfg_env(tmp_path)
    manage.target(["add", "nas", "http://nas"], clientconfig.load(env), env, io.StringIO(), io.StringIO())
    manage.target(["use", "nas"], clientconfig.load(env), env, io.StringIO(), io.StringIO())
    out = io.StringIO()
    manage.target(["list"], clientconfig.load(env), env, out, io.StringIO())
    assert out.getvalue().startswith("  local") and "* nas" in out.getvalue()
    manage.target(["use", "local"], clientconfig.load(env), env, io.StringIO(), io.StringIO())
    assert clientconfig.load(env).default_target is None
    manage.target(["use", "nas"], clientconfig.load(env), env, io.StringIO(), io.StringIO())
    manage.target(["rm", "nas"], clientconfig.load(env), env, io.StringIO(), io.StringIO())
    cfg = clientconfig.load(env)
    assert "nas" not in cfg.targets and cfg.default_target is None

  def test_map_list_rm_and_errors(self, tmp_path):
    env = _cfg_env(tmp_path)
    manage.target(["add", "nas", "http://nas"], clientconfig.load(env), env, io.StringIO(), io.StringIO())
    manage.mapping(["add", "nas", "/a", "/b"], clientconfig.load(env), env, io.StringIO(), io.StringIO())
    out = io.StringIO()
    manage.mapping(["list", "nas"], clientconfig.load(env), env, out, io.StringIO())
    assert out.getvalue().split() == ["nas", "/a", "->", "/b"]
    manage.mapping(["rm", "nas", "/a/"], clientconfig.load(env), env, io.StringIO(), io.StringIO())
    assert clientconfig.load(env).targets["nas"].mappings == []
    for args, match in ((["add", "box", "/a", "/b"], "unknown target"), (["add", "nas", "a", "/b"], "absolute"), (["add", "nas"], "map add"), (["x"], "unknown map")):
      with pytest.raises(UsageError, match=match):
        manage.mapping(args, clientconfig.load(env), env, io.StringIO(), io.StringIO())


class TestJobs:
  def _run(self, monkeypatch, args, daemon=None, json_out=False):
    daemon = daemon or FakeDaemon()
    monkeypatch.setattr(routing, "client_for", lambda target, facts, timeout=10.0: daemon)
    out, err = io.StringIO(), io.StringIO()
    rc = manage.jobs(args, ClientConfig(), _facts(), {"json": json_out}, out, err, sleep=lambda s: None)
    return rc, out.getvalue(), err.getvalue(), daemon

  def test_list_show_logs_cancel(self, monkeypatch):
    rc, out, _e, _d = self._run(monkeypatch, [])
    assert rc == 0 and "repair" in out and "/m/a" in out
    rc, out, _e, _d = self._run(monkeypatch, ["list", "--status", "failed"], json_out=True)
    assert json.loads(out)[0]["status"] == "failed"
    rc, out, _e, _d = self._run(monkeypatch, ["show", "7"])
    assert "id:" in out and "kind:" in out
    rc, out, _e, _d = self._run(monkeypatch, ["show", "7"], json_out=True)
    assert json.loads(out)["id"] == 7
    rc, out, _e, _d = self._run(monkeypatch, ["logs", "7"])
    assert out == "line 1\n"
    rc, out, _e, _d = self._run(monkeypatch, ["logs", "7", "-f"], daemon=FakeDaemon(final="failed"))
    assert rc == 1 and out == "line 1\nline 2\n"
    rc, _o, err, daemon = self._run(monkeypatch, ["cancel", "7"])
    assert daemon.cancelled == [7] and "cancelled job 7" in err

  def test_errors(self, monkeypatch):
    with pytest.raises(UsageError, match="needs a job id"):
      self._run(monkeypatch, ["show"])
    with pytest.raises(UsageError, match="must be a number"):
      self._run(monkeypatch, ["show", "x"])
    with pytest.raises(UsageError, match="unknown jobs command"):
      self._run(monkeypatch, ["frob"])
    with pytest.raises(Unreachable, match="no daemon"):
      self._run(monkeypatch, [], daemon=FakeDaemon(healthy=False))


# ---------------------------------------------------------------- commands


class TestCommandsRouting:
  @pytest.fixture
  def env(self, tmp_path):
    plan = tmp_path / "plan"
    plan.mkdir()
    (plan / "argstat").write_text("0\t-\t/x\n1\td\t/mnt/tv\n2\td\t/mnt/tv\n3\t-\t/z\n")
    return {
      "SMA_PLAN_DIR": str(plan),
      "SMA_CONFIG_DIR": str(tmp_path / "none"),
      "SMA_CLIENT_HOME": str(tmp_path / "home"),
      "SMA_HOST_PWD": "/home/dave",
      "SMA_HOST_UID": "1000",
      "SMA_HOST_GID": "1000",
      "SMA_HOME": "/opt/sma",
      "SMA_IMAGE": "img:1",
    }

  def test_reachable_daemon_gets_the_job(self, env):
    daemon = FakeDaemon()
    out, err = io.StringIO(), io.StringIO()
    rc = commands.run(["--no-wait", "repair", "/mnt/tv"], env=env, out=out, err=err, client=daemon)
    assert rc == 0 and daemon.submitted == [("repair", "/mnt/tv", [])]
    assert not os.path.exists(os.path.join(env["SMA_PLAN_DIR"], "plan"))

  def test_local_flag_plans_throwaway(self, env):
    daemon = FakeDaemon()
    err = io.StringIO()
    assert commands.run(["--local", "repair", "/mnt/tv"], env=env, out=io.StringIO(), err=err, client=daemon) == 0
    assert daemon.submitted == [] and os.path.exists(os.path.join(env["SMA_PLAN_DIR"], "plan"))
    assert "competes for the GPU" in err.getvalue()

  def test_unreachable_implicit_falls_back(self, env):
    assert commands.run(["repair", "/mnt/tv"], env=env, out=io.StringIO(), err=io.StringIO(), client=FakeDaemon(healthy=False)) == 0
    assert os.path.exists(os.path.join(env["SMA_PLAN_DIR"], "plan"))

  def test_target_and_map_commands_need_no_host_facts(self, env, tmp_path):
    del env["SMA_IMAGE"]
    assert commands.run(["target", "add", "nas", "http://nas"], env=env, out=io.StringIO(), err=io.StringIO()) == 0
    assert commands.run(["map", "list"], env=env, out=io.StringIO(), err=io.StringIO()) == 0

  def test_jobs_command(self, env, monkeypatch):
    monkeypatch.setattr(routing, "client_for", lambda target, facts, timeout=10.0: FakeDaemon())
    out = io.StringIO()
    assert commands.run(["jobs"], env=env, out=out, err=io.StringIO()) == 0
    assert "repair" in out.getvalue()

  def test_main_maps_keyboard_interrupt(self, monkeypatch):
    def boom(argv, env=None):
      raise KeyboardInterrupt

    monkeypatch.setattr(commands, "run", boom)
    assert commands.main(["x"]) == 130


def test_facts_dataclass_is_replaceable():
  assert dataclasses.replace(_facts(), os="Darwin").os == "Darwin"
