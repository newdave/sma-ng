"""Tests for converter/__init__.py Converter class."""
import pytest
from converter import Converter, ConverterError
from converter.avcodecs import video_codec_list, audio_codec_list, subtitle_codec_list


class TestConverterInit:
    def test_video_codecs_populated(self):
        c = Converter(ffmpeg_path='ffmpeg', ffprobe_path='ffprobe')
        assert len(c.video_codecs) > 0
        assert 'h264' in c.video_codecs

    def test_audio_codecs_populated(self):
        c = Converter(ffmpeg_path='ffmpeg', ffprobe_path='ffprobe')
        assert 'aac' in c.audio_codecs

    def test_subtitle_codecs_populated(self):
        c = Converter(ffmpeg_path='ffmpeg', ffprobe_path='ffprobe')
        assert 'mov_text' in c.subtitle_codecs

    def test_formats_populated(self):
        c = Converter(ffmpeg_path='ffmpeg', ffprobe_path='ffprobe')
        assert 'mp4' in c.formats
        assert 'mkv' in c.formats


class TestCodecLookups:
    def test_codec_name_to_ffmpeg(self):
        assert Converter.codec_name_to_ffmpeg_codec_name('h264') == 'libx264'

    def test_codec_name_to_ffprobe(self):
        result = Converter.codec_name_to_ffprobe_codec_name('aac')
        assert result is not None

    def test_unknown_codec_returns_none(self):
        assert Converter.codec_name_to_ffmpeg_codec_name('nonexistent') is None

    def test_encoder_lookup(self):
        enc = Converter.encoder('h264')
        assert enc is not None
        assert enc.codec_name == 'h264'

    def test_encoder_unknown_returns_none(self):
        assert Converter.encoder('nonexistent') is None


class TestParseOptions:
    def test_missing_source_raises(self):
        c = Converter(ffmpeg_path='ffmpeg', ffprobe_path='ffprobe')
        with pytest.raises(ConverterError, match='No source'):
            c.parse_options({'format': 'mp4', 'audio': {'codec': 'aac'}})

    def test_no_streams_raises(self):
        c = Converter(ffmpeg_path='ffmpeg', ffprobe_path='ffprobe')
        with pytest.raises(ConverterError, match='Neither audio nor video'):
            c.parse_options({'format': 'mp4', 'source': ['/dev/null']})

    def test_invalid_options_type_raises(self):
        c = Converter(ffmpeg_path='ffmpeg', ffprobe_path='ffprobe')
        with pytest.raises(ConverterError, match='Invalid output'):
            c.parse_options("not a dict")

    def test_unknown_audio_codec_raises(self):
        c = Converter(ffmpeg_path='ffmpeg', ffprobe_path='ffprobe')
        with pytest.raises(ConverterError, match='unknown audio codec'):
            c.parse_options({'format': 'mp4', 'source': ['/dev/null'], 'subtitle': [], 'audio': [{'codec': 'bogus'}]})

    def test_unknown_video_codec_raises(self):
        c = Converter(ffmpeg_path='ffmpeg', ffprobe_path='ffprobe')
        with pytest.raises(ConverterError, match='unknown video codec'):
            c.parse_options({'format': 'mp4', 'source': ['/dev/null'], 'subtitle': [], 'video': {'codec': 'bogus'}})
