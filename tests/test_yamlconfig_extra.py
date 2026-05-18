"""Coverage tests for resources/yamlconfig.py helpers and dedup loader."""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from resources import yamlconfig


class TestCfgGetters:
  def test_cfg_getpath_returns_none_for_falsy(self):
    assert yamlconfig.cfg_getpath(None) is None
    assert yamlconfig.cfg_getpath("") is None
    assert yamlconfig.cfg_getpath(0) is None

  def test_cfg_getpath_expands_envvars(self, monkeypatch):
    monkeypatch.setenv("MY_TEST_HOME", "/tmp/somewhere")
    out = yamlconfig.cfg_getpath("$MY_TEST_HOME/file.bin")
    assert out == os.path.normpath("/tmp/somewhere/file.bin")

  def test_cfg_getdirectory_creates_dir(self, tmp_path):
    target = tmp_path / "a" / "b" / "c"
    result = yamlconfig.cfg_getdirectory(str(target))
    assert result == os.path.normpath(str(target))
    assert result is not None
    assert os.path.isdir(result)

  def test_cfg_getdirectory_falsy_returns_none(self):
    assert yamlconfig.cfg_getdirectory(None) is None
    assert yamlconfig.cfg_getdirectory("") is None

  def test_cfg_getdirectories_empty_input(self):
    assert yamlconfig.cfg_getdirectories(None) == []
    assert yamlconfig.cfg_getdirectories([]) == []

  def test_cfg_getdirectories_creates_each_and_filters_falsy(self, tmp_path):
    paths = [str(tmp_path / "d1"), "", str(tmp_path / "d2")]
    result = yamlconfig.cfg_getdirectories(paths)
    assert len(result) == 2
    for p in result:
      assert os.path.isdir(p)

  def test_cfg_getextension_strip_dot_and_lower(self):
    assert yamlconfig.cfg_getextension("MP4") == "mp4"
    assert yamlconfig.cfg_getextension(".MKV") == "mkv"
    assert yamlconfig.cfg_getextension(" mp4 ") == "mp4"

  def test_cfg_getextension_falsy(self):
    assert yamlconfig.cfg_getextension(None) is None
    assert yamlconfig.cfg_getextension("") is None
    # All-dot reduces to empty -> None
    assert yamlconfig.cfg_getextension("...") is None

  def test_cfg_getextensions_filters_empty(self):
    assert yamlconfig.cfg_getextensions(None) == []
    assert yamlconfig.cfg_getextensions([]) == []
    assert yamlconfig.cfg_getextensions(["MP4", ".mkv", "", "..."]) == ["mp4", "mkv"]


class TestLoadWriteRoundTrip:
  def test_load_missing_file_returns_empty_dict(self, tmp_path):
    missing = tmp_path / "does-not-exist.yml"
    assert yamlconfig.load(str(missing)) == {}

  def test_write_then_load(self, tmp_path):
    path = tmp_path / "out.yml"
    data = {"a": 1, "b": {"c": [1, 2, 3]}}
    yamlconfig.write(str(path), data)
    out = yamlconfig.load(str(path))
    assert out["a"] == 1
    assert out["b"]["c"] == [1, 2, 3]

  def test_write_creates_parent_directories(self, tmp_path):
    path = tmp_path / "nested" / "deep" / "out.yml"
    yamlconfig.write(str(path), {"k": "v"})
    assert path.exists()
    assert yamlconfig.load(str(path)) == {"k": "v"}

  def test_load_empty_file_returns_empty_dict(self, tmp_path):
    path = tmp_path / "empty.yml"
    path.write_text("")
    assert yamlconfig.load(str(path)) == {}


class TestDedupTopLevelKeys:
  def test_duplicate_top_level_keys_merge_with_later_wins(self, tmp_path):
    """Two top-level 'base:' blocks should merge; later scalar overrides earlier."""
    path = tmp_path / "dup.yml"
    path.write_text("base:\n  one: 1\n  shared: original\nbase:\n  two: 2\n  shared: overridden\n")
    out = yamlconfig.load(str(path))
    assert out["base"]["one"] == 1
    assert out["base"]["two"] == 2
    assert out["base"]["shared"] == "overridden"

  def test_unique_top_level_keys_pass_through_ruamel(self, tmp_path):
    """Non-duplicate keys take the fast path (ruamel only)."""
    path = tmp_path / "ok.yml"
    path.write_text("a: 1\nb:\n  c: 2\n")
    out = yamlconfig.load(str(path))
    assert out == {"a": 1, "b": {"c": 2}}

  def test_kebab_and_underscore_collapse_after_dedup(self, tmp_path):
    """Triggering the dedup path also canonicalizes ``foo-bar`` and ``foo_bar``
    to the same key so hand-edited configs don't end up with two entries."""
    path = tmp_path / "mixed.yml"
    path.write_text("base:\n  child:\n    foo-bar: 1\nbase:\n  child:\n    foo_bar: 2\n")
    out = yamlconfig.load(str(path))
    # After merging duplicate blocks, the canonicalizer collapses dashes -> underscores.
    assert "foo_bar" in out["base"]["child"]
    # Later block wins.
    assert out["base"]["child"]["foo_bar"] == 2
