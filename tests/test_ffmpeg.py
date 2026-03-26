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


class TestFFMpegConvertError:
    def test_init_stores_fields(self):
        from converter.ffmpeg import FFMpegConvertError
        e = FFMpegConvertError('msg', 'ffmpeg -i x', 'output data', details='detail', pid=123)
        assert e.cmd == 'ffmpeg -i x'
        assert e.output == 'output data'
        assert e.details == 'detail'
        assert e.pid == 123

    def test_repr_with_details(self):
        from converter.ffmpeg import FFMpegConvertError
        e = FFMpegConvertError('msg', 'cmd', 'out', details='the error', pid=42)
        r = repr(e)
        assert 'the error' in r
        assert '42' in r
        assert 'cmd' in r

    def test_repr_without_details_uses_details_when_present(self):
        from converter.ffmpeg import FFMpegConvertError
        # When details is provided, repr uses it
        e = FFMpegConvertError('msg', 'cmd', 'out', details='specific error', pid=1)
        r = repr(e)
        assert 'specific error' in r

    def test_str_equals_repr(self):
        from converter.ffmpeg import FFMpegConvertError
        e = FFMpegConvertError('msg', 'cmd', 'out', details='err', pid=0)
        assert str(e) == repr(e)


class TestMediaStreamInfoParseFFprobe:
    def test_parse_index(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('index', '0')
        assert s.index == 0

    def test_parse_codec_type(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('codec_type', 'video')
        assert s.type == 'video'

    def test_parse_codec_name(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('codec_name', 'H264')
        assert s.codec == 'h264'

    def test_parse_codec_long_name(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('codec_long_name', 'H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10')
        assert s.codec_desc == 'H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10'

    def test_parse_duration(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('duration', '7200.5')
        assert s.duration == pytest.approx(7200.5)

    def test_parse_bitrate(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('bit_rate', '5000000')
        assert s.bitrate == 5000000

    def test_parse_low_bitrate_set_to_none(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('bit_rate', '500')
        assert s.bitrate is None

    def test_parse_width_height(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('width', '1920')
        s.parse_ffprobe('height', '1080')
        assert s.video_width == 1920
        assert s.video_height == 1080

    def test_parse_channels(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('channels', '6')
        assert s.audio_channels == 6

    def test_parse_sample_rate(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('sample_rate', '48000')
        assert s.audio_samplerate == 48000

    def test_parse_attached_pic(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('DISPOSITION:attached_pic', '1')
        assert s.attached_pic == 1

    def test_parse_profile(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('profile', 'High 10')
        assert s.profile == 'high10'

    def test_parse_disposition_forced(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('DISPOSITION:forced', '1')
        assert s.disposition.get('forced') is True

    def test_parse_disposition_default(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('DISPOSITION:default', '0')
        assert s.disposition.get('default') is False

    def test_parse_tag(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('TAG:language', 'eng')
        assert s.metadata['language'] == 'eng'

    def test_parse_tag_title_preserves_case(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('TAG:title', 'My Custom Title')
        assert s.metadata['title'] == 'My Custom Title'

    def test_parse_bps_tag_as_bitrate_fallback(self):
        s = MediaStreamInfo()
        s.parse_ffprobe('TAG:BPS', '8000000')
        assert s.bitrate == 8000000

    def test_parse_video_frame_rate_fraction(self):
        s = MediaStreamInfo()
        s.type = 'video'
        s.parse_ffprobe('r_frame_rate', '24000/1001')
        assert s.fps == pytest.approx(23.976, rel=0.01)

    def test_parse_video_frame_rate_decimal(self):
        s = MediaStreamInfo()
        s.type = 'video'
        s.parse_ffprobe('r_frame_rate', '25.0')
        assert s.fps == pytest.approx(25.0)

    def test_parse_audio_frame_rate_fraction(self):
        s = MediaStreamInfo()
        s.type = 'audio'
        s.parse_ffprobe('avg_frame_rate', '48000/1')
        assert s.fps == pytest.approx(48000.0)

    def test_parse_video_level(self):
        s = MediaStreamInfo()
        s.type = 'video'
        s.codec = 'nonexistent_codec'
        s.parse_ffprobe('level', '41')
        assert s.video_level == pytest.approx(41.0)

    def test_parse_pix_fmt(self):
        s = MediaStreamInfo()
        s.type = 'video'
        s.parse_ffprobe('pix_fmt', 'YUV420P')
        assert s.pix_fmt == 'yuv420p'

    def test_parse_field_order(self):
        s = MediaStreamInfo()
        s.type = 'video'
        s.parse_ffprobe('field_order', 'progressive')
        assert s.field_order == 'progressive'

    def test_parse_color_properties(self):
        s = MediaStreamInfo()
        s.type = 'video'
        s.parse_ffprobe('color_range', 'tv')
        s.parse_ffprobe('color_space', 'bt709')
        s.parse_ffprobe('color_transfer', 'bt709')
        s.parse_ffprobe('color_primaries', 'bt709')
        assert s.color['range'] == 'tv'
        assert s.color['space'] == 'bt709'
        assert s.color['transfer'] == 'bt709'
        assert s.color['primaries'] == 'bt709'


class TestMediaFormatInfoParseFFprobe:
    def test_parse_format_long_name(self):
        f = MediaFormatInfo()
        f.parse_ffprobe('format_long_name', 'Matroska / WebM')
        assert f.fullname == 'Matroska / WebM'

    def test_parse_size(self):
        f = MediaFormatInfo()
        f.parse_ffprobe('size', '1073741824')
        assert f.size == pytest.approx(1073741824.0)

    def test_repr_with_duration(self):
        f = MediaFormatInfo()
        f.format = 'mkv'
        f.duration = 120.5
        r = repr(f)
        assert 'mkv' in r
        assert '120.50' in r

    def test_repr_without_duration(self):
        f = MediaFormatInfo()
        f.format = 'mp4'
        r = repr(f)
        assert 'mp4' in r


class TestMediaInfoParseFFprobe:
    def test_parse_full_ffprobe_output(self):
        raw = """[STREAM]
index=0
codec_type=video
codec_name=h264
width=1920
height=1080
r_frame_rate=24000/1001
pix_fmt=yuv420p
profile=High
level=41
[/STREAM]
[STREAM]
index=1
codec_type=audio
codec_name=aac
channels=2
sample_rate=48000
TAG:language=eng
[/STREAM]
[STREAM]
index=2
codec_type=subtitle
codec_name=subrip
TAG:language=fre
[/STREAM]
[FORMAT]
format_name=matroska,webm
format_long_name=Matroska / WebM
duration=7200.000000
bit_rate=10000000
[/FORMAT]"""
        info = MediaInfo()
        info.parse_ffprobe(raw)
        assert info.format.format == 'matroska,webm'
        assert info.format.duration == pytest.approx(7200.0)
        assert len(info.streams) == 3
        assert info.video.codec == 'h264'
        assert info.video.video_width == 1920
        assert len(info.audio) == 1
        assert info.audio[0].audio_channels == 2
        assert len(info.subtitle) == 1

    def test_parse_ignores_empty_lines(self):
        raw = "\n\n[FORMAT]\nformat_name=mp4\n[/FORMAT]\n\n"
        info = MediaInfo()
        info.parse_ffprobe(raw)
        assert info.format.format == 'mp4'

    def test_parse_stream_without_type_ignored(self):
        raw = "[STREAM]\nindex=0\n[/STREAM]"
        info = MediaInfo()
        info.parse_ffprobe(raw)
        assert len(info.streams) == 0

    def test_video_property_excludes_attached_pic(self):
        info = MediaInfo(posters_as_video=False)
        s = MediaStreamInfo()
        s.type = 'video'
        s.attached_pic = 1
        info.streams.append(s)
        assert info.video is None

    def test_posters_property(self):
        info = MediaInfo()
        s = MediaStreamInfo()
        s.type = 'video'
        s.attached_pic = 1
        info.streams.append(s)
        assert len(info.posters) == 1

    def test_attachment_property(self, make_stream):
        info = MediaInfo()
        info.streams.append(make_stream(type='attachment', codec='ttf', index=5,
                                         metadata={'filename': 'font.ttf', 'mimetype': 'font/ttf'}))
        assert len(info.attachment) == 1

    def test_json_property(self, make_stream):
        info = MediaInfo()
        info.format.format = 'mkv'
        info.format.fullname = 'Matroska'
        v = make_stream(type='video', codec='h264', index=0,
                        video_width=1920, video_height=1080, fps=24.0,
                        pix_fmt='yuv420p', profile='main', field_order='progressive',
                        video_level=4.1)
        v.framedata = {}
        info.streams.append(v)
        a = make_stream(type='audio', codec='aac', index=1,
                        audio_channels=2, audio_samplerate=48000)
        info.streams.append(a)
        j = info.json
        assert j['format'] == 'mkv'
        assert j['video']['codec'] == 'h264'
        assert len(j['audio']) == 1

    def test_repr(self, make_stream):
        info = MediaInfo()
        info.format.format = 'mp4'
        r = repr(info)
        assert 'MediaInfo' in r


class TestMediaStreamInfoRepr:
    def test_audio_repr(self):
        s = MediaStreamInfo()
        s.type = 'audio'
        s.codec = 'aac'
        s.audio_channels = 2
        s.audio_samplerate = 48000
        s.metadata = {}
        r = repr(s)
        assert 'audio' in r
        assert 'aac' in r

    def test_video_repr(self):
        s = MediaStreamInfo()
        s.type = 'video'
        s.codec = 'h264'
        s.video_width = 1920
        s.video_height = 1080
        s.fps = 24.0
        s.metadata = {}
        r = repr(s)
        assert 'video' in r
        assert '1920' in r

    def test_subtitle_repr(self):
        s = MediaStreamInfo()
        s.type = 'subtitle'
        s.codec = 'srt'
        s.metadata = {}
        r = repr(s)
        assert 'subtitle' in r

    def test_repr_with_bitrate(self):
        s = MediaStreamInfo()
        s.type = 'audio'
        s.codec = 'aac'
        s.audio_channels = 2
        s.audio_samplerate = 48000
        s.bitrate = 128000
        s.metadata = {}
        r = repr(s)
        assert '128000' in r

    def test_repr_with_metadata(self):
        s = MediaStreamInfo()
        s.type = 'audio'
        s.codec = 'aac'
        s.audio_channels = 2
        s.audio_samplerate = 48000
        s.metadata = {'language': 'eng'}
        r = repr(s)
        assert 'language=eng' in r


class TestMediaStreamInfoJsonEdgeCases:
    def test_json_video_no_dimensions(self):
        s = MediaStreamInfo()
        s.index = 0
        s.type = 'video'
        s.codec = 'h264'
        s.pix_fmt = 'yuv420p'
        s.profile = 'main'
        s.fps = 24.0
        s.video_width = None
        s.video_height = None
        s.video_level = None
        s.field_order = 'progressive'
        s.framedata = {}
        j = s.json
        assert 'dimensions' not in j
        assert 'level' not in j

    def test_json_with_bitrate(self):
        s = MediaStreamInfo()
        s.index = 0
        s.type = 'video'
        s.codec = 'h264'
        s.bitrate = 5000000
        s.pix_fmt = 'yuv420p'
        s.profile = 'main'
        s.fps = 24.0
        s.video_width = 1920
        s.video_height = 1080
        s.video_level = 4.1
        s.field_order = 'progressive'
        s.framedata = {}
        j = s.json
        assert j['bitrate'] == 5000000
        assert j['level'] == 4.1
