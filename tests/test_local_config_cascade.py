"""Tests for the ``_defaults`` cascade in scripts/local-config.py.

Mirrors the same shape used by Services._apply_service_defaults in the
pydantic schema — operators can DRY-up the ``hosts:`` (and
``services.<type>:``) blocks of setup/local.yml by writing common keys
once and letting siblings inherit, while still overriding per-instance.

Inheritance chain for a host lookup is:

    hosts.<name>.<key>   >   hosts._defaults.<key>   >   deploy.<key>
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "local-config.py"

spec = importlib.util.spec_from_file_location("local_config", SCRIPT)
assert spec is not None and spec.loader is not None
local_config = importlib.util.module_from_spec(spec)
sys.modules["local_config"] = local_config
spec.loader.exec_module(local_config)


def _yaml(data, tmp_path):
  import yaml

  path = tmp_path / "local.yml"
  with open(path, "w") as f:
    yaml.safe_dump(data, f)
  return path


def test_host_inherits_from_defaults(tmp_path):
  data = {
    "deploy": {"workers": 1},
    "hosts": {
      "_defaults": {"user": "iadmin", "ffmpeg_dir": "/usr/local/bin", "workers": 3},
      "sma-master": {"address": "10.30.0.40"},
    },
  }
  _yaml(data, tmp_path)
  d = local_config._load(str(tmp_path / "local.yml"))
  assert local_config.resolve(d, "sma-master", "user", "") == "iadmin"
  assert local_config.resolve(d, "sma-master", "ffmpeg_dir", "") == "/usr/local/bin"
  assert local_config.resolve(d, "sma-master", "workers", "") == "3"
  assert local_config.resolve(d, "sma-master", "address", "") == "10.30.0.40"


def test_host_overrides_defaults(tmp_path):
  data = {
    "hosts": {
      "_defaults": {"workers": 3},
      "sma-master": {"address": "10.30.0.40", "workers": 8},
    },
  }
  _yaml(data, tmp_path)
  d = local_config._load(str(tmp_path / "local.yml"))
  # Per-host value wins over _defaults.
  assert local_config.resolve(d, "sma-master", "workers", "") == "8"


def test_host_falls_through_to_deploy_when_not_in_defaults(tmp_path):
  data = {
    "deploy": {"ssh_port": "2222"},
    "hosts": {
      "_defaults": {"user": "iadmin"},
      "sma-master": {"address": "10.30.0.40"},
    },
  }
  _yaml(data, tmp_path)
  d = local_config._load(str(tmp_path / "local.yml"))
  # ssh_port absent from both _defaults and the host block — falls to deploy.
  assert local_config.resolve(d, "sma-master", "ssh_port", "") == "2222"


def test_no_defaults_still_works(tmp_path):
  data = {
    "deploy": {"workers": 1},
    "hosts": {"sma-master": {"address": "10.30.0.40", "workers": 4}},
  }
  _yaml(data, tmp_path)
  d = local_config._load(str(tmp_path / "local.yml"))
  assert local_config.resolve(d, "sma-master", "workers", "") == "4"
  assert local_config.resolve(d, "sma-master", "address", "") == "10.30.0.40"


def test_service_defaults_cascade_in_local_config(tmp_path):
  data = {
    "services": {
      "sonarr": {
        "_defaults": {"apikey": "abc123", "rescan": True},
        "main": {"url": "https://sonarr.example.com"},
      },
    },
  }
  _yaml(data, tmp_path)
  d = local_config._load(str(tmp_path / "local.yml"))
  assert local_config.resolve(d, "sonarr.main", "apikey", "") == "abc123"
  assert local_config.resolve(d, "sonarr.main", "rescan", "") == "true"
  assert local_config.resolve(d, "sonarr.main", "url", "") == "https://sonarr.example.com"


@pytest.mark.parametrize(
  "instance_overrides,expected",
  [
    ({"rescan": False}, "false"),
    ({}, "true"),
  ],
)
def test_service_instance_overrides_defaults(tmp_path, instance_overrides, expected):
  data = {
    "services": {
      "sonarr": {
        "_defaults": {"rescan": True},
        "main": {"url": "https://x", **instance_overrides},
      },
    },
  }
  _yaml(data, tmp_path)
  d = local_config._load(str(tmp_path / "local.yml"))
  assert local_config.resolve(d, "sonarr.main", "rescan", "") == expected
