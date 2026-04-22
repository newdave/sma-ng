"""Static checks for mise deploy task scripts.

These tests keep lightweight guardrails around the shell task wrappers without
requiring SSH, Docker, or remote hosts.
"""

import os

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


class TestDeployPostgresTasks:
    def test_pg_tasks_source_shared_library(self):
        for rel_path in (
            ".mise/tasks/deploy/docker-pg-restart",
            ".mise/tasks/deploy/docker-pg-recreate",
        ):
            text = _read(rel_path)
            assert 'source "$(dirname "$0")/lib.sh"' in text
