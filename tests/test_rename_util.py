"""Tests for resources/rename_util.py"""

import logging
import os
from unittest.mock import MagicMock, call, patch

import pytest

from resources.metadata import MediaType
from resources.rename_util import RenameProcessor, _TypeStub

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_processor(settings=None):
  if settings is None:
    settings = MagicMock()
    settings.Plex = {"host": None}
    settings.plexmatch_enabled = False
    settings.tagging_language = None
    settings.input_extension = None
    settings.sonarr_instances = []
    settings.radarr_instances = []
  with patch("resources.rename_util.MediaProcessor"):
    proc = RenameProcessor(settings, logger=logging.getLogger("test"))
  return proc


# ---------------------------------------------------------------------------
# _TypeStub
# ---------------------------------------------------------------------------


class TestTypeStub:
  def test_mediatype_stored(self):
    stub = _TypeStub(MediaType.Movie)
    assert stub.mediatype == MediaType.Movie

  def test_tv_mediatype_stored(self):
    stub = _TypeStub(MediaType.TV)
    assert stub.mediatype == MediaType.TV

  def test_all_fields_none(self):
    stub = _TypeStub(MediaType.Movie)
    for attr in ("showname", "showdata", "season", "episode", "episodes", "title", "tmdbid", "tvdbid", "imdbid", "date"):
      assert getattr(stub, attr) is None


# ---------------------------------------------------------------------------
# RenameProcessor._extract_ids_from_path
# ---------------------------------------------------------------------------


class TestExtractIdsFromPath:
  @pytest.mark.parametrize(
    "filepath,expected_ids",
    [
      ("/media/Movies/The Matrix {tmdb-603}/The Matrix {tmdb-603}.mp4", {"tmdbid": 603}),
      ("/media/TV/Show {tvdb-12345}/Season 1/ep.mp4", {"tvdbid": 12345}),
      ("/media/TV/Show {imdb-tt1234567}/ep.mp4", {"imdbid": "tt1234567"}),
      ("/media/Movies/Plain Movie/file.mp4", {}),
    ],
  )
  def test_extracts_ids(self, filepath, expected_ids):
    ids, _ = RenameProcessor._extract_ids_from_path(filepath)
    for key, val in expected_ids.items():
      assert ids[key] == val

  def test_cleans_basename(self):
    filepath = "/media/Movies/The Matrix {tmdb-603}/The Matrix {tmdb-603}.mp4"
    _, clean = RenameProcessor._extract_ids_from_path(filepath)
    assert "{tmdb-603}" not in clean
    assert "The Matrix" in clean

  def test_tmdb_in_parent_dir(self):
    filepath = "/media/Movies/The Matrix {tmdb-603}/file.mp4"
    ids, _ = RenameProcessor._extract_ids_from_path(filepath)
    assert ids.get("tmdbid") == 603

  def test_multiple_ids_first_wins(self):
    filepath = "/base/{tmdb-100}/sub/{tmdb-200}/file.mp4"
    ids, _ = RenameProcessor._extract_ids_from_path(filepath)
    # basename has no id, parent has tmdb-200, grandparent has tmdb-100
    assert "tmdbid" in ids

  def test_all_three_ids(self):
    filepath = "/base/show {tvdb-99}/season/{imdb-tt0000001}.mp4"
    ids, _ = RenameProcessor._extract_ids_from_path(filepath)
    assert ids.get("tvdbid") == 99
    assert ids.get("imdbid") == "tt0000001"


# ---------------------------------------------------------------------------
# RenameProcessor._infer_mediatype
# ---------------------------------------------------------------------------


class TestInferMediatype:
  def _proc(self):
    return _make_processor()

  def test_season_dir_returns_tv(self):
    proc = self._proc()
    result = proc._infer_mediatype("/media/TV/My Show/Season 02/ep.mp4")
    assert result == MediaType.TV

  def test_s01_dir_returns_tv(self):
    proc = self._proc()
    result = proc._infer_mediatype("/media/TV/My Show/S01/ep.mp4")
    assert result == MediaType.TV

  def test_movies_dir_returns_movie(self):
    proc = self._proc()
    result = proc._infer_mediatype("/media/Movies/The Matrix (1999)/file.mp4")
    assert result == MediaType.Movie

  def test_films_dir_returns_movie(self):
    proc = self._proc()
    result = proc._infer_mediatype("/media/Films/Some Film/file.mp4")
    assert result == MediaType.Movie

  @patch("resources.rename_util.guessit.guessit")
  def test_guessit_episode_with_season_returns_tv(self, mock_guessit):
    mock_guessit.return_value = {"type": "episode", "season": 2, "episode": 5}
    proc = self._proc()
    result = proc._infer_mediatype("/media/downloads/Show.S02E05.mp4")
    assert result == MediaType.TV

  @patch("resources.rename_util.guessit.guessit")
  def test_guessit_episode_without_season_returns_movie(self, mock_guessit):
    mock_guessit.return_value = {"type": "episode", "season": None, "episode": 57}
    proc = self._proc()
    result = proc._infer_mediatype("/media/downloads/57 Seconds.mp4")
    assert result == MediaType.Movie

  @patch("resources.rename_util.guessit.guessit")
  def test_guessit_date_returns_tv(self, mock_guessit):
    mock_guessit.return_value = {"type": "episode", "date": "2024-05-15"}
    proc = self._proc()
    result = proc._infer_mediatype("/media/downloads/The Late Show 2024-05-15.mp4")
    assert result == MediaType.TV

  @patch("resources.rename_util.guessit.guessit")
  def test_default_returns_movie(self, mock_guessit):
    mock_guessit.return_value = {"type": "movie", "title": "Some Movie"}
    proc = self._proc()
    result = proc._infer_mediatype("/media/downloads/Some.Movie.2020.mp4")
    assert result == MediaType.Movie

  @patch("resources.rename_util.guessit.guessit")
  def test_season_above_100_not_tv(self, mock_guessit):
    mock_guessit.return_value = {"type": "episode", "season": 150, "episode": 5}
    proc = self._proc()
    result = proc._infer_mediatype("/media/downloads/fake.mp4")
    assert result == MediaType.Movie


# ---------------------------------------------------------------------------
# RenameProcessor._resolve_metadata
# ---------------------------------------------------------------------------


class TestResolveMetadata:
  def _proc(self):
    proc = _make_processor()
    proc.mp = MagicMock()
    return proc

  def test_invalid_source_returns_none_none(self):
    proc = self._proc()
    proc.mp.isValidSource.return_value = None
    info, tagdata = proc._resolve_metadata("/media/fake.mp4")
    assert info is None
    assert tagdata is None

  def test_type_hint_movie_uses_movie(self):
    proc = self._proc()
    proc.mp.isValidSource.return_value = MagicMock()
    info, tagdata = proc._resolve_metadata("/media/downloads/some.mp4", type_hint="movie")
    assert tagdata.mediatype == MediaType.Movie

  def test_type_hint_tv_uses_tv(self):
    proc = self._proc()
    proc.mp.isValidSource.return_value = MagicMock()
    info, tagdata = proc._resolve_metadata("/media/downloads/some.mp4", type_hint="tv")
    assert tagdata.mediatype == MediaType.TV

  def test_season_arg_forces_tv(self):
    proc = self._proc()
    proc.mp.isValidSource.return_value = MagicMock()
    info, tagdata = proc._resolve_metadata("/media/downloads/some.mp4", season=1)
    assert tagdata.mediatype == MediaType.TV

  def test_tv_with_id_but_no_season_uses_stub(self):
    proc = self._proc()
    proc.mp.isValidSource.return_value = MagicMock()
    info, tagdata = proc._resolve_metadata("/media/downloads/some.mp4", tvdbid=12345, type_hint="tv")
    assert isinstance(tagdata, _TypeStub)
    assert tagdata.mediatype == MediaType.TV

  @patch("resources.rename_util.Metadata")
  def test_tv_with_id_and_season_episode_uses_metadata(self, mock_meta):
    mock_meta.return_value = MagicMock(mediatype=MediaType.TV)
    proc = self._proc()
    proc.mp.isValidSource.return_value = MagicMock()
    info, tagdata = proc._resolve_metadata("/media/downloads/some.mp4", tvdbid=12345, season=1, episode=2, type_hint="tv")
    mock_meta.assert_called_once()

  @patch("resources.rename_util.Metadata")
  def test_metadata_exception_falls_back_to_stub(self, mock_meta):
    mock_meta.side_effect = Exception("network error")
    proc = self._proc()
    proc.mp.isValidSource.return_value = MagicMock()
    info, tagdata = proc._resolve_metadata("/media/downloads/some.mp4", tmdbid=603, type_hint="movie")
    assert isinstance(tagdata, _TypeStub)

  def test_path_ids_extracted_when_no_explicit_ids(self):
    proc = self._proc()
    proc.mp.isValidSource.return_value = MagicMock()
    with patch.object(proc, "_lookup_tmdb_tv", return_value=_TypeStub(MediaType.TV)) as mock_lookup:
      proc._resolve_metadata("/media/TV/Show {tvdb-99}/Season 1/ep.mp4", type_hint="tv")
      # tvdb id found but no season/episode → stub returned (no lookup call for TV with id but no s/e)


# ---------------------------------------------------------------------------
# RenameProcessor.rename_file
# ---------------------------------------------------------------------------


class TestRenameFile:
  def _proc(self):
    proc = _make_processor()
    proc.mp = MagicMock()
    return proc

  def test_invalid_source_returns_unchanged(self):
    proc = self._proc()
    with patch.object(proc, "_resolve_metadata", return_value=(None, None)):
      result = proc.rename_file("/media/file.mp4")
    assert result["changed"] is False
    assert result["new"] == "/media/file.mp4"

  @patch("resources.rename_util.generate_name", return_value=None)
  def test_generate_name_none_returns_unchanged(self, _):
    proc = self._proc()
    with patch.object(proc, "_resolve_metadata", return_value=(MagicMock(), MagicMock())):
      with patch("resources.rename_util.guessit.guessit", return_value={}):
        result = proc.rename_file("/media/file.mp4")
    assert result["changed"] is False

  @patch("resources.rename_util.generate_name", return_value="file")
  def test_same_name_returns_unchanged(self, _):
    proc = self._proc()
    with patch.object(proc, "_resolve_metadata", return_value=(MagicMock(), MagicMock())):
      with patch("resources.rename_util.guessit.guessit", return_value={}):
        result = proc.rename_file("/media/file.mp4")
    assert result["changed"] is False

  @patch("resources.rename_util._rename_file", return_value="/media/New Name.mp4")
  @patch("resources.rename_util.generate_name", return_value="New Name")
  def test_rename_executed(self, _, mock_rename):
    proc = self._proc()
    with patch.object(proc, "_resolve_metadata", return_value=(MagicMock(), MagicMock())):
      with patch("resources.rename_util.guessit.guessit", return_value={}):
        result = proc.rename_file("/media/Old Name.mp4")
    assert result["changed"] is True
    assert result["new"] == "/media/New Name.mp4"

  @patch("resources.rename_util.generate_name", return_value="New Name")
  def test_dry_run_does_not_rename(self, _):
    proc = self._proc()
    with patch.object(proc, "_resolve_metadata", return_value=(MagicMock(), MagicMock())):
      with patch("resources.rename_util.guessit.guessit", return_value={}):
        with patch("resources.rename_util._rename_file") as mock_rename:
          result = proc.rename_file("/media/Old Name.mp4", dry_run=True)
    mock_rename.assert_not_called()
    assert result["dry_run"] is True
    assert result["changed"] is True
    assert "New Name" in result["new"]

  @patch("resources.rename_util._rename_file", return_value="/media/Old Name.mp4")
  @patch("resources.rename_util.generate_name", return_value="New Name")
  def test_rename_returning_original_marks_unchanged(self, _, __):
    proc = self._proc()
    with patch.object(proc, "_resolve_metadata", return_value=(MagicMock(), MagicMock())):
      with patch("resources.rename_util.guessit.guessit", return_value={}):
        result = proc.rename_file("/media/Old Name.mp4")
    assert result["changed"] is False

  def test_use_arr_delegates_to_arr_method(self):
    proc = self._proc()
    with patch.object(proc, "_rename_file_via_arr", return_value={"old": "/f.mp4", "new": "/new.mp4", "changed": True, "dry_run": False}) as mock_arr:
      result = proc.rename_file("/f.mp4", use_arr=True)
    mock_arr.assert_called_once()
    assert result["changed"] is True


# ---------------------------------------------------------------------------
# RenameProcessor._rename_file_via_arr
# ---------------------------------------------------------------------------


class TestRenameFileViaArr:
  def _proc(self, sonarr=None, radarr=None):
    settings = MagicMock()
    settings.Plex = {"host": None}
    settings.sonarr_instances = sonarr or []
    settings.radarr_instances = radarr or []
    with patch("resources.rename_util.MediaProcessor"):
      proc = RenameProcessor(settings, logger=logging.getLogger("test"))
    return proc

  def test_no_matching_instance_logs_warning(self):
    proc = self._proc()
    result_stub = {"old": "/media/TV/file.mp4", "new": "/media/TV/file.mp4", "changed": False, "dry_run": False}
    result = proc._rename_file_via_arr("/media/TV/file.mp4", dict(result_stub))
    assert result["changed"] is False

  def test_sonarr_instance_matched(self):
    inst = {"path": "/media/TV", "apikey": "abc", "host": "localhost", "port": 8989, "ssl": False, "web_root": ""}
    proc = self._proc(sonarr=[inst])
    result_stub = {"old": "/media/TV/file.mp4", "new": "/media/TV/file.mp4", "changed": False, "dry_run": False}
    with patch("resources.mediamanager.build_api", return_value=("http://localhost:8989", {})):
      with patch("resources.mediamanager.rename_via_arr", return_value="/media/TV/New File.mp4"):
        result = proc._rename_file_via_arr("/media/TV/file.mp4", dict(result_stub))
    assert result["changed"] is True
    assert result["new"] == "/media/TV/New File.mp4"

  def test_radarr_instance_matched(self):
    inst = {"path": "/media/Movies", "apikey": "xyz", "host": "localhost", "port": 7878, "ssl": False, "web_root": ""}
    proc = self._proc(radarr=[inst])
    result_stub = {"old": "/media/Movies/file.mp4", "new": "/media/Movies/file.mp4", "changed": False, "dry_run": False}
    with patch("resources.mediamanager.build_api", return_value=("http://localhost:7878", {})):
      with patch("resources.mediamanager.rename_via_arr", return_value="/media/Movies/New File.mp4"):
        result = proc._rename_file_via_arr("/media/Movies/file.mp4", dict(result_stub))
    assert result["changed"] is True

  def test_arr_returns_none_logs_warning(self):
    inst = {"path": "/media/TV", "apikey": "abc", "host": "localhost", "port": 8989, "ssl": False, "web_root": ""}
    proc = self._proc(sonarr=[inst])
    result_stub = {"old": "/media/TV/file.mp4", "new": "/media/TV/file.mp4", "changed": False, "dry_run": False}
    with patch("resources.mediamanager.build_api", return_value=("http://localhost:8989", {})):
      with patch("resources.mediamanager.rename_via_arr", return_value=None):
        result = proc._rename_file_via_arr("/media/TV/file.mp4", dict(result_stub))
    assert result["changed"] is False


# ---------------------------------------------------------------------------
# RenameProcessor.rename_directory
# ---------------------------------------------------------------------------


class TestRenameDirectory:
  def _proc(self):
    proc = _make_processor()
    proc.mp = MagicMock()
    return proc

  def test_processes_media_files(self, tmp_path):
    proc = self._proc()
    (tmp_path / "file.mp4").write_text("x")
    (tmp_path / "file.mkv").write_text("x")
    (tmp_path / "file.nfo").write_text("x")
    with patch.object(proc, "rename_file", return_value={"old": "", "new": "", "changed": False, "dry_run": False}) as mock_rename:
      results = proc.rename_directory(str(tmp_path))
    assert mock_rename.call_count == 2

  def test_skips_hidden_files(self, tmp_path):
    proc = self._proc()
    (tmp_path / ".hidden.mp4").write_text("x")
    (tmp_path / "visible.mp4").write_text("x")
    with patch.object(proc, "rename_file", return_value={"old": "", "new": "", "changed": False, "dry_run": False}) as mock_rename:
      results = proc.rename_directory(str(tmp_path))
    assert mock_rename.call_count == 1

  def test_skips_hidden_directories(self, tmp_path):
    proc = self._proc()
    hidden_dir = tmp_path / ".hidden"
    hidden_dir.mkdir()
    (hidden_dir / "file.mp4").write_text("x")
    with patch.object(proc, "rename_file", return_value={"old": "", "new": "", "changed": False, "dry_run": False}) as mock_rename:
      results = proc.rename_directory(str(tmp_path))
    mock_rename.assert_not_called()

  def test_recurses_into_subdirectories(self, tmp_path):
    proc = self._proc()
    subdir = tmp_path / "Season 1"
    subdir.mkdir()
    (subdir / "ep.mp4").write_text("x")
    with patch.object(proc, "rename_file", return_value={"old": "", "new": "", "changed": False, "dry_run": False}) as mock_rename:
      results = proc.rename_directory(str(tmp_path))
    assert mock_rename.call_count == 1

  def test_uses_custom_extensions_from_settings(self, tmp_path):
    settings = MagicMock()
    settings.Plex = {"host": None}
    settings.plexmatch_enabled = False
    settings.input_extension = ["mkv"]
    settings.sonarr_instances = []
    settings.radarr_instances = []
    with patch("resources.rename_util.MediaProcessor"):
      proc = RenameProcessor(settings, logger=logging.getLogger("test"))
    proc.mp = MagicMock()
    (tmp_path / "file.mkv").write_text("x")
    (tmp_path / "file.mp4").write_text("x")
    with patch.object(proc, "rename_file", return_value={"old": "", "new": "", "changed": False, "dry_run": False}) as mock_rename:
      results = proc.rename_directory(str(tmp_path))
    assert mock_rename.call_count == 1

  def test_returns_results_list(self, tmp_path):
    proc = self._proc()
    (tmp_path / "a.mp4").write_text("x")
    (tmp_path / "b.mp4").write_text("x")
    expected = {"old": "", "new": "", "changed": False, "dry_run": False}
    with patch.object(proc, "rename_file", return_value=expected):
      results = proc.rename_directory(str(tmp_path))
    assert len(results) == 2

  def test_passes_kwargs_to_rename_file(self, tmp_path):
    proc = self._proc()
    (tmp_path / "file.mp4").write_text("x")
    with patch.object(proc, "rename_file", return_value={"old": "", "new": "", "changed": False, "dry_run": False}) as mock_rename:
      proc.rename_directory(str(tmp_path), tmdbid=603, dry_run=True, type_hint="movie")
    _, kwargs = mock_rename.call_args
    assert kwargs["tmdbid"] == 603
    assert kwargs["dry_run"] is True
    assert kwargs["type_hint"] == "movie"


# ---------------------------------------------------------------------------
# RenameProcessor.update_plexmatch_for_results
# ---------------------------------------------------------------------------


class TestUpdatePlexmatchForResults:
  def test_disabled_setting_skips(self):
    proc = _make_processor()
    proc.settings.plexmatch_enabled = False
    results = [{"changed": True, "dry_run": False, "new": "/media/file.mp4"}]
    with patch("resources.rename_util.update_plexmatch") as mock_pm:
      proc.update_plexmatch_for_results(results)
    mock_pm.assert_not_called()

  def test_skips_dry_run_results(self):
    proc = _make_processor()
    proc.settings.plexmatch_enabled = True
    results = [{"changed": True, "dry_run": True, "new": "/media/file.mp4"}]
    with patch("resources.rename_util.update_plexmatch") as mock_pm:
      proc.update_plexmatch_for_results(results)
    mock_pm.assert_not_called()

  def test_skips_unchanged_results(self):
    proc = _make_processor()
    proc.settings.plexmatch_enabled = True
    results = [{"changed": False, "dry_run": False, "new": "/media/file.mp4"}]
    with patch("resources.rename_util.update_plexmatch") as mock_pm:
      proc.update_plexmatch_for_results(results)
    mock_pm.assert_not_called()

  def test_calls_update_plexmatch_for_changed(self):
    proc = _make_processor()
    proc.settings.plexmatch_enabled = True
    results = [{"changed": True, "dry_run": False, "new": "/media/file.mp4"}]
    with patch("resources.rename_util.update_plexmatch") as mock_pm:
      proc.update_plexmatch_for_results(results)
    mock_pm.assert_called_once_with("/media/file.mp4", None, proc.settings, log=proc.log)

  def test_exception_is_swallowed(self):
    proc = _make_processor()
    proc.settings.plexmatch_enabled = True
    results = [{"changed": True, "dry_run": False, "new": "/media/file.mp4"}]
    with patch("resources.rename_util.update_plexmatch", side_effect=Exception("boom")):
      proc.update_plexmatch_for_results(results)  # must not raise


# ---------------------------------------------------------------------------
# RenameProcessor.refresh_plex_for_results
# ---------------------------------------------------------------------------


class TestRefreshPlexForResults:
  def test_no_host_skips(self):
    proc = _make_processor()
    proc.settings.Plex = {"host": None}
    results = [{"changed": True, "dry_run": False, "new": "/media/Movies/file.mp4"}]
    with patch("resources.rename_util.refreshPlex") as mock_plex:
      proc.refresh_plex_for_results(results)
    mock_plex.assert_not_called()

  def test_no_token_skips(self):
    proc = _make_processor()
    proc.settings.Plex = {"host": "localhost", "token": None}
    results = [{"changed": True, "dry_run": False, "new": "/media/Movies/file.mp4"}]
    with patch("resources.rename_util.refreshPlex") as mock_plex:
      proc.refresh_plex_for_results(results)
    mock_plex.assert_not_called()

  def test_calls_refresh_for_changed(self):
    proc = _make_processor()
    proc.settings.Plex = {"host": "localhost", "token": "secret"}
    results = [{"changed": True, "dry_run": False, "new": "/media/Movies/file.mp4"}]
    with patch("resources.rename_util.refreshPlex") as mock_plex:
      proc.refresh_plex_for_results(results)
    mock_plex.assert_called_once()

  def test_skips_dry_run(self):
    proc = _make_processor()
    proc.settings.Plex = {"host": "localhost", "token": "secret"}
    results = [{"changed": True, "dry_run": True, "new": "/media/Movies/file.mp4"}]
    with patch("resources.rename_util.refreshPlex") as mock_plex:
      proc.refresh_plex_for_results(results)
    mock_plex.assert_not_called()

  def test_skips_unchanged(self):
    proc = _make_processor()
    proc.settings.Plex = {"host": "localhost", "token": "secret"}
    results = [{"changed": False, "dry_run": False, "new": "/media/Movies/file.mp4"}]
    with patch("resources.rename_util.refreshPlex") as mock_plex:
      proc.refresh_plex_for_results(results)
    mock_plex.assert_not_called()

  def test_deduplicates_by_directory(self):
    proc = _make_processor()
    proc.settings.Plex = {"host": "localhost", "token": "secret"}
    results = [
      {"changed": True, "dry_run": False, "new": "/media/Movies/a.mp4"},
      {"changed": True, "dry_run": False, "new": "/media/Movies/b.mp4"},
    ]
    with patch("resources.rename_util.refreshPlex") as mock_plex:
      proc.refresh_plex_for_results(results)
    assert mock_plex.call_count == 1

  def test_exception_is_swallowed(self):
    proc = _make_processor()
    proc.settings.Plex = {"host": "localhost", "token": "secret"}
    results = [{"changed": True, "dry_run": False, "new": "/media/Movies/file.mp4"}]
    with patch("resources.rename_util.refreshPlex", side_effect=Exception("plex down")):
      proc.refresh_plex_for_results(results)  # must not raise


# ---------------------------------------------------------------------------
# RenameProcessor._find_season_for_date / _find_episode_for_date
# ---------------------------------------------------------------------------


class TestFindSeasonForDate:
  def test_returns_correct_season(self):
    mock_tmdb = MagicMock()
    mock_tmdb.TV.return_value.info.return_value = {
      "seasons": [
        {"season_number": 1, "air_date": "2020-01-01"},
        {"season_number": 2, "air_date": "2021-01-01"},
        {"season_number": 3, "air_date": "2022-01-01"},
      ]
    }
    result = RenameProcessor._find_season_for_date(mock_tmdb, 123, "2021-06-15")
    assert result == 2

  def test_returns_none_on_exception(self):
    mock_tmdb = MagicMock()
    mock_tmdb.TV.return_value.info.side_effect = Exception("api error")
    result = RenameProcessor._find_season_for_date(mock_tmdb, 123, "2021-06-15")
    assert result is None

  def test_returns_none_when_no_season_before_date(self):
    mock_tmdb = MagicMock()
    mock_tmdb.TV.return_value.info.return_value = {"seasons": [{"season_number": 1, "air_date": "2025-01-01"}]}
    result = RenameProcessor._find_season_for_date(mock_tmdb, 123, "2020-06-15")
    assert result is None

  def test_skips_season_zero(self):
    mock_tmdb = MagicMock()
    mock_tmdb.TV.return_value.info.return_value = {
      "seasons": [
        {"season_number": 0, "air_date": "2019-01-01"},
        {"season_number": 1, "air_date": "2020-01-01"},
      ]
    }
    result = RenameProcessor._find_season_for_date(mock_tmdb, 123, "2020-06-01")
    assert result == 1


class TestFindEpisodeForDate:
  def test_returns_episode_number_on_match(self):
    mock_tmdb = MagicMock()
    mock_tmdb.TV_Seasons.return_value.info.return_value = {
      "episodes": [
        {"episode_number": 1, "air_date": "2021-01-05"},
        {"episode_number": 2, "air_date": "2021-01-12"},
      ]
    }
    result = RenameProcessor._find_episode_for_date(mock_tmdb, 123, 1, "2021-01-12")
    assert result == 2

  def test_returns_none_when_no_match(self):
    mock_tmdb = MagicMock()
    mock_tmdb.TV_Seasons.return_value.info.return_value = {"episodes": [{"episode_number": 1, "air_date": "2021-01-05"}]}
    result = RenameProcessor._find_episode_for_date(mock_tmdb, 123, 1, "2021-03-01")
    assert result is None

  def test_returns_none_on_exception(self):
    mock_tmdb = MagicMock()
    mock_tmdb.TV_Seasons.return_value.info.side_effect = Exception("timeout")
    result = RenameProcessor._find_episode_for_date(mock_tmdb, 123, 1, "2021-01-12")
    assert result is None
