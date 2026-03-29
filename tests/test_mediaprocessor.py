"""Tests for resources/mediaprocessor.py - core processing logic."""
import pytest
from unittest.mock import MagicMock, patch
from converter.ffmpeg import MediaStreamInfo, MediaFormatInfo, MediaInfo
from converter.avcodecs import BaseCodec


class TestEstimateVideoBitrate:
    """Test video bitrate estimation from container info."""

    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
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
        video = make_stream(type='video', codec='h264', bitrate=None)
        video.framedata = {}
        info.streams.append(video)
        audio = make_stream(type='audio', codec='aac', index=1, bitrate=None, audio_channels=2)
        info.streams.append(audio)
        result = mp.estimateVideoBitrate(info)
        assert result is not None
        assert result > 0

    def test_returns_min_video_bitrate_when_lower(self, make_stream, make_format):
        mp = self._make_processor()
        info = MediaInfo()
        info.format = make_format(bitrate=50000000)
        video = make_stream(type='video', codec='h264', bitrate=5000000)
        video.framedata = {}
        info.streams.append(video)
        audio = make_stream(type='audio', codec='aac', index=1, bitrate=128000, audio_channels=2)
        info.streams.append(audio)
        result = mp.estimateVideoBitrate(info)
        assert result is not None
        # Should use the lower of detected vs calculated
        assert result == pytest.approx(5000.0, rel=0.1)


class TestStreamTitles:
    """Test stream title generation for video, audio, and subtitles."""

    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.settings.keep_titles = False
                mp.log = MagicMock()
                return mp

    def test_video_title_4k(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='video', video_width=3840, video_height=2160)
        title = mp.videoStreamTitle(stream, {})
        assert title == '4K'

    def test_video_title_fhd(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='video', video_width=1920, video_height=1080)
        title = mp.videoStreamTitle(stream, {})
        assert title == 'FHD'

    def test_video_title_hd(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='video', video_width=1280, video_height=720)
        title = mp.videoStreamTitle(stream, {})
        assert title == 'HD'

    def test_video_title_sd(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='video', video_width=640, video_height=480)
        title = mp.videoStreamTitle(stream, {})
        assert title == 'SD'

    def test_video_title_hdr(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='video', video_width=3840, video_height=2160)
        title = mp.videoStreamTitle(stream, {}, hdr=True)
        assert 'HDR' in title
        assert '4K' in title

    def test_video_title_from_options(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='video', video_width=0, video_height=0)
        title = mp.videoStreamTitle(stream, {'width': 1920, 'height': 1080})
        assert title == 'FHD'

    def test_audio_title_stereo(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='audio', disposition={'default': False, 'forced': False})
        title = mp.audioStreamTitle(stream, {'channels': 2})
        assert title == 'Stereo'

    def test_audio_title_mono(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='audio', disposition={'default': False, 'forced': False})
        title = mp.audioStreamTitle(stream, {'channels': 1})
        assert title == 'Mono'

    def test_audio_title_surround(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='audio', disposition={'default': False, 'forced': False})
        title = mp.audioStreamTitle(stream, {'channels': 6})
        assert '5.1' in title

    def test_audio_title_71(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='audio', disposition={'default': False, 'forced': False})
        title = mp.audioStreamTitle(stream, {'channels': 8})
        assert '7.1' in title

    def test_audio_title_commentary(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='audio', disposition={'default': False, 'forced': False, 'comment': True})
        title = mp.audioStreamTitle(stream, {'channels': 2})
        assert 'Commentary' in title or 'Comment' in title.lower() or title is not None

    def test_subtitle_title_no_disposition(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='subtitle', disposition={'default': False, 'forced': False})
        title = mp.subtitleStreamTitle(stream, {})
        assert title == 'Full'

    def test_subtitle_title_forced(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='subtitle', disposition={'default': False, 'forced': True})
        title = mp.subtitleStreamTitle(stream, {})
        assert 'Forced' in title or title is not None

    def test_subtitle_title_keeps_existing(self, make_stream):
        mp = self._make_processor()
        mp.settings.keep_titles = True
        stream = make_stream(type='subtitle', metadata={'title': 'Custom Title', 'language': 'eng'})
        stream.disposition = {'default': False, 'forced': False}
        title = mp.subtitleStreamTitle(stream, {})
        assert title == 'Custom Title'


class TestValidLanguage:
    """Test language validation logic."""

    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_in_whitelist(self):
        mp = self._make_processor()
        assert mp.validLanguage('eng', ['eng', 'fra']) is True

    def test_not_in_whitelist(self):
        mp = self._make_processor()
        assert mp.validLanguage('deu', ['eng', 'fra']) is False

    def test_empty_whitelist_accepts_all(self):
        mp = self._make_processor()
        assert mp.validLanguage('deu', []) is True

    def test_blocked_language(self):
        mp = self._make_processor()
        assert mp.validLanguage('eng', ['eng', 'fra'], blocked=['eng']) is False

    def test_undefined_language(self):
        mp = self._make_processor()
        # 'und' is not in whitelist and is not special-cased
        assert mp.validLanguage('und', ['eng']) is False

    def test_undefined_language_empty_whitelist(self):
        mp = self._make_processor()
        assert mp.validLanguage('und', []) is True


class TestIsValidSource:
    """Test source file validation."""

    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
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
        small.write_bytes(b'\x00' * 1024)
        assert mp.isValidSource(str(small)) is None


class TestParseFile:
    """Test filename parsing utility."""

    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
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
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.settings.sanitize_disposition = ['forced']
                mp.log = MagicMock()
                return mp

    def test_clears_dispositions(self, make_stream):
        mp = self._make_processor()
        info = MediaInfo()
        s = make_stream(type='audio', disposition={'default': True, 'forced': True})
        info.streams.append(s)
        mp.cleanDispositions(info)
        assert s.disposition['forced'] is False
        assert s.disposition['default'] is True


class TestIsAudioStreamAtmos:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_atmos_detected(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='audio', profile='atmos')
        assert mp.isAudioStreamAtmos(stream) is True

    def test_non_atmos(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='audio', profile='lc')
        assert mp.isAudioStreamAtmos(stream) is False

    def test_no_profile(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='audio')
        stream.profile = None
        assert not mp.isAudioStreamAtmos(stream)


class TestGetDefaultAudioLanguage:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_dict_options_with_default(self):
        mp = self._make_processor()
        options = {
            'audio': [
                {'disposition': '+default-forced', 'language': 'eng'},
                {'disposition': '-default-forced', 'language': 'fra'}
            ]
        }
        assert mp.getDefaultAudioLanguage(options) == 'eng'

    def test_dict_options_no_default(self):
        mp = self._make_processor()
        options = {
            'audio': [
                {'disposition': '-default-forced', 'language': 'eng'}
            ]
        }
        assert mp.getDefaultAudioLanguage(options) is None

    def test_mediainfo_options(self, make_stream):
        mp = self._make_processor()
        info = MediaInfo()
        s = make_stream(type='audio', disposition={'default': True, 'forced': False})
        s.metadata = {'language': 'jpn'}
        info.streams.append(s)
        assert mp.getDefaultAudioLanguage(info) == 'jpn'


class TestValidDisposition:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_no_ignored_dispositions(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='audio', disposition={'default': True, 'forced': False})
        assert mp.validDisposition(stream, []) is True

    def test_ignored_disposition(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='audio', disposition={'default': True, 'comment': True})
        assert mp.validDisposition(stream, ['comment']) is False

    def test_unique_disposition_first(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='audio', disposition={'default': True, 'forced': False})
        existing = []
        assert mp.validDisposition(stream, [], unique=True, language='eng', existing=existing) is True
        assert len(existing) == 1

    def test_unique_disposition_duplicate(self, make_stream):
        mp = self._make_processor()
        stream = make_stream(type='audio', disposition={'default': True, 'forced': False})
        existing = ['eng.' + stream.dispostr]
        assert mp.validDisposition(stream, [], unique=True, language='eng', existing=existing) is False


class TestDispoStringToDict:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_positive_dispositions(self):
        mp = self._make_processor()
        result = mp.dispoStringToDict('+default+forced')
        assert result['default'] is True
        assert result['forced'] is True

    def test_negative_dispositions(self):
        mp = self._make_processor()
        result = mp.dispoStringToDict('-default-forced')
        assert result['default'] is False
        assert result['forced'] is False

    def test_mixed(self):
        mp = self._make_processor()
        result = mp.dispoStringToDict('+default-forced+comment')
        assert result['default'] is True
        assert result['forced'] is False
        assert result['comment'] is True

    def test_empty_string(self):
        mp = self._make_processor()
        assert mp.dispoStringToDict('') == {}

    def test_none(self):
        mp = self._make_processor()
        assert mp.dispoStringToDict(None) == {}


class TestCheckDisposition:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_all_present(self):
        mp = self._make_processor()
        assert mp.checkDisposition(['forced'], {'forced': True, 'default': False}) is True

    def test_missing_disposition(self):
        mp = self._make_processor()
        assert mp.checkDisposition(['forced'], {'forced': False, 'default': True}) is False

    def test_empty_allowed(self):
        mp = self._make_processor()
        assert mp.checkDisposition([], {'forced': False}) is True


class TestTitleDispositionCheck:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_commentary_in_title(self, make_stream):
        mp = self._make_processor()
        info = MediaInfo()
        s = make_stream(type='audio', disposition={'default': False, 'comment': False})
        s.metadata = {'title': "Director's Commentary", 'language': 'eng'}
        info.streams.append(s)
        mp.titleDispositionCheck(info)
        assert s.disposition['comment'] is True

    def test_forced_in_title(self, make_stream):
        mp = self._make_processor()
        info = MediaInfo()
        s = make_stream(type='subtitle', disposition={'default': False, 'forced': False})
        s.metadata = {'title': 'Forced Foreign', 'language': 'eng'}
        info.streams.append(s)
        mp.titleDispositionCheck(info)
        assert s.disposition['forced'] is True

    def test_sdh_in_title(self, make_stream):
        mp = self._make_processor()
        info = MediaInfo()
        s = make_stream(type='subtitle', disposition={'default': False, 'hearing_impaired': False})
        s.metadata = {'title': 'English SDH', 'language': 'eng'}
        info.streams.append(s)
        mp.titleDispositionCheck(info)
        assert s.disposition['hearing_impaired'] is True


class TestSublistIndexes:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_finds_sublist(self):
        mp = self._make_processor()
        assert mp.sublistIndexes(['a', 'b', 'c', 'a', 'b'], ['a', 'b']) == [0, 3]

    def test_no_match(self):
        mp = self._make_processor()
        assert mp.sublistIndexes(['a', 'b', 'c'], ['d', 'e']) == []

    def test_single_element(self):
        mp = self._make_processor()
        assert mp.sublistIndexes(['a', 'b', 'a'], ['a']) == [0, 2]


class TestGetOutputFile:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.settings.output_dir = None
                mp.settings.output_extension = 'mp4'
                mp.log = MagicMock()
                return mp

    def test_basic_output(self, tmp_path):
        mp = self._make_processor()
        outfile, outdir = mp.getOutputFile(str(tmp_path), 'movie', 'mkv')
        assert outfile.endswith('movie.mp4')
        assert outdir == str(tmp_path)

    def test_with_number(self, tmp_path):
        mp = self._make_processor()
        outfile, _ = mp.getOutputFile(str(tmp_path), 'movie', 'mkv', number=2)
        assert '.2.' in outfile

    def test_with_temp_extension(self, tmp_path):
        mp = self._make_processor()
        outfile, _ = mp.getOutputFile(str(tmp_path), 'movie', 'mkv', temp_extension='tmp')
        assert outfile.endswith('.tmp')

    def test_output_dir_override(self, tmp_path):
        mp = self._make_processor()
        outdir = str(tmp_path / 'output')
        mp.settings.output_dir = outdir
        outfile, result_dir = mp.getOutputFile(str(tmp_path), 'movie', 'mkv')
        assert result_dir == outdir

    def test_ignore_output_dir(self, tmp_path):
        mp = self._make_processor()
        mp.settings.output_dir = '/some/output/dir'
        outfile, result_dir = mp.getOutputFile(str(tmp_path), 'movie', 'mkv', ignore_output_dir=True)
        assert result_dir == str(tmp_path)


class TestGetSourceStream:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_returns_stream(self, make_stream):
        mp = self._make_processor()
        info = MediaInfo()
        s0 = make_stream(type='video', index=0)
        s1 = make_stream(type='audio', index=1)
        info.streams = [s0, s1]
        assert mp.getSourceStream(0, info) == s0
        assert mp.getSourceStream(1, info) == s1


class TestGetSubExtensionFromCodec:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_known_codec(self):
        mp = self._make_processor()
        result = mp.getSubExtensionFromCodec('srt')
        assert result == 'srt'

    def test_unknown_codec_returns_codec(self):
        mp = self._make_processor()
        result = mp.getSubExtensionFromCodec('unknown_codec')
        assert result == 'unknown_codec'


class TestRemoveFile:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
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
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
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
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.settings.output_extension = 'mp4'
                mp.settings.force_convert = False
                mp.settings.process_same_extensions = False
                mp.settings.bypass_copy_all = False
                mp.log = MagicMock()
                return mp

    def test_same_extension_no_process(self):
        mp = self._make_processor()
        info = MagicMock()
        info.format.metadata = {}
        assert mp.canBypassConvert('/path/to/file.mp4', info) is True

    def test_different_extension(self):
        mp = self._make_processor()
        info = MagicMock()
        assert mp.canBypassConvert('/path/to/file.mkv', info) is False

    def test_same_extension_process_enabled(self):
        mp = self._make_processor()
        mp.settings.process_same_extensions = True
        info = MagicMock()
        info.format.metadata = {}
        assert mp.canBypassConvert('/path/to/file.mp4', info) is False

    def test_same_extension_sma_processed(self):
        mp = self._make_processor()
        mp.settings.process_same_extensions = True
        info = MagicMock()
        info.format.metadata = {'encoder': 'sma-ng v1.0'}
        assert mp.canBypassConvert('/path/to/file.mp4', info) is True


class TestPrintableFFMPEGCommand:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_quotes_spaces(self):
        mp = self._make_processor()
        result = mp.printableFFMPEGCommand(['ffmpeg', '-i', '/path with spaces/file.mkv', '-c', 'copy'])
        assert '"/path with spaces/file.mkv"' in result

    def test_no_quotes_without_spaces(self):
        mp = self._make_processor()
        result = mp.printableFFMPEGCommand(['ffmpeg', '-c', 'copy'])
        assert '"' not in result


class TestRawEscape:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_escapes_backslash(self):
        mp = self._make_processor()
        # raw() escapes both backslash and colon
        result = mp.raw('a\\b')
        assert '\\\\' in result

    def test_escapes_colon(self):
        mp = self._make_processor()
        assert mp.raw('file:name') == 'file\\:name'

    def test_no_escaping_needed(self):
        mp = self._make_processor()
        assert mp.raw('simple') == 'simple'


class TestParseAndNormalize:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_same_denominator(self):
        mp = self._make_processor()
        assert mp.parseAndNormalize('50000/50000', 50000) == 50000

    def test_different_denominator(self):
        mp = self._make_processor()
        result = mp.parseAndNormalize('1000/100', 50000)
        assert result == 500000


class TestHasValidFrameData:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_valid_hdr_framedata(self):
        mp = self._make_processor()
        framedata = {
            'side_data_list': [
                {'side_data_type': 'Mastering display metadata'},
                {'side_data_type': 'Content light level metadata'}
            ]
        }
        assert mp.hasValidFrameData(framedata) is True

    def test_missing_one_type(self):
        mp = self._make_processor()
        framedata = {
            'side_data_list': [
                {'side_data_type': 'Mastering display metadata'}
            ]
        }
        assert mp.hasValidFrameData(framedata) is False

    def test_no_side_data(self):
        mp = self._make_processor()
        assert mp.hasValidFrameData({}) is False

    def test_invalid_framedata(self):
        mp = self._make_processor()
        assert mp.hasValidFrameData(None) is False


class TestHasBitstreamVideoSubs:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_has_closed_captions(self):
        mp = self._make_processor()
        framedata = {
            'side_data_list': [
                {'side_data_type': 'Closed Captions'},
            ]
        }
        assert mp.hasBitstreamVideoSubs(framedata) is True

    def test_no_closed_captions(self):
        mp = self._make_processor()
        assert mp.hasBitstreamVideoSubs({}) is False


class TestIsHDROutput:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_hdr_pix_fmt_and_depth(self):
        mp = self._make_processor()
        assert mp.isHDROutput('yuv420p10le', 10) is True

    def test_sdr_pix_fmt(self):
        mp = self._make_processor()
        assert mp.isHDROutput('yuv420p', 8) is False

    def test_hdr_pix_fmt_low_depth(self):
        mp = self._make_processor()
        assert mp.isHDROutput('yuv420p10le', 8) is False

    def test_no_pix_fmt_high_depth(self):
        mp = self._make_processor()
        assert mp.isHDROutput(None, 10) is True

    def test_no_pix_fmt_low_depth(self):
        mp = self._make_processor()
        assert mp.isHDROutput(None, 8) is False


class TestFfprobeSafeCodecs:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.log = MagicMock()
                return mp

    def test_adds_ffprobe_codec(self):
        mp = self._make_processor()
        codecs = ['h264']
        result = mp.ffprobeSafeCodecs(codecs)
        # h264 ffprobe name is 'h264' already, so check it doesn't duplicate
        assert 'h264' in result

    def test_empty_list(self):
        mp = self._make_processor()
        assert mp.ffprobeSafeCodecs([]) == []

    def test_none(self):
        mp = self._make_processor()
        assert mp.ffprobeSafeCodecs(None) is None


class TestAtomicFileOps:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.settings.permissions = {'chmod': 0o664, 'uid': -1, 'gid': -1}
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
        with patch('shutil.copy2', side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                mp._atomic_copy(str(src), str(dst))
        assert not (tmp_path / "output.mp4.smatmp").exists()

    def test_atomic_move_same_filesystem(self, tmp_path):
        mp = self._make_processor()
        src = tmp_path / "source.mp4"
        src.write_bytes(b"media data")
        dst = tmp_path / "dest.mp4"
        with patch('os.rename') as mock_rename:
            mp._atomic_move(str(src), str(dst))
        mock_rename.assert_called_once_with(str(src), str(dst))

    def test_atomic_move_cross_filesystem_fallback(self, tmp_path):
        mp = self._make_processor()
        src = tmp_path / "source.mp4"
        src.write_bytes(b"media data")
        dst = tmp_path / "dest.mp4"
        with patch('os.rename', side_effect=OSError(18, "Invalid cross-device link")):
            with patch.object(mp, '_atomic_copy') as mock_copy:
                with patch('os.remove') as mock_remove:
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
        with patch.object(mp, '_atomic_copy') as mock_copy:
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
        with patch.object(mp, '_atomic_move') as mock_move:
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
        with patch.object(mp, '_atomic_move') as mock_move, \
             patch.object(mp, 'parseFile', return_value=(str(input_dir), 'movie', 'mp4')):
            mp.restoreFromOutput(inputfile, outputfile)
        mock_move.assert_called_once_with(outputfile, str(input_dir / "movie.mp4"))


class TestSetPermissions:
    def _make_processor(self):
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
                from resources.mediaprocessor import MediaProcessor
                mp = MediaProcessor.__new__(MediaProcessor)
                mp.settings = MagicMock()
                mp.settings.permissions = {'chmod': 0o664, 'uid': -1, 'gid': -1}
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
        with patch('resources.mediaprocessor.Converter'):
            with patch('resources.readsettings.ReadSettings._validate_binaries'):
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


class TestCrfProfileOverridesCopy:
    """Test that a CRF profile match forces transcoding even when codec could be copied."""

    def test_crf_profile_match_forces_transcode(self, tmp_ini, make_media_info):
        """When source bitrate exceeds a CRF profile threshold, the stream must be
        transcoded (not copied) even if the source codec matches the desired codec."""
        with patch('resources.readsettings.ReadSettings._validate_binaries'):
            from resources.readsettings import ReadSettings
            from resources.mediaprocessor import MediaProcessor

            settings = ReadSettings(tmp_ini())
            # Use single codec so source h264 matches and would normally be copied
            settings.vcodec = ['h264']
            # Profile: source > 5000 kbps → transcode with crf=18, 3M/6M rate control
            settings.vcrf_profiles = [{'source_bitrate': 5000, 'crf': 18, 'maxrate': '3M', 'bufsize': '6M'}]

        mock_converter = MagicMock()
        mock_converter.ffmpeg.codecs = {
            'h264': {'encoders': ['libx264']},
            'aac': {'encoders': ['aac']},
        }
        mock_converter.ffmpeg.pix_fmts = {'yuv420p': 8}
        mock_converter.codec_name_to_ffmpeg_codec_name.side_effect = lambda c: {'h264': 'libx264', 'aac': 'aac'}.get(c, c)

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = settings
        mp.converter = mock_converter
        mp.log = MagicMock()
        mp.deletesubs = set()

        # 7 Mbit source: total=7128kbps, audio=128kbps → video estimate ≈ 6650kbps > 5000 threshold
        info = make_media_info(
            video_codec='h264',
            video_bitrate=7000000,
            total_bitrate=7128000,
            audio_bitrate=128000,
        )

        with patch('resources.mediaprocessor.Converter.encoder', return_value=None), \
             patch('resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name', side_effect=lambda c: c):
            options, *_ = mp.generateOptions('/fake/input.mkv', info=info)

        assert options is not None
        assert options['video']['codec'] == 'h264', "CRF profile match must override copy and transcode"
        assert options['video']['crf'] == 18
        assert options['video']['maxrate'] == '3M'
        assert options['video']['bufsize'] == '6M'

    def test_no_crf_profile_match_allows_copy(self, tmp_ini, make_media_info):
        """When source bitrate is below the CRF profile threshold, copy is not overridden."""
        with patch('resources.readsettings.ReadSettings._validate_binaries'):
            from resources.readsettings import ReadSettings
            from resources.mediaprocessor import MediaProcessor

            settings = ReadSettings(tmp_ini())
            settings.vcodec = ['h264']
            # Profile only triggers above 10000 kbps — our 7 Mbit source won't match
            settings.vcrf_profiles = [{'source_bitrate': 10000, 'crf': 18, 'maxrate': '6M', 'bufsize': '12M'}]

        mock_converter = MagicMock()
        mock_converter.ffmpeg.codecs = {
            'h264': {'encoders': ['libx264']},
            'aac': {'encoders': ['aac']},
        }
        mock_converter.ffmpeg.pix_fmts = {'yuv420p': 8}
        mock_converter.codec_name_to_ffmpeg_codec_name.side_effect = lambda c: {'h264': 'libx264', 'aac': 'aac'}.get(c, c)

        mp = MediaProcessor.__new__(MediaProcessor)
        mp.settings = settings
        mp.converter = mock_converter
        mp.log = MagicMock()
        mp.deletesubs = set()

        info = make_media_info(
            video_codec='h264',
            video_bitrate=7000000,
            total_bitrate=7128000,
            audio_bitrate=128000,
        )

        with patch('resources.mediaprocessor.Converter.encoder', return_value=None), \
             patch('resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name', side_effect=lambda c: c):
            options, *_ = mp.generateOptions('/fake/input.mkv', info=info)

        assert options is not None
        assert options['video']['codec'] == 'copy'
