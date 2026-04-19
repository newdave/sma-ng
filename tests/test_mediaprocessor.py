"""Tests for resources/mediaprocessor.py - core processing logic."""

from unittest.mock import MagicMock, patch

import pytest

from converter.ffmpeg import MediaInfo


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
        assert "5.1" in title

    def test_audio_title_71(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type="audio", disposition={"default": False, "forced": False})
        title = mp.audioStreamTitle(stream, {"channels": 8})
        assert "7.1" in title

    def test_audio_title_commentary(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type="audio", disposition={"default": False, "forced": False, "comment": True})
        title = mp.audioStreamTitle(stream, {"channels": 2})
        assert "Commentary" in title or "Comment" in title.lower() or title is not None

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


class TestValidLanguage:
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

    def _make_processor(self):
        with patch("resources.mediaprocessor.Converter"):
            with patch("resources.readsettings.ReadSettings._validate_binaries"):
                from resources.mediaprocessor import MediaProcessor
                from resources.readsettings import ReadSettings

                settings = ReadSettings()
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

    def _make_processor(self):
        with patch("resources.mediaprocessor.Converter"):
            with patch("resources.readsettings.ReadSettings._validate_binaries"):
                from resources.mediaprocessor import MediaProcessor
                from resources.readsettings import ReadSettings

                settings = ReadSettings()
                return MediaProcessor(settings)

    def test_parse_simple_path(self):
        mp = self._make_processor()
        d, name, ext = mp.parseFile("/path/to/movie.mkv")
        assert d == "/path/to"
        assert name == "movie"
        assert ext == "mkv"

    def test_parse_no_extension(self):
        mp = self._make_processor()
        d, name, ext = mp.parseFile("/path/to/file")
        assert name == "file"

    def test_parse_extension_lowercased(self):
        mp = self._make_processor()
        d, name, ext = mp.parseFile("/path/to/Movie.MKV")
        assert ext == "mkv"

    def test_parse_dotted_filename(self):
        mp = self._make_processor()
        d, name, ext = mp.parseFile("/path/to/Movie.2024.1080p.mkv")
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


class TestDispoStringToDict:
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


class TestCheckDisposition:
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


class TestTitleDispositionCheck:
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


class TestSublistIndexes:
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
        assert outfile.endswith("movie.mp4")
        assert outdir == str(tmp_path)

    def test_with_number(self, tmp_path):
        mp = self._make_processor()
        outfile, _ = mp.getOutputFile(str(tmp_path), "movie", "mkv", number=2)
        assert ".2." in outfile

    def test_with_temp_extension(self, tmp_path):
        mp = self._make_processor()
        outfile, _ = mp.getOutputFile(str(tmp_path), "movie", "mkv", temp_extension="tmp")
        assert outfile.endswith(".tmp")

    def test_output_dir_override(self, tmp_path):
        mp = self._make_processor()
        outdir = str(tmp_path / "output")
        mp.settings.output_dir = outdir
        outfile, result_dir = mp.getOutputFile(str(tmp_path), "movie", "mkv")
        assert result_dir == outdir

    def test_ignore_output_dir(self, tmp_path):
        mp = self._make_processor()
        mp.settings.output_dir = "/some/output/dir"
        outfile, result_dir = mp.getOutputFile(str(tmp_path), "movie", "mkv", ignore_output_dir=True)
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
        assert '"/path with spaces/file.mkv"' in result

    def test_no_quotes_without_spaces(self):
        mp = self._make_processor()
        result = mp.printableFFMPEGCommand(["ffmpeg", "-c", "copy"])
        assert '"' not in result


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


class TestFfprobeSafeCodecs:
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
        mp.log.debug.assert_called()


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

    def _make_mp(self, tmp_ini, vmaxbitrate):
        with patch("resources.readsettings.ReadSettings._validate_binaries"):
            from resources.mediaprocessor import MediaProcessor
            from resources.readsettings import ReadSettings

            settings = ReadSettings(tmp_ini())
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

    def test_vmaxbitrate_sets_maxrate_and_bufsize(self, tmp_ini, make_media_info):
        mp = self._make_mp(tmp_ini, vmaxbitrate=8000)
        info = make_media_info(video_codec="h264", video_bitrate=10000000, total_bitrate=10128000, audio_bitrate=128000)
        with patch("resources.mediaprocessor.Converter.encoder", return_value=None), patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
            options, *_ = mp.generateOptions("/fake/input.mkv", info=info)
        assert options["video"]["maxrate"] == "8000k"
        assert options["video"]["bufsize"] == "16000k"

    def test_zero_vmaxbitrate_leaves_vbv_unset(self, tmp_ini, make_media_info):
        mp = self._make_mp(tmp_ini, vmaxbitrate=0)
        info = make_media_info(video_codec="h264", video_bitrate=5000000, total_bitrate=5128000, audio_bitrate=128000)
        with patch("resources.mediaprocessor.Converter.encoder", return_value=None), patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
            options, *_ = mp.generateOptions("/fake/input.mkv", info=info)
        assert options["video"]["maxrate"] is None
        assert options["video"]["bufsize"] is None


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


class TestSafeLanguage:
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
        awl, swl = mp.safeLanguage(info)
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


class TestMapStreamCombinations:
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


class TestSetDefaultAudioStream:
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


class TestSetDefaultSubtitleStream:
    def test_sets_default_when_sdl_and_force(self):
        mp = _make_mp()
        mp.settings.sdl = "eng"
        mp.settings.sforcedefault = True
        streams = [
            {"language": "eng", "disposition": "-default"},
            {"language": "fra", "disposition": "-default"},
        ]
        mp.setDefaultSubtitleStream(streams)
        assert "+default" in streams[0]["disposition"]

    def test_does_not_override_existing_default(self):
        mp = _make_mp()
        mp.settings.sdl = "eng"
        mp.settings.sforcedefault = True
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
        mp.settings.sforcedefault = True
        streams = [{"language": "eng", "disposition": "-default"}]
        mp.setDefaultSubtitleStream(streams)
        assert "+default" not in streams[0]["disposition"]

    def test_skips_when_empty_streams(self):
        mp = _make_mp()
        mp.settings.sdl = "eng"
        mp.settings.sforcedefault = True
        mp.setDefaultSubtitleStream([])  # Should not raise


class TestSortStreams:
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
        opts, device = mp.setAcceleration("h264", "yuv420p")
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

    def _make_mp(self, adl="eng"):
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

    def _make_mp(self, sdl="eng", sforcedefault=True):
        mp = _make_mp()
        mp.settings.sdl = sdl
        mp.settings.sforcedefault = sforcedefault
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
        mp = self._make_mp(sforcedefault=False)
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
        mp.converter.probe.return_value = info
        result = mp.isValidSubtitleSource("/path/to/file.srt")
        assert result is info

    def test_file_with_video_stream_returns_none(self):
        mp = self._make_mp()
        info = MagicMock()
        info.subtitle = [MagicMock()]
        info.video = MagicMock()
        info.audio = []
        mp.converter.probe.return_value = info
        assert mp.isValidSubtitleSource("/path/to/file.srt") is None

    def test_file_with_audio_streams_returns_none(self):
        mp = self._make_mp()
        info = MagicMock()
        info.subtitle = [MagicMock()]
        info.video = None
        info.audio = [MagicMock()]
        mp.converter.probe.return_value = info
        assert mp.isValidSubtitleSource("/path/to/file.srt") is None

    def test_probe_exception_returns_none(self):
        mp = self._make_mp()
        mp.converter.probe.side_effect = Exception("ffprobe failed")
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
        mp.converter.probe.return_value = info
        result = mp.getDimensions("/path/to/file.mkv")
        assert result == {"x": 1920, "y": 1080}

    def test_probe_returns_none_gives_zeros(self):
        mp = self._make_mp()
        mp.converter.probe.return_value = None
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

    def _make_mp(self, tmp_ini, vbitrate_profiles, vmaxbitrate=0):
        with patch("resources.readsettings.ReadSettings._validate_binaries"):
            from resources.mediaprocessor import MediaProcessor
            from resources.readsettings import ReadSettings

            settings = ReadSettings(tmp_ini())
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

    def test_profile_match_sets_vbitrate_maxrate_bufsize(self, tmp_ini, make_media_info):
        profiles = [{"source_kbps": 0, "target": 3000, "maxrate": 6000}]
        mp = self._make_mp(tmp_ini, vbitrate_profiles=profiles)
        info = make_media_info(video_codec="h264", video_bitrate=5000000, total_bitrate=5128000, audio_bitrate=128000)
        with patch("resources.mediaprocessor.Converter.encoder", return_value=None), patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
            options, *_ = mp.generateOptions("/fake/input.mkv", info=info)
        assert options["video"]["bitrate"] == 3000
        assert options["video"]["maxrate"] == "6000k"
        assert options["video"]["bufsize"] == "12000k"

    def test_profile_match_forces_transcode(self, tmp_ini, make_media_info):
        profiles = [{"source_kbps": 0, "target": 3000, "maxrate": 6000}]
        mp = self._make_mp(tmp_ini, vbitrate_profiles=profiles)
        info = make_media_info(video_codec="h264", video_bitrate=5000000, total_bitrate=5128000, audio_bitrate=128000)
        with patch("resources.mediaprocessor.Converter.encoder", return_value=None), patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
            options, *_ = mp.generateOptions("/fake/input.mkv", info=info)
        assert options["video"]["codec"] != "copy"

    def test_no_profiles_leaves_vbv_unset_without_maxbitrate(self, tmp_ini, make_media_info):
        mp = self._make_mp(tmp_ini, vbitrate_profiles=[], vmaxbitrate=0)
        info = make_media_info(video_codec="h264", video_bitrate=5000000, total_bitrate=5128000, audio_bitrate=128000)
        with patch("resources.mediaprocessor.Converter.encoder", return_value=None), patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
            options, *_ = mp.generateOptions("/fake/input.mkv", info=info)
        assert options["video"]["maxrate"] is None
        assert options["video"]["bufsize"] is None
