"""Tests for the fallback-policy enum migration on ConverterSettings.

Covers the deprecation alias mapping ``software-fallback: bool`` →
``fallback-policy: <enum>`` performed by the ``_migrate_software_fallback``
model validator, plus the legacy ``settings.software_fallback`` projection
in ReadSettings.
"""

from __future__ import annotations

import logging

import pytest

from resources.config_schema import ConverterSettings, FallbackPolicy


def test_default_policy_is_aggressive() -> None:
  cfg = ConverterSettings()
  assert cfg.fallback_policy == FallbackPolicy.AGGRESSIVE


def test_explicit_policy_round_trips() -> None:
  cfg = ConverterSettings.model_validate({"fallback-policy": "sw_decode_only"})
  assert cfg.fallback_policy == FallbackPolicy.SW_DECODE_ONLY


def test_legacy_false_maps_to_hw_only() -> None:
  cfg = ConverterSettings.model_validate({"software-fallback": False})
  assert cfg.fallback_policy == FallbackPolicy.HW_ONLY


def test_legacy_true_maps_to_aggressive() -> None:
  cfg = ConverterSettings.model_validate({"software-fallback": True})
  assert cfg.fallback_policy == FallbackPolicy.AGGRESSIVE


def test_new_key_wins_over_legacy_when_both_present() -> None:
  cfg = ConverterSettings.model_validate(
    {"software-fallback": True, "fallback-policy": "hw_only"},
  )
  assert cfg.fallback_policy == FallbackPolicy.HW_ONLY


def test_snake_case_legacy_key_also_migrates() -> None:
  cfg = ConverterSettings.model_validate({"software_fallback": False})
  assert cfg.fallback_policy == FallbackPolicy.HW_ONLY


def test_deprecation_sentinel_set_on_migration() -> None:
  cfg = ConverterSettings.model_validate({"software-fallback": False})
  extras = cfg.model_extra or {}
  assert extras.get("_software_fallback_deprecated") is True


def test_no_deprecation_sentinel_for_new_key() -> None:
  cfg = ConverterSettings.model_validate({"fallback-policy": "hw_only"})
  extras = cfg.model_extra or {}
  assert "_software_fallback_deprecated" not in extras


def test_sample_is_in_sync_with_schema() -> None:
  """`mise run config:sample` must produce a sample identical to what's committed."""
  import subprocess
  import sys
  from pathlib import Path

  repo = Path(__file__).resolve().parent.parent
  result = subprocess.run(
    [sys.executable, str(repo / "scripts" / "generate_sma_ng_sample.py"), "--check"],
    capture_output=True,
    text=True,
    check=False,
  )
  assert result.returncode == 0, f"sample drift:\n{result.stdout}\n{result.stderr}"


def test_settings_emits_deprecation_warning(caplog: pytest.LogCaptureFixture) -> None:
  """ReadSettings must log exactly one deprecation warning for legacy boolean."""
  import sys
  from pathlib import Path

  repo = Path(__file__).resolve().parent.parent
  sys.path.insert(0, str(repo))
  from resources.readsettings import ReadSettings

  yaml_path = repo / "tests" / "tmp_legacy_software_fallback.yml"
  yaml_path.write_text(
    "daemon:\n  host: 127.0.0.1\nbase:\n  converter:\n    software-fallback: false\n",
  )
  try:
    with caplog.at_level(logging.WARNING, logger="resources.readsettings"):
      ReadSettings(str(yaml_path))
    deprecation_messages = [r for r in caplog.records if "software-fallback" in r.getMessage() and "deprecated" in r.getMessage()]
    assert len(deprecation_messages) == 1
  finally:
    if yaml_path.exists():
      yaml_path.unlink()
