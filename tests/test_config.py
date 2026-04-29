"""Tests for resources/readsettings.py - configuration parsing."""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from resources.readsettings import ReadSettings


class TestReadSettingsMultiInstance:
  """Test multi-instance Sonarr/Radarr discovery."""

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_discovers_sonarr_instances(self, mock_validate, tmp_yaml):
    yml = tmp_yaml(
      overrides={
        "services": {
          "sonarr": {
            "main": {"url": "http://sonarr.local:8989", "apikey": "abc123"},
            "kids": {"url": "http://sonarr-kids.local:8989", "apikey": "def456"},
          },
          "radarr": {
            "main": {"url": "http://radarr.local:7878", "apikey": "ghi789"},
            "4k": {"url": "http://radarr-4k.local:7878", "apikey": "jkl012"},
          },
        },
        "daemon": {
          "routing": [
            {"match": "/tv", "services": ["sonarr.main"]},
            {"match": "/tv-kids", "services": ["sonarr.kids"]},
            {"match": "/movies", "services": ["radarr.main"]},
            {"match": "/movies/4k", "services": ["radarr.4k"]},
          ],
        },
      }
    )
    settings = ReadSettings(yml)
    assert len(settings.sonarr_instances) == 2
    assert len(settings.radarr_instances) == 2

    sonarr_sections = [i["section"] for i in settings.sonarr_instances]
    assert "main" in sonarr_sections
    assert "sonarr-kids" in sonarr_sections

    radarr_sections = [i["section"] for i in settings.radarr_instances]
    assert "main" in radarr_sections
    assert "radarr-4k" in radarr_sections

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_sorted_by_path_length(self, mock_validate, tmp_yaml):
    """Instances should be sorted longest-path-first when multiple routing
    rules contribute paths to the same instance."""
    yml = tmp_yaml(
      overrides={
        "services": {
          "sonarr": {
            "main": {"url": "http://localhost:8989", "apikey": "x"},
            "kids": {"url": "http://localhost:8990", "apikey": "y"},
          },
        },
        "daemon": {
          "routing": [
            {"match": "/tv", "services": ["sonarr.main"]},
            {"match": "/tv/kids", "services": ["sonarr.kids"]},
          ],
        },
      }
    )
    settings = ReadSettings(yml)
    paths = [i.get("path", "") for i in settings.sonarr_instances]
    assert [len(p) for p in paths] == sorted([len(p) for p in paths], reverse=True)

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_discovers_autoscan_instances(self, mock_validate, tmp_yaml):
    """autoscan_instances should be built from services.autoscan, with paths
    derived from routing rules and disabled instances filtered out."""
    yml = tmp_yaml(
      overrides={
        "services": {
          "autoscan": {
            "main": {
              "url": "http://localhost:3030",
              "username": "u",
              "password": "p",
              "path-mapping": "/library/Media=/data/Media",
            },
            "off": {"url": "http://other:3030", "enabled": False},
          },
        },
        "daemon": {
          "routing": [
            {"match": "/library/Media", "services": ["autoscan.main"]},
          ],
        },
      }
    )
    settings = ReadSettings(yml)
    assert len(settings.autoscan_instances) == 1
    inst = settings.autoscan_instances[0]
    assert inst["section"] == "main"
    assert inst["host"] == "localhost"
    assert inst["port"] == 3030
    assert inst["username"] == "u"
    assert inst["password"] == "p"
    assert inst["path"] == "/library/Media"
    assert inst["path-mapping"] == {"/library/Media": "/data/Media"}

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_autoscan_no_routing_yields_empty_path(self, mock_validate, tmp_yaml):
    """An autoscan instance with no routing reference still appears in
    autoscan_instances (with empty path) so admins can see it; the
    triggerAutoscan path-prefix gate just won't match anything."""
    yml = tmp_yaml(
      overrides={
        "services": {"autoscan": {"main": {"url": "http://localhost:3030"}}},
      }
    )
    settings = ReadSettings(yml)
    assert len(settings.autoscan_instances) == 1
    assert settings.autoscan_instances[0]["path"] == ""


class TestGpuProfile:
  """Test gpu shorthand auto-derives all HW acceleration settings."""

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_qsv_profile(self, mock_validate, tmp_yaml):
    settings = ReadSettings(tmp_yaml(gpu="qsv"))
    assert settings.gpu == "qsv"
    assert "qsv" in settings.hwaccels
    assert "hevc_qsv" in settings.hwaccel_decoders
    assert "h264_qsv" in settings.hwaccel_decoders
    assert settings.hwdevices.get("qsv") == "/dev/dri/renderD128"
    assert settings.hwoutputfmt.get("qsv") == "qsv"

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_nvenc_profile(self, mock_validate, tmp_yaml):
    settings = ReadSettings(tmp_yaml(gpu="nvenc"))
    assert settings.gpu == "nvenc"
    assert "cuda" in settings.hwaccels
    assert "hevc_cuvid" in settings.hwaccel_decoders
    assert "h264_cuvid" in settings.hwaccel_decoders
    assert settings.hwoutputfmt.get("cuda") == "cuda"

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_vaapi_profile(self, mock_validate, tmp_yaml):
    settings = ReadSettings(tmp_yaml(gpu="vaapi"))
    assert settings.gpu == "vaapi"
    assert "vaapi" in settings.hwaccels
    assert "hevc_vaapi" in settings.hwaccel_decoders
    assert settings.hwdevices.get("vaapi") == "/dev/dri/renderD128"
    assert settings.hwoutputfmt.get("vaapi") == "vaapi"

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_videotoolbox_profile(self, mock_validate, tmp_yaml):
    settings = ReadSettings(tmp_yaml(gpu="videotoolbox"))
    assert settings.gpu == "videotoolbox"
    assert "videotoolbox" in settings.hwaccels

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_no_gpu(self, mock_validate, tmp_yaml):
    """Empty gpu should not populate any HW settings."""
    settings = ReadSettings(tmp_yaml())
    assert settings.gpu == ""
    assert settings.hwaccels == []
    assert settings.hwaccel_decoders == []

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_explicit_override_preserved(self, mock_validate, tmp_yaml):
    """If user explicitly sets hwaccels alongside gpu, the explicit value wins."""
    yml = tmp_yaml(
      gpu="qsv",
      overrides={"base": {"converter": {"hwaccels": ["cuda"]}}},
    )
    settings = ReadSettings(yml)
    assert "cuda" in settings.hwaccels

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_codec_mapping_qsv(self, mock_validate, tmp_yaml):
    """gpu=qsv should map hevc→h265qsv with software fallback."""
    yml = tmp_yaml(
      gpu="qsv",
      overrides={"base": {"video": {"codec": ["hevc", "h264"]}}},
    )
    settings = ReadSettings(yml)
    assert "h265qsv" in settings.vcodec
    assert "hevc" in settings.vcodec
    assert "h264qsv" in settings.vcodec
    assert "h264" in settings.vcodec
    assert settings.vcodec.index("h265qsv") < settings.vcodec.index("hevc")

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_codec_mapping_vaapi(self, mock_validate, tmp_yaml):
    """gpu=vaapi should map hevc→h265vaapi."""
    yml = tmp_yaml(
      gpu="vaapi",
      overrides={"base": {"video": {"codec": ["hevc"]}}},
    )
    settings = ReadSettings(yml)
    assert "h265vaapi" in settings.vcodec
    assert "hevc" in settings.vcodec


class TestValidateBinaries:
  """Test FFmpeg/FFprobe binary validation."""

  @patch("shutil.which", return_value="/usr/bin/ffmpeg")
  def test_valid_binary_in_path(self, mock_which, tmp_yaml):
    """Should not exit when binary is found via which."""
    ini = tmp_yaml()
    # Should not raise
    ReadSettings(ini)

  @patch("shutil.which", return_value=None)
  @patch("os.path.isfile", return_value=False)
  def test_missing_binary_exits(self, mock_isfile, mock_which, tmp_yaml):
    """Should sys.exit when binary not found."""
    ini = tmp_yaml()
    with pytest.raises(SystemExit):
      ReadSettings(ini)


class TestMapCodecsWithFallback:
  def test_maps_codec(self):
    result = ReadSettings._map_codecs_with_fallback(["h265", "h264"], {"h265": "h265qsv", "h264": "h264qsv"})
    assert result[0] == "h265qsv"
    assert "h265" in result  # fallback kept
    assert "h264qsv" in result

  def test_no_mapping(self):
    result = ReadSettings._map_codecs_with_fallback(["aac"], {})
    assert result == ["aac"]

  def test_dedup(self):
    result = ReadSettings._map_codecs_with_fallback(["h264", "h264"], {"h264": "h264qsv"})
    assert result.count("h264qsv") == 1

  def test_alias_resolution(self):
    result = ReadSettings._map_codecs_with_fallback(["hevc"], {"h265": "h265qsv"})
    assert "h265qsv" in result
    assert "hevc" in result


class TestForceConvertOverride:
  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_force_convert_sets_process_same(self, mock_validate, tmp_yaml):
    yml = tmp_yaml(overrides={"base": {"converter": {"force-convert": True}}})
    settings = ReadSettings(yml)
    assert settings.force_convert is True
    assert settings.process_same_extensions is True


class TestArtworkParsing:
  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_poster_artwork(self, mock_validate, tmp_yaml):
    settings = ReadSettings(tmp_yaml())
    # Default is 'false' in the test fixture
    assert settings.thumbnail is False

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_thumbnail_artwork(self, mock_validate, tmp_yaml):
    yml = tmp_yaml(overrides={"base": {"metadata": {"download-artwork": "thumbnail"}}})
    settings = ReadSettings(yml)
    assert settings.artwork is True
    assert settings.thumbnail is True


class TestPermissionsParsing:
  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_invalid_chmod_defaults_to_664(self, mock_validate, tmp_yaml):
    yml = tmp_yaml(overrides={"base": {"permissions": {"chmod": "notoctal"}}})
    settings = ReadSettings(yml)
    assert settings.permissions["chmod"] == 0o664

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_valid_chmod_is_parsed(self, mock_validate, tmp_yaml):
    yml = tmp_yaml(overrides={"base": {"permissions": {"chmod": "0755"}}})
    settings = ReadSettings(yml)
    assert settings.permissions["chmod"] == 0o755


class TestArtworkParsingExtended:
  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_poster_value(self, mock_validate, tmp_yaml):
    yml = tmp_yaml(overrides={"base": {"metadata": {"download-artwork": "poster"}}})
    settings = ReadSettings(yml)
    assert settings.artwork is True
    assert settings.thumbnail is False

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_invalid_artwork_value_defaults_to_true(self, mock_validate, tmp_yaml):
    yml = tmp_yaml(overrides={"base": {"metadata": {"download-artwork": "maybe"}}})
    settings = ReadSettings(yml)
    assert settings.artwork is True


class TestReadSettingsUniversalAudio:
  """Test Universal Audio config parsing."""

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_universal_audio_enabled_flag_defaults_false(self, mock_validate, tmp_yaml):
    yml = tmp_yaml(
      overrides={
        "base": {
          "audio": {
            "universal": {
              "enabled": False,
              "codec": ["aac"],
              "first-stream-only": True,
            }
          }
        }
      }
    )
    settings = ReadSettings(yml)
    assert settings.ua_enabled is False
    assert settings.ua == ["aac"]
    assert settings.ua_first_only is True


class TestReadSettingsAnalyzer:
  """Test Analyzer config parsing."""

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_analyzer_defaults_are_backfilled(self, mock_validate, tmp_yaml):
    settings = ReadSettings(tmp_yaml())

    assert settings.analyzer == {
      "enabled": False,
      "backend": "openvino",
      "device": "AUTO",
      "model_dir": None,
      "cache_dir": None,
      "max_frames": 12,
      "target_width": 960,
      "allow_codec_reorder": True,
      "allow_bitrate_adjustments": True,
      "allow_preset_adjustments": True,
      "allow_filter_adjustments": True,
      "allow_force_reencode": True,
    }

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_analyzer_custom_values_are_parsed(self, mock_validate, tmp_yaml, tmp_path):
    model_dir = tmp_path / "models"
    cache_dir = tmp_path / "cache"
    yml = tmp_yaml(
      overrides={
        "base": {
          "analyzer": {
            "enabled": True,
            "backend": "openvino",
            "device": "NPU",
            "model-dir": str(model_dir),
            "cache-dir": str(cache_dir),
            "max-frames": 24,
            "target-width": 1280,
            "allow-codec-reorder": False,
            "allow-bitrate-adjustments": False,
            "allow-preset-adjustments": False,
            "allow-filter-adjustments": False,
            "allow-force-reencode": False,
          }
        }
      }
    )
    settings = ReadSettings(yml)

    assert settings.analyzer == {
      "enabled": True,
      "backend": "openvino",
      "device": "NPU",
      "model_dir": os.path.normpath(str(model_dir)),
      "cache_dir": os.path.normpath(str(cache_dir)),
      "max_frames": 24,
      "target_width": 1280,
      "allow_codec_reorder": False,
      "allow_bitrate_adjustments": False,
      "allow_preset_adjustments": False,
      "allow_filter_adjustments": False,
      "allow_force_reencode": False,
    }


class TestBitrateProfiles:
  """Test _parse_bitrate_profiles static method and vbitrate_profiles attribute."""

  def test_empty_string_returns_empty_list(self):
    result = ReadSettings._parse_bitrate_profiles("")
    assert result == []

  def test_whitespace_only_returns_empty_list(self):
    result = ReadSettings._parse_bitrate_profiles("   ")
    assert result == []

  def test_single_entry_m_suffix(self):
    result = ReadSettings._parse_bitrate_profiles("0:22:2M:4M")
    assert result == [{"source_kbps": 0, "target": 2000, "maxrate": 4000}]

  def test_m_suffix_multiplies_by_1000(self):
    result = ReadSettings._parse_bitrate_profiles("0:22:1M:3M")
    assert result[0]["target"] == 1000
    assert result[0]["maxrate"] == 3000

  def test_k_suffix_keeps_value_as_is(self):
    result = ReadSettings._parse_bitrate_profiles("0:22:3000k:6000k")
    assert result[0]["target"] == 3000
    assert result[0]["maxrate"] == 6000

  def test_bare_number_treated_as_kbps(self):
    result = ReadSettings._parse_bitrate_profiles("0:22:3000:6000")
    assert result[0]["target"] == 3000
    assert result[0]["maxrate"] == 6000

  def test_multiple_entries_sorted_by_source_kbps_ascending(self):
    result = ReadSettings._parse_bitrate_profiles("8000:22:5M:10M, 0:22:2M:4M, 4000:22:3M:6M")
    assert [p["source_kbps"] for p in result] == [0, 4000, 8000]

  def test_entry_with_wrong_field_count_is_skipped(self):
    result = ReadSettings._parse_bitrate_profiles("0:22:2M, 4000:22:3M:6M")
    assert len(result) == 1
    assert result[0]["source_kbps"] == 4000

  def test_entry_with_non_numeric_source_kbps_is_skipped(self):
    result = ReadSettings._parse_bitrate_profiles("bad:22:2M:4M, 4000:22:3M:6M")
    assert len(result) == 1
    assert result[0]["source_kbps"] == 4000

  def test_entry_with_non_numeric_target_is_skipped(self):
    result = ReadSettings._parse_bitrate_profiles("0:22:bad:4M, 4000:22:3M:6M")
    assert len(result) == 1
    assert result[0]["source_kbps"] == 4000

  def test_entry_with_non_numeric_maxrate_is_skipped(self):
    result = ReadSettings._parse_bitrate_profiles("0:22:2M:bad, 4000:22:3M:6M")
    assert len(result) == 1
    assert result[0]["source_kbps"] == 4000

  def test_all_bad_entries_returns_empty_list(self):
    result = ReadSettings._parse_bitrate_profiles("bad:22:2M:4M, 0:22:bad:4M")
    assert result == []

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_vbitrate_profiles_empty_when_not_configured(self, mock_validate, tmp_yaml):
    settings = ReadSettings(tmp_yaml())
    assert settings.vbitrate_profiles == []

  @patch("resources.readsettings.ReadSettings._validate_binaries")
  def test_vbitrate_profiles_parsed_when_configured(self, mock_validate, tmp_yaml):
    yml = tmp_yaml(overrides={"base": {"video": {"crf-profiles": "0:22:2M:4M,4000:22:3M:6M"}}})
    settings = ReadSettings(yml)
    assert len(settings.vbitrate_profiles) == 2
    assert settings.vbitrate_profiles[0] == {"source_kbps": 0, "target": 2000, "maxrate": 4000}
    assert settings.vbitrate_profiles[1] == {"source_kbps": 4000, "target": 3000, "maxrate": 6000}
