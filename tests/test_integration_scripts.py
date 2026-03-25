"""Tests for post-processing integration scripts.

Tests that each script correctly parses its inputs and delegates
to the webhook client. Uses mocking to avoid real daemon connections
and real media manager API calls.
"""
import os
import sys
import json
import importlib
import pytest
from unittest.mock import patch, MagicMock, call


def _run_script(script_path, mock_submit_return=None, expect_exit=None):
    """Execute a script with webhook_client.submit_job and submit_and_wait mocked.

    Patches at the module attribute level so both `from X import Y` and `import X; X.Y()`
    style imports see the mock.
    """
    import resources.webhook_client as wc
    original_submit = wc.submit_job
    original_submit_and_wait = wc.submit_and_wait
    mock_submit = MagicMock(return_value=mock_submit_return or {'job_id': 1})
    mock_submit_and_wait = MagicMock(return_value={'id': 1, 'status': 'completed'})
    wc.submit_job = mock_submit
    wc.submit_and_wait = mock_submit_and_wait
    exit_code = None
    try:
        exec(compile(open(script_path).read(), script_path, 'exec'))
    except SystemExit as e:
        exit_code = e.code
    finally:
        wc.submit_job = original_submit
        wc.submit_and_wait = original_submit_and_wait
    return mock_submit, mock_submit_and_wait


class TestPostRadarr:
    """Test postRadarr.py webhook integration."""

    def _radarr_env(self, **overrides):
        env = {
            'radarr_eventtype': 'Download',
            'radarr_moviefile_path': '/movies/The Matrix (1999)/The Matrix.mkv',
            'radarr_moviefile_scenename': 'The.Matrix.1999.BluRay',
            'radarr_movie_imdbid': 'tt0133093',
            'radarr_movie_tmdbid': '603',
            'radarr_movie_id': '1',
            'radarr_moviefile_id': '1',
            'radarr_moviefile_releasegroup': 'FGT',
            'radarr_moviefile_sourcefolder': '/downloads/The.Matrix.1999',
        }
        env.update(overrides)
        return env

    @patch('resources.webhook_client.submit_and_wait')
    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_test_event_exits(self, mock_validate, mock_submit):
        with patch.dict(os.environ, {'radarr_eventtype': 'Test'}, clear=False):
            with pytest.raises(SystemExit) as exc:
                exec(compile(open('postRadarr.py').read(), 'postRadarr.py', 'exec'))
            assert exc.value.code == 0
        mock_submit.assert_not_called()

    @patch('resources.webhook_client.submit_and_wait')
    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_invalid_event_exits(self, mock_validate, mock_submit):
        with patch.dict(os.environ, {'radarr_eventtype': 'Rename'}, clear=False):
            with pytest.raises(SystemExit) as exc:
                exec(compile(open('postRadarr.py').read(), 'postRadarr.py', 'exec'))
            assert exc.value.code == 1

    @patch('requests.post')
    @patch('requests.get')
    @patch('resources.webhook_client.submit_and_wait')
    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_submits_webhook_with_tmdb(self, mock_validate, mock_submit, mock_get, mock_post):
        mock_submit.return_value = {'id': 1, 'status': 'completed'}

        # Mock Radarr API responses for rescan
        mock_rescan_resp = MagicMock()
        mock_rescan_resp.json.return_value = {'id': 100, 'status': 'completed'}
        mock_post.return_value = mock_rescan_resp

        mock_command_resp = MagicMock()
        mock_command_resp.json.return_value = {'status': 'completed'}
        mock_movie_resp = MagicMock()
        mock_movie_resp.json.return_value = {'hasFile': True, 'title': 'The Matrix', 'monitored': False, 'movieFile': {'id': 1}}
        mock_get.side_effect = [mock_command_resp, mock_movie_resp, mock_movie_resp]

        with patch.dict(os.environ, self._radarr_env(), clear=False):
            try:
                exec(compile(open('postRadarr.py').read(), 'postRadarr.py', 'exec'))
            except SystemExit:
                pass

        mock_submit.assert_called_once()
        submit_args = mock_submit.call_args
        assert submit_args[0][0] == '/movies/The Matrix (1999)/The Matrix.mkv'
        # Should include -tmdb arg
        extra_args = submit_args[1].get('args') or submit_args[0][1] if len(submit_args[0]) > 1 else None
        if extra_args is None:
            extra_args = submit_args[1].get('args', [])
        assert '-tmdb' in extra_args
        assert '603' in extra_args


class TestPostSonarr:
    """Test postSonarr.py webhook integration."""

    def _sonarr_env(self, **overrides):
        env = {
            'sonarr_eventtype': 'Download',
            'sonarr_episodefile_path': '/tv/Breaking Bad/Season 01/Breaking.Bad.S01E01.mkv',
            'sonarr_episodefile_scenename': 'Breaking.Bad.S01E01',
            'sonarr_series_tvdbid': '81189',
            'sonarr_series_imdbid': 'tt0903747',
            'sonarr_episodefile_seasonnumber': '1',
            'sonarr_series_id': '5',
            'sonarr_episodefile_releasegroup': 'NTb',
            'sonarr_episodefile_id': '10',
            'sonarr_episodefile_sourcefolder': '/downloads/Breaking.Bad.S01E01',
            'sonarr_episodefile_episodenumbers': '1',
            'sonarr_episodefile_episodeids': '100',
        }
        env.update(overrides)
        return env

    @patch('resources.webhook_client.submit_and_wait')
    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_test_event_exits(self, mock_validate, mock_submit):
        with patch.dict(os.environ, {'sonarr_eventtype': 'Test'}, clear=False):
            with pytest.raises(SystemExit) as exc:
                exec(compile(open('postSonarr.py').read(), 'postSonarr.py', 'exec'))
            assert exc.value.code == 0

    @patch('requests.post')
    @patch('requests.get')
    @patch('resources.webhook_client.submit_and_wait')
    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_submits_with_tvdb_season_episode(self, mock_validate, mock_submit, mock_get, mock_post):
        mock_submit.return_value = {'id': 1, 'status': 'completed'}

        mock_rescan_resp = MagicMock()
        mock_rescan_resp.json.return_value = {'id': 100, 'status': 'completed'}
        mock_post.return_value = mock_rescan_resp

        mock_command_resp = MagicMock()
        mock_command_resp.json.return_value = {'status': 'completed'}
        mock_episode_resp = MagicMock()
        mock_episode_resp.json.return_value = {'hasFile': True, 'title': 'Pilot', 'monitored': False, 'episodeFileId': 10}
        mock_get.side_effect = [mock_command_resp, mock_episode_resp, mock_episode_resp]

        with patch.dict(os.environ, self._sonarr_env(), clear=False):
            try:
                exec(compile(open('postSonarr.py').read(), 'postSonarr.py', 'exec'))
            except SystemExit:
                pass

        mock_submit.assert_called_once()
        extra_args = mock_submit.call_args[1].get('args', [])
        assert '-tvdb' in extra_args
        assert '81189' in extra_args
        assert '-s' in extra_args
        assert '1' in extra_args
        assert '-e' in extra_args


class TestPostSickbeard:
    """Test postSickbeard.py webhook integration."""

    @patch('resources.webhook_client.submit_and_wait')
    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_submits_with_tvdb_args(self, mock_validate, mock_submit):
        mock_submit.return_value = {'id': 1, 'status': 'completed'}

        original_argv = sys.argv
        sys.argv = ['postSickbeard.py', '/tv/show.mkv', 'show.S01E01', '73871', '1', '1']
        try:
            with patch('requests.get') as mock_get:
                mock_get.return_value = MagicMock(text='OK')
                try:
                    exec(compile(open('postSickbeard.py').read(), 'postSickbeard.py', 'exec'))
                except SystemExit:
                    pass
        finally:
            sys.argv = original_argv

        mock_submit.assert_called_once()
        extra_args = mock_submit.call_args[1].get('args', [])
        assert '-tvdb' in extra_args
        assert '73871' in extra_args

    @patch('resources.webhook_client.submit_and_wait')
    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_not_enough_args_exits(self, mock_validate, mock_submit):
        original_argv = sys.argv
        sys.argv = ['postSickbeard.py', '/file.mkv']
        try:
            with pytest.raises(SystemExit) as exc:
                exec(compile(open('postSickbeard.py').read(), 'postSickbeard.py', 'exec'))
            assert exc.value.code == 1
        finally:
            sys.argv = original_argv


class TestSABPostProcess:
    """Test SABPostProcess.py webhook integration."""

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_submits_files_in_directory(self, mock_validate, tmp_path):
        (tmp_path / 'movie.mkv').touch()
        (tmp_path / 'movie.nfo').touch()

        original_argv = sys.argv
        sys.argv = ['SABPostProcess.py', str(tmp_path), 'nzb', 'clean', '0', 'movies', 'group', '0']
        try:
            mock_submit, _ = _run_script('SABPostProcess.py')
            assert mock_submit.call_count == 2
        finally:
            sys.argv = original_argv

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_failed_status_exits(self, mock_validate):
        original_argv = sys.argv
        sys.argv = ['SABPostProcess.py', '/path', 'nzb', 'clean', '0', 'movies', 'group', '1']
        try:
            mock_submit, _ = _run_script('SABPostProcess.py')
            mock_submit.assert_not_called()
        finally:
            sys.argv = original_argv


class TestNZBGetPostProcess:
    """Test NZBGetPostProcess.py webhook integration."""

    def _nzbget_env(self, directory, **overrides):
        env = {
            'NZBOP_VERSION': '21.0',
            'NZBPO_MP4_FOLDER': os.path.dirname(os.path.abspath(__file__)) + '/../',
            'NZBPO_SHOULDCONVERT': 'true',
            'NZBPO_SONARR_CAT': 'sonarr',
            'NZBPO_RADARR_CAT': 'radarr',
            'NZBPO_SICKBEARD_CAT': 'sickbeard',
            'NZBPO_SICKRAGE_CAT': 'sickrage',
            'NZBPO_BYPASS_CAT': 'bypass',
            'NZBPP_TOTALSTATUS': 'SUCCESS',
            'NZBPP_DIRECTORY': directory,
            'NZBPP_NZBFILENAME': 'test.nzb',
            'NZBPP_CATEGORY': 'movies',
        }
        env.update(overrides)
        return env

    @patch('resources.webhook_client.submit_job')
    def test_submits_files(self, mock_submit, tmp_path):
        mock_submit.return_value = {'job_id': 1}
        (tmp_path / 'movie.mkv').touch()

        with patch.dict(os.environ, self._nzbget_env(str(tmp_path)), clear=False):
            with pytest.raises(SystemExit) as exc:
                exec(compile(open('NZBGetPostProcess.py').read(), 'NZBGetPostProcess.py', 'exec'))
            assert exc.value.code == 93  # POSTPROCESS_SUCCESS

        assert mock_submit.call_count == 1

    @patch('resources.webhook_client.submit_job')
    def test_bypass_category_skips(self, mock_submit, tmp_path):
        (tmp_path / 'movie.mkv').touch()

        env = self._nzbget_env(str(tmp_path), NZBPP_CATEGORY='bypass')
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc:
                exec(compile(open('NZBGetPostProcess.py').read(), 'NZBGetPostProcess.py', 'exec'))
            assert exc.value.code == 95  # POSTPROCESS_NONE

        mock_submit.assert_not_called()

    @patch('resources.webhook_client.submit_job')
    def test_convert_disabled_skips(self, mock_submit, tmp_path):
        (tmp_path / 'movie.mkv').touch()

        env = self._nzbget_env(str(tmp_path), NZBPO_SHOULDCONVERT='false')
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc:
                exec(compile(open('NZBGetPostProcess.py').read(), 'NZBGetPostProcess.py', 'exec'))
            assert exc.value.code == 95

        mock_submit.assert_not_called()

    def test_no_nzbget_env_exits(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit) as exc:
                exec(compile(open('NZBGetPostProcess.py').read(), 'NZBGetPostProcess.py', 'exec'))
            assert exc.value.code == 94  # POSTPROCESS_ERROR


class TestQBittorrentPostProcess:
    """Test qBittorrentPostProcess.py webhook integration."""

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_submits_single_file(self, mock_validate, tmp_path):
        filepath = tmp_path / 'movie.mkv'
        filepath.touch()

        original_argv = sys.argv
        sys.argv = ['qBittorrentPostProcess.py', 'movies', 'tracker', str(tmp_path), str(filepath), 'Movie.Name', 'abc123']
        try:
            mock_submit, _ = _run_script('qBittorrentPostProcess.py')
            mock_submit.assert_called_once()
        finally:
            sys.argv = original_argv

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_submits_directory_contents(self, mock_validate, tmp_path):
        (tmp_path / 'ep01.mkv').touch()
        (tmp_path / 'ep02.mkv').touch()

        original_argv = sys.argv
        sys.argv = ['qBittorrentPostProcess.py', 'tv', 'tracker', str(tmp_path), str(tmp_path), 'Show.S01', 'def456']
        try:
            mock_submit, _ = _run_script('qBittorrentPostProcess.py')
            assert mock_submit.call_count == 2
        finally:
            sys.argv = original_argv

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_bypass_label_skips(self, mock_validate, tmp_path):
        """Bypass label must match what the active config defines."""
        (tmp_path / 'file.mkv').touch()

        # Determine the bypass label from the active config
        from resources.readsettings import ReadSettings
        with patch('resources.readsettings.ReadSettings._validate_binaries'):
            s = ReadSettings()
        bypass_list = s.qBittorrent.get('bypass', ['bypass'])
        bypass_label = bypass_list[0] if bypass_list else 'bypass'

        original_argv = sys.argv
        sys.argv = ['qBittorrentPostProcess.py', bypass_label, 'tracker', str(tmp_path), str(tmp_path), 'Name', 'hash']
        try:
            mock_submit, _ = _run_script('qBittorrentPostProcess.py')
            mock_submit.assert_not_called()
        finally:
            sys.argv = original_argv


class TestUTorrentPostProcess:
    """Test uTorrentPostProcess.py webhook integration."""

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_submits_single_file(self, mock_validate, tmp_path):
        filepath = tmp_path / 'movie.mkv'
        filepath.touch()

        original_argv = sys.argv
        sys.argv = ['uTorrentPostProcess.py', 'movies', 'tracker', str(tmp_path), 'single', 'movie.mkv', 'hash123', 'Movie']
        try:
            mock_submit, _ = _run_script('uTorrentPostProcess.py')
            mock_submit.assert_called_once()
        finally:
            sys.argv = original_argv

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_submits_multi_directory(self, mock_validate, tmp_path):
        (tmp_path / 'ep1.mkv').touch()
        (tmp_path / 'ep2.mkv').touch()

        original_argv = sys.argv
        sys.argv = ['uTorrentPostProcess.py', 'tv', 'tracker', str(tmp_path), 'multi', '', 'hash456', 'Show']
        try:
            mock_submit, _ = _run_script('uTorrentPostProcess.py')
            assert mock_submit.call_count == 2
        finally:
            sys.argv = original_argv

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_bypass_skips(self, mock_validate, tmp_path):
        original_argv = sys.argv
        sys.argv = ['uTorrentPostProcess.py', 'bypass', 'tracker', str(tmp_path), 'single', 'f.mkv', 'hash', 'Name']
        try:
            mock_submit, _ = _run_script('uTorrentPostProcess.py')
            mock_submit.assert_not_called()
        finally:
            sys.argv = original_argv
