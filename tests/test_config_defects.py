"""Regression tests for config loader / schema defects.

Covers profile deep-merge, universal-audio shortcut survival, internal-key
warning suppression, data-key preservation in yamlconfig, path-rewrite
directory boundaries, validation constraints and sample/schema consistency.
"""

from __future__ import annotations

import logging
import os
import re
import textwrap
import typing

import pytest
from pydantic import BaseModel, ValidationError

from resources import yamlconfig
from resources.config_loader import ConfigError, ConfigLoader
from resources.config_schema import (
  AudioSettings,
  DaemonConfig,
  PathRewrite,
  RoutingRule,
  ScanPath,
  SmaConfig,
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SAMPLE_PATH = os.path.join(REPO_ROOT, "setup", "sma-ng.yml.sample")


def _write(tmp_path, body: str) -> str:
  path = tmp_path / "sma-ng.yml"
  path.write_text(textwrap.dedent(body))
  return str(path)


# ---------------------------------------------------------------------------
# apply_profile deep merge
# ---------------------------------------------------------------------------


def test_apply_profile_deep_merges_nested_qsv_block():
  cfg = SmaConfig.model_validate(
    {
      "base": {"video": {"qsv": {"async-depth": 4, "codec-parameters": "-adaptive_i 1"}}},
      "profiles": {"p": {"video": {"qsv": {"low-power": 1}}}},
    }
  )
  merged = ConfigLoader().apply_profile(cfg, "p")
  assert merged.video.qsv.low_power == 1
  assert merged.video.qsv.async_depth == 4
  assert "-adaptive_i 1" in merged.video.qsv.codec_parameters


def test_apply_profile_deep_merges_audio_sorting_universal_and_dicts():
  cfg = SmaConfig.model_validate(
    {
      "base": {
        "audio": {
          "sorting": {"sorting": ["language"], "codecs": ["eac3"]},
          "universal": {"enabled": True, "channel-bitrate": 96},
          "channel-filters": {"6-2": "pan=stereo|FL=FC", "8-2": "pan=stereo|FL=FL"},
        },
        "subtitle": {"subliminal": {"auth": {"opensubtitles": "u:p", "tvsubtitles": "x:y"}}},
      },
      "profiles": {
        "p": {
          "audio": {
            "sorting": {"default-sorting": ["map"]},
            "universal": {"first-stream-only": True},
            "channel-filters": {"6-2": "pan=stereo|FL=BL"},
          },
          "subtitle": {"subliminal": {"auth": {"opensubtitles": "new:pw"}}},
        }
      },
    }
  )
  merged = ConfigLoader().apply_profile(cfg, "p")
  assert merged.audio.sorting.sorting == ["language"]
  assert merged.audio.sorting.codecs == ["eac3"]
  assert merged.audio.sorting.default_sorting == ["map"]
  assert merged.audio.universal.enabled is True
  assert merged.audio.universal.channel_bitrate == 96
  assert merged.audio.universal.first_stream_only is True
  assert merged.audio.channel_filters == {"6-2": "pan=stereo|FL=BL", "8-2": "pan=stereo|FL=FL"}
  assert merged.subtitle.subliminal.auth.opensubtitles == "new:pw"
  assert merged.subtitle.subliminal.auth.tvsubtitles == "x:y"


def test_apply_profile_lists_replace():
  cfg = SmaConfig.model_validate({"base": {"video": {"codec": ["h265", "h264"]}}, "profiles": {"p": {"video": {"codec": ["av1"]}}}})
  assert ConfigLoader().apply_profile(cfg, "p").video.codec == ["av1"]


@pytest.mark.parametrize("value", [True, False])
def test_profile_universal_audio_shortcut_survives_apply_profile(value):
  cfg = SmaConfig.model_validate(
    {
      "base": {"audio": {"universal": {"enabled": not value}}},
      "profiles": {"p": {"audio": {"universal-audio": value}}},
    }
  )
  assert ConfigLoader().apply_profile(cfg, "p").audio.universal.enabled is value


# ---------------------------------------------------------------------------
# _warn_extras
# ---------------------------------------------------------------------------


def test_deprecated_software_fallback_is_not_reported_as_unknown_key(tmp_path, caplog):
  path = _write(
    tmp_path,
    """
    base:
      converter:
        software-fallback: false
    """,
  )
  with caplog.at_level(logging.WARNING):
    ConfigLoader().load(path)
  assert not [r for r in caplog.records if "_software_fallback_deprecated" in r.getMessage()]


# ---------------------------------------------------------------------------
# yamlconfig key canonicalisation
# ---------------------------------------------------------------------------


def test_duplicate_toplevel_keys_preserve_data_keys(tmp_path):
  path = _write(
    tmp_path,
    """
    profiles:
      hq-4k:
        video:
          max-bitrate: 1
    base:
      audio:
        channel-filters:
          "6-2": "pan=stereo|FL=FC"
    services:
      sonarr:
        my-show:
          url: http://x
    profiles:
      lq-1080:
        video:
          max-width: 1920
    """,
  )
  data = yamlconfig.load(path)
  assert set(data["profiles"]) == {"hq-4k", "lq-1080"}
  assert "6-2" in data["base"]["audio"]["channel_filters"]
  assert "my-show" in data["services"]["sonarr"]


# ---------------------------------------------------------------------------
# path rewrite boundary
# ---------------------------------------------------------------------------


def test_path_rewrite_requires_directory_boundary():
  cfg = SmaConfig.model_validate({"daemon": {"path-rewrites": [{"from": "/mnt/tv", "to": "/data/tv"}]}})
  loader = ConfigLoader()
  assert loader._normalise("/mnt/tv/show/a.mkv", cfg) == "/data/tv/show/a.mkv"
  assert loader._normalise("/mnt/tv", cfg) == "/data/tv"
  assert loader._normalise("/mnt/tvshows/x.mkv", cfg) == "/mnt/tvshows/x.mkv"


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["abc", "99", "0889", ""])
def test_chmod_invalid_string_warns_and_defaults(bad, caplog):
  with caplog.at_level(logging.WARNING):
    cfg = SmaConfig.model_validate({"base": {"permissions": {"chmod": bad}}})
  assert cfg.base.permissions.chmod == "0664"
  assert any("octal" in r.getMessage() for r in caplog.records)


def test_chmod_invalid_int_rejected():
  with pytest.raises(ValidationError, match="octal"):
    SmaConfig.model_validate({"base": {"permissions": {"mode": 999}}})


def test_chmod_valid_forms():
  assert SmaConfig.model_validate({"base": {"permissions": {"chmod": "0775"}}}).base.permissions.chmod == "0775"
  assert SmaConfig.model_validate({"base": {"permissions": {"mode": 664}}}).base.permissions.chmod == "0664"


@pytest.mark.parametrize(
  "payload",
  [{"port": 99999}, {"port": 0}, {"workers": 0}, {"workers": -1}],
)
def test_daemon_numeric_ranges(payload):
  with pytest.raises(ValidationError):
    DaemonConfig.model_validate(payload)


def test_scan_path_interval_must_be_positive():
  with pytest.raises(ValidationError):
    ScanPath.model_validate({"path": "/x", "interval": 0})


@pytest.mark.parametrize("match", ["", "/", "  "])
def test_routing_match_rejects_catch_all(match):
  with pytest.raises(ValidationError, match="every path"):
    RoutingRule.model_validate({"match": match})


def test_routing_match_accepts_prefix():
  assert RoutingRule.model_validate({"match": "/media/tv/**"}).match == "/media/tv/**"


def test_path_rewrite_model_still_valid():
  assert PathRewrite.model_validate({"from": "/a", "to": "/b"}).from_ == "/a"


def test_non_numeric_channel_filter_key_warns_but_loads(caplog):
  with caplog.at_level(logging.WARNING):
    audio = AudioSettings.model_validate({"channel-filters": {"stereo": "x"}})
  assert audio.channel_filters == {"stereo": "x"}
  assert any("channel-filters" in r.getMessage() for r in caplog.records)


def test_invalid_crf_profiles_warns(caplog):
  from resources.readsettings import ReadSettings

  with caplog.at_level(logging.WARNING):
    assert ReadSettings._parse_bitrate_profiles("junk") == []
  assert any("crf-profiles" in r.getMessage() for r in caplog.records)


def test_config_error_for_bad_port_via_loader(tmp_path):
  path = _write(
    tmp_path,
    """
    daemon:
      port: 99999
    """,
  )
  with pytest.raises(ConfigError):
    ConfigLoader().load(path)


# ---------------------------------------------------------------------------
# sample covers every schema field
# ---------------------------------------------------------------------------


def _model_classes(annotation) -> list[type[BaseModel]]:
  out: list[type[BaseModel]] = []
  if isinstance(annotation, type) and issubclass(annotation, BaseModel):
    return [annotation]
  for arg in typing.get_args(annotation):
    out.extend(_model_classes(arg))
  return out


def _kebab_fields(model: type[BaseModel], seen: set[type]) -> set[str]:
  if model in seen:
    return set()
  seen.add(model)
  names: set[str] = set()
  for name, field in model.model_fields.items():
    if field.exclude:
      continue
    names.add(name.replace("_", "-"))
    for sub in _model_classes(field.annotation):
      names |= _kebab_fields(sub, seen)
  return names


def test_every_schema_field_appears_in_sample():
  with open(SAMPLE_PATH) as f:
    sample = f.read()
  present = set(re.findall(r"(?m)^[\s#-]*([A-Za-z_][\w-]*):", sample))
  names = _kebab_fields(SmaConfig, set())
  # Entry fields of the empty-by-default list blocks (scan-paths,
  # path-rewrites, audit.paths) cannot appear in a generated sample whose
  # lists are empty; the legacy Plex ``servername`` is kept only for
  # compatibility. Both are documented in docs/configuration.md.
  names -= {"from-", "to", "path", "interval", "rewrite-from", "rewrite-to", "servername"}
  missing = sorted(n for n in names if n not in present)
  assert not missing, f"schema fields missing from setup/sma-ng.yml.sample: {missing}"
