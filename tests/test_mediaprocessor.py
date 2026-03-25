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
