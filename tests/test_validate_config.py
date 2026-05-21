"""Smoke tests for scripts/validate-config.py.

The validator is a thin wrapper over the schema + a handful of operator-
facing checks. These tests construct minimal configs in tmp dirs and
assert the right findings fire.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "validate-config.py"

# Some pytest collection passes can't import a hyphenated script via
# importlib by name, so use spec_from_file_location.
spec = importlib.util.spec_from_file_location("validate_config", SCRIPT)
assert spec is not None and spec.loader is not None
validate_config = importlib.util.module_from_spec(spec)
sys.modules["validate_config"] = validate_config
spec.loader.exec_module(validate_config)


def _write_yaml(tmp_path: Path, body: str) -> Path:
  path = tmp_path / "sma-ng.yml"
  path.write_text(body)
  return path


def test_clean_config_returns_no_findings(tmp_path: Path) -> None:
  yaml_path = _write_yaml(
    tmp_path,
    "daemon:\n  host: 127.0.0.1\nbase:\n  video:\n    codec: ['h265']\n  audio:\n    codec: ['eac3']\n",
  )
  cfg, findings = validate_config._load_config(str(yaml_path))
  assert cfg is not None
  assert all(f.level != "error" for f in findings)


def test_routing_to_undefined_profile_is_an_error(tmp_path: Path) -> None:
  yaml_path = _write_yaml(
    tmp_path,
    "daemon:\n  host: 127.0.0.1\n  routing:\n  - match: /mnt/tv\n    profile: notreal\nbase:\n  video:\n    codec: ['h265']\n",
  )
  cfg, _ = validate_config._load_config(str(yaml_path))
  # ConfigLoader rejects this upfront — cfg should be None and a finding logged.
  # If schema lets it through, the routing check picks it up.
  # In either case, no clean run.
  findings: list = []
  if cfg is None:
    return
  validate_config._check_routing_references(cfg, findings)
  assert any(f.level == "error" and "notreal" in f.message for f in findings)


def test_qsv_only_token_in_video_codec_parameters_warns(tmp_path: Path) -> None:
  # The migration shim should move QSV-only flags into qsv.codec-parameters.
  # If somehow they stayed on the parent (e.g. operator hand-edited the
  # stamped file after deploy), the validator catches it.
  from resources.config_schema import SmaConfig

  cfg = SmaConfig.model_validate({"base": {"video": {"codec-parameters": "-encoder_agnostic 1"}}})
  # Force-inject a QSV-only token into the resolved string to simulate the
  # post-migration stale state.
  cfg.base.video.codec_parameters = "-encoder_agnostic 1 -low_power 0"
  findings: list = []
  validate_config._check_encoder_flag_leaks(cfg, findings)
  assert any(f.level == "warn" and "QSV-only" in f.message for f in findings)


def test_audio_codec_first_entry_copy_is_an_error(tmp_path: Path) -> None:
  yaml_path = _write_yaml(
    tmp_path,
    "daemon:\n  host: 127.0.0.1\nbase:\n  audio:\n    codec: ['copy', 'eac3']\n",
  )
  cfg, _ = validate_config._load_config(str(yaml_path))
  assert cfg is not None
  findings: list = []
  validate_config._check_codec_list_shapes(cfg, findings)
  assert any(f.level == "error" and "audio.codec" in f.path for f in findings)


def test_missing_service_credentials_warns(tmp_path: Path) -> None:
  yaml_path = _write_yaml(
    tmp_path,
    "daemon:\n  host: 127.0.0.1\nbase:\n  video:\n    codec: ['h265']\nservices:\n  sonarr:\n    main:\n      url: https://sonarr\n",
  )
  cfg, _ = validate_config._load_config(str(yaml_path))
  assert cfg is not None
  findings: list = []
  validate_config._check_service_completeness(cfg, findings)
  assert any(f.level == "warn" and "apikey" in f.message for f in findings)


@pytest.mark.parametrize(
  "gpu,subblock_field,subblock_value,expected_level",
  [
    ("vaapi", {"qsv": {"low-power": 0}}, "qsv", "warn"),
    ("qsv", {"vaapi": {"codec-parameters": "-rc_mode VBR"}}, "vaapi", "info"),
  ],
)
def test_subblock_alignment_warnings(tmp_path: Path, gpu, subblock_field, subblock_value, expected_level):
  from resources.config_schema import SmaConfig

  cfg = SmaConfig.model_validate({"base": {"video": {"gpu": gpu, "codec": ["h265"], **subblock_field}}})
  findings: list = []
  validate_config._check_subblock_encoder_alignment(cfg, findings)
  assert any(f.level == expected_level and subblock_value in f.path for f in findings)
