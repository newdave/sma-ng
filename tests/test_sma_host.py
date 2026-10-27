"""Tests for setup/sma-host.sh (the `sma` Docker host command) against a fake docker."""

import os
import shutil
import stat
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SMA = os.path.join(ROOT, "setup", "sma-host.sh")
ENTRYPOINT = "/usr/local/bin/entrypoint.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="sh required")

FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_LOG"
case "$1" in
  inspect)
    case "$3" in
      *Mounts*) printf '%s' "${FAKE_MOUNTS:-}" ;;
      *Running*) printf '%s\\n' "${FAKE_RUNNING:-true}" ;;
      *) printf 'state\\n' ;;
    esac
    ;;
  compose) printf '%s\\n' "${FAKE_COMPOSE_ID:-}" ;;
  exec) exit "${FAKE_EXEC_RC:-0}" ;;
esac
exit 0
"""

MOUNTS = "/opt/sma/config|/config\n/mnt|/mnt\n/transcode|/transcode\n"


@pytest.fixture
def env(tmp_path):
  bindir = tmp_path / "bin"
  bindir.mkdir()
  docker = bindir / "docker"
  docker.write_text(FAKE_DOCKER)
  docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
  log = tmp_path / "docker.log"
  log.write_text("")
  e = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"])
  for k in ("SMA_CONTAINER", "CONTAINER", "SMA_COMPOSE_SERVICE", "DOCKER_BIN"):
    e.pop(k, None)
  e.update(FAKE_LOG=str(log), FAKE_MOUNTS=MOUNTS, SMA_COMPOSE_DIR=str(tmp_path / "nocompose"))
  return e


def _run(env, *args, stdin=subprocess.DEVNULL, cwd=None, **extra):
  return subprocess.run(
    ["sh", SMA, *args],
    capture_output=True,
    text=True,
    env=dict(env, **extra),
    stdin=stdin,
    cwd=cwd,
    check=False,
  )


def _calls(env):
  with open(env["FAKE_LOG"]) as f:
    return [line.rstrip("\n") for line in f if line.strip()]


def _exec_call(env):
  calls = [c for c in _calls(env) if c.startswith("exec ")]
  assert len(calls) == 1, calls
  return calls[0]


@pytest.mark.parametrize(
  "args,tail",
  [
    (("convert", "/mnt/tv/a.mkv"), "python manual.py -i /mnt/tv/a.mkv -a"),
    (("convert", "/mnt/tv/a.mkv", "-p"), "python manual.py -i /mnt/tv/a.mkv -a -p"),
    (("preview", "/mnt/tv/a.mkv"), "python manual.py -i /mnt/tv/a.mkv -oo"),
    (("audit", "/mnt/tv"), "python manual.py --audit -i /mnt/tv"),
    (("repair", "-n", "/mnt/tv", "/transcode/x.mp4"), "python repair.py -n /mnt/tv /transcode/x.mp4"),
    (("rename", "/mnt/tv/a.mkv", "--dry-run"), "python rename.py /mnt/tv/a.mkv --dry-run"),
    (("manual", "-i", "/mnt/a.mkv", "-oo"), "python manual.py -i /mnt/a.mkv -oo"),
    (("codecs",), "python manual.py -cl"),
    (("smoke",), "python daemon.py --smoke-test"),
    (("shell", "-c", "id"), "bash -c id"),
  ],
)
def test_subcommand_maps_to_entrypoint_run(env, args, tail):
  r = _run(env, *args)
  assert r.returncode == 0, r.stderr
  assert _exec_call(env) == "exec sma-ng %s run %s" % (ENTRYPOINT, tail)


def test_repair_skips_config_value_and_nonpath_values(env):
  r = _run(env, "repair", "-c", "/config/sma-ng.yml", "/mnt/tv")
  assert r.returncode == 0, r.stderr
  assert _exec_call(env).endswith("run python repair.py -c /config/sma-ng.yml /mnt/tv")


def test_no_tty_no_stdin_has_no_flags(env):
  _run(env, "codecs")
  assert _exec_call(env).startswith("exec sma-ng ")


def test_piped_stdin_adds_i_only(env):
  _run(env, "codecs", stdin=subprocess.PIPE)
  assert _exec_call(env).startswith("exec -i sma-ng ")


@pytest.mark.skipif(sys.platform == "win32", reason="pty required")
def test_tty_adds_it(env):
  import pty

  master, slave = pty.openpty()
  try:
    r = subprocess.run(["sh", SMA, "codecs"], env=env, stdin=slave, stdout=slave, stderr=subprocess.PIPE, check=False)
  finally:
    os.close(master)
    os.close(slave)
  assert r.returncode == 0
  assert _exec_call(env).startswith("exec -it sma-ng ")


def test_stdin_tty_but_stdout_piped_is_i_only(env):
  import pty

  master, slave = pty.openpty()
  try:
    subprocess.run(["sh", SMA, "codecs"], env=env, stdin=slave, stdout=subprocess.PIPE, check=False)
  finally:
    os.close(master)
    os.close(slave)
  assert _exec_call(env).startswith("exec -i sma-ng ")


def test_container_env_override(env):
  _run(env, "codecs", SMA_CONTAINER="custom")
  assert "inspect -f {{.State.Running}} custom" in _calls(env)
  assert _exec_call(env).startswith("exec custom ")


def test_container_resolved_via_compose(env, tmp_path):
  (tmp_path / "compose").mkdir()
  _run(env, "codecs", SMA_COMPOSE_DIR=str(tmp_path / "compose"), FAKE_COMPOSE_ID="abc123")
  calls = _calls(env)
  assert any(c.startswith("compose --project-directory") and c.endswith(" ps -q") for c in calls)
  assert _exec_call(env).startswith("exec abc123 ")


def test_compose_service_override(env, tmp_path):
  (tmp_path / "compose").mkdir()
  _run(env, "codecs", SMA_COMPOSE_DIR=str(tmp_path / "compose"), SMA_COMPOSE_SERVICE="sma-intel", FAKE_COMPOSE_ID="zz")
  assert any(c.endswith("ps -q sma-intel") for c in _calls(env))


def test_falls_back_to_sma_ng_when_compose_empty(env, tmp_path):
  (tmp_path / "compose").mkdir()
  _run(env, "codecs", SMA_COMPOSE_DIR=str(tmp_path / "compose"), FAKE_COMPOSE_ID="")
  assert _exec_call(env).startswith("exec sma-ng ")


def test_no_running_container_exits_3(env):
  r = _run(env, "codecs", FAKE_RUNNING="false")
  assert r.returncode == 3
  assert r.stderr.count("\n") == 1
  assert "no running sma-ng container" in r.stderr
  assert not [c for c in _calls(env) if c.startswith("exec ")]


def test_exec_exit_code_passed_through(env):
  assert _run(env, "codecs", FAKE_EXEC_RC="7").returncode == 7


@pytest.mark.parametrize("path", ["/mnt/x", "/mnt", "/transcode/a/b.mkv", "/mnt/../mnt/y"])
def test_path_accepted(env, path):
  r = _run(env, "convert", path)
  assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("path", ["/mntx/y", "/downloads/x", "/opt/sma/config/x", "/config/x", "/", "/mnt/../etc/x"])
def test_path_rejected_with_exit_2(env, path):
  r = _run(env, "convert", path)
  assert r.returncode == 2
  assert r.stderr.count("\n") == 1
  assert "not visible at the same path" in r.stderr
  assert not [c for c in _calls(env) if c.startswith("exec ")]


def test_relative_path_outside_mounts_rejected(env, tmp_path):
  r = _run(env, "convert", "../x.mkv", cwd=tmp_path)
  assert r.returncode == 2
  assert "not visible" in r.stderr


def test_mount_with_different_destination_is_not_identity(env):
  r = _run(env, "convert", "/downloads/x.mkv", FAKE_MOUNTS="/mnt/unionfs/downloads|/downloads\n/mnt|/mnt\n")
  assert r.returncode == 2


def test_manual_checks_input_value_only(env):
  assert _run(env, "manual", "-i", "/downloads/a.mkv").returncode == 2
  assert _run(env, "manual", "-cl").returncode == 0


def test_usage_errors_exit_2(env):
  assert _run(env).returncode == 2
  assert _run(env, "bogus").returncode == 2
  assert _run(env, "convert").returncode == 2


def test_help_needs_no_docker(env):
  r = _run(env, "help")
  assert r.returncode == 0
  assert "usage: sma" in r.stdout
  assert _calls(env) == []


def test_logs_runs_docker_logs(env):
  _run(env, "logs", "--tail", "5")
  assert "logs -f sma-ng --tail 5" in _calls(env)


def test_script_is_executable_and_posix():
  assert os.access(SMA, os.X_OK)
  with open(SMA) as f:
    assert f.readline().strip() == "#!/bin/sh"
