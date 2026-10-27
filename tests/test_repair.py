"""Tests for repair.py — CLI parsing, settings loading, and main() flow."""

import logging
from unittest.mock import MagicMock, patch

import pytest

import repair
from resources.media_repair import RepairResult


def _run(argv):
  with pytest.raises(SystemExit) as exc:
    repair.main(argv)
  return exc.value.code


class TestMain:
  @pytest.fixture(autouse=True)
  def _processor(self):
    with (
      patch.object(repair, "ReadSettings") as rs,
      patch.object(repair, "RepairProcessor") as rp,
      patch.object(repair, "_load_path_config_manager", return_value=None) as lpcm,
      patch.object(repair, "refreshPlex") as plex,
    ):
      self.read_settings = rs
      self.processor_cls = rp
      self.processor = rp.return_value
      self.load_pcm = lpcm
      self.plex = plex
      yield

  def test_missing_path_exits_1(self, tmp_path):
    assert _run([str(tmp_path / "nope")]) == 1
    self.read_settings.assert_not_called()

  def test_settings_loaded_from_config_flag(self, tmp_path):
    self.processor.repair_file.return_value = RepairResult("a.mp4", "normal")
    with patch.object(repair, "iter_targets", return_value=iter([str(tmp_path / "a.mp4")])):
      assert _run(["-c", "/cfg.yml", str(tmp_path)]) == 0
    self.read_settings.assert_called_once_with("/cfg.yml", logger=repair.log, profile=None)
    self.processor_cls.assert_called_once_with(self.read_settings.return_value, logger=repair.log)
    self.load_pcm.assert_not_called()

  def test_settings_error_exits_1(self, tmp_path):
    self.read_settings.side_effect = RuntimeError("ffmpeg binary not found")
    with patch.object(repair, "iter_targets", return_value=iter([str(tmp_path / "a.mp4")])), patch.object(repair, "log") as log:
      assert _run([str(tmp_path)]) == 1
    log.exception.assert_called_once()

  def test_routing_builds_one_processor_per_config_and_profile(self, tmp_path):
    files = [str(tmp_path / n) for n in ("a.mp4", "b.mp4", "c.mp4")]
    pcm = MagicMock()
    pcm.get_config_for_path.return_value = "/main.yml"
    pcm.get_profile_for_path.side_effect = lambda p: "rq" if p.endswith("c.mp4") else "default"
    self.load_pcm.return_value = pcm
    self.processor.repair_file.return_value = RepairResult("x", "normal")
    with patch.object(repair, "iter_targets", return_value=iter(files)):
      assert _run([str(tmp_path)]) == 0
    assert self.processor_cls.call_count == 2
    profiles = sorted(c.kwargs["profile"] for c in self.read_settings.call_args_list)
    assert profiles == ["default", "rq"]
    for c in self.read_settings.call_args_list:
      assert c.args == ("/main.yml",)

  def test_explicit_config_disables_routing(self, tmp_path):
    files = [str(tmp_path / n) for n in ("a.mp4", "b.mp4")]
    self.load_pcm.return_value = MagicMock()
    self.processor.repair_file.return_value = RepairResult("x", "normal")
    with patch.object(repair, "iter_targets", return_value=iter(files)):
      assert _run(["-c", "/cfg.yml", str(tmp_path)]) == 0
    self.load_pcm.assert_not_called()
    assert self.processor_cls.call_count == 1

  def test_no_routing_falls_back_to_single_default_config(self, tmp_path):
    files = [str(tmp_path / n) for n in ("a.mp4", "b.mp4")]
    self.processor.repair_file.return_value = RepairResult("x", "normal")
    with patch.object(repair, "iter_targets", return_value=iter(files)):
      assert _run([str(tmp_path)]) == 0
    self.read_settings.assert_called_once_with(None, logger=repair.log, profile=None)
    assert self.processor_cls.call_count == 1

  def test_all_statuses_counted_and_exit_1_on_failure(self, tmp_path):
    files = [str(tmp_path / n) for n in ("a.mp4", "b.mp4", "c.mp4", "d.mp4")]
    self.processor.repair_file.side_effect = [
      RepairResult(files[0], "normal"),
      RepairResult(files[1], "repaired", "artwork streams 2"),
      RepairResult(files[2], "would_repair", "data streams 3"),
      RepairResult(files[3], "failed", "boom"),
    ]
    with patch.object(repair, "iter_targets", return_value=iter(files)) as it, patch.object(repair, "log") as log:
      assert _run(["-n", "-v", str(tmp_path)]) == 1
    it.assert_called_once_with([str(tmp_path)])
    assert self.processor.repair_file.call_args.kwargs["dry_run"] is True
    summary = log.info.call_args_list[-1].args[0]
    assert summary == "Repair finished: 1 repaired, 1 would repair, 1 normal, 1 failed"
    log.error.assert_called_once()

  def test_verbose_lowers_handler_levels(self, tmp_path):
    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    root.addHandler(handler)
    old_level = root.level
    try:
      with patch.object(repair, "iter_targets", return_value=iter([])):
        assert _run(["-v", str(tmp_path)]) == 0
      assert handler.level == logging.DEBUG
      assert repair.log.isEnabledFor(logging.DEBUG)
    finally:
      root.removeHandler(handler)
      root.setLevel(old_level)
      repair.log.setLevel(logging.NOTSET)

  def test_clean_run_exits_0(self, tmp_path):
    self.processor.repair_file.return_value = RepairResult("a.mp4", "normal")
    with patch.object(repair, "iter_targets", return_value=iter([str(tmp_path / "a.mp4")])):
      assert _run([str(tmp_path)]) == 0

  def test_keyboard_interrupt_exits_1(self, tmp_path):
    self.processor.repair_file.side_effect = KeyboardInterrupt
    with patch.object(repair, "iter_targets", return_value=iter([str(tmp_path / "a.mp4")])):
      assert _run([str(tmp_path)]) == 1


class TestPlexRefresh:
  @pytest.fixture(autouse=True)
  def _processor(self):
    with (
      patch.object(repair, "ReadSettings"),
      patch.object(repair, "RepairProcessor") as rp,
      patch.object(repair, "_load_path_config_manager", return_value=None),
      patch.object(repair, "refreshPlex") as plex,
    ):
      self.processor = rp.return_value
      self.processor.settings.plex_instances = [{"refresh": True}]
      self.processor.repair_file.side_effect = lambda path, dry_run=False: RepairResult(path, "repaired", "x")
      self.plex = plex
      yield

  def _go(self, files, extra=()):
    with patch.object(repair, "iter_targets", return_value=iter(files)):
      return _run([*extra, "/"])

  def test_refreshes_once_per_directory(self, tmp_path):
    (tmp_path / "other").mkdir()
    files = [str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4"), str(tmp_path / "other" / "c.mp4")]
    assert self._go(files) == 0
    assert self.plex.call_count == 2
    assert {c.args[1] for c in self.plex.call_args_list} == {files[0], files[2]}

  def test_no_plex_flag_skips_refresh(self, tmp_path):
    assert self._go([str(tmp_path / "a.mp4")], extra=("--no-plex",)) == 0
    self.plex.assert_not_called()

  def test_not_configured_skips_refresh(self, tmp_path):
    self.processor.settings.plex_instances = []
    self.processor.settings.Plex = {}
    assert self._go([str(tmp_path / "a.mp4")]) == 0
    self.plex.assert_not_called()

  def test_instances_with_refresh_disabled_skip(self, tmp_path):
    self.processor.settings.plex_instances = [{"refresh": False}]
    assert self._go([str(tmp_path / "a.mp4")]) == 0
    self.plex.assert_not_called()

  def test_legacy_plex_settings_trigger_refresh(self, tmp_path):
    self.processor.settings.plex_instances = None
    self.processor.settings.Plex = {"host": "h", "token": "t"}
    assert self._go([str(tmp_path / "a.mp4")]) == 0
    self.plex.assert_called_once()

  def test_refresh_exception_is_swallowed(self, tmp_path):
    self.plex.side_effect = RuntimeError("plex down")
    assert self._go([str(tmp_path / "a.mp4")]) == 0
    self.plex.assert_called_once()

  def test_dry_run_does_not_refresh(self, tmp_path):
    self.processor.repair_file.side_effect = lambda path, dry_run=False: RepairResult(path, "would_repair", "x")
    assert self._go([str(tmp_path / "a.mp4")], extra=("-n",)) == 0
    self.plex.assert_not_called()


class TestLoadPathConfigManager:
  def test_returns_manager(self):
    with patch("resources.daemon.config.PathConfigManager") as cls:
      assert repair._load_path_config_manager() is cls.return_value

  def test_returns_none_on_error(self):
    with patch("resources.daemon.config.PathConfigManager", side_effect=RuntimeError("x")):
      assert repair._load_path_config_manager() is None
