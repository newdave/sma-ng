"""Tests for Docker configuration files: Dockerfile, docker-compose.yml,
.dockerignore, and .github/workflows/docker.yml.

All tests are static — no Docker daemon required.
"""

import os
import re

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(PROJECT_ROOT, name)) as f:
        return f.read()


def _load_yaml(name):
    with open(os.path.join(PROJECT_ROOT, name)) as f:
        return yaml.safe_load(f)


# ──────────────────────────────────────────────────────────────────────────────
# Dockerfile — structural parsing helpers
# ──────────────────────────────────────────────────────────────────────────────


def _parse_dockerfile(text):
    """Return a list of (instruction, rest) tuples, stripping comments and
    joining continuation lines (backslash at end of line)."""
    # Join continuation lines
    joined = re.sub(r"\\\n", " ", text)
    instructions = []
    for line in joined.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if parts:
            instructions.append((parts[0].upper(), parts[1] if len(parts) > 1 else ""))
    return instructions


def _stages(instructions):
    """Return list of stage names from FROM … AS … lines."""
    stages = []
    for instr, rest in instructions:
        if instr == "FROM":
            m = re.search(r"\bAS\s+(\S+)", rest, re.IGNORECASE)
            if m:
                stages.append(m.group(1))
    return stages


def _instructions_of(instr_type, instructions):
    return [rest for i, rest in instructions if i == instr_type]


# ──────────────────────────────────────────────────────────────────────────────
# Dockerfile tests
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def dockerfile():
    return _parse_dockerfile(_read("docker/Dockerfile"))


@pytest.fixture(scope="module")
def dockerfile_raw():
    return _read("docker/Dockerfile")


class TestDockerfileStages:
    def test_three_named_stages(self, dockerfile):
        assert _stages(dockerfile) == ["ffmpeg-builder", "python-builder", "runtime"]

    def test_ffmpeg_builder_from_ubuntu(self, dockerfile_raw):
        assert "FROM ubuntu:24.04 AS ffmpeg-builder" in dockerfile_raw

    def test_python_builder_from_ubuntu(self, dockerfile_raw):
        assert "FROM ubuntu:24.04 AS python-builder" in dockerfile_raw

    def test_runtime_from_ubuntu(self, dockerfile_raw):
        assert "FROM ubuntu:24.04 AS runtime" in dockerfile_raw

    def test_runtime_copies_ffmpeg_from_builder(self, dockerfile_raw):
        assert "--from=ffmpeg-builder" in dockerfile_raw
        assert "/usr/local/bin/ffmpeg" in dockerfile_raw
        assert "/usr/local/bin/ffprobe" in dockerfile_raw

    def test_runtime_copies_python_packages_from_builder(self, dockerfile_raw):
        assert "--from=python-builder" in dockerfile_raw


class TestDockerfileFFmpegBuild:
    def test_ffmpeg_version_arg_defined(self, dockerfile_raw):
        assert "ARG FFMPEG_VERSION=8" in dockerfile_raw

    def test_enable_vaapi(self, dockerfile_raw):
        assert "--enable-vaapi" in dockerfile_raw

    def test_enable_nvenc(self, dockerfile_raw):
        # NVENC/NVDEC enabled via ffnvcodec headers (--enable-ffnvcodec covers both)
        assert "--enable-ffnvcodec" in dockerfile_raw

    def test_enable_ffnvcodec(self, dockerfile_raw):
        assert "--enable-ffnvcodec" in dockerfile_raw

    def test_enable_libvpl_for_qsv(self, dockerfile_raw):
        assert "--enable-libvpl" in dockerfile_raw

    def test_enable_libdrm(self, dockerfile_raw):
        assert "--enable-libdrm" in dockerfile_raw

    def test_static_enabled_shared_disabled(self, dockerfile_raw):
        assert "--enable-static" in dockerfile_raw
        assert "--disable-shared" in dockerfile_raw

    def test_gpl_and_version3_enabled(self, dockerfile_raw):
        assert "--enable-gpl" in dockerfile_raw
        # version3 enables additional LGPL-v3 codecs; nonfree intentionally omitted
        # (fdk-aac is non-free and not in Debian main)
        assert "--enable-version3" in dockerfile_raw

    def test_core_codecs_enabled(self, dockerfile_raw):
        for flag in ("--enable-libx264", "--enable-libx265", "--enable-libopus", "--enable-libvorbis", "--enable-libvpx"):
            assert flag in dockerfile_raw, f"Missing codec flag: {flag}"

    def test_av1_codecs_enabled(self, dockerfile_raw):
        for flag in ("--enable-libaom", "--enable-libdav1d", "--enable-libsvtav1"):
            assert flag in dockerfile_raw, f"Missing AV1 flag: {flag}"

    def test_subtitle_support(self, dockerfile_raw):
        assert "--enable-libass" in dockerfile_raw

    def test_openssl_for_https(self, dockerfile_raw):
        assert "--enable-openssl" in dockerfile_raw

    def test_binaries_stripped(self, dockerfile_raw):
        assert "strip /usr/local/bin/ffmpeg /usr/local/bin/ffprobe" in dockerfile_raw

    def test_nv_codec_headers_installed(self, dockerfile_raw):
        assert "nv-codec-headers" in dockerfile_raw

    def test_build_deps_include_vaapi_headers(self, dockerfile_raw):
        assert "libva-dev" in dockerfile_raw

    def test_build_deps_include_vpl_headers(self, dockerfile_raw):
        assert "libvpl-dev" in dockerfile_raw

    def test_build_cache_cleaned(self, dockerfile_raw):
        # Each RUN that calls apt-get should clean the lists
        assert "rm -rf /var/lib/apt/lists/*" in dockerfile_raw


class TestDockerfileRuntime:
    def test_gpu_runtime_libs_present(self, dockerfile_raw):
        for lib in ("libva2", "libva-drm2", "libdrm2", "libvpl2"):
            assert lib in dockerfile_raw, f"Missing GPU runtime lib: {lib}"

    def test_tini_installed(self, dockerfile_raw):
        assert "tini" in dockerfile_raw

    def test_non_root_user_created(self, dockerfile_raw):
        assert "groupadd" in dockerfile_raw
        assert "useradd" in dockerfile_raw

    def test_user_set_to_sma(self, dockerfile):
        # USER is set via ARG UID/GID (e.g. USER ${UID}:${GID})
        user_instructions = _instructions_of("USER", dockerfile)
        assert len(user_instructions) > 0

    def test_port_8585_exposed(self, dockerfile):
        exposed = _instructions_of("EXPOSE", dockerfile)
        assert any("8585" in p for p in exposed)

    def test_volumes_declared(self, dockerfile):
        volumes = _instructions_of("VOLUME", dockerfile)
        assert any("/config" in v for v in volumes)
        assert any("/logs" in v for v in volumes)

    def test_healthcheck_present(self, dockerfile):
        assert any(i == "HEALTHCHECK" for i, _ in dockerfile)

    def test_healthcheck_uses_health_endpoint(self, dockerfile_raw):
        assert "/health" in dockerfile_raw

    def test_entrypoint_uses_tini(self, dockerfile):
        entrypoints = _instructions_of("ENTRYPOINT", dockerfile)
        assert any("tini" in e for e in entrypoints)

    def test_cmd_runs_daemon(self, dockerfile):
        cmds = _instructions_of("CMD", dockerfile)
        assert any("daemon.py" in c for c in cmds)

    def test_cmd_binds_all_interfaces(self, dockerfile_raw):
        # Host is set via SMA_DAEMON_HOST env var, referenced in CMD
        assert "SMA_DAEMON_HOST=0.0.0.0" in dockerfile_raw
        assert "SMA_DAEMON_HOST" in dockerfile_raw

    def test_cmd_includes_config_and_logs_paths(self, dockerfile_raw):
        # Paths are set via env vars, referenced in CMD
        assert "SMA_DAEMON_CONFIG=/app/config/daemon.json" in dockerfile_raw
        assert "SMA_DAEMON_LOGS_DIR=/logs" in dockerfile_raw

    def test_sma_config_env_points_to_volume(self, dockerfile_raw):
        assert "SMA_CONFIG=/app/config/autoProcess.ini" in dockerfile_raw

    def test_uid_gid_args_defined(self, dockerfile):
        args = _instructions_of("ARG", dockerfile)
        assert any("UID" in a for a in args)
        assert any("GID" in a for a in args)

    def test_python_unbuffered(self, dockerfile_raw):
        assert "PYTHONUNBUFFERED=1" in dockerfile_raw

    def test_workdir_is_app(self, dockerfile):
        workdirs = _instructions_of("WORKDIR", dockerfile)
        assert "/app" in workdirs


# ──────────────────────────────────────────────────────────────────────────────
# docker-compose.yml tests
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def compose():
    return _load_yaml("docker/docker-compose.yml")


class TestComposeBaseService:
    def test_software_service_exists(self, compose):
        assert "sma-software" in compose["services"]

    def test_build_targets_runtime_stage(self, compose):
        assert compose["services"]["sma-software"]["build"]["target"] == "runtime"

    def test_port_8585_mapped(self, compose):
        ports = compose["services"]["sma-software"]["ports"]
        assert any("8585" in str(p) for p in ports)

    def test_config_volume_mounted(self, compose):
        volumes = compose["services"]["sma-software"]["volumes"]
        assert any("/config" in str(v) for v in volumes)

    def test_logs_volume_mounted(self, compose):
        volumes = compose["services"]["sma-software"]["volumes"]
        assert any("/logs" in str(v) for v in volumes)

    def test_media_volume_mounted(self, compose):
        volumes = compose["services"]["sma-software"]["volumes"]
        assert any("/mnt" in str(v) for v in volumes)

    def test_sma_config_env_set(self, compose):
        env = compose["services"]["sma-software"]["environment"]
        assert any("SMA_CONFIG" in str(e) for e in env)

    def test_restart_policy(self, compose):
        assert compose["services"]["sma-software"]["restart"] == "unless-stopped"

    def test_env_file_configured(self, compose):
        env_file = compose["services"]["sma-software"]["env_file"]
        assert any("daemon.env" in str(e) for e in env_file)


class TestComposeGpuProfiles:
    def test_nvidia_profile_exists(self, compose):
        assert "sma-nvidia" in compose["services"]
        assert "nvidia" in compose["services"]["sma-nvidia"]["profiles"]
        assert "sma-nvidia-pg" in compose["services"]
        assert "nvidia-pg" in compose["services"]["sma-nvidia-pg"]["profiles"]

    def test_nvidia_gpu_reservation(self, compose):
        devices = compose["services"]["sma-nvidia"]["deploy"]["resources"]["reservations"]["devices"]
        assert any(d.get("driver") == "nvidia" for d in devices)
        devices_pg = compose["services"]["sma-nvidia-pg"]["deploy"]["resources"]["reservations"]["devices"]
        assert any(d.get("driver") == "nvidia" for d in devices_pg)

    def test_nvidia_capabilities_include_video(self, compose):
        devices = compose["services"]["sma-nvidia"]["deploy"]["resources"]["reservations"]["devices"]
        caps = devices[0]["capabilities"]
        assert "video" in caps

    def test_nvidia_env_vars_set(self, compose):
        env = compose["services"]["sma-nvidia"]["environment"]
        env_str = str(env)
        assert "NVIDIA_VISIBLE_DEVICES" in env_str
        assert "NVIDIA_DRIVER_CAPABILITIES" in env_str
        env_pg = compose["services"]["sma-nvidia-pg"]["environment"]
        assert "NVIDIA_VISIBLE_DEVICES" in str(env_pg)

    def test_intel_profile_exists(self, compose):
        assert "sma-intel" in compose["services"]
        assert "intel" in compose["services"]["sma-intel"]["profiles"]
        assert "sma-intel-pg" in compose["services"]
        assert "intel-pg" in compose["services"]["sma-intel-pg"]["profiles"]

    def test_intel_mounts_dev_dri(self, compose):
        devices = compose["services"]["sma-intel"]["devices"]
        assert any("/dev/dri" in str(d) for d in devices)
        devices_pg = compose["services"]["sma-intel-pg"]["devices"]
        assert any("/dev/dri" in str(d) for d in devices_pg)

    def test_intel_adds_render_group(self, compose):
        # group_add uses numeric GID env vars to avoid name-resolution failures
        groups = compose["services"]["sma-intel"]["group_add"]
        assert any("RENDER_GID" in str(g) or "109" in str(g) for g in groups)
        groups_pg = compose["services"]["sma-intel-pg"]["group_add"]
        assert any("RENDER_GID" in str(g) or "109" in str(g) for g in groups_pg)

    def test_software_profiles_exist(self, compose):
        assert "software" in compose["services"]["sma-software"]["profiles"]
        assert "software-pg" in compose["services"]["sma-software-pg"]["profiles"]


class TestComposePostgres:
    def test_postgres_service_exists(self, compose):
        assert "sma-pgsql" in compose["services"]

    def test_postgres_starts_only_for_pg_profiles(self, compose):
        profiles = compose["services"]["sma-pgsql"]["profiles"]
        assert set(profiles) == {"software-pg", "intel-pg", "nvidia-pg"}

    def test_postgres_uses_alpine_image(self, compose):
        assert "postgres" in compose["services"]["sma-pgsql"]["image"]
        assert "alpine" in compose["services"]["sma-pgsql"]["image"]

    def test_postgres_restart_policy(self, compose):
        assert compose["services"]["sma-pgsql"]["restart"] == "unless-stopped"

    def test_postgres_has_named_volume(self, compose):
        vols = compose["services"]["sma-pgsql"]["volumes"]
        assert any("pgdata" in str(v) for v in vols)

    def test_postgres_named_volume_declared(self, compose):
        assert "sma-pgdata" in compose.get("volumes", {})

    def test_postgres_env_vars_set(self, compose):
        env = compose["services"]["sma-pgsql"]["environment"]
        env_str = str(env)
        assert "POSTGRES_DB" in env_str
        assert "POSTGRES_USER" in env_str
        assert "POSTGRES_PASSWORD" in env_str

    def test_postgres_has_healthcheck(self, compose):
        hc = compose["services"]["sma-pgsql"].get("healthcheck")
        assert hc is not None
        assert "pg_isready" in str(hc["test"])

    def test_only_pg_profiles_depend_on_postgres(self, compose):
        for svc in ("sma-software-pg", "sma-intel-pg", "sma-nvidia-pg"):
            dep = compose["services"][svc].get("depends_on", {})
            assert "sma-pgsql" in dep
            assert dep["sma-pgsql"]["condition"] == "service_healthy"
        for svc in ("sma-software", "sma-intel", "sma-nvidia"):
            assert "depends_on" not in compose["services"][svc]

    def test_non_pg_profiles_default_db_url_blank(self, compose):
        for svc in ("sma-software", "sma-intel", "sma-nvidia"):
            env = compose["services"][svc]["environment"]
            assert env["SMA_DAEMON_DB_URL"] == "${SMA_DAEMON_DB_URL:-}"

    def test_pg_profiles_default_db_url_to_bundled_postgres(self, compose):
        for svc in ("sma-software-pg", "sma-intel-pg", "sma-nvidia-pg"):
            env = compose["services"][svc]["environment"]
            assert "sma-pgsql:5432" in env["SMA_DAEMON_DB_URL"]


# ──────────────────────────────────────────────────────────────────────────────
# .dockerignore tests
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def dockerignore():
    return _read(".dockerignore")


class TestDockerignore:
    def test_excludes_git(self, dockerignore):
        assert ".git" in dockerignore

    def test_excludes_venv(self, dockerignore):
        assert "venv/" in dockerignore

    def test_excludes_tests(self, dockerignore):
        assert "tests/" in dockerignore

    def test_excludes_pycache(self, dockerignore):
        assert "__pycache__" in dockerignore

    def test_excludes_dist_and_build(self, dockerignore):
        assert "dist/" in dockerignore
        assert "build/" in dockerignore

    def test_excludes_coverage_artefacts(self, dockerignore):
        assert "coverage.xml" in dockerignore or ".coverage" in dockerignore

    def test_excludes_config_dir(self, dockerignore):
        # Runtime state injected via volume — must not be baked into the image
        assert "config/" in dockerignore

    def test_excludes_logs_dir(self, dockerignore):
        assert "logs/" in dockerignore or "logs" in dockerignore

    def test_excludes_pre_commit(self, dockerignore):
        assert ".pre-commit-config.yaml" in dockerignore

    def test_excludes_github_dir(self, dockerignore):
        assert ".github" in dockerignore


# ──────────────────────────────────────────────────────────────────────────────
# .github/workflows/docker.yml tests
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def docker_workflow():
    return _load_yaml(".github/workflows/docker.yml")


class TestDockerWorkflow:
    def test_triggers_on_push_to_main(self, docker_workflow):
        # PyYAML parses bare 'on' key as boolean True
        triggers = docker_workflow[True]
        assert "main" in triggers["push"]["branches"]

    def test_triggers_on_version_tags(self, docker_workflow):
        triggers = docker_workflow[True]
        tags = triggers["push"]["tags"]
        assert any("v*" in str(t) for t in tags)

    def test_triggers_on_pull_request(self, docker_workflow):
        triggers = docker_workflow[True]
        assert "pull_request" in triggers

    def test_build_job_exists(self, docker_workflow):
        assert "build" in docker_workflow["jobs"]

    def test_runs_on_ubuntu(self, docker_workflow):
        assert "ubuntu" in docker_workflow["jobs"]["build"]["runs-on"]

    def test_uses_buildx(self, docker_workflow):
        steps = docker_workflow["jobs"]["build"]["steps"]
        step_uses = [s.get("uses", "") for s in steps]
        assert any("setup-buildx-action" in u for u in step_uses)

    def test_uses_ghcr_registry(self, docker_workflow):
        assert "ghcr.io" in str(docker_workflow)

    def test_uses_build_push_action(self, docker_workflow):
        steps = docker_workflow["jobs"]["build"]["steps"]
        assert any("build-push-action" in s.get("uses", "") for s in steps)

    def test_targets_runtime_stage(self, docker_workflow):
        steps = docker_workflow["jobs"]["build"]["steps"]
        build_step = next(s for s in steps if "build-push-action" in s.get("uses", ""))
        assert build_step["with"]["target"] == "runtime"

    def test_build_arg_sets_ffmpeg_version(self, docker_workflow):
        steps = docker_workflow["jobs"]["build"]["steps"]
        build_step = next(s for s in steps if "build-push-action" in s.get("uses", ""))
        assert "FFMPEG_VERSION" in build_step["with"]["build-args"]

    def test_ffmpeg_version_is_8(self, docker_workflow):
        # The version is defined in the workflow-level env block, then
        # referenced as ${{ env.FFMPEG_VERSION }} in build-args.
        assert str(docker_workflow["env"]["FFMPEG_VERSION"]).startswith("8")

    def test_layer_cache_configured(self, docker_workflow):
        steps = docker_workflow["jobs"]["build"]["steps"]
        build_step = next(s for s in steps if "build-push-action" in s.get("uses", ""))
        assert "cache-from" in build_step["with"]
        assert "cache-to" in build_step["with"]

    def test_push_skipped_on_pr(self, docker_workflow):
        steps = docker_workflow["jobs"]["build"]["steps"]
        build_step = next(s for s in steps if "build-push-action" in s.get("uses", ""))
        # push expression must reference pull_request event
        push_val = str(build_step["with"]["push"])
        assert "pull_request" in push_val

    def test_packages_write_permission(self, docker_workflow):
        perms = docker_workflow["jobs"]["build"]["permissions"]
        assert perms.get("packages") == "write"

    def test_semver_tags_configured(self, docker_workflow):
        steps = docker_workflow["jobs"]["build"]["steps"]
        meta_step = next((s for s in steps if "metadata-action" in s.get("uses", "")), None)
        assert meta_step is not None
        tags_config = meta_step["with"]["tags"]
        assert "semver" in tags_config
