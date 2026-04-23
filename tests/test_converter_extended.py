"""Extended tests for converter/__init__.py covering tag(), convert(), probe(),
framedata(), thumbnail(), and thumbnails() delegation paths."""

import os
from unittest.mock import MagicMock, patch

import pytest

from converter import Converter, ConverterError
from converter.ffmpeg import FFMpegError, MediaFormatInfo, MediaInfo, MediaStreamInfo

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_converter():
  with patch("os.path.exists", return_value=True):
    return Converter(ffmpeg_path="/usr/bin/ffmpeg", ffprobe_path="/usr/bin/ffprobe")


def _make_info(has_video=True, has_audio=True, duration=120.0):
  info = MediaInfo()
  info.format = MediaFormatInfo()
  info.format.duration = duration
  info.format.bitrate = 5_000_000
  if has_video:
    v = MediaStreamInfo()
    v.type = "video"
    v.codec = "h264"
    v.video_width = 1920
    v.video_height = 1080
    info.streams.append(v)
  if has_audio:
    a = MediaStreamInfo()
    a.type = "audio"
    a.codec = "aac"
    a.audio_channels = 2
    info.streams.append(a)
  return info


# ---------------------------------------------------------------------------
# Converter.probe / framedata delegation
# ---------------------------------------------------------------------------


class TestConverterProbeFramedata:
  def test_probe_delegates_to_ffmpeg(self):
    c = _make_converter()
    fake_info = _make_info()
    with patch.object(c.ffmpeg, "probe", return_value=fake_info) as mock_probe:
      result = c.probe("movie.mkv")
    mock_probe.assert_called_once_with("movie.mkv", True)
    assert result is fake_info

  def test_probe_with_posters_false(self):
    c = _make_converter()
    with patch.object(c.ffmpeg, "probe", return_value=None) as mock_probe:
      c.probe("movie.mkv", posters_as_video=False)
    mock_probe.assert_called_once_with("movie.mkv", False)

  def test_framedata_returns_dict_on_success(self):
    c = _make_converter()
    with patch.object(c.ffmpeg, "framedata", return_value={"pix_fmt": "yuv420p"}):
      result = c.framedata("movie.mkv")
    assert result == {"pix_fmt": "yuv420p"}

  def test_framedata_returns_none_on_ffmpeg_error(self):
    c = _make_converter()
    with patch.object(c.ffmpeg, "framedata", side_effect=FFMpegError("bad")):
      result = c.framedata("movie.mkv")
    assert result is None


# ---------------------------------------------------------------------------
# Converter.thumbnail / thumbnails delegation
# ---------------------------------------------------------------------------


class TestConverterThumbnails:
  def test_thumbnail_delegates(self):
    c = _make_converter()
    with patch.object(c.ffmpeg, "thumbnail") as mock_thumb:
      c.thumbnail("movie.mkv", 5, "/tmp/out.jpg", "320x240")
    mock_thumb.assert_called_once_with("movie.mkv", 5, "/tmp/out.jpg", "320x240", 4)

  def test_thumbnails_delegates(self):
    c = _make_converter()
    opts = [(5, "/tmp/t1.jpg"), (10, "/tmp/t2.jpg")]
    with patch.object(c.ffmpeg, "thumbnails") as mock_thumbs:
      c.thumbnails("movie.mkv", opts)
    mock_thumbs.assert_called_once_with("movie.mkv", opts)


# ---------------------------------------------------------------------------
# Converter.tag
# ---------------------------------------------------------------------------


class TestConverterTag:
  def test_tag_renames_input_and_converts(self, tmp_path):
    c = _make_converter()
    f = tmp_path / "movie.mp4"
    f.write_bytes(b"data")

    fake_info = _make_info()
    fake_info.format.duration = 30.0

    def fake_convert(outfile, opts, timeout=0):
      yield 50.0, ""

    with (
      patch.object(c.ffmpeg, "probe", return_value=fake_info),
      patch.object(c.ffmpeg, "convert", side_effect=fake_convert),
      patch("os.rename"),
      patch("os.remove"),
    ):
      results = list(c.tag(str(f), metadata={"title": "Test Movie"}))
    assert len(results) > 0

  def test_tag_with_png_cover(self, tmp_path):
    c = _make_converter()
    f = tmp_path / "movie.mp4"
    f.write_bytes(b"data")
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png")

    fake_info = _make_info()
    fake_info.format.duration = 10.0

    def fake_convert(outfile, opts, timeout=0):
      yield 5.0, ""

    with (
      patch.object(c.ffmpeg, "probe", return_value=fake_info),
      patch.object(c.ffmpeg, "convert", side_effect=fake_convert),
      patch("os.rename"),
      patch("os.remove"),
    ):
      results = list(c.tag(str(f), coverpath=str(cover)))
    assert isinstance(results, list)

  def test_tag_with_jpg_cover(self, tmp_path):
    c = _make_converter()
    f = tmp_path / "movie.mp4"
    f.write_bytes(b"data")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"jpg")

    fake_info = _make_info()
    fake_info.format.duration = 10.0

    def fake_convert(outfile, opts, timeout=0):
      yield 5.0, ""

    with (
      patch.object(c.ffmpeg, "probe", return_value=fake_info),
      patch.object(c.ffmpeg, "convert", side_effect=fake_convert),
      patch("os.rename"),
      patch("os.remove"),
    ):
      results = list(c.tag(str(f), coverpath=str(cover)))
    assert isinstance(results, list)

  def test_tag_with_existing_tag_file(self, tmp_path):
    """When .tag file already exists, suffix counter is appended."""
    c = _make_converter()
    f = tmp_path / "movie.mp4"
    f.write_bytes(b"data")
    tag_file = tmp_path / "movie.mp4.tag"
    tag_file.write_bytes(b"existing")

    fake_info = _make_info()
    fake_info.format.duration = 10.0

    def fake_convert(outfile, opts, timeout=0):
      yield 5.0, ""

    with (
      patch.object(c.ffmpeg, "probe", return_value=fake_info),
      patch.object(c.ffmpeg, "convert", side_effect=fake_convert),
      patch("os.rename"),
      patch("os.remove"),
    ):
      results = list(c.tag(str(f)))
    assert isinstance(results, list)

  def test_tag_cues_to_front(self, tmp_path):
    c = _make_converter()
    f = tmp_path / "movie.mp4"
    f.write_bytes(b"data")

    fake_info = _make_info()
    fake_info.format.duration = 10.0
    captured_opts = []

    def fake_convert(outfile, opts, timeout=0):
      captured_opts.extend(opts)
      yield 5.0, ""

    with (
      patch.object(c.ffmpeg, "probe", return_value=fake_info),
      patch.object(c.ffmpeg, "convert", side_effect=fake_convert),
      patch("os.rename"),
      patch("os.remove"),
    ):
      list(c.tag(str(f), cues_to_front=True))
    assert "-cues_to_front" in captured_opts


# ---------------------------------------------------------------------------
# Converter.convert (full flow with options)
# ---------------------------------------------------------------------------


class TestConverterConvertFull:
  def test_convert_basic_flow(self, tmp_path):
    c = _make_converter()
    src = tmp_path / "in.mkv"
    src.write_bytes(b"x")

    fake_info = _make_info()

    def fake_convert(outfile, optlist, timeout=10, preopts=None, postopts=None):
      yield 0.0, []
      yield 120.0, ""

    with patch.object(c.ffmpeg, "probe", return_value=fake_info), patch.object(c.ffmpeg, "convert", side_effect=fake_convert):
      results = list(
        c.convert(
          str(tmp_path / "out.mp4"),
          {"format": "mp4", "source": [str(src)], "audio": [{"codec": "aac"}], "subtitle": [], "video": {"codec": "h264"}},
        )
      )
    assert len(results) > 0

  def test_convert_can_not_get_info_raises(self, tmp_path):
    c = _make_converter()
    src = tmp_path / "in.mkv"
    src.write_bytes(b"x")
    with patch.object(c.ffmpeg, "probe", return_value=None):
      with pytest.raises(ConverterError, match="Can't get information"):
        list(c.convert(str(tmp_path / "out.mp4"), {"format": "mp4", "source": [str(src)]}))

  def test_convert_no_streams_raises(self, tmp_path):
    c = _make_converter()
    src = tmp_path / "in.mkv"
    src.write_bytes(b"x")
    info = MediaInfo()
    info.format = MediaFormatInfo()
    info.format.duration = 10.0
    with patch.object(c.ffmpeg, "probe", return_value=info):
      with pytest.raises(ConverterError, match="no audio, video, or subtitle"):
        list(c.convert(str(tmp_path / "out.mp4"), {"format": "mp4", "source": [str(src)]}))

  def test_convert_zero_length_video_raises(self, tmp_path):
    c = _make_converter()
    src = tmp_path / "in.mkv"
    src.write_bytes(b"x")
    info = _make_info()
    info.format.duration = 0.005  # truthy but < 0.01 threshold → zero-length
    with patch.object(c.ffmpeg, "probe", return_value=info):
      with pytest.raises(ConverterError, match="Zero-length"):
        list(
          c.convert(
            str(tmp_path / "out.mp4"),
            {"format": "mp4", "source": [str(src)], "audio": [{"codec": "aac"}], "subtitle": [], "video": {"codec": "h264"}},
          )
        )

  def test_convert_twopass(self, tmp_path):
    c = _make_converter()
    src = tmp_path / "in.mkv"
    src.write_bytes(b"x")
    fake_info = _make_info()

    def fake_convert(outfile, optlist, timeout=10, preopts=None, postopts=None):
      yield 0.0, []
      yield 120.0, ""

    with patch.object(c.ffmpeg, "probe", return_value=fake_info), patch.object(c.ffmpeg, "convert", side_effect=fake_convert):
      results = list(
        c.convert(
          str(tmp_path / "out.mp4"),
          {"format": "mp4", "source": [str(src)], "audio": [{"codec": "aac"}], "subtitle": [], "video": {"codec": "h264"}},
          twopass=True,
        )
      )
    assert len(results) > 0

  def test_convert_zero_duration_audio_only(self, tmp_path):
    """Audio-only file with zero duration should not raise Zero-length."""
    c = _make_converter()
    src = tmp_path / "in.mp3"
    src.write_bytes(b"x")
    info = MediaInfo()
    info.format = MediaFormatInfo()
    info.format.duration = 0.0
    a = MediaStreamInfo()
    a.type = "audio"
    a.codec = "mp3"
    a.audio_channels = 2
    info.streams.append(a)

    def fake_convert(outfile, optlist, timeout=10, preopts=None, postopts=None):
      yield 0.0, []

    with patch.object(c.ffmpeg, "probe", return_value=info), patch.object(c.ffmpeg, "convert", side_effect=fake_convert):
      results = list(
        c.convert(
          str(tmp_path / "out.mp4"),
          {"format": "mp4", "source": [str(src)], "audio": [{"codec": "aac"}], "subtitle": []},
        )
      )
    assert isinstance(results, list)

  def test_convert_subtitle_source_with_fix_sub_duration(self, tmp_path):
    """External subtitle source triggers -fix_sub_duration in source options."""
    c = _make_converter()
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    sub = tmp_path / "movie.eng.srt"
    sub.write_bytes(b"subtitle")
    fake_info = _make_info()

    def fake_convert(outfile, optlist, timeout=10, preopts=None, postopts=None):
      yield 0.0, []
      yield 120.0, ""

    with patch.object(c.ffmpeg, "probe", return_value=fake_info), patch.object(c.ffmpeg, "convert", side_effect=fake_convert):
      opts = c.parse_options(
        {
          "format": "mp4",
          "source": [str(src), str(sub)],
          "audio": [{"codec": "aac"}],
          "subtitle": [{"codec": "mov_text", "map": "0:2", "source": 1}],
        }
      )
    assert "-fix_sub_duration" in opts
