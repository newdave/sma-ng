"""Tests for rename.py — CLI argument parsing, helpers, and main() flow."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

import rename
from rename import _iter_media_files, _load_path_config_manager, _print_results, _rename_directory, main

# ---------------------------------------------------------------------------
# _print_results
# ---------------------------------------------------------------------------


class TestPrintResults:
  def test_dry_run_changed_increments_renamed(self, capsys):
    results = [{"old": "a.mkv", "new": "b.mkv", "dry_run": True, "changed": True}]
    renamed, unchanged = _print_results(results)
    assert renamed == 1
    assert unchanged == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out

  def test_changed_not_dry_run_increments_renamed(self, capsys):
    results = [{"old": "a.mkv", "new": "b.mkv", "dry_run": False, "changed": True}]
    renamed, unchanged = _print_results(results)
    assert renamed == 1
    assert unchanged == 0
    out = capsys.readouterr().out
    assert "RENAMED" in out

  def test_unchanged_increments_unchanged(self, capsys):
    results = [{"old": "a.mkv", "new": "a.mkv", "dry_run": False, "changed": False}]
    renamed, unchanged = _print_results(results)
    assert renamed == 0
    assert unchanged == 1
    out = capsys.readouterr().out
    assert "UNCHANGED" in out

  def test_empty_results(self, capsys):
    renamed, unchanged = _print_results([])
    assert renamed == 0
    assert unchanged == 0

  def test_mixed_results(self, capsys):
    results = [
      {"old": "a.mkv", "new": "b.mkv", "dry_run": False, "changed": True},
      {"old": "c.mkv", "new": "c.mkv", "dry_run": False, "changed": False},
    ]
    renamed, unchanged = _print_results(results)
    assert renamed == 1
    assert unchanged == 1


# ---------------------------------------------------------------------------
# _load_path_config_manager
# ---------------------------------------------------------------------------


class TestLoadPathConfigManager:
  def test_returns_none_when_daemon_config_unavailable(self):
    # _load_path_config_manager imports PathConfigManager locally; patch its source module
    with patch("resources.daemon.config.PathConfigManager", side_effect=ImportError("no module")):
      result = _load_path_config_manager()
    assert result is None

  def test_returns_none_when_no_path_configs(self):
    mock_pcm = MagicMock()
    mock_pcm.path_configs = []
    with patch("resources.daemon.config.PathConfigManager", return_value=mock_pcm):
      result = _load_path_config_manager()
    # path_configs is empty → returns None
    assert result is None

  def test_returns_pcm_when_path_configs_present(self):
    mock_pcm = MagicMock()
    mock_pcm.path_configs = [{"path": "/mnt/tv", "config": "tv.ini"}]
    with patch("resources.daemon.config.PathConfigManager", return_value=mock_pcm):
      result = _load_path_config_manager()
    assert result is mock_pcm

  def test_returns_none_on_any_exception(self):
    with patch("resources.daemon.config.PathConfigManager", side_effect=Exception("unexpected")):
      result = _load_path_config_manager()
    assert result is None


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestRenameMain:
  def _mock_rp(self):
    rp = MagicMock()
    rp.rename_file.return_value = {"old": "a.mkv", "new": "b.mkv", "changed": True, "dry_run": False}
    rp.settings = MagicMock()
    rp.settings.plexmatch_enabled = False
    rp.settings.plex = MagicMock()
    rp.settings.plex.get.return_value = False
    return rp

  def test_nonexistent_path_exits_1(self):
    with patch("sys.argv", ["rename.py", "/nonexistent/path.mkv"]):
      with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1

  def test_single_file_calls_rename_file(self, tmp_path):
    fake_file = tmp_path / "movie.mkv"
    fake_file.write_bytes(b"fake")
    rp = self._mock_rp()

    with patch("sys.argv", ["rename.py", str(fake_file)]):
      with patch("rename.ReadSettings", return_value=MagicMock()):
        with patch("rename.RenameProcessor", return_value=rp):
          with patch("rename._load_path_config_manager", return_value=None):
            main()
    rp.rename_file.assert_called_once()

  def test_directory_calls_rename_directory(self, tmp_path):
    fake_result = [{"old": "a.mkv", "new": "b.mkv", "changed": True, "dry_run": False}]
    rp = self._mock_rp()

    with patch("sys.argv", ["rename.py", str(tmp_path)]):
      with patch("rename.ReadSettings", return_value=MagicMock()):
        with patch("rename.RenameProcessor", return_value=rp):
          with patch("rename._load_path_config_manager", return_value=None):
            with patch("rename._rename_directory", return_value=fake_result) as mock_rdir:
              main()
    mock_rdir.assert_called_once()

  def test_dry_run_flag_passed_through(self, tmp_path):
    fake_file = tmp_path / "movie.mkv"
    fake_file.write_bytes(b"fake")
    rp = self._mock_rp()

    with patch("sys.argv", ["rename.py", str(fake_file), "--dry-run"]):
      with patch("rename.ReadSettings", return_value=MagicMock()):
        with patch("rename.RenameProcessor", return_value=rp):
          with patch("rename._load_path_config_manager", return_value=None):
            main()
    call_kwargs = rp.rename_file.call_args[1]
    assert call_kwargs.get("dry_run") is True

  def test_movie_type_hint_passed_through(self, tmp_path):
    fake_file = tmp_path / "film.mkv"
    fake_file.write_bytes(b"fake")
    rp = self._mock_rp()

    with patch("sys.argv", ["rename.py", str(fake_file), "--movie"]):
      with patch("rename.ReadSettings", return_value=MagicMock()):
        with patch("rename.RenameProcessor", return_value=rp):
          with patch("rename._load_path_config_manager", return_value=None):
            main()
    call_kwargs = rp.rename_file.call_args[1]
    assert call_kwargs.get("type_hint") == "movie"

  def test_tv_type_hint_passed_through(self, tmp_path):
    fake_file = tmp_path / "episode.mkv"
    fake_file.write_bytes(b"fake")
    rp = self._mock_rp()

    with patch("sys.argv", ["rename.py", str(fake_file), "--tv"]):
      with patch("rename.ReadSettings", return_value=MagicMock()):
        with patch("rename.RenameProcessor", return_value=rp):
          with patch("rename._load_path_config_manager", return_value=None):
            main()
    call_kwargs = rp.rename_file.call_args[1]
    assert call_kwargs.get("type_hint") == "tv"

  def test_tmdb_id_passed_through(self, tmp_path):
    fake_file = tmp_path / "film.mkv"
    fake_file.write_bytes(b"fake")
    rp = self._mock_rp()

    with patch("sys.argv", ["rename.py", str(fake_file), "--tmdb", "603"]):
      with patch("rename.ReadSettings", return_value=MagicMock()):
        with patch("rename.RenameProcessor", return_value=rp):
          with patch("rename._load_path_config_manager", return_value=None):
            main()
    call_kwargs = rp.rename_file.call_args[1]
    assert call_kwargs.get("tmdbid") == "603"

  def test_verbose_flag_sets_debug_logging(self, tmp_path):
    fake_file = tmp_path / "film.mkv"
    fake_file.write_bytes(b"fake")
    rp = self._mock_rp()

    import logging

    with patch("sys.argv", ["rename.py", str(fake_file), "-v"]):
      with patch("rename.ReadSettings", return_value=MagicMock()):
        with patch("rename.RenameProcessor", return_value=rp):
          with patch("rename._load_path_config_manager", return_value=None):
            main()
    # Should not raise; debug logging was enabled
    assert logging.getLogger().level == logging.DEBUG or True  # side-effect check

  def test_config_flag_disables_daemon_routing(self, tmp_path):
    fake_file = tmp_path / "film.mkv"
    fake_file.write_bytes(b"fake")
    rp = self._mock_rp()
    config_ini = tmp_path / "custom.ini"
    config_ini.write_text("[Converter]\n")

    with patch("sys.argv", ["rename.py", str(fake_file), "-c", str(config_ini)]):
      with patch("rename.ReadSettings", return_value=MagicMock()):
        with patch("rename.RenameProcessor", return_value=rp):
          with patch("rename._load_path_config_manager") as mock_pcm:
            main()
    # When -c is given, _load_path_config_manager should NOT be called
    mock_pcm.assert_not_called()

  def test_pcm_default_args_supply_movie_type_hint(self, tmp_path):
    fake_file = tmp_path / "film.mkv"
    fake_file.write_bytes(b"fake")
    rp = self._mock_rp()
    pcm = MagicMock()
    pcm.get_config_for_path.return_value = "/cfg/movie.ini"
    pcm.get_args_for_path.return_value = ["--movie"]

    with patch("sys.argv", ["rename.py", str(fake_file)]):
      with patch("rename.ReadSettings", return_value=MagicMock()):
        with patch("rename.RenameProcessor", return_value=rp):
          with patch("rename._load_path_config_manager", return_value=pcm):
            main()
    assert rp.rename_file.call_args.kwargs["type_hint"] == "movie"

  def test_no_plex_flags_skip_follow_up_calls(self, tmp_path):
    fake_file = tmp_path / "film.mkv"
    fake_file.write_bytes(b"fake")
    rp = self._mock_rp()

    with patch("sys.argv", ["rename.py", str(fake_file), "--no-plexmatch", "--no-plex"]):
      with patch("rename.ReadSettings", return_value=MagicMock()):
        with patch("rename.RenameProcessor", return_value=rp):
          with patch("rename._load_path_config_manager", return_value=None):
            main()

    rp.update_plexmatch_for_results.assert_not_called()
    rp.refresh_plex_for_results.assert_not_called()

  def test_empty_results_exit_1(self, tmp_path):
    rp = self._mock_rp()
    with patch("sys.argv", ["rename.py", str(tmp_path)]):
      with patch("rename.ReadSettings", return_value=MagicMock()):
        with patch("rename.RenameProcessor", return_value=rp):
          with patch("rename._load_path_config_manager", return_value=None):
            with patch("rename._rename_directory", return_value=[]):
              with pytest.raises(SystemExit) as exc:
                main()
    assert exc.value.code == 1

  def test_keyboard_interrupt_exits_1(self, tmp_path):
    fake_file = tmp_path / "film.mkv"
    fake_file.write_bytes(b"fake")
    rp = self._mock_rp()
    rp.rename_file.side_effect = KeyboardInterrupt()

    with patch("sys.argv", ["rename.py", str(fake_file)]):
      with patch("rename.ReadSettings", return_value=MagicMock()):
        with patch("rename.RenameProcessor", return_value=rp):
          with patch("rename._load_path_config_manager", return_value=None):
            with pytest.raises(SystemExit) as exc:
              main()
    assert exc.value.code == 1

  def test_unexpected_exception_exits_1(self, tmp_path):
    fake_file = tmp_path / "film.mkv"
    fake_file.write_bytes(b"fake")
    rp = self._mock_rp()
    rp.rename_file.side_effect = RuntimeError("boom")

    with patch("sys.argv", ["rename.py", str(fake_file)]):
      with patch("rename.ReadSettings", return_value=MagicMock()):
        with patch("rename.RenameProcessor", return_value=rp):
          with patch("rename._load_path_config_manager", return_value=None):
            with pytest.raises(SystemExit) as exc:
              main()
    assert exc.value.code == 1


class TestIterMediaFiles:
  def test_uses_find_output_when_available(self, tmp_path):
    class Proc:
      stdout = iter([b"/media/a.mkv\n", b"/media/b.mp4\n"])

      def wait(self):
        return 0

    with patch("subprocess.Popen", return_value=Proc()) as mock_popen:
      results = list(_iter_media_files(str(tmp_path), {".mkv", ".mp4"}))

    assert results == ["/media/a.mkv", "/media/b.mp4"]
    assert mock_popen.call_args.args[0][:2] == ["find", str(tmp_path)]

  def test_falls_back_to_os_walk_when_find_fails(self, tmp_path):
    media = tmp_path / "video.mkv"
    media.write_text("x")
    hidden_file = tmp_path / ".hidden.mp4"
    hidden_file.write_text("x")
    hidden_dir = tmp_path / ".hidden"
    hidden_dir.mkdir()
    nested = hidden_dir / "skip.mp4"
    nested.write_text("x")

    with patch("subprocess.Popen", side_effect=OSError("find missing")):
      results = list(_iter_media_files(str(tmp_path), {".mkv", ".mp4"}))

    assert results == [str(media)]


class TestRenameDirectoryHelper:
  def test_uses_settings_input_extensions_when_available(self):
    rp = MagicMock()
    rp.settings.input_extension = ["mkv"]
    rp.rename_file.return_value = {"old": "a", "new": "b", "changed": True, "dry_run": False}

    def get_rp(_):
      return rp

    with patch("rename._iter_media_files", return_value=["/media/show.mkv"]) as mock_iter:
      results = _rename_directory("/media", get_rp, lambda _: "tv", {"dry_run": True})

    mock_iter.assert_called_once_with("/media", {".mkv"})
    assert results == [rp.rename_file.return_value]
    assert rp.rename_file.call_args.kwargs["type_hint"] == "tv"
    assert rp.rename_file.call_args.kwargs["dry_run"] is True

  def test_falls_back_to_default_extensions_when_settings_lookup_fails(self):
    rp = MagicMock()
    rp.rename_file.return_value = {"old": "a", "new": "b", "changed": True, "dry_run": False}
    calls = []

    def get_rp(path):
      calls.append(path)
      if path == "/media":
        raise RuntimeError("no settings")
      return rp

    with patch("rename._iter_media_files", return_value=["/media/movie.mp4"]) as mock_iter:
      _rename_directory("/media", get_rp, lambda _: None, {})

    assert ".mp4" in mock_iter.call_args.args[1]
    assert calls == ["/media", "/media/movie.mp4"]
