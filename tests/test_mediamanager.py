"""Tests for resources/mediamanager.py - Sonarr/Radarr API helpers."""

from unittest.mock import MagicMock, patch

from resources.mediamanager import api_command, api_get, api_put, build_api, rename, rename_via_arr, rescan, wait_for_command


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
        mock_post.assert_called_once_with("http://localhost:8989/api/v3/command", json={"name": "RescanSeries"}, headers={"X-Api-Key": "k"})
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
        assert wait_for_command("http://localhost:8989", {}, 42, log, retries=5, delay=1) is True
        assert mock_sleep.call_count == 2

    @patch("resources.mediamanager.time.sleep")
    @patch("resources.mediamanager.requests.get")
    def test_returns_false_on_timeout(self, mock_get, mock_sleep):
        mock_get.return_value.json.return_value = {"status": "started"}
        log = MagicMock()
        assert wait_for_command("http://localhost:8989", {}, 42, log, retries=2, delay=0) is False


class TestApiGet:
    @patch("resources.mediamanager.requests.get")
    def test_gets_endpoint(self, mock_get):
        mock_get.return_value.json.return_value = [{"id": 1}]
        log = MagicMock()
        result = api_get("http://localhost:8989", {"X-Api-Key": "k"}, "series", log)
        mock_get.assert_called_once_with("http://localhost:8989/api/v3/series", headers={"X-Api-Key": "k"})
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
