"""Extended tests for converter/__init__.py covering tag(), convert(), probe(),
framedata(), thumbnail(), and thumbnails() delegation paths."""

from unittest.mock import patch

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
    v.index = 0
    v.type = "video"
    v.codec = "h264"
    v.video_width = 1920
    v.video_height = 1080
    info.streams.append(v)
  if has_audio:
    a = MediaStreamInfo()
    a.index = 1
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

  def test_tag_failure_restores_original_and_removes_orphan(self, tmp_path):
    """When the tag remux blows up mid-flight (ffprobe error, ffmpeg crash,
    SIGTERM, etc.) the original file must be restored from `.tag` so we
    don't leave a stranded `Movie.mp4.tag` and a missing `Movie.mp4`."""
    c = _make_converter()
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"original-source-bytes")

    # ffmpeg "writes" a partial output then raises.
    def failing_convert(outfile, opts, timeout=0):
      # simulate a few bytes of output written before the crash
      with open(outfile, "wb") as f:
        f.write(b"partial-output")
      if False:  # makes the function a generator without a reachable yield
        yield
      raise ConverterError("simulated crash mid-mux")

    fake_info = _make_info()
    fake_info.format.duration = 10.0

    with (
      patch.object(c.ffmpeg, "probe", return_value=fake_info),
      patch.object(c.ffmpeg, "convert", side_effect=failing_convert),
    ):
      with pytest.raises(ConverterError):
        list(c.tag(str(src)))

    # No leftover .tag, no half-written .mp4, original bytes restored.
    assert not (tmp_path / "movie.mp4.tag").exists()
    assert src.exists()
    assert src.read_bytes() == b"original-source-bytes"

  def test_tag_failure_in_probe_restores_original(self, tmp_path):
    """ffprobe returning None (file unreadable after rename, e.g. fs flake)
    must still restore the original."""
    c = _make_converter()
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"original-source-bytes")

    with patch.object(c.ffmpeg, "probe", return_value=None):
      with pytest.raises(ConverterError):
        list(c.tag(str(src)))

    assert not (tmp_path / "movie.mp4.tag").exists()
    assert src.exists()
    assert src.read_bytes() == b"original-source-bytes"

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

  def test_per_source_subtitle_encoding_emits_sub_charenc(self, tmp_path):
    """A subtitle entry's detected encoding renders as -sub_charenc before its own -i only."""
    c = _make_converter()
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    sub = tmp_path / "movie.eng.srt"
    sub.write_bytes(b"subtitle")
    fake_info = _make_info()

    with patch.object(c.ffmpeg, "probe", return_value=fake_info):
      opts = c.parse_options(
        {
          "format": "mp4",
          "source": [str(src), str(sub)],
          "audio": [{"codec": "aac"}],
          "subtitle": [{"codec": "mov_text", "map": "0:2", "source": 1, "encoding": "cp1252"}],
        }
      )
    charenc_at = opts.index("-sub_charenc")
    assert opts[charenc_at + 1] == "cp1252"
    # -sub_charenc must precede the subtitle source's -i and follow the main source's.
    assert opts.index(str(src)) < charenc_at < opts.index(str(sub))

  def test_global_sub_encoding_still_applies(self, tmp_path):
    """Legacy global sub-encoding option still renders when no per-stream encoding is set."""
    c = _make_converter()
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    sub = tmp_path / "movie.eng.srt"
    sub.write_bytes(b"subtitle")
    fake_info = _make_info()

    with patch.object(c.ffmpeg, "probe", return_value=fake_info):
      opts = c.parse_options(
        {
          "format": "mp4",
          "source": [str(src), str(sub)],
          "sub-encoding": "utf-16le",
          "audio": [{"codec": "aac"}],
          "subtitle": [{"codec": "mov_text", "map": "0:2", "source": 1}],
        }
      )
    charenc_at = opts.index("-sub_charenc")
    assert opts[charenc_at + 1] == "utf-16le"


def _attachment(index, mimetype=None, filename=None):
  a = MediaStreamInfo()
  a.index = index
  a.type = "attachment"
  a.codec = "ttf"
  a.metadata = {}
  if mimetype:
    a.metadata["mimetype"] = mimetype
  if filename:
    a.metadata["filename"] = filename
  return a


class TestTagStripsCoverArt:
  def _run_tag(self, tmp_path, attachments, **kwargs):
    c = _make_converter()
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"data")
    info = _make_info()
    info.streams.extend(attachments)
    captured = {}

    def fake_convert(outfile, opts, timeout=0):
      captured["opts"] = opts
      yield 5.0, ""

    with (
      patch.object(c.ffmpeg, "probe", return_value=info),
      patch.object(c.ffmpeg, "convert", side_effect=fake_convert),
      patch("os.rename"),
      patch("os.remove"),
    ):
      list(c.tag(str(f), **kwargs))
    return captured["opts"]

  def _maps(self, opts):
    return [opts[i + 1] for i, o in enumerate(opts) if o == "-map"]

  def _stream(self, index, kind, codec, attached_pic=0):
    s = MediaStreamInfo()
    s.index = index
    s.type = kind
    s.codec = codec
    s.attached_pic = attached_pic
    return s

  def test_maps_every_kept_stream_by_index(self, tmp_path):
    assert self._maps(self._run_tag(tmp_path, [])) == ["0:0", "0:1"]

  def test_attached_pic_and_demoted_poster_omitted(self, tmp_path):
    posters = [self._stream(2, "video", "mjpeg", attached_pic=1), self._stream(3, "video", "png")]
    assert self._maps(self._run_tag(tmp_path, posters)) == ["0:0", "0:1"]

  def test_data_and_unknown_streams_omitted(self, tmp_path):
    streams = [
      self._stream(2, "data", "bin_data"),
      self._stream(3, "unknown", None),
      self._stream(4, "audio", "none"),
      self._stream(5, "subtitle", "mov_text"),
    ]
    assert self._maps(self._run_tag(tmp_path, streams)) == ["0:0", "0:1", "0:5"]

  def test_font_attachment_mapped_image_omitted(self, tmp_path):
    atts = [
      _attachment(3, "application/x-truetype-font", "font.ttf"),
      _attachment(4, "image/jpeg", "cover.jpg"),
    ]
    opts = self._run_tag(tmp_path, atts)
    maps = [opts[i + 1] for i, o in enumerate(opts) if o == "-map"]
    assert "0:3" in maps
    assert "0:4" not in maps
    assert opts[opts.index("-c:t") + 1] == "copy"

  def test_mkv_output_has_no_mp4_muxer_flags(self, tmp_path):
    assert "-write_tmcd" not in self._run_tag(tmp_path, [])

  def test_mp4_output_never_synthesises_tmcd(self, tmp_path):
    c = _make_converter()
    f = tmp_path / "movie.mp4"
    f.write_bytes(b"data")
    captured = {}

    def fake_convert(outfile, opts, timeout=0):
      captured["opts"] = opts
      yield 5.0, ""

    with patch.object(c.ffmpeg, "probe", return_value=_make_info()), patch.object(c.ffmpeg, "convert", side_effect=fake_convert), patch("os.rename"), patch("os.remove"):
      list(c.tag(str(f)))
    assert captured["opts"][captured["opts"].index("-write_tmcd") + 1] == "0"

  def test_cues_to_front_passed(self, tmp_path):
    opts = self._run_tag(tmp_path, [], cues_to_front=True)
    assert opts[opts.index("-cues_to_front") + 1] == "true"


class TestIsCoverArtAttachment:
  @pytest.mark.parametrize(
    "mimetype,filename,expected",
    [
      ("image/jpeg", "whatever.bin", True),
      ("IMAGE/PNG", None, True),
      ("application/x-truetype-font", "font.ttf", False),
      (None, "cover.jpg", True),
      (None, "Poster.PNG", True),
      (None, "art.webp", True),
      (None, "font.otf", False),
      (None, None, False),
    ],
  )
  def test_cases(self, mimetype, filename, expected):
    from converter.ffmpeg import is_cover_art_attachment

    assert is_cover_art_attachment(_attachment(1, mimetype, filename)) is expected


def _parsed(*streams):
  """MediaInfo parsed from ffprobe -show_streams text; each stream is a dict of key=value lines."""
  raw = []
  for s in streams:
    raw.append("[STREAM]")
    raw.extend("%s=%s" % kv for kv in s.items())
    raw.append("[/STREAM]")
  info = MediaInfo(posters_as_video=False)
  info.parse_ffprobe("\n".join(raw))
  return info


def _v(index, codec, attached_pic=0, tag="avc1"):
  return {"index": index, "codec_type": "video", "codec_name": codec, "codec_tag_string": tag, "DISPOSITION:attached_pic": attached_pic}


class TestMediaInfoCoverArtAndUnknown:
  def test_codec_tag_parsed(self):
    assert _parsed(_v(0, "h264"), {"index": 1, "codec_type": "data", "codec_name": "bin_data", "codec_tag_string": "text"}).streams[1].codec_tag == "text"

  def test_demoted_poster_is_cover_art_not_primary(self):
    # Byproduct of the old "-map 0" repair: mjpeg poster as a real mp4v track, ahead of the movie.
    info = _parsed(_v(0, "mjpeg", tag="mp4v"), _v(1, "hevc", tag="hvc1"))
    assert info.video.index == 1
    assert [s.index for s in info.cover_art] == [0]

  def test_attached_pic_is_cover_art(self):
    info = _parsed(_v(0, "h264"), _v(1, "png", attached_pic=1))
    assert info.video.index == 0
    assert [s.index for s in info.cover_art] == [1]

  def test_image_only_video_stays_primary(self):
    info = _parsed(_v(0, "mjpeg"))
    assert info.video.index == 0
    assert info.cover_art == []

  def test_image_attachment_is_cover_art_font_is_not(self):
    info = _parsed(
      _v(0, "h264"),
      {"index": 1, "codec_type": "attachment", "codec_name": "ttf", "TAG:mimetype": "font/ttf", "TAG:filename": "f.ttf"},
      {"index": 2, "codec_type": "attachment", "codec_name": "mjpeg", "TAG:mimetype": "image/jpeg", "TAG:filename": "cover.jpg"},
    )
    assert [s.index for s in info.cover_art] == [2]

  def test_unknown_streams_excluded_from_typed_lists(self):
    info = _parsed(
      _v(0, "h264"),
      {"index": 1, "codec_type": "audio", "codec_name": "aac"},
      {"index": 2, "codec_type": "audio", "codec_name": "none"},
      {"index": 3, "codec_type": "subtitle", "codec_name": "unknown"},
      {"index": 4, "codec_type": "attachment"},
      {"index": 5, "codec_type": "unknown", "codec_name": "foo"},
      {"index": 6, "codec_type": "data", "codec_name": "unknown", "codec_tag_string": "tmcd"},
    )
    assert [s.index for s in info.audio] == [1]
    assert info.subtitle == []
    assert info.attachment == []
    assert [s.index for s in info.unknown] == [2, 3, 4, 5, 6]

  def test_unknown_video_never_primary(self):
    info = _parsed(_v(0, "none"), _v(1, "h264"))
    assert info.video.index == 1
