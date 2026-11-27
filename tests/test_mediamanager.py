"""Tests for resources/mediamanager.py - Sonarr/Radarr API helpers."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from resources.mediamanager import (
  REQUEST_TIMEOUT,
  api_command,
  api_get,
  api_put,
  build_api,
  downloaded_scan_via_arr,
  find_arr_instance,
  matching_arr_instances,
  rename,
  rename_via_arr,
  rescan,
  rescan_via_arr,
  wait_for_command,
)


class TestBuildApi:
  def test_http_url(self):
    settings = {"ssl": False, "host": "localhost", "port": 8989, "webroot": "", "apikey": "abc123"}
    url, headers = build_api(settings, "SMA-NG")
    assert url == "http://localhost:8989"
    assert headers["X-Api-Key"] == "abc123"
    assert headers["User-Agent"] == "SMA-NG"

  def test_https_url(self):
    settings = {"ssl": True, "host": "sonarr.local", "port": 443, "webroot": "/sonarr", "apikey": "key"}
    url, _ = build_api(settings, "SMA")
    assert url == "https://sonarr.local:443/sonarr"

  def test_webroot_included(self):
    settings = {"ssl": False, "host": "localhost", "port": 8989, "webroot": "/api-root", "apikey": "x"}
    url, _ = build_api(settings, "UA")
    assert url.endswith("/api-root")


class TestApiCommand:
  @patch("resources.mediamanager.requests.post")
  def test_posts_to_command_endpoint(self, mock_post):
    mock_post.return_value.json.return_value = {"id": 42, "status": "queued"}
    log = MagicMock()
    result = api_command("http://localhost:8989", {"X-Api-Key": "k"}, {"name": "RescanSeries"}, log)
    mock_post.assert_called_once_with("http://localhost:8989/api/v3/command", json={"name": "RescanSeries"}, headers={"X-Api-Key": "k"}, timeout=REQUEST_TIMEOUT)
    assert result["id"] == 42

  @patch("resources.mediamanager.requests.post")
  def test_unwraps_array_response(self, mock_post):
    mock_post.return_value.json.return_value = [{"id": 1, "status": "started"}]
    log = MagicMock()
    result = api_command("http://localhost:8989", {}, {}, log)
    assert result["id"] == 1

  @patch("resources.mediamanager.requests.post")
  def test_handles_non_indexable_response(self, mock_post):
    mock_post.return_value.json.return_value = {"id": 5, "status": "queued"}
    log = MagicMock()
    result = api_command("http://localhost:8989", {}, {}, log)
    assert result["id"] == 5


class TestWaitForCommand:
  @patch("resources.mediamanager.time.sleep")
  @patch("resources.mediamanager.requests.get")
  def test_returns_true_when_completed(self, mock_get, mock_sleep):
    mock_get.return_value.json.return_value = {"status": "completed"}
    log = MagicMock()
    assert wait_for_command("http://localhost:8989", {}, 42, log) is True
    mock_sleep.assert_not_called()

  @patch("resources.mediamanager.time.sleep")
  @patch("resources.mediamanager.requests.get")
  def test_returns_true_on_complete(self, mock_get, mock_sleep):
    mock_get.return_value.json.return_value = {"status": "complete"}
    log = MagicMock()
    assert wait_for_command("http://localhost:8989", {}, 42, log) is True

  @patch("resources.mediamanager.time.sleep")
  @patch("resources.mediamanager.requests.get")
  def test_polls_until_completed(self, mock_get, mock_sleep):
    mock_get.return_value.json.side_effect = [
      {"status": "started"},
      {"status": "started"},
      {"status": "completed"},
    ]
    log = MagicMock()
    assert wait_for_command("http://localhost:8989", {}, 42, log, delay=1) is True
    assert mock_sleep.call_count == 2

  @patch("resources.mediamanager.time.sleep")
  @patch("resources.mediamanager.requests.get")
  def test_backoff_doubles_up_to_max(self, mock_get, mock_sleep):
    mock_get.return_value.json.side_effect = [{"status": "started"}] * 5 + [{"status": "completed"}]
    wait_for_command("http://localhost:8989", {}, 42, MagicMock(), timeout=1000, delay=2, max_delay=10)
    assert [c.args[0] for c in mock_sleep.call_args_list] == [2, 4, 8, 10, 10]

  def _fake_clock(self):
    now = [0.0]
    return now, (lambda: now[0]), (lambda s: now.__setitem__(0, now[0] + s))

  @patch("resources.mediamanager.requests.get")
  def test_returns_false_on_timeout_and_logs_last_status(self, mock_get):
    now, clock, sleep = self._fake_clock()
    mock_get.return_value.json.return_value = {"status": "started"}
    log = MagicMock()
    with patch("resources.mediamanager.time.monotonic", clock), patch("resources.mediamanager.time.sleep", sleep):
      assert wait_for_command("http://localhost:8989", {}, 42, log, timeout=300) is False
    assert now[0] == 300
    assert "started" in log.warning.call_args.args[0]
    assert "300" in log.warning.call_args.args[0]

  @patch("resources.mediamanager.requests.get")
  def test_slow_command_completing_within_budget(self, mock_get):
    now, clock, sleep = self._fake_clock()
    mock_get.return_value.json.side_effect = lambda: {"status": "completed" if now[0] >= 120 else "started"}
    with patch("resources.mediamanager.time.monotonic", clock), patch("resources.mediamanager.time.sleep", sleep):
      assert wait_for_command("http://localhost:8989", {}, 42, MagicMock()) is True

  @pytest.mark.parametrize("state", ["failed", "aborted", "cancelled", "orphaned", "Failed"])
  @patch("resources.mediamanager.time.sleep")
  @patch("resources.mediamanager.requests.get")
  def test_terminal_state_stops_early_and_logs_status(self, mock_get, mock_sleep, state):
    mock_get.return_value.json.return_value = {"status": state}
    log = MagicMock()
    assert wait_for_command("http://localhost:8989", {}, 42, log) is False
    mock_sleep.assert_not_called()
    assert state.lower() in log.warning.call_args.args[0]

  @patch("resources.mediamanager.time.sleep")
  @patch("resources.mediamanager.requests.get")
  def test_http_error_on_poll_is_retried(self, mock_get, mock_sleep):
    ok = MagicMock()
    ok.json.return_value = {"status": "completed"}
    mock_get.side_effect = [requests.ConnectionError("boom"), ValueError("bad json"), ok]
    log = MagicMock()
    assert wait_for_command("http://localhost:8989", {}, 42, log) is True
    assert log.warning.call_count == 2

  @patch("resources.mediamanager.requests.get")
  def test_persistent_http_error_times_out_false(self, mock_get):
    now, clock, sleep = self._fake_clock()
    mock_get.side_effect = requests.ConnectionError("down")
    log = MagicMock()
    with patch("resources.mediamanager.time.monotonic", clock), patch("resources.mediamanager.time.sleep", sleep):
      assert wait_for_command("http://localhost:8989", {}, 42, log, timeout=60) is False
    assert "unknown" in log.warning.call_args.args[0]


class TestApiGet:
  @patch("resources.mediamanager.requests.get")
  def test_gets_endpoint(self, mock_get):
    mock_get.return_value.json.return_value = [{"id": 1}]
    log = MagicMock()
    result = api_get("http://localhost:8989", {"X-Api-Key": "k"}, "series", log)
    mock_get.assert_called_once_with("http://localhost:8989/api/v3/series", headers={"X-Api-Key": "k"}, timeout=REQUEST_TIMEOUT)
    assert result == [{"id": 1}]


class TestApiPut:
  @patch("resources.mediamanager.requests.put")
  def test_puts_data(self, mock_put):
    mock_put.return_value.json.return_value = {"id": 1, "monitored": True}
    log = MagicMock()
    result = api_put("http://localhost:8989", {}, "series/1", {"monitored": True}, log)
    mock_put.assert_called_once()
    assert result["monitored"] is True


class TestRescan:
  @patch("resources.mediamanager.wait_for_command")
  @patch("resources.mediamanager.api_command")
  def test_calls_command_and_waits(self, mock_cmd, mock_wait):
    mock_cmd.return_value = {"id": 10}
    mock_wait.return_value = True
    log = MagicMock()
    result = rescan("http://localhost:8989", {}, "RescanSeries", "seriesId", 123, log)
    mock_cmd.assert_called_once_with("http://localhost:8989", {}, {"name": "RescanSeries", "seriesId": 123}, log)
    mock_wait.assert_called_once_with("http://localhost:8989", {}, 10, log)
    assert result is True


class TestRename:
  @patch("resources.mediamanager.wait_for_command")
  @patch("resources.mediamanager.api_command")
  def test_rename_with_file_id(self, mock_cmd, mock_wait):
    mock_cmd.return_value = {"id": 20}
    log = MagicMock()
    rename("http://localhost:8989", {}, 55, "RenameFiles", "RenameSeries", "seriesId", 123, log)
    payload = mock_cmd.call_args[0][2]
    assert payload["name"] == "RenameFiles"
    assert payload["files"] == [55]
    assert payload["seriesId"] == 123

  @patch("resources.mediamanager.wait_for_command")
  @patch("resources.mediamanager.api_command")
  def test_rename_without_file_id(self, mock_cmd, mock_wait):
    mock_cmd.return_value = {"id": 21}
    log = MagicMock()
    rename("http://localhost:8989", {}, None, "RenameFiles", "RenameSeries", "seriesId", 123, log)
    payload = mock_cmd.call_args[0][2]
    assert payload["name"] == "RenameSeries"
    assert payload["seriesIds"] == [123]


def _make_response(data):
  mock = MagicMock()
  mock.json.return_value = data
  return mock


class TestRenameViaArr:
  @patch("resources.mediamanager.wait_for_command")
  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_sonarr_success(self, mock_get, mock_post, mock_wait):
    mock_get.side_effect = [
      _make_response({"series": {"id": 10, "title": "Show"}, "episodes": [{"episodeNumber": 1}]}),
      _make_response([{"id": 55, "path": "/tv/Show/S01E01.mp4"}]),
      _make_response({"id": 55, "path": "/tv/Show/Show - S01E01.mp4"}),
    ]
    mock_post.return_value = _make_response({"id": 99, "status": "queued"})
    mock_wait.return_value = True
    log = MagicMock()
    result = rename_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Show/S01E01.mp4", log)
    assert result == "/tv/Show/Show - S01E01.mp4"

  @patch("resources.mediamanager.wait_for_command")
  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_sonarr_no_series_found(self, mock_get, mock_post, mock_wait):
    mock_get.side_effect = [
      _make_response({"series": None}),
    ]
    log = MagicMock()
    result = rename_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Show/S01E01.mp4", log)
    assert result is None

  @patch("resources.mediamanager.wait_for_command")
  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_sonarr_no_matching_file(self, mock_get, mock_post, mock_wait):
    mock_get.side_effect = [
      _make_response({"series": {"id": 10, "title": "Show"}, "episodes": [{"episodeNumber": 1}]}),
      _make_response([{"id": 55, "path": "/tv/Show/OtherEpisode.mp4"}]),
    ]
    log = MagicMock()
    result = rename_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Show/S01E01.mp4", log)
    assert result is None

  @patch("resources.mediamanager.wait_for_command")
  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_sonarr_command_fails(self, mock_get, mock_post, mock_wait):
    mock_get.side_effect = [
      _make_response({"series": {"id": 10, "title": "Show"}, "episodes": [{"episodeNumber": 1}]}),
      _make_response([{"id": 55, "path": "/tv/Show/S01E01.mp4"}]),
    ]
    mock_post.return_value = _make_response({"id": 99, "status": "queued"})
    mock_wait.return_value = False
    log = MagicMock()
    result = rename_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Show/S01E01.mp4", log)
    assert result is None

  @patch("resources.mediamanager.wait_for_command")
  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_radarr_success(self, mock_get, mock_post, mock_wait):
    mock_get.side_effect = [
      _make_response({"movie": {"id": 20, "title": "Film"}}),
      _make_response([{"id": 77, "path": "/movies/Film.mp4"}]),
      _make_response({"id": 77, "path": "/movies/Film (2020).mp4"}),
    ]
    mock_post.return_value = _make_response({"id": 88, "status": "queued"})
    mock_wait.return_value = True
    log = MagicMock()
    result = rename_via_arr("http://localhost:8989", {}, "radarr", "/movies/Film.mp4", log)
    assert result == "/movies/Film (2020).mp4"

  @patch("resources.mediamanager.wait_for_command")
  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_radarr_moviefile_as_dict(self, mock_get, mock_post, mock_wait):
    mock_get.side_effect = [
      _make_response({"movie": {"id": 20, "title": "Film"}}),
      _make_response({"id": 77, "path": "/movies/Film.mp4"}),
      _make_response({"id": 77, "path": "/movies/Film (2020).mp4"}),
    ]
    mock_post.return_value = _make_response({"id": 88, "status": "queued"})
    mock_wait.return_value = True
    log = MagicMock()
    result = rename_via_arr("http://localhost:8989", {}, "radarr", "/movies/Film.mp4", log)
    assert result == "/movies/Film (2020).mp4"

  @patch("resources.mediamanager.requests.get")
  def test_exception_returns_none(self, mock_get):
    mock_get.side_effect = Exception("network error")
    log = MagicMock()
    result = rename_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Show/S01E01.mp4", log)
    assert result is None

  @pytest.mark.parametrize(
    ("arr_type", "parse_body", "files_endpoint", "id_field", "media_id"),
    [
      ("sonarr", {"series": {"id": 10}}, "episodefile", "seriesId", 10),
      ("radarr", {"movie": {"id": 20}}, "moviefile", "movieId", 20),
    ],
  )
  @patch("resources.mediamanager.wait_for_command", return_value=True)
  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_requests_per_arr_type(self, mock_get, mock_post, mock_wait, arr_type, parse_body, files_endpoint, id_field, media_id):
    mock_get.side_effect = [
      _make_response(parse_body),
      _make_response([{"id": 5, "path": "/m/a.mp4"}]),
      _make_response({"id": 5, "path": "/m/b.mp4"}),
    ]
    mock_post.return_value = _make_response({"id": 9})
    assert rename_via_arr("http://h", {"X": "1"}, arr_type, "/m/a.mp4", MagicMock()) == "/m/b.mp4"
    urls = [c.args[0] for c in mock_get.call_args_list]
    assert urls == ["http://h/api/v3/parse", "http://h/api/v3/" + files_endpoint, "http://h/api/v3/%s/5" % files_endpoint]
    assert mock_get.call_args_list[0].kwargs["params"] == {"title": "a.mp4"}
    assert mock_get.call_args_list[1].kwargs["params"] == {id_field: media_id}
    assert mock_post.call_args.kwargs["json"] == {"name": "RenameFiles", id_field: media_id, "files": [5]}

  @patch("resources.mediamanager.wait_for_command", return_value=True)
  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_sonarr_episodefile_as_dict(self, mock_get, mock_post, mock_wait):
    """Behaviour change (SMANG-32): a single-record dict is accepted for Sonarr too, as it was for Radarr."""
    mock_get.side_effect = [
      _make_response({"series": {"id": 10}}),
      _make_response({"id": 55, "path": "/tv/Show/S01E01.mp4"}),
      _make_response({"id": 55, "path": "/tv/Show/Show - S01E01.mp4"}),
    ]
    mock_post.return_value = _make_response({"id": 99})
    log = MagicMock()
    assert rename_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Show/S01E01.mp4", log) == "/tv/Show/Show - S01E01.mp4"
    log.exception.assert_not_called()


class TestRescanViaArr:
  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_sonarr_issues_rescan_series(self, mock_get, mock_post):
    mock_get.return_value = _make_response({"series": {"id": 10, "title": "Show"}})
    mock_post.return_value = _make_response({"id": 99, "status": "queued"})
    log = MagicMock()
    cmd_id = rescan_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Show/S01E01.mkv", log)
    assert cmd_id == 99
    body = mock_post.call_args[1]["json"]
    assert body["name"] == "RescanSeries"
    assert body["seriesId"] == 10

  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_radarr_issues_rescan_movie(self, mock_get, mock_post):
    mock_get.return_value = _make_response({"movie": {"id": 42, "title": "Film"}})
    mock_post.return_value = _make_response({"id": 17, "status": "queued"})
    log = MagicMock()
    cmd_id = rescan_via_arr("http://localhost:7878", {}, "radarr", "/movies/Film/Film.mkv", log)
    assert cmd_id == 17
    body = mock_post.call_args[1]["json"]
    assert body["name"] == "RescanMovie"
    assert body["movieIds"] == [42]

  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_sonarr_unmatched_series_returns_none(self, mock_get, mock_post):
    mock_get.return_value = _make_response({"series": None})
    log = MagicMock()
    assert rescan_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Unknown.mkv", log) is None
    mock_post.assert_not_called()

  @patch("resources.mediamanager.requests.get")
  def test_exception_returns_none(self, mock_get):
    mock_get.side_effect = Exception("network error")
    log = MagicMock()
    assert rescan_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Show/S01E01.mkv", log) is None


class TestDownloadedScanViaArr:
  @patch("resources.mediamanager.requests.post")
  def test_sonarr_issues_downloaded_episodes_scan(self, mock_post):
    mock_post.return_value = _make_response({"id": 123, "status": "queued"})
    log = MagicMock()
    cmd_id = downloaded_scan_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Show/S01E01.mp4", log)
    assert cmd_id == 123
    body = mock_post.call_args[1]["json"]
    assert body["name"] == "DownloadedEpisodesScan"
    assert body["path"] == "/tv/Show/S01E01.mp4"
    assert body["importMode"] == "Move"

  @patch("resources.mediamanager.requests.post")
  def test_radarr_issues_downloaded_movies_scan(self, mock_post):
    mock_post.return_value = _make_response({"id": 456, "status": "queued"})
    log = MagicMock()
    cmd_id = downloaded_scan_via_arr("http://localhost:7878", {}, "radarr", "/movies/Film/Film.mp4", log)
    assert cmd_id == 456
    body = mock_post.call_args[1]["json"]
    assert body["name"] == "DownloadedMoviesScan"
    assert body["path"] == "/movies/Film/Film.mp4"
    assert body["importMode"] == "Move"

  @patch("resources.mediamanager.requests.post")
  def test_exception_returns_none(self, mock_post):
    mock_post.side_effect = Exception("network error")
    log = MagicMock()
    assert downloaded_scan_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Show/S01E01.mp4", log) is None


class TestFindArrInstance:
  """One lookup for every Sonarr/Radarr path match (manual, rename, naming)."""

  @staticmethod
  def _settings(sonarr=None, radarr=None):
    s = MagicMock()
    s.sonarr_instances = sonarr or []
    s.radarr_instances = radarr or []
    return s

  def test_no_instances(self):
    assert find_arr_instance("/media/TV/Show/e.mkv", self._settings()) == (None, None)

  def test_sonarr_and_radarr_prefix_match(self):
    tv = {"path": "/media/TV", "apikey": "a"}
    mv = {"path": "/media/Movies", "apikey": "b"}
    s = self._settings(sonarr=[tv], radarr=[mv])
    assert find_arr_instance("/media/TV/Show/e.mkv", s) == (tv, "sonarr")
    assert find_arr_instance("/media/Movies/F/f.mkv", s) == (mv, "radarr")

  def test_separator_aware(self):
    s = self._settings(sonarr=[{"path": "/media/TV", "apikey": "a"}])
    assert find_arr_instance("/media/TV-Anime/S/e.mkv", s) == (None, None)

  def test_longest_prefix_wins_across_kinds(self):
    broad = {"path": "/media", "apikey": "a"}
    narrow = {"path": "/media/Movies", "apikey": "b"}
    s = self._settings(sonarr=[broad], radarr=[narrow])
    assert find_arr_instance("/media/Movies/F/f.mkv", s) == (narrow, "radarr")

  def test_longest_prefix_wins_within_kind_regardless_of_order(self):
    broad = {"path": "/media/TV", "apikey": "a"}
    narrow = {"path": "/media/TV/Kids", "apikey": "b"}
    s = self._settings(sonarr=[broad, narrow])
    assert find_arr_instance("/media/TV/Kids/S/e.mkv", s) == (narrow, "sonarr")

  def test_equal_length_tie_prefers_sonarr(self):
    tv = {"path": "/media", "apikey": "a"}
    mv = {"path": "/media", "apikey": "b"}
    s = self._settings(sonarr=[tv], radarr=[mv])
    assert find_arr_instance("/media/x/e.mkv", s) == (tv, "sonarr")

  def test_paths_are_normalised(self):
    tv = {"path": "/media/TV/", "apikey": "a"}
    s = self._settings(sonarr=[tv])
    assert find_arr_instance("/media//TV/./Show/e.mkv", s) == (tv, "sonarr")

  def test_instances_without_path_or_apikey_are_skipped(self):
    s = self._settings(sonarr=[{"path": "", "apikey": "a"}, {"path": "/media/TV"}])
    assert find_arr_instance("/media/TV/S/e.mkv", s) == (None, None)

  def test_kinds_filter(self):
    s = self._settings(sonarr=[{"path": "/media", "apikey": "a"}])
    assert find_arr_instance("/media/F/f.mkv", s, kinds=("radarr",)) == (None, None)

  def test_matching_arr_instances_lists_all_matches_longest_first(self):
    broad = {"path": "/media", "apikey": "a"}
    narrow = {"path": "/media/TV", "apikey": "b"}
    other = {"path": "/other", "apikey": "c"}
    s = self._settings(sonarr=[broad, other, narrow])
    assert matching_arr_instances("/media/TV/S/e.mkv", s) == [(narrow, "sonarr"), (broad, "sonarr")]

  def test_settings_without_instance_lists(self):
    assert find_arr_instance("/media/TV/e.mkv", object()) == (None, None)


class TestRescanViaArrLookup:
  """Series/movie resolution must not depend on Sonarr/Radarr parsing the basename.

  Sonarr parses "Blade Runner 2099 (2026) - S01E01" with year 2099 and no series,
  so the id passed by the trigger (TVDB/TMDB) or the library path must win.
  """

  @staticmethod
  def _get_by_url(routes):
    def _get(url, headers=None, params=None, timeout=None):
      for key, data in routes.items():
        if url.endswith(key):
          return _make_response(data)
      raise AssertionError("unexpected GET %s %s" % (url, params))

    return _get

  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_sonarr_resolves_series_by_tvdbid_before_parse(self, mock_get, mock_post):
    mock_get.side_effect = self._get_by_url({"/api/v3/series": [{"id": 2116, "tvdbId": 424855, "path": "/tv/Blade Runner 2099 (2026)"}]})
    mock_post.return_value = _make_response({"id": 5, "status": "queued"})
    log = MagicMock()
    cmd_id = rescan_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Blade Runner 2099 (2026)/Season 01/Blade Runner 2099 (2026) - S01E01 - Hades.mp4", log, tvdbid="424855")
    assert cmd_id == 5
    assert mock_post.call_args[1]["json"] == {"name": "RescanSeries", "seriesId": 2116}
    series_call = mock_get.call_args_list[0]
    assert series_call.args[0].endswith("/api/v3/series")
    assert series_call.kwargs["params"] == {"tvdbId": "424855"}
    assert not any(c.args[0].endswith("/api/v3/parse") for c in mock_get.call_args_list)

  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_sonarr_falls_back_to_series_path_when_parse_fails(self, mock_get, mock_post):
    mock_get.side_effect = self._get_by_url(
      {
        "/api/v3/parse": {"series": None, "episodes": []},
        "/api/v3/series": [
          {"id": 1, "path": "/tv/Blade Runner"},
          {"id": 2116, "path": "/tv/Blade Runner 2099 (2026)"},
        ],
      }
    )
    mock_post.return_value = _make_response({"id": 6, "status": "queued"})
    log = MagicMock()
    cmd_id = rescan_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Blade Runner 2099 (2026)/Season 01/ep.mp4", log)
    assert cmd_id == 6
    assert mock_post.call_args[1]["json"] == {"name": "RescanSeries", "seriesId": 2116}

  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_sonarr_unknown_tvdbid_falls_back_to_parse(self, mock_get, mock_post):
    mock_get.side_effect = self._get_by_url({"/api/v3/series": [], "/api/v3/parse": {"series": {"id": 10}}})
    mock_post.return_value = _make_response({"id": 7, "status": "queued"})
    log = MagicMock()
    assert rescan_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Show/S01E01.mp4", log, tvdbid="1") == 7
    assert mock_post.call_args[1]["json"]["seriesId"] == 10

  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_sonarr_no_match_anywhere_returns_none(self, mock_get, mock_post):
    mock_get.side_effect = self._get_by_url({"/api/v3/parse": {"series": None}, "/api/v3/series": [{"id": 1, "path": "/tv/Other"}]})
    log = MagicMock()
    assert rescan_via_arr("http://localhost:8989", {}, "sonarr", "/tv/Show/S01E01.mp4", log) is None
    mock_post.assert_not_called()
    assert "could not resolve series" in log.warning.call_args[0][0]

  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_radarr_resolves_movie_by_tmdbid_before_parse(self, mock_get, mock_post):
    mock_get.side_effect = self._get_by_url({"/api/v3/movie": [{"id": 42, "tmdbId": 335984, "path": "/movies/Film (2017)"}]})
    mock_post.return_value = _make_response({"id": 8, "status": "queued"})
    log = MagicMock()
    cmd_id = rescan_via_arr("http://localhost:7878", {}, "radarr", "/movies/Film (2017)/Film.mp4", log, tmdbid=335984)
    assert cmd_id == 8
    assert mock_post.call_args[1]["json"] == {"name": "RescanMovie", "movieId": 42, "movieIds": [42]}
    assert mock_get.call_args_list[0].kwargs["params"] == {"tmdbId": 335984}
    assert not any(c.args[0].endswith("/api/v3/parse") for c in mock_get.call_args_list)

  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_radarr_falls_back_to_movie_path_when_parse_fails(self, mock_get, mock_post):
    mock_get.side_effect = self._get_by_url({"/api/v3/parse": {"movie": None}, "/api/v3/movie": [{"id": 42, "path": "/movies/Film (2017)"}]})
    mock_post.return_value = _make_response({"id": 9, "status": "queued"})
    log = MagicMock()
    assert rescan_via_arr("http://localhost:7878", {}, "radarr", "/movies/Film (2017)/Film.mp4", log) == 9
    assert mock_post.call_args[1]["json"]["movieIds"] == [42]
