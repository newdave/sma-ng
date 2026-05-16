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


@pytest.fixture(scope="module")
def entrypoint_raw():
  return _read("docker/docker-entrypoint.sh")


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

  def test_intel_graphics_ppa_added_before_vpl_headers(self, dockerfile_raw):
    ppa_idx = dockerfile_raw.index("add-apt-repository ppa:kobuk-team/intel-graphics")
    vpl_idx = dockerfile_raw.index("libvpl-dev")
    assert ppa_idx < vpl_idx

  def test_build_cache_cleaned(self, dockerfile_raw):
    # Each RUN that calls apt-get should clean the lists
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile_raw


class TestDockerfileRuntime:
  def test_gpu_runtime_libs_present(self, dockerfile_raw):
    for lib in ("libva2", "libva-drm2", "libdrm2", "libvpl2"):
      assert lib in dockerfile_raw, f"Missing GPU runtime lib: {lib}"

  def test_intel_qsv_runtime_mfx_packages_present(self, dockerfile_raw):
    assert "libmfx1" in dockerfile_raw
    assert "libmfx-gen1" in dockerfile_raw

  def test_intel_gpu_tools_installed(self, dockerfile_raw):
    assert "intel-gpu-tools" in dockerfile_raw

  def test_intel_opencl_runtime_installed(self, dockerfile_raw):
    assert "intel-opencl-icd" in dockerfile_raw

  def test_intel_non_free_va_driver_installed(self, dockerfile_raw):
    assert "intel-media-va-driver-non-free" in dockerfile_raw

  def test_i965_shader_package_installed(self, dockerfile_raw):
    assert "i965-va-driver-shaders" in dockerfile_raw

  def test_runtime_ppa_added_before_intel_runtime_packages(self, dockerfile_raw):
    runtime_ppa_idx = dockerfile_raw.rindex("add-apt-repository ppa:kobuk-team/intel-graphics")
    non_free_idx = dockerfile_raw.rindex("intel-media-va-driver-non-free")
    assert runtime_ppa_idx < non_free_idx

  def test_tini_installed(self, dockerfile_raw):
    assert "tini" in dockerfile_raw

  def test_runtime_drops_to_ubuntu_via_setpriv(self, dockerfile_raw, entrypoint_raw):
    # The entrypoint starts as root so it can reconcile /dev/dri device GIDs
    # at startup, then drops privileges to the built-in `ubuntu` user
    # (UID/GID 1000) via setpriv with --init-groups.
    assert "USER ubuntu" not in dockerfile_raw
    assert "util-linux" in dockerfile_raw
    assert "setpriv --reuid=ubuntu --regid=ubuntu --init-groups" in entrypoint_raw

  def test_render_group_created_with_fixed_gid(self, dockerfile_raw):
    assert "getent group render" in dockerfile_raw
    assert "groupadd -g 992 render" in dockerfile_raw

  def test_entrypoint_gid_fixup_is_opt_in(self, entrypoint_raw):
    """The /dev/dri GID reconciliation block must be gated behind
    SMA_ENTRYPOINT_FIX_GIDS=1 (T6). The default path is declarative
    `group_add` in docker-compose.yml; the entrypoint block is the
    bare-`docker run` fallback only."""
    assert "SMA_ENTRYPOINT_FIX_GIDS" in entrypoint_raw
    # Make sure the guard is wrapped around the /dev/dri loop, not the
    # chown of /config/logs.
    assert "SMA_ENTRYPOINT_FIX_GIDS:-0" in entrypoint_raw

  def test_ubuntu_user_added_to_render_group(self, dockerfile_raw):
    assert "usermod -aG render ubuntu" in dockerfile_raw

  def test_no_user_directive(self, dockerfile):
    # The container starts as root so the entrypoint can reconcile /dev/dri
    # device GIDs and add the runtime user to the matching host groups before
    # dropping privileges via setpriv. A USER directive would prevent that.
    assert _instructions_of("USER", dockerfile) == []

  def test_port_8585_exposed(self, dockerfile):
    exposed = _instructions_of("EXPOSE", dockerfile)
    assert any("8585" in p for p in exposed)

  def test_volumes_declared(self, dockerfile):
    volumes = _instructions_of("VOLUME", dockerfile)
    assert any("/config" in v for v in volumes)
    assert any("/logs" in v for v in volumes)
    assert any("/data" in v for v in volumes)

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
    assert "--host 0.0.0.0" in dockerfile_raw
    assert "SMA_DAEMON_HOST" not in dockerfile_raw

  def test_cmd_includes_config_and_logs_paths(self, dockerfile_raw):
    assert "--daemon-config /config/sma-ng.yml" in dockerfile_raw
    assert "--logs-dir /logs" in dockerfile_raw

  def test_no_sma_config_env_in_image(self, dockerfile_raw):
    assert "SMA_CONFIG=" not in dockerfile_raw

  def test_no_uid_gid_args(self, dockerfile):
    # ubuntu:24.04 built-in 'ubuntu' user is used directly; ARG UID/GID
    # are not needed and should not be present.
    args = _instructions_of("ARG", dockerfile)
    assert not any("UID" in a for a in args)
    assert not any("GID" in a for a in args)

  def test_python_unbuffered(self, dockerfile_raw):
    assert "PYTHONUNBUFFERED=1" in dockerfile_raw

  def test_workdir_is_app(self, dockerfile):
    workdirs = _instructions_of("WORKDIR", dockerfile)
    assert "/app" in workdirs

  def test_repo_entry_scripts_rewrite_shebangs_for_container_venv(self, dockerfile_raw):
    assert "#!/opt/sma/venv/bin/python3" in dockerfile_raw
    assert "#!/venv/bin/python3" in dockerfile_raw
    assert "daemon.py" in dockerfile_raw
    assert "manual.py" in dockerfile_raw
    assert "rename.py" in dockerfile_raw


# ──────────────────────────────────────────────────────────────────────────────
# docker-compose.yml tests
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def compose():
  return _load_yaml("docker/docker-compose.yml")


class TestComposeBaseService:
  def test_software_service_exists(self, compose):
    assert "sma-software" in compose["services"]

  def test_image_from_ghcr(self, compose):
    # Services pull from GHCR; no local build context in docker-compose.yml.
    image = compose["services"]["sma-software"]["image"]
    assert "ghcr.io" in image

  def test_port_8585_mapped(self, compose):
    ports = compose["services"]["sma-software"]["ports"]
    assert any("8585" in str(p) for p in ports)

  def test_config_volume_mounted(self, compose):
    volumes = compose["services"]["sma-software"]["volumes"]
    assert any("/config" in str(v) for v in volumes)

  def test_logs_volume_mounted(self, compose):
    volumes = compose["services"]["sma-software"]["volumes"]
    assert any("/logs" in str(v) for v in volumes)

  def test_data_volume_mounted_for_sqlite(self, compose):
    volumes = compose["services"]["sma-software"]["volumes"]
    assert "/opt/sma/data:/data" in volumes

  def test_media_volume_mounted(self, compose):
    volumes = compose["services"]["sma-software"]["volumes"]
    assert any("/mnt" in str(v) for v in volumes)

  def test_sma_config_env_not_set(self, compose):
    env = compose["services"]["sma-software"].get("environment", {}) or {}
    assert "SMA_CONFIG" not in str(env)

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

  def test_daemon_services_pin_stable_hostname(self, compose):
    # Each profile must declare an explicit hostname so socket.gethostname()
    # remains stable across container recreates.
    for service_name in (
      "sma-software",
      "sma-software-pg",
      "sma-nvidia",
      "sma-nvidia-pg",
      "sma-intel",
      "sma-intel-pg",
    ):
      hostname = compose["services"][service_name].get("hostname")
      assert hostname, f"{service_name} is missing a hostname pin"
      assert "SMA_NODE_NAME" not in hostname

  def test_intel_exposes_render_node(self, compose):
    # Headless QSV/VAAPI encoding needs at least the render node. Either
    # the whole `/dev/dri` tree (preferred, lets SR-IOV guests map every
    # card*/renderD* pair without per-host edits) or an explicit renderD*
    # mapping is acceptable.
    def _exposes_dri(devices):
      return any(("renderD" in str(d)) or (str(d).split(":", 1)[0] == "/dev/dri") for d in devices)

    assert _exposes_dri(compose["services"]["sma-intel"]["devices"])
    assert _exposes_dri(compose["services"]["sma-intel-pg"]["devices"])

  def test_intel_declarative_group_add(self, compose):
    """Intel profiles ship declarative group_add for /dev/dri access.

    The container's ubuntu user joins the host's `video` and `render`
    groups (and the image's baked-in numeric 992 fallback for hosts
    where neither group name exists). This replaces the prior
    root-mode entrypoint GID reconciliation, which is now opt-in via
    SMA_ENTRYPOINT_FIX_GIDS=1.
    """
    for svc in ("sma-intel", "sma-intel-pg"):
      groups = compose["services"][svc].get("group_add") or []
      assert "video" in groups, "%s missing 'video' group_add" % svc
      assert "render" in groups, "%s missing 'render' group_add" % svc
      assert "992" in groups, "%s missing numeric '992' render-GID fallback" % svc

  def test_software_profiles_exist(self, compose):
    assert "software" in compose["services"]["sma-software"]["profiles"]
    assert "software-pg" in compose["services"]["sma-software-pg"]["profiles"]


class TestComposePostgres:
  def test_no_bundled_postgres_service(self, compose):
    assert "sma-pgsql" not in compose["services"]

  def test_sma_daemon_services_share_canonical_container_name(self, compose):
    # All SMA daemon variants resolve to a single container named
    # `sma-ng` regardless of profile, so switching a host's profile
    # in setup/local.yml just replaces the same container in place
    # rather than leaving stale per-profile containers around.
    sma_services = (
      "sma-software",
      "sma-software-pg",
      "sma-intel",
      "sma-intel-pg",
      "sma-nvidia",
      "sma-nvidia-pg",
    )
    for svc in sma_services:
      assert compose["services"][svc].get("container_name") == "sma-ng", f"{svc} should have container_name=sma-ng for cross-profile reuse"

  def test_no_profiles_depend_on_bundled_postgres(self, compose):
    for svc in ("sma-software-pg", "sma-intel-pg", "sma-nvidia-pg"):
      assert "depends_on" not in compose["services"][svc]
    for svc in ("sma-software", "sma-intel", "sma-nvidia"):
      assert "depends_on" not in compose["services"][svc]

  def test_non_pg_profiles_do_not_set_db_url_env(self, compose):
    for svc in ("sma-software", "sma-intel", "sma-nvidia"):
      svc_def = compose["services"][svc]
      env = svc_def.get("environment", {}) or {}
      assert "SMA_DAEMON_DB_URL" not in env
      assert "SMA_DB_URL" not in env
      env_files = svc_def.get("env_file", [])
      assert any(
        (isinstance(ef, dict) and ef.get("path") in ("/opt/sma/config/daemon.env", "../config/daemon.env")) or ef in ("/opt/sma/config/daemon.env", "../config/daemon.env") for ef in env_files
      )
      assert "/opt/sma/data:/data" in svc_def["volumes"]

  def test_pg_profiles_do_not_set_db_url_env(self, compose):
    for svc in ("sma-software-pg", "sma-intel-pg", "sma-nvidia-pg"):
      env = compose["services"][svc].get("environment", {}) or {}
      assert "SMA_DAEMON_DB_URL" not in env


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
