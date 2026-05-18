"""Coverage tests for converter/ffmpeg.py parse helpers and MediaInfo.

Focuses on the static parse_* helpers, MediaStreamInfo.parse_ffprobe edge
cases, MediaFormatInfo.parse_ffprobe metadata extraction, and the bitrate
suppression heuristic.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converter.ffmpeg import MediaFormatInfo, MediaInfo, MediaStreamInfo


class TestParseHelpers:
  def test_parse_int_defaults(self):
    assert MediaStreamInfo.parse_int("42") == 42
    assert MediaStreamInfo.parse_int("not-an-int", 0) == 0
    assert MediaStreamInfo.parse_int(None, None) is None

  def test_parse_float_defaults(self):
    assert MediaStreamInfo.parse_float("3.14") == 3.14
    assert MediaStreamInfo.parse_float("nope", 0.0) == 0.0
    assert MediaStreamInfo.parse_float(None, None) is None

  def test_parse_bool(self):
    assert MediaStreamInfo.parse_bool(1) is True
    assert MediaStreamInfo.parse_bool(0) is False

    # Sentinel that raises in bool() context: use a deliberately broken object
    class BadBool:
      def __bool__(self):
        raise RuntimeError("boom")

    assert MediaStreamInfo.parse_bool(BadBool(), default=True) is True


class TestStreamParseFfprobe:
  def test_basic_audio_stream_keys(self):
    s = MediaStreamInfo()
    s.parse_ffprobe("index", "1")
    s.parse_ffprobe("codec_type", "audio")
    s.parse_ffprobe("codec_name", "AAC")
    s.parse_ffprobe("codec_long_name", "Advanced Audio Coding")
    s.parse_ffprobe("channels", "6")
    s.parse_ffprobe("sample_rate", "48000")
    s.parse_ffprobe("bit_rate", "192000")
    assert s.index == 1
    assert s.type == "audio"
    assert s.codec == "aac"  # lowercased
    assert s.codec_desc == "Advanced Audio Coding"
    assert s.audio_channels == 6
    assert s.audio_samplerate == 48000
    assert s.bitrate == 192000

  def test_profile_strips_spaces_and_lowercases(self):
    s = MediaStreamInfo()
    s.parse_ffprobe("profile", "High 10")
    assert s.profile == "high10"

  def test_disposition_flags(self):
    s = MediaStreamInfo()
    s.parse_ffprobe("DISPOSITION:forced", "1")
    s.parse_ffprobe("DISPOSITION:default", "0")
    s.parse_ffprobe("DISPOSITION:attached_pic", "1")
    assert s.forced is True
    assert s.default is False
    assert s.attached_pic == 1

  def test_tag_bps_fallback_populates_bitrate_when_missing(self):
    s = MediaStreamInfo()
    # No bit_rate yet; tag:BPS should populate
    s.parse_ffprobe("tag:bps-eng", "1500000")
    assert s.bitrate == 1500000

  def test_tag_bps_does_not_overwrite_existing_bitrate(self):
    s = MediaStreamInfo()
    s.parse_ffprobe("bit_rate", "2000000")
    s.parse_ffprobe("tag:bps", "9999")
    assert s.bitrate == 2000000

  def test_bitrate_below_1000_is_suppressed(self):
    s = MediaStreamInfo()
    s.parse_ffprobe("bit_rate", "500")
    # parse_ffprobe collapses sub-1000 bps reads to None (likely garbage).
    assert s.bitrate is None


class TestFormatParseFfprobe:
  def test_format_keys_and_tag_metadata(self):
    f = MediaFormatInfo()
    f.parse_ffprobe("format_name", "matroska,webm")
    f.parse_ffprobe("format_long_name", "Matroska / WebM")
    f.parse_ffprobe("bit_rate", "12000000")
    f.parse_ffprobe("duration", "7200.5")
    f.parse_ffprobe("TAG:title", "  My Movie  ")
    assert f.format == "matroska,webm"
    assert f.fullname == "Matroska / WebM"
    assert f.bitrate == 12000000.0
    assert f.duration == 7200.5
    # TAG: keys are normalized: lowercased key, lowercased+stripped value
    assert f.metadata["title"] == "my movie"

  def test_repr_with_and_without_duration(self):
    f = MediaFormatInfo()
    f.format = "mp4"
    assert "format=mp4" in repr(f)
    f.duration = 60.0
    assert "60.00" in repr(f)


class TestMediaInfoIteratorsAndJson:
  def _make(self):
    info = MediaInfo()
    info.format.format = "mp4"
    info.format.duration = 100.0

    v = MediaStreamInfo()
    v.type = "video"
    v.codec = "h264"
    v.index = 0
    v.video_width = 1920
    v.video_height = 1080
    v.fps = 24.0
    v.pix_fmt = "yuv420p"
    v.profile = "high"
    v.video_level = 4.1
    v.field_order = "progressive"
    info.streams.append(v)

    a = MediaStreamInfo()
    a.type = "audio"
    a.codec = "aac"
    a.index = 1
    a.audio_channels = 2
    a.audio_samplerate = 48000
    a.metadata = {"language": "eng"}
    a.disposition = {"default": True, "forced": False}
    info.streams.append(a)

    s = MediaStreamInfo()
    s.type = "subtitle"
    s.codec = "subrip"
    s.index = 2
    s.metadata = {"language": "fre"}
    s.disposition = {"default": False, "forced": True}
    info.streams.append(s)
    return info

  def test_video_audio_subtitle_iterators(self):
    info = self._make()
    # info.video returns the first video stream (or None), audio/subtitle return lists.
    assert info.video is not None
    assert info.video.codec == "h264"
    assert len(info.audio) == 1
    assert info.audio[0].codec == "aac"
    assert len(info.subtitle) == 1
    assert info.subtitle[0].codec == "subrip"

  def test_stream_json_serialization(self):
    info = self._make()
    assert info.video is not None
    v_json = info.video.json
    assert v_json["codec"] == "h264"
    assert v_json["index"] == 0
    a_json = info.audio[0].json
    assert a_json["channels"] == 2
    assert a_json["samplerate"] == 48000
    assert a_json["language"] == "eng"
