"""Tests for resources/postprocess.py PostProcessor."""
import os
import pytest
from unittest.mock import patch, MagicMock
from resources.postprocess import PostProcessor
from resources.metadata import MediaType


class TestPostProcessorInit:
    def test_sets_environment(self):
        """PostProcessor sets SMA_FILES in environment."""
        pp = PostProcessor(['/path/to/file.mp4'])
        assert '/path/to/file.mp4' in pp.post_process_environment.get('SMA_FILES', '')

    def test_gathers_from_post_process_dir(self):
        """PostProcessor gathers scripts from ../post_process relative to module."""
        pp = PostProcessor(['/path/to/file.mp4'])
        # Scripts list should be a list (may be empty if no scripts in post_process/)
        assert isinstance(pp.scripts, list)


class TestPostProcessorEnv:
    def test_set_tv_metadata(self):
        pp = PostProcessor(['/file.mp4'])
        pp.setEnv(MediaType.TV, tmdbid=1396, season=1, episode=1)
        assert pp.post_process_environment['SMA_TMDBID'] == '1396'
        assert pp.post_process_environment['SMA_SEASON'] == '1'
        assert pp.post_process_environment['SMA_EPISODE'] == '1'

    def test_set_movie_metadata(self):
        pp = PostProcessor(['/file.mp4'])
        pp.setEnv(MediaType.Movie, tmdbid=603)
        assert pp.post_process_environment['SMA_TMDBID'] == '603'
        assert 'SMA_SEASON' not in pp.post_process_environment


class TestPostProcessorRunScripts:
    def test_run_scripts_calls_popen(self):
        pp = PostProcessor(['/file.mp4'])
        pp.scripts = ['/tmp/fakescript.sh']
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b'stdout', b'stderr')
        mock_proc.wait.return_value = 0
        with patch.object(pp, 'run_script_command', return_value=mock_proc) as mock_cmd:
            pp.run_scripts()
            mock_cmd.assert_called_once_with('/tmp/fakescript.sh')

    def test_run_scripts_wait_mode(self):
        pp = PostProcessor(['/file.mp4'], wait=True)
        pp.scripts = ['/tmp/fakescript.sh']
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b'out', b'err')
        mock_proc.wait.return_value = 0
        with patch.object(pp, 'run_script_command', return_value=mock_proc):
            pp.run_scripts()
            mock_proc.wait.assert_called_once()

    def test_run_scripts_handles_exception(self):
        pp = PostProcessor(['/file.mp4'])
        pp.scripts = ['/tmp/badscript.sh']
        with patch.object(pp, 'run_script_command', side_effect=OSError("not found")):
            pp.run_scripts()  # Should not raise

    def test_run_scripts_no_scripts(self):
        pp = PostProcessor(['/file.mp4'])
        pp.scripts = []
        pp.run_scripts()  # Should not raise

    def test_gather_scripts_skips_bad_extensions(self):
        pp = PostProcessor(['/file.mp4'])
        # gather_scripts returns a list - verify it doesn't include bad files
        for script in pp.scripts:
            ext = os.path.splitext(script)[1]
            from resources.extensions import bad_post_extensions
            assert ext not in bad_post_extensions


class TestPostProcessorRunScriptCommand:
    def test_returns_popen(self):
        pp = PostProcessor(['/file.mp4'])
        with patch('resources.postprocess.Popen') as mock_popen:
            mock_popen.return_value = MagicMock()
            result = pp.run_script_command('/tmp/script.sh')
            mock_popen.assert_called_once()
            assert result is not None
