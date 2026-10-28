"""Tests for resources/cli/mounts.py."""

import re

import pytest

from resources.cli import mounts
from resources.cli.errors import UsageError
from resources.cli.hostfacts import ArgStat
from resources.config_schema import AnalyzerSettings, ConverterSettings


def test_required_dir_for_directory_file_and_missing():
  assert mounts.required_dir(ArgStat(1, "d", "/mnt/tv/Show"), "Show") == "/mnt/tv/Show"
  assert mounts.required_dir(ArgStat(1, "f", "/mnt/tv/a b.mkv"), "a b.mkv") == "/mnt/tv"
  with pytest.raises(UsageError, match="sma: gone.mkv: no such file or directory"):
    mounts.required_dir(ArgStat(1, "-", "/x/gone.mkv"), "gone.mkv")


@pytest.mark.parametrize("stat", [ArgStat(1, "d", "/"), ArgStat(1, "f", "/top.mkv"), ArgStat(1, "d", "/mnt/a:b")])
def test_refuses_root_and_colon(stat):
  with pytest.raises(UsageError) as exc:
    mounts.required_dir(stat, "arg")
  assert exc.value.exit_code == 2


def test_build_dedupes_and_required_wins():
  m = mounts.build(
    ["/mnt/tv/Show/S01", "/mnt/tv/Show", "/mnt/tv/Show/", "/srv/x"],
    ["/mnt/tv/Show/extra", "/transcode", "/transcode", "/srv/x", "/recycle"],
  )
  assert m.required == ("/mnt/tv/Show", "/srv/x")
  assert m.optional == ("/transcode", "/recycle")


def test_build_rejects_bad_optional():
  with pytest.raises(UsageError):
    mounts.build([], ["relative/path"])


CONFIG = """\
base:
  converter:
    output-directory: /transcode/sma
    move-to: /mnt/done
    copy-to: [/mnt/copy1, relative/skip]
    recycle-bin: /recycle
  analyzer:
    model-dir: /models
profiles:
  rq:
    converter:
      output-directory: /transcode/rq
"""


def test_config_paths_base_and_profiles(tmp_path):
  (tmp_path / "sma-ng.yml").write_text(CONFIG)
  found = mounts.config_paths(str(tmp_path))
  assert found[:4] == ["/transcode/sma", "/mnt/done", "/mnt/copy1", "/recycle"]
  assert "/models" in found and "/transcode/rq" in found
  assert "relative/skip" not in found


def test_config_paths_missing_file(tmp_path):
  assert mounts.config_paths(str(tmp_path)) == []


def test_config_error_is_usage_error(tmp_path):
  (tmp_path / "sma-ng.yml").write_text("base:\n  converter:\n    threads: not-a-number\n")
  with pytest.raises(UsageError, match="sma-ng.yml"):
    mounts.config_paths(str(tmp_path))


def test_config_dir_from_env(tmp_path, monkeypatch):
  (tmp_path / "sma-ng.yml").write_text(CONFIG)
  monkeypatch.setenv("SMA_CONFIG_DIR", str(tmp_path))
  assert "/recycle" in mounts.config_paths()


_PATHISH = re.compile(r"dir|directory|path|move|copy|recycle")


@pytest.mark.parametrize(
  "model,collected,ignored",
  [
    (ConverterSettings, mounts.CONVERTER_PATH_FIELDS, mounts.CONVERTER_IGNORED_FIELDS),
    (AnalyzerSettings, mounts.ANALYZER_PATH_FIELDS, ()),
  ],
)
def test_every_path_like_field_is_collected_or_ignored(model, collected, ignored):
  # A new directory setting must be mounted for throwaway runs, or ignored on purpose.
  pathish = {name for name in model.model_fields if _PATHISH.search(name)}
  assert pathish == set(collected) | set(ignored)
