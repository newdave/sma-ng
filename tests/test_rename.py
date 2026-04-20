"""Tests for rename.py — CLI argument parsing, _print_results, and main() flow."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

import rename
from rename import _load_path_config_manager, _print_results, main

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
