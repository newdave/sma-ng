"""Static checks for mise deploy task scripts.

These tests keep lightweight guardrails around the shell task wrappers without
requiring SSH, Docker, or remote hosts.
"""

import os
import subprocess
import textwrap

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel_path):
    with open(os.path.join(PROJECT_ROOT, rel_path)) as f:
        return f.read()


class TestDeployDockerUpgradeTask:
    def test_sources_shared_library(self):
        text = _read(".mise/tasks/deploy/docker-upgrade")
        assert 'source "$(dirname "$0")/lib.sh"' in text

    def test_uses_shared_remote_runner_for_healthcheck(self):
        text = _read(".mise/tasks/deploy/docker-upgrade")
        assert 'while ! run_remote_command "$host" "$health_cmd"' in text

    def test_does_not_reference_legacy_ssh_opts_array(self):
        text = _read(".mise/tasks/deploy/docker-upgrade")
        assert "SSH_OPTS" not in text

    def test_init_helpers_return_success_for_normal_host_context(self, tmp_path):
        local_ini = tmp_path / ".local.ini"
        local_ini.write_text(
            textwrap.dedent(
                """
                [deploy]
                DEPLOY_DIR = /opt/sma
                SSH_PORT = 22
                DOCKER_PROFILE = intel

                [test@example]
                DOCKER_COMPOSE_DIR = /opt/sma/docker
                """
            ).strip()
            + "\n"
        )

        bash_script = textwrap.dedent(
            f"""
            set -euo pipefail
            cd {PROJECT_ROOT!r}
            source .mise/tasks/deploy/lib.sh
            LOCAL={str(local_ini)!r}
            init_host_context test@example
            init_docker_host_context test@example
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
            ".mise/tasks/deploy/docker-pg-restart",
            ".mise/tasks/deploy/docker-pg-recreate",
        ):
            text = _read(rel_path)
            assert 'source "$(dirname "$0")/lib.sh"' in text


class TestDeployConfigTask:
    def test_no_inline_python_heredocs_in_deploy_config(self):
        """deploy/config must not contain <<PY or <<PYREMOTE heredocs."""
        import re

        text = _read(".mise/tasks/deploy/config")
        # Match any heredoc delimiter starting with PY (case-insensitive)
        heredoc_pattern = re.compile(r"<<'?PYREMOTE|<<'?PY\b", re.IGNORECASE)
        matches = heredoc_pattern.findall(text)
        assert matches == [], f"Found {len(matches)} inline Python heredoc(s) in deploy/config: {matches}"

    def test_deploy_config_uses_lib_helpers_for_python(self):
        """deploy/config must delegate Python work via lib/ helper files."""
        text = _read(".mise/tasks/deploy/config")
        assert "lib/build_force_keys.py" in text
        assert "lib/ini_ensure_services.py" in text
        assert "lib/ini_stamp_credentials.py" in text
        assert "lib/stamp_ffmpeg.py" in text
        assert "lib/stamp_daemon.py" in text
        assert "lib/stamp_postprocess.py" in text

    def test_deploy_config_has_backup_step(self):
        """deploy/config must create a timestamped backup before mutating configs."""
        text = _read(".mise/tasks/deploy/config")
        assert ".backup/" in text

    def test_ini_merge_called_with_sort_and_deprecate(self):
        """sync_ini_keys must pass --sort and --deprecate to ini_merge.py."""
        text = _read(".mise/tasks/deploy/config")
        assert "--sort" in text
        assert "--deprecate" in text
