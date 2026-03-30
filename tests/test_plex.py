"""Tests for autoprocess/plex.py - Plex server connection and refresh."""

from unittest.mock import MagicMock, patch


def _make_settings(username="", servername="", host="", port=32400, token="", password="", ssl=False, ignore_certs=False, path_mapping=None):
    settings = MagicMock()
    settings.Plex = {
        "username": username,
        "servername": servername,
        "host": host,
        "port": port,
        "token": token,
        "password": password,
        "ssl": ssl,
        "ignore_certs": ignore_certs,
        "path-mapping": path_mapping or {},
    }
    return settings


class TestGetPlexServer:
    """Test getPlexServer() connection logic."""

    def test_returns_none_when_no_config(self):
        from autoprocess.plex import getPlexServer

        settings = _make_settings()
        result = getPlexServer(settings)
        # Returns (None, None) when neither username nor host is configured
        assert result == (None, None)

    def test_creates_ssl_session_when_ignore_certs(self):
        from autoprocess.plex import getPlexServer

        settings = _make_settings(host="plex.local", token="abc", ignore_certs=True)
        with patch("autoprocess.plex.requests.Session") as mock_session_cls, patch("autoprocess.plex.requests.packages") as mock_packages, patch("autoprocess.plex.PlexServer") as mock_ps:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_plex = MagicMock()
            mock_plex.friendlyName = "MyPlex"
            mock_ps.return_value = mock_plex

            result = getPlexServer(settings)

            mock_session_cls.assert_called_once()
            assert mock_session.verify is False
            assert result is mock_plex

    def test_direct_server_connection_with_token(self):
        from autoprocess.plex import getPlexServer

        settings = _make_settings(host="plex.local", port=32400, token="mytoken")
        with patch("autoprocess.plex.PlexServer") as mock_ps:
            mock_plex = MagicMock()
            mock_plex.friendlyName = "MyPlex"
            mock_ps.return_value = mock_plex

            result = getPlexServer(settings)

            mock_ps.assert_called_once_with("http://plex.local:32400", "mytoken", session=None)
            assert result is mock_plex

    def test_direct_server_connection_ssl(self):
        from autoprocess.plex import getPlexServer

        settings = _make_settings(host="plex.local", port=32400, token="tok", ssl=True)
        with patch("autoprocess.plex.PlexServer") as mock_ps:
            mock_plex = MagicMock()
            mock_plex.friendlyName = "MyPlex"
            mock_ps.return_value = mock_plex

            getPlexServer(settings)

            url = mock_ps.call_args[0][0]
            assert url.startswith("https://")

    def test_plex_tv_token_auth(self):
        from autoprocess.plex import getPlexServer

        settings = _make_settings(username="user@example.com", servername="MyServer", token="mytoken")
        with patch("autoprocess.plex.MyPlexAccount") as mock_account_cls:
            mock_account = MagicMock()
            mock_resource = MagicMock()
            mock_plex = MagicMock()
            mock_plex.friendlyName = "MyServer"
            mock_account_cls.return_value = mock_account
            mock_account.resource.return_value = mock_resource
            mock_resource.connect.return_value = mock_plex

            result = getPlexServer(settings)

            mock_account_cls.assert_called_once_with(username="user@example.com", token="mytoken", session=None)
            assert result is mock_plex

    def test_plex_tv_token_fails_falls_back_to_password(self):
        from autoprocess.plex import getPlexServer

        settings = _make_settings(username="user@example.com", servername="MyServer", token="badtoken", password="mypass")
        call_count = {"n": 0}

        def account_side_effect(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("token auth failed")
            mock_account = MagicMock()
            mock_resource = MagicMock()
            mock_plex = MagicMock()
            mock_plex.friendlyName = "MyServer"
            mock_account.resource.return_value = mock_resource
            mock_resource.connect.return_value = mock_plex
            return mock_account

        with patch("autoprocess.plex.MyPlexAccount", side_effect=account_side_effect):
            result = getPlexServer(settings)

        assert call_count["n"] == 2

    def test_direct_connection_error_returns_none(self):
        from autoprocess.plex import getPlexServer

        settings = _make_settings(host="plex.local", port=32400, token="tok")
        with patch("autoprocess.plex.PlexServer", side_effect=Exception("connection refused")):
            result = getPlexServer(settings)
        assert result is None

    def test_no_token_no_host_returns_none(self):
        from autoprocess.plex import getPlexServer

        settings = _make_settings(host="plex.local", port=32400)  # no token
        result = getPlexServer(settings)
        assert result is None


class TestRefreshPlex:
    """Test refreshPlex() section scanning and update logic."""

    def _make_section(self, title, locations):
        section = MagicMock()
        section.title = title
        section.locations = locations
        return section

    def test_no_plex_server_logs_error(self):
        from autoprocess.plex import refreshPlex

        settings = _make_settings()
        logger = MagicMock()
        with patch("autoprocess.plex.getPlexServer", return_value=None):
            refreshPlex(settings, path="/movies/file.mkv", logger=logger)
        logger.error.assert_called_once()

    def test_matching_section_gets_updated(self):
        from autoprocess.plex import refreshPlex

        settings = _make_settings()
        mock_plex = MagicMock()
        section = self._make_section("Movies", ["/movies"])
        mock_plex.library.sections.return_value = [section]

        with patch("autoprocess.plex.getPlexServer", return_value=mock_plex):
            refreshPlex(settings, path="/movies/The Matrix/file.mkv")

        section.update.assert_called_once_with(path="/movies/The Matrix")

    def test_non_matching_section_not_updated(self):
        from autoprocess.plex import refreshPlex

        settings = _make_settings()
        mock_plex = MagicMock()
        section = self._make_section("TV", ["/tv"])
        mock_plex.library.sections.return_value = [section]

        with patch("autoprocess.plex.getPlexServer", return_value=mock_plex):
            refreshPlex(settings, path="/movies/The Matrix/file.mkv")

        section.update.assert_not_called()

    def test_path_mapping_applied(self):
        from autoprocess.plex import refreshPlex

        settings = _make_settings(path_mapping={"/downloads": "/media"})
        mock_plex = MagicMock()
        section = self._make_section("Movies", ["/media"])
        mock_plex.library.sections.return_value = [section]

        with patch("autoprocess.plex.getPlexServer", return_value=mock_plex):
            refreshPlex(settings, path="/downloads/movie/file.mkv")

        section.update.assert_called_once()
        updated_path = section.update.call_args[1]["path"]
        assert updated_path.startswith("/media")

    def test_multiple_sections_only_matching_updated(self):
        from autoprocess.plex import refreshPlex

        settings = _make_settings()
        mock_plex = MagicMock()
        movies = self._make_section("Movies", ["/movies"])
        tv = self._make_section("TV", ["/tv"])
        mock_plex.library.sections.return_value = [movies, tv]

        with patch("autoprocess.plex.getPlexServer", return_value=mock_plex):
            refreshPlex(settings, path="/movies/The Matrix/file.mkv")

        movies.update.assert_called_once()
        tv.update.assert_not_called()
