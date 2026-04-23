"""Extended tests for converter/ffmpeg.py — covers properties and methods not exercised
by test_ffmpeg.py, including codecs, hwaccels, encoders, decoders, pix_fmts,
hwaccel_decoder, encoder_formats, decoder_formats, framedata, probe, convert, and thumbnails."""

import json
import os
from io import BytesIO
from subprocess import Popen
from unittest.mock import MagicMock, patch

import pytest

from converter.ffmpeg import FFMpeg, FFMpegError, MediaFormatInfo, MediaInfo, MediaStreamInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ffmpeg():
  """Build an FFMpeg instance with patched binary existence checks."""
  with patch("os.path.exists", return_value=True):
    ff = FFMpeg.__new__(FFMpeg)
    ff.ffmpeg_path = "/usr/bin/ffmpeg"
    ff.ffprobe_path = "/usr/bin/ffprobe"
    return ff


# ---------------------------------------------------------------------------
# codecs property
# ---------------------------------------------------------------------------


class TestFFMpegCodecsProperty:
  CODEC_OUTPUT = " D.V.L. h264                 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (decoders: h264 h264_cuvid ) (encoders: libx264 )\n DEA.L. aac                  AAC (Advanced Audio Coding)\n"

  def test_codecs_returns_dict(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value=self.CODEC_OUTPUT):
      result = ff.codecs
    assert isinstance(result, dict)
    assert "h264" in result
    assert "aac" in result

  def test_codecs_encoder_list(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value=self.CODEC_OUTPUT):
      result = ff.codecs
    assert "libx264" in result["h264"]["encoders"]

  def test_codecs_decoder_list(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value=self.CODEC_OUTPUT):
      result = ff.codecs
    assert "h264" in result["h264"]["decoders"]

  def test_codecs_self_encoder_fallback(self):
    """When no explicit encoders list, codec name is used as self-encoder."""
    ff = _make_ffmpeg()
    # aac: DEA → D=decoder capable, E=encoder capable → self-encoder fallback
    with patch.object(ff, "_get_stdout", return_value=self.CODEC_OUTPUT):
      result = ff.codecs
    assert "aac" in result["aac"]["encoders"]

  def test_codecs_empty_output(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value=""):
      result = ff.codecs
    assert result == {}


# ---------------------------------------------------------------------------
# hwaccels property
# ---------------------------------------------------------------------------


class TestFFMpegHwaccels:
  def test_hwaccels_parses_lines(self):
    ff = _make_ffmpeg()
    output = "Hardware acceleration methods:\nvdpau\ncuda\nvideotoolbox\n"
    with patch.object(ff, "_get_stdout", return_value=output):
      result = ff.hwaccels
    assert "vdpau" in result
    assert "cuda" in result
    assert "videotoolbox" in result

  def test_hwaccels_empty(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value="Hardware acceleration methods:\n"):
      result = ff.hwaccels
    assert result == []


# ---------------------------------------------------------------------------
# encoders property
# ---------------------------------------------------------------------------


class TestFFMpegEncoders:
  ENCODER_OUTPUT = "Encoders:\n V..... libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10\n A..... aac                  AAC (Advanced Audio Coding)\n"

  def test_encoders_returns_list(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value=self.ENCODER_OUTPUT):
      result = ff.encoders
    assert "libx264" in result
    assert "aac" in result

  def test_encoders_empty(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value=""):
      result = ff.encoders
    assert result == []


# ---------------------------------------------------------------------------
# decoders property
# ---------------------------------------------------------------------------


class TestFFMpegDecoders:
  def test_decoders_returns_list(self):
    ff = _make_ffmpeg()
    # regex requires exactly 6 chars: [A-Z.]{6}
    output = " VFS... h264                 H.264 / AVC\n AFS... aac                  AAC\n"
    with patch.object(ff, "_get_stdout", return_value=output):
      result = ff.decoders
    assert "h264" in result
    assert "aac" in result


# ---------------------------------------------------------------------------
# pix_fmts property
# ---------------------------------------------------------------------------


class TestFFMpegPixFmts:
  # Each line: FLAGS NAME NB_COMPONENTS BITS_PER_PIXEL BIT_DEPTHS (5 tokens)
  PIX_FMT_OUTPUT = "\n" * 8 + "IO... yuv420p 3 12 8-8-8\n IO... yuv420p10le 3 30 10-10-10\n"

  def test_pix_fmts_returns_dict(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value=self.PIX_FMT_OUTPUT):
      result = ff.pix_fmts
    assert "yuv420p" in result
    assert result["yuv420p"] == 8

  def test_pix_fmts_multi_component_depth(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value=self.PIX_FMT_OUTPUT):
      result = ff.pix_fmts
    assert "yuv420p10le" in result
    assert result["yuv420p10le"] == 10

  def test_pix_fmts_ignores_malformed_lines(self):
    ff = _make_ffmpeg()
    output = "\n" * 8 + "IO... badformat\n"  # only 2 tokens, not 5
    with patch.object(ff, "_get_stdout", return_value=output):
      result = ff.pix_fmts
    assert result == {}


# ---------------------------------------------------------------------------
# hwaccel_decoder
# ---------------------------------------------------------------------------


class TestHwaccelDecoder:
  def test_known_codec_combined(self):
    ff = _make_ffmpeg()
    assert ff.hwaccel_decoder("h264", "cuvid") == "h264_cuvid"

  def test_mpeg1_synonym(self):
    ff = _make_ffmpeg()
    assert ff.hwaccel_decoder("mpeg1video", "cuvid") == "mpeg1_cuvid"

  def test_mpeg2_synonym(self):
    ff = _make_ffmpeg()
    assert ff.hwaccel_decoder("mpeg2video", "vdpau") == "mpeg2_vdpau"

  def test_unknown_codec_passthrough(self):
    ff = _make_ffmpeg()
    assert ff.hwaccel_decoder("hevc", "cuvid") == "hevc_cuvid"


# ---------------------------------------------------------------------------
# encoder_formats / decoder_formats
# ---------------------------------------------------------------------------


class TestEncoderDecoderFormats:
  HELP_WITH_FMT = "Encoder libx264 [H.264 / AVC]:\nSupported pixel formats: yuv420p yuv422p yuv444p\n"
  HELP_WITHOUT_FMT = "Encoder aac [AAC]:\nSome other line\n"

  def test_encoder_formats_found(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value=self.HELP_WITH_FMT):
      result = ff.encoder_formats("libx264")
    assert "yuv420p" in result
    assert "yuv444p" in result

  def test_encoder_formats_not_found(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value=self.HELP_WITHOUT_FMT):
      result = ff.encoder_formats("aac")
    assert result == []

  def test_decoder_formats_found(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value=self.HELP_WITH_FMT):
      result = ff.decoder_formats("h264")
    assert "yuv420p" in result

  def test_decoder_formats_not_found(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value=""):
      result = ff.decoder_formats("h264")
    assert result == []


# ---------------------------------------------------------------------------
# framedata
# ---------------------------------------------------------------------------


class TestFramedata:
  FRAME_JSON = json.dumps({"frames": [{"color_space": "bt709", "pix_fmt": "yuv420p", "side_data_list": []}]})

  def test_framedata_returns_dict(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value=self.FRAME_JSON):
      result = ff.framedata("movie.mkv")
    assert result["color_space"] == "bt709"
    assert result["pix_fmt"] == "yuv420p"

  def test_framedata_invalid_json_raises(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value="not json"):
      with pytest.raises(FFMpegError, match="framedata"):
        ff.framedata("movie.mkv")

  def test_framedata_empty_frames_raises(self):
    ff = _make_ffmpeg()
    with patch.object(ff, "_get_stdout", return_value=json.dumps({"frames": []})):
      with pytest.raises(FFMpegError):
        ff.framedata("movie.mkv")


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


class TestProbe:
  PROBE_OUTPUT = (
    "[FORMAT]\n"
    "format_name=matroska,webm\n"
    "duration=120.0\n"
    "bit_rate=5000000\n"
    "[/FORMAT]\n"
    "[STREAM]\n"
    "index=0\n"
    "codec_type=video\n"
    "codec_name=h264\n"
    "width=1920\n"
    "height=1080\n"
    "[/STREAM]\n"
    "[STREAM]\n"
    "index=1\n"
    "codec_type=audio\n"
    "codec_name=aac\n"
    "channels=2\n"
    "[/STREAM]\n"
  )

  def test_probe_returns_none_when_missing(self):
    ff = _make_ffmpeg()
    with patch("os.path.exists", return_value=False):
      result = ff.probe("/nonexistent/file.mkv")
    assert result is None

  def test_probe_returns_none_when_no_streams_or_format(self):
    ff = _make_ffmpeg()
    with patch("os.path.exists", return_value=True), patch.object(ff, "_get_stdout", return_value=""), patch.object(ff, "framedata", side_effect=Exception):
      result = ff.probe("empty.mkv")
    assert result is None

  def test_probe_parses_streams(self):
    ff = _make_ffmpeg()
    with patch("os.path.exists", return_value=True), patch.object(ff, "_get_stdout", return_value=self.PROBE_OUTPUT), patch.object(ff, "framedata", side_effect=Exception):
      result = ff.probe("movie.mkv")
    assert result is not None
    assert result.video is not None
    assert result.video.codec == "h264"
    assert len(result.audio) == 1


# ---------------------------------------------------------------------------
# thumbnails
# ---------------------------------------------------------------------------


class TestThumbnails:
  def test_thumbnails_raises_when_file_missing(self):
    ff = _make_ffmpeg()
    with patch("os.path.exists", return_value=False):
      with pytest.raises(IOError, match="No such file"):
        ff.thumbnails("nonexistent.mkv", [(5, "/tmp/thumb.jpg")])

  def test_thumbnails_raises_on_ffmpeg_error(self, tmp_path):
    ff = _make_ffmpeg()
    src = tmp_path / "video.mkv"
    src.write_bytes(b"fake")
    out = str(tmp_path / "thumb.jpg")  # does not actually exist on disk

    mock_proc = MagicMock()
    mock_proc.communicate.return_value = (b"", b"some stderr")
    # src exists but out does not — triggers the "Error creating thumbnail" path
    with patch("os.path.exists", side_effect=lambda p: p == str(src)), patch.object(ff, "_spawn", return_value=mock_proc):
      with pytest.raises(FFMpegError):
        ff.thumbnails(str(src), [(5, out)])

  def test_thumbnails_with_size(self, tmp_path):
    ff = _make_ffmpeg()
    src = tmp_path / "video.mkv"
    src.write_bytes(b"fake")
    out = tmp_path / "thumb.jpg"
    out.write_bytes(b"fake_thumb")

    mock_proc = MagicMock()
    mock_proc.communicate.return_value = (b"", b"some output")
    with patch("os.path.exists", return_value=True), patch.object(ff, "_spawn", return_value=mock_proc):
      ff.thumbnails(str(src), [(5, str(out), "320x240")])
    mock_proc.communicate.assert_called_once()

  def test_thumbnail_delegates_to_thumbnails(self, tmp_path):
    ff = _make_ffmpeg()
    src = str(tmp_path / "video.mkv")
    out = str(tmp_path / "thumb.jpg")
    with patch.object(ff, "thumbnails") as mock_thumbnails:
      ff.thumbnail(src, 5, out, "320x240")
    mock_thumbnails.assert_called_once_with(src, [(5, out, "320x240", 4)])


# ---------------------------------------------------------------------------
# convert (main loop branches)
# ---------------------------------------------------------------------------


class TestConvertMainLoop:
  def _fake_proc(self, stderr_chunks):
    """Return a mock Popen process emitting the given stderr chunks."""
    mock_proc = MagicMock()
    mock_proc.stderr.read.side_effect = [c.encode() if isinstance(c, str) else c for c in stderr_chunks] + [b""]
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0
    return mock_proc

  def test_convert_raises_when_input_missing(self):
    ff = _make_ffmpeg()
    with patch("os.path.exists", return_value=False):
      with pytest.raises(FFMpegError, match="Input file"):
        list(ff.convert("out.mp4", ["-i", "nonexistent.mkv"]))

  def test_convert_yields_zero_first(self, tmp_path):
    ff = _make_ffmpeg()
    src = tmp_path / "in.mkv"
    src.write_bytes(b"x")
    # Provide non-empty output so convert doesn't raise "Error while calling"
    mock_proc = self._fake_proc(["ffmpeg progress output"])
    with (
      patch("os.path.exists", return_value=True),
      patch.object(ff, "_spawn", return_value=mock_proc),
      patch.object(ff, "generateCommands", return_value=[str(ff.ffmpeg_path), "-i", str(src), "-y", "out.mp4"]),
    ):
      results = list(ff.convert("out.mp4", ["-i", str(src)], timeout=0))
    assert results[0][0] == 0

  def test_convert_parses_time_from_stderr(self, tmp_path):
    ff = _make_ffmpeg()
    src = tmp_path / "in.mkv"
    src.write_bytes(b"x")
    stderr = "frame=100 fps=25 time=00:00:05.00 bitrate=1000\r"
    mock_proc = self._fake_proc([stderr])
    with (
      patch("os.path.exists", return_value=True),
      patch.object(ff, "_spawn", return_value=mock_proc),
      patch.object(ff, "generateCommands", return_value=[str(ff.ffmpeg_path), "-i", str(src), "-y", "out.mp4"]),
    ):
      results = list(ff.convert("out.mp4", ["-i", str(src)], timeout=0))
    timecodes = [r[0] for r in results if r[0] > 0]
    assert any(abs(t - 5.0) < 1 for t in timecodes)

  def test_convert_raises_on_nonzero_returncode(self, tmp_path):
    from converter.ffmpeg import FFMpegConvertError

    ff = _make_ffmpeg()
    src = tmp_path / "in.mkv"
    src.write_bytes(b"x")
    mock_proc = self._fake_proc(["some ffmpeg output"])
    mock_proc.returncode = 1
    with (
      patch("os.path.exists", return_value=True),
      patch.object(ff, "_spawn", return_value=mock_proc),
      patch.object(ff, "generateCommands", return_value=[str(ff.ffmpeg_path), "-i", str(src), "-y", "out.mp4"]),
    ):
      with pytest.raises(FFMpegConvertError):
        list(ff.convert("out.mp4", ["-i", str(src)], timeout=0))

  def test_convert_raises_on_empty_output(self, tmp_path):
    ff = _make_ffmpeg()
    src = tmp_path / "in.mkv"
    src.write_bytes(b"x")
    mock_proc = MagicMock()
    mock_proc.stderr.read.return_value = b""
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0
    with (
      patch("os.path.exists", return_value=True),
      patch.object(ff, "_spawn", return_value=mock_proc),
      patch.object(ff, "generateCommands", return_value=[str(ff.ffmpeg_path), "-i", str(src), "-y", "out.mp4"]),
    ):
      with pytest.raises(FFMpegError, match="calling ffmpeg"):
        list(ff.convert("out.mp4", ["-i", str(src)], timeout=0))

  def test_convert_null_output(self, tmp_path):
    ff = _make_ffmpeg()
    src = tmp_path / "in.mkv"
    src.write_bytes(b"x")
    mock_proc = self._fake_proc(["output data"])
    with (
      patch("os.path.exists", return_value=True),
      patch.object(ff, "_spawn", return_value=mock_proc),
      patch.object(ff, "generateCommands", return_value=[str(ff.ffmpeg_path), "-i", str(src), "-f", "null", "-"]),
    ):
      results = list(ff.convert(None, ["-i", str(src)], timeout=0))
    assert results[0][0] == 0


# ---------------------------------------------------------------------------
# MediaStreamInfo.parse_ffprobe branches
# ---------------------------------------------------------------------------


class TestMediaStreamInfoParseBranches:
  def test_parse_tag_prefix(self):
    s = MediaStreamInfo()
    s.parse_ffprobe("TAG:language", "eng")
    assert s.metadata["language"] == "eng"

  def test_parse_tag_title_preserves_case(self):
    s = MediaStreamInfo()
    s.parse_ffprobe("TAG:title", "My Title")
    assert s.metadata["title"] == "My Title"

  def test_parse_disposition_prefix(self):
    s = MediaStreamInfo()
    s.parse_ffprobe("DISPOSITION:forced", "1")
    assert s.disposition["forced"] is True

  def test_parse_bps_tag(self):
    s = MediaStreamInfo()
    s.parse_ffprobe("tag:bps-eng", "5000000")
    assert s.bitrate == 5000000

  def test_parse_bps_below_1000_cleared(self):
    s = MediaStreamInfo()
    s.parse_ffprobe("bit_rate", "500")
    assert s.bitrate is None

  def test_parse_audio_fps_fraction(self):
    s = MediaStreamInfo()
    s.type = "audio"
    s.parse_ffprobe("avg_frame_rate", "44100/1")
    assert s.fps == pytest.approx(44100.0)

  def test_parse_audio_fps_float(self):
    s = MediaStreamInfo()
    s.type = "audio"
    s.parse_ffprobe("avg_frame_rate", "23.976")
    assert s.fps == pytest.approx(23.976)

  def test_parse_attached_pic(self):
    s = MediaStreamInfo()
    s.parse_ffprobe("DISPOSITION:attached_pic", "1")
    assert s.attached_pic == 1

  def test_parse_profile(self):
    s = MediaStreamInfo()
    s.parse_ffprobe("profile", "High 10")
    assert s.profile == "high10"


# ---------------------------------------------------------------------------
# MediaInfo
# ---------------------------------------------------------------------------


class TestMediaInfoParsing:
  PROBE_OUTPUT = (
    "[FORMAT]\nformat_name=mp4\nduration=3600.0\nbit_rate=8000000\n[/FORMAT]\n"
    "[STREAM]\nindex=0\ncodec_type=video\ncodec_name=h264\nwidth=1920\nheight=1080\n[/STREAM]\n"
    "[STREAM]\nindex=1\ncodec_type=audio\ncodec_name=aac\nchannels=6\n[/STREAM]\n"
    "[STREAM]\nindex=2\ncodec_type=subtitle\ncodec_name=srt\n[/STREAM]\n"
    "[STREAM]\nindex=3\ncodec_type=attachment\ncodec_name=ttf\n[/STREAM]\n"
  )

  def test_parse_full_file(self):
    info = MediaInfo()
    info.parse_ffprobe(self.PROBE_OUTPUT)
    assert info.video is not None
    assert len(info.audio) == 1
    assert len(info.subtitle) == 1
    assert len(info.attachment) == 1

  def test_json_format(self):
    info = MediaInfo()
    info.parse_ffprobe(self.PROBE_OUTPUT)
    j = info.json
    assert "format" in j
    assert "audio" in j
    assert "video" in j
    assert j["format"] == "mp4"

  def test_attachment_list(self, make_stream):
    info = MediaInfo()
    att = make_stream(type="attachment", codec="ttf", index=3)
    info.streams.append(att)
    assert len(info.attachment) == 1
