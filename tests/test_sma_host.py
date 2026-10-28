"""Tests for setup/sma-host.sh (the `sma` bootstrap) against a fake docker.

The fake docker records every call (one argument per line, so arguments are
compared verbatim) and emulates the client container: for `run ... -m
resources.cli` it saves argstat and the SMA_* environment, writes $FAKE_PLAN
into the mounted /plan directory and exits $FAKE_CLIENT_RC.
"""

import os
import shutil
import stat
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SMA = os.path.join(ROOT, "setup", "sma-host.sh")

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="sh required")

FAKE_DOCKER = r"""#!/bin/sh
{ echo "--CALL--"; for a in "$@"; do printf '%s\n' "$a"; done; } >> "$FAKE_LOG"
case "$1" in
  info) [ "${FAKE_INFO_RC:-0}" = 0 ] || exit "$FAKE_INFO_RC"; printf '%s\n' "${FAKE_SECOPTS:-[name=seccomp]}"; exit 0 ;;
  inspect) [ -n "${FAKE_DAEMON:-}" ] && { printf '%s\n' "$FAKE_DAEMON"; exit 0; }; exit 1 ;;
  create) echo cid123; exit 0 ;;
  cp) printf '#!/bin/sh\necho upgraded-script\n' > "$3"; exit 0 ;;
  pull) exit "${FAKE_PULL_RC:-0}" ;;
esac
client=0; plan=""; prev=""
for a in "$@"; do
  [ "$prev" = "-m" ] && [ "$a" = "resources.cli" ] && client=1
  case "$prev:$a" in -v:*:/plan|-v:*:/plan:z) plan="${a%%:/plan*}" ;; esac
  prev="$a"
done
if [ "$client" = 1 ] && [ -n "${FAKE_REAL_CLIENT:-}" ]; then
  # Run the real client: everything after "-m resources.cli" is its argv.
  seen=0; for a in "$@"; do
    shift
    if [ "$seen" = 1 ]; then set -- "$@" "$a"; fi
    [ "$prev2" = "-m" ] && [ "$a" = "resources.cli" ] && seen=1
    prev2="$a"
  done
  cd "$FAKE_ROOT" && SMA_PLAN_DIR="$plan" SMA_CLIENT_HOME="$FAKE_DIR/none" SMA_CONFIG_DIR="$FAKE_DIR/none" \
    exec "$FAKE_PYTHON" -m resources.cli "$@"
fi
if [ "$client" = 1 ]; then
  cp "$plan/argstat" "$FAKE_DIR/argstat.copy"
  env | grep '^SMA_' | sort > "$FAKE_DIR/env.copy"
  [ -n "${FAKE_PLAN:-}" ] && cp "$FAKE_PLAN" "$plan/plan"
  exit "${FAKE_CLIENT_RC:-0}"
fi
for a in "$@"; do
  [ -n "${FAKE_FAIL_ON:-}" ] && [ "$a" = "$FAKE_FAIL_ON" ] && exit 7
done
exit "${FAKE_STEP_RC:-0}"
"""


def _exe(path, text):
  path.write_text(text)
  path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def env(tmp_path):
  bindir = tmp_path / "bin"
  bindir.mkdir()
  _exe(bindir / "docker", FAKE_DOCKER)
  _exe(bindir / "nvidia-smi", "#!/bin/sh\nexit 1\n")
  (tmp_path / "tmp").mkdir()
  (tmp_path / "home").mkdir()
  (tmp_path / "work").mkdir()
  (tmp_path / "calls.log").write_text("")
  e = {k: v for k, v in os.environ.items() if not k.startswith(("SMA_", "SUDO_"))}
  e.pop("DOCKER_BIN", None)
  e.update(
    PATH=str(bindir) + os.pathsep + os.environ["PATH"],
    HOME=str(tmp_path / "home"),
    TMPDIR=str(tmp_path / "tmp"),
    SMA_HOME=str(tmp_path / "opt"),
    SMA_DEV_DRI=str(tmp_path / "nodri"),
    FAKE_LOG=str(tmp_path / "calls.log"),
    FAKE_DIR=str(tmp_path),
  )
  return e


def _run(env, *args, script=SMA, cwd=None, **extra):
  return subprocess.run(
    ["sh", script, *args],
    capture_output=True,
    text=True,
    env=dict(env, **extra),
    stdin=subprocess.DEVNULL,
    cwd=cwd or os.path.join(env["FAKE_DIR"], "work"),
    check=False,
  )


def _calls(env):
  with open(env["FAKE_LOG"]) as f:
    text = f.read()
  return [chunk.split("\n")[:-1] for chunk in text.split("--CALL--\n")[1:]]


def _client_call(env):
  calls = [c for c in _calls(env) if c[0] == "run" and "resources.cli" in c]
  assert len(calls) == 1, calls
  return calls[0]


def _steps(env):
  return [c for c in _calls(env) if c[0] not in ("info", "inspect") and "resources.cli" not in c]


def _plan(tmp_path, *steps, header="sma-plan 1"):
  lines = [header]
  for step in steps:
    lines.append("step docker")
    lines.extend(step)
    lines.append("end")
  path = tmp_path / "fake.plan"
  path.write_text("\n".join(lines) + "\n")
  return str(path)


def _read(tmp_path, name):
  return (tmp_path / name).read_text()


def test_client_container_invocation(env, tmp_path):
  (tmp_path / "opt" / "config").mkdir(parents=True)
  r = _run(env, "repair", "/mnt/tv")
  assert r.returncode == 0, r.stderr
  call = _client_call(env)
  assert call[:2] == ["run", "--rm"]
  assert call[-5:] == ["ghcr.io/newdave/sma-ng:latest", "-m", "resources.cli", "repair", "/mnt/tv"]
  assert call[call.index("--entrypoint") + 1] == "python"
  assert call[call.index("-w") + 1] == "/app"
  assert call[call.index("--user") + 1] == "%d:%d" % (os.getuid(), os.getgid())
  mounts = [call[i + 1] for i, a in enumerate(call) if a == "-v"]
  assert any(m.endswith(":/plan") for m in mounts)
  assert str(tmp_path / "home" / ".config" / "sma") + ":/home-config" in mounts
  assert str(tmp_path / "opt" / "config") + ":/app/config:ro" in mounts
  for name in ("SMA_HOST_PWD", "SMA_HOST_UID", "SMA_IMAGE", "SMA_HOME", "SMA_HOST_DRI_GIDS"):
    assert name in call


def test_argstat_is_verbatim_and_typed(env, tmp_path):
  work = tmp_path / "work"
  (work / "My Show").mkdir()
  (work / "a.mkv").write_text("x")
  args = ["repair", "My Show", "a.mkv", "$(touch pwned)", "x;y", "*", "/nope/../mnt/z"]
  r = _run(env, *args)
  assert r.returncode == 0, r.stderr
  real = os.path.realpath(work)
  rows = [line.split("\t") for line in _read(tmp_path, "argstat.copy").splitlines()]
  assert rows == [
    ["0", "-", real + "/repair"],
    ["1", "d", real + "/My Show"],
    ["2", "f", real + "/a.mkv"],
    ["3", "-", real + "/$(touch pwned)"],
    ["4", "-", real + "/x;y"],
    ["5", "-", real + "/*"],
    ["6", "-", "/mnt/z"],
  ]
  assert not (work / "pwned").exists()
  assert _client_call(env)[-len(args) :] == args


def test_host_facts_environment(env, tmp_path):
  (tmp_path / "opt" / "logs").mkdir(parents=True)
  bindir = tmp_path / "bin"
  _exe(bindir / "nvidia-smi", "#!/bin/sh\nexit 0\n")
  dri = tmp_path / "dri"
  dri.mkdir()
  _exe(bindir / "ls", "#!/bin/sh\nprintf 'total 0\\ncrw-rw---- 1 0 993 226, 128 Jan 1 renderD128\\ncrw-rw---- 1 0 44 226, 0 Jan 1 card0\\n'\n")
  r = _run(env, "codecs", SMA_DEV_DRI=str(dri), FAKE_SECOPTS="[name=seccomp name=rootless name=selinux]", SMA_IMAGE="img:9")
  assert r.returncode == 0, r.stderr
  facts = dict(line.split("=", 1) for line in _read(tmp_path, "env.copy").splitlines())
  assert facts["SMA_HOST_DRI_GIDS"] == "44,993"
  assert facts["SMA_HOST_NVIDIA"] == "1"
  assert facts["SMA_HOST_ROOTLESS"] == "1" and facts["SMA_HOST_SELINUX"] == "1"
  assert facts["SMA_HOST_LOGS_WRITABLE"] == "1" and facts["SMA_HOST_CONFIG"] == "0"
  assert facts["SMA_HOST_TTY"] == "0" and facts["SMA_IMAGE"] == "img:9"
  call = _client_call(env)
  assert "--user" not in call  # rootless
  assert any(a.endswith(":/plan:z") for a in call)


def test_image_from_config_file(env, tmp_path):
  cfg = tmp_path / "home" / ".config" / "sma"
  cfg.mkdir(parents=True)
  (cfg / "image").write_text("ghcr.io/newdave/sma-ng:3.2.21\n")
  _run(env, "codecs")
  assert "ghcr.io/newdave/sma-ng:3.2.21" in _client_call(env)


def test_plan_steps_run_in_order_with_verbatim_args(env, tmp_path):
  plan = _plan(
    tmp_path,
    ["arg pull", "arg img:1"],
    ["arg run", "arg --rm", "arg -v", "arg /m/My Show:/m/My Show", "arg img:1", "arg python", "arg $(id) ;*", "arg "],
  )
  r = _run(env, "convert", "x", FAKE_PLAN=plan, FAKE_STEP_RC="9")
  assert r.returncode == 9  # the final step's code (exec'd)
  steps = _steps(env)
  assert steps[0] == ["pull", "img:1"]
  assert steps[1] == ["run", "--rm", "-v", "/m/My Show:/m/My Show", "img:1", "python", "$(id) ;*", ""]
  assert os.listdir(tmp_path / "tmp") == []  # plan dir removed


def test_failed_step_aborts(env, tmp_path):
  plan = _plan(tmp_path, ["arg run", "arg bad"], ["arg run", "arg never"])
  r = _run(env, "convert", "x", FAKE_PLAN=plan, FAKE_FAIL_ON="bad")
  assert r.returncode == 7
  assert _steps(env) == [["run", "bad"]]


def test_optmount_only_when_directory_exists(env, tmp_path):
  (tmp_path / "exists").mkdir()
  plan = _plan(tmp_path, ["arg run", "optmount %s" % (tmp_path / "exists"), "optmount /definitely/missing", "arg img"])
  assert _run(env, "repair", "x", FAKE_PLAN=plan).returncode == 0
  ex = str(tmp_path / "exists")
  assert _steps(env)[0] == ["run", "-v", "%s:%s" % (ex, ex), "img"]


def test_optmount_selinux_adds_z(env, tmp_path):
  (tmp_path / "exists").mkdir()
  plan = _plan(tmp_path, ["arg run", "optmount %s" % (tmp_path / "exists"), "arg img"])
  _run(env, "repair", "x", FAKE_PLAN=plan, FAKE_SECOPTS="[name=selinux]")
  ex = str(tmp_path / "exists")
  assert _steps(env)[0][2] == "%s:%s:z" % (ex, ex)


def test_plan_version_mismatch_exits_5(env, tmp_path):
  plan = _plan(tmp_path, ["arg run"], header="sma-plan 2")
  r = _run(env, "repair", "x", FAKE_PLAN=plan)
  assert r.returncode == 5
  assert "plan version 'sma-plan 2' unsupported; run sma upgrade" in r.stderr
  assert _steps(env) == []


@pytest.mark.parametrize("lines,match", [(["arg system", "arg prune"], "not allowed"), (["bogus line"], "unknown plan line")])
def test_bad_plan_refused(env, tmp_path, lines, match):
  r = _run(env, "repair", "x", FAKE_PLAN=_plan(tmp_path, lines))
  assert r.returncode == 5
  assert match in r.stderr
  assert _steps(env) == []


def test_empty_step_refused(env, tmp_path):
  r = _run(env, "repair", "x", FAKE_PLAN=_plan(tmp_path, []))
  assert r.returncode == 5


def test_client_error_code_passed_through_without_steps(env, tmp_path):
  r = _run(env, "convert", "gone.mkv", FAKE_CLIENT_RC="2", FAKE_PLAN=_plan(tmp_path, ["arg run"]))
  assert r.returncode == 2
  assert _steps(env) == []
  assert os.listdir(tmp_path / "tmp") == []


def test_no_plan_exits_0(env):
  r = _run(env, "help")
  assert r.returncode == 0
  assert _steps(env) == []


def test_version_prints_host_line_then_runs_client(env):
  r = _run(env, "version")
  assert r.returncode == 0
  assert r.stdout.startswith("sma host command: dev\n")
  assert _client_call(env)[-1] == "version"


def test_docker_unreachable_exits_3(env):
  r = _run(env, "codecs", FAKE_INFO_RC="1")
  assert r.returncode == 3
  assert "cannot talk to Docker" in r.stderr


def test_daemon_inspect_written_when_present(env, tmp_path):
  # The fake client copies argstat only; daemon.json presence is visible through the inspect call.
  _run(env, "codecs", FAKE_DAEMON='[{"Name": "/sma-ng"}]')
  assert ["inspect", "sma-ng"] in _calls(env)


def test_upgrade_replaces_script_and_pins_image(env, tmp_path):
  installed = tmp_path / "bin" / "sma"
  shutil.copy(SMA, installed)
  r = _run(env, "upgrade", "3.2.23", script=str(installed))
  assert r.returncode == 0, r.stderr
  assert "upgraded to ghcr.io/newdave/sma-ng:3.2.23" in r.stdout
  calls = _calls(env)
  assert ["pull", "ghcr.io/newdave/sma-ng:3.2.23"] in calls
  assert ["create", "ghcr.io/newdave/sma-ng:3.2.23"] in calls
  assert ["rm", "cid123"] in calls
  assert "upgraded-script" in installed.read_text()
  assert os.access(installed, os.X_OK)
  assert (tmp_path / "home" / ".config" / "sma" / "image").read_text() == "ghcr.io/newdave/sma-ng:3.2.23\n"


def test_upgrade_without_tag_repulls_current(env, tmp_path):
  installed = tmp_path / "bin" / "sma"
  shutil.copy(SMA, installed)
  r = _run(env, "upgrade", script=str(installed), SMA_IMAGE="registry:5000/sma:1.0")
  assert r.returncode == 0, r.stderr
  assert ["pull", "registry:5000/sma:1.0"] in _calls(env)


def test_upgrade_tag_on_registry_with_port(env, tmp_path):
  installed = tmp_path / "bin" / "sma"
  shutil.copy(SMA, installed)
  _run(env, "upgrade", "2.0", script=str(installed), SMA_IMAGE="registry:5000/sma:1.0")
  assert ["pull", "registry:5000/sma:2.0"] in _calls(env)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_upgrade_unwritable_gives_sudo_hint(env, tmp_path):
  ro = tmp_path / "ro"
  ro.mkdir()
  installed = ro / "sma"
  shutil.copy(SMA, installed)
  ro.chmod(0o555)
  try:
    r = _run(env, "upgrade", "3.2.23", script=str(installed))
  finally:
    ro.chmod(0o755)
  assert r.returncode == 2
  assert "run: sudo sma upgrade 3.2.23" in r.stderr
  assert not [c for c in _calls(env) if c[0] == "pull"]


def test_upgrade_pull_failure(env, tmp_path):
  installed = tmp_path / "bin" / "sma"
  shutil.copy(SMA, installed)
  r = _run(env, "upgrade", script=str(installed), FAKE_PULL_RC="1")
  assert r.returncode == 1
  assert "upgraded-script" not in installed.read_text()


def test_end_to_end_with_real_client(env, tmp_path):
  import sys

  work = tmp_path / "work"
  (work / "My Show; $(x)").mkdir()
  r = _run(env, "repair", "-n", "My Show; $(x)", FAKE_REAL_CLIENT="1", FAKE_ROOT=ROOT, FAKE_PYTHON=sys.executable)
  assert r.returncode == 0, r.stderr
  (step,) = _steps(env)
  target = os.path.realpath(work / "My Show; $(x)")
  assert step[:2] == ["run", "--rm"]
  assert "%s:%s" % (target, target) in step
  assert step[-5:] == ["run", "python", "repair.py", "-n", target]


def test_end_to_end_missing_path(env, tmp_path):
  import sys

  r = _run(env, "convert", "gone.mkv", FAKE_REAL_CLIENT="1", FAKE_ROOT=ROOT, FAKE_PYTHON=sys.executable)
  assert r.returncode == 2
  assert r.stderr == "sma: gone.mkv: no such file or directory\n"
  assert _steps(env) == []


def test_linux_client_uses_host_network(env, tmp_path):
  _exe(tmp_path / "bin" / "uname", "#!/bin/sh\necho Linux\n")
  _run(env, "repair", "x")
  call = _client_call(env)
  assert call[call.index("--network") + 1] == "host"


def test_darwin_client_keeps_default_network(env, tmp_path):
  _exe(tmp_path / "bin" / "uname", "#!/bin/sh\necho Darwin\n")
  _run(env, "repair", "x")
  assert "--network" not in _client_call(env)


def test_stdin_only_attached_for_api_key(env):
  _run(env, "repair", "x")
  assert "-i" not in _client_call(env)[: _client_call(env).index("--entrypoint")]
  open(env["FAKE_LOG"], "w").close()
  _run(env, "target", "add", "nas", "http://nas:8585", "--api-key-stdin")
  call = _client_call(env)
  assert "-i" in call[: call.index("--entrypoint")]


def test_script_is_executable_and_posix():
  assert os.access(SMA, os.X_OK)
  with open(SMA) as f:
    assert f.readline().strip() == "#!/bin/sh"
