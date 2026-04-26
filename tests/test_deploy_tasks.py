"""Static checks for mise deploy task scripts.

These tests keep lightweight guardrails around the shell task wrappers without
requiring SSH, Docker, or remote hosts.
"""

import json
import os
import re
import shutil
import subprocess
import textwrap
from base64 import b64encode

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(PROJECT_ROOT, "venv", "bin", "python")


def _read(rel_path):
  with open(os.path.join(PROJECT_ROOT, rel_path)) as f:
    return f.read()


class TestDeployDockerUpgradeTask:
  def test_sources_shared_library(self):
    text = _read(".mise/tasks/deploy/docker")
    assert 'source "$(dirname "$0")/../../shared/deploy/lib.sh"' in text

  def test_uses_shared_remote_runner_for_healthcheck(self):
    text = _read(".mise/tasks/deploy/docker")
    assert 'while ! run_remote_command "$host" "$health_cmd"' in text

  def test_does_not_reference_legacy_ssh_opts_array(self):
    text = _read(".mise/tasks/deploy/docker")
    assert "SSH_OPTS" not in text

  def test_init_helpers_return_success_for_normal_host_context(self, tmp_path):
    local_yml = tmp_path / "local.yml"
    local_yml.write_text(
      textwrap.dedent(
        """
        deploy:
          hosts:
            - sma-node1
          deploy_dir: /opt/sma
          ssh_port: 22
          docker_profile: intel

        hosts:
          sma-node1:
            address: 192.168.1.10
            user: deploy
            docker_compose_dir: /opt/sma/docker
        """
      ).strip()
      + "\n"
    )

    bash_script = textwrap.dedent(
      f"""
            set -euo pipefail
            cd {PROJECT_ROOT!r}
            source venv/bin/activate
            source .mise/shared/deploy/lib.sh
            LOCAL={str(local_yml)!r}
            init_host_context sma-node1
            init_docker_host_context sma-node1
            printf 'dir=%s\ncompose_dir=%s\ncompose_cmd=%s\n' "$dir" "$compose_dir" "$compose_cmd"
            """
    )

    result = subprocess.run(
      ["bash", "-lc", bash_script],
      cwd=PROJECT_ROOT,
      capture_output=True,
      text=True,
      check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "dir=/opt/sma" in result.stdout
    assert "compose_dir=/opt/sma/docker" in result.stdout
    assert "compose_cmd=docker compose" in result.stdout


class TestDeployPostgresTasks:
  def test_pg_tasks_source_shared_library(self):
    for rel_path in (
      ".mise/tasks/pg/restart",
      ".mise/tasks/pg/recreate",
    ):
      text = _read(rel_path)
      assert 'source "$(dirname "$0")/../../shared/deploy/lib.sh"' in text


class TestClusterTasks:
  def test_cluster_tasks_source_shared_library(self):
    for rel_path in (
      ".mise/tasks/cluster/stop",
      ".mise/tasks/cluster/start",
      ".mise/tasks/cluster/restart",
    ):
      text = _read(rel_path)
      assert 'source "$(dirname "$0")/../../shared/deploy/lib.sh"' in text


class TestMiseTaskLayout:
  def test_mise_toml_does_not_define_inline_tasks(self):
    text = _read("mise.toml")
    assert "[tasks." not in text

  def test_task_root_contains_only_group_directories(self):
    task_root = os.path.join(PROJECT_ROOT, ".mise/tasks")
    entries = os.listdir(task_root)
    files = [entry for entry in entries if os.path.isfile(os.path.join(task_root, entry))]
    assert files == []

  def test_task_names_use_colons_not_underscores_or_dashes(self):
    result = subprocess.run(
      ["mise", "tasks"],
      cwd=PROJECT_ROOT,
      capture_output=True,
      text=True,
      check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    task_names = [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    assert all("_" not in task and "-" not in task for task in task_names), task_names

  def test_preferred_task_names_and_aliases_are_registered(self):
    result = subprocess.run(
      ["mise", "tasks", "--json"],
      cwd=PROJECT_ROOT,
      capture_output=True,
      text=True,
      check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    tasks = {task["name"]: set(task["aliases"]) for task in json.loads(result.stdout)}

    assert "test:run" in tasks
    assert "dev:test:run" not in tasks
    assert {"test", "dev:test:run"} <= tasks["test:run"]

    assert "daemon:start" in tasks
    assert "media:daemon:start" not in tasks
    assert "media:daemon:start" in tasks["daemon:start"]

    assert "test:openapi" in tasks
    assert "dev:openapi" not in tasks
    assert "dev:openapi" in tasks["test:openapi"]

    assert "test:lint" in tasks
    assert "dev:lint:check" not in tasks
    assert "dev:lint:check" in tasks["test:lint"]

    assert "dev:lint" in tasks
    assert "dev:lint:fix" not in tasks
    assert "dev:lint:fix" in tasks["dev:lint"]

    assert "deploy:docker" in tasks
    assert "deploy:docker:upgrade" not in tasks
    assert "deploy:docker:upgrade" in tasks["deploy:docker"]

    assert "cluster:stop" in tasks
    assert "cluster:start" in tasks
    assert "cluster:restart" in tasks

    assert "pg:restart" in tasks
    assert "deploy:docker:pg:restart" not in tasks
    assert "deploy:docker:pg:restart" in tasks["pg:restart"]

    assert "pg:recreate" in tasks
    assert "deploy:docker:pg:recreate" not in tasks
    assert "deploy:docker:pg:recreate" in tasks["pg:recreate"]

    assert "setup:deps" in tasks["setup:deps:base"]

    # systemd-related tasks have been removed in favour of Docker-only deployments.
    assert "systemd:restart" not in tasks
    assert "systemd:install" not in tasks
    assert "systemd:uninstall" not in tasks

  def test_nested_task_names_are_exposed_to_completion(self, tmp_path):
    if not shutil.which("usage"):
      pytest.skip("usage CLI is not installed in this test environment")

    spec_file = tmp_path / "mise-usage.spec"
    with spec_file.open("w") as f:
      subprocess.run(
        ["mise", "usage"],
        cwd=PROJECT_ROOT,
        stdout=f,
        text=True,
        check=True,
      )

    result = subprocess.run(
      [
        "usage",
        "complete-word",
        "--shell",
        "zsh",
        "-f",
        str(spec_file),
        "--",
        "mise",
        "run",
        "config:",
      ],
      cwd=PROJECT_ROOT,
      capture_output=True,
      text=True,
      check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    completions = [re.split(r"(?<!\\):", line, maxsplit=1)[0].replace("\\:", ":") for line in result.stdout.splitlines()]
    assert "config:roll" in completions
    assert "config:sample" in completions

  def test_alias_task_names_are_exposed_to_completion(self, tmp_path):
    if not shutil.which("usage"):
      pytest.skip("usage CLI is not installed in this test environment")

    spec_file = tmp_path / "mise-usage.spec"
    with spec_file.open("w") as f:
      subprocess.run(
        ["mise", "usage"],
        cwd=PROJECT_ROOT,
        stdout=f,
        text=True,
        check=True,
      )

    result = subprocess.run(
      [
        "usage",
        "complete-word",
        "--shell",
        "zsh",
        "-f",
        str(spec_file),
        "--",
        "mise",
        "run",
        "setup:",
      ],
      cwd=PROJECT_ROOT,
      capture_output=True,
      text=True,
      check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    completions = [re.split(r"(?<!\\):", line, maxsplit=1)[0].replace("\\:", ":") for line in result.stdout.splitlines()]
    assert "setup:deps" in completions
    assert "setup:deps:base" in completions

  def test_shared_deploy_library_is_not_a_task(self):
    assert not os.path.exists(os.path.join(PROJECT_ROOT, ".mise/tasks/deploy/lib.sh"))
    assert os.path.exists(os.path.join(PROJECT_ROOT, ".mise/shared/deploy/lib.sh"))
    assert not os.path.exists(os.path.join(PROJECT_ROOT, ".mise/tasks/deploy/lib"))
    assert os.path.exists(os.path.join(PROJECT_ROOT, ".mise/shared/deploy/lib"))

  def test_deploy_config_tasks_are_config_aliases(self):
    result = subprocess.run(
      ["mise", "tasks", "--json"],
      cwd=PROJECT_ROOT,
      capture_output=True,
      text=True,
      check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    tasks = {task["name"]: set(task["aliases"]) for task in json.loads(result.stdout)}

    assert "config:roll" in tasks
    assert "deploy:config:roll" not in tasks
    assert "deploy:config:roll" in tasks["config:roll"]

  def test_mise_tasks_do_not_call_make(self):
    task_root = os.path.join(PROJECT_ROOT, ".mise/tasks")
    shared_root = os.path.join(PROJECT_ROOT, ".mise/shared")
    make_call = re.compile(r"\bmake(?:\s|$)")

    offenders = []
    for root in (task_root, shared_root):
      for dirpath, _, filenames in os.walk(root):
        if "__pycache__" in dirpath.split(os.sep):
          continue
        for filename in filenames:
          path = os.path.join(dirpath, filename)
          if filename.endswith((".pyc", ".py")):
            continue
          rel_path = os.path.relpath(path, PROJECT_ROOT)
          for line_no, line in enumerate(_read(rel_path).splitlines(), start=1):
            if make_call.search(line):
              offenders.append(f"{rel_path}:{line_no}: {line}")

    assert offenders == []

  def test_makefile_exposes_install_mise(self):
    text = _read("Makefile")
    assert "install-mise:" in text
    assert "mise run" in text


class TestDeployConfigTask:
  def test_no_inline_python_heredocs_in_deploy_config(self):
    """deploy/config must not contain <<PY or <<PYREMOTE heredocs."""
    import re

    text = _read(".mise/tasks/config/roll")
    # Match any heredoc delimiter starting with PY (case-insensitive)
    heredoc_pattern = re.compile(r"<<'?PYREMOTE|<<'?PY\b", re.IGNORECASE)
    matches = heredoc_pattern.findall(text)
    assert matches == [], f"Found {len(matches)} inline Python heredoc(s) in deploy/config: {matches}"

  def test_deploy_config_uses_lib_helpers_for_python(self):
    """deploy/config must delegate Python work via lib/ helper files."""
    text = _read(".mise/tasks/config/roll")
    assert "lib/stamp_ffmpeg.py" in text
    assert "lib/stamp_daemon.py" in text
    assert "lib/stamp_postprocess.py" in text

  def test_deploy_config_depends_on_deploy_mise(self):
    """config:roll must depend on deploy:mise so remote helper code is current."""
    text = _read(".mise/tasks/config/roll")
    assert '#MISE depends=["deploy:mise"]' in text

  def test_deploy_config_has_backup_step(self):
    """deploy/config must create a timestamped backup before mutating configs."""
    text = _read(".mise/tasks/config/roll")
    assert ".backup/" in text

  def test_yaml_merge_called_with_sort_and_deprecate(self):
    """sync_yaml_keys must pass --sort and --deprecate to yaml_merge.py."""
    text = _read(".mise/tasks/config/roll")
    assert "yaml_merge.py" in text
    assert "--sort" in text
    assert "--deprecate" in text
    assert "--additions" in text


class TestDeployLibHelpers:
  def _run_stamp_daemon(self, deploy_dir, services, *, api_key="", db_url="", ffmpeg_dir="", node_name=""):
    services_b64 = b64encode(json.dumps(services).encode()).decode()
    args = [
      PYTHON,
      ".mise/shared/deploy/lib/stamp_daemon.py",
      str(deploy_dir),
      b64encode(api_key.encode()).decode() if api_key else "",
      b64encode(db_url.encode()).decode() if db_url else "",
      b64encode(ffmpeg_dir.encode()).decode() if ffmpeg_dir else "",
      b64encode(node_name.encode()).decode() if node_name else "",
      "",
      "",
      "",
      services_b64,
    ]
    return subprocess.run(args, cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)

  def test_stamp_daemon_writes_kebab_case_credentials(self, tmp_path):
    deploy_dir = tmp_path / "deploy"
    config_dir = deploy_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "sma-ng.yml").write_text("daemon: {}\n")
    (config_dir / "daemon.env").write_text("# existing\n")

    result = self._run_stamp_daemon(deploy_dir, {}, api_key="abc123", db_url="postgresql://x", ffmpeg_dir="/usr/local/bin")
    assert result.returncode == 0, result.stderr or result.stdout
    content = (config_dir / "sma-ng.yml").read_text()
    assert "api-key: abc123" in content
    assert "db-url: postgresql://x" in content
    assert "ffmpeg-dir: /usr/local/bin" in content

  def test_stamp_daemon_writes_nested_service_credentials(self, tmp_path):
    deploy_dir = tmp_path / "deploy"
    config_dir = deploy_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "sma-ng.yml").write_text("daemon: {}\nservices: {}\n")
    (config_dir / "daemon.env").write_text("# existing\n")

    services = {
      "sonarr": {
        "main": {"url": "http://sonarr.example.com", "apikey": "tv-key", "path": "/media/tv", "profile": "rq"},
        "kids": {"url": "http://sonarr-kids.example.com", "apikey": "kids-key", "path": "/media/tv/Kids", "profile": "lq"},
      },
      "plex": {
        "main": {"url": "http://plex.example.com:32400", "token": "plex-token"},
      },
    }
    result = self._run_stamp_daemon(deploy_dir, services)
    assert result.returncode == 0, result.stderr or result.stdout
    content = (config_dir / "sma-ng.yml").read_text()

    # Service blocks must end up under services.<type>.<instance>, not at the root.
    assert "services:" in content
    assert "sonarr:" in content
    assert "main:" in content
    assert "url: http://sonarr.example.com" in content
    assert "apikey: tv-key" in content
    assert "kids:" in content
    assert "apikey: kids-key" in content
    assert "token: plex-token" in content
    # Routing-only metadata must NOT leak into the service block.
    assert "path: /media/tv\n" not in content.split("services:")[1]
    # ...but it MUST appear in the routing rules below.
    assert "match: /media/tv" in content
    assert "match: /media/tv/Kids" in content

  def test_stamp_daemon_builds_routing_longest_match_first(self, tmp_path):
    deploy_dir = tmp_path / "deploy"
    config_dir = deploy_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "sma-ng.yml").write_text("daemon: {}\nservices: {}\n")
    (config_dir / "daemon.env").write_text("# existing\n")

    services = {
      "sonarr": {
        "main": {"url": "http://x", "path": "/media/tv", "profile": "rq"},
        "kids": {"url": "http://y", "path": "/media/tv/Kids", "profile": "lq"},
      },
    }
    result = self._run_stamp_daemon(deploy_dir, services)
    assert result.returncode == 0, result.stderr or result.stdout

    content = (config_dir / "sma-ng.yml").read_text()
    # The longer prefix must appear in the routing list before the shorter one.
    kids_idx = content.find("/media/tv/Kids")
    tv_idx = content.find("match: /media/tv\n")
    assert kids_idx != -1 and tv_idx != -1
    assert kids_idx < tv_idx, "longest-match routing rule must be listed first"
    # The routing entry must reference services as <type>.<instance>.
    assert "- sonarr.main" in content
    assert "- sonarr.kids" in content

  def test_stamp_daemon_writes_sma_node_name_to_daemon_env(self, tmp_path):
    deploy_dir = tmp_path / "deploy"
    config_dir = deploy_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "daemon.env").write_text("# existing\n")
    result = self._run_stamp_daemon(deploy_dir, {}, node_name="sma-slave0")
    assert result.returncode == 0, result.stderr or result.stdout
    content = (config_dir / "daemon.env").read_text()
    assert "SMA_NODE_NAME=sma-slave0" in content


class TestDeployMiseTask:
  def test_deploy_mise_task_syncs_repo_control_plane(self):
    text = _read(".mise/tasks/deploy/mise")
    assert '#MISE depends=["deploy:check"]' in text
    assert "mkdir -p $dir/.mise" in text
    assert '.mise/ "$ssh_target:$dir/.mise/"' in text

  def test_deploy_mise_does_not_use_quoted_cfg_variable(self):
    text = _read(".mise/tasks/deploy/mise")
    assert '"$CFG"' not in text

  def test_deploy_restart_sources_shared_lib(self):
    text = _read(".mise/tasks/deploy/restart")
    assert 'source "$(dirname "$0")/../../shared/deploy/lib.sh"' in text

  def test_no_task_uses_quoted_cfg_variable(self):
    task_root = os.path.join(PROJECT_ROOT, ".mise/tasks")
    offenders = []
    for dirpath, _, filenames in os.walk(task_root):
      for filename in filenames:
        path = os.path.join(dirpath, filename)
        rel_path = os.path.relpath(path, PROJECT_ROOT)
        text = _read(rel_path)
        if '"$CFG"' in text:
          offenders.append(rel_path)
    assert offenders == [], f"Tasks still use quoted $CFG: {offenders}"

  def test_remote_deploy_tasks_depend_on_deploy_mise(self):
    for rel_path in (
      ".mise/tasks/config/roll",
      ".mise/tasks/deploy/sync",
      ".mise/tasks/deploy/docker",
      ".mise/tasks/cluster/stop",
      ".mise/tasks/cluster/start",
      ".mise/tasks/cluster/restart",
      ".mise/tasks/deploy/restart",
      ".mise/tasks/deploy/login",
      ".mise/tasks/pg/restart",
      ".mise/tasks/pg/recreate",
    ):
      text = _read(rel_path)
      assert '#MISE depends=["deploy:mise"]' in text, rel_path


# ── Shared test fixture ───────────────────────────────────────────────────────

FIXTURE_LOCAL_YML = (
  textwrap.dedent(
    """
  deploy:
    hosts:
      - sma-node1
      - sma-node2
    deploy_dir: /opt/sma
    ssh_port: 22
    docker_profile: intel
    ffmpeg_dir: /usr/bin

  hosts:
    sma-node1:
      address: 192.168.1.10
      user: deploy
      docker_profile: intel-pg
      ffmpeg_dir: /usr/local/bin
    sma-node2:
      address: 192.168.1.11
      user: deploy

  daemon:
    api_key: test-api-key

  services:
    sonarr:
      main:
        url: http://sonarr.example.com
        apikey: test-sonarr-key
        path: /media/tv
        profile: rq
      kids:
        url: http://sonarr-kids.example.com
        apikey: test-kids-key
        path: /media/tv/Kids
        profile: lq
    plex:
      main:
        url: http://plex.example.com:32400
        token: test-plex-token
  """
  ).strip()
  + "\n"
)


@pytest.fixture()
def fixture_local_yml(tmp_path):
  p = tmp_path / "local.yml"
  p.write_text(FIXTURE_LOCAL_YML)
  return p


# ── TestLocalConfig ───────────────────────────────────────────────────────────


class TestLocalConfig:
  """Tests for scripts/local-config.py resolution logic."""

  def _run(self, fixture_local_yml, *args):
    result = subprocess.run(
      ["python3", "scripts/local-config.py", str(fixture_local_yml), *args],
      cwd=PROJECT_ROOT,
      capture_output=True,
      text=True,
      check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout

  def test_deploy_hosts_returns_space_separated_list(self, fixture_local_yml):
    out = self._run(fixture_local_yml, "deploy", "hosts", "")
    assert out == "sma-node1 sma-node2"

  def test_deploy_deploy_dir_returns_global_default(self, fixture_local_yml):
    out = self._run(fixture_local_yml, "deploy", "deploy_dir", "")
    assert out == "/opt/sma"

  def test_host_inherits_deploy_ffmpeg_dir(self, fixture_local_yml):
    out = self._run(fixture_local_yml, "sma-node2", "ffmpeg_dir", "")
    assert out == "/usr/bin"

  def test_host_overrides_deploy_ffmpeg_dir(self, fixture_local_yml):
    out = self._run(fixture_local_yml, "sma-node1", "ffmpeg_dir", "")
    assert out == "/usr/local/bin"

  def test_host_override_docker_profile(self, fixture_local_yml):
    out = self._run(fixture_local_yml, "sma-node1", "docker_profile", "")
    assert out == "intel-pg"

  def test_host_inherits_docker_profile_from_deploy(self, fixture_local_yml):
    out = self._run(fixture_local_yml, "sma-node2", "docker_profile", "")
    assert out == "intel"

  def test_daemon_api_key_resolves(self, fixture_local_yml):
    out = self._run(fixture_local_yml, "daemon", "api_key", "")
    assert out == "test-api-key"

  def test_service_section_resolves_main_instance_by_default(self, fixture_local_yml):
    out = self._run(fixture_local_yml, "sonarr", "apikey", "")
    assert out == "test-sonarr-key"

  def test_service_section_resolves_named_instance_via_dot(self, fixture_local_yml):
    out = self._run(fixture_local_yml, "sonarr.kids", "apikey", "")
    assert out == "test-kids-key"

  def test_service_section_resolves_plex_token(self, fixture_local_yml):
    out = self._run(fixture_local_yml, "plex", "token", "")
    assert out == "test-plex-token"

  def test_missing_key_returns_default(self, fixture_local_yml):
    out = self._run(fixture_local_yml, "sma-node1", "ssh_key", "fallback")
    assert out == "fallback"

  def test_host_address_resolves(self, fixture_local_yml):
    out = self._run(fixture_local_yml, "sma-node1", "address", "")
    assert out == "192.168.1.10"

  def test_host_user_resolves(self, fixture_local_yml):
    out = self._run(fixture_local_yml, "sma-node1", "user", "")
    assert out == "deploy"


# ── TestInitHostContext ───────────────────────────────────────────────────────


class TestInitHostContext:
  """Tests for init_host_context and init_docker_host_context in lib.sh."""

  def _run_bash(self, fixture_local_yml, script_body):
    bash_script = textwrap.dedent(
      f"""
      set -euo pipefail
      cd {PROJECT_ROOT!r}
      source venv/bin/activate
      source .mise/shared/deploy/lib.sh
      LOCAL={str(fixture_local_yml)!r}
      {script_body}
      """
    )
    return subprocess.run(
      ["bash", "-lc", bash_script],
      cwd=PROJECT_ROOT,
      capture_output=True,
      text=True,
      check=False,
    )

  def test_ssh_target_resolves_to_user_at_address(self, fixture_local_yml):
    result = self._run_bash(
      fixture_local_yml,
      "init_host_context sma-node1\nprintf '%s' \"$ssh_target\"",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "deploy@192.168.1.10"

  def test_deploy_dir_resolves_from_deploy_section(self, fixture_local_yml):
    result = self._run_bash(
      fixture_local_yml,
      "init_host_context sma-node1\nprintf '%s' \"$dir\"",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "/opt/sma"

  def test_ffmpeg_dir_host_override_takes_precedence(self, fixture_local_yml):
    result = self._run_bash(
      fixture_local_yml,
      "init_host_context sma-node1\nprintf '%s' \"$ffmpeg_dir\"",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "/usr/local/bin"

  def test_ffmpeg_dir_falls_back_to_deploy_default(self, fixture_local_yml):
    result = self._run_bash(
      fixture_local_yml,
      "init_host_context sma-node2\nprintf '%s' \"$ffmpeg_dir\"",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "/usr/bin"

  def test_docker_profile_host_override(self, fixture_local_yml):
    result = self._run_bash(
      fixture_local_yml,
      "init_docker_host_context sma-node1\nprintf '%s' \"$profile\"",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "intel-pg"

  def test_docker_profile_inherits_deploy_default(self, fixture_local_yml):
    result = self._run_bash(
      fixture_local_yml,
      "init_docker_host_context sma-node2\nprintf '%s' \"$profile\"",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "intel"

  def test_second_host_ssh_target_resolves(self, fixture_local_yml):
    result = self._run_bash(
      fixture_local_yml,
      "init_host_context sma-node2\nprintf '%s' \"$ssh_target\"",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "deploy@192.168.1.11"


# ── TestLcHelper ─────────────────────────────────────────────────────────────


class TestLcHelper:
  """Tests for the lc() shorthand in lib.sh."""

  def _run_bash(self, fixture_local_yml, script_body):
    bash_script = textwrap.dedent(
      f"""
      set -euo pipefail
      cd {PROJECT_ROOT!r}
      source venv/bin/activate
      source .mise/shared/deploy/lib.sh
      LOCAL={str(fixture_local_yml)!r}
      {script_body}
      """
    )
    return subprocess.run(
      ["bash", "-lc", bash_script],
      cwd=PROJECT_ROOT,
      capture_output=True,
      text=True,
      check=False,
    )

  def test_lc_resolves_deploy_hosts(self, fixture_local_yml):
    result = self._run_bash(fixture_local_yml, 'printf "%s" "$(lc deploy hosts "")"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == "sma-node1 sma-node2"

  def test_lc_resolves_daemon_api_key(self, fixture_local_yml):
    result = self._run_bash(fixture_local_yml, 'printf "%s" "$(lc daemon api_key "")"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == "test-api-key"

  def test_lc_returns_default_for_missing_key(self, fixture_local_yml):
    result = self._run_bash(fixture_local_yml, 'printf "%s" "$(lc deploy nonexistent_key fallback)"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == "fallback"


# ── TestDeployCheck ───────────────────────────────────────────────────────────


class TestDeployCheck:
  """Tests for .mise/tasks/deploy/check."""

  def test_check_fails_when_local_yml_missing(self):
    text = _read(".mise/tasks/deploy/check")
    assert '! -f "$LOCAL"' in text
    assert "exit 1" in text

  def test_check_fails_when_hosts_empty(self):
    text = _read(".mise/tasks/deploy/check")
    assert '-z "$DEPLOY_HOSTS"' in text
    assert "exit 1" in text

  def test_check_prints_deployment_targets(self):
    text = _read(".mise/tasks/deploy/check")
    assert "Deployment targets:" in text

  def test_deploy_check_uses_hosts_key_not_deploy_hosts(self):
    text = _read(".mise/tasks/deploy/check")
    assert "deploy hosts" in text
    assert "deploy DEPLOY_HOSTS" not in text

  def test_deploy_check_does_not_use_quoted_cfg(self):
    text = _read(".mise/tasks/deploy/check")
    assert '"$CFG"' not in text
