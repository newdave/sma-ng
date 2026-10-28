"""docs/openapi.yaml must not repeat a key: YAML loaders keep only the last one."""

import os

from ruamel.yaml import YAML

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_openapi_has_no_duplicate_keys():
  with open(os.path.join(ROOT, "docs", "openapi.yaml")) as f:
    spec = YAML(typ="rt").load(f)  # raises DuplicateKeyError on a repeated key
  assert {"get", "post"} <= set(spec["paths"]["/jobs"])
