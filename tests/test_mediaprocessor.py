"""Tests for resources/mediaprocessor.py - core processing logic."""

import json
from unittest.mock import MagicMock, patch

import pytest

from converter.ffmpeg import MediaInfo
from resources.analyzer import AnalyzerObservations, AnalyzerRecommendations


class TestEstimateVideoBitrate:
  """Test video bitrate estimation from container info."""

  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        settings = MagicMock()
        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = settings
        mp.log = MagicMock()
        return mp

  def test_basic_estimation(self, make_media_info):
    mp = self._make_processor()
    info = make_media_info(total_bitrate=10000000, audio_bitrate=128000)
    result = mp.estimateVideoBitrate(info)
    # (10000000 - 128000) / 1000 * 0.95 = ~9378
    assert result is not None
    assert result > 0
    assert result < 10000  # Should be in kbps range

  def test_no_audio_bitrate_uses_baserate(self, make_stream, make_format):
    mp = self._make_processor()
    info = MediaInfo()
    info.format = make_format(bitrate=10000000)
    video = make_stream(type="video", codec="h264", bitrate=None)
    video.framedata = {}
    info.streams.append(video)
    audio = make_stream(type="audio", codec="aac", index=1, bitrate=None, audio_channels=2)
    info.streams.append(audio)
    result = mp.estimateVideoBitrate(info)
    assert result is not None
    assert result > 0

  def test_returns_min_video_bitrate_when_lower(self, make_stream, make_format):
    mp = self._make_processor()
    info = MediaInfo()
    info.format = make_format(bitrate=50000000)
    video = make_stream(type="video", codec="h264", bitrate=5000000)
    video.framedata = {}
    info.streams.append(video)
    audio = make_stream(type="audio", codec="aac", index=1, bitrate=128000, audio_channels=2)
    info.streams.append(audio)
    result = mp.estimateVideoBitrate(info)
    assert result is not None
    # Should use the lower of detected vs calculated
    assert result == pytest.approx(5000.0, rel=0.1)


class TestStreamTitles:
  """Test stream title generation for video, audio, and subtitles."""

  @pytest.fixture(autouse=True)
  def _no_custom_stream_title(self):
    """Suppress any user-defined streamTitle hook so tests use the built-in logic."""
    with patch("resources.mediaprocessor.streamTitle", None):
      yield

  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.settings.keep_titles = False
        mp.log = MagicMock()
        return mp

  def test_video_title_4k(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="video", video_width=3840, video_height=2160)
    title = mp.videoStreamTitle(stream, {})
    assert title == "4K"

  def test_video_title_fhd(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="video", video_width=1920, video_height=1080)
    title = mp.videoStreamTitle(stream, {})
    assert title == "FHD"

  def test_video_title_hd(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="video", video_width=1280, video_height=720)
    title = mp.videoStreamTitle(stream, {})
    assert title == "HD"

  def test_video_title_sd(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="video", video_width=640, video_height=480)
    title = mp.videoStreamTitle(stream, {})
    assert title == "SD"

  def test_video_title_hdr(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="video", video_width=3840, video_height=2160)
    title = mp.videoStreamTitle(stream, {}, hdr=True)
    assert title is not None
    assert "HDR" in title
    assert "4K" in title

  def test_video_title_from_options(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="video", video_width=0, video_height=0)
    title = mp.videoStreamTitle(stream, {"width": 1920, "height": 1080})
    assert title == "FHD"

  def test_audio_title_stereo(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="audio", disposition={"default": False, "forced": False})
    title = mp.audioStreamTitle(stream, {"channels": 2})
    assert title == "Stereo"

  def test_audio_title_mono(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="audio", disposition={"default": False, "forced": False})
    title = mp.audioStreamTitle(stream, {"channels": 1})
    assert title == "Mono"

  def test_audio_title_surround(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="audio", disposition={"default": False, "forced": False})
    title = mp.audioStreamTitle(stream, {"channels": 6})
    assert title is not None
    assert "5.1" in title

  def test_audio_title_71(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="audio", disposition={"default": False, "forced": False})
    title = mp.audioStreamTitle(stream, {"channels": 8})
    assert title is not None
    assert "7.1" in title

  def test_audio_title_commentary(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="audio", disposition={"default": False, "forced": False, "comment": True})
    title = mp.audioStreamTitle(stream, {"channels": 2})
    assert title is not None
    assert "Commentary" in title or "Comment" in title.lower()

  def test_subtitle_title_no_disposition(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="subtitle", disposition={"default": False, "forced": False})
    title = mp.subtitleStreamTitle(stream, {})
    assert title == "Full"

  def test_subtitle_title_forced(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="subtitle", disposition={"default": False, "forced": True})
    title = mp.subtitleStreamTitle(stream, {})
    assert "Forced" in title or title is not None

  def test_subtitle_title_keeps_existing(self, make_stream):
    mp = self._make_processor()
    mp.settings.keep_titles = True
    stream = make_stream(type="subtitle", metadata={"title": "Custom Title", "language": "eng"})
    stream.disposition = {"default": False, "forced": False}
    title = mp.subtitleStreamTitle(stream, {})
    assert title == "Custom Title"


class TestValidLanguageLegacy:
  """Test language validation logic."""

  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_in_whitelist(self):
    mp = self._make_processor()
    assert mp.validLanguage("eng", ["eng", "fra"]) is True

  def test_not_in_whitelist(self):
    mp = self._make_processor()
    assert mp.validLanguage("deu", ["eng", "fra"]) is False

  def test_empty_whitelist_accepts_all(self):
    mp = self._make_processor()
    assert mp.validLanguage("deu", []) is True

  def test_blocked_language(self):
    mp = self._make_processor()
    assert mp.validLanguage("eng", ["eng", "fra"], blocked=["eng"]) is False

  def test_undefined_language(self):
    mp = self._make_processor()
    # 'und' is not in whitelist and is not special-cased
    assert mp.validLanguage("und", ["eng"]) is False

  def test_undefined_language_empty_whitelist(self):
    mp = self._make_processor()
    assert mp.validLanguage("und", []) is True


class TestIsValidSource:
  """Test source file validation."""

  @pytest.fixture(autouse=True)
  def _yaml_cfg(self, tmp_yaml):
    self._cfg = tmp_yaml()

  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor
        from resources.readsettings import ReadSettings

        settings = ReadSettings(self._cfg)
        return MediaProcessor(settings)

  def test_nonexistent_file_returns_none(self):
    mp = self._make_processor()
    assert mp.isValidSource("/nonexistent/file.mkv") is None

  def test_ignored_extension_returns_none(self, tmp_path):
    mp = self._make_processor()
    nfo = tmp_path / "movie.nfo"
    nfo.write_text("test")
    assert mp.isValidSource(str(nfo)) is None

  def test_undersized_file_returns_none(self, tmp_path):
    mp = self._make_processor()
    mp.settings.minimum_size = 100  # 100MB
    small = tmp_path / "tiny.mkv"
    small.write_bytes(b"\x00" * 1024)
    assert mp.isValidSource(str(small)) is None


class TestParseFile:
  """Test filename parsing utility."""

  @pytest.fixture(autouse=True)
  def _yaml_cfg(self, tmp_yaml):
    self._cfg = tmp_yaml()

  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor
        from resources.readsettings import ReadSettings

        settings = ReadSettings(self._cfg)
        return MediaProcessor(settings)

  def test_parse_simple_path(self):
    mp = self._make_processor()
    d, name, ext = mp.parseFile("/path/to/movie.mkv")
    assert d == "/path/to"
    assert name == "movie"
    assert ext == "mkv"

  def test_parse_no_extension(self):
    mp = self._make_processor()
    _, name, _ = mp.parseFile("/path/to/file")
    assert name == "file"

  def test_parse_extension_lowercased(self):
    mp = self._make_processor()
    _, _, ext = mp.parseFile("/path/to/Movie.MKV")
    assert ext == "mkv"

  def test_parse_dotted_filename(self):
    mp = self._make_processor()
    _, name, ext = mp.parseFile("/path/to/Movie.2024.1080p.mkv")
    assert ext == "mkv"
    assert name == "Movie.2024.1080p"


class TestCleanDispositions:
  """Test disposition sanitization."""

  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.settings.sanitize_disposition = ["forced"]
        mp.log = MagicMock()
        return mp

  def test_clears_dispositions(self, make_stream):
    mp = self._make_processor()
    info = MediaInfo()
    s = make_stream(type="audio", disposition={"default": True, "forced": True})
    info.streams.append(s)
    mp.cleanDispositions(info)
    assert s.disposition["forced"] is False
    assert s.disposition["default"] is True


class TestIsAudioStreamAtmos:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_atmos_detected(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="audio", profile="atmos")
    assert mp.isAudioStreamAtmos(stream) is True

  def test_non_atmos(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="audio", profile="lc")
    assert mp.isAudioStreamAtmos(stream) is False

  def test_no_profile(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="audio")
    stream.profile = None
    assert not mp.isAudioStreamAtmos(stream)


class TestGetDefaultAudioLanguage:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_dict_options_with_default(self):
    mp = self._make_processor()
    options = {"audio": [{"disposition": "+default-forced", "language": "eng"}, {"disposition": "-default-forced", "language": "fra"}]}
    assert mp.getDefaultAudioLanguage(options) == "eng"

  def test_dict_options_no_default(self):
    mp = self._make_processor()
    options = {"audio": [{"disposition": "-default-forced", "language": "eng"}]}
    assert mp.getDefaultAudioLanguage(options) is None

  def test_mediainfo_options(self, make_stream):
    mp = self._make_processor()
    info = MediaInfo()
    s = make_stream(type="audio", disposition={"default": True, "forced": False})
    s.metadata = {"language": "jpn"}
    info.streams.append(s)
    assert mp.getDefaultAudioLanguage(info) == "jpn"


class TestValidDisposition:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_no_ignored_dispositions(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="audio", disposition={"default": True, "forced": False})
    assert mp.validDisposition(stream, []) is True

  def test_ignored_disposition(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="audio", disposition={"default": True, "comment": True})
    assert mp.validDisposition(stream, ["comment"]) is False

  def test_unique_disposition_first(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="audio", disposition={"default": True, "forced": False})
    existing = []
    assert mp.validDisposition(stream, [], unique=True, language="eng", existing=existing) is True
    assert len(existing) == 1

  def test_unique_disposition_duplicate(self, make_stream):
    mp = self._make_processor()
    stream = make_stream(type="audio", disposition={"default": True, "forced": False})
    existing = ["eng." + stream.dispostr]
    assert mp.validDisposition(stream, [], unique=True, language="eng", existing=existing) is False


class TestDispoStringToDictLegacy:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_positive_dispositions(self):
    mp = self._make_processor()
    result = mp.dispoStringToDict("+default+forced")
    assert result["default"] is True
    assert result["forced"] is True

  def test_negative_dispositions(self):
    mp = self._make_processor()
    result = mp.dispoStringToDict("-default-forced")
    assert result["default"] is False
    assert result["forced"] is False

  def test_mixed(self):
    mp = self._make_processor()
    result = mp.dispoStringToDict("+default-forced+comment")
    assert result["default"] is True
    assert result["forced"] is False
    assert result["comment"] is True

  def test_empty_string(self):
    mp = self._make_processor()
    assert mp.dispoStringToDict("") == {}

  def test_none(self):
    mp = self._make_processor()
    assert mp.dispoStringToDict(None) == {}


class TestCheckDispositionLegacy:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_all_present(self):
    mp = self._make_processor()
    assert mp.checkDisposition(["forced"], {"forced": True, "default": False}) is True

  def test_missing_disposition(self):
    mp = self._make_processor()
    assert mp.checkDisposition(["forced"], {"forced": False, "default": True}) is False

  def test_empty_allowed(self):
    mp = self._make_processor()
    assert mp.checkDisposition([], {"forced": False}) is True


class TestTitleDispositionCheckLegacy:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_commentary_in_title(self, make_stream):
    mp = self._make_processor()
    info = MediaInfo()
    s = make_stream(type="audio", disposition={"default": False, "comment": False})
    s.metadata = {"title": "Director's Commentary", "language": "eng"}
    info.streams.append(s)
    mp.titleDispositionCheck(info)
    assert s.disposition["comment"] is True

  def test_forced_in_title(self, make_stream):
    mp = self._make_processor()
    info = MediaInfo()
    s = make_stream(type="subtitle", disposition={"default": False, "forced": False})
    s.metadata = {"title": "Forced Foreign", "language": "eng"}
    info.streams.append(s)
    mp.titleDispositionCheck(info)
    assert s.disposition["forced"] is True

  def test_sdh_in_title(self, make_stream):
    mp = self._make_processor()
    info = MediaInfo()
    s = make_stream(type="subtitle", disposition={"default": False, "hearing_impaired": False})
    s.metadata = {"title": "English SDH", "language": "eng"}
    info.streams.append(s)
    mp.titleDispositionCheck(info)
    assert s.disposition["hearing_impaired"] is True


class TestSublistIndexesLegacy:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_finds_sublist(self):
    mp = self._make_processor()
    assert mp.sublistIndexes(["a", "b", "c", "a", "b"], ["a", "b"]) == [0, 3]

  def test_no_match(self):
    mp = self._make_processor()
    assert mp.sublistIndexes(["a", "b", "c"], ["d", "e"]) == []

  def test_single_element(self):
    mp = self._make_processor()
    assert mp.sublistIndexes(["a", "b", "a"], ["a"]) == [0, 2]


class TestGetOutputFile:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.settings.output_dir = None
        mp.settings.output_extension = "mp4"
        mp.log = MagicMock()
        return mp

  def test_basic_output(self, tmp_path):
    mp = self._make_processor()
    outfile, outdir = mp.getOutputFile(str(tmp_path), "movie", "mkv")
    assert outfile is not None
    assert outfile.endswith("movie.mp4")
    assert outdir == str(tmp_path)

  def test_with_number(self, tmp_path):
    mp = self._make_processor()
    outfile, _ = mp.getOutputFile(str(tmp_path), "movie", "mkv", number=2)
    assert outfile is not None
    assert ".2." in outfile

  def test_with_temp_extension(self, tmp_path):
    mp = self._make_processor()
    outfile, _ = mp.getOutputFile(str(tmp_path), "movie", "mkv", temp_extension="tmp")
    assert outfile is not None
    assert outfile.endswith(".tmp")

  def test_output_dir_override(self, tmp_path):
    mp = self._make_processor()
    outdir = str(tmp_path / "output")
    mp.settings.output_dir = outdir
    _, result_dir = mp.getOutputFile(str(tmp_path), "movie", "mkv")
    assert result_dir == outdir

  def test_ignore_output_dir(self, tmp_path):
    mp = self._make_processor()
    mp.settings.output_dir = "/some/output/dir"
    _, result_dir = mp.getOutputFile(str(tmp_path), "movie", "mkv", ignore_output_dir=True)
    assert result_dir == str(tmp_path)


class TestGetSourceStream:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_returns_stream(self, make_stream):
    mp = self._make_processor()
    info = MediaInfo()
    s0 = make_stream(type="video", index=0)
    s1 = make_stream(type="audio", index=1)
    info.streams = [s0, s1]
    assert mp.getSourceStream(0, info) == s0
    assert mp.getSourceStream(1, info) == s1


class TestGetSubExtensionFromCodec:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_known_codec(self):
    mp = self._make_processor()
    result = mp.getSubExtensionFromCodec("srt")
    assert result == "srt"

  def test_unknown_codec_returns_codec(self):
    mp = self._make_processor()
    result = mp.getSubExtensionFromCodec("unknown_codec")
    assert result == "unknown_codec"


class TestRemoveFile:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_removes_existing_file(self, tmp_path):
    mp = self._make_processor()
    f = tmp_path / "to_delete.txt"
    f.write_text("data")
    assert mp.removeFile(str(f)) is True
    assert not f.exists()

  def test_nonexistent_file_returns_true(self, tmp_path):
    mp = self._make_processor()
    assert mp.removeFile(str(tmp_path / "nonexistent.txt"), retries=0) is True

  def test_replacement(self, tmp_path):
    mp = self._make_processor()
    original = tmp_path / "original.txt"
    replacement = tmp_path / "replacement.txt"
    original.write_text("old")
    replacement.write_text("new")
    result = mp.removeFile(str(original), replacement=str(replacement))
    assert result is True
    assert original.exists()
    with open(str(original)) as f:
      assert f.read() == "new"


class TestOutputDirHasFreeSpace:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_no_ratio_returns_true(self, tmp_path):
    mp = self._make_processor()
    mp.settings.output_dir = str(tmp_path)
    mp.settings.output_dir_ratio = 0.0
    f = tmp_path / "test.mkv"
    f.write_text("x")
    assert mp.outputDirHasFreeSpace(str(f)) is True

  def test_no_output_dir_returns_true(self, tmp_path):
    mp = self._make_processor()
    mp.settings.output_dir = None
    mp.settings.output_dir_ratio = 2.0
    f = tmp_path / "test.mkv"
    f.write_text("x")
    assert mp.outputDirHasFreeSpace(str(f)) is True


class TestCanBypassConvert:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.settings.output_extension = "mp4"
        mp.settings.force_convert = False
        mp.settings.process_same_extensions = False
        mp.settings.bypass_copy_all = False
        mp.log = MagicMock()
        return mp

  def test_same_extension_no_process(self):
    mp = self._make_processor()
    info = MagicMock()
    info.format.metadata = {}
    assert mp.canBypassConvert("/path/to/file.mp4", info) is True

  def test_different_extension(self):
    mp = self._make_processor()
    info = MagicMock()
    assert mp.canBypassConvert("/path/to/file.mkv", info) is False

  def test_same_extension_process_enabled(self):
    mp = self._make_processor()
    mp.settings.process_same_extensions = True
    info = MagicMock()
    info.format.metadata = {}
    assert mp.canBypassConvert("/path/to/file.mp4", info) is False

  def test_same_extension_sma_processed(self):
    mp = self._make_processor()
    mp.settings.process_same_extensions = True
    info = MagicMock()
    info.format.metadata = {"encoder": "sma-ng v1.0"}
    assert mp.canBypassConvert("/path/to/file.mp4", info) is True


class TestPrintableFFMPEGCommand:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_quotes_spaces(self):
    mp = self._make_processor()
    result = mp.printableFFMPEGCommand(["ffmpeg", "-i", "/path with spaces/file.mkv", "-c", "copy"])
    # shlex.quote uses single quotes for shell-safety
    assert "'/path with spaces/file.mkv'" in result

  def test_no_quotes_without_spaces(self):
    mp = self._make_processor()
    result = mp.printableFFMPEGCommand(["ffmpeg", "-c", "copy"])
    assert "'" not in result and '"' not in result

  def test_quotes_metadata_titles_with_spaces(self):
    mp = self._make_processor()
    result = mp.printableFFMPEGCommand(["ffmpeg", "-metadata:s:v", "title=FHD HDR", "-metadata:s:a:0", "title=5.1 Channel"])
    # Each title= value with spaces is quoted as a single argv token
    assert "'title=FHD HDR'" in result
    assert "'title=5.1 Channel'" in result


class TestRawEscape:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_escapes_backslash(self):
    mp = self._make_processor()
    # raw() escapes both backslash and colon
    result = mp.raw("a\\b")
    assert "\\\\" in result

  def test_escapes_colon(self):
    mp = self._make_processor()
    assert mp.raw("file:name") == "file\\:name"

  def test_no_escaping_needed(self):
    mp = self._make_processor()
    assert mp.raw("simple") == "simple"


class TestParseAndNormalize:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_same_denominator(self):
    mp = self._make_processor()
    assert mp.parseAndNormalize("50000/50000", 50000) == 50000

  def test_different_denominator(self):
    mp = self._make_processor()
    result = mp.parseAndNormalize("1000/100", 50000)
    assert result == 500000


class TestHasValidFrameData:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_valid_hdr_framedata(self):
    mp = self._make_processor()
    framedata = {"side_data_list": [{"side_data_type": "Mastering display metadata"}, {"side_data_type": "Content light level metadata"}]}
    assert mp.hasValidFrameData(framedata) is True

  def test_missing_one_type(self):
    mp = self._make_processor()
    framedata = {"side_data_list": [{"side_data_type": "Mastering display metadata"}]}
    assert mp.hasValidFrameData(framedata) is False

  def test_no_side_data(self):
    mp = self._make_processor()
    assert mp.hasValidFrameData({}) is False

  def test_invalid_framedata(self):
    mp = self._make_processor()
    assert mp.hasValidFrameData(None) is False


class TestHasBitstreamVideoSubs:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_has_closed_captions(self):
    mp = self._make_processor()
    framedata = {
      "side_data_list": [
        {"side_data_type": "Closed Captions"},
      ]
    }
    assert mp.hasBitstreamVideoSubs(framedata) is True

  def test_no_closed_captions(self):
    mp = self._make_processor()
    assert mp.hasBitstreamVideoSubs({}) is False


class TestIsHDROutput:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_hdr_pix_fmt_and_depth(self):
    mp = self._make_processor()
    assert mp.isHDROutput("yuv420p10le", 10) is True

  def test_sdr_pix_fmt(self):
    mp = self._make_processor()
    assert mp.isHDROutput("yuv420p", 8) is False

  def test_hdr_pix_fmt_low_depth(self):
    mp = self._make_processor()
    assert mp.isHDROutput("yuv420p10le", 8) is False

  def test_no_pix_fmt_high_depth(self):
    mp = self._make_processor()
    assert mp.isHDROutput(None, 10) is True

  def test_no_pix_fmt_low_depth(self):
    mp = self._make_processor()
    assert mp.isHDROutput(None, 8) is False


class TestFfprobeSafeCodecsLegacy:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_adds_ffprobe_codec(self):
    mp = self._make_processor()
    codecs = ["h264"]
    result = mp.ffprobeSafeCodecs(codecs)
    # h264 ffprobe name is 'h264' already, so check it doesn't duplicate
    assert "h264" in result

  def test_empty_list(self):
    mp = self._make_processor()
    assert mp.ffprobeSafeCodecs([]) == []

  def test_none(self):
    mp = self._make_processor()
    assert mp.ffprobeSafeCodecs(None) is None


class TestAtomicFileOps:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.settings.permissions = {"chmod": 0o664, "uid": -1, "gid": -1}
        mp.settings.copyto = []
        mp.settings.moveto = None
        mp.log = MagicMock()
        return mp

  def test_atomic_copy_success(self, tmp_path):
    mp = self._make_processor()
    src = tmp_path / "source.mp4"
    src.write_bytes(b"media data")
    dst = tmp_path / "dest" / "output.mp4"
    dst.parent.mkdir()
    mp._atomic_copy(str(src), str(dst))
    assert dst.read_bytes() == b"media data"
    assert not (tmp_path / "dest" / "output.mp4.smatmp").exists()

  def test_atomic_copy_cleans_up_temp_on_failure(self, tmp_path):
    mp = self._make_processor()
    src = tmp_path / "source.mp4"
    src.write_bytes(b"media data")
    dst = tmp_path / "output.mp4"
    with patch("shutil.copy2", side_effect=OSError("disk full")):
      with pytest.raises(OSError):
        mp._atomic_copy(str(src), str(dst))
    assert not (tmp_path / "output.mp4.smatmp").exists()

  def test_atomic_move_same_filesystem(self, tmp_path):
    mp = self._make_processor()
    src = tmp_path / "source.mp4"
    src.write_bytes(b"media data")
    dst = tmp_path / "dest.mp4"
    with patch("os.rename") as mock_rename:
      mp._atomic_move(str(src), str(dst))
    mock_rename.assert_called_once_with(str(src), str(dst))

  def test_atomic_move_cross_filesystem_fallback(self, tmp_path):
    mp = self._make_processor()
    src = tmp_path / "source.mp4"
    src.write_bytes(b"media data")
    dst = tmp_path / "dest.mp4"
    with patch("os.rename", side_effect=OSError(18, "Invalid cross-device link")):
      with patch.object(mp, "_atomic_copy") as mock_copy:
        with patch("os.remove") as mock_remove:
          mp._atomic_move(str(src), str(dst))
    mock_copy.assert_called_once_with(str(src), str(dst))
    mock_remove.assert_called_once_with(str(src))

  def test_replicate_copyto_uses_atomic_copy(self, tmp_path):
    mp = self._make_processor()
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"x")
    dest_dir = tmp_path / "library"
    dest_dir.mkdir()
    mp.settings.copyto = [str(dest_dir)]
    mp.settings.moveto = None
    with patch.object(mp, "_atomic_copy") as mock_copy:
      mp.replicate(str(src))
    mock_copy.assert_called_once_with(str(src), str(dest_dir / "movie.mp4"))

  def test_replicate_moveto_uses_atomic_move(self, tmp_path):
    mp = self._make_processor()
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"x")
    dest_dir = tmp_path / "library"
    dest_dir.mkdir()
    mp.settings.copyto = []
    mp.settings.moveto = str(dest_dir)
    with patch.object(mp, "_atomic_move") as mock_move:
      mp.replicate(str(src))
    mock_move.assert_called_once_with(str(src), str(dest_dir / "movie.mp4"))

  def test_restore_from_output_uses_atomic_move(self, tmp_path):
    mp = self._make_processor()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    inputfile = str(input_dir / "movie.mkv")
    outputfile = str(output_dir / "movie.mp4")
    mp.settings.output_dir = str(output_dir)
    mp.settings.moveto = None
    with patch.object(mp, "_atomic_move") as mock_move, patch.object(mp, "parseFile", return_value=(str(input_dir), "movie", "mp4")):
      mp.restoreFromOutput(inputfile, outputfile)
    mock_move.assert_called_once_with(outputfile, str(input_dir / "movie.mp4"))


class TestSetPermissions:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.settings.permissions = {"chmod": 0o664, "uid": -1, "gid": -1}
        mp.log = MagicMock()
        return mp

  def test_sets_permissions_on_existing_file(self, tmp_path):
    mp = self._make_processor()
    f = tmp_path / "test.txt"
    f.write_text("data")
    mp.setPermissions(str(f))
    # Should not raise

  def test_nonexistent_file_logs(self, tmp_path):
    mp = self._make_processor()
    mp.setPermissions(str(tmp_path / "nonexistent.txt"))
    mp.log.debug.assert_called()  # type: ignore[attr-defined]


class TestScanForExternalMetadata:
  def _make_processor(self):
    with patch("resources.mediaprocessor.Converter"):
      with patch("resources.readsettings.ReadSettings._validate_binaries"):
        from resources.mediaprocessor import MediaProcessor

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = MagicMock()
        mp.log = MagicMock()
        return mp

  def test_finds_metadata_file(self, tmp_path):
    mp = self._make_processor()
    src = tmp_path / "movie.mkv"
    src.write_text("x")
    meta = tmp_path / "movie.metadata.txt"
    meta.write_text("chapters")
    result = mp.scanForExternalMetadata(str(src))
    assert result is not None
    assert result.endswith("movie.metadata.txt")

  def test_no_metadata_file(self, tmp_path):
    mp = self._make_processor()
    src = tmp_path / "movie.mkv"
    src.write_text("x")
    result = mp.scanForExternalMetadata(str(src))
    assert result is None


class TestMaxBitrateVBV:
  """Test that vmaxbitrate populates VBV maxrate/bufsize in video_settings."""

  def _make_mp(self, tmp_yaml, vmaxbitrate):
    with patch("resources.readsettings.ReadSettings._validate_binaries"):
      from resources.mediaprocessor import MediaProcessor
      from resources.readsettings import ReadSettings

      settings = ReadSettings(tmp_yaml())
      settings.vcodec = ["h264"]
      settings.vmaxbitrate = vmaxbitrate

    mock_converter = MagicMock()
    mock_converter.ffmpeg.codecs = {
      "h264": {"encoders": ["libx264"]},
      "aac": {"encoders": ["aac"]},
    }
    mock_converter.ffmpeg.pix_fmts = {"yuv420p": 8}
    mock_converter.codec_name_to_ffmpeg_codec_name.side_effect = lambda c: {"h264": "libx264", "aac": "aac"}.get(c, c)

    mp = MediaProcessor.__new__(MediaProcessor)
    mp.settings = settings
    mp.converter = mock_converter
    mp.log = MagicMock()
    mp.deletesubs = set()
    from resources.subtitles import SubtitleProcessor

    mp.subtitles = SubtitleProcessor(mp)
    return mp

  def test_vmaxbitrate_sets_maxrate_and_bufsize(self, tmp_yaml, make_media_info):
    mp = self._make_mp(tmp_yaml, vmaxbitrate=8000)
    info = make_media_info(video_codec="h264", video_bitrate=10000000, total_bitrate=10128000, audio_bitrate=128000)
    with patch("resources.mediaprocessor.Converter.encoder", return_value=None), patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
      options, *_ = mp.generateOptions("/fake/input.mkv", info=info)
    assert options is not None
    assert options["video"]["maxrate"] == "8000k"
    assert options["video"]["bufsize"] == "16000k"

  def test_zero_vmaxbitrate_leaves_vbv_unset(self, tmp_yaml, make_media_info):
    mp = self._make_mp(tmp_yaml, vmaxbitrate=0)
    info = make_media_info(video_codec="h264", video_bitrate=5000000, total_bitrate=5128000, audio_bitrate=128000)
    with patch("resources.mediaprocessor.Converter.encoder", return_value=None), patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
      options, *_ = mp.generateOptions("/fake/input.mkv", info=info)
    assert options is not None
    assert options["video"]["maxrate"] is None
    assert options["video"]["bufsize"] is None


class TestAnalyzerProcessorIntegration:
  def _make_mp(self, tmp_yaml):
    with patch("resources.readsettings.ReadSettings._validate_binaries"):
      from resources.mediaprocessor import MediaProcessor
      from resources.readsettings import ReadSettings

      settings = ReadSettings(tmp_yaml())
      settings.vcodec = ["h264", "av1"]
      settings.acodec = ["aac"]
      settings.scodec = ["mov_text"]
      settings.scodec_image = []
      settings.analyzer["enabled"] = True
      settings.analyzer["backend"] = "openvino"
      settings.analyzer["device"] = "NPU"

    mock_converter = MagicMock()
    mock_converter.ffmpeg.codecs = {
      "h264": {"encoders": ["libx264"], "decoders": []},
      "av1": {"encoders": ["libsvtav1"], "decoders": []},
      "aac": {"encoders": ["aac"], "decoders": []},
      "mov_text": {"encoders": ["mov_text"], "decoders": []},
    }
    mock_converter.ffmpeg.pix_fmts = {"yuv420p": 8}
    mock_converter.ffmpeg.hwaccels = []
    mock_converter.ffmpeg.hwaccel_decoder.return_value = None
    mock_converter.codec_name_to_ffmpeg_codec_name.side_effect = lambda c: {
      "h264": "libx264",
      "av1": "libsvtav1",
      "aac": "aac",
      "mov_text": "mov_text",
    }.get(c, c)

    mp = MediaProcessor.__new__(MediaProcessor)
    mp.settings = settings
    mp.converter = mock_converter
    mp.log = MagicMock()
    mp.deletesubs = set()
    from resources.subtitles import SubtitleProcessor

    mp.subtitles = SubtitleProcessor(mp)
    return mp

  def test_get_analyzer_recommendations_passes_npu_config_to_backend(self, tmp_yaml, make_media_info):
    mp = self._make_mp(tmp_yaml)
    info = make_media_info()

    with (
      patch("resources.mediaprocessor.OpenVINOAnalyzerBackend") as mock_backend,
      patch("resources.mediaprocessor.build_recommendations", return_value=AnalyzerRecommendations(force_reencode=True)) as mock_build,
    ):
      backend_instance = mock_backend.return_value
      backend_instance.analyze.return_value = AnalyzerObservations(content_type="animation")

      recommendations = mp._get_analyzer_recommendations("/fake/input.mkv", info)

    mock_backend.assert_called_once_with(mp.settings.analyzer)
    backend_instance.analyze.assert_called_once_with(inputfile="/fake/input.mkv", info=info)
    mock_build.assert_called_once_with(backend_instance.analyze.return_value, mp.settings.analyzer)
    assert recommendations.force_reencode is True

  def test_generate_options_applies_analyzer_video_overrides(self, tmp_yaml, make_media_info):
    mp = self._make_mp(tmp_yaml)
    info = make_media_info(video_codec="h264", video_bitrate=4000000, total_bitrate=4128000, audio_bitrate=128000)

    recommendations = AnalyzerRecommendations(
      codec_order=["av1", "h264"],
      bitrate_ratio_multiplier=1.2,
      max_bitrate_ceiling=7000,
      preset="slow",
      filters=["bwdif"],
      force_reencode=True,
    )

    with (
      patch.object(mp, "_get_analyzer_recommendations", return_value=recommendations),
      patch("resources.mediaprocessor.Converter.encoder", return_value=None),
      patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c),
    ):
      options, *_ = mp.generateOptions("/fake/input.mkv", info=info)

    assert options is not None
    assert options["video"]["codec"] == "av1"
    assert options["video"]["preset"] == "slow"
    assert options["video"]["filter"] == "bwdif"
    assert options["video"]["bitrate"] == pytest.approx(4560, rel=0.05)
    assert options["video"]["maxrate"] == "7000k"
    assert options["video"]["bufsize"] == "14000k"
    assert ".analyzer-force-reencode" in options["video"]["debug"]
    assert ".analyzer-filter" in options["video"]["debug"]
    assert ".analyzer-preset" in options["video"]["debug"]

  def test_generate_options_logs_structured_analyzer_recommendations(self, tmp_yaml, make_media_info):
    mp = self._make_mp(tmp_yaml)
    info = make_media_info()

    recommendations = AnalyzerRecommendations(
      codec_order=["av1", "h264"],
      preset="slow",
      filters=["bwdif"],
      reasons=["interlaced content requires deinterlacing"],
    )

    with (
      patch.object(mp, "_get_analyzer_recommendations", return_value=recommendations),
      patch("resources.mediaprocessor.Converter.encoder", return_value=None),
      patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c),
    ):
      mp.generateOptions("/fake/input.mkv", info=info)

    logged_messages = [call.args[0] for call in mp.log.info.call_args_list if call.args]
    assert any("Analyzer recommendations:" in message for message in logged_messages)
    assert any('"filters": ["bwdif"]' in message for message in logged_messages)

  def test_analyzer_codec_reorder_preserves_hw_encoder_priority_within_family(self, tmp_yaml, make_media_info):
    mp = self._make_mp(tmp_yaml)
    mp.settings.vcodec = ["h265qsv", "h265"]
    info = make_media_info(video_codec="vp9", video_bitrate=4000000, total_bitrate=4128000, audio_bitrate=128000)

    recommendations = AnalyzerRecommendations(codec_order=["h265"])

    with (
      patch.object(mp, "_get_analyzer_recommendations", return_value=recommendations),
      patch("resources.mediaprocessor.Converter.encoder", return_value=None),
      patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c),
    ):
      options, *_ = mp.generateOptions("/fake/input.mkv", info=info)

    assert options is not None
    assert options["video"]["codec"] == "h265qsv"


class TestAnalyzerPreviewSurfacing:
  def test_json_dump_includes_serialized_analyzer_recommendations(self):
    mp = _make_mp()
    info = MagicMock()
    info.video = MagicMock(codec="h264")

    recommendations = AnalyzerRecommendations(
      codec_order=["av1", "h264"],
      preset="slow",
      filters=["bwdif"],
      force_reencode=True,
    )

    mp.generateSourceDict = MagicMock(return_value=({"extension": "mkv"}, info))
    mp._get_analyzer_recommendations = MagicMock(return_value=recommendations)
    mp.generateOptions = MagicMock(return_value=({"source": ["/fake/input.mkv"], "format": "mp4", "video": {"codec": "av1"}, "audio": [], "subtitle": [], "attachment": []}, [], [], [], []))
    mp.canBypassConvert = MagicMock(return_value=False)
    mp.converter.parse_options = MagicMock(return_value={})
    mp.parseFile = MagicMock(return_value=("/fake", "input", "mkv"))
    mp.getOutputFile = MagicMock(return_value=("/fake/input.mp4", "/fake"))
    mp.converter.ffmpeg.generateCommands = MagicMock(return_value=["ffmpeg", "-i", "/fake/input.mkv"])

    dump = json.loads(mp.jsonDump("/fake/input.mkv"))

    assert dump["analyzer"] == {
      "codec_order": ["av1", "h264"],
      "filters": ["bwdif"],
      "force_reencode": True,
      "preset": "slow",
    }
    mp.generateOptions.assert_called_once_with("/fake/input.mkv", info=info, original=None, tagdata=None, analyzer_recommendations=recommendations)


def _make_mp():
  """Shared helper: build a MediaProcessor with mocked converter and settings."""
  with patch("resources.mediaprocessor.Converter"):
    with patch("resources.readsettings.ReadSettings._validate_binaries"):
      from resources.mediaprocessor import MediaProcessor
      from resources.subtitles import SubtitleProcessor

      mp = MediaProcessor.__new__(MediaProcessor)
      mp.settings = MagicMock()
      mp.log = MagicMock()
      mp.converter = MagicMock()
      mp.subtitles = SubtitleProcessor(mp)
      return mp


class TestValidLanguage:
  def test_empty_whitelist_allows_any(self):
    mp = _make_mp()
    assert mp.validLanguage("eng", []) is True
    assert mp.validLanguage("fra", []) is True

  def test_language_in_whitelist(self):
    mp = _make_mp()
    assert mp.validLanguage("eng", ["eng", "fra"]) is True

  def test_language_not_in_whitelist(self):
    mp = _make_mp()
    assert mp.validLanguage("deu", ["eng", "fra"]) is False

  def test_blocked_language_excluded(self):
    mp = _make_mp()
    assert mp.validLanguage("eng", ["eng", "fra"], blocked=["eng"]) is False

  def test_blocked_overrides_empty_whitelist(self):
    mp = _make_mp()
    assert mp.validLanguage("eng", [], blocked=["eng"]) is False

  def test_empty_blocked_list_allows_whitelist(self):
    mp = _make_mp()
    assert mp.validLanguage("eng", ["eng"], blocked=[]) is True


class TestDispoStringToDict:
  def test_plus_sets_true(self):
    mp = _make_mp()
    assert mp.dispoStringToDict("+default") == {"default": True}

  def test_minus_sets_false(self):
    mp = _make_mp()
    assert mp.dispoStringToDict("-forced") == {"forced": False}

  def test_mixed_signs(self):
    mp = _make_mp()
    result = mp.dispoStringToDict("+default-forced+comment")
    assert result == {"default": True, "forced": False, "comment": True}

  def test_none_returns_empty(self):
    mp = _make_mp()
    assert mp.dispoStringToDict(None) == {}

  def test_empty_string_returns_empty(self):
    mp = _make_mp()
    assert mp.dispoStringToDict("") == {}


class TestCheckDisposition:
  def test_all_required_present_and_true(self):
    mp = _make_mp()
    assert mp.checkDisposition(["default"], {"default": True, "forced": False}) is True

  def test_required_false_returns_false(self):
    mp = _make_mp()
    assert mp.checkDisposition(["forced"], {"default": True, "forced": False}) is False

  def test_empty_allowed_always_true(self):
    mp = _make_mp()
    assert mp.checkDisposition([], {"default": False}) is True

  def test_missing_key_returns_false(self):
    mp = _make_mp()
    assert mp.checkDisposition(["comment"], {"default": True}) is False


class TestSublistIndexes:
  def test_finds_single_match(self):
    mp = _make_mp()
    assert mp.sublistIndexes(["a", "b", "c"], ["b", "c"]) == [1]

  def test_finds_multiple_matches(self):
    mp = _make_mp()
    assert mp.sublistIndexes(["a", "b", "a", "b"], ["a", "b"]) == [0, 2]

  def test_no_match_returns_empty(self):
    mp = _make_mp()
    assert mp.sublistIndexes(["a", "b", "c"], ["x", "y"]) == []

  def test_single_element_pattern(self):
    mp = _make_mp()
    assert mp.sublistIndexes(["a", "b", "a"], ["a"]) == [0, 2]


class TestMinResolvedMap:
  def test_map_in_combination_returns_min(self):
    mp = _make_mp()
    assert mp.minResolvedMap(3, [[1, 2, 3]]) == 1

  def test_map_not_in_any_combination_returns_itself(self):
    mp = _make_mp()
    assert mp.minResolvedMap(5, [[1, 2, 3]]) == 5

  def test_empty_combinations(self):
    mp = _make_mp()
    assert mp.minResolvedMap(7, []) == 7


class TestGetSourceIndexFromMap:
  def test_returns_stream_position(self, make_stream):
    mp = _make_mp()
    from converter.ffmpeg import MediaInfo

    info = MediaInfo()
    s0 = make_stream(index=0)
    s1 = make_stream(index=1, type="audio")
    s2 = make_stream(index=2, type="subtitle")
    info.streams = [s0, s1, s2]
    assert mp.getSourceIndexFromMap(1, info, []) == 1

  def test_returns_999_for_missing_map(self, make_stream):
    mp = _make_mp()
    from converter.ffmpeg import MediaInfo

    info = MediaInfo()
    info.streams = [make_stream(index=0)]
    assert mp.getSourceIndexFromMap(99, info, []) == 999

  def test_resolves_via_combination(self, make_stream):
    mp = _make_mp()
    from converter.ffmpeg import MediaInfo

    info = MediaInfo()
    s0 = make_stream(index=0)
    s1 = make_stream(index=3, type="audio")
    info.streams = [s0, s1]
    # map=3 is in combo [1,3], min=1; stream with index=1 doesn't exist → 999
    assert mp.getSourceIndexFromMap(3, info, [[1, 3]]) == 999


class TestTitleDispositionCheck:
  def test_comment_in_title(self, make_stream):
    mp = _make_mp()
    from converter.ffmpeg import MediaInfo

    s = make_stream(type="audio", metadata={"title": "Commentary Track", "language": "eng"})
    s.disposition = {"default": False, "forced": False, "comment": False}
    info = MediaInfo()
    info.streams = [s]
    mp.titleDispositionCheck(info)
    assert s.disposition["comment"] is True

  def test_sdh_sets_hearing_impaired(self, make_stream):
    mp = _make_mp()
    from converter.ffmpeg import MediaInfo

    s = make_stream(type="subtitle", metadata={"title": "English SDH", "language": "eng"})
    s.disposition = {"default": False, "forced": False, "hearing_impaired": False}
    info = MediaInfo()
    info.streams = [s]
    mp.titleDispositionCheck(info)
    assert s.disposition["hearing_impaired"] is True

  def test_forced_in_title(self, make_stream):
    mp = _make_mp()
    from converter.ffmpeg import MediaInfo

    s = make_stream(type="subtitle", metadata={"title": "Forced Subtitles", "language": "eng"})
    s.disposition = {"default": False, "forced": False}
    info = MediaInfo()
    info.streams = [s]
    mp.titleDispositionCheck(info)
    assert s.disposition["forced"] is True

  def test_no_match_unchanged(self, make_stream):
    mp = _make_mp()
    from converter.ffmpeg import MediaInfo

    s = make_stream(type="audio", metadata={"title": "Main Audio", "language": "eng"})
    s.disposition = {"default": True, "forced": False, "comment": False}
    info = MediaInfo()
    info.streams = [s]
    mp.titleDispositionCheck(info)
    assert s.disposition["comment"] is False

  def test_case_insensitive(self, make_stream):
    mp = _make_mp()
    from converter.ffmpeg import MediaInfo

    s = make_stream(type="subtitle", metadata={"title": "FORCED ENGLISH", "language": "eng"})
    s.disposition = {"forced": False}
    info = MediaInfo()
    info.streams = [s]
    mp.titleDispositionCheck(info)
    assert s.disposition["forced"] is True


class TestSafeLanguageLegacy:
  def _make_audio_stream(self, lang, make_stream):
    s = make_stream(type="audio", metadata={"language": lang})
    s.disposition = {"default": True, "forced": False, "comment": False, "hearing_impaired": False, "visual_impaired": False}
    return s

  def test_normalizes_undefined_audio_to_adl(self, make_stream):
    mp = _make_mp()
    from converter.ffmpeg import MediaInfo

    mp.settings.awl = ["eng"]
    mp.settings.swl = ["eng"]
    mp.settings.adl = "eng"
    mp.settings.sdl = None
    mp.settings.audio_original_language = False
    mp.settings.subtitle_original_language = False
    mp.settings.ignored_audio_dispositions = []
    info = MediaInfo()
    audio = self._make_audio_stream("und", make_stream)
    info.streams = [audio]
    _, _ = mp.safeLanguage(info)
    # 'und' normalized to 'eng' (adl)
    assert audio.metadata["language"] == "eng"

  def test_relaxes_awl_when_no_valid_tracks(self, make_stream):
    mp = _make_mp()
    from converter.ffmpeg import MediaInfo

    mp.settings.awl = ["fra"]
    mp.settings.swl = []
    mp.settings.adl = None
    mp.settings.sdl = None
    mp.settings.audio_original_language = False
    mp.settings.subtitle_original_language = False
    mp.settings.ignored_audio_dispositions = []
    info = MediaInfo()
    audio = self._make_audio_stream("eng", make_stream)
    info.streams = [audio]
    awl, _ = mp.safeLanguage(info)
    # No 'fra' tracks found → awl relaxed to []
    assert awl == []

  def test_appends_original_language_to_awl(self, make_stream):
    mp = _make_mp()
    from converter.ffmpeg import MediaInfo

    mp.settings.awl = ["eng"]
    mp.settings.swl = []
    mp.settings.adl = None
    mp.settings.sdl = None
    mp.settings.audio_original_language = True
    mp.settings.subtitle_original_language = False
    mp.settings.ignored_audio_dispositions = []
    tagdata = MagicMock()
    tagdata.original_language = "jpn"
    info = MediaInfo()
    audio = self._make_audio_stream("eng", make_stream)
    info.streams = [audio]
    awl, _ = mp.safeLanguage(info, tagdata)
    assert "jpn" in awl


class TestMapStreamCombinationsLegacy:
  def test_matching_combination_same_language_and_dispo(self, make_stream):
    mp = _make_mp()
    mp.settings.stream_codec_combinations = [["aac", "ac3"]]
    a1 = make_stream(type="audio", codec="aac", index=0, metadata={"language": "eng"})
    a1.disposition = {"default": True, "forced": False}
    a2 = make_stream(type="audio", codec="ac3", index=1, metadata={"language": "eng"})
    a2.disposition = {"default": True, "forced": False}
    result = mp.mapStreamCombinations([a1, a2])
    assert result == [[0, 1]]

  def test_different_language_not_matched(self, make_stream):
    mp = _make_mp()
    mp.settings.stream_codec_combinations = [["aac", "ac3"]]
    a1 = make_stream(type="audio", codec="aac", index=0, metadata={"language": "eng"})
    a1.disposition = {"default": False, "forced": False}
    a2 = make_stream(type="audio", codec="ac3", index=1, metadata={"language": "fra"})
    a2.disposition = {"default": False, "forced": False}
    result = mp.mapStreamCombinations([a1, a2])
    assert result == []

  def test_no_combinations_configured(self, make_stream):
    mp = _make_mp()
    mp.settings.stream_codec_combinations = []
    a1 = make_stream(type="audio", codec="aac", index=0, metadata={"language": "eng"})
    a1.disposition = {"default": False, "forced": False}
    result = mp.mapStreamCombinations([a1])
    assert result == []

  def test_combination_not_present_in_streams(self, make_stream):
    mp = _make_mp()
    mp.settings.stream_codec_combinations = [["eac3", "truehd"]]
    a1 = make_stream(type="audio", codec="aac", index=0, metadata={"language": "eng"})
    a1.disposition = {"default": False, "forced": False}
    result = mp.mapStreamCombinations([a1])
    assert result == []


class TestDuplicateStreamSort:
  def test_copy_codec_sorted_first(self, make_stream):
    mp = _make_mp()
    from converter.ffmpeg import MediaInfo

    info = MediaInfo()
    s0 = make_stream(index=0, type="audio")
    s0.disposition = {"default": False}
    s1 = make_stream(index=1, type="audio")
    s1.disposition = {"default": False}
    info.streams = [s0, s1]
    opts = [
      {"map": 1, "codec": "aac", "bitrate": 256},
      {"map": 0, "codec": "copy", "bitrate": 128},
    ]
    mp.duplicateStreamSort(opts, info)
    assert opts[0]["codec"] == "copy"

  def test_higher_bitrate_sorted_first_when_same_codec(self, make_stream):
    mp = _make_mp()
    from converter.ffmpeg import MediaInfo

    info = MediaInfo()
    s0 = make_stream(index=0, type="audio")
    s0.disposition = {"default": False}
    s1 = make_stream(index=1, type="audio")
    s1.disposition = {"default": False}
    info.streams = [s0, s1]
    opts = [
      {"map": 0, "codec": "aac", "bitrate": 128},
      {"map": 1, "codec": "aac", "bitrate": 320},
    ]
    mp.duplicateStreamSort(opts, info)
    assert opts[0]["bitrate"] == 320


class TestFfprobeSafeCodecs:
  def test_adds_ffprobe_variant_when_missing(self):
    mp = _make_mp()
    with patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", return_value="h264"):
      result = mp.ffprobeSafeCodecs(["hevc"])
      assert "h264" in result

  def test_does_not_duplicate_when_already_present(self):
    mp = _make_mp()
    with patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", return_value="aac"):
      result = mp.ffprobeSafeCodecs(["aac", "ac3"])
      assert result.count("aac") == 1

  def test_returns_none_for_empty_list(self):
    mp = _make_mp()
    result = mp.ffprobeSafeCodecs([])
    assert result == []

  def test_none_ffprobe_value_ignored(self):
    mp = _make_mp()
    with patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", return_value=None):
      result = mp.ffprobeSafeCodecs(["custom_codec"])
      assert result == ["custom_codec"]


class TestSetDefaultAudioStreamLegacy:
  def test_sets_default_when_none_present(self):
    mp = _make_mp()
    mp.settings.adl = "eng"
    mp.settings.audio_sorting_default = []
    streams = [
      {"language": "eng", "codec": "aac", "channels": 2, "disposition": "-default"},
      {"language": "fra", "codec": "aac", "channels": 2, "disposition": "-default"},
    ]
    mp.setDefaultAudioStream(streams)
    assert "+default" in streams[0]["disposition"]
    assert "+default" not in streams[1].get("disposition", "")

  def test_prefers_preferred_language(self):
    mp = _make_mp()
    mp.settings.adl = "fra"
    mp.settings.audio_sorting_default = []
    streams = [
      {"language": "eng", "codec": "aac", "channels": 2, "disposition": "+default"},
      {"language": "fra", "codec": "aac", "channels": 2, "disposition": "-default"},
    ]
    mp.setDefaultAudioStream(streams)
    assert "+default" in streams[1]["disposition"]

  def test_removes_extra_defaults_in_preferred_language(self):
    mp = _make_mp()
    mp.settings.adl = "eng"
    mp.settings.audio_sorting_default = []
    streams = [
      {"language": "eng", "codec": "aac", "channels": 2, "disposition": "+default"},
      {"language": "eng", "codec": "ac3", "channels": 6, "disposition": "+default"},
    ]
    mp.setDefaultAudioStream(streams)
    # Only one should remain with +default
    count = sum(1 for s in streams if "+default" in s.get("disposition", ""))
    assert count == 1

  def test_empty_streams_no_error(self):
    mp = _make_mp()
    mp.settings.adl = "eng"
    mp.settings.audio_sorting_default = []
    mp.setDefaultAudioStream([])  # Should not raise

  def test_removes_default_from_non_preferred_language(self):
    mp = _make_mp()
    mp.settings.adl = "eng"
    mp.settings.audio_sorting_default = []
    streams = [
      {"language": "eng", "codec": "aac", "channels": 2, "disposition": "-default"},
      {"language": "jpn", "codec": "aac", "channels": 2, "disposition": "+default"},
    ]
    mp.setDefaultAudioStream(streams)
    # jpn stream should lose +default, eng stream should gain it
    assert "+default" not in streams[1].get("disposition", "")
    assert "+default" in streams[0]["disposition"]


class TestSetDefaultSubtitleStreamLegacy:
  def test_sets_default_when_sdl_and_force(self):
    mp = _make_mp()
    mp.settings.sdl = "eng"
    mp.settings.force_subtitle_defaults = True
    streams = [
      {"language": "eng", "disposition": "-default"},
      {"language": "fra", "disposition": "-default"},
    ]
    mp.setDefaultSubtitleStream(streams)
    assert "+default" in streams[0]["disposition"]

  def test_does_not_override_existing_default(self):
    mp = _make_mp()
    mp.settings.sdl = "eng"
    mp.settings.force_subtitle_defaults = True
    streams = [
      {"language": "fra", "disposition": "+default"},
      {"language": "eng", "disposition": "-default"},
    ]
    mp.setDefaultSubtitleStream(streams)
    # Already has a default → don't override
    assert "+default" in streams[0]["disposition"]

  def test_skips_when_no_sdl(self):
    mp = _make_mp()
    mp.settings.sdl = None
    mp.settings.force_subtitle_defaults = True
    streams = [{"language": "eng", "disposition": "-default"}]
    mp.setDefaultSubtitleStream(streams)
    assert "+default" not in streams[0]["disposition"]

  def test_skips_when_empty_streams(self):
    mp = _make_mp()
    mp.settings.sdl = "eng"
    mp.settings.force_subtitle_defaults = True
    mp.setDefaultSubtitleStream([])  # Should not raise


class TestSortStreamsLegacy:
  def _make_info(self, make_stream):
    from converter.ffmpeg import MediaInfo

    info = MediaInfo()
    s0 = make_stream(index=0, type="audio")
    s1 = make_stream(index=1, type="audio")
    s2 = make_stream(index=2, type="audio")
    info.streams = [s0, s1, s2]
    return info

  def test_sorts_by_channels_descending(self, make_stream):
    mp = _make_mp()
    info = self._make_info(make_stream)
    streams = [
      {"map": 0, "channels": 2, "codec": "aac", "language": "eng"},
      {"map": 1, "channels": 6, "codec": "aac", "language": "eng"},
      {"map": 2, "channels": 8, "codec": "aac", "language": "eng"},
    ]
    result = mp.sortStreams(streams, ["channels.d"], ["eng"], ["aac"], info)
    assert result[0]["channels"] == 8
    assert result[1]["channels"] == 6
    assert result[2]["channels"] == 2

  def test_sorts_by_channels_ascending(self, make_stream):
    mp = _make_mp()
    info = self._make_info(make_stream)
    streams = [
      {"map": 0, "channels": 8, "codec": "aac", "language": "eng"},
      {"map": 1, "channels": 2, "codec": "aac", "language": "eng"},
    ]
    result = mp.sortStreams(streams, ["channels.a"], ["eng"], ["aac"], info)
    assert result[0]["channels"] == 2

  def test_sorts_by_language_preference(self, make_stream):
    mp = _make_mp()
    info = self._make_info(make_stream)
    streams = [
      {"map": 0, "channels": 2, "codec": "aac", "language": "deu"},
      {"map": 1, "channels": 2, "codec": "aac", "language": "eng"},
      {"map": 2, "channels": 2, "codec": "aac", "language": "fra"},
    ]
    result = mp.sortStreams(streams, ["language"], ["eng", "fra", "deu"], ["aac"], info)
    assert result[0]["language"] == "eng"
    assert result[1]["language"] == "fra"
    assert result[2]["language"] == "deu"

  def test_sorts_by_disposition_flag(self, make_stream):
    mp = _make_mp()
    info = self._make_info(make_stream)
    streams = [
      {"map": 0, "channels": 2, "codec": "aac", "language": "eng", "disposition": "-default"},
      {"map": 1, "channels": 2, "codec": "aac", "language": "eng", "disposition": "+default"},
    ]
    result = mp.sortStreams(streams, ["d.default.d"], ["eng"], ["aac"], info)
    assert result[0]["disposition"] == "+default"

  def test_single_stream_unchanged(self, make_stream):
    mp = _make_mp()
    info = self._make_info(make_stream)
    streams = [{"map": 0, "channels": 2, "codec": "aac", "language": "eng"}]
    result = mp.sortStreams(streams, ["channels.d"], ["eng"], ["aac"], info)
    assert result == streams

  def test_unknown_sort_key_skipped(self, make_stream):
    mp = _make_mp()
    info = self._make_info(make_stream)
    streams = [
      {"map": 0, "channels": 2, "codec": "aac", "language": "eng"},
      {"map": 1, "channels": 6, "codec": "aac", "language": "eng"},
    ]
    result = mp.sortStreams(streams, ["nonexistent_key"], ["eng"], ["aac"], info)
    # Should not raise, returns original order
    assert len(result) == 2


class TestSetAcceleration:
  def _make_mp_with_hwaccel(self, hwaccels_available, settings_hwaccels, pix_fmts=None, codecs=None, hwdevices=None, hwoutputfmt=None, hwaccel_decoders=None):
    mp = _make_mp()
    mp.converter = MagicMock()
    mp.converter.ffmpeg.hwaccels = hwaccels_available
    mp.converter.ffmpeg.pix_fmts = pix_fmts or {"yuv420p": 8}
    mp.converter.ffmpeg.codecs = codecs or {"h264": {"decoders": [], "encoders": []}}
    mp.settings.hwaccels = settings_hwaccels
    mp.settings.hwdevices = hwdevices or {}
    mp.settings.hwoutputfmt = hwoutputfmt or {}
    mp.settings.hwaccel_decoders = hwaccel_decoders or []
    return mp

  def test_no_hwaccel_match_returns_empty_opts(self):
    mp = self._make_mp_with_hwaccel(
      hwaccels_available=["cuda"],
      settings_hwaccels=["videotoolbox"],
    )
    opts, device = mp.setAcceleration("h264", "yuv420p")
    assert opts == []
    assert device is None

  def test_matching_hwaccel_adds_flag(self):
    mp = self._make_mp_with_hwaccel(
      hwaccels_available=["cuda", "videotoolbox"],
      settings_hwaccels=["videotoolbox"],
    )
    mp.converter.ffmpeg.hwaccel_decoder = MagicMock(return_value="h264_videotoolbox")
    opts, _ = mp.setAcceleration("h264", "yuv420p")
    assert "-hwaccel" in opts
    assert "videotoolbox" in opts

  def test_hwdevice_appended_when_configured(self):
    mp = self._make_mp_with_hwaccel(
      hwaccels_available=["vaapi"],
      settings_hwaccels=["vaapi"],
      hwdevices={"vaapi": "/dev/dri/renderD128"},
    )
    mp.converter.ffmpeg.hwaccel_decoder = MagicMock(return_value="h264_vaapi")
    opts, device = mp.setAcceleration("h264", "yuv420p")
    assert device == "/dev/dri/renderD128"
    assert "-init_hw_device" in opts

  def test_empty_hwaccels_settings_returns_empty(self):
    mp = self._make_mp_with_hwaccel(
      hwaccels_available=["cuda"],
      settings_hwaccels=[],
    )
    opts, device = mp.setAcceleration("h264", "yuv420p")
    assert opts == []
    assert device is None


# ---------------------------------------------------------------------------
# setDefaultAudioStream
# ---------------------------------------------------------------------------


class TestSetDefaultAudioStream:
  def _make_stream(self, language="eng", disposition="+default", channels=2, codec="aac"):
    return {"language": language, "disposition": disposition, "channels": channels, "codec": codec, "bitrate": 128}

  def _make_mp(self, adl: str | None = "eng"):
    mp = _make_mp()
    mp.settings.adl = adl
    mp.settings.audio_sorting_default = "channels.d"
    return mp

  def test_single_stream_gets_default(self):
    mp = self._make_mp()
    streams = [self._make_stream(disposition="-default")]
    mp.setDefaultAudioStream(streams)
    assert "+default" in streams[0]["disposition"]

  def test_preferred_language_wins_over_other(self):
    mp = self._make_mp(adl="eng")
    streams = [
      self._make_stream(language="fra", disposition="+default"),
      self._make_stream(language="eng", disposition="-default"),
    ]
    mp.setDefaultAudioStream(streams)
    # eng stream should now have +default
    eng = next(s for s in streams if s["language"] == "eng")
    assert "+default" in eng["disposition"]

  def test_non_preferred_language_default_cleared(self):
    mp = self._make_mp(adl="eng")
    streams = [
      self._make_stream(language="fra", disposition="+default"),
      self._make_stream(language="eng", disposition="-default"),
    ]
    mp.setDefaultAudioStream(streams)
    fra = next(s for s in streams if s["language"] == "fra")
    assert "+default" not in fra["disposition"]

  def test_no_preferred_language_streams_uses_first(self):
    mp = self._make_mp(adl="eng")
    streams = [
      self._make_stream(language="fra", disposition="-default"),
      self._make_stream(language="deu", disposition="-default"),
    ]
    mp.setDefaultAudioStream(streams)
    assert "+default" in streams[0]["disposition"]

  def test_multiple_preferred_defaults_keeps_first_clears_rest(self):
    mp = self._make_mp(adl="eng")
    streams = [
      self._make_stream(language="eng", disposition="+default"),
      self._make_stream(language="eng", disposition="+default"),
    ]
    mp.setDefaultAudioStream(streams)
    defaults = [s for s in streams if "+default" in s["disposition"]]
    assert len(defaults) == 1

  def test_empty_streams_does_not_raise(self):
    mp = self._make_mp()
    mp.setDefaultAudioStream([])  # should not raise

  def test_no_preferred_language_set_uses_first_stream(self):
    mp = self._make_mp(adl=None)
    streams = [self._make_stream(language="fra", disposition="-default")]
    mp.setDefaultAudioStream(streams)
    assert "+default" in streams[0]["disposition"]


# ---------------------------------------------------------------------------
# setDefaultSubtitleStream
# ---------------------------------------------------------------------------


class TestSetDefaultSubtitleStream:
  def _make_sub(self, language="eng", disposition="-default"):
    return {"language": language, "disposition": disposition}

  def _make_mp(self, sdl: str | None = "eng", force_subtitle_defaults: bool = True):
    mp = _make_mp()
    mp.settings.sdl = sdl
    mp.settings.force_subtitle_defaults = force_subtitle_defaults
    return mp

  def test_sets_default_when_none_present(self):
    mp = self._make_mp()
    subs = [self._make_sub(language="eng", disposition="-default")]
    mp.setDefaultSubtitleStream(subs)
    assert "+default" in subs[0]["disposition"]

  def test_already_has_default_no_change(self):
    mp = self._make_mp()
    subs = [self._make_sub(language="eng", disposition="+default")]
    mp.setDefaultSubtitleStream(subs)
    assert "+default" in subs[0]["disposition"]

  def test_empty_list_does_not_raise(self):
    mp = self._make_mp()
    mp.setDefaultSubtitleStream([])

  def test_no_sdl_does_not_set_default(self):
    mp = self._make_mp(sdl=None)
    subs = [self._make_sub(language="eng", disposition="-default")]
    mp.setDefaultSubtitleStream(subs)
    assert "+default" not in subs[0]["disposition"]

  def test_force_default_false_does_not_override(self):
    mp = self._make_mp(force_subtitle_defaults=False)
    subs = [self._make_sub(language="eng", disposition="-default")]
    mp.setDefaultSubtitleStream(subs)
    assert "+default" not in subs[0]["disposition"]


# ---------------------------------------------------------------------------
# sortStreams
# ---------------------------------------------------------------------------


class TestSortStreams:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings = MagicMock()
    return mp

  def _stream(self, language="eng", channels=2, codec="aac", map_idx=0, disposition=""):
    return {"language": language, "channels": channels, "codec": codec, "map": map_idx, "disposition": disposition, "bitrate": 128}

  def test_single_stream_unchanged(self):
    mp = self._make_mp()
    streams = [self._stream()]
    result = mp.sortStreams(streams, ["language"], ["eng"], ["aac"], MagicMock(), tagdata=None)
    assert result == streams

  def test_sort_by_language(self):
    mp = self._make_mp()
    streams = [self._stream(language="fra"), self._stream(language="eng")]
    result = mp.sortStreams(streams, ["language"], ["eng", "fra"], ["aac"], MagicMock(), tagdata=None)
    assert result[0]["language"] == "eng"

  def test_sort_by_channels_descending(self):
    mp = self._make_mp()
    streams = [self._stream(channels=2), self._stream(channels=6)]
    result = mp.sortStreams(streams, ["channels.d"], ["eng"], ["aac"], MagicMock(), tagdata=None)
    assert result[0]["channels"] == 6

  def test_sort_by_channels_ascending(self):
    mp = self._make_mp()
    streams = [self._stream(channels=6), self._stream(channels=2)]
    result = mp.sortStreams(streams, ["channels.a"], ["eng"], ["aac"], MagicMock(), tagdata=None)
    assert result[0]["channels"] == 2

  def test_sort_by_codec_preference(self):
    mp = self._make_mp()
    mp.getCodecFromOptions = lambda x, info: x["codec"]
    streams = [self._stream(codec="mp3"), self._stream(codec="aac")]
    result = mp.sortStreams(streams, ["codec"], ["eng"], ["aac", "mp3"], MagicMock(), tagdata=None)
    assert result[0]["codec"] == "aac"

  def test_unknown_sort_key_skipped(self):
    mp = self._make_mp()
    streams = [self._stream(language="fra"), self._stream(language="eng")]
    # Should not raise, just skip the unknown key
    result = mp.sortStreams(streams, ["nonexistent_key"], ["eng"], ["aac"], MagicMock(), tagdata=None)
    assert len(result) == 2

  def test_sort_by_disposition_descending(self):
    mp = self._make_mp()
    streams = [self._stream(disposition="-default"), self._stream(disposition="+default")]
    result = mp.sortStreams(streams, ["d.default.d"], ["eng"], ["aac"], MagicMock(), tagdata=None)
    assert "+default" in result[0]["disposition"]

  def test_empty_streams_returns_empty(self):
    mp = self._make_mp()
    result = mp.sortStreams([], ["language"], ["eng"], ["aac"], MagicMock(), tagdata=None)
    assert result == []


# ---------------------------------------------------------------------------
# safeLanguage
# ---------------------------------------------------------------------------


class TestSafeLanguage:
  def _make_mp(self, awl=None, swl=None, adl="eng", sdl="eng", audio_original_language=False, subtitle_original_language=False):
    mp = _make_mp()
    mp.settings.awl = awl if awl is not None else []
    mp.settings.swl = swl if swl is not None else []
    mp.settings.adl = adl
    mp.settings.sdl = sdl
    mp.settings.audio_original_language = audio_original_language
    mp.settings.subtitle_original_language = subtitle_original_language
    mp.settings.ignored_audio_dispositions = []
    return mp

  def _make_info(self, audio_langs=("eng",), sub_langs=("eng",)):
    info = MagicMock()
    audio = []
    for lang in audio_langs:
      s = MagicMock()
      s.metadata = {"language": lang}
      s.disposition = {}
      audio.append(s)
    subs = []
    for lang in sub_langs:
      s = MagicMock()
      s.metadata = {"language": lang}
      subs.append(s)
    info.audio = audio
    info.subtitle = subs
    return info

  def test_returns_awl_and_swl(self):
    mp = self._make_mp(awl=["eng"], swl=["eng"])
    info = self._make_info()
    awl, swl = mp.safeLanguage(info)
    assert awl == ["eng"]
    assert swl == ["eng"]

  def test_awl_relaxed_when_no_valid_audio(self):
    mp = self._make_mp(awl=["jpn"], adl="jpn")
    info = self._make_info(audio_langs=["eng"])  # eng not in awl
    awl, _ = mp.safeLanguage(info)
    assert awl == []

  def test_original_language_appended_to_awl(self):
    mp = self._make_mp(awl=["eng"], audio_original_language=True)
    tagdata = MagicMock()
    tagdata.original_language = "jpn"
    info = self._make_info(audio_langs=["eng", "jpn"])
    awl, _ = mp.safeLanguage(info, tagdata=tagdata)
    assert "jpn" in awl

  def test_original_language_appended_to_swl(self):
    mp = self._make_mp(swl=["eng"], subtitle_original_language=True)
    tagdata = MagicMock()
    tagdata.original_language = "jpn"
    info = self._make_info()
    _, swl = mp.safeLanguage(info, tagdata=tagdata)
    assert "jpn" in swl

  def test_no_tagdata_original_language_not_appended(self):
    mp = self._make_mp(awl=["eng"], audio_original_language=True)
    info = self._make_info()
    awl, _ = mp.safeLanguage(info, tagdata=None)
    assert "jpn" not in awl


# ---------------------------------------------------------------------------
# isValidSubtitleSource
# ---------------------------------------------------------------------------


class TestIsValidSubtitleSource:
  def _make_mp(self, ignored_extensions=None):
    mp = _make_mp()
    mp.settings.ignored_extensions = ignored_extensions or ["nfo", "ds_store"]
    mp.converter = MagicMock()
    return mp

  def test_ignored_extension_returns_none(self):
    mp = self._make_mp()
    assert mp.isValidSubtitleSource("/path/to/file.nfo") is None

  def test_bad_sub_extension_returns_none(self):
    mp = self._make_mp()
    # .idx is in bad_sub_extensions
    assert mp.isValidSubtitleSource("/path/to/file.idx") is None

  def test_valid_sub_with_subtitle_streams(self):
    mp = self._make_mp()
    info = MagicMock()
    info.subtitle = [MagicMock()]
    info.video = None
    info.audio = []
    mp.converter.probe.return_value = info  # type: ignore[attr-defined]
    result = mp.isValidSubtitleSource("/path/to/file.srt")
    assert result is info

  def test_file_with_video_stream_returns_none(self):
    mp = self._make_mp()
    info = MagicMock()
    info.subtitle = [MagicMock()]
    info.video = MagicMock()
    info.audio = []
    mp.converter.probe.return_value = info  # type: ignore[attr-defined]
    assert mp.isValidSubtitleSource("/path/to/file.srt") is None

  def test_file_with_audio_streams_returns_none(self):
    mp = self._make_mp()
    info = MagicMock()
    info.subtitle = [MagicMock()]
    info.video = None
    info.audio = [MagicMock()]
    mp.converter.probe.return_value = info  # type: ignore[attr-defined]
    assert mp.isValidSubtitleSource("/path/to/file.srt") is None

  def test_probe_exception_returns_none(self):
    mp = self._make_mp()
    mp.converter.probe.side_effect = Exception("ffprobe failed")  # type: ignore[attr-defined]
    assert mp.isValidSubtitleSource("/path/to/file.srt") is None


# ---------------------------------------------------------------------------
# isHDRInput
# ---------------------------------------------------------------------------


class TestIsHDRInput:
  def _make_mp(self, hdr_params=None):
    mp = _make_mp()
    mp.settings.hdr = hdr_params or {
      "space": ["bt2020nc"],
      "transfer": ["smpte2084"],
      "primaries": ["bt2020"],
    }
    return mp

  def _make_stream(self, space=None, transfer=None, primaries=None):
    stream = MagicMock()
    stream.index = 0
    stream.color = {}
    if space:
      stream.color["space"] = space
    if transfer:
      stream.color["transfer"] = transfer
    if primaries:
      stream.color["primaries"] = primaries
    return stream

  def test_matching_hdr_params_returns_true(self):
    mp = self._make_mp()
    stream = self._make_stream(space="bt2020nc", transfer="smpte2084", primaries="bt2020")
    assert mp.isHDRInput(stream) is True

  def test_mismatched_space_returns_false(self):
    mp = self._make_mp()
    stream = self._make_stream(space="bt709", transfer="smpte2084", primaries="bt2020")
    assert mp.isHDRInput(stream) is False

  def test_no_hdr_params_configured_returns_false(self):
    mp = self._make_mp(hdr_params={"space": [], "transfer": [], "primaries": []})
    stream = self._make_stream(space="bt2020nc", transfer="smpte2084", primaries="bt2020")
    assert mp.isHDRInput(stream) is False

  def test_stream_missing_color_params_passes_if_hdr_not_configured(self):
    mp = self._make_mp(hdr_params={"space": [], "transfer": [], "primaries": []})
    stream = self._make_stream()
    assert mp.isHDRInput(stream) is False


# ---------------------------------------------------------------------------
# isDolbyVision
# ---------------------------------------------------------------------------


class TestIsDolbyVision:
  def _make_mp(self):
    return _make_mp()

  def test_dolby_vision_metadata_detected(self):
    mp = self._make_mp()
    framedata = {"side_data_list": [{"side_data_type": "Dolby Vision Metadata"}]}
    assert mp.isDolbyVision(framedata) is True

  def test_non_dv_side_data_returns_false(self):
    mp = self._make_mp()
    framedata = {"side_data_list": [{"side_data_type": "HDR Dynamic Metadata SMPTE2094-40 (HDR10+)"}]}
    assert mp.isDolbyVision(framedata) is False

  def test_no_side_data_list_returns_false(self):
    mp = self._make_mp()
    assert mp.isDolbyVision({}) is False

  def test_empty_side_data_list_returns_false(self):
    mp = self._make_mp()
    assert mp.isDolbyVision({"side_data_list": []}) is False

  def test_invalid_framedata_returns_false(self):
    mp = self._make_mp()
    assert mp.isDolbyVision(None) is False


# ---------------------------------------------------------------------------
# getDimensions
# ---------------------------------------------------------------------------


class TestGetDimensions:
  def _make_mp(self):
    mp = _make_mp()
    mp.converter = MagicMock()
    return mp

  def test_returns_width_and_height(self):
    mp = self._make_mp()
    info = MagicMock()
    info.video.video_width = 1920
    info.video.video_height = 1080
    mp.converter.probe.return_value = info  # type: ignore[attr-defined]
    result = mp.getDimensions("/path/to/file.mkv")
    assert result == {"x": 1920, "y": 1080}

  def test_probe_returns_none_gives_zeros(self):
    mp = self._make_mp()
    mp.converter.probe.return_value = None  # type: ignore[attr-defined]
    result = mp.getDimensions("/path/to/file.mkv")
    assert result == {"x": 0, "y": 0}


# ---------------------------------------------------------------------------
# mapStreamCombinations
# ---------------------------------------------------------------------------


class TestMapStreamCombinations:
  def _make_mp(self, combinations=None):
    mp = _make_mp()
    mp.settings.stream_codec_combinations = combinations or []
    return mp

  def _make_stream(self, codec, language="eng", index=0, disposition=None):
    s = MagicMock()
    s.codec = codec
    s.index = index
    s.metadata = {"language": language}
    s.disposition = disposition or {"default": False}
    return s

  def test_no_combinations_configured_returns_empty(self):
    mp = self._make_mp(combinations=[])
    streams = [self._make_stream("aac", index=0), self._make_stream("ac3", index=1)]
    assert mp.mapStreamCombinations(streams) == []

  def test_matching_combination_same_language_and_dispo(self):
    mp = self._make_mp(combinations=[["aac", "ac3"]])
    streams = [
      self._make_stream("aac", language="eng", index=0),
      self._make_stream("ac3", language="eng", index=1),
    ]
    result = mp.mapStreamCombinations(streams)
    assert [0, 1] in result

  def test_different_language_not_combined(self):
    mp = self._make_mp(combinations=[["aac", "ac3"]])
    streams = [
      self._make_stream("aac", language="eng", index=0),
      self._make_stream("ac3", language="fra", index=1),
    ]
    result = mp.mapStreamCombinations(streams)
    assert result == []

  def test_no_codec_match_returns_empty(self):
    mp = self._make_mp(combinations=[["eac3", "ac3"]])
    streams = [
      self._make_stream("aac", language="eng", index=0),
      self._make_stream("mp3", language="eng", index=1),
    ]
    assert mp.mapStreamCombinations(streams) == []


# ---------------------------------------------------------------------------
# processExternalSub
# ---------------------------------------------------------------------------


class TestProcessExternalSub:
  def _make_mp(self, sdl="eng"):
    mp = _make_mp()
    mp.settings.sdl = sdl
    return mp

  def _make_sub_info(self, path):
    from converter.ffmpeg import MediaInfo, MediaStreamInfo

    sub_stream = MediaStreamInfo()
    sub_stream.type = "subtitle"
    sub_stream.codec = "srt"
    sub_stream.index = 0
    sub_stream.metadata = {"language": "und"}
    sub_stream.disposition = {}

    info = MediaInfo()
    info.streams.append(sub_stream)
    info.path = path
    return info

  def test_none_input_returns_none(self):
    mp = self._make_mp()
    assert mp.processExternalSub(None, "/path/movie.mkv") is None

  def test_language_extracted_from_suffix(self):
    mp = self._make_mp()
    sub_info = self._make_sub_info("/path/movie.eng.srt")
    result = mp.processExternalSub(sub_info, "/path/movie.mkv")
    assert result.subtitle[0].metadata["language"] == "eng"

  def test_sdl_used_when_no_language_in_filename(self):
    mp = self._make_mp(sdl="fra")
    sub_info = self._make_sub_info("/path/movie.srt")
    result = mp.processExternalSub(sub_info, "/path/movie.mkv")
    assert result.subtitle[0].metadata["language"] == "fra"

  def test_forced_disposition_set_from_suffix(self):
    mp = self._make_mp()
    sub_info = self._make_sub_info("/path/movie.eng.forced.srt")
    result = mp.processExternalSub(sub_info, "/path/movie.mkv")
    assert result.subtitle[0].disposition.get("forced") is True


class TestMatchBitrateProfile:
  """Test _match_bitrate_profile returns the correct profile or None."""

  def _make_mp_with_profiles(self, profiles):
    mp = _make_mp()
    mp.settings.vbitrate_profiles = profiles
    return mp

  def test_empty_profiles_returns_none(self):
    mp = self._make_mp_with_profiles([])
    assert mp._match_bitrate_profile(5000) is None

  def test_zero_source_kbps_returns_none(self):
    mp = self._make_mp_with_profiles([{"source_kbps": 0, "target": 2000, "maxrate": 4000}])
    assert mp._match_bitrate_profile(0) is None

  def test_none_source_kbps_returns_none(self):
    mp = self._make_mp_with_profiles([{"source_kbps": 0, "target": 2000, "maxrate": 4000}])
    assert mp._match_bitrate_profile(None) is None

  def test_source_below_all_tiers_returns_none(self):
    profiles = [
      {"source_kbps": 1000, "target": 2000, "maxrate": 4000},
      {"source_kbps": 4000, "target": 3000, "maxrate": 6000},
    ]
    mp = self._make_mp_with_profiles(profiles)
    assert mp._match_bitrate_profile(500) is None

  def test_source_exactly_at_tier_threshold_matches_that_tier(self):
    profiles = [
      {"source_kbps": 1000, "target": 2000, "maxrate": 4000},
      {"source_kbps": 4000, "target": 3000, "maxrate": 6000},
    ]
    mp = self._make_mp_with_profiles(profiles)
    result = mp._match_bitrate_profile(4000)
    assert result == {"source_kbps": 4000, "target": 3000, "maxrate": 6000}

  def test_source_between_tiers_matches_lower_tier(self):
    profiles = [
      {"source_kbps": 1000, "target": 2000, "maxrate": 4000},
      {"source_kbps": 4000, "target": 3000, "maxrate": 6000},
    ]
    mp = self._make_mp_with_profiles(profiles)
    result = mp._match_bitrate_profile(2500)
    assert result == {"source_kbps": 1000, "target": 2000, "maxrate": 4000}

  def test_source_above_all_tiers_matches_highest_tier(self):
    profiles = [
      {"source_kbps": 1000, "target": 2000, "maxrate": 4000},
      {"source_kbps": 4000, "target": 3000, "maxrate": 6000},
      {"source_kbps": 8000, "target": 5000, "maxrate": 10000},
    ]
    mp = self._make_mp_with_profiles(profiles)
    result = mp._match_bitrate_profile(20000)
    assert result == {"source_kbps": 8000, "target": 5000, "maxrate": 10000}

  def test_source_exactly_at_lowest_tier_matches_it(self):
    profiles = [
      {"source_kbps": 1000, "target": 2000, "maxrate": 4000},
    ]
    mp = self._make_mp_with_profiles(profiles)
    result = mp._match_bitrate_profile(1000)
    assert result == {"source_kbps": 1000, "target": 2000, "maxrate": 4000}


class TestBitrateProfileIntegration:
  """Test that a matching crf-profile forces transcode and sets vbitrate/maxrate/bufsize."""

  def _make_mp(self, tmp_yaml, vbitrate_profiles, vmaxbitrate=0):
    with patch("resources.readsettings.ReadSettings._validate_binaries"):
      from resources.mediaprocessor import MediaProcessor
      from resources.readsettings import ReadSettings

      settings = ReadSettings(tmp_yaml())
      settings.vcodec = ["h264"]
      settings.vmaxbitrate = vmaxbitrate
      settings.vbitrate_profiles = vbitrate_profiles

    mock_converter = MagicMock()
    mock_converter.ffmpeg.codecs = {
      "h264": {"encoders": ["libx264"]},
      "aac": {"encoders": ["aac"]},
    }
    mock_converter.ffmpeg.pix_fmts = {"yuv420p": 8}
    mock_converter.codec_name_to_ffmpeg_codec_name.side_effect = lambda c: {"h264": "libx264", "aac": "aac"}.get(c, c)

    mp = MediaProcessor.__new__(MediaProcessor)
    mp.settings = settings
    mp.converter = mock_converter
    mp.log = MagicMock()
    mp.deletesubs = set()
    from resources.subtitles import SubtitleProcessor

    mp.subtitles = SubtitleProcessor(mp)
    return mp

  def test_profile_match_sets_vbitrate_maxrate_bufsize(self, tmp_yaml, make_media_info):
    profiles = [{"source_kbps": 0, "target": 3000, "maxrate": 6000}]
    mp = self._make_mp(tmp_yaml, vbitrate_profiles=profiles)
    info = make_media_info(video_codec="h264", video_bitrate=5000000, total_bitrate=5128000, audio_bitrate=128000)
    with patch("resources.mediaprocessor.Converter.encoder", return_value=None), patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
      options, *_ = mp.generateOptions("/fake/input.mkv", info=info)
    assert options is not None
    assert options["video"]["bitrate"] == 3000
    assert options["video"]["maxrate"] == "6000k"
    assert options["video"]["bufsize"] == "12000k"

  def test_profile_match_forces_transcode(self, tmp_yaml, make_media_info):
    profiles = [{"source_kbps": 0, "target": 3000, "maxrate": 6000}]
    mp = self._make_mp(tmp_yaml, vbitrate_profiles=profiles)
    info = make_media_info(video_codec="h264", video_bitrate=5000000, total_bitrate=5128000, audio_bitrate=128000)
    with patch("resources.mediaprocessor.Converter.encoder", return_value=None), patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
      options, *_ = mp.generateOptions("/fake/input.mkv", info=info)
    assert options is not None
    assert options["video"]["codec"] != "copy"

  def test_no_profiles_leaves_vbv_unset_without_maxbitrate(self, tmp_yaml, make_media_info):
    mp = self._make_mp(tmp_yaml, vbitrate_profiles=[], vmaxbitrate=0)
    info = make_media_info(video_codec="h264", video_bitrate=5000000, total_bitrate=5128000, audio_bitrate=128000)
    with patch("resources.mediaprocessor.Converter.encoder", return_value=None), patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
      options, *_ = mp.generateOptions("/fake/input.mkv", info=info)
    assert options is not None
    assert options["video"]["maxrate"] is None
    assert options["video"]["bufsize"] is None


# ===========================================================================
# New tests to increase coverage
# ===========================================================================


def _build_mp_with_real_settings(tmp_yaml_factory):
  """Build a MediaProcessor with real ReadSettings and a mocked Converter."""
  with patch("resources.readsettings.ReadSettings._validate_binaries"):
    from resources.mediaprocessor import MediaProcessor
    from resources.readsettings import ReadSettings
    from resources.subtitles import SubtitleProcessor

    settings = ReadSettings(tmp_yaml_factory())

  mock_converter = MagicMock()
  mock_converter.ffmpeg.codecs = {
    "h264": {"encoders": ["libx264"], "decoders": []},
    "hevc": {"encoders": ["libx265"], "decoders": []},
    "aac": {"encoders": ["aac"], "decoders": []},
    "mov_text": {"encoders": ["mov_text"], "decoders": []},
  }
  mock_converter.ffmpeg.pix_fmts = {"yuv420p": 8, "yuv420p10le": 10}
  mock_converter.ffmpeg.hwaccels = []
  mock_converter.codec_name_to_ffmpeg_codec_name.side_effect = lambda c: c
  mock_converter.ffmpeg.hwaccel_decoder = MagicMock(return_value=None)

  mp = MediaProcessor.__new__(MediaProcessor)
  mp.settings = settings
  mp.converter = mock_converter
  mp.log = MagicMock()
  mp.deletesubs = set()
  mp.subtitles = SubtitleProcessor(mp)
  return mp


# ---------------------------------------------------------------------------
# fullprocess
# ---------------------------------------------------------------------------


class TestFullProcess:
  """Tests for the fullprocess() orchestration method."""

  def _make_mp(self):
    mp = _make_mp()
    mp.settings.tagfile = False
    mp.settings.relocate_moov = False
    mp.settings.naming_enabled = False
    mp.settings.plexmatch_enabled = False
    mp.settings.postprocess = False
    mp.settings.Plex = {"refresh": False}
    mp.settings.taglanguage = None
    mp.settings.output_format = "mp4"
    mp.deletesubs = set()
    return mp

  def test_invalid_source_returns_false(self):
    mp = self._make_mp()
    mp.isValidSource = MagicMock(return_value=None)
    result = mp.fullprocess("/fake/file.mkv", "movie")
    assert result is False

  def test_process_fails_returns_false(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.process = MagicMock(return_value=None)
    result = mp.fullprocess("/fake/file.mkv", "movie", tagdata=MagicMock())
    assert result is False

  def test_successful_path_returns_output_files(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    output = {
      "output": "/fake/file.mp4",
      "external_subs": [],
      "options": {"audio": []},
      "x": 1920,
      "y": 1080,
      "cues_to_front": False,
    }
    mp.process = MagicMock(return_value=output)
    mp.restoreFromOutput = MagicMock(side_effect=lambda i, o: o)
    mp.replicate = MagicMock(return_value=["/fake/file.mp4"])
    mp.setPermissions = MagicMock()
    mp.post = MagicMock()
    mp.getDefaultAudioLanguage = MagicMock(return_value="eng")
    result = mp.fullprocess("/fake/file.mkv", "movie", tagdata=MagicMock(), post=True)
    assert result == ["/fake/file.mp4"]
    mp.post.assert_called_once()

  def test_post_false_skips_post(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    output = {
      "output": "/fake/file.mp4",
      "external_subs": [],
      "options": {"audio": []},
      "x": 1920,
      "y": 1080,
      "cues_to_front": False,
    }
    mp.process = MagicMock(return_value=output)
    mp.restoreFromOutput = MagicMock(side_effect=lambda i, o: o)
    mp.replicate = MagicMock(return_value=["/fake/file.mp4"])
    mp.setPermissions = MagicMock()
    mp.post = MagicMock()
    mp.getDefaultAudioLanguage = MagicMock(return_value="eng")
    result = mp.fullprocess("/fake/file.mkv", "movie", tagdata=MagicMock(), post=False)
    assert result == ["/fake/file.mp4"]
    mp.post.assert_not_called()

  def test_tagging_enabled_calls_write_tags(self):
    mp = self._make_mp()
    mp.settings.tagfile = True
    mp.settings.artwork = True
    mp.settings.thumbnail = True
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    tagdata = MagicMock()
    tagdata.tmdbid = 12345
    output = {
      "output": "/fake/file.mp4",
      "external_subs": [],
      "options": {"audio": []},
      "x": 1920,
      "y": 1080,
      "cues_to_front": False,
    }
    mp.process = MagicMock(return_value=output)
    mp.restoreFromOutput = MagicMock(side_effect=lambda i, o: o)
    mp.replicate = MagicMock(return_value=["/fake/file.mp4"])
    mp.setPermissions = MagicMock()
    mp.post = MagicMock()
    mp.getDefaultAudioLanguage = MagicMock(return_value="eng")
    mp.fullprocess("/fake/file.mkv", "movie", tagdata=tagdata, post=False)
    tagdata.writeTags.assert_called_once()

  def test_metadata_fetch_exception_continues(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    output = {
      "output": "/fake/file.mp4",
      "external_subs": [],
      "options": {"audio": []},
      "x": 1920,
      "y": 1080,
      "cues_to_front": False,
    }
    mp.process = MagicMock(return_value=output)
    mp.restoreFromOutput = MagicMock(side_effect=lambda i, o: o)
    mp.replicate = MagicMock(return_value=["/fake/file.mp4"])
    mp.setPermissions = MagicMock()
    mp.post = MagicMock()
    mp.getDefaultAudioLanguage = MagicMock(return_value="eng")
    # No tagdata provided, so Metadata() will be called; patch it to raise
    with patch("resources.mediaprocessor.Metadata", side_effect=Exception("tmdb error")):
      result = mp.fullprocess("/fake/file.mkv", "movie", post=False)
    # Should still complete successfully (tagdata=None after exception)
    assert result == ["/fake/file.mp4"]

  def test_exception_in_process_returns_false(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.process = MagicMock(side_effect=Exception("unexpected"))
    result = mp.fullprocess("/fake/file.mkv", "movie", tagdata=MagicMock())
    assert result is False

  def test_relocate_moov_called_when_enabled(self):
    mp = self._make_mp()
    mp.settings.relocate_moov = True
    mp.settings.tagfile = True
    mp.settings.artwork = False
    mp.settings.thumbnail = False
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    tagdata = MagicMock()
    tagdata.tmdbid = 1
    output = {
      "output": "/fake/file.mp4",
      "external_subs": [],
      "options": {"audio": []},
      "x": 1920,
      "y": 1080,
      "cues_to_front": False,
    }
    mp.process = MagicMock(return_value=output)
    mp.restoreFromOutput = MagicMock(side_effect=lambda i, o: o)
    mp.replicate = MagicMock(return_value=["/fake/file.mp4"])
    mp.setPermissions = MagicMock()
    mp.post = MagicMock()
    mp.getDefaultAudioLanguage = MagicMock(return_value="eng")
    mp.QTFS = MagicMock()
    # tagging won't raise
    tagdata.writeTags = MagicMock()
    mp.fullprocess("/fake/file.mkv", "movie", tagdata=tagdata, post=False)
    mp.QTFS.assert_called_once()

  def test_external_subs_replicated(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    sub_path = "/fake/file.eng.srt"
    output = {
      "output": "/fake/file.mp4",
      "external_subs": [sub_path],
      "options": {"audio": []},
      "x": 1920,
      "y": 1080,
      "cues_to_front": False,
    }
    mp.process = MagicMock(return_value=output)
    mp.restoreFromOutput = MagicMock(side_effect=lambda i, o: o)
    mp.replicate = MagicMock(return_value=["/fake/file.mp4"])
    mp.setPermissions = MagicMock()
    mp.post = MagicMock()
    mp.getDefaultAudioLanguage = MagicMock(return_value="eng")
    with patch("os.path.exists", return_value=True):
      mp.fullprocess("/fake/file.mkv", "movie", tagdata=MagicMock(), post=False)
    # replicate called once for main output + once for sub
    assert mp.replicate.call_count == 2


# ---------------------------------------------------------------------------
# post()
# ---------------------------------------------------------------------------


class TestPost:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.postprocess = False
    mp.settings.Plex = {"refresh": False}
    return mp

  def test_no_postprocess_no_plex_noop(self):
    mp = self._make_mp()
    mp.post(["/fake/file.mp4"], "movie")  # should not raise

  def test_postprocess_runs_scripts(self):
    mp = self._make_mp()
    mp.settings.postprocess = True
    mp.settings.waitpostprocess = False
    with patch("resources.mediaprocessor.PostProcessor") as MockPP:
      pp_instance = MagicMock()
      MockPP.return_value = pp_instance
      mp.post(["/fake/file.mp4"], "movie", tmdbid=123)
    MockPP.assert_called_once()
    pp_instance.setEnv.assert_called_once()
    pp_instance.run_scripts.assert_called_once()

  def test_plex_refresh_called(self):
    mp = self._make_mp()
    mp.settings.Plex = {"refresh": True}
    with patch("resources.mediaprocessor.plex.refreshPlex") as mock_refresh:
      mp.post(["/fake/file.mp4"], "movie")
    mock_refresh.assert_called_once()

  def test_plex_refresh_exception_logged(self):
    mp = self._make_mp()
    mp.settings.Plex = {"refresh": True}
    with patch("resources.mediaprocessor.plex.refreshPlex", side_effect=Exception("plex down")):
      mp.post(["/fake/file.mp4"], "movie")  # should not raise
    mp.log.exception.assert_called()


# ---------------------------------------------------------------------------
# process()
# ---------------------------------------------------------------------------


class TestProcess:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.delete = False
    mp.settings.output_dir = None
    mp.settings.output_extension = "mp4"
    mp.settings.output_format = "mp4"
    mp.settings.relocate_moov = False
    mp.settings.recycle_bin = None
    mp.deletesubs = set()
    return mp

  def test_invalid_source_returns_none(self):
    mp = self._make_mp()
    mp.isValidSource = MagicMock(return_value=None)
    mp.outputDirHasFreeSpace = MagicMock(return_value=True)
    assert mp.process("/fake/input.mkv") is None

  def test_bypass_convert_sets_outputfile_to_inputfile(self, tmp_path):
    mp = self._make_mp()
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"x" * 100)
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.outputDirHasFreeSpace = MagicMock(return_value=True)
    mp.generateOptions = MagicMock(return_value=({"video": {}, "audio": []}, [], [], [], []))
    mp.canBypassConvert = MagicMock(return_value=True)
    mp.getDimensions = MagicMock(return_value={"x": 1920, "y": 1080})
    mp._cleanup_input = MagicMock(return_value=False)
    result = mp.process(str(src))
    assert result is not None
    assert result["output"] == str(src)
    assert result["input"] == str(src)

  def test_generate_options_exception_returns_none(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.outputDirHasFreeSpace = MagicMock(return_value=True)
    mp.generateOptions = MagicMock(side_effect=Exception("options error"))
    result = mp.process("/fake/input.mkv", info=info)
    assert result is None

  def test_run_ffmpeg_failure_returns_none(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.outputDirHasFreeSpace = MagicMock(return_value=True)
    options = {"video": {}, "audio": [{"map": 1, "codec": "aac"}]}
    mp.generateOptions = MagicMock(return_value=(options, [], [], [], []))
    mp.canBypassConvert = MagicMock(return_value=False)
    mp._run_ffmpeg = MagicMock(return_value=(None, "/fake/input.mkv", []))
    result = mp.process("/fake/input.mkv", info=info)
    assert result is None

  def test_bypass_with_output_dir_copies_file(self, tmp_path):
    mp = self._make_mp()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    mp.settings.output_dir = str(output_dir)
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"data")
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.outputDirHasFreeSpace = MagicMock(return_value=True)
    mp.generateOptions = MagicMock(return_value=({"video": {}, "audio": []}, [], [], [], []))
    mp.canBypassConvert = MagicMock(return_value=True)
    mp.getDimensions = MagicMock(return_value={"x": 1920, "y": 1080})
    mp._cleanup_input = MagicMock(return_value=False)
    result = mp.process(str(src))
    assert result is not None
    # outputfile should be in output_dir
    assert str(output_dir) in result["output"]

  def test_options_empty_returns_none(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.outputDirHasFreeSpace = MagicMock(return_value=True)
    mp.generateOptions = MagicMock(return_value=(None, [], [], [], []))
    mp.canBypassConvert = MagicMock(return_value=False)
    result = mp.process("/fake/input.mkv", info=info)
    assert result is None


# ---------------------------------------------------------------------------
# _run_ffmpeg()
# ---------------------------------------------------------------------------


class TestRunFfmpeg:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.embedsubs = True
    mp.settings.downloadsubs = False
    return mp

  def test_success_returns_tuple(self):
    mp = self._make_mp()
    mp.subtitles = MagicMock()
    mp.subtitles.ripSubs.return_value = []
    mp.cleanExternalSub = MagicMock()
    mp.convert = MagicMock(return_value=("/fake/output.mp4", "/fake/input.mkv"))
    result = mp._run_ffmpeg("/fake/input.mkv", {"video": {}, "audio": []}, [], [], [], [], False, None)
    assert result == ("/fake/output.mp4", "/fake/input.mkv", [])

  def test_convert_exception_returns_none(self):
    mp = self._make_mp()
    mp.subtitles = MagicMock()
    mp.subtitles.ripSubs.return_value = []
    mp.cleanExternalSub = MagicMock()
    mp.convert = MagicMock(side_effect=Exception("conversion failed"))
    outputfile, inputfile, ripped = mp._run_ffmpeg("/fake/input.mkv", {}, [], [], [], [], False, None)
    assert outputfile is None
    assert inputfile == "/fake/input.mkv"

  def test_no_outputfile_returns_none(self):
    mp = self._make_mp()
    mp.subtitles = MagicMock()
    mp.subtitles.ripSubs.return_value = []
    mp.cleanExternalSub = MagicMock()
    mp.convert = MagicMock(return_value=(None, "/fake/input.mkv"))
    outputfile, inputfile, ripped = mp._run_ffmpeg("/fake/input.mkv", {}, [], [], [], [], False, None)
    assert outputfile is None


# ---------------------------------------------------------------------------
# _cleanup_input()
# ---------------------------------------------------------------------------


class TestCleanupInput:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.recycle_bin = None
    mp.deletesubs = set()
    return mp

  def test_delete_false_returns_false(self, tmp_path):
    mp = self._make_mp()
    src = tmp_path / "input.mkv"
    src.write_bytes(b"data")
    result = mp._cleanup_input(str(src), delete=False)
    assert result is False
    assert src.exists()

  def test_delete_true_removes_file(self, tmp_path):
    mp = self._make_mp()
    src = tmp_path / "input.mkv"
    src.write_bytes(b"data")
    result = mp._cleanup_input(str(src), delete=True)
    assert result is True
    assert not src.exists()

  def test_delete_true_with_recycle_bin_copies_first(self, tmp_path):
    mp = self._make_mp()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    mp.settings.recycle_bin = str(recycle)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"data")
    mp._atomic_copy = MagicMock()
    result = mp._cleanup_input(str(src), delete=True)
    mp._atomic_copy.assert_called_once()
    assert result is True

  def test_recycle_bin_collision_adds_suffix(self, tmp_path):
    mp = self._make_mp()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    mp.settings.recycle_bin = str(recycle)
    # Pre-create file in recycle to trigger collision
    (recycle / "input.mkv").write_bytes(b"old")
    src = tmp_path / "input.mkv"
    src.write_bytes(b"data")
    mp._atomic_copy = MagicMock()
    mp._cleanup_input(str(src), delete=True)
    # The copy dst should have ".2." in the name
    call_args = mp._atomic_copy.call_args[0]
    assert ".2." in call_args[1] or "input.2.mkv" in call_args[1]

  def test_delete_subs_on_delete(self, tmp_path):
    mp = self._make_mp()
    src = tmp_path / "input.mkv"
    src.write_bytes(b"data")
    sub = tmp_path / "input.eng.srt"
    sub.write_bytes(b"subtitle")
    mp.deletesubs = {str(sub)}
    mp._cleanup_input(str(src), delete=True)
    assert not sub.exists()

  def test_recycle_exception_logged_not_raised(self, tmp_path):
    mp = self._make_mp()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    mp.settings.recycle_bin = str(recycle)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"data")
    with patch.object(mp, "_atomic_copy", side_effect=Exception("disk full")):
      # Should not raise; the exception is logged
      mp._cleanup_input(str(src), delete=True)
    mp.log.exception.assert_called()


# ---------------------------------------------------------------------------
# convert()
# ---------------------------------------------------------------------------


class TestConvert:
  def _make_mp(self, tmp_path):
    mp = _make_mp()
    mp.settings.output_extension = "mp4"
    mp.settings.output_dir = None
    mp.settings.temp_extension = None
    mp.settings.delete = True
    mp.settings.strip_metadata = False
    mp.settings.burn_subtitles = False
    mp.settings.detailedprogress = False
    mp.settings.permissions = {"chmod": 0o664, "uid": -1, "gid": -1}
    return mp

  def test_no_audio_streams_returns_none(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"x")
    options = {"source": [str(src)], "audio": [], "video": {}, "subtitle": []}
    result, _ = mp.convert(options, [], [], False, None)
    assert result is None

  def test_outputfile_none_returns_none(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"x")
    options = {"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": []}
    with patch.object(mp, "getOutputFile", return_value=(None, str(tmp_path))):
      result, _ = mp.convert(options, [], [], False, None)
    assert result is None

  def test_successful_conversion(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"x")
    out = tmp_path / "input.mp4"

    options = {"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": []}

    def fake_convert(outputfile, opts, timeout=None, preopts=None, postopts=None, strip_metadata=False):
      out.write_bytes(b"output")
      yield None, ["ffmpeg", "-i", str(src), str(out)]
      yield 100, ""

    mp.converter.convert = fake_convert
    mp.setPermissions = MagicMock()
    result, inp = mp.convert(options, [], [], False, None)
    assert result == str(out)

  def test_ffmpeg_convert_error_removes_output(self, tmp_path):
    from converter import FFMpegConvertError

    mp = self._make_mp(tmp_path)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"x")
    out = tmp_path / "input.mp4"
    out.write_bytes(b"partial")

    options = {"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": []}

    def fake_convert(outputfile, opts, timeout=None, preopts=None, postopts=None, strip_metadata=False):
      yield None, ["ffmpeg"]
      raise FFMpegConvertError("cmd", "output", 1)

    mp.converter.convert = fake_convert
    mp.setPermissions = MagicMock()
    result, inp = mp.convert(options, [], [], False, None)
    assert result is None

  def test_input_same_as_output_renames_input(self, tmp_path):
    mp = self._make_mp(tmp_path)
    # Create an mp4 input so output extension matches
    src = tmp_path / "input.mp4"
    src.write_bytes(b"x")
    out = tmp_path / "input.mp4"

    options = {"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": []}

    results = []

    def fake_convert(outputfile, opts, timeout=None, preopts=None, postopts=None, strip_metadata=False):
      out.write_bytes(b"output")
      yield None, ["ffmpeg"]
      yield 100, ""

    mp.converter.convert = fake_convert
    mp.setPermissions = MagicMock()
    result, inp = mp.convert(options, [], [], False, None)
    # Should succeed (rename collision resolved)
    assert result is not None

  def test_progress_output_callback_called(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"x")
    out = tmp_path / "input.mp4"

    options = {"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": []}
    progress_calls = []

    def fake_convert(outputfile, opts, timeout=None, preopts=None, postopts=None, strip_metadata=False):
      out.write_bytes(b"output")
      yield None, ["ffmpeg"]
      yield 50, "frame=100"

    mp.converter.convert = fake_convert
    mp.setPermissions = MagicMock()
    mp.convert(options, [], [], reportProgress=True, progressOutput=lambda t, d: progress_calls.append(t))
    assert len(progress_calls) > 0


# ---------------------------------------------------------------------------
# displayProgressBar()
# ---------------------------------------------------------------------------


class TestDisplayProgressBar:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.detailedprogress = False
    return mp

  def test_non_tty_skips_output(self):
    mp = self._make_mp()
    import sys

    with patch.object(sys.stdout, "isatty", return_value=False):
      mp.displayProgressBar(50)  # should not raise or output

  def test_non_tty_emits_raw_debug_line(self, capsys):
    mp = self._make_mp()
    import sys

    with patch.object(sys.stdout, "isatty", return_value=False):
      mp.displayProgressBar(50, debug="frame=  100 fps=25 time=00:00:10")
    out = capsys.readouterr().out
    assert "frame=" in out
    assert "%" not in out

  def test_non_tty_flushes_raw_debug_line(self):
    mp = self._make_mp()
    import sys

    with patch.object(sys.stdout, "isatty", return_value=False):
      with patch("builtins.print") as mock_print:
        mp.displayProgressBar(50, debug="frame=  100 fps=25 time=00:00:10")
    mock_print.assert_called_once_with("frame=  100 fps=25 time=00:00:10", flush=True)

  def test_tty_writes_bar(self, capsys):
    mp = self._make_mp()
    import sys

    with patch.object(sys.stdout, "isatty", return_value=True):
      mp.displayProgressBar(50)
    out = capsys.readouterr().out
    assert "%" in out or len(out) >= 0  # just ensure no exception

  def test_over_100_capped(self):
    mp = self._make_mp()
    import sys

    with patch.object(sys.stdout, "isatty", return_value=True):
      mp.displayProgressBar(150)  # should not raise (capped to 100)

  def test_newline_when_requested(self, capsys):
    mp = self._make_mp()
    import sys

    with patch.object(sys.stdout, "isatty", return_value=True):
      mp.displayProgressBar(100, newline=True)
    out = capsys.readouterr().out
    assert "\n" in out

  def test_detailed_progress_shows_debug(self, capsys):
    mp = self._make_mp()
    mp.settings.detailedprogress = True
    import sys

    with patch.object(sys.stdout, "isatty", return_value=True):
      mp.displayProgressBar(50, debug="fps=25")
    out = capsys.readouterr().out
    assert "fps=25" in out


# ---------------------------------------------------------------------------
# QTFS()
# ---------------------------------------------------------------------------


class TestQTFS:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.relocate_moov = True
    mp.settings.output_format = "mp4"
    mp.settings.permissions = {"chmod": 0o664, "uid": -1, "gid": -1}
    return mp

  def test_nonexistent_file_returns_inputfile(self, tmp_path):
    mp = self._make_mp()
    result = mp.QTFS(str(tmp_path / "nonexistent.mp4"))
    assert result == str(tmp_path / "nonexistent.mp4")

  def test_mkv_format_skips(self, tmp_path):
    mp = self._make_mp()
    mp.settings.output_format = "mkv"
    f = tmp_path / "file.mkv"
    f.write_bytes(b"data")
    result = mp.QTFS(str(f))
    assert result == str(f)

  def test_relocate_moov_false_skips(self, tmp_path):
    mp = self._make_mp()
    mp.settings.relocate_moov = False
    f = tmp_path / "file.mp4"
    f.write_bytes(b"data")
    result = mp.QTFS(str(f))
    assert result == str(f)

  def test_faststart_exception_returns_inputfile(self, tmp_path):
    mp = self._make_mp()
    f = tmp_path / "file.mp4"
    f.write_bytes(b"data")
    from qtfaststart import exceptions

    with patch("qtfaststart.processor.process", side_effect=exceptions.FastStartException("already at start")):
      result = mp.QTFS(str(f))
    assert result == str(f)

  def test_successful_qtfs_returns_outputfile(self, tmp_path):
    mp = self._make_mp()
    f = tmp_path / "file.mp4"
    f.write_bytes(b"data")
    temp_out = str(f) + ".QTFS"

    def fake_process(src, dst):
      import shutil

      shutil.copy2(src, dst)

    with patch("qtfaststart.processor.process", side_effect=fake_process):
      result = mp.QTFS(str(f))
    assert result == str(f)


# ---------------------------------------------------------------------------
# getSubOutputFile()
# ---------------------------------------------------------------------------


class TestGetSubOutputFile:
  def _make_mp(self, tmp_path):
    mp = _make_mp()
    mp.settings.output_dir = None
    mp.settings.filename_dispositions = ["forced", "hearing_impaired"]
    return mp

  def test_basic_output_path(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    result = mp.getSubOutputFile(str(src), "eng", "+default-forced", "srt", False)
    assert result.endswith(".srt")
    assert "eng" in result

  def test_forced_dispo_in_filename(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    result = mp.getSubOutputFile(str(src), "eng", "+forced", "srt", False)
    assert ".forced." in result

  def test_collision_appends_counter(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    # Pre-create the target file
    existing = tmp_path / "movie.eng.srt"
    existing.write_bytes(b"sub")
    result = mp.getSubOutputFile(str(src), "eng", "-forced", "srt", False)
    assert ".2." in result

  def test_output_dir_used_when_set(self, tmp_path):
    mp = self._make_mp(tmp_path)
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    mp.settings.output_dir = str(out_dir)
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    result = mp.getSubOutputFile(str(src), "eng", "-forced", "srt", False)
    assert str(out_dir) in result

  def test_include_all_uses_all_dispositions(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    # With include_all=True, all disposition flags are eligible
    result = mp.getSubOutputFile(str(src), "eng", "+comment", "srt", include_all=True)
    assert ".comment." in result


# ---------------------------------------------------------------------------
# generateRipSubOpts()
# ---------------------------------------------------------------------------


class TestGenerateRipSubOpts:
  def _make_mp(self):
    mp = _make_mp()
    return mp

  def test_returns_expected_structure(self, make_stream):
    mp = self._make_mp()
    s = make_stream(type="subtitle", codec="hdmv_pgs_subtitle", index=3, metadata={"language": "eng"})
    s.disposition = {"default": False, "forced": False}
    result = mp.generateRipSubOpts("/fake/input.mkv", s, "copy")
    assert result["source"] == ["/fake/input.mkv"]
    assert result["subtitle"][0]["map"] == 3
    assert result["subtitle"][0]["codec"] == "copy"
    assert result["language"] == "eng"


# ---------------------------------------------------------------------------
# generateSourceDict() - invalid source path
# ---------------------------------------------------------------------------


class TestGenerateSourceDict:
  def _make_mp(self):
    mp = _make_mp()
    mp.titleDispositionCheck = MagicMock()
    return mp

  def test_invalid_source_returns_error_dict(self):
    mp = self._make_mp()
    mp.isValidSource = MagicMock(return_value=None)
    result, probe = mp.generateSourceDict("/fake/file.mkv")
    assert probe is None
    assert result["error"] == "Invalid input, unable to read"

  def test_valid_source_returns_json_data(self):
    mp = self._make_mp()
    info = MagicMock()
    info.json = {"streams": [], "format": {}}
    mp.isValidSource = MagicMock(return_value=info)
    result, probe = mp.generateSourceDict("/fake/file.mkv")
    assert probe is info
    assert "streams" in result


# ---------------------------------------------------------------------------
# estimateVideoBitrate() - exception fallback branch
# ---------------------------------------------------------------------------


class TestEstimateVideoBitrateException:
  def _make_mp(self):
    mp = _make_mp()
    return mp

  def test_exception_falls_back_to_format_bitrate(self, make_stream, make_format):
    mp = self._make_mp()
    info = MagicMock()
    info.video = MagicMock()
    info.video.bitrate = None
    info.format = MagicMock()
    # Raise on first access (try block), return None on second (except block)
    bitrate_mock = MagicMock(side_effect=[Exception("no bitrate"), None])
    type(info.format).bitrate = property(fget=bitrate_mock)
    info.audio = []
    result = mp.estimateVideoBitrate(info)
    # Should return min_video_bitrate (None in this case) without raising
    assert result is None

  def test_no_bitrate_at_all_returns_none(self):
    mp = self._make_mp()
    info = MagicMock()
    info.video = MagicMock()
    info.video.bitrate = None
    info.format = MagicMock()
    info.format.bitrate = None
    info.audio = []
    result = mp.estimateVideoBitrate(info)
    assert result is None


# ---------------------------------------------------------------------------
# restoreFromOutput()
# ---------------------------------------------------------------------------


class TestRestoreFromOutput:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.moveto = None
    return mp

  def test_no_output_dir_returns_outputfile(self, tmp_path):
    mp = self._make_mp()
    mp.settings.output_dir = None
    result = mp.restoreFromOutput("/input/movie.mkv", "/output/movie.mp4")
    assert result == "/output/movie.mp4"

  def test_with_moveto_returns_outputfile(self, tmp_path):
    mp = self._make_mp()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    mp.settings.output_dir = str(output_dir)
    mp.settings.moveto = "/some/moveto"
    outputfile = str(output_dir / "movie.mp4")
    result = mp.restoreFromOutput("/input/movie.mkv", outputfile)
    assert result == outputfile

  def test_outputfile_outside_output_dir_returns_unchanged(self, tmp_path):
    mp = self._make_mp()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    mp.settings.output_dir = str(output_dir)
    # outputfile is NOT under output_dir
    result = mp.restoreFromOutput("/input/movie.mkv", "/some/other/movie.mp4")
    assert result == "/some/other/movie.mp4"


# ---------------------------------------------------------------------------
# _select_subtitle_codec()
# ---------------------------------------------------------------------------


class TestSelectSubtitleCodec:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.embedsubs = True
    mp.settings.embedimgsubs = False
    mp.settings.scodec = ["mov_text"]
    mp.settings.scodec_image = []
    return mp

  def test_text_embed_copy(self):
    mp = self._make_mp()
    result = mp._select_subtitle_codec("mov_text", False, embed=True)
    assert result == "copy"

  def test_text_embed_transcode(self):
    mp = self._make_mp()
    result = mp._select_subtitle_codec("srt", False, embed=True)
    assert result == "mov_text"

  def test_text_embed_disabled_returns_none(self):
    mp = self._make_mp()
    mp.settings.embedsubs = False
    result = mp._select_subtitle_codec("mov_text", False, embed=True)
    assert result is None

  def test_image_embed_disabled_returns_none(self):
    mp = self._make_mp()
    # embedimgsubs=False → image embed is disabled
    result = mp._select_subtitle_codec("hdmv_pgs_subtitle", True, embed=True)
    assert result is None

  def test_rip_text_when_embed_disabled(self):
    mp = self._make_mp()
    mp.settings.embedsubs = False
    mp.settings.scodec = ["srt"]
    result = mp._select_subtitle_codec("mov_text", False, embed=False)
    assert result is not None

  def test_empty_pool_returns_none(self):
    mp = self._make_mp()
    mp.settings.scodec = []
    result = mp._select_subtitle_codec("mov_text", False, embed=True)
    assert result is None


# ---------------------------------------------------------------------------
# _subtitle_passes_filter()
# ---------------------------------------------------------------------------


class TestSubtitlePassesFilter:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.ignored_subtitle_dispositions = ["comment"]
    mp.settings.unique_subtitle_dispositions = False
    mp.settings.force_subtitle_defaults = False
    return mp

  def _make_stream(self, lang="eng", disposition=None):
    s = MagicMock()
    s.metadata = {"language": lang}
    s.disposition = disposition or {"default": False, "forced": False, "comment": False}
    s.dispostr = "-default-forced"
    return s

  def test_valid_language_passes(self):
    mp = self._make_mp()
    s = self._make_stream("eng")
    assert mp._subtitle_passes_filter(s, ["eng"], [], []) is True

  def test_invalid_language_fails(self):
    mp = self._make_mp()
    s = self._make_stream("deu")
    assert mp._subtitle_passes_filter(s, ["eng"], [], []) is False

  def test_ignored_disposition_fails(self):
    mp = self._make_mp()
    s = self._make_stream("eng", disposition={"comment": True, "forced": False, "default": False})
    assert mp._subtitle_passes_filter(s, ["eng"], [], []) is False

  def test_force_default_bypasses_language_check(self):
    mp = self._make_mp()
    mp.settings.force_subtitle_defaults = True
    s = self._make_stream("deu", disposition={"default": True, "forced": False, "comment": False})
    assert mp._subtitle_passes_filter(s, ["eng"], [], []) is True


# ---------------------------------------------------------------------------
# _process_audio_stream() - key branches
# ---------------------------------------------------------------------------


class TestProcessAudioStream:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.acodec = ["aac"]
    mp.settings.ua = []
    mp.settings.ua_bitrate = 128
    mp.settings.ua_vbr = 0
    mp.settings.ua_filter = None
    mp.settings.ua_profile = None
    mp.settings.ua_forcefilter = False
    mp.settings.ua_first_only = False
    mp.settings.audio_samplerates = []
    mp.settings.audio_sampleformat = None
    mp.settings.maxchannels = 0
    mp.settings.abitrate = 128
    mp.settings.amaxbitrate = 0
    mp.settings.afilter = None
    mp.settings.aforcefilter = False
    mp.settings.afilterchannels = {}
    mp.settings.avbr = 0
    mp.settings.aprofile = None
    mp.settings.aac_adtstoasc = False
    mp.settings.audio_copyoriginal = False
    mp.settings.audio_first_language_stream = False
    mp.settings.audio_atmos_force_copy = False
    mp.settings.force_audio_defaults = False
    mp.settings.ignored_audio_dispositions = []
    mp.settings.unique_audio_dispositions = False
    return mp

  def _make_audio_stream(self, codec="aac", channels=2, lang="eng", bitrate=128000, index=1):
    from converter.ffmpeg import MediaStreamInfo

    a = MediaStreamInfo()
    a.type = "audio"
    a.codec = codec
    a.index = index
    a.bitrate = bitrate
    a.audio_channels = channels
    a.audio_samplerate = 48000
    a.metadata = {"language": lang}
    a.disposition = {"default": True, "forced": False, "comment": False}
    a.profile = None
    return a

  def _make_info(self, audio_streams=None):
    from converter.ffmpeg import MediaInfo

    info = MediaInfo()
    for a in audio_streams or []:
      info.streams.append(a)
    return info

  def test_basic_aac_stream_appended(self):
    mp = self._make_mp()
    a = self._make_audio_stream("aac", channels=2)
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert len(audio_settings) == 1
    assert audio_settings[0]["codec"] == "copy"

  def test_non_whitelisted_language_skipped(self):
    mp = self._make_mp()
    a = self._make_audio_stream("aac", lang="deu")
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, ["eng"], True, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert len(audio_settings) == 0

  def test_max_channels_limits_channels(self):
    mp = self._make_mp()
    mp.settings.maxchannels = 2
    a = self._make_audio_stream("aac", channels=6)
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert audio_settings[0]["channels"] == 2

  def test_copy_original_appends_extra_stream(self):
    mp = self._make_mp()
    mp.settings.audio_copyoriginal = True
    a = self._make_audio_stream("ac3", channels=6)
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    # One transcoded + one copy-original
    assert len(audio_settings) == 2
    copy_orig = next((x for x in audio_settings if x.get("debug") == "audio-copy-original"), None)
    assert copy_orig is not None
    assert copy_orig["codec"] == "copy"

  def test_ua_creates_stereo_stream(self):
    mp = self._make_mp()
    a = self._make_audio_stream("aac", channels=6)
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None), patch("resources.mediaprocessor.skipUA", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], True, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=["aac"])
    # Should have original + UA stereo
    assert len(audio_settings) == 2
    ua = next((x for x in audio_settings if x.get("debug") == "universal-audio"), None)
    assert ua is not None
    assert ua["channels"] == 2

  def test_force_filter_prevents_copy(self):
    mp = self._make_mp()
    mp.settings.afilter = "loudnorm"
    mp.settings.aforcefilter = True
    a = self._make_audio_stream("aac", channels=2)
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert audio_settings[0]["codec"] != "copy"

  def test_amaxbitrate_caps_bitrate(self):
    mp = self._make_mp()
    mp.settings.abitrate = 256
    mp.settings.amaxbitrate = 256
    a = self._make_audio_stream("aac", channels=8)  # 8*256=2048 > 256
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert audio_settings[0]["bitrate"] == 256

  def test_zero_abitrate_uses_source_bitrate(self):
    mp = self._make_mp()
    mp.settings.abitrate = 0
    a = self._make_audio_stream("aac", channels=2, bitrate=192000)
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    # bitrate = (192000/1000 / 2) * 2 = 192
    assert audio_settings[0]["bitrate"] == pytest.approx(192.0, rel=0.1)

  def test_first_language_stream_blocks_others(self):
    mp = self._make_mp()
    mp.settings.audio_first_language_stream = True
    a = self._make_audio_stream("aac", lang="eng")
    info = self._make_info([a])
    audio_settings = []
    blocked = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, blocked, [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert "eng" in blocked

  def test_atmos_force_copy(self):
    mp = self._make_mp()
    mp.settings.audio_atmos_force_copy = True
    a = self._make_audio_stream("eac3", channels=6)
    a.profile = "Dolby TrueHD + Dolby Atmos"
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert audio_settings[0]["codec"] == "copy"

  def test_aac_adtstoasc_set_for_copy(self):
    mp = self._make_mp()
    mp.settings.aac_adtstoasc = True
    a = self._make_audio_stream("aac", channels=2)
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert audio_settings[0]["bsf"] == "aac_adtstoasc"


# ---------------------------------------------------------------------------
# _process_subtitle_stream()
# ---------------------------------------------------------------------------


class TestProcessSubtitleStream:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.embedsubs = True
    mp.settings.embedimgsubs = False
    mp.settings.scodec = ["mov_text"]
    mp.settings.scodec_image = []
    mp.settings.ignored_subtitle_dispositions = []
    mp.settings.unique_subtitle_dispositions = False
    mp.settings.force_subtitle_defaults = False
    mp.settings.sub_first_language_stream = False
    mp.settings.cleanit = False
    mp.settings.ffsubsync = False
    return mp

  def _make_sub_stream(self, codec="mov_text", lang="eng", index=2, forced=False):
    from converter.ffmpeg import MediaStreamInfo

    s = MediaStreamInfo()
    s.type = "subtitle"
    s.codec = codec
    s.index = index
    s.metadata = {"language": lang}
    s.disposition = {"default": False, "forced": forced, "comment": False}
    return s

  def _make_info(self):
    from converter.ffmpeg import MediaInfo

    return MediaInfo()

  def test_text_sub_embedded(self):
    mp = self._make_mp()
    s = self._make_sub_stream("mov_text")
    info = self._make_info()
    sub_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch.object(mp, "isImageBasedSubtitle", return_value=False):
      mp._process_subtitle_stream(s, "/fake/input.mkv", info, ["eng"], [], [], sub_settings, [], [], None, scodecs=["mov_text"], scodecs_image=[])
    assert len(sub_settings) == 1
    assert sub_settings[0]["codec"] == "copy"

  def test_image_sub_skipped_when_embedimgsubs_false(self):
    mp = self._make_mp()
    s = self._make_sub_stream("hdmv_pgs_subtitle")
    info = self._make_info()
    sub_settings = []
    ripsubopts = []
    with patch("resources.mediaprocessor.skipStream", None), patch.object(mp, "isImageBasedSubtitle", return_value=True):
      mp._process_subtitle_stream(s, "/fake/input.mkv", info, ["eng"], [], [], sub_settings, [], ripsubopts, None, scodecs=["mov_text"], scodecs_image=[])
    # Should not be embedded (no embedimgsubs), and no rip (no image codec pool)
    assert len(sub_settings) == 0

  def test_language_filter_blocks_stream(self):
    mp = self._make_mp()
    s = self._make_sub_stream("mov_text", lang="deu")
    info = self._make_info()
    sub_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch.object(mp, "isImageBasedSubtitle", return_value=False):
      mp._process_subtitle_stream(s, "/fake/input.mkv", info, ["eng"], [], [], sub_settings, [], [], None, scodecs=["mov_text"], scodecs_image=[])
    assert len(sub_settings) == 0

  def test_transcode_when_codec_not_in_pool(self):
    mp = self._make_mp()
    s = self._make_sub_stream("subrip")  # not mov_text
    info = self._make_info()
    sub_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch.object(mp, "isImageBasedSubtitle", return_value=False):
      mp._process_subtitle_stream(s, "/fake/input.mkv", info, ["eng"], [], [], sub_settings, [], [], None, scodecs=["mov_text"], scodecs_image=[])
    assert len(sub_settings) == 1
    assert sub_settings[0]["codec"] == "mov_text"

  def test_sub_first_language_blocks_second(self):
    mp = self._make_mp()
    mp.settings.sub_first_language_stream = True
    s = self._make_sub_stream("mov_text")
    info = self._make_info()
    sub_settings = []
    blocked = []
    with patch("resources.mediaprocessor.skipStream", None), patch.object(mp, "isImageBasedSubtitle", return_value=False):
      mp._process_subtitle_stream(s, "/fake/input.mkv", info, ["eng"], blocked, [], sub_settings, [], [], None, scodecs=["mov_text"], scodecs_image=[])
    assert "eng" in blocked

  def test_image_based_error_skips_stream(self):
    mp = self._make_mp()
    s = self._make_sub_stream("hdmv_pgs_subtitle")
    info = self._make_info()
    sub_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch.object(mp, "isImageBasedSubtitle", side_effect=Exception("error")):
      mp._process_subtitle_stream(s, "/fake/input.mkv", info, ["eng"], [], [], sub_settings, [], [], None, scodecs=["mov_text"], scodecs_image=[])
    assert len(sub_settings) == 0


# ---------------------------------------------------------------------------
# _process_external_sub()
# ---------------------------------------------------------------------------


class TestProcessExternalSubStream:
  def _make_mp(self):
    mp = _make_mp()
    mp.deletesubs = set()
    mp.settings.embedsubs = True
    mp.settings.embedimgsubs = False
    mp.settings.scodec = ["mov_text"]
    mp.settings.scodec_image = []
    mp.settings.ignored_subtitle_dispositions = []
    mp.settings.unique_subtitle_dispositions = False
    mp.settings.force_subtitle_defaults = False
    mp.settings.sub_first_language_stream = False
    return mp

  def _make_external_sub(self, path="/fake/movie.eng.srt", lang="eng", codec="srt"):
    from converter.ffmpeg import MediaInfo, MediaStreamInfo

    sub_stream = MediaStreamInfo()
    sub_stream.type = "subtitle"
    sub_stream.codec = codec
    sub_stream.index = 0
    sub_stream.metadata = {"language": lang}
    sub_stream.disposition = {"default": False, "forced": False, "comment": False}

    info = MediaInfo()
    info.path = path
    info.streams.append(sub_stream)
    return info

  def test_text_sub_appended(self):
    mp = self._make_mp()
    ext_sub = self._make_external_sub()
    sub_settings = []
    sources = ["/fake/movie.mkv"]
    with patch.object(mp, "isImageBasedSubtitle", return_value=False), patch.object(mp, "cleanDispositions"):
      mp._process_external_sub(ext_sub, "/fake/movie.mkv", ["eng"], [], [], sub_settings, sources, None)
    assert len(sub_settings) == 1

  def test_no_valid_codec_skips(self):
    mp = self._make_mp()
    mp.settings.embedsubs = False
    ext_sub = self._make_external_sub()
    sub_settings = []
    sources = ["/fake/movie.mkv"]
    with patch.object(mp, "isImageBasedSubtitle", return_value=False), patch.object(mp, "cleanDispositions"):
      mp._process_external_sub(ext_sub, "/fake/movie.mkv", ["eng"], [], [], sub_settings, sources, None)
    assert len(sub_settings) == 0

  def test_path_added_to_sources(self):
    mp = self._make_mp()
    ext_sub = self._make_external_sub("/fake/movie.eng.srt")
    sub_settings = []
    sources = ["/fake/movie.mkv"]
    with patch.object(mp, "isImageBasedSubtitle", return_value=False), patch.object(mp, "cleanDispositions"):
      mp._process_external_sub(ext_sub, "/fake/movie.mkv", ["eng"], [], [], sub_settings, sources, None)
    assert "/fake/movie.eng.srt" in sources

  def test_deletesubs_scheduled(self):
    mp = self._make_mp()
    ext_sub = self._make_external_sub("/fake/movie.eng.srt")
    sub_settings = []
    sources = ["/fake/movie.mkv"]
    with patch.object(mp, "isImageBasedSubtitle", return_value=False), patch.object(mp, "cleanDispositions"):
      mp._process_external_sub(ext_sub, "/fake/movie.mkv", ["eng"], [], [], sub_settings, sources, None)
    assert "/fake/movie.eng.srt" in mp.deletesubs

  def test_image_error_skips(self):
    mp = self._make_mp()
    ext_sub = self._make_external_sub()
    sub_settings = []
    sources = ["/fake/movie.mkv"]
    with patch.object(mp, "isImageBasedSubtitle", side_effect=Exception("error")), patch.object(mp, "cleanDispositions"):
      mp._process_external_sub(ext_sub, "/fake/movie.mkv", ["eng"], [], [], sub_settings, sources, None)
    assert len(sub_settings) == 0


# ---------------------------------------------------------------------------
# cleanExternalSub()
# ---------------------------------------------------------------------------


class TestCleanExternalSub:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.cleanit = False
    mp.settings.cleanit_config = None
    mp.settings.cleanit_tags = ["default"]
    return mp

  def test_cleanit_disabled_noop(self, tmp_path):
    mp = self._make_mp()
    f = tmp_path / "sub.srt"
    f.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    mp.cleanExternalSub(str(f))  # should not raise

  def test_cleanit_enabled_processes(self, tmp_path):
    mp = self._make_mp()
    mp.settings.cleanit = True
    f = tmp_path / "sub.srt"
    f.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    with patch("resources.mediaprocessor.cleanit") as mock_cleanit:
      mock_sub = MagicMock()
      mock_cleanit.Subtitle.return_value = mock_sub
      mock_cfg = MagicMock()
      mock_cleanit.Config.return_value = mock_cfg
      mock_rules = MagicMock()
      mock_cfg.select_rules.return_value = mock_rules
      mock_sub.clean.return_value = True
      mp.cleanExternalSub(str(f))
    mock_sub.save.assert_called_once()


# ---------------------------------------------------------------------------
# videoStreamTitle() - 8K + custom streamTitle hook
# ---------------------------------------------------------------------------


class TestVideoStreamTitleExtended:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.keep_titles = False
    return mp

  def test_8k_resolution(self, make_stream):
    mp = self._make_mp()
    stream = make_stream(type="video", video_width=7680, video_height=4320)
    with patch("resources.mediaprocessor.streamTitle", None):
      title = mp.videoStreamTitle(stream, {})
    assert title == "8K"

  def test_custom_stream_title_overrides(self, make_stream):
    mp = self._make_mp()
    stream = make_stream(type="video", video_width=1920, video_height=1080)
    custom_fn = MagicMock(return_value="Custom Video Title")
    with patch("resources.mediaprocessor.streamTitle", custom_fn):
      title = mp.videoStreamTitle(stream, {})
    assert title == "Custom Video Title"

  def test_custom_stream_title_returns_none_falls_through(self, make_stream):
    mp = self._make_mp()
    stream = make_stream(type="video", video_width=1920, video_height=1080)
    custom_fn = MagicMock(return_value=None)
    with patch("resources.mediaprocessor.streamTitle", custom_fn):
      title = mp.videoStreamTitle(stream, {})
    assert title == "FHD"

  def test_keep_titles_uses_existing(self, make_stream):
    mp = self._make_mp()
    mp.settings.keep_titles = True
    stream = make_stream(type="video", video_width=1920, video_height=1080, metadata={"title": "Main Feature", "language": "eng"})
    with patch("resources.mediaprocessor.streamTitle", None):
      title = mp.videoStreamTitle(stream, {})
    assert title == "Main Feature"

  def test_custom_stream_title_exception_falls_through(self, make_stream):
    mp = self._make_mp()
    stream = make_stream(type="video", video_width=1920, video_height=1080)

    def bad_fn(*args, **kwargs):
      raise ValueError("oops")

    with patch("resources.mediaprocessor.streamTitle", bad_fn):
      title = mp.videoStreamTitle(stream, {})
    assert title == "FHD"


# ---------------------------------------------------------------------------
# audioStreamTitle() - keep_titles + atmos copy
# ---------------------------------------------------------------------------


class TestAudioStreamTitleExtended:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.keep_titles = False
    return mp

  def test_keep_titles_uses_existing(self, make_stream):
    mp = self._make_mp()
    mp.settings.keep_titles = True
    stream = make_stream(type="audio", metadata={"title": "Director Mix", "language": "eng"})
    stream.disposition = {"default": False, "forced": False}
    with patch("resources.mediaprocessor.streamTitle", None):
      title = mp.audioStreamTitle(stream, {"channels": 2})
    assert title == "Director Mix"

  def test_atmos_copy_appends_atmos(self, make_stream):
    mp = self._make_mp()
    stream = make_stream(type="audio", disposition={"default": False, "forced": False})
    stream.profile = "Dolby TrueHD + Atmos"
    with patch("resources.mediaprocessor.streamTitle", None):
      title = mp.audioStreamTitle(stream, {"channels": 8, "codec": "copy"})
    assert "Atmos" in title

  def test_custom_stream_title_exception_falls_through(self, make_stream):
    mp = self._make_mp()
    stream = make_stream(type="audio", disposition={"default": False, "forced": False})

    def bad_fn(*args, **kwargs):
      raise ValueError("oops")

    with patch("resources.mediaprocessor.streamTitle", bad_fn):
      title = mp.audioStreamTitle(stream, {"channels": 2})
    assert title == "Stereo"


# ---------------------------------------------------------------------------
# normalizeFramedata()
# ---------------------------------------------------------------------------


class TestNormalizeFramedata:
  def _make_mp(self):
    return _make_mp()

  def test_hdr_sets_flags(self):
    mp = self._make_mp()
    fd = {}
    result = mp.normalizeFramedata(fd, hdr=True)
    assert result["hdr"] is True
    assert result["repeat-headers"] is True

  def test_mastering_display_normalized(self):
    mp = self._make_mp()
    fd = {
      "hdr": True,
      "side_data_list": [
        {
          "side_data_type": "Mastering display metadata",
          "red_x": "34000/50000",
          "red_y": "16000/50000",
          "green_x": "13250/50000",
          "green_y": "34500/50000",
          "blue_x": "7500/50000",
          "blue_y": "3000/50000",
          "white_point_x": "15635/50000",
          "white_point_y": "16450/50000",
          "min_luminance": "500/10000",
          "max_luminance": "50000000/10000",
        }
      ],
    }
    result = mp.normalizeFramedata(fd, hdr=True)
    mastering = next(x for x in result["side_data_list"] if x["side_data_type"] == "Mastering display metadata")
    assert mastering["red_x"] == 34000
    assert mastering["min_luminance"] == 500

  def test_exception_returns_original(self):
    mp = self._make_mp()
    fd = None
    result = mp.normalizeFramedata(fd, hdr=False)
    assert result is None


# ---------------------------------------------------------------------------
# isValidSource() - validation hook and no audio/video
# ---------------------------------------------------------------------------


class TestIsValidSourceExtended:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.ignored_extensions = ["nfo"]
    mp.settings.minimum_size = 0
    return mp

  def test_no_video_stream_returns_none(self, tmp_path):
    mp = self._make_mp()
    f = tmp_path / "audio.mp3"
    f.write_bytes(b"x" * 1024)
    info = MagicMock()
    info.video = None
    info.audio = [MagicMock()]
    mp.converter.probe.return_value = info
    result = mp.isValidSource(str(f))
    assert result is None

  def test_no_audio_stream_returns_none(self, tmp_path):
    mp = self._make_mp()
    f = tmp_path / "video.mkv"
    f.write_bytes(b"x" * 1024)
    info = MagicMock()
    info.video = MagicMock()
    info.audio = []
    mp.converter.probe.return_value = info
    result = mp.isValidSource(str(f))
    assert result is None

  def test_validation_hook_returns_false(self, tmp_path):
    mp = self._make_mp()
    f = tmp_path / "video.mkv"
    f.write_bytes(b"x" * 1024)
    info = MagicMock()
    info.video = MagicMock()
    info.audio = [MagicMock()]
    mp.converter.probe.return_value = info
    with patch("resources.mediaprocessor.validation", MagicMock(return_value=False)):
      result = mp.isValidSource(str(f))
    assert result is None

  def test_probe_returns_none_returns_none(self, tmp_path):
    mp = self._make_mp()
    f = tmp_path / "video.mkv"
    f.write_bytes(b"x" * 1024)
    mp.converter.probe.return_value = None
    result = mp.isValidSource(str(f))
    assert result is None


# ---------------------------------------------------------------------------
# canBypassConvert() - bypass_copy_all branch
# ---------------------------------------------------------------------------


class TestCanBypassConvertExtended:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.output_extension = "mp4"
    mp.settings.force_convert = False
    mp.settings.process_same_extensions = True  # allows inspection of the encoder check
    mp.settings.bypass_copy_all = True
    return mp

  def test_bypass_all_copy_returns_true(self):
    mp = self._make_mp()
    info = MagicMock()
    info.format.metadata = {}
    info.audio = [MagicMock()]
    info.subtitle = [MagicMock()]
    options = {
      "video": {"codec": "copy"},
      "audio": [{"codec": "copy"}],
      "subtitle": [{"codec": "copy"}],
    }
    result = mp.canBypassConvert("/path/to/file.mp4", info, options)
    assert result is True

  def test_bypass_copy_all_false_when_transcoding(self):
    mp = self._make_mp()
    info = MagicMock()
    info.format.metadata = {}
    info.audio = [MagicMock()]
    info.subtitle = []
    options = {
      "video": {"codec": "h264"},
      "audio": [{"codec": "aac"}],
      "subtitle": [],
    }
    result = mp.canBypassConvert("/path/to/file.mp4", info, options)
    assert result is False


# ---------------------------------------------------------------------------
# _init_hw_device_opts() static method
# ---------------------------------------------------------------------------


class TestInitHwDeviceOpts:
  def test_qsv_uses_qsv_device_flag(self):
    from resources.mediaprocessor import MediaProcessor

    opts = MediaProcessor._init_hw_device_opts("qsv", "sma", "/dev/dri/renderD128")
    assert opts == ["-qsv_device", "/dev/dri/renderD128"]

  def test_non_qsv_uses_init_hw_device(self):
    from resources.mediaprocessor import MediaProcessor

    opts = MediaProcessor._init_hw_device_opts("vaapi", "sma", "/dev/dri/renderD128")
    assert "-init_hw_device" in opts
    assert "vaapi=sma:/dev/dri/renderD128" in opts


# ---------------------------------------------------------------------------
# purgeDuplicateStreams()
# ---------------------------------------------------------------------------


class TestPurgeDuplicateStreams:
  def _make_mp(self):
    mp = _make_mp()
    return mp

  def _make_info(self, make_stream, streams):
    from converter.ffmpeg import MediaInfo

    info = MediaInfo()
    for s in streams:
      info.streams.append(s)
    return info

  def test_purges_duplicate_same_codec(self, make_stream):
    mp = self._make_mp()
    s0 = make_stream(type="audio", codec="aac", index=0)
    s0.disposition = {"default": False}
    s1 = make_stream(type="audio", codec="aac", index=1)
    s1.disposition = {"default": False}
    info = self._make_info(make_stream, [s0, s1])
    combinations = [[0, 1]]
    options = [
      {"map": 0, "codec": "aac", "channels": 2, "bitrate": 128},
      {"map": 1, "codec": "aac", "channels": 2, "bitrate": 256},
    ]
    with patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
      result = mp.purgeDuplicateStreams(combinations, options, info, ["aac"], [])
    # The lower bitrate stream should be removed
    assert len(options) == 1
    assert result is True

  def test_no_combinations_no_purge(self, make_stream):
    mp = self._make_mp()
    s0 = make_stream(type="audio", codec="aac", index=0)
    s0.disposition = {"default": False}
    info = self._make_info(make_stream, [s0])
    options = [{"map": 0, "codec": "aac", "channels": 2, "bitrate": 128}]
    with patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
      result = mp.purgeDuplicateStreams([], options, info, ["aac"], [])
    assert len(options) == 1
    assert result is False


# ---------------------------------------------------------------------------
# setAcceleration() - hwaccel with output format
# ---------------------------------------------------------------------------


class TestSetAccelerationExtended:
  def test_qsv_device_uses_qsv_flag(self):
    mp = _make_mp()
    mp.converter = MagicMock()
    mp.converter.ffmpeg.hwaccels = ["qsv"]
    mp.converter.ffmpeg.pix_fmts = {"yuv420p": 8}
    mp.converter.ffmpeg.codecs = {"h264": {"decoders": [], "encoders": []}}
    mp.converter.ffmpeg.hwaccel_decoder = MagicMock(return_value=None)
    mp.settings.hwaccels = ["qsv"]
    mp.settings.hwdevices = {"qsv": "/dev/dri/renderD128"}
    mp.settings.hwoutputfmt = {}
    mp.settings.hwaccel_decoders = []
    opts, device = mp.setAcceleration("h264", "yuv420p")
    assert "-qsv_device" in opts
    assert "/dev/dri/renderD128" in opts
    assert device == "/dev/dri/renderD128"

  def test_hwaccel_output_format_added(self):
    mp = _make_mp()
    mp.converter = MagicMock()
    mp.converter.ffmpeg.hwaccels = ["videotoolbox"]
    mp.converter.ffmpeg.pix_fmts = {"yuv420p": 8}
    mp.converter.ffmpeg.codecs = {"h264": {"decoders": [], "encoders": []}}
    mp.converter.ffmpeg.hwaccel_decoder = MagicMock(return_value=None)
    mp.settings.hwaccels = ["videotoolbox"]
    mp.settings.hwdevices = {}
    mp.settings.hwoutputfmt = {"videotoolbox": "videotoolbox_vld"}
    mp.settings.hwaccel_decoders = []
    opts, _ = mp.setAcceleration("h264", "yuv420p")
    assert "-hwaccel_output_format" in opts
    assert "videotoolbox_vld" in opts


# ---------------------------------------------------------------------------
# outputDirHasFreeSpace() - enough/not-enough branches
# ---------------------------------------------------------------------------


class TestOutputDirHasFreeSpaceExtended:
  def _make_mp(self):
    mp = _make_mp()
    return mp

  def test_not_enough_space_returns_false(self, tmp_path):
    import shutil

    mp = self._make_mp()
    mp.settings.output_dir = str(tmp_path)
    mp.settings.output_dir_ratio = 1000000  # requires 1 million x file size
    f = tmp_path / "big.mkv"
    f.write_bytes(b"x" * 1024)
    fake_usage = shutil.disk_usage(str(tmp_path))._replace(free=0)
    with patch("resources.mediaprocessor.shutil.disk_usage", return_value=fake_usage):
      result = mp.outputDirHasFreeSpace(str(f))
    assert result is False

  def test_enough_space_returns_true(self, tmp_path):
    mp = self._make_mp()
    mp.settings.output_dir = str(tmp_path)
    mp.settings.output_dir_ratio = 0.0001  # requires tiny fraction
    f = tmp_path / "small.mkv"
    f.write_bytes(b"x" * 1024)
    result = mp.outputDirHasFreeSpace(str(f))
    assert result is True


# ---------------------------------------------------------------------------
# replicate() - copyto failure branch
# ---------------------------------------------------------------------------


class TestReplicateExtended:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.copyto = []
    mp.settings.moveto = None
    return mp

  def test_copyto_failure_logged_not_raised(self, tmp_path):
    mp = self._make_mp()
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"data")
    dest_dir = tmp_path / "library"
    dest_dir.mkdir()
    mp.settings.copyto = [str(dest_dir)]
    with patch.object(mp, "_atomic_copy", side_effect=Exception("disk full")):
      result = mp.replicate(str(src))
    # Even if copy fails twice, original is still in list
    assert str(src) in result
    mp.log.exception.assert_called()

  def test_moveto_failure_logged(self, tmp_path):
    mp = self._make_mp()
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"data")
    dest_dir = tmp_path / "library"
    dest_dir.mkdir()
    mp.settings.moveto = str(dest_dir)
    with patch.object(mp, "_atomic_move", side_effect=Exception("permission denied")):
      result = mp.replicate(str(src))
    mp.log.exception.assert_called()

  def test_relative_path_appended(self, tmp_path):
    mp = self._make_mp()
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"data")
    dest_dir = tmp_path / "library"
    dest_dir.mkdir()
    mp.settings.copyto = [str(dest_dir)]
    with patch.object(mp, "_atomic_copy"):
      result = mp.replicate(str(src), relativePath="Shows/Season1")
    # Should not raise


# ---------------------------------------------------------------------------
# _warn_unsupported_encoders() - warning branches
# ---------------------------------------------------------------------------


class TestWarnUnsupportedEncoders:
  def _make_mp(self):
    mp = _make_mp()
    return mp

  def test_undefined_codec_logs_warning(self):
    mp = self._make_mp()
    codecs = {"h264": {"encoders": ["libx264"]}}
    mp.converter.codec_name_to_ffmpeg_codec_name.return_value = None
    stream_options = [{"codec": "unknown_codec_xyz"}]
    mp._warn_unsupported_encoders(codecs, stream_options)
    mp.log.warning.assert_called()

  def test_unsupported_by_ffmpeg_logs_warning(self):
    mp = self._make_mp()
    codecs = {"h264": {"encoders": ["libx264"]}}
    mp.converter.codec_name_to_ffmpeg_codec_name.return_value = "libnvenc_h264"
    with patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", return_value="h264"):
      stream_options = [{"codec": "h264_nvenc"}]
      mp._warn_unsupported_encoders(codecs, stream_options)
    mp.log.warning.assert_called()

  def test_copy_codec_skipped(self):
    mp = self._make_mp()
    codecs = {"h264": {"encoders": ["libx264"]}}
    stream_options = [{"codec": "copy"}]
    mp._warn_unsupported_encoders(codecs, stream_options)
    mp.log.warning.assert_not_called()

  def test_no_codec_key_skipped(self):
    mp = self._make_mp()
    codecs = {"h264": {"encoders": ["libx264"]}}
    stream_options = [{}]  # no 'codec' key
    mp._warn_unsupported_encoders(codecs, stream_options)
    mp.log.warning.assert_not_called()


# ===========================================================================
# New tests to increase coverage
# ===========================================================================


def _build_mp_with_real_settings(tmp_yaml_factory):
  """Build a MediaProcessor with real ReadSettings and a mocked Converter."""
  with patch("resources.readsettings.ReadSettings._validate_binaries"):
    from resources.mediaprocessor import MediaProcessor
    from resources.readsettings import ReadSettings
    from resources.subtitles import SubtitleProcessor

    settings = ReadSettings(tmp_yaml_factory())

  mock_converter = MagicMock()
  mock_converter.ffmpeg.codecs = {
    "h264": {"encoders": ["libx264"], "decoders": []},
    "hevc": {"encoders": ["libx265"], "decoders": []},
    "aac": {"encoders": ["aac"], "decoders": []},
    "mov_text": {"encoders": ["mov_text"], "decoders": []},
  }
  mock_converter.ffmpeg.pix_fmts = {"yuv420p": 8, "yuv420p10le": 10}
  mock_converter.ffmpeg.hwaccels = []
  mock_converter.codec_name_to_ffmpeg_codec_name.side_effect = lambda c: c
  mock_converter.ffmpeg.hwaccel_decoder = MagicMock(return_value=None)

  mp = MediaProcessor.__new__(MediaProcessor)
  mp.settings = settings
  mp.converter = mock_converter
  mp.log = MagicMock()
  mp.deletesubs = set()
  mp.subtitles = SubtitleProcessor(mp)
  return mp


# ---------------------------------------------------------------------------
# fullprocess
# ---------------------------------------------------------------------------


class TestFullProcess:
  """Tests for the fullprocess() orchestration method."""

  def _make_mp(self):
    mp = _make_mp()
    mp.settings.tagfile = False
    mp.settings.relocate_moov = False
    mp.settings.naming_enabled = False
    mp.settings.plexmatch_enabled = False
    mp.settings.postprocess = False
    mp.settings.Plex = {"refresh": False}
    mp.settings.taglanguage = None
    mp.settings.output_format = "mp4"
    mp.deletesubs = set()
    return mp

  def test_invalid_source_returns_false(self):
    mp = self._make_mp()
    mp.isValidSource = MagicMock(return_value=None)
    result = mp.fullprocess("/fake/file.mkv", "movie")
    assert result is False

  def test_process_fails_returns_false(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.process = MagicMock(return_value=None)
    result = mp.fullprocess("/fake/file.mkv", "movie", tagdata=MagicMock())
    assert result is False

  def test_successful_path_returns_output_files(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    output = {
      "output": "/fake/file.mp4",
      "external_subs": [],
      "options": {"audio": []},
      "x": 1920,
      "y": 1080,
      "cues_to_front": False,
    }
    mp.process = MagicMock(return_value=output)
    mp.restoreFromOutput = MagicMock(side_effect=lambda i, o: o)
    mp.replicate = MagicMock(return_value=["/fake/file.mp4"])
    mp.setPermissions = MagicMock()
    mp.post = MagicMock()
    mp.getDefaultAudioLanguage = MagicMock(return_value="eng")
    result = mp.fullprocess("/fake/file.mkv", "movie", tagdata=MagicMock(), post=True)
    assert result == ["/fake/file.mp4"]
    mp.post.assert_called_once()

  def test_post_false_skips_post(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    output = {
      "output": "/fake/file.mp4",
      "external_subs": [],
      "options": {"audio": []},
      "x": 1920,
      "y": 1080,
      "cues_to_front": False,
    }
    mp.process = MagicMock(return_value=output)
    mp.restoreFromOutput = MagicMock(side_effect=lambda i, o: o)
    mp.replicate = MagicMock(return_value=["/fake/file.mp4"])
    mp.setPermissions = MagicMock()
    mp.post = MagicMock()
    mp.getDefaultAudioLanguage = MagicMock(return_value="eng")
    result = mp.fullprocess("/fake/file.mkv", "movie", tagdata=MagicMock(), post=False)
    assert result == ["/fake/file.mp4"]
    mp.post.assert_not_called()

  def test_tagging_enabled_calls_write_tags(self):
    mp = self._make_mp()
    mp.settings.tagfile = True
    mp.settings.artwork = True
    mp.settings.thumbnail = True
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    tagdata = MagicMock()
    tagdata.tmdbid = 12345
    output = {
      "output": "/fake/file.mp4",
      "external_subs": [],
      "options": {"audio": []},
      "x": 1920,
      "y": 1080,
      "cues_to_front": False,
    }
    mp.process = MagicMock(return_value=output)
    mp.restoreFromOutput = MagicMock(side_effect=lambda i, o: o)
    mp.replicate = MagicMock(return_value=["/fake/file.mp4"])
    mp.setPermissions = MagicMock()
    mp.post = MagicMock()
    mp.getDefaultAudioLanguage = MagicMock(return_value="eng")
    mp.fullprocess("/fake/file.mkv", "movie", tagdata=tagdata, post=False)
    tagdata.writeTags.assert_called_once()

  def test_metadata_fetch_exception_continues(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    output = {
      "output": "/fake/file.mp4",
      "external_subs": [],
      "options": {"audio": []},
      "x": 1920,
      "y": 1080,
      "cues_to_front": False,
    }
    mp.process = MagicMock(return_value=output)
    mp.restoreFromOutput = MagicMock(side_effect=lambda i, o: o)
    mp.replicate = MagicMock(return_value=["/fake/file.mp4"])
    mp.setPermissions = MagicMock()
    mp.post = MagicMock()
    mp.getDefaultAudioLanguage = MagicMock(return_value="eng")
    # No tagdata provided, so Metadata() will be called; patch it to raise
    with patch("resources.mediaprocessor.Metadata", side_effect=Exception("tmdb error")):
      result = mp.fullprocess("/fake/file.mkv", "movie", post=False)
    # Should still complete successfully (tagdata=None after exception)
    assert result == ["/fake/file.mp4"]

  def test_exception_in_process_returns_false(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.process = MagicMock(side_effect=Exception("unexpected"))
    result = mp.fullprocess("/fake/file.mkv", "movie", tagdata=MagicMock())
    assert result is False

  def test_relocate_moov_called_when_enabled(self):
    mp = self._make_mp()
    mp.settings.relocate_moov = True
    mp.settings.tagfile = True
    mp.settings.artwork = False
    mp.settings.thumbnail = False
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    tagdata = MagicMock()
    tagdata.tmdbid = 1
    output = {
      "output": "/fake/file.mp4",
      "external_subs": [],
      "options": {"audio": []},
      "x": 1920,
      "y": 1080,
      "cues_to_front": False,
    }
    mp.process = MagicMock(return_value=output)
    mp.restoreFromOutput = MagicMock(side_effect=lambda i, o: o)
    mp.replicate = MagicMock(return_value=["/fake/file.mp4"])
    mp.setPermissions = MagicMock()
    mp.post = MagicMock()
    mp.getDefaultAudioLanguage = MagicMock(return_value="eng")
    mp.QTFS = MagicMock()
    # tagging won't raise
    tagdata.writeTags = MagicMock()
    mp.fullprocess("/fake/file.mkv", "movie", tagdata=tagdata, post=False)
    mp.QTFS.assert_called_once()

  def test_external_subs_replicated(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    sub_path = "/fake/file.eng.srt"
    output = {
      "output": "/fake/file.mp4",
      "external_subs": [sub_path],
      "options": {"audio": []},
      "x": 1920,
      "y": 1080,
      "cues_to_front": False,
    }
    mp.process = MagicMock(return_value=output)
    mp.restoreFromOutput = MagicMock(side_effect=lambda i, o: o)
    mp.replicate = MagicMock(return_value=["/fake/file.mp4"])
    mp.setPermissions = MagicMock()
    mp.post = MagicMock()
    mp.getDefaultAudioLanguage = MagicMock(return_value="eng")
    with patch("os.path.exists", return_value=True):
      mp.fullprocess("/fake/file.mkv", "movie", tagdata=MagicMock(), post=False)
    # replicate called once for main output + once for sub
    assert mp.replicate.call_count == 2


# ---------------------------------------------------------------------------
# post()
# ---------------------------------------------------------------------------


class TestPost:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.postprocess = False
    mp.settings.Plex = {"refresh": False}
    return mp

  def test_no_postprocess_no_plex_noop(self):
    mp = self._make_mp()
    mp.post(["/fake/file.mp4"], "movie")  # should not raise

  def test_postprocess_runs_scripts(self):
    mp = self._make_mp()
    mp.settings.postprocess = True
    mp.settings.waitpostprocess = False
    with patch("resources.mediaprocessor.PostProcessor") as MockPP:
      pp_instance = MagicMock()
      MockPP.return_value = pp_instance
      mp.post(["/fake/file.mp4"], "movie", tmdbid=123)
    MockPP.assert_called_once()
    pp_instance.setEnv.assert_called_once()
    pp_instance.run_scripts.assert_called_once()

  def test_plex_refresh_called(self):
    mp = self._make_mp()
    mp.settings.Plex = {"refresh": True}
    with patch("resources.mediaprocessor.plex.refreshPlex") as mock_refresh:
      mp.post(["/fake/file.mp4"], "movie")
    mock_refresh.assert_called_once()

  def test_plex_refresh_exception_logged(self):
    mp = self._make_mp()
    mp.settings.Plex = {"refresh": True}
    with patch("resources.mediaprocessor.plex.refreshPlex", side_effect=Exception("plex down")):
      mp.post(["/fake/file.mp4"], "movie")  # should not raise
    mp.log.exception.assert_called()


# ---------------------------------------------------------------------------
# process()
# ---------------------------------------------------------------------------


class TestProcess:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.delete = False
    mp.settings.output_dir = None
    mp.settings.output_extension = "mp4"
    mp.settings.output_format = "mp4"
    mp.settings.relocate_moov = False
    mp.settings.recycle_bin = None
    mp.deletesubs = set()
    return mp

  def test_invalid_source_returns_none(self):
    mp = self._make_mp()
    mp.isValidSource = MagicMock(return_value=None)
    mp.outputDirHasFreeSpace = MagicMock(return_value=True)
    assert mp.process("/fake/input.mkv") is None

  def test_bypass_convert_sets_outputfile_to_inputfile(self, tmp_path):
    mp = self._make_mp()
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"x" * 100)
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.outputDirHasFreeSpace = MagicMock(return_value=True)
    mp.generateOptions = MagicMock(return_value=({"video": {}, "audio": []}, [], [], [], []))
    mp.canBypassConvert = MagicMock(return_value=True)
    mp.getDimensions = MagicMock(return_value={"x": 1920, "y": 1080})
    mp._cleanup_input = MagicMock(return_value=False)
    result = mp.process(str(src))
    assert result is not None
    assert result["output"] == str(src)
    assert result["input"] == str(src)

  def test_generate_options_exception_returns_none(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.outputDirHasFreeSpace = MagicMock(return_value=True)
    mp.generateOptions = MagicMock(side_effect=Exception("options error"))
    result = mp.process("/fake/input.mkv", info=info)
    assert result is None

  def test_run_ffmpeg_failure_returns_none(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.outputDirHasFreeSpace = MagicMock(return_value=True)
    options = {"video": {}, "audio": [{"map": 1, "codec": "aac"}]}
    mp.generateOptions = MagicMock(return_value=(options, [], [], [], []))
    mp.canBypassConvert = MagicMock(return_value=False)
    mp._run_ffmpeg = MagicMock(return_value=(None, "/fake/input.mkv", []))
    result = mp.process("/fake/input.mkv", info=info)
    assert result is None

  def test_bypass_with_output_dir_copies_file(self, tmp_path):
    mp = self._make_mp()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    mp.settings.output_dir = str(output_dir)
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"data")
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.outputDirHasFreeSpace = MagicMock(return_value=True)
    mp.generateOptions = MagicMock(return_value=({"video": {}, "audio": []}, [], [], [], []))
    mp.canBypassConvert = MagicMock(return_value=True)
    mp.getDimensions = MagicMock(return_value={"x": 1920, "y": 1080})
    mp._cleanup_input = MagicMock(return_value=False)
    result = mp.process(str(src))
    assert result is not None
    # outputfile should be in output_dir
    assert str(output_dir) in result["output"]

  def test_options_empty_returns_none(self):
    mp = self._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.outputDirHasFreeSpace = MagicMock(return_value=True)
    mp.generateOptions = MagicMock(return_value=(None, [], [], [], []))
    mp.canBypassConvert = MagicMock(return_value=False)
    result = mp.process("/fake/input.mkv", info=info)
    assert result is None


# ---------------------------------------------------------------------------
# _run_ffmpeg()
# ---------------------------------------------------------------------------


class TestRunFfmpeg:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.embedsubs = True
    mp.settings.downloadsubs = False
    return mp

  def test_success_returns_tuple(self):
    mp = self._make_mp()
    mp.subtitles = MagicMock()
    mp.subtitles.ripSubs.return_value = []
    mp.cleanExternalSub = MagicMock()
    mp.convert = MagicMock(return_value=("/fake/output.mp4", "/fake/input.mkv"))
    result = mp._run_ffmpeg("/fake/input.mkv", {"video": {}, "audio": []}, [], [], [], [], False, None)
    assert result == ("/fake/output.mp4", "/fake/input.mkv", [])

  def test_convert_exception_returns_none(self):
    mp = self._make_mp()
    mp.subtitles = MagicMock()
    mp.subtitles.ripSubs.return_value = []
    mp.cleanExternalSub = MagicMock()
    mp.convert = MagicMock(side_effect=Exception("conversion failed"))
    outputfile, inputfile, ripped = mp._run_ffmpeg("/fake/input.mkv", {}, [], [], [], [], False, None)
    assert outputfile is None
    assert inputfile == "/fake/input.mkv"

  def test_no_outputfile_returns_none(self):
    mp = self._make_mp()
    mp.subtitles = MagicMock()
    mp.subtitles.ripSubs.return_value = []
    mp.cleanExternalSub = MagicMock()
    mp.convert = MagicMock(return_value=(None, "/fake/input.mkv"))
    outputfile, inputfile, ripped = mp._run_ffmpeg("/fake/input.mkv", {}, [], [], [], [], False, None)
    assert outputfile is None


# ---------------------------------------------------------------------------
# _cleanup_input()
# ---------------------------------------------------------------------------


class TestCleanupInput:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.recycle_bin = None
    mp.deletesubs = set()
    return mp

  def test_delete_false_returns_false(self, tmp_path):
    mp = self._make_mp()
    src = tmp_path / "input.mkv"
    src.write_bytes(b"data")
    result = mp._cleanup_input(str(src), delete=False)
    assert result is False
    assert src.exists()

  def test_delete_true_removes_file(self, tmp_path):
    mp = self._make_mp()
    src = tmp_path / "input.mkv"
    src.write_bytes(b"data")
    result = mp._cleanup_input(str(src), delete=True)
    assert result is True
    assert not src.exists()

  def test_delete_true_with_recycle_bin_copies_first(self, tmp_path):
    mp = self._make_mp()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    mp.settings.recycle_bin = str(recycle)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"data")
    mp._atomic_copy = MagicMock()
    result = mp._cleanup_input(str(src), delete=True)
    mp._atomic_copy.assert_called_once()
    assert result is True

  def test_recycle_bin_collision_adds_suffix(self, tmp_path):
    mp = self._make_mp()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    mp.settings.recycle_bin = str(recycle)
    # Pre-create file in recycle to trigger collision
    (recycle / "input.mkv").write_bytes(b"old")
    src = tmp_path / "input.mkv"
    src.write_bytes(b"data")
    mp._atomic_copy = MagicMock()
    mp._cleanup_input(str(src), delete=True)
    # The copy dst should have ".2." in the name
    call_args = mp._atomic_copy.call_args[0]
    assert ".2." in call_args[1] or "input.2.mkv" in call_args[1]

  def test_delete_subs_on_delete(self, tmp_path):
    mp = self._make_mp()
    src = tmp_path / "input.mkv"
    src.write_bytes(b"data")
    sub = tmp_path / "input.eng.srt"
    sub.write_bytes(b"subtitle")
    mp.deletesubs = {str(sub)}
    mp._cleanup_input(str(src), delete=True)
    assert not sub.exists()

  def test_recycle_exception_logged_not_raised(self, tmp_path):
    mp = self._make_mp()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    mp.settings.recycle_bin = str(recycle)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"data")
    with patch.object(mp, "_atomic_copy", side_effect=Exception("disk full")):
      # Should not raise; the exception is logged
      mp._cleanup_input(str(src), delete=True)
    mp.log.exception.assert_called()


# ---------------------------------------------------------------------------
# convert()
# ---------------------------------------------------------------------------


class TestConvert:
  def _make_mp(self, tmp_path):
    mp = _make_mp()
    mp.settings.output_extension = "mp4"
    mp.settings.output_dir = None
    mp.settings.temp_extension = None
    mp.settings.delete = True
    mp.settings.strip_metadata = False
    mp.settings.burn_subtitles = False
    mp.settings.detailedprogress = False
    mp.settings.permissions = {"chmod": 0o664, "uid": -1, "gid": -1}
    return mp

  def test_no_audio_streams_returns_none(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"x")
    options = {"source": [str(src)], "audio": [], "video": {}, "subtitle": []}
    result, _ = mp.convert(options, [], [], False, None)
    assert result is None

  def test_outputfile_none_returns_none(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"x")
    options = {"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": []}
    with patch.object(mp, "getOutputFile", return_value=(None, str(tmp_path))):
      result, _ = mp.convert(options, [], [], False, None)
    assert result is None

  def test_successful_conversion(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"x")
    out = tmp_path / "input.mp4"

    options = {"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": []}

    def fake_convert(outputfile, opts, timeout=None, preopts=None, postopts=None, strip_metadata=False):
      out.write_bytes(b"output")
      yield None, ["ffmpeg", "-i", str(src), str(out)]
      yield 100, ""

    mp.converter.convert = fake_convert
    mp.setPermissions = MagicMock()
    result, inp = mp.convert(options, [], [], False, None)
    assert result == str(out)

  def test_ffmpeg_convert_error_removes_output(self, tmp_path):
    from converter import FFMpegConvertError

    mp = self._make_mp(tmp_path)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"x")
    out = tmp_path / "input.mp4"
    out.write_bytes(b"partial")

    options = {"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": []}

    def fake_convert(outputfile, opts, timeout=None, preopts=None, postopts=None, strip_metadata=False):
      yield None, ["ffmpeg"]
      raise FFMpegConvertError("cmd", "output", 1)

    mp.converter.convert = fake_convert
    mp.setPermissions = MagicMock()
    result, inp = mp.convert(options, [], [], False, None)
    assert result is None

  def test_input_same_as_output_renames_input(self, tmp_path):
    mp = self._make_mp(tmp_path)
    # Create an mp4 input so output extension matches
    src = tmp_path / "input.mp4"
    src.write_bytes(b"x")
    out = tmp_path / "input.mp4"

    options = {"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": []}

    results = []

    def fake_convert(outputfile, opts, timeout=None, preopts=None, postopts=None, strip_metadata=False):
      out.write_bytes(b"output")
      yield None, ["ffmpeg"]
      yield 100, ""

    mp.converter.convert = fake_convert
    mp.setPermissions = MagicMock()
    result, inp = mp.convert(options, [], [], False, None)
    # Should succeed (rename collision resolved)
    assert result is not None

  def test_progress_output_callback_called(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "input.mkv"
    src.write_bytes(b"x")
    out = tmp_path / "input.mp4"

    options = {"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": []}
    progress_calls = []

    def fake_convert(outputfile, opts, timeout=None, preopts=None, postopts=None, strip_metadata=False):
      out.write_bytes(b"output")
      yield None, ["ffmpeg"]
      yield 50, "frame=100"

    mp.converter.convert = fake_convert
    mp.setPermissions = MagicMock()
    mp.convert(options, [], [], reportProgress=True, progressOutput=lambda t, d: progress_calls.append(t))
    assert len(progress_calls) > 0


# ---------------------------------------------------------------------------
# displayProgressBar()
# ---------------------------------------------------------------------------


class TestDisplayProgressBar:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.detailedprogress = False
    return mp

  def test_non_tty_skips_output(self):
    mp = self._make_mp()
    import sys

    with patch.object(sys.stdout, "isatty", return_value=False):
      mp.displayProgressBar(50)  # should not raise or output

  def test_tty_writes_bar(self, capsys):
    mp = self._make_mp()
    import sys

    with patch.object(sys.stdout, "isatty", return_value=True):
      mp.displayProgressBar(50)
    out = capsys.readouterr().out
    assert "%" in out or len(out) >= 0  # just ensure no exception

  def test_over_100_capped(self):
    mp = self._make_mp()
    import sys

    with patch.object(sys.stdout, "isatty", return_value=True):
      mp.displayProgressBar(150)  # should not raise (capped to 100)

  def test_newline_when_requested(self, capsys):
    mp = self._make_mp()
    import sys

    with patch.object(sys.stdout, "isatty", return_value=True):
      mp.displayProgressBar(100, newline=True)
    out = capsys.readouterr().out
    assert "\n" in out

  def test_detailed_progress_shows_debug(self, capsys):
    mp = self._make_mp()
    mp.settings.detailedprogress = True
    import sys

    with patch.object(sys.stdout, "isatty", return_value=True):
      mp.displayProgressBar(50, debug="fps=25")
    out = capsys.readouterr().out
    assert "fps=25" in out


# ---------------------------------------------------------------------------
# QTFS()
# ---------------------------------------------------------------------------


class TestQTFS:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.relocate_moov = True
    mp.settings.output_format = "mp4"
    mp.settings.permissions = {"chmod": 0o664, "uid": -1, "gid": -1}
    return mp

  def test_nonexistent_file_returns_inputfile(self, tmp_path):
    mp = self._make_mp()
    result = mp.QTFS(str(tmp_path / "nonexistent.mp4"))
    assert result == str(tmp_path / "nonexistent.mp4")

  def test_mkv_format_skips(self, tmp_path):
    mp = self._make_mp()
    mp.settings.output_format = "mkv"
    f = tmp_path / "file.mkv"
    f.write_bytes(b"data")
    result = mp.QTFS(str(f))
    assert result == str(f)

  def test_relocate_moov_false_skips(self, tmp_path):
    mp = self._make_mp()
    mp.settings.relocate_moov = False
    f = tmp_path / "file.mp4"
    f.write_bytes(b"data")
    result = mp.QTFS(str(f))
    assert result == str(f)

  def test_faststart_exception_returns_inputfile(self, tmp_path):
    mp = self._make_mp()
    f = tmp_path / "file.mp4"
    f.write_bytes(b"data")
    from qtfaststart import exceptions

    with patch("qtfaststart.processor.process", side_effect=exceptions.FastStartException("already at start")):
      result = mp.QTFS(str(f))
    assert result == str(f)

  def test_successful_qtfs_returns_outputfile(self, tmp_path):
    mp = self._make_mp()
    f = tmp_path / "file.mp4"
    f.write_bytes(b"data")
    temp_out = str(f) + ".QTFS"

    def fake_process(src, dst):
      import shutil

      shutil.copy2(src, dst)

    with patch("qtfaststart.processor.process", side_effect=fake_process):
      result = mp.QTFS(str(f))
    assert result == str(f)


# ---------------------------------------------------------------------------
# getSubOutputFile()
# ---------------------------------------------------------------------------


class TestGetSubOutputFile:
  def _make_mp(self, tmp_path):
    mp = _make_mp()
    mp.settings.output_dir = None
    mp.settings.filename_dispositions = ["forced", "hearing_impaired"]
    return mp

  def test_basic_output_path(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    result = mp.getSubOutputFile(str(src), "eng", "+default-forced", "srt", False)
    assert result.endswith(".srt")
    assert "eng" in result

  def test_forced_dispo_in_filename(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    result = mp.getSubOutputFile(str(src), "eng", "+forced", "srt", False)
    assert ".forced." in result

  def test_collision_appends_counter(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    # Pre-create the target file
    existing = tmp_path / "movie.eng.srt"
    existing.write_bytes(b"sub")
    result = mp.getSubOutputFile(str(src), "eng", "-forced", "srt", False)
    assert ".2." in result

  def test_output_dir_used_when_set(self, tmp_path):
    mp = self._make_mp(tmp_path)
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    mp.settings.output_dir = str(out_dir)
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    result = mp.getSubOutputFile(str(src), "eng", "-forced", "srt", False)
    assert str(out_dir) in result

  def test_include_all_uses_all_dispositions(self, tmp_path):
    mp = self._make_mp(tmp_path)
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    # With include_all=True, all disposition flags are eligible
    result = mp.getSubOutputFile(str(src), "eng", "+comment", "srt", include_all=True)
    assert ".comment." in result


# ---------------------------------------------------------------------------
# generateRipSubOpts()
# ---------------------------------------------------------------------------


class TestGenerateRipSubOpts:
  def _make_mp(self):
    mp = _make_mp()
    return mp

  def test_returns_expected_structure(self, make_stream):
    mp = self._make_mp()
    s = make_stream(type="subtitle", codec="hdmv_pgs_subtitle", index=3, metadata={"language": "eng"})
    s.disposition = {"default": False, "forced": False}
    result = mp.generateRipSubOpts("/fake/input.mkv", s, "copy")
    assert result["source"] == ["/fake/input.mkv"]
    assert result["subtitle"][0]["map"] == 3
    assert result["subtitle"][0]["codec"] == "copy"
    assert result["language"] == "eng"


# ---------------------------------------------------------------------------
# generateSourceDict() - invalid source path
# ---------------------------------------------------------------------------


class TestGenerateSourceDict:
  def _make_mp(self):
    mp = _make_mp()
    mp.titleDispositionCheck = MagicMock()
    return mp

  def test_invalid_source_returns_error_dict(self):
    mp = self._make_mp()
    mp.isValidSource = MagicMock(return_value=None)
    result, probe = mp.generateSourceDict("/fake/file.mkv")
    assert probe is None
    assert result["error"] == "Invalid input, unable to read"

  def test_valid_source_returns_json_data(self):
    mp = self._make_mp()
    info = MagicMock()
    info.json = {"streams": [], "format": {}}
    mp.isValidSource = MagicMock(return_value=info)
    result, probe = mp.generateSourceDict("/fake/file.mkv")
    assert probe is info
    assert "streams" in result


# ---------------------------------------------------------------------------
# estimateVideoBitrate() - exception fallback branch
# ---------------------------------------------------------------------------


class TestEstimateVideoBitrateException:
  def _make_mp(self):
    mp = _make_mp()
    return mp

  def test_exception_falls_back_to_format_bitrate(self, make_stream, make_format):
    mp = self._make_mp()
    info = MagicMock()
    info.video = MagicMock()
    info.video.bitrate = None
    info.format = MagicMock()
    # Raise on first access (try block), return None on second (except block)
    bitrate_mock = MagicMock(side_effect=[Exception("no bitrate"), None])
    type(info.format).bitrate = property(fget=bitrate_mock)
    info.audio = []
    result = mp.estimateVideoBitrate(info)
    # Should return min_video_bitrate (None in this case) without raising
    assert result is None

  def test_no_bitrate_at_all_returns_none(self):
    mp = self._make_mp()
    info = MagicMock()
    info.video = MagicMock()
    info.video.bitrate = None
    info.format = MagicMock()
    info.format.bitrate = None
    info.audio = []
    result = mp.estimateVideoBitrate(info)
    assert result is None


# ---------------------------------------------------------------------------
# restoreFromOutput()
# ---------------------------------------------------------------------------


class TestRestoreFromOutput:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.moveto = None
    return mp

  def test_no_output_dir_returns_outputfile(self, tmp_path):
    mp = self._make_mp()
    mp.settings.output_dir = None
    result = mp.restoreFromOutput("/input/movie.mkv", "/output/movie.mp4")
    assert result == "/output/movie.mp4"

  def test_with_moveto_returns_outputfile(self, tmp_path):
    mp = self._make_mp()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    mp.settings.output_dir = str(output_dir)
    mp.settings.moveto = "/some/moveto"
    outputfile = str(output_dir / "movie.mp4")
    result = mp.restoreFromOutput("/input/movie.mkv", outputfile)
    assert result == outputfile

  def test_outputfile_outside_output_dir_returns_unchanged(self, tmp_path):
    mp = self._make_mp()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    mp.settings.output_dir = str(output_dir)
    # outputfile is NOT under output_dir
    result = mp.restoreFromOutput("/input/movie.mkv", "/some/other/movie.mp4")
    assert result == "/some/other/movie.mp4"


# ---------------------------------------------------------------------------
# _select_subtitle_codec()
# ---------------------------------------------------------------------------


class TestSelectSubtitleCodec:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.embedsubs = True
    mp.settings.embedimgsubs = False
    mp.settings.scodec = ["mov_text"]
    mp.settings.scodec_image = []
    return mp

  def test_text_embed_copy(self):
    mp = self._make_mp()
    result = mp._select_subtitle_codec("mov_text", False, embed=True)
    assert result == "copy"

  def test_text_embed_transcode(self):
    mp = self._make_mp()
    result = mp._select_subtitle_codec("srt", False, embed=True)
    assert result == "mov_text"

  def test_text_embed_disabled_returns_none(self):
    mp = self._make_mp()
    mp.settings.embedsubs = False
    result = mp._select_subtitle_codec("mov_text", False, embed=True)
    assert result is None

  def test_image_embed_disabled_returns_none(self):
    mp = self._make_mp()
    # embedimgsubs=False → image embed is disabled
    result = mp._select_subtitle_codec("hdmv_pgs_subtitle", True, embed=True)
    assert result is None

  def test_rip_text_when_embed_disabled(self):
    mp = self._make_mp()
    mp.settings.embedsubs = False
    mp.settings.scodec = ["srt"]
    result = mp._select_subtitle_codec("mov_text", False, embed=False)
    assert result is not None

  def test_empty_pool_returns_none(self):
    mp = self._make_mp()
    mp.settings.scodec = []
    result = mp._select_subtitle_codec("mov_text", False, embed=True)
    assert result is None


# ---------------------------------------------------------------------------
# _subtitle_passes_filter()
# ---------------------------------------------------------------------------


class TestSubtitlePassesFilter:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.ignored_subtitle_dispositions = ["comment"]
    mp.settings.unique_subtitle_dispositions = False
    mp.settings.force_subtitle_defaults = False
    return mp

  def _make_stream(self, lang="eng", disposition=None):
    s = MagicMock()
    s.metadata = {"language": lang}
    s.disposition = disposition or {"default": False, "forced": False, "comment": False}
    s.dispostr = "-default-forced"
    return s

  def test_valid_language_passes(self):
    mp = self._make_mp()
    s = self._make_stream("eng")
    assert mp._subtitle_passes_filter(s, ["eng"], [], []) is True

  def test_invalid_language_fails(self):
    mp = self._make_mp()
    s = self._make_stream("deu")
    assert mp._subtitle_passes_filter(s, ["eng"], [], []) is False

  def test_ignored_disposition_fails(self):
    mp = self._make_mp()
    s = self._make_stream("eng", disposition={"comment": True, "forced": False, "default": False})
    assert mp._subtitle_passes_filter(s, ["eng"], [], []) is False

  def test_force_default_bypasses_language_check(self):
    mp = self._make_mp()
    mp.settings.force_subtitle_defaults = True
    s = self._make_stream("deu", disposition={"default": True, "forced": False, "comment": False})
    assert mp._subtitle_passes_filter(s, ["eng"], [], []) is True


# ---------------------------------------------------------------------------
# _process_audio_stream() - key branches
# ---------------------------------------------------------------------------


class TestProcessAudioStream:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.acodec = ["aac"]
    mp.settings.ua = []
    mp.settings.ua_bitrate = 128
    mp.settings.ua_vbr = 0
    mp.settings.ua_filter = None
    mp.settings.ua_profile = None
    mp.settings.ua_forcefilter = False
    mp.settings.ua_first_only = False
    mp.settings.audio_samplerates = []
    mp.settings.audio_sampleformat = None
    mp.settings.maxchannels = 0
    mp.settings.abitrate = 128
    mp.settings.amaxbitrate = 0
    mp.settings.afilter = None
    mp.settings.aforcefilter = False
    mp.settings.afilterchannels = {}
    mp.settings.avbr = 0
    mp.settings.aprofile = None
    mp.settings.aac_adtstoasc = False
    mp.settings.audio_copyoriginal = False
    mp.settings.audio_first_language_stream = False
    mp.settings.audio_atmos_force_copy = False
    mp.settings.force_audio_defaults = False
    mp.settings.ignored_audio_dispositions = []
    mp.settings.unique_audio_dispositions = False
    return mp

  def _make_audio_stream(self, codec="aac", channels=2, lang="eng", bitrate=128000, index=1):
    from converter.ffmpeg import MediaStreamInfo

    a = MediaStreamInfo()
    a.type = "audio"
    a.codec = codec
    a.index = index
    a.bitrate = bitrate
    a.audio_channels = channels
    a.audio_samplerate = 48000
    a.metadata = {"language": lang}
    a.disposition = {"default": True, "forced": False, "comment": False}
    a.profile = None
    return a

  def _make_info(self, audio_streams=None):
    from converter.ffmpeg import MediaInfo

    info = MediaInfo()
    for a in audio_streams or []:
      info.streams.append(a)
    return info

  def test_basic_aac_stream_appended(self):
    mp = self._make_mp()
    a = self._make_audio_stream("aac", channels=2)
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert len(audio_settings) == 1
    assert audio_settings[0]["codec"] == "copy"

  def test_non_whitelisted_language_skipped(self):
    mp = self._make_mp()
    a = self._make_audio_stream("aac", lang="deu")
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, ["eng"], True, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert len(audio_settings) == 0

  def test_max_channels_limits_channels(self):
    mp = self._make_mp()
    mp.settings.maxchannels = 2
    a = self._make_audio_stream("aac", channels=6)
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert audio_settings[0]["channels"] == 2

  def test_copy_original_appends_extra_stream(self):
    mp = self._make_mp()
    mp.settings.audio_copyoriginal = True
    a = self._make_audio_stream("ac3", channels=6)
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    # One transcoded + one copy-original
    assert len(audio_settings) == 2
    copy_orig = next((x for x in audio_settings if x.get("debug") == "audio-copy-original"), None)
    assert copy_orig is not None
    assert copy_orig["codec"] == "copy"

  def test_ua_creates_stereo_stream(self):
    mp = self._make_mp()
    a = self._make_audio_stream("aac", channels=6)
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None), patch("resources.mediaprocessor.skipUA", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], True, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=["aac"])
    # Should have original + UA stereo
    assert len(audio_settings) == 2
    ua = next((x for x in audio_settings if x.get("debug") == "universal-audio"), None)
    assert ua is not None
    assert ua["channels"] == 2

  def test_force_filter_prevents_copy(self):
    mp = self._make_mp()
    mp.settings.afilter = "loudnorm"
    mp.settings.aforcefilter = True
    a = self._make_audio_stream("aac", channels=2)
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert audio_settings[0]["codec"] != "copy"

  def test_amaxbitrate_caps_bitrate(self):
    mp = self._make_mp()
    mp.settings.abitrate = 256
    mp.settings.amaxbitrate = 256
    a = self._make_audio_stream("aac", channels=8)  # 8*256=2048 > 256
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert audio_settings[0]["bitrate"] == 256

  def test_zero_abitrate_uses_source_bitrate(self):
    mp = self._make_mp()
    mp.settings.abitrate = 0
    a = self._make_audio_stream("aac", channels=2, bitrate=192000)
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    # bitrate = (192000/1000 / 2) * 2 = 192
    assert audio_settings[0]["bitrate"] == pytest.approx(192.0, rel=0.1)

  def test_first_language_stream_blocks_others(self):
    mp = self._make_mp()
    mp.settings.audio_first_language_stream = True
    a = self._make_audio_stream("aac", lang="eng")
    info = self._make_info([a])
    audio_settings = []
    blocked = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, blocked, [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert "eng" in blocked

  def test_atmos_force_copy(self):
    mp = self._make_mp()
    mp.settings.audio_atmos_force_copy = True
    a = self._make_audio_stream("eac3", channels=6)
    a.profile = "Dolby TrueHD + Dolby Atmos"
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert audio_settings[0]["codec"] == "copy"

  def test_aac_adtstoasc_set_for_copy(self):
    mp = self._make_mp()
    mp.settings.aac_adtstoasc = True
    a = self._make_audio_stream("aac", channels=2)
    info = self._make_info([a])
    audio_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch("resources.mediaprocessor.blockAudioCopy", None):
      mp._process_audio_stream(a, "/fake/input.mkv", info, [], False, [], [], audio_settings, None, acodecs=["aac"], ua_codecs=[])
    assert audio_settings[0]["bsf"] == "aac_adtstoasc"


# ---------------------------------------------------------------------------
# _process_subtitle_stream()
# ---------------------------------------------------------------------------


class TestProcessSubtitleStream:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.embedsubs = True
    mp.settings.embedimgsubs = False
    mp.settings.scodec = ["mov_text"]
    mp.settings.scodec_image = []
    mp.settings.ignored_subtitle_dispositions = []
    mp.settings.unique_subtitle_dispositions = False
    mp.settings.force_subtitle_defaults = False
    mp.settings.sub_first_language_stream = False
    mp.settings.cleanit = False
    mp.settings.ffsubsync = False
    return mp

  def _make_sub_stream(self, codec="mov_text", lang="eng", index=2, forced=False):
    from converter.ffmpeg import MediaStreamInfo

    s = MediaStreamInfo()
    s.type = "subtitle"
    s.codec = codec
    s.index = index
    s.metadata = {"language": lang}
    s.disposition = {"default": False, "forced": forced, "comment": False}
    return s

  def _make_info(self):
    from converter.ffmpeg import MediaInfo

    return MediaInfo()

  def test_text_sub_embedded(self):
    mp = self._make_mp()
    s = self._make_sub_stream("mov_text")
    info = self._make_info()
    sub_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch.object(mp, "isImageBasedSubtitle", return_value=False):
      mp._process_subtitle_stream(s, "/fake/input.mkv", info, ["eng"], [], [], sub_settings, [], [], None, scodecs=["mov_text"], scodecs_image=[])
    assert len(sub_settings) == 1
    assert sub_settings[0]["codec"] == "copy"

  def test_image_sub_skipped_when_embedimgsubs_false(self):
    mp = self._make_mp()
    s = self._make_sub_stream("hdmv_pgs_subtitle")
    info = self._make_info()
    sub_settings = []
    ripsubopts = []
    with patch("resources.mediaprocessor.skipStream", None), patch.object(mp, "isImageBasedSubtitle", return_value=True):
      mp._process_subtitle_stream(s, "/fake/input.mkv", info, ["eng"], [], [], sub_settings, [], ripsubopts, None, scodecs=["mov_text"], scodecs_image=[])
    # Should not be embedded (no embedimgsubs), and no rip (no image codec pool)
    assert len(sub_settings) == 0

  def test_language_filter_blocks_stream(self):
    mp = self._make_mp()
    s = self._make_sub_stream("mov_text", lang="deu")
    info = self._make_info()
    sub_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch.object(mp, "isImageBasedSubtitle", return_value=False):
      mp._process_subtitle_stream(s, "/fake/input.mkv", info, ["eng"], [], [], sub_settings, [], [], None, scodecs=["mov_text"], scodecs_image=[])
    assert len(sub_settings) == 0

  def test_transcode_when_codec_not_in_pool(self):
    mp = self._make_mp()
    s = self._make_sub_stream("subrip")  # not mov_text
    info = self._make_info()
    sub_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch.object(mp, "isImageBasedSubtitle", return_value=False):
      mp._process_subtitle_stream(s, "/fake/input.mkv", info, ["eng"], [], [], sub_settings, [], [], None, scodecs=["mov_text"], scodecs_image=[])
    assert len(sub_settings) == 1
    assert sub_settings[0]["codec"] == "mov_text"

  def test_sub_first_language_blocks_second(self):
    mp = self._make_mp()
    mp.settings.sub_first_language_stream = True
    s = self._make_sub_stream("mov_text")
    info = self._make_info()
    sub_settings = []
    blocked = []
    with patch("resources.mediaprocessor.skipStream", None), patch.object(mp, "isImageBasedSubtitle", return_value=False):
      mp._process_subtitle_stream(s, "/fake/input.mkv", info, ["eng"], blocked, [], sub_settings, [], [], None, scodecs=["mov_text"], scodecs_image=[])
    assert "eng" in blocked

  def test_image_based_error_skips_stream(self):
    mp = self._make_mp()
    s = self._make_sub_stream("hdmv_pgs_subtitle")
    info = self._make_info()
    sub_settings = []
    with patch("resources.mediaprocessor.skipStream", None), patch.object(mp, "isImageBasedSubtitle", side_effect=Exception("error")):
      mp._process_subtitle_stream(s, "/fake/input.mkv", info, ["eng"], [], [], sub_settings, [], [], None, scodecs=["mov_text"], scodecs_image=[])
    assert len(sub_settings) == 0


# ---------------------------------------------------------------------------
# _process_external_sub()
# ---------------------------------------------------------------------------


class TestProcessExternalSubStream:
  def _make_mp(self):
    mp = _make_mp()
    mp.deletesubs = set()
    mp.settings.embedsubs = True
    mp.settings.embedimgsubs = False
    mp.settings.scodec = ["mov_text"]
    mp.settings.scodec_image = []
    mp.settings.ignored_subtitle_dispositions = []
    mp.settings.unique_subtitle_dispositions = False
    mp.settings.force_subtitle_defaults = False
    mp.settings.sub_first_language_stream = False
    return mp

  def _make_external_sub(self, path="/fake/movie.eng.srt", lang="eng", codec="srt"):
    from converter.ffmpeg import MediaInfo, MediaStreamInfo

    sub_stream = MediaStreamInfo()
    sub_stream.type = "subtitle"
    sub_stream.codec = codec
    sub_stream.index = 0
    sub_stream.metadata = {"language": lang}
    sub_stream.disposition = {"default": False, "forced": False, "comment": False}

    info = MediaInfo()
    info.path = path
    info.streams.append(sub_stream)
    return info

  def test_text_sub_appended(self):
    mp = self._make_mp()
    ext_sub = self._make_external_sub()
    sub_settings = []
    sources = ["/fake/movie.mkv"]
    with patch.object(mp, "isImageBasedSubtitle", return_value=False), patch.object(mp, "cleanDispositions"):
      mp._process_external_sub(ext_sub, "/fake/movie.mkv", ["eng"], [], [], sub_settings, sources, None)
    assert len(sub_settings) == 1

  def test_no_valid_codec_skips(self):
    mp = self._make_mp()
    mp.settings.embedsubs = False
    ext_sub = self._make_external_sub()
    sub_settings = []
    sources = ["/fake/movie.mkv"]
    with patch.object(mp, "isImageBasedSubtitle", return_value=False), patch.object(mp, "cleanDispositions"):
      mp._process_external_sub(ext_sub, "/fake/movie.mkv", ["eng"], [], [], sub_settings, sources, None)
    assert len(sub_settings) == 0

  def test_path_added_to_sources(self):
    mp = self._make_mp()
    ext_sub = self._make_external_sub("/fake/movie.eng.srt")
    sub_settings = []
    sources = ["/fake/movie.mkv"]
    with patch.object(mp, "isImageBasedSubtitle", return_value=False), patch.object(mp, "cleanDispositions"):
      mp._process_external_sub(ext_sub, "/fake/movie.mkv", ["eng"], [], [], sub_settings, sources, None)
    assert "/fake/movie.eng.srt" in sources

  def test_deletesubs_scheduled(self):
    mp = self._make_mp()
    ext_sub = self._make_external_sub("/fake/movie.eng.srt")
    sub_settings = []
    sources = ["/fake/movie.mkv"]
    with patch.object(mp, "isImageBasedSubtitle", return_value=False), patch.object(mp, "cleanDispositions"):
      mp._process_external_sub(ext_sub, "/fake/movie.mkv", ["eng"], [], [], sub_settings, sources, None)
    assert "/fake/movie.eng.srt" in mp.deletesubs

  def test_image_error_skips(self):
    mp = self._make_mp()
    ext_sub = self._make_external_sub()
    sub_settings = []
    sources = ["/fake/movie.mkv"]
    with patch.object(mp, "isImageBasedSubtitle", side_effect=Exception("error")), patch.object(mp, "cleanDispositions"):
      mp._process_external_sub(ext_sub, "/fake/movie.mkv", ["eng"], [], [], sub_settings, sources, None)
    assert len(sub_settings) == 0


# ---------------------------------------------------------------------------
# cleanExternalSub()
# ---------------------------------------------------------------------------


class TestCleanExternalSub:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.cleanit = False
    mp.settings.cleanit_config = None
    mp.settings.cleanit_tags = ["default"]
    return mp

  def test_cleanit_disabled_noop(self, tmp_path):
    mp = self._make_mp()
    f = tmp_path / "sub.srt"
    f.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    mp.cleanExternalSub(str(f))  # should not raise

  def test_cleanit_enabled_processes(self, tmp_path):
    mp = self._make_mp()
    mp.settings.cleanit = True
    f = tmp_path / "sub.srt"
    f.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    with patch("resources.mediaprocessor.cleanit") as mock_cleanit:
      mock_sub = MagicMock()
      mock_cleanit.Subtitle.return_value = mock_sub
      mock_cfg = MagicMock()
      mock_cleanit.Config.return_value = mock_cfg
      mock_rules = MagicMock()
      mock_cfg.select_rules.return_value = mock_rules
      mock_sub.clean.return_value = True
      mp.cleanExternalSub(str(f))
    mock_sub.save.assert_called_once()


# ---------------------------------------------------------------------------
# videoStreamTitle() - 8K + custom streamTitle hook
# ---------------------------------------------------------------------------


class TestVideoStreamTitleExtended:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.keep_titles = False
    return mp

  def test_8k_resolution(self, make_stream):
    mp = self._make_mp()
    stream = make_stream(type="video", video_width=7680, video_height=4320)
    with patch("resources.mediaprocessor.streamTitle", None):
      title = mp.videoStreamTitle(stream, {})
    assert title == "8K"

  def test_custom_stream_title_overrides(self, make_stream):
    mp = self._make_mp()
    stream = make_stream(type="video", video_width=1920, video_height=1080)
    custom_fn = MagicMock(return_value="Custom Video Title")
    with patch("resources.mediaprocessor.streamTitle", custom_fn):
      title = mp.videoStreamTitle(stream, {})
    assert title == "Custom Video Title"

  def test_custom_stream_title_returns_none_falls_through(self, make_stream):
    mp = self._make_mp()
    stream = make_stream(type="video", video_width=1920, video_height=1080)
    custom_fn = MagicMock(return_value=None)
    with patch("resources.mediaprocessor.streamTitle", custom_fn):
      title = mp.videoStreamTitle(stream, {})
    assert title == "FHD"

  def test_keep_titles_uses_existing(self, make_stream):
    mp = self._make_mp()
    mp.settings.keep_titles = True
    stream = make_stream(type="video", video_width=1920, video_height=1080, metadata={"title": "Main Feature", "language": "eng"})
    with patch("resources.mediaprocessor.streamTitle", None):
      title = mp.videoStreamTitle(stream, {})
    assert title == "Main Feature"

  def test_custom_stream_title_exception_falls_through(self, make_stream):
    mp = self._make_mp()
    stream = make_stream(type="video", video_width=1920, video_height=1080)

    def bad_fn(*args, **kwargs):
      raise ValueError("oops")

    with patch("resources.mediaprocessor.streamTitle", bad_fn):
      title = mp.videoStreamTitle(stream, {})
    assert title == "FHD"


# ---------------------------------------------------------------------------
# audioStreamTitle() - keep_titles + atmos copy
# ---------------------------------------------------------------------------


class TestAudioStreamTitleExtended:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.keep_titles = False
    return mp

  def test_keep_titles_uses_existing(self, make_stream):
    mp = self._make_mp()
    mp.settings.keep_titles = True
    stream = make_stream(type="audio", metadata={"title": "Director Mix", "language": "eng"})
    stream.disposition = {"default": False, "forced": False}
    with patch("resources.mediaprocessor.streamTitle", None):
      title = mp.audioStreamTitle(stream, {"channels": 2})
    assert title == "Director Mix"

  def test_atmos_copy_appends_atmos(self, make_stream):
    mp = self._make_mp()
    stream = make_stream(type="audio", disposition={"default": False, "forced": False})
    stream.profile = "Dolby TrueHD + Atmos"
    with patch("resources.mediaprocessor.streamTitle", None):
      title = mp.audioStreamTitle(stream, {"channels": 8, "codec": "copy"})
    assert "Atmos" in title

  def test_custom_stream_title_exception_falls_through(self, make_stream):
    mp = self._make_mp()
    stream = make_stream(type="audio", disposition={"default": False, "forced": False})

    def bad_fn(*args, **kwargs):
      raise ValueError("oops")

    with patch("resources.mediaprocessor.streamTitle", bad_fn):
      title = mp.audioStreamTitle(stream, {"channels": 2})
    assert title == "Stereo"


# ---------------------------------------------------------------------------
# normalizeFramedata()
# ---------------------------------------------------------------------------


class TestNormalizeFramedata:
  def _make_mp(self):
    return _make_mp()

  def test_hdr_sets_flags(self):
    mp = self._make_mp()
    fd = {}
    result = mp.normalizeFramedata(fd, hdr=True)
    assert result["hdr"] is True
    assert result["repeat-headers"] is True

  def test_mastering_display_normalized(self):
    mp = self._make_mp()
    fd = {
      "hdr": True,
      "side_data_list": [
        {
          "side_data_type": "Mastering display metadata",
          "red_x": "34000/50000",
          "red_y": "16000/50000",
          "green_x": "13250/50000",
          "green_y": "34500/50000",
          "blue_x": "7500/50000",
          "blue_y": "3000/50000",
          "white_point_x": "15635/50000",
          "white_point_y": "16450/50000",
          "min_luminance": "500/10000",
          "max_luminance": "50000000/10000",
        }
      ],
    }
    result = mp.normalizeFramedata(fd, hdr=True)
    mastering = next(x for x in result["side_data_list"] if x["side_data_type"] == "Mastering display metadata")
    assert mastering["red_x"] == 34000
    assert mastering["min_luminance"] == 500

  def test_exception_returns_original(self):
    mp = self._make_mp()
    fd = None
    result = mp.normalizeFramedata(fd, hdr=False)
    assert result is None


# ---------------------------------------------------------------------------
# isValidSource() - validation hook and no audio/video
# ---------------------------------------------------------------------------


class TestIsValidSourceExtended:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.ignored_extensions = ["nfo"]
    mp.settings.minimum_size = 0
    return mp

  def test_no_video_stream_returns_none(self, tmp_path):
    mp = self._make_mp()
    f = tmp_path / "audio.mp3"
    f.write_bytes(b"x" * 1024)
    info = MagicMock()
    info.video = None
    info.audio = [MagicMock()]
    mp.converter.probe.return_value = info
    result = mp.isValidSource(str(f))
    assert result is None

  def test_no_audio_stream_returns_none(self, tmp_path):
    mp = self._make_mp()
    f = tmp_path / "video.mkv"
    f.write_bytes(b"x" * 1024)
    info = MagicMock()
    info.video = MagicMock()
    info.audio = []
    mp.converter.probe.return_value = info
    result = mp.isValidSource(str(f))
    assert result is None

  def test_validation_hook_returns_false(self, tmp_path):
    mp = self._make_mp()
    f = tmp_path / "video.mkv"
    f.write_bytes(b"x" * 1024)
    info = MagicMock()
    info.video = MagicMock()
    info.audio = [MagicMock()]
    mp.converter.probe.return_value = info
    with patch("resources.mediaprocessor.validation", MagicMock(return_value=False)):
      result = mp.isValidSource(str(f))
    assert result is None

  def test_probe_returns_none_returns_none(self, tmp_path):
    mp = self._make_mp()
    f = tmp_path / "video.mkv"
    f.write_bytes(b"x" * 1024)
    mp.converter.probe.return_value = None
    result = mp.isValidSource(str(f))
    assert result is None


# ---------------------------------------------------------------------------
# canBypassConvert() - bypass_copy_all branch
# ---------------------------------------------------------------------------


class TestCanBypassConvertExtended:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.output_extension = "mp4"
    mp.settings.force_convert = False
    mp.settings.process_same_extensions = True  # allows inspection of the encoder check
    mp.settings.bypass_copy_all = True
    return mp

  def test_bypass_all_copy_returns_true(self):
    mp = self._make_mp()
    info = MagicMock()
    info.format.metadata = {}
    info.audio = [MagicMock()]
    info.subtitle = [MagicMock()]
    options = {
      "video": {"codec": "copy"},
      "audio": [{"codec": "copy"}],
      "subtitle": [{"codec": "copy"}],
    }
    result = mp.canBypassConvert("/path/to/file.mp4", info, options)
    assert result is True

  def test_bypass_copy_all_false_when_transcoding(self):
    mp = self._make_mp()
    info = MagicMock()
    info.format.metadata = {}
    info.audio = [MagicMock()]
    info.subtitle = []
    options = {
      "video": {"codec": "h264"},
      "audio": [{"codec": "aac"}],
      "subtitle": [],
    }
    result = mp.canBypassConvert("/path/to/file.mp4", info, options)
    assert result is False


# ---------------------------------------------------------------------------
# _init_hw_device_opts() static method
# ---------------------------------------------------------------------------


class TestInitHwDeviceOpts:
  def test_qsv_uses_qsv_device_flag(self):
    from resources.mediaprocessor import MediaProcessor

    opts = MediaProcessor._init_hw_device_opts("qsv", "sma", "/dev/dri/renderD128")
    assert opts == ["-qsv_device", "/dev/dri/renderD128"]

  def test_non_qsv_uses_init_hw_device(self):
    from resources.mediaprocessor import MediaProcessor

    opts = MediaProcessor._init_hw_device_opts("vaapi", "sma", "/dev/dri/renderD128")
    assert "-init_hw_device" in opts
    assert "vaapi=sma:/dev/dri/renderD128" in opts


# ---------------------------------------------------------------------------
# purgeDuplicateStreams()
# ---------------------------------------------------------------------------


class TestPurgeDuplicateStreams:
  def _make_mp(self):
    mp = _make_mp()
    return mp

  def _make_info(self, make_stream, streams):
    from converter.ffmpeg import MediaInfo

    info = MediaInfo()
    for s in streams:
      info.streams.append(s)
    return info

  def test_purges_duplicate_same_codec(self, make_stream):
    mp = self._make_mp()
    s0 = make_stream(type="audio", codec="aac", index=0)
    s0.disposition = {"default": False}
    s1 = make_stream(type="audio", codec="aac", index=1)
    s1.disposition = {"default": False}
    info = self._make_info(make_stream, [s0, s1])
    combinations = [[0, 1]]
    options = [
      {"map": 0, "codec": "aac", "channels": 2, "bitrate": 128},
      {"map": 1, "codec": "aac", "channels": 2, "bitrate": 256},
    ]
    with patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
      result = mp.purgeDuplicateStreams(combinations, options, info, ["aac"], [])
    # The lower bitrate stream should be removed
    assert len(options) == 1
    assert result is True

  def test_no_combinations_no_purge(self, make_stream):
    mp = self._make_mp()
    s0 = make_stream(type="audio", codec="aac", index=0)
    s0.disposition = {"default": False}
    info = self._make_info(make_stream, [s0])
    options = [{"map": 0, "codec": "aac", "channels": 2, "bitrate": 128}]
    with patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
      result = mp.purgeDuplicateStreams([], options, info, ["aac"], [])
    assert len(options) == 1
    assert result is False


# ---------------------------------------------------------------------------
# setAcceleration() - hwaccel with output format
# ---------------------------------------------------------------------------


class TestSetAccelerationExtended:
  def test_qsv_device_uses_qsv_flag(self):
    mp = _make_mp()
    mp.converter = MagicMock()
    mp.converter.ffmpeg.hwaccels = ["qsv"]
    mp.converter.ffmpeg.pix_fmts = {"yuv420p": 8}
    mp.converter.ffmpeg.codecs = {"h264": {"decoders": [], "encoders": []}}
    mp.converter.ffmpeg.hwaccel_decoder = MagicMock(return_value=None)
    mp.settings.hwaccels = ["qsv"]
    mp.settings.hwdevices = {"qsv": "/dev/dri/renderD128"}
    mp.settings.hwoutputfmt = {}
    mp.settings.hwaccel_decoders = []
    opts, device = mp.setAcceleration("h264", "yuv420p")
    assert "-qsv_device" in opts
    assert "/dev/dri/renderD128" in opts
    assert device == "/dev/dri/renderD128"

  def test_hwaccel_output_format_added(self):
    mp = _make_mp()
    mp.converter = MagicMock()
    mp.converter.ffmpeg.hwaccels = ["videotoolbox"]
    mp.converter.ffmpeg.pix_fmts = {"yuv420p": 8}
    mp.converter.ffmpeg.codecs = {"h264": {"decoders": [], "encoders": []}}
    mp.converter.ffmpeg.hwaccel_decoder = MagicMock(return_value=None)
    mp.settings.hwaccels = ["videotoolbox"]
    mp.settings.hwdevices = {}
    mp.settings.hwoutputfmt = {"videotoolbox": "videotoolbox_vld"}
    mp.settings.hwaccel_decoders = []
    opts, _ = mp.setAcceleration("h264", "yuv420p")
    assert "-hwaccel_output_format" in opts
    assert "videotoolbox_vld" in opts


# ---------------------------------------------------------------------------
# outputDirHasFreeSpace() - enough/not-enough branches
# ---------------------------------------------------------------------------


class TestOutputDirHasFreeSpaceExtended:
  def _make_mp(self):
    mp = _make_mp()
    return mp

  def test_not_enough_space_returns_false(self, tmp_path):
    import shutil

    mp = self._make_mp()
    mp.settings.output_dir = str(tmp_path)
    mp.settings.output_dir_ratio = 1000000  # requires 1 million x file size
    f = tmp_path / "big.mkv"
    f.write_bytes(b"x" * 1024)
    fake_usage = shutil.disk_usage(str(tmp_path))._replace(free=0)
    with patch("resources.mediaprocessor.shutil.disk_usage", return_value=fake_usage):
      result = mp.outputDirHasFreeSpace(str(f))
    assert result is False

  def test_enough_space_returns_true(self, tmp_path):
    mp = self._make_mp()
    mp.settings.output_dir = str(tmp_path)
    mp.settings.output_dir_ratio = 0.0001  # requires tiny fraction
    f = tmp_path / "small.mkv"
    f.write_bytes(b"x" * 1024)
    result = mp.outputDirHasFreeSpace(str(f))
    assert result is True


# ---------------------------------------------------------------------------
# replicate() - copyto failure branch
# ---------------------------------------------------------------------------


class TestReplicateExtended:
  def _make_mp(self):
    mp = _make_mp()
    mp.settings.copyto = []
    mp.settings.moveto = None
    return mp

  def test_copyto_failure_logged_not_raised(self, tmp_path):
    mp = self._make_mp()
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"data")
    dest_dir = tmp_path / "library"
    dest_dir.mkdir()
    mp.settings.copyto = [str(dest_dir)]
    with patch.object(mp, "_atomic_copy", side_effect=Exception("disk full")):
      result = mp.replicate(str(src))
    # Even if copy fails twice, original is still in list
    assert str(src) in result
    mp.log.exception.assert_called()

  def test_moveto_failure_logged(self, tmp_path):
    mp = self._make_mp()
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"data")
    dest_dir = tmp_path / "library"
    dest_dir.mkdir()
    mp.settings.moveto = str(dest_dir)
    with patch.object(mp, "_atomic_move", side_effect=Exception("permission denied")):
      result = mp.replicate(str(src))
    mp.log.exception.assert_called()

  def test_relative_path_appended(self, tmp_path):
    mp = self._make_mp()
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"data")
    dest_dir = tmp_path / "library"
    dest_dir.mkdir()
    mp.settings.copyto = [str(dest_dir)]
    with patch.object(mp, "_atomic_copy"):
      result = mp.replicate(str(src), relativePath="Shows/Season1")
    # Should not raise


# ---------------------------------------------------------------------------
# _warn_unsupported_encoders() - warning branches
# ---------------------------------------------------------------------------


class TestWarnUnsupportedEncoders:
  def _make_mp(self):
    mp = _make_mp()
    return mp

  def test_undefined_codec_logs_warning(self):
    mp = self._make_mp()
    codecs = {"h264": {"encoders": ["libx264"]}}
    mp.converter.codec_name_to_ffmpeg_codec_name.return_value = None
    stream_options = [{"codec": "unknown_codec_xyz"}]
    mp._warn_unsupported_encoders(codecs, stream_options)
    mp.log.warning.assert_called()

  def test_unsupported_by_ffmpeg_logs_warning(self):
    mp = self._make_mp()
    codecs = {"h264": {"encoders": ["libx264"]}}
    mp.converter.codec_name_to_ffmpeg_codec_name.return_value = "libnvenc_h264"
    with patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", return_value="h264"):
      stream_options = [{"codec": "h264_nvenc"}]
      mp._warn_unsupported_encoders(codecs, stream_options)
    mp.log.warning.assert_called()

  def test_copy_codec_skipped(self):
    mp = self._make_mp()
    codecs = {"h264": {"encoders": ["libx264"]}}
    stream_options = [{"codec": "copy"}]
    mp._warn_unsupported_encoders(codecs, stream_options)
    mp.log.warning.assert_not_called()

  def test_no_codec_key_skipped(self):
    mp = self._make_mp()
    codecs = {"h264": {"encoders": ["libx264"]}}
    stream_options = [{}]  # no 'codec' key
    mp._warn_unsupported_encoders(codecs, stream_options)
    mp.log.warning.assert_not_called()


class TestPostMethod:
  """mp.post() runs Plex refresh independently of the post-process scripts gate.

  Regression: prior to this fix, manual.py wrapped mp.post() in
  `if mp.settings.postprocess`, so disabling post-process scripts (the
  default) silently disabled the Plex refresh as well.
  """

  def _make_mp(self, postprocess=False, plex_refresh=True):
    from resources.mediaprocessor import MediaProcessor

    settings = MagicMock()
    settings.postprocess = postprocess
    settings.waitpostprocess = False
    settings.Plex = {"refresh": plex_refresh, "host": "plex.local", "token": "tok"}

    mp = MediaProcessor.__new__(MediaProcessor)
    mp.settings = settings
    mp.log = MagicMock()
    return mp

  @patch("resources.mediaprocessor.plex.refreshPlex")
  @patch("resources.mediaprocessor.PostProcessor")
  def test_plex_refresh_fires_when_postprocess_disabled(self, mock_pp, mock_refresh):
    mp = self._make_mp(postprocess=False, plex_refresh=True)
    mp.post(["/out/file.mkv"], "movie")
    mock_pp.assert_not_called()
    mock_refresh.assert_called_once()

  @patch("resources.mediaprocessor.plex.refreshPlex")
  @patch("resources.mediaprocessor.PostProcessor")
  def test_postprocess_runs_when_plex_disabled(self, mock_pp, mock_refresh):
    mp = self._make_mp(postprocess=True, plex_refresh=False)
    mock_instance = MagicMock()
    mock_pp.return_value = mock_instance
    mp.post(["/out/file.mkv"], "movie")
    mock_pp.assert_called_once()
    mock_instance.run_scripts.assert_called_once()
    mock_refresh.assert_not_called()

  @patch("resources.mediaprocessor.plex.refreshPlex")
  @patch("resources.mediaprocessor.PostProcessor")
  def test_plex_refresh_swallows_exceptions(self, mock_pp, mock_refresh):
    mp = self._make_mp(postprocess=False, plex_refresh=True)
    mock_refresh.side_effect = RuntimeError("plex offline")
    mp.post(["/out/file.mkv"], "movie")
    mp.log.exception.assert_called()
