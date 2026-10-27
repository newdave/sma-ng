"""Tests for repair.py — CLI parsing, binary lookup, and main() flow."""

from unittest.mock import MagicMock, patch

import pytest

import repair
from resources.media_repair import RepairResult


def _run(argv):
  with pytest.raises(SystemExit) as exc:
    repair.main(argv)
  return exc.value.code


class TestFfmpegPaths:
  def test_reads_paths_from_settings(self):
    settings = MagicMock(ffmpeg="/opt/ff/ffmpeg", ffprobe="/opt/ff/ffprobe")
    with patch("resources.readsettings.ReadSettings", return_value=settings) as rs:
      assert repair._ffmpeg_paths("/cfg.yml") == ("/opt/ff/ffmpeg", "/opt/ff/ffprobe")
    rs.assert_called_once_with("/cfg.yml")

  def test_missing_default_config_falls_back_to_path(self):
    with patch("resources.readsettings.ReadSettings", side_effect=RuntimeError("no config")):
      assert repair._ffmpeg_paths(None) == (None, None)

  def test_explicit_config_error_propagates(self):
    with patch("resources.readsettings.ReadSettings", side_effect=RuntimeError("bad yaml")):
      with pytest.raises(RuntimeError):
        repair._ffmpeg_paths("/cfg.yml")


class TestMain:
  @pytest.fixture(autouse=True)
  def _binaries(self):
    with patch.object(repair, "_ffmpeg_paths", return_value=(None, None)), patch.object(repair, "FFMpeg") as ff:
      self.ffmpeg = ff
      yield

  def test_missing_path_exits_2(self, tmp_path):
    assert _run([str(tmp_path / "nope")]) == 2

  def test_ffmpeg_not_found_exits_2(self, tmp_path):
    self.ffmpeg.side_effect = RuntimeError("ffmpeg binary not found")
    assert _run([str(tmp_path)]) == 2

  def test_all_statuses_counted_and_exit_1_on_failure(self, tmp_path):
    files = [str(tmp_path / n) for n in ("a.mp4", "b.mp4", "c.mp4", "d.mp4")]
    results = [
      RepairResult(files[0], "normal"),
      RepairResult(files[1], "repaired", "artwork streams 2"),
      RepairResult(files[2], "would_repair", "data streams 3"),
      RepairResult(files[3], "failed", "boom"),
    ]
    with patch.object(repair, "iter_targets", return_value=iter(files)) as it, patch.object(repair, "repair_file", side_effect=results) as rf:
      with patch.object(repair, "log") as log:
        assert _run(["-n", "-v", str(tmp_path)]) == 1
    it.assert_called_once_with([str(tmp_path)])
    assert rf.call_args.kwargs["dry_run"] is True
    summary = log.info.call_args_list[-1].args[0]
    assert summary == "Repair finished: 1 repaired, 1 would repair, 1 normal, 1 failed"
    log.error.assert_called_once()

  def test_clean_run_exits_0(self, tmp_path):
    with patch.object(repair, "iter_targets", return_value=iter([str(tmp_path / "a.mp4")])):
      with patch.object(repair, "repair_file", return_value=RepairResult("a.mp4", "normal")):
        assert _run([str(tmp_path)]) == 0

  def test_keyboard_interrupt_exits_1(self, tmp_path):
    with patch.object(repair, "iter_targets", return_value=iter([str(tmp_path / "a.mp4")])):
      with patch.object(repair, "repair_file", side_effect=KeyboardInterrupt):
        assert _run([str(tmp_path)]) == 1
