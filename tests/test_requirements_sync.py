"""Guard against drift between pyproject.toml (source of truth) and setup/requirements*.txt."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pyproject():
  with open(ROOT / "pyproject.toml", "rb") as f:
    return tomllib.load(f)["project"]


def _requirements(name):
  lines = (ROOT / "setup" / name).read_text().splitlines()
  return {ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")}


def test_requirements_txt_matches_core_plus_postgres():
  proj = _pyproject()
  expected = set(proj["dependencies"]) | set(proj["optional-dependencies"]["postgres"])
  assert _requirements("requirements.txt") == expected


def test_extra_requirement_files_match_extras():
  extras = _pyproject()["optional-dependencies"]
  for name, extra in [
    ("requirements-dev.txt", "dev"),
    ("requirements-openvino.txt", "openvino"),
    ("requirements-qbittorrent.txt", "qbittorrent"),
    ("requirements-deluge.txt", "deluge"),
  ]:
    assert _requirements(name) == set(extras[extra]), name


def test_docs_ui_dependency_declared_in_package_metadata():
  # resources/daemon/docs_ui.py imports mistune; `pip install .` must pull it in.
  assert any(d.startswith("mistune") for d in _pyproject()["dependencies"])
