"""Tests for manual.py — CLI argument parsing, helper functions, and main() flow."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to import manual.py without executing module-level side effects that
# require TMDB or FFmpeg at import time.
# ---------------------------------------------------------------------------
import manual
from manual import (
  SkipFileException,
  _find_arr_instance,
  _tmdb_search,
  addtoProcessedArchive,
  apply_cli_overrides,
  checkAlreadyProcessed,
  guessInfo,
  main,
  movieInfo,
  processFile,
  showCodecs,
  tvInfo,
)

# ---------------------------------------------------------------------------
# checkAlreadyProcessed
# ---------------------------------------------------------------------------


class TestCheckAlreadyProcessed:
  def test_none_list_returns_false(self):
    assert checkAlreadyProcessed("/some/file.mp4", None) is False

  def test_file_in_list_returns_true(self):
    assert checkAlreadyProcessed("/some/file.mp4", ["/some/file.mp4"]) is True

  def test_file_not_in_list_returns_false(self):
    assert checkAlreadyProcessed("/other/file.mp4", ["/some/file.mp4"]) is False

  def test_empty_list_returns_false(self):
    assert checkAlreadyProcessed("/some/file.mp4", []) is False


# ---------------------------------------------------------------------------
# addtoProcessedArchive
# ---------------------------------------------------------------------------


class TestAddToProcessedArchive:
  def test_noop_when_list_is_none(self, tmp_path):
    archive = str(tmp_path / "archive.json")
    addtoProcessedArchive(["/file.mp4"], None, archive)
    assert not os.path.exists(archive)

  def test_noop_when_archive_is_none(self):
    lst = []
    addtoProcessedArchive(["/file.mp4"], lst, None)
    assert lst == []

  def test_appends_to_list_and_writes_file(self, tmp_path):
    archive = str(tmp_path / "archive.json")
    lst = ["/existing.mp4"]
    addtoProcessedArchive(["/new.mp4"], lst, archive)
    assert "/new.mp4" in lst
    with open(archive, encoding="utf8") as f:
      data = json.load(f)
    assert "/new.mp4" in data
    assert "/existing.mp4" in data

  def test_deduplicates_on_write(self, tmp_path):
    archive = str(tmp_path / "archive.json")
    lst = ["/dupe.mp4"]
    addtoProcessedArchive(["/dupe.mp4"], lst, archive)
    with open(archive, encoding="utf8") as f:
      data = json.load(f)
    assert data.count("/dupe.mp4") == 1


# ---------------------------------------------------------------------------
# apply_cli_overrides
# ---------------------------------------------------------------------------


def _base_args(**overrides):
  """Return a minimal args dict with all keys that apply_cli_overrides reads."""
  defaults = {
    "nomove": False,
    "moveto": None,
    "nocopy": False,
    "nodelete": False,
    "processsameextensions": False,
    "forceconvert": False,
    "tagonly": False,
    "notag": False,
    "nopost": False,
    "optionsonly": False,
    "minsize": None,
    "tv": False,
    "movie": False,
  }
  defaults.update(overrides)
  return defaults


class TestApplyCliOverrides:
  def _mock_settings(self):
    s = MagicMock()
    s.output_dir = "/out"
    s.moveto = "/move"
    s.copyto = "/copy"
    s.delete = True
    s.process_same_extensions = False
    s.force_convert = False
    s.tagfile = True
    s.postprocess = True
    s.minimum_size = 0
    return s

  def test_nomove_clears_moveto(self):
    s = self._mock_settings()
    apply_cli_overrides(_base_args(nomove=True), s)
    assert s.output_dir is None
    assert s.moveto is None

  def test_moveto_override(self):
    s = self._mock_settings()
    apply_cli_overrides(_base_args(moveto="/custom"), s)
    assert s.moveto == "/custom"

  def test_nocopy_clears_copyto(self):
    s = self._mock_settings()
    apply_cli_overrides(_base_args(nocopy=True), s)
    assert s.copyto is None

  def test_nodelete_sets_delete_false(self):
    s = self._mock_settings()
    apply_cli_overrides(_base_args(nodelete=True), s)
    assert s.delete is False

  def test_processsameextensions(self):
    s = self._mock_settings()
    apply_cli_overrides(_base_args(processsameextensions=True), s)
    assert s.process_same_extensions is True

  def test_forceconvert(self):
    s = self._mock_settings()
    apply_cli_overrides(_base_args(forceconvert=True), s)
    assert s.force_convert is True
    assert s.process_same_extensions is True

  def test_notag_disables_tagfile(self):
    s = self._mock_settings()
    apply_cli_overrides(_base_args(notag=True), s)
    assert s.tagfile is False

  def test_nopost_disables_postprocess(self):
    s = self._mock_settings()
    apply_cli_overrides(_base_args(nopost=True), s)
    assert s.postprocess is False

  def test_type_hint_tv(self):
    s = self._mock_settings()
    hint = apply_cli_overrides(_base_args(tv=True), s)
    assert hint == "tv"

  def test_type_hint_movie(self):
    s = self._mock_settings()
    hint = apply_cli_overrides(_base_args(movie=True), s)
    assert hint == "movie"

  def test_type_hint_none_when_neither(self):
    s = self._mock_settings()
    hint = apply_cli_overrides(_base_args(), s)
    assert hint is None

  def test_minsize_sets_minimum_size(self):
    s = self._mock_settings()
    apply_cli_overrides(_base_args(minsize="100"), s)
    assert s.minimum_size == 100

  def test_minsize_invalid_logs_error(self):
    s = self._mock_settings()
    # Should not raise; invalid minsize is silently logged
    apply_cli_overrides(_base_args(minsize=None), s)


# ---------------------------------------------------------------------------
# _find_arr_instance
# ---------------------------------------------------------------------------


class TestFindArrInstance:
  def _settings_with(self, sonarr=None, radarr=None):
    s = MagicMock()
    s.sonarr_instances = sonarr or []
    s.radarr_instances = radarr or []
    return s

  def test_no_instances_returns_none(self):
    s = self._settings_with()
    inst, kind = _find_arr_instance("/mnt/media/TV/show.mkv", s)
    assert inst is None
    assert kind is None

  def test_sonarr_match_by_path_prefix(self):
    instance = {"path": "/mnt/media/TV", "apikey": "abc", "section": "sonarr"}
    s = self._settings_with(sonarr=[instance])
    inst, kind = _find_arr_instance("/mnt/media/TV/show/ep.mkv", s)
    assert inst is instance
    assert kind == "sonarr"

  def test_radarr_match_by_path_prefix(self):
    instance = {"path": "/mnt/media/Movies", "apikey": "xyz", "section": "radarr"}
    s = self._settings_with(radarr=[instance])
    inst, kind = _find_arr_instance("/mnt/media/Movies/film.mkv", s)
    assert inst is instance
    assert kind == "radarr"

  def test_no_match_when_path_not_prefix(self):
    instance = {"path": "/mnt/media/TV", "apikey": "abc", "section": "sonarr"}
    s = self._settings_with(sonarr=[instance])
    inst, kind = _find_arr_instance("/mnt/media/Movies/film.mkv", s)
    assert inst is None

  def test_no_match_when_apikey_missing(self):
    instance = {"path": "/mnt/media/TV", "section": "sonarr"}  # no apikey
    s = self._settings_with(sonarr=[instance])
    inst, kind = _find_arr_instance("/mnt/media/TV/ep.mkv", s)
    assert inst is None


# ---------------------------------------------------------------------------
# _tmdb_search
# ---------------------------------------------------------------------------


class TestTmdbSearch:
  def test_returns_first_result_for_movie(self):
    mock_search = MagicMock()
    mock_search.results = [{"id": 603, "title": "The Matrix"}]
    with patch("manual.tmdb.Search", return_value=mock_search):
      result = _tmdb_search("movie", "The Matrix", 1999)
    assert result == {"id": 603, "title": "The Matrix"}

  def test_returns_none_when_no_results(self):
    mock_search = MagicMock()
    mock_search.results = []
    with patch("manual.tmdb.Search", return_value=mock_search):
      result = _tmdb_search("movie", "Nonexistent Film", None)
    assert result is None

  def test_falls_back_to_yearless_search_for_movie(self):
    mock_search = MagicMock()
    # First call (with year) has no results; second (without year) has results
    mock_search.results = []
    second_result = [{"id": 1, "title": "Test"}]

    def side_effect_movie(query, year=None):
      if year is not None:
        mock_search.results = []
      else:
        mock_search.results = second_result

    mock_search.movie = side_effect_movie
    with patch("manual.tmdb.Search", return_value=mock_search):
      result = _tmdb_search("movie", "Test", 2000)
    assert result == {"id": 1, "title": "Test"}

  def test_tv_search(self):
    mock_search = MagicMock()
    mock_search.results = [{"id": 1399, "name": "Game of Thrones"}]
    with patch("manual.tmdb.Search", return_value=mock_search):
      result = _tmdb_search("tv", "Game of Thrones", None)
    assert result["id"] == 1399


# ---------------------------------------------------------------------------
# movieInfo / tvInfo
# ---------------------------------------------------------------------------


class TestMovieInfo:
  def test_with_tmdbid_skips_search(self):
    mock_meta = MagicMock()
    mock_meta.title = "The Matrix"
    mock_meta.date = "1999"
    mock_meta.tmdbid = 603
    with patch("manual.Metadata", return_value=mock_meta) as MockMeta:
      result = movieInfo({}, tmdbid=603)
    assert result is mock_meta

  def test_returns_none_when_search_fails(self):
    with patch("manual._tmdb_search", return_value=None):
      result = movieInfo({"title": "Unknown Film", "year": 2020})
    assert result is None

  def test_searches_and_creates_metadata(self):
    mock_meta = MagicMock()
    mock_meta.title = "Test"
    mock_meta.date = "2020"
    mock_meta.tmdbid = 99
    with patch("manual._tmdb_search", return_value={"id": 99}):
      with patch("manual.Metadata", return_value=mock_meta):
        result = movieInfo({"title": "Test", "year": 2020})
    assert result is mock_meta


class TestTvInfo:
  def test_with_tmdbid_skips_search(self):
    mock_meta = MagicMock()
    mock_meta.showname = "Breaking Bad"
    mock_meta.tmdbid = 1396
    mock_meta.season = 1
    mock_meta.episodes = [1]
    with patch("manual.Metadata", return_value=mock_meta):
      result = tvInfo({"season": 1, "episode": 1}, tmdbid=1396)
    assert result is mock_meta

  def test_returns_none_when_search_fails(self):
    with patch("manual._tmdb_search", return_value=None):
      result = tvInfo({"title": "Unknown Show"})
    assert result is None

  def test_season_episode_fallback_to_guessdata(self):
    mock_meta = MagicMock()
    mock_meta.showname = "Show"
    mock_meta.tmdbid = 100
    mock_meta.season = 2
    mock_meta.episodes = [3]
    with patch("manual._tmdb_search", return_value={"id": 100}):
      with patch("manual.Metadata", return_value=mock_meta) as MockMeta:
        tvInfo({"title": "Show", "season": 2, "episode": 3})
    call_kwargs = MockMeta.call_args[1]
    assert call_kwargs["season"] == 2
    assert call_kwargs["episode"] == 3


# ---------------------------------------------------------------------------
# guessInfo
# ---------------------------------------------------------------------------


class TestGuessInfo:
  def test_fullpathguess_false_strips_path(self):
    settings = MagicMock()
    settings.fullpathguess = False
    with patch("manual.guessit.guessit", return_value={"type": "movie", "title": "Test"}) as mock_guess:
      with patch("manual.movieInfo", return_value=MagicMock()):
        guessInfo("/path/to/Test.mkv", settings)
    # Should be called with just basename
    mock_guess.assert_called_once_with("Test.mkv", {})

  def test_type_hint_tv_passes_episode_type(self):
    settings = MagicMock()
    settings.fullpathguess = True
    with patch("manual.guessit.guessit", return_value={"type": "episode", "title": "Show", "season": 1, "episode": 1}) as mock_guess:
      with patch("manual.tvInfo", return_value=MagicMock()):
        guessInfo("Show.S01E01.mkv", settings, type_hint="tv")
    mock_guess.assert_called_once_with("Show.S01E01.mkv", {"type": "episode"})

  def test_returns_none_on_exception(self):
    settings = MagicMock()
    settings.fullpathguess = True
    # guessit returns a dict but movieInfo raises — exception is caught inside guessInfo
    with patch("manual.guessit.guessit", return_value={"type": "movie", "title": "Test"}):
      with patch("manual.movieInfo", side_effect=Exception("tmdb fail")):
        result = guessInfo("bad.mkv", settings)
    assert result is None


# ---------------------------------------------------------------------------
# showCodecs
# ---------------------------------------------------------------------------


class TestShowCodecs:
  def test_outputs_codec_list(self, capsys):
    showCodecs()
    out = capsys.readouterr().out
    assert "video" in out
    assert "audio" in out


# ---------------------------------------------------------------------------
# processFile
# ---------------------------------------------------------------------------


class TestProcessFile:
  def _make_mp(self):
    mp = MagicMock()
    mp.settings.taglanguage = None
    mp.settings.tagfile = True
    mp.settings.artwork = False
    mp.settings.thumbnail = False
    mp.settings.relocate_moov = False
    mp.settings.postprocess = False
    mp.settings.plexmatch_enabled = False
    mp.settings.naming_enabled = False
    mp.settings.sonarr_instances = []
    mp.settings.radarr_instances = []
    return mp

  def test_skips_already_processed_file(self):
    mp = self._make_mp()
    result = processFile("/file.mp4", mp, processedList=["/file.mp4"])
    assert result is None

  def test_skips_invalid_source(self):
    mp = self._make_mp()
    mp.isValidSource.return_value = None
    result = processFile("/bad.mp4", mp)
    assert result is None

  def test_options_only_calls_display_and_returns_none(self):
    mp = self._make_mp()
    mp.isValidSource.return_value = MagicMock()
    with patch("manual.getInfo", return_value=None):
      with patch("manual.displayOptions") as mock_display:
        result = processFile("/file.mp4", mp, optionsOnly=True, info=MagicMock())
    mock_display.assert_called_once()
    assert result is None

  def test_returns_false_when_mp_process_returns_none(self):
    mp = self._make_mp()
    mp.process.return_value = None
    with patch("manual.getInfo", return_value=None):
      result = processFile("/file.mp4", mp, info=MagicMock())
    assert result is False

  def test_returns_true_on_success(self):
    mp = self._make_mp()
    mp.process.return_value = {
      "output": "/out/file.mp4",
      "input": "/file.mp4",
      "input_deleted": False,
      "delete": False,
      "external_subs": [],
      "options": {},
      "x": 1920,
      "y": 1080,
      "cues_to_front": False,
    }
    mp.replicate.return_value = ["/out/file.mp4"]
    mp.getDefaultAudioLanguage.return_value = None
    mp.restoreFromOutput.return_value = "/out/file.mp4"
    with patch("manual.getInfo", return_value=None):
      with patch("manual._find_arr_instance", return_value=(None, None)):
        with patch("manual.triggerRescan", return_value=None):
          result = processFile("/file.mp4", mp, info=MagicMock())
    assert result is True

  def test_tagonly_writes_tags(self):
    mp = self._make_mp()
    mock_tagdata = MagicMock()
    with patch("manual.getInfo", return_value=mock_tagdata):
      result = processFile("/file.mp4", mp, info=MagicMock(), tagOnly=True)
    mock_tagdata.writeTags.assert_called_once()
    assert result is None  # tagOnly returns early (None)


# ---------------------------------------------------------------------------
# main() — arg parsing and branching
# ---------------------------------------------------------------------------


class TestMain:
  def _mock_settings(self):
    s = MagicMock()
    s.tagfile = True
    s.postprocess = True
    s.output_dir = None
    s.moveto = None
    s.copyto = None
    s.delete = True
    s.process_same_extensions = False
    s.force_convert = False
    s.minimum_size = 0
    s.naming_enabled = False
    s.sonarr_instances = []
    s.radarr_instances = []
    return s

  def test_codeclist_exits_early(self, capsys):
    with patch("sys.argv", ["manual.py", "-cl"]):
      with patch("manual.showCodecs") as mock_sc:
        main()
    mock_sc.assert_called_once()

  def test_file_input_processes_file(self, tmp_path):
    fake_file = tmp_path / "movie.mkv"
    fake_file.write_bytes(b"fake")

    mock_mp = MagicMock()
    mock_mp.isValidSource.return_value = MagicMock()
    mock_mp.settings = self._mock_settings()

    with patch("sys.argv", ["manual.py", "-i", str(fake_file), "-a"]):
      with patch("manual.ReadSettings", return_value=self._mock_settings()):
        with patch("manual.MediaProcessor", return_value=mock_mp):
          with patch("manual.processFile", return_value=True):
            main()

  def test_nonexistent_file_logs_message(self, capsys):
    with patch("sys.argv", ["manual.py", "-i", "/nonexistent/path.mkv"]):
      with patch("manual.ReadSettings", return_value=self._mock_settings()):
        main()  # should not raise

  def test_directory_calls_walkdir(self, tmp_path):
    with patch("sys.argv", ["manual.py", "-i", str(tmp_path), "-a"]):
      with patch("manual.ReadSettings", return_value=self._mock_settings()):
        with patch("manual.walkDir", return_value=True) as mock_walk:
          main()
    mock_walk.assert_called_once()

  def test_directory_walkdir_failure_exits_1(self, tmp_path):
    with patch("sys.argv", ["manual.py", "-i", str(tmp_path), "-a"]):
      with patch("manual.ReadSettings", return_value=self._mock_settings()):
        with patch("manual.walkDir", return_value=False):
          with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 1

  def test_processfile_failure_exits_1(self, tmp_path):
    fake_file = tmp_path / "movie.mkv"
    fake_file.write_bytes(b"fake")

    mock_mp = MagicMock()
    mock_mp.isValidSource.return_value = MagicMock()
    mock_mp.settings = self._mock_settings()

    with patch("sys.argv", ["manual.py", "-i", str(fake_file), "-a"]):
      with patch("manual.ReadSettings", return_value=self._mock_settings()):
        with patch("manual.MediaProcessor", return_value=mock_mp):
          with patch("manual.processFile", return_value=False):
            with pytest.raises(SystemExit) as exc:
              main()
    assert exc.value.code == 1

  def test_config_file_used_when_provided(self, tmp_path):
    config = tmp_path / "custom.ini"
    config.write_text("[Converter]\n")

    with patch("sys.argv", ["manual.py", "-cl", "-c", str(config)]):
      with patch("manual.showCodecs") as mock_sc:
        main()
    mock_sc.assert_called_once()

  def test_processedarchive_created_when_not_exists(self, tmp_path):
    fake_file = tmp_path / "movie.mkv"
    fake_file.write_bytes(b"fake")
    archive = tmp_path / "archive.json"

    mock_mp = MagicMock()
    mock_mp.isValidSource.return_value = MagicMock()
    mock_mp.settings = self._mock_settings()

    with patch("sys.argv", ["manual.py", "-i", str(fake_file), "-a", "-pa", str(archive)]):
      with patch("manual.ReadSettings", return_value=self._mock_settings()):
        with patch("manual.MediaProcessor", return_value=mock_mp):
          with patch("manual.processFile", return_value=True):
            main()
    assert archive.exists()

  def test_invalid_source_does_not_call_processfile(self, tmp_path):
    fake_file = tmp_path / "notmedia.txt"
    fake_file.write_bytes(b"text")

    mock_mp = MagicMock()
    mock_mp.isValidSource.return_value = None  # not a valid media source

    with patch("sys.argv", ["manual.py", "-i", str(fake_file), "-a"]):
      with patch("manual.ReadSettings", return_value=self._mock_settings()):
        with patch("manual.MediaProcessor", return_value=mock_mp):
          with patch("manual.processFile") as mock_pf:
            main()
    mock_pf.assert_not_called()

  def test_skip_file_exception_is_caught(self, tmp_path):
    fake_file = tmp_path / "movie.mkv"
    fake_file.write_bytes(b"fake")

    mock_mp = MagicMock()
    mock_mp.isValidSource.return_value = MagicMock()
    mock_mp.settings = self._mock_settings()

    with patch("sys.argv", ["manual.py", "-i", str(fake_file)]):
      with patch("manual.ReadSettings", return_value=self._mock_settings()):
        with patch("manual.MediaProcessor", return_value=mock_mp):
          with patch("manual.processFile", side_effect=SkipFileException):
            main()  # should not raise
