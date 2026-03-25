"""Tests for converter/ffmpeg.py - stream info parsing and command generation."""
import pytest
from converter.ffmpeg import MediaStreamInfo, MediaFormatInfo, MediaInfo, FFMpeg


class TestMediaStreamInfoParsing:
    """Test static parse helpers."""

    def test_parse_float_valid(self):
        assert MediaStreamInfo.parse_float('23.976') == pytest.approx(23.976)

    def test_parse_float_invalid(self):
        assert MediaStreamInfo.parse_float('N/A', 0.0) == 0.0

    def test_parse_float_none(self):
        assert MediaStreamInfo.parse_float(None, -1.0) == -1.0

    def test_parse_int_valid(self):
        assert MediaStreamInfo.parse_int('1920') == 1920

    def test_parse_int_invalid(self):
        assert MediaStreamInfo.parse_int('N/A', 0) == 0

    def test_parse_bool_truthy(self):
        assert MediaStreamInfo.parse_bool(1) is True

    def test_parse_bool_default(self):
        assert MediaStreamInfo.parse_bool(None, False) is False


class TestMediaStreamInfoProperties:
    """Test stream info computed properties."""

    def test_dispostr_default_forced(self):
        s = MediaStreamInfo()
        s.disposition = {'default': True, 'forced': True}
        assert '+default' in s.dispostr
        assert '+forced' in s.dispostr

    def test_dispostr_negative(self):
        s = MediaStreamInfo()
        s.disposition = {'default': False, 'forced': False}
        assert '-default' in s.dispostr
        assert '-forced' in s.dispostr

    def test_dispostr_empty(self):
        s = MediaStreamInfo()
        s.disposition = {}
        assert s.dispostr == ''

    def test_json_video(self):
        s = MediaStreamInfo()
        s.index = 0
        s.type = 'video'
        s.codec = 'h264'
        s.pix_fmt = 'yuv420p'
        s.profile = 'main'
        s.fps = 23.976
        s.video_width = 1920
        s.video_height = 1080
        s.framedata = {}
        s.field_order = 'progressive'
        j = s.json
        assert j['codec'] == 'h264'
        assert j['dimensions'] == '1920x1080'
        assert j['pix_fmt'] == 'yuv420p'

    def test_json_audio(self):
        s = MediaStreamInfo()
        s.index = 1
        s.type = 'audio'
        s.codec = 'aac'
        s.audio_channels = 2
        s.audio_samplerate = 48000
        s.metadata = {'language': 'eng'}
        s.disposition = {'default': True, 'forced': False}
        j = s.json
        assert j['codec'] == 'aac'
        assert j['channels'] == 2
        assert j['language'] == 'eng'

    def test_json_subtitle(self):
        s = MediaStreamInfo()
        s.index = 2
        s.type = 'subtitle'
        s.codec = 'srt'
        s.metadata = {'language': 'fre'}
        s.disposition = {'default': False, 'forced': True}
        j = s.json
        assert j['language'] == 'fre'
        assert '+forced' in j['disposition']

    def test_json_attachment(self):
        s = MediaStreamInfo()
        s.index = 3
        s.type = 'attachment'
        s.codec = 'ttf'
        s.metadata = {'filename': 'font.ttf', 'mimetype': 'application/x-truetype-font'}
        s.disposition = {}
        j = s.json
        assert j['filename'] == 'font.ttf'


class TestMediaFormatInfo:
    """Test format info parsing."""

    def test_parse_format_name(self):
        f = MediaFormatInfo()
        f.parse_ffprobe('format_name', 'matroska,webm')
        assert f.format == 'matroska,webm'

    def test_parse_bitrate(self):
        f = MediaFormatInfo()
        f.parse_ffprobe('bit_rate', '10000000')
        assert f.bitrate == pytest.approx(10000000.0)

    def test_parse_duration(self):
        f = MediaFormatInfo()
        f.parse_ffprobe('duration', '7200.5')
        assert f.duration == pytest.approx(7200.5)

    def test_parse_tag(self):
        f = MediaFormatInfo()
        f.parse_ffprobe('TAG:title', 'My Movie')
        assert f.metadata['title'] == 'my movie'

    def test_parse_invalid_bitrate(self):
        f = MediaFormatInfo()
        f.parse_ffprobe('bit_rate', 'N/A')
        assert f.bitrate is None


class TestMediaInfo:
    """Test MediaInfo stream type accessors."""

    def test_video_property(self, make_stream):
        info = MediaInfo()
        info.streams.append(make_stream(type='video', codec='h264'))
        assert info.video is not None
        assert info.video.codec == 'h264'

    def test_video_none_when_no_video(self):
        info = MediaInfo()
        assert info.video is None

    def test_audio_property(self, make_stream):
        info = MediaInfo()
        info.streams.append(make_stream(type='audio', codec='aac', index=1))
        info.streams.append(make_stream(type='audio', codec='ac3', index=2))
        assert len(info.audio) == 2

    def test_subtitle_property(self, make_stream):
        info = MediaInfo()
        info.streams.append(make_stream(type='subtitle', codec='srt', index=2))
        assert len(info.subtitle) == 1

    def test_mixed_streams(self, make_stream):
        info = MediaInfo()
        info.streams.append(make_stream(type='video', codec='h264', index=0))
        info.streams.append(make_stream(type='audio', codec='aac', index=1))
        info.streams.append(make_stream(type='subtitle', codec='srt', index=2))
        assert info.video is not None
        assert len(info.audio) == 1
        assert len(info.subtitle) == 1


class TestMinStrict:
    """Test FFMpeg.minstrict deduplication."""

    def test_single_strict_unchanged(self):
        cmds = ['ffmpeg', '-strict', 'experimental', '-i', 'input.mkv']
        FFMpeg.minstrict(None, cmds)  # Static-like call (method modifies in place)

    def test_multiple_strict_uses_least(self):
        # We need an FFMpeg instance, but minstrict works on lists
        # Testing the logic directly
        from converter.ffmpeg import STRICT
        cmds = ['-strict', 'experimental', '-strict', 'normal']
        # minstrict should keep only the least strict value
        # experimental = -2, normal = 0 → min = -2
        # Create a mock object with minstrict method
        class MockFFMpeg:
            minstrict = FFMpeg.minstrict
        m = MockFFMpeg()
        m.minstrict(cmds)
        assert cmds.count('-strict') == 1
        idx = cmds.index('-strict')
        assert cmds[idx + 1] == '-2'


class TestGenerateCommands:
    """Test FFmpeg command generation."""

    def test_basic_command_structure(self):
        class MockFFMpeg:
            ffmpeg_path = '/usr/bin/ffmpeg'
            minstrict = FFMpeg.minstrict
            generateCommands = FFMpeg.generateCommands
        m = MockFFMpeg()
        cmds = m.generateCommands('output.mp4', ['-i', 'input.mkv', '-c', 'copy'])
        assert cmds[0] == '/usr/bin/ffmpeg'
        assert '-i' in cmds
        assert cmds[-2] == '-y'
        assert cmds[-1] == 'output.mp4'

    def test_preopts_before_opts(self):
        class MockFFMpeg:
            ffmpeg_path = 'ffmpeg'
            minstrict = FFMpeg.minstrict
            generateCommands = FFMpeg.generateCommands
        m = MockFFMpeg()
        cmds = m.generateCommands('out.mp4', ['-i', 'in.mkv'], preopts=['-hwaccel', 'qsv'])
        hwaccel_idx = cmds.index('-hwaccel')
        input_idx = cmds.index('-i')
        assert hwaccel_idx < input_idx

    def test_postopts_after_opts(self):
        class MockFFMpeg:
            ffmpeg_path = 'ffmpeg'
            minstrict = FFMpeg.minstrict
            generateCommands = FFMpeg.generateCommands
        m = MockFFMpeg()
        cmds = m.generateCommands('out.mp4', ['-i', 'in.mkv'], postopts=['-threads', '4'])
        input_idx = cmds.index('-i')
        threads_idx = cmds.index('-threads')
        assert threads_idx > input_idx

    def test_null_output(self):
        class MockFFMpeg:
            ffmpeg_path = 'ffmpeg'
            minstrict = FFMpeg.minstrict
            generateCommands = FFMpeg.generateCommands
        m = MockFFMpeg()
        cmds = m.generateCommands(None, ['-i', 'in.mkv'])
        assert '-f' in cmds
        assert 'null' in cmds
