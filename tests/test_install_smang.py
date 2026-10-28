"""Tests for install-smang.sh (one-line CLI installer) against fake docker/curl."""

import os
import shutil
import stat
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER = os.path.join(ROOT, "install-smang.sh")

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="sh required")

FAKE_DOCKER = r"""#!/bin/sh
printf 'docker %s\n' "$*" >> "$FAKE_LOG"
case "$1" in
  info) exit "${FAKE_INFO_RC:-0}" ;;
  pull) exit "${FAKE_PULL_RC:-0}" ;;
  create) echo cid42 ;;
  cp) [ "${FAKE_CP_RC:-0}" = 0 ] || exit "$FAKE_CP_RC"; printf '#!/bin/sh\necho bootstrap\n' > "$3" ;;
  run) printf 'base:\n  converter: {}\n' ;;
esac
exit 0
"""

FAKE_CURL = r"""#!/bin/sh
printf 'curl %s\n' "$*" >> "$FAKE_LOG"
printf '{\n  "tag_name": "sma-ng-v3.2.23",\n  "name": "x"\n}\n'
"""


def _exe(path, text):
  path.write_text(text)
  path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def env(tmp_path):
  bindir = tmp_path / "bin"
  bindir.mkdir()
  _exe(bindir / "docker", FAKE_DOCKER)
  _exe(bindir / "curl", FAKE_CURL)
  _exe(bindir / "uname", '#!/bin/sh\necho "${FAKE_ARCH:-x86_64}"\n')
  (tmp_path / "home").mkdir()
  (tmp_path / "calls.log").write_text("")
  e = {k: v for k, v in os.environ.items() if not k.startswith(("SMA_", "SUDO_"))}
  e.update(
    PATH=str(bindir) + os.pathsep + os.environ["PATH"],
    HOME=str(tmp_path / "home"),
    FAKE_LOG=str(tmp_path / "calls.log"),
    DESTDIR=str(tmp_path / "root"),
    SMA_API_URL="https://api.test/repos/newdave/sma-ng",
    SUDO="",
  )
  return e


def _run(env, *args, piped=False, **extra):
  e = dict(env, **extra)
  if piped:  # curl ... | sh -s -- ARGS
    with open(INSTALLER) as f:
      return subprocess.run(["sh", "-s", "--", *args], stdin=f, capture_output=True, text=True, env=e, check=False)
  return subprocess.run(["sh", INSTALLER, *args], capture_output=True, text=True, env=e, check=False)


def _calls(env):
  with open(env["FAKE_LOG"]) as f:
    return [line.rstrip("\n") for line in f if line.strip()]


def _dest(env, *parts):
  return os.path.join(env["DESTDIR"], *[p.lstrip("/") for p in parts])


def test_fresh_install_when_piped(env):
  r = _run(env, piped=True)
  assert r.returncode == 0, r.stderr
  calls = _calls(env)
  assert "docker pull ghcr.io/newdave/sma-ng:latest" in calls
  assert "docker create ghcr.io/newdave/sma-ng:latest" in calls
  assert any(c.startswith("docker cp cid42:/usr/local/bin/sma-host ") for c in calls)
  assert "docker rm cid42" in calls
  sma = _dest(env, "/usr/local/bin/sma")
  assert os.access(sma, os.X_OK)
  with open(_dest(env, env["HOME"], ".config/sma/image")) as f:
    assert f.read() == "ghcr.io/newdave/sma-ng:latest\n"
  for d in ("config", "logs", "data"):
    assert os.path.isdir(_dest(env, "/opt/sma", d)), d
  with open(_dest(env, "/opt/sma/config/sma-ng.yml")) as f:
    assert f.read().startswith("base:")
  assert "docker run --rm --entrypoint cat ghcr.io/newdave/sma-ng:latest /app/setup/sma-ng.yml.sample" in calls
  assert "sma is installed" in r.stdout
  assert not any(c.startswith("docker compose") for c in calls)


def test_rerun_keeps_existing_config(env):
  assert _run(env).returncode == 0
  cfg = _dest(env, "/opt/sma/config/sma-ng.yml")
  with open(cfg, "w") as f:
    f.write("mine\n")
  open(env["FAKE_LOG"], "w").close()
  r = _run(env)
  assert r.returncode == 0, r.stderr
  with open(cfg) as f:
    assert f.read() == "mine\n"
  assert not any(c.startswith("docker run") for c in _calls(env))
  assert "Keeping existing" in r.stdout


def test_arm64_uses_latest_release(env):
  r = _run(env, FAKE_ARCH="aarch64")
  assert r.returncode == 0, r.stderr
  assert "curl -fsSL https://api.test/repos/newdave/sma-ng/releases/latest" in _calls(env)
  assert "docker pull ghcr.io/newdave/sma-ng:3.2.23" in _calls(env)


@pytest.mark.parametrize("how", ["flag", "env"])
def test_tag_override(env, how):
  r = _run(env, "--tag", "3.2.21", FAKE_ARCH="aarch64") if how == "flag" else _run(env, FAKE_ARCH="aarch64", SMA_IMAGE_TAG="3.2.21")
  assert r.returncode == 0, r.stderr
  assert "docker pull ghcr.io/newdave/sma-ng:3.2.21" in _calls(env)
  assert not any(c.startswith("curl") for c in _calls(env))


def test_bin_dir_option_and_path_warning(env, tmp_path):
  r = _run(env, "--bin-dir", "/opt/tools")
  assert r.returncode == 0, r.stderr
  assert os.access(_dest(env, "/opt/tools/sma"), os.X_OK)
  assert "/opt/tools is not on your PATH" in r.stderr


def test_without_root_falls_back_to_local_bin(env):
  r = _run(env, SUDO="none")
  assert r.returncode == 0, r.stderr
  assert os.access(_dest(env, env["HOME"], ".local/bin/sma"), os.X_OK)


def test_docker_missing_or_unreachable(env, tmp_path):
  r = _run(env, FAKE_INFO_RC="1")
  assert r.returncode == 1
  assert r.stderr.strip().startswith("error: cannot talk to Docker")
  os.remove(tmp_path / "bin" / "docker")
  r = _run(env, PATH=str(tmp_path / "bin") + os.pathsep + "/usr/bin:/bin")
  if shutil.which("docker", path="/usr/bin:/bin") is None:
    assert r.returncode == 1
    assert "Docker is required" in r.stderr


def test_image_without_sma_command(env):
  r = _run(env, FAKE_CP_RC="1")
  assert r.returncode == 1
  assert "pick a newer --tag" in r.stderr
  assert "docker rm cid42" in _calls(env)


def test_pull_failure_stops(env):
  r = _run(env, FAKE_PULL_RC="1")
  assert r.returncode != 0
  assert not os.path.exists(_dest(env, "/usr/local/bin/sma"))


def test_daemon_flag_not_available_yet(env):
  r = _run(env, "--daemon")
  assert r.returncode == 0, r.stderr
  assert "not available yet" in r.stderr


def test_unknown_option(env):
  r = _run(env, "--bogus")
  assert r.returncode == 1
  assert "unknown option" in r.stderr


def test_installer_is_posix_and_executable():
  assert os.access(INSTALLER, os.X_OK)
  with open(INSTALLER) as f:
    assert f.readline().strip() == "#!/bin/sh"
