"""Tests for docker-entrypoint.sh — config seeding and ffmpeg path patching.

Runs the real shell script against a temporary directory so behaviour is
verified end-to-end without a Docker daemon.  Requires /bin/sh (present on
Linux and macOS).
"""

import os
import re
import shutil
import subprocess
import tempfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRYPOINT = os.path.join(PROJECT_ROOT, "docker", "docker-entrypoint.sh")
SETUP_DIR = os.path.join(PROJECT_ROOT, "setup")

# Sample files the entrypoint is expected to seed
SEEDED_FILES = {
    "autoProcess.ini": "autoProcess.ini.sample",
    "daemon.json": "daemon.json.sample",
    "daemon.env": "daemon.env.sample",
    "custom.py": "custom.py.sample",
}


# ── helpers ───────────────────────────────────────────────────────────────────


def _run(config_dir, env_extra=None, cmd="true"):
    """Execute the entrypoint script with an isolated CONFIG_DIR.

    Returns (returncode, stdout, stderr).
    """
    env = os.environ.copy()
    env["CONFIG_DIR"] = config_dir
    # Point SETUP_DIR inside the script to the real project setup/
    # by creating a symlink called 'setup' inside a fake /app hierarchy.
    # Simpler: override via a wrapper that sets the variable — but the script
    # hard-codes SETUP_DIR=/app/setup, so we create that path in a temp tree.
    env["PATH"] = env.get("PATH", "/usr/bin:/bin")
    if env_extra:
        env.update(env_extra)

    # Build a minimal fake /app/setup pointing at the real samples
    fake_app = tempfile.mkdtemp(prefix="sma-app-")
    try:
        fake_setup = os.path.join(fake_app, "setup")
        os.symlink(SETUP_DIR, fake_setup)

        # Patch the SETUP_DIR path in the script so it points at our fake_app
        with open(ENTRYPOINT) as fh:
            script = fh.read()
        script = script.replace('SETUP_DIR="/app/setup"', f'SETUP_DIR="{fake_setup}"')

        patched = os.path.join(fake_app, "entrypoint.sh")
        with open(patched, "w") as fh:
            fh.write(script)
        os.chmod(patched, 0o755)

        result = subprocess.run(
            ["/bin/sh", patched, cmd],
            env=env,
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout, result.stderr
    finally:
        shutil.rmtree(fake_app, ignore_errors=True)


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def empty_config(tmp_path):
    """A completely empty config directory (simulates first run)."""
    d = tmp_path / "config"
    d.mkdir()
    return str(d)


@pytest.fixture
def populated_config(tmp_path):
    """A config directory that already has all expected files (simulates restart)."""
    d = tmp_path / "config"
    d.mkdir()
    for dst_name in SEEDED_FILES:
        (d / dst_name).write_text(f"# existing {dst_name}\n")
    return str(d)


# ── script-level static checks ────────────────────────────────────────────────


class TestEntrypointScript:
    def test_script_exists(self):
        assert os.path.isfile(ENTRYPOINT)

    def test_script_is_executable(self):
        assert os.access(ENTRYPOINT, os.X_OK)

    def test_has_shebang(self):
        with open(ENTRYPOINT) as fh:
            first = fh.readline()
        assert first.startswith("#!/bin/sh")

    def test_uses_set_e(self):
        with open(ENTRYPOINT) as fh:
            content = fh.read()
        assert "set -e" in content

    def test_ends_with_exec(self):
        with open(ENTRYPOINT) as fh:
            content = fh.read()
        assert 'exec "$@"' in content

    def test_references_all_sample_files(self):
        with open(ENTRYPOINT) as fh:
            content = fh.read()
        for sample in SEEDED_FILES.values():
            assert sample in content, f"Missing reference to {sample}"

    def test_references_defaults_dir(self):
        with open(ENTRYPOINT) as fh:
            content = fh.read()
        assert "defaults" in content

    def test_patches_ffmpeg_path(self):
        with open(ENTRYPOINT) as fh:
            content = fh.read()
        assert "ffmpeg" in content
        assert "ffprobe" in content

    def test_syntax_valid(self):
        result = subprocess.run(["/bin/sh", "-n", ENTRYPOINT], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


# ── first-run seeding ─────────────────────────────────────────────────────────


class TestFirstRunSeeding:
    def test_seeds_all_config_files(self, empty_config):
        rc, _, _ = _run(empty_config)
        assert rc == 0
        for dst_name in SEEDED_FILES:
            assert os.path.isfile(os.path.join(empty_config, dst_name)), f"Expected {dst_name} to be seeded"

    def test_seeded_ini_matches_sample(self, empty_config):
        _run(empty_config)
        seeded = open(os.path.join(empty_config, "autoProcess.ini")).read()
        sample = open(os.path.join(SETUP_DIR, "autoProcess.ini.sample")).read()
        # Content should be equal except for ffmpeg/ffprobe path substitution
        sample_stripped = re.sub(r"^(ffmpeg|ffprobe) = .*", "", sample, flags=re.M)
        seeded_stripped = re.sub(r"^(ffmpeg|ffprobe) = .*", "", seeded, flags=re.M)
        assert seeded_stripped == sample_stripped

    def test_seeded_json_is_valid(self, empty_config):
        import json

        _run(empty_config)
        with open(os.path.join(empty_config, "daemon.json")) as fh:
            parsed = json.load(fh)
        assert "default_config" in parsed

    def test_seeded_env_is_readable(self, empty_config):
        _run(empty_config)
        content = open(os.path.join(empty_config, "daemon.env")).read()
        assert len(content) > 0

    def test_logs_seeded_message_to_stderr(self, empty_config):
        _, _, stderr = _run(empty_config)
        assert "Seeded" in stderr or "seeded" in stderr.lower()


# ── idempotency — existing files must not be overwritten ─────────────────────


class TestIdempotency:
    def test_existing_ini_not_overwritten(self, populated_config):
        _run(populated_config)
        content = open(os.path.join(populated_config, "autoProcess.ini")).read()
        assert "# existing autoProcess.ini" in content

    def test_existing_json_not_overwritten(self, populated_config):
        _run(populated_config)
        content = open(os.path.join(populated_config, "daemon.json")).read()
        assert "# existing daemon.json" in content

    def test_existing_env_not_overwritten(self, populated_config):
        _run(populated_config)
        content = open(os.path.join(populated_config, "daemon.env")).read()
        assert "# existing daemon.env" in content

    def test_existing_custom_py_not_overwritten(self, populated_config):
        _run(populated_config)
        content = open(os.path.join(populated_config, "custom.py")).read()
        assert "# existing custom.py" in content

    def test_no_seeded_message_when_files_exist(self, populated_config):
        _, _, stderr = _run(populated_config)
        assert "Seeded" not in stderr


# ── defaults/ directory ───────────────────────────────────────────────────────


class TestDefaultsDirectory:
    def test_defaults_dir_created(self, empty_config):
        _run(empty_config)
        assert os.path.isdir(os.path.join(empty_config, "defaults"))

    def test_all_samples_written_to_defaults(self, empty_config):
        _run(empty_config)
        defaults_dir = os.path.join(empty_config, "defaults")
        for sample in SEEDED_FILES.values():
            assert os.path.isfile(os.path.join(defaults_dir, sample)), f"Missing defaults/{sample}"

    def test_defaults_match_source_samples(self, empty_config):
        _run(empty_config)
        defaults_dir = os.path.join(empty_config, "defaults")
        for sample in SEEDED_FILES.values():
            shipped = open(os.path.join(SETUP_DIR, sample)).read()
            written = open(os.path.join(defaults_dir, sample)).read()
            assert shipped == written, f"defaults/{sample} differs from source"

    def test_defaults_refreshed_even_when_config_exists(self, populated_config):
        """defaults/ must always reflect the latest shipped samples."""
        # Write a stale defaults file
        defaults_dir = os.path.join(populated_config, "defaults")
        os.makedirs(defaults_dir, exist_ok=True)
        stale = os.path.join(defaults_dir, "autoProcess.ini.sample")
        open(stale, "w").write("# stale content\n")

        _run(populated_config)

        refreshed = open(stale).read()
        assert "# stale content" not in refreshed


# ── ffmpeg path patching ──────────────────────────────────────────────────────


class TestFFmpegPatching:
    def test_default_ffmpeg_path_written_to_ini(self, empty_config):
        _run(empty_config)
        ini = open(os.path.join(empty_config, "autoProcess.ini")).read()
        assert "ffmpeg = /usr/local/bin/ffmpeg" in ini

    def test_default_ffprobe_path_written_to_ini(self, empty_config):
        _run(empty_config)
        ini = open(os.path.join(empty_config, "autoProcess.ini")).read()
        assert "ffprobe = /usr/local/bin/ffprobe" in ini

    def test_custom_ffmpeg_path_via_env(self, empty_config):
        _run(empty_config, env_extra={"SMA_FFMPEG": "/opt/bin/ffmpeg"})
        ini = open(os.path.join(empty_config, "autoProcess.ini")).read()
        assert "ffmpeg = /opt/bin/ffmpeg" in ini

    def test_custom_ffprobe_path_via_env(self, empty_config):
        _run(empty_config, env_extra={"SMA_FFPROBE": "/opt/bin/ffprobe"})
        ini = open(os.path.join(empty_config, "autoProcess.ini")).read()
        assert "ffprobe = /opt/bin/ffprobe" in ini

    def test_user_custom_ffmpeg_path_preserved(self, populated_config):
        """If the user has already set a custom path, the patch must not change it."""
        ini_path = os.path.join(populated_config, "autoProcess.ini")
        open(ini_path, "w").write("[Converter]\nffmpeg = /my/custom/ffmpeg\nffprobe = /my/custom/ffprobe\n")
        _run(populated_config)
        ini = open(ini_path).read()
        # The sed rule only replaces bare "ffmpeg" / "ffprobe" values, not absolute paths
        assert "/my/custom/ffmpeg" in ini
        assert "/my/custom/ffprobe" in ini


# ── exec hand-off ─────────────────────────────────────────────────────────────


class TestExecHandoff:
    def test_returns_zero_on_success(self, empty_config):
        rc, _, _ = _run(empty_config, cmd="true")
        assert rc == 0

    def test_propagates_nonzero_exit_code(self, empty_config):
        rc, _, _ = _run(empty_config, cmd="false")
        assert rc != 0

    def test_logs_config_ready_message(self, empty_config):
        _, _, stderr = _run(empty_config)
        assert "Config directory ready" in stderr or "ready" in stderr.lower()
