"""Compatibility tests for importing ini_merge from the repo root."""

import importlib
import os
import sys


def test_repo_root_import_exposes_parse_keys():
  project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
  sys.modules.pop("ini_merge", None)
  sys.path.insert(0, project_root)
  try:
    module = importlib.import_module("ini_merge")
    assert callable(module.parse_keys)
    assert callable(module.main)
  finally:
    sys.path.pop(0)
    sys.modules.pop("ini_merge", None)
