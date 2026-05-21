"""Schema + projection tests for the ``base.video.vaapi`` / ``base.hdr.vaapi``
nested overlay used by the ``hw_alt`` fallback tier.

The runtime overlay reader (and the byte-identical tier-1 path) is owned by
the parallel runtime lane; this module covers the schema-side shape:

- ``VAAPISettings`` sentinel defaults
- Partial overlay merge: setting one field doesn't wipe parent video fields
- ``hdr.vaapi`` resolves independently from ``video.vaapi``
- ``ReadSettings`` projects both onto ``self.vaapi`` and ``self.hdr["vaapi"]``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resources.config_schema import HDRSettings, SmaConfig, VAAPISettings, VideoSettings

# ---------------------------------------------------------------------------
# T1.1 — sentinel defaults
# ---------------------------------------------------------------------------


def test_vaapi_settings_defaults_are_sentinels() -> None:
  """All fields default to the documented sentinel values."""
  v = VAAPISettings()
  assert v.preset == ""
  assert v.codec_parameters == ""
  assert v.look_ahead_depth == 0
  assert v.global_quality == 0
  assert v.b_frames == -1
  assert v.ref_frames == -1
  assert v.max_level == 0.0
  assert v.rc_mode == ""


def test_vaapi_settings_model_dump_is_stable() -> None:
  """model_dump() emits every sentinel key — runtime reader relies on it."""
  v = VAAPISettings()
  dumped = v.model_dump(by_alias=False)
  assert set(dumped) == {
    "preset",
    "codec_parameters",
    "look_ahead_depth",
    "global_quality",
    "b_frames",
    "ref_frames",
    "max_level",
    "rc_mode",
  }


# ---------------------------------------------------------------------------
# T1.2 — attached to VideoSettings and HDRSettings
# ---------------------------------------------------------------------------


def test_video_settings_has_default_vaapi_block() -> None:
  vs = VideoSettings()
  assert isinstance(vs.vaapi, VAAPISettings)
  assert vs.vaapi.codec_parameters == ""


def test_hdr_settings_has_default_vaapi_block() -> None:
  hs = HDRSettings()
  assert isinstance(hs.vaapi, VAAPISettings)
  assert hs.vaapi.codec_parameters == ""


def test_video_vaapi_kebab_yaml_parses() -> None:
  """YAML ``base.video.vaapi.codec-parameters`` populates the snake field."""
  cfg = SmaConfig.model_validate(
    {
      "base": {
        "video": {
          "vaapi": {"codec-parameters": "-rc_mode VBR", "rc-mode": "VBR"},
        },
      },
    }
  )
  assert cfg.base.video.vaapi.codec_parameters == "-rc_mode VBR"
  assert cfg.base.video.vaapi.rc_mode == "VBR"
  # The HDR side is untouched (independent block).
  assert cfg.base.hdr.vaapi.codec_parameters == ""
  assert cfg.base.hdr.vaapi.rc_mode == ""


# ---------------------------------------------------------------------------
# T1.6 — partial overlay merge: parent fields survive
# ---------------------------------------------------------------------------


def test_partial_vaapi_overlay_preserves_parent_video_fields() -> None:
  """Setting one vaapi field must not zero out the parent VideoSettings."""
  cfg = SmaConfig.model_validate(
    {
      "base": {
        "video": {
          "preset": "slower",
          "codec-parameters": "-low_power 1 -global_quality 22",
          "b-frames": 3,
          "ref-frames": 2,
          "vaapi": {"codec-parameters": "-compression_level 4"},
        },
      },
    }
  )
  vid = cfg.base.video
  # Parent block survives untouched.
  assert vid.preset == "slower"
  assert vid.codec_parameters == "-low_power 1 -global_quality 22"
  assert vid.b_frames == 3
  assert vid.ref_frames == 2
  # Overlay only mutates the nested block.
  assert vid.vaapi.codec_parameters == "-compression_level 4"
  # Unset overlay fields stay at sentinels (inherit-from-parent semantics).
  assert vid.vaapi.preset == ""
  assert vid.vaapi.b_frames == -1
  assert vid.vaapi.ref_frames == -1


def test_hdr_vaapi_independent_from_video_vaapi() -> None:
  """``base.hdr.vaapi`` and ``base.video.vaapi`` are distinct overlays."""
  cfg = SmaConfig.model_validate(
    {
      "base": {
        "video": {"vaapi": {"codec-parameters": "-compression_level 4"}},
        "hdr": {"vaapi": {"codec-parameters": "-compression_level 7", "rc-mode": "CQP"}},
      },
    }
  )
  assert cfg.base.video.vaapi.codec_parameters == "-compression_level 4"
  assert cfg.base.video.vaapi.rc_mode == ""
  assert cfg.base.hdr.vaapi.codec_parameters == "-compression_level 7"
  assert cfg.base.hdr.vaapi.rc_mode == "CQP"


# ---------------------------------------------------------------------------
# T2.3 — ReadSettings projection
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, body: str) -> Path:
  path = tmp_path / "sma-ng.yml"
  path.write_text(body)
  return path


def test_readsettings_projects_video_vaapi(tmp_path: Path) -> None:
  from resources.readsettings import ReadSettings

  yaml_path = _write_yaml(
    tmp_path,
    "daemon:\n  host: 127.0.0.1\nbase:\n  video:\n    vaapi:\n      codec-parameters: '-compression_level 4'\n      rc-mode: 'VBR'\n",
  )
  s = ReadSettings(str(yaml_path))
  assert isinstance(s.vaapi, dict)
  assert s.vaapi["codec_parameters"] == "-compression_level 4"
  assert s.vaapi["rc_mode"] == "VBR"
  # Sentinels still present so the runtime reader sees every key.
  assert s.vaapi["preset"] == ""
  assert s.vaapi["b_frames"] == -1


def test_readsettings_projects_hdr_vaapi_independently(tmp_path: Path) -> None:
  from resources.readsettings import ReadSettings

  yaml_path = _write_yaml(
    tmp_path,
    "daemon:\n  host: 127.0.0.1\n"
    "base:\n"
    "  video:\n    vaapi:\n      codec-parameters: '-compression_level 4'\n"
    "  hdr:\n    vaapi:\n      codec-parameters: '-compression_level 7'\n      rc-mode: 'CQP'\n",
  )
  s = ReadSettings(str(yaml_path))
  assert s.vaapi["codec_parameters"] == "-compression_level 4"
  assert s.vaapi["rc_mode"] == ""
  assert isinstance(s.hdr.get("vaapi"), dict)
  assert s.hdr["vaapi"]["codec_parameters"] == "-compression_level 7"
  assert s.hdr["vaapi"]["rc_mode"] == "CQP"


# ---------------------------------------------------------------------------
# T5.3 — runtime overlay carrier must not pollute tier-1 path
# ---------------------------------------------------------------------------


def _make_mp_for_generate_options(tmp_path: Path):
  """Build a MediaProcessor backed by a real ReadSettings + mocked converter."""
  from unittest.mock import MagicMock, patch

  with patch("resources.readsettings.ReadSettings._validate_binaries"):
    from resources.mediaprocessor import MediaProcessor
    from resources.readsettings import ReadSettings
    from resources.subtitles import SubtitleProcessor

    yaml_path = _write_yaml(
      tmp_path,
      "daemon:\n  host: 127.0.0.1\nbase:\n  video:\n    codec: ['h264']\n    preset: 'medium'\n",
    )
    settings = ReadSettings(str(yaml_path))
    settings.vcodec = ["h264"]

    mock_converter = MagicMock()
    mock_converter.ffmpeg.codecs = {
      "h264": {"encoders": ["libx264"]},
      "aac": {"encoders": ["aac"]},
    }
    mock_converter.ffmpeg.pix_fmts = {"yuv420p": 8}
    mock_converter.codec_name_to_ffmpeg_codec_name.side_effect = lambda c: {"h264": "libx264", "aac": "aac"}.get(c, c)

    mp = MediaProcessor.__new__(MediaProcessor)
    mp.settings = settings
    mp.converter = mock_converter
    mp.log = MagicMock()
    mp.deletesubs = set()
    mp.subtitles = SubtitleProcessor(mp)
    return mp


def test_tier1_path_unchanged_with_vaapi_overlay(tmp_path: Path, make_media_info) -> None:
  """Tier-1 (``hw``) ffmpeg invocation must be byte-identical whether or
  not ``self.settings.vaapi`` carries an override. The overlay is read
  into ``options['_vaapi_overlay']`` only — never into ``options['video']``."""
  import copy as _copy
  from unittest.mock import patch

  from resources.mediaprocessor import Converter

  info = make_media_info(video_codec="h264", video_bitrate=5000000, total_bitrate=5128000, audio_bitrate=128000)

  # Run 1: empty VAAPI overlay. Force SDR detection so the SDR overlay carrier runs.
  mp1 = _make_mp_for_generate_options(tmp_path)
  mp1.settings.hdr = {"space": [], "transfer": [], "primaries": [], "vaapi": {}}
  mp1.settings.vaapi = {}
  with patch.object(Converter, "encoder", return_value=None), patch.object(Converter, "codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
    options1, *_ = mp1.generateOptions("/fake/input.mkv", info=info)

  # Run 2: codec-parameters overlay set.
  mp2 = _make_mp_for_generate_options(tmp_path)
  mp2.settings.hdr = {"space": [], "transfer": [], "primaries": [], "vaapi": {}}
  mp2.settings.vaapi = {"codec_parameters": "-rc_mode VBR"}
  with patch.object(Converter, "encoder", return_value=None), patch.object(Converter, "codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
    options2, *_ = mp2.generateOptions("/fake/input.mkv", info=info)

  assert options1 is not None and options2 is not None
  # The carrier captured the override on the second run.
  assert options1.get("_vaapi_overlay", {}).get("codec_parameters", "") == ""
  assert options2.get("_vaapi_overlay", {}).get("codec_parameters") == "-rc_mode VBR"
  v1 = _copy.deepcopy(options1["video"])
  v2 = _copy.deepcopy(options2["video"])
  assert v1 == v2, "tier-1 video dict must be byte-identical with and without VAAPI overlay"


def test_tier_one_path_byte_identical_with_and_without_vaapi_overlay(tmp_path: Path, make_media_info) -> None:
  """Schema-lane name kept for back-compat — delegates to the T5.3 test."""
  test_tier1_path_unchanged_with_vaapi_overlay(tmp_path, make_media_info)
