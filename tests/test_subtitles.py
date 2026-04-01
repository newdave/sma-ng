"""Tests for resources/subtitles.py SubtitleProcessor."""

from unittest.mock import MagicMock

import pytest

from resources.subtitles import SubtitleProcessor


def _make_sp(sdl="eng"):
    """Create a SubtitleProcessor backed by a mock MediaProcessor."""
    mp = MagicMock()
    mp.settings.sdl = sdl
    sp = SubtitleProcessor(mp)
    return sp, mp


def _make_sub_info(path, lang="und"):
    """Return a minimal MediaInfo-like object for an external subtitle file."""
    from converter.ffmpeg import MediaInfo, MediaStreamInfo

    sub_stream = MediaStreamInfo()
    sub_stream.type = "subtitle"
    sub_stream.codec = "srt"
    sub_stream.index = 0
    sub_stream.metadata = {"language": lang}
    sub_stream.disposition = {}

    info = MediaInfo()
    info.streams.append(sub_stream)
    info.path = path
    return info


class TestProcessExternalSub:
    def test_none_input_returns_none(self):
        sp, mp = _make_sp()
        assert sp.processExternalSub(None, "/path/movie.mkv") is None

    def test_language_extracted_from_suffix(self):
        sp, mp = _make_sp()
        mp.parseFile.side_effect = lambda p: (
            "/path",
            p.rsplit("/", 1)[-1].rsplit(".", 1)[0],
            p.rsplit(".", 1)[-1],
        )
        sub_info = _make_sub_info("/path/movie.eng.srt")
        result = sp.processExternalSub(sub_info, "/path/movie.mkv")
        assert result.subtitle[0].metadata["language"] == "eng"

    def test_sdl_used_when_no_language_in_filename(self):
        sp, mp = _make_sp(sdl="fra")
        mp.parseFile.side_effect = lambda p: (
            "/path",
            p.rsplit("/", 1)[-1].rsplit(".", 1)[0],
            p.rsplit(".", 1)[-1],
        )
        sub_info = _make_sub_info("/path/movie.srt")
        result = sp.processExternalSub(sub_info, "/path/movie.mkv")
        assert result.subtitle[0].metadata["language"] == "fra"

    def test_forced_disposition_set_from_suffix(self):
        sp, mp = _make_sp()
        mp.parseFile.side_effect = lambda p: (
            "/path",
            p.rsplit("/", 1)[-1].rsplit(".", 1)[0],
            p.rsplit(".", 1)[-1],
        )
        sub_info = _make_sub_info("/path/movie.eng.forced.srt")
        result = sp.processExternalSub(sub_info, "/path/movie.mkv")
        assert result.subtitle[0].disposition.get("forced") is True
