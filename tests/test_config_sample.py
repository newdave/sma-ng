"""Round-trip test for setup/sma-ng.yml.sample.

The committed sample is the bytes-identical output of
``scripts/generate_sma_ng_sample.py``. The same `--check` mode also runs
in the ``config-sample-consistency`` CI job; this test catches the same
drift locally so a developer who forgets ``mise run config:generate-sample``
gets a clear pytest failure instead of a CI red-light.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SAMPLE_PATH = os.path.join(REPO_ROOT, "setup", "sma-ng.yml.sample")
GENERATOR_PATH = os.path.join(REPO_ROOT, "scripts", "generate_sma_ng_sample.py")


@pytest.fixture(scope="module")
def generator_module():
  """Import the generator script as a module so we can call build_sample_yaml()."""
  if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
  spec = importlib.util.spec_from_file_location("generate_sma_ng_sample", GENERATOR_PATH)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_committed_sample_is_byte_identical_to_generator(generator_module):
  """A regenerated sample must match the committed bytes exactly.

  If this fails, run ``mise run config:generate-sample`` and commit the
  diff — the schema or the illustrative routing/services entries have
  changed and the sample is stale.
  """
  generated = generator_module.build_sample_yaml()
  with open(SAMPLE_PATH, "rb") as f:
    committed = f.read()
  assert generated == committed, "setup/sma-ng.yml.sample is out of sync with the schema. Run `mise run config:generate-sample` to regenerate."


def test_committed_sample_loads_through_config_loader():
  """Sanity check: the committed sample must be a reload-safe four-bucket YAML."""
  from resources.config_loader import ConfigLoader

  cfg = ConfigLoader().load(SAMPLE_PATH)
  assert cfg.daemon.host == "0.0.0.0"
  assert cfg.daemon.port == 8585
  assert "rq" in cfg.profiles
  assert "main" in cfg.services.sonarr
  assert len(cfg.daemon.routing) >= 1
