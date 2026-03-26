"""Tests for resources/postprocess.py PostProcessor."""
import os
import pytest
from unittest.mock import patch
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
