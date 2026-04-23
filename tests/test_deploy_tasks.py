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

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
            source .mise/shared/deploy/lib.sh
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
            ".mise/tasks/pgsql/restart",
            ".mise/tasks/pgsql/recreate",
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

        assert "pgsql:restart" in tasks
        assert "deploy:docker:pg:restart" not in tasks
        assert "deploy:docker:pg:restart" in tasks["pgsql:restart"]

        assert "pgsql:recreate" in tasks
        assert "deploy:docker:pg:recreate" not in tasks
        assert "deploy:docker:pg:recreate" in tasks["pgsql:recreate"]

        assert "systemd:restart" in tasks
        assert "systemd:force:restart" not in tasks
        assert "systemd:force:restart" in tasks["systemd:restart"]

        assert "setup:deps" in tasks["setup:deps:base"]

    def test_nested_task_names_are_exposed_to_completion(self, tmp_path):
        if not shutil.which("usage"):
            raise AssertionError("usage CLI is required for mise shell completions")

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
        assert "config:audit" in completions

    def test_alias_task_names_are_exposed_to_completion(self, tmp_path):
        if not shutil.which("usage"):
            raise AssertionError("usage CLI is required for mise shell completions")

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

        assert "config:audit" in tasks
        assert "deploy:config:audit" not in tasks
        assert "deploy:config:audit" in tasks["config:audit"]

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
        assert "lib/build_force_keys.py" in text
        assert "lib/ini_ensure_services.py" in text
        assert "lib/ini_stamp_credentials.py" in text
        assert "lib/stamp_ffmpeg.py" in text
        assert "lib/stamp_daemon.py" in text
        assert "lib/stamp_postprocess.py" in text

    def test_deploy_config_has_backup_step(self):
        """deploy/config must create a timestamped backup before mutating configs."""
        text = _read(".mise/tasks/config/roll")
        assert ".backup/" in text

    def test_ini_merge_called_with_sort_and_deprecate(self):
        """sync_ini_keys must pass --sort and --deprecate to ini_merge.py."""
        text = _read(".mise/tasks/config/roll")
        assert "--sort" in text
        assert "--deprecate" in text
