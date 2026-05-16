"""Unit tests for scripts/probe-hw.py.

Loads the script as a module via importlib so the script doesn't need to
be on sys.path. All subprocess invocations are mocked — the script itself
is the unit under test.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent.parent
PROBE_PATH = REPO / "scripts" / "probe-hw.py"


@pytest.fixture(scope="module")
def probe_mod():
  """Load `scripts/probe-hw.py` as a module."""
  spec = importlib.util.spec_from_file_location("probe_hw", PROBE_PATH)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  sys.modules["probe_hw"] = module
  spec.loader.exec_module(module)
  return module


def _mk_run(rc_table):
  """Build a fake `_run` that dispatches on argv[0]/argv[1] to a rc/stdout/stderr triple."""

  def fake(argv):
    binary = os.path.basename(argv[0])
    key = binary
    if binary == "ffmpeg" and len(argv) >= 3:
      key = "ffmpeg:%s" % argv[2]  # "-hwaccels", "-encoders", "-decoders", "-version"
    elif binary == "nvidia-smi":
      key = "nvidia-smi"
    elif binary == "vainfo":
      key = "vainfo"
    return rc_table.get(key, (127, "", "no mock for %s" % key))

  return fake


_VAINFO_OK = """libva info: VA-API version 1.23.0
libva info: Trying to open /usr/lib/dri/iHD_drv_video.so
vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics - 23.4.0
      VAEntrypointVLD
      VAEntrypointEncSlice
"""

_VAINFO_FAILED = """libva info: VA-API version 1.23.0
libva info: Trying to open /usr/lib/dri/iHD_drv_video.so
libva error: /dev/dri/renderD128: Permission denied
vainfo: failed to initialize
"""

_FFMPEG_HWACCELS_QSV = "Hardware acceleration methods:\nqsv\nvaapi\nvulkan\n"
_FFMPEG_HWACCELS_NVENC = "Hardware acceleration methods:\ncuda\n"
_FFMPEG_HWACCELS_NONE = "Hardware acceleration methods:\n"

_FFMPEG_ENCODERS_QSV = """Encoders:
 V..... = Video
 ------
 V..... libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
 V..... h264_qsv             H.264 (Intel Quick Sync Video)
 V..... hevc_qsv             HEVC (Intel Quick Sync Video)
 V..... av1_qsv              AV1 (Intel Quick Sync Video)
"""

_FFMPEG_ENCODERS_NVENC = """Encoders:
 ------
 V..... libx264              libx264
 V..... h264_nvenc           NVIDIA NVENC H.264 encoder
 V..... hevc_nvenc           NVIDIA NVENC hevc encoder
"""

_FFMPEG_DECODERS_QSV = """Decoders:
 ------
 V..... h264_qsv             H.264 QSV decoder
 V..... hevc_qsv             HEVC QSV decoder
"""

_FFMPEG_VERSION = "ffmpeg version 8.1 Copyright (c) 2000-2025 the FFmpeg developers\n"


def test_qsv_host_classifies_ok(probe_mod):
  rc_table = {
    "ffmpeg:-hwaccels": (0, _FFMPEG_HWACCELS_QSV, ""),
    "ffmpeg:-encoders": (0, _FFMPEG_ENCODERS_QSV, ""),
    "ffmpeg:-decoders": (0, _FFMPEG_DECODERS_QSV, ""),
    "ffmpeg:-version": (0, _FFMPEG_VERSION, ""),
    "ffprobe:-version": (0, _FFMPEG_VERSION, ""),
    "vainfo": (0, _VAINFO_OK, ""),
    "nvidia-smi": (127, "", "not present"),
  }
  with patch.object(probe_mod, "_run", side_effect=_mk_run(rc_table)):
    with patch.object(probe_mod, "probe_render_nodes", return_value=["/dev/dri/renderD128"]):
      snap = probe_mod.build_snapshot("ffmpeg", "ffprobe", "2.4.0")
  assert snap["gpu_status"] == "ok"
  assert snap["selected_backend"] == "qsv"
  assert "qsv" in snap["capabilities"]["hwaccels"]
  assert snap["capabilities"]["encoders"]["hevc_qsv"] is True
  assert snap["capabilities"]["vainfo_driver"].lower().startswith("intel") or "iHD" in snap["capabilities"]["vainfo_driver"]


def test_nvenc_host_classifies_ok(probe_mod):
  rc_table = {
    "ffmpeg:-hwaccels": (0, _FFMPEG_HWACCELS_NVENC, ""),
    "ffmpeg:-encoders": (0, _FFMPEG_ENCODERS_NVENC, ""),
    "ffmpeg:-decoders": (0, "Decoders:\n V..... h264_cuvid          NV decoder\n", ""),
    "ffmpeg:-version": (0, _FFMPEG_VERSION, ""),
    "ffprobe:-version": (0, _FFMPEG_VERSION, ""),
    "vainfo": (127, "", "absent"),
    "nvidia-smi": (0, "GPU 0: NVIDIA L4\n", ""),
  }
  with patch.object(probe_mod, "_run", side_effect=_mk_run(rc_table)):
    with patch.object(probe_mod, "probe_render_nodes", return_value=[]):
      snap = probe_mod.build_snapshot("ffmpeg", "ffprobe", "2.4.0")
  assert snap["selected_backend"] == "nvenc"
  assert snap["capabilities"]["nvidia"] is True


def test_vainfo_failure_downgrades_to_degraded(probe_mod):
  rc_table = {
    "ffmpeg:-hwaccels": (0, _FFMPEG_HWACCELS_QSV, ""),
    "ffmpeg:-encoders": (0, _FFMPEG_ENCODERS_QSV, ""),
    "ffmpeg:-decoders": (0, _FFMPEG_DECODERS_QSV, ""),
    "ffmpeg:-version": (0, _FFMPEG_VERSION, ""),
    "ffprobe:-version": (0, _FFMPEG_VERSION, ""),
    "vainfo": (1, "", _VAINFO_FAILED),
    "nvidia-smi": (127, "", "absent"),
  }
  with patch.object(probe_mod, "_run", side_effect=_mk_run(rc_table)):
    with patch.object(probe_mod, "probe_render_nodes", return_value=["/dev/dri/renderD128"]):
      snap = probe_mod.build_snapshot("ffmpeg", "ffprobe", "2.4.0")
  assert snap["gpu_status"] == "degraded"
  assert any("vainfo" in e for e in snap["errors"])


def test_ffmpeg_unreachable_marks_unreachable(probe_mod):
  rc_table = {
    "ffmpeg:-hwaccels": (127, "", "binary not found"),
    "ffmpeg:-encoders": (127, "", "binary not found"),
    "ffmpeg:-decoders": (127, "", "binary not found"),
    "ffmpeg:-version": (127, "", "binary not found"),
    "ffprobe:-version": (127, "", "binary not found"),
    "nvidia-smi": (127, "", "absent"),
  }
  with patch.object(probe_mod, "_run", side_effect=_mk_run(rc_table)):
    with patch.object(probe_mod, "probe_render_nodes", return_value=[]):
      snap = probe_mod.build_snapshot("ffmpeg", "ffprobe", "2.4.0")
  assert snap["gpu_status"] == "unreachable"
  assert snap["selected_backend"] == "software"


def test_software_host_classifies_ok(probe_mod):
  rc_table = {
    "ffmpeg:-hwaccels": (0, _FFMPEG_HWACCELS_NONE, ""),
    "ffmpeg:-encoders": (0, "Encoders:\n ------\n V..... libx264              libx264\n", ""),
    "ffmpeg:-decoders": (0, "Decoders:\n ------\n V..... h264                 H.264\n", ""),
    "ffmpeg:-version": (0, _FFMPEG_VERSION, ""),
    "ffprobe:-version": (0, _FFMPEG_VERSION, ""),
    "nvidia-smi": (127, "", "absent"),
  }
  with patch.object(probe_mod, "_run", side_effect=_mk_run(rc_table)):
    with patch.object(probe_mod, "probe_render_nodes", return_value=[]):
      snap = probe_mod.build_snapshot("ffmpeg", "ffprobe", "2.4.0")
  assert snap["gpu_status"] == "ok"
  assert snap["selected_backend"] == "software"


def test_atomic_write_replaces_file(probe_mod, tmp_path):
  dst = tmp_path / "caps.json"
  dst.write_text('{"old": true}')
  snapshot = {"schema_version": 1, "gpu_status": "ok"}
  probe_mod.write_snapshot(dst, snapshot)
  data = json.loads(dst.read_text())
  assert data["gpu_status"] == "ok"
  assert "old" not in data
  # tmp file must not linger
  assert not (tmp_path / "caps.json.tmp").exists()


def test_atomic_write_creates_parent_directories(probe_mod, tmp_path):
  dst = tmp_path / "cache" / "nested" / "caps.json"
  probe_mod.write_snapshot(dst, {"x": 1})
  assert dst.exists()


def test_main_cli_writes_snapshot(probe_mod, tmp_path):
  dst = tmp_path / "caps.json"
  rc_table = {
    "ffmpeg:-hwaccels": (0, _FFMPEG_HWACCELS_NONE, ""),
    "ffmpeg:-encoders": (0, "Encoders:\n", ""),
    "ffmpeg:-decoders": (0, "Decoders:\n", ""),
    "ffmpeg:-version": (0, _FFMPEG_VERSION, ""),
    "ffprobe:-version": (0, _FFMPEG_VERSION, ""),
    "nvidia-smi": (127, "", "absent"),
  }
  with patch.object(probe_mod, "_run", side_effect=_mk_run(rc_table)):
    with patch.object(probe_mod, "probe_render_nodes", return_value=[]):
      rc = probe_mod.main(["--output", str(dst), "--image-version", "test"])
  assert rc == 0
  snap = json.loads(dst.read_text())
  assert snap["schema_version"] == 1
  assert snap["image_version"] == "test"
  assert snap["gpu_status"] == "ok"


def test_main_fail_open_on_internal_error(probe_mod, tmp_path):
  """If `build_snapshot` blows up, the script still writes a `gpu_status: unknown` snapshot and returns 0."""
  dst = tmp_path / "caps.json"
  with patch.object(probe_mod, "build_snapshot", side_effect=RuntimeError("boom")):
    rc = probe_mod.main(["--output", str(dst)])
  assert rc == 0
  snap = json.loads(dst.read_text())
  assert snap["gpu_status"] == "unknown"
  assert any("probe_failed" in e for e in snap["errors"])
