"""Tests for converter/formats.py - container format definitions."""

import pytest

from converter.formats import (
  MkvFormat,
  MovFormat,
  Mp4Format,
  SrtFormat,
  WebmFormat,
  format_list,
)


class TestFormatParseOptions:
  """Test format option parsing to FFmpeg flags."""

  def test_mp4_format(self):
    f = Mp4Format()
    opts = f.parse_options({"format": "mp4"})
    assert "-f" in opts
    assert "mp4" in opts

  @pytest.mark.parametrize("cls,name", [(Mp4Format, "mp4"), (MovFormat, "mov")])
  def test_mp4_family_never_synthesises_tmcd(self, cls, name):
    assert cls().parse_options({"format": name}) == ["-f", name, "-write_tmcd", "0"]

  def test_mkv_format(self):
    f = MkvFormat()
    opts = f.parse_options({"format": "mkv"})
    assert opts == ["-f", "matroska"]

  def test_webm_format(self):
    f = WebmFormat()
    opts = f.parse_options({"format": "webm"})
    assert "-f" in opts

  def test_srt_format(self):
    f = SrtFormat()
    opts = f.parse_options({"format": "srt"})
    assert "-f" in opts
    assert "srt" in opts

  def test_invalid_format_raises(self):
    f = Mp4Format()
    with pytest.raises(ValueError):
      f.parse_options({"format": "wrong"})

  def test_missing_format_raises(self):
    f = Mp4Format()
    with pytest.raises(ValueError):
      f.parse_options({})


class TestFormatList:
  """Test format registry."""

  def test_not_empty(self):
    assert len(format_list) > 0

  def test_mp4_in_list(self):
    names = [f.format_name for f in format_list]
    assert "mp4" in names

  def test_mkv_in_list(self):
    names = [f.format_name for f in format_list]
    assert "mkv" in names

  def test_all_have_ffmpeg_name(self):
    for f in format_list:
      assert f.ffmpeg_format_name is not None
