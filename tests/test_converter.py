"""Tests for converter/__init__.py Converter class."""

import pytest

from converter import Converter, ConverterError


class TestConverterInit:
    def test_video_codecs_populated(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        assert len(c.video_codecs) > 0
        assert "h264" in c.video_codecs

    def test_audio_codecs_populated(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        assert "aac" in c.audio_codecs

    def test_subtitle_codecs_populated(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        assert "mov_text" in c.subtitle_codecs

    def test_formats_populated(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        assert "mp4" in c.formats
        assert "mkv" in c.formats


class TestCodecLookups:
    def test_codec_name_to_ffmpeg(self):
        assert Converter.codec_name_to_ffmpeg_codec_name("h264") == "libx264"

    def test_codec_name_to_ffprobe(self):
        result = Converter.codec_name_to_ffprobe_codec_name("aac")
        assert result is not None

    def test_unknown_codec_returns_none(self):
        assert Converter.codec_name_to_ffmpeg_codec_name("nonexistent") is None

    def test_encoder_lookup(self):
        enc = Converter.encoder("h264")
        assert enc is not None
        assert enc.codec_name == "h264"

    def test_encoder_unknown_returns_none(self):
        assert Converter.encoder("nonexistent") is None


class TestParseOptions:
    def test_missing_source_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="No source"):
            c.parse_options({"format": "mp4", "audio": {"codec": "aac"}})

    def test_no_streams_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="Neither audio nor video"):
            c.parse_options({"format": "mp4", "source": ["/dev/null"]})

    def test_invalid_options_type_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="Invalid output"):
            c.parse_options("not a dict")

    def test_unknown_audio_codec_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="unknown audio codec"):
            c.parse_options({"format": "mp4", "source": ["/dev/null"], "subtitle": [], "audio": [{"codec": "bogus"}]})

    def test_unknown_video_codec_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="unknown video codec"):
            c.parse_options({"format": "mp4", "source": ["/dev/null"], "subtitle": [], "video": {"codec": "bogus"}})

    def test_invalid_audio_spec_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="Invalid audio codec"):
            c.parse_options({"format": "mp4", "source": ["/dev/null"], "subtitle": [], "audio": [{"no_codec_key": True}]})

    def test_invalid_video_spec_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="Invalid video codec"):
            c.parse_options({"format": "mp4", "source": ["/dev/null"], "subtitle": [], "video": "not_a_dict"})

    def test_unknown_subtitle_codec_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="unknown subtitle codec"):
            c.parse_options({"format": "mp4", "source": ["/dev/null"], "subtitle": [{"codec": "bogus"}], "audio": [{"codec": "aac"}]})

    def test_invalid_subtitle_spec_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="Invalid subtitle codec"):
            c.parse_options({"format": "mp4", "source": ["/dev/null"], "subtitle": [{"no_codec": True}], "audio": [{"codec": "aac"}]})

    def test_attachment_missing_filename_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="requires a filename"):
            c.parse_options({"format": "mkv", "source": ["/dev/null"], "audio": [{"codec": "aac"}], "subtitle": [], "attachment": [{"codec": "ttf", "mimetype": "font/ttf"}]})

    def test_attachment_missing_mimetype_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="requires a mimetype"):
            c.parse_options({"format": "mkv", "source": ["/dev/null"], "audio": [{"codec": "aac"}], "subtitle": [], "attachment": [{"codec": "ttf", "filename": "font.ttf"}]})

    def test_attachment_unknown_codec_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="unknown attachment codec"):
            c.parse_options(
                {"format": "mkv", "source": ["/dev/null"], "audio": [{"codec": "aac"}], "subtitle": [], "attachment": [{"codec": "nonexistent", "filename": "f.ttf", "mimetype": "font/ttf"}]}
            )

    def test_attachment_invalid_spec_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="Invalid attachment codec"):
            c.parse_options({"format": "mkv", "source": ["/dev/null"], "audio": [{"codec": "aac"}], "subtitle": [], "attachment": ["not_a_dict"]})

    def test_source_nonexistent_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="Source file does not exist"):
            c.parse_options({"format": "mp4", "source": ["/nonexistent/path/to/file.mkv"], "subtitle": [], "audio": [{"codec": "aac"}]})

    def test_source_as_string_converted_to_list(self, tmp_path):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        src = str(tmp_path / "test.mkv")
        with open(src, "w") as f:
            f.write("x")
        opts = c.parse_options({"format": "mp4", "source": src, "subtitle": [], "audio": [{"codec": "aac"}]})
        assert "-i" in opts
        assert src in opts

    def test_strip_metadata_adds_map_metadata(self, tmp_path):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        src = str(tmp_path / "test.mkv")
        with open(src, "w") as f:
            f.write("x")
        opts = c.parse_options({"format": "mp4", "source": [src], "subtitle": [], "audio": [{"codec": "aac"}]}, strip_metadata=True)
        assert "-map_metadata" in opts
        idx = opts.index("-map_metadata")
        assert opts[idx + 1] == "-1"

    def test_strip_metadata_false_no_map_metadata(self, tmp_path):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        src = str(tmp_path / "test.mkv")
        with open(src, "w") as f:
            f.write("x")
        opts = c.parse_options({"format": "mp4", "source": [src], "subtitle": [], "audio": [{"codec": "aac"}]}, strip_metadata=False)
        assert "-map_metadata" not in opts

    def test_twopass_1(self, tmp_path):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        src = str(tmp_path / "test.mkv")
        with open(src, "w") as f:
            f.write("x")
        opts = c.parse_options({"format": "mp4", "source": [src], "subtitle": [], "audio": [{"codec": "aac"}]}, twopass=1)
        assert "-pass" in opts
        idx = opts.index("-pass")
        assert opts[idx + 1] == "1"

    def test_twopass_2(self, tmp_path):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        src = str(tmp_path / "test.mkv")
        with open(src, "w") as f:
            f.write("x")
        opts = c.parse_options({"format": "mp4", "source": [src], "subtitle": [], "audio": [{"codec": "aac"}]}, twopass=2)
        idx = opts.index("-pass")
        assert opts[idx + 1] == "2"

    def test_invalid_format_falls_back_gracefully(self, tmp_path):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        src = str(tmp_path / "test.mkv")
        with open(src, "w") as f:
            f.write("x")
        opts = c.parse_options({"format": "nonexistent_format", "source": [src], "subtitle": [], "audio": [{"codec": "aac"}]})
        # Should not raise - format_options falls back to []
        assert isinstance(opts, list)

    def test_subtitle_as_dict_wrapped_in_list(self, tmp_path):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        src = str(tmp_path / "test.mkv")
        with open(src, "w") as f:
            f.write("x")
        opts = c.parse_options({"format": "mp4", "source": [src], "subtitle": {"codec": "mov_text", "map": "0:2", "source": 0}, "audio": [{"codec": "aac"}]})
        assert isinstance(opts, list)

    def test_audio_as_dict_wrapped_in_list(self, tmp_path):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        src = str(tmp_path / "test.mkv")
        with open(src, "w") as f:
            f.write("x")
        opts = c.parse_options({"format": "mp4", "source": [src], "subtitle": [], "audio": {"codec": "aac"}})
        assert isinstance(opts, list)


class TestConverterConvert:
    def test_invalid_options_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="Invalid options"):
            list(c.convert("out.mp4", "not_a_dict"))

    def test_missing_source_raises(self):
        c = Converter(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        with pytest.raises(ConverterError, match="No source specified"):
            list(c.convert("out.mp4", {"format": "mp4"}))


class TestConverterStaticMethods:
    def test_ffmpeg_codec_name_to_codec_name(self):
        result = Converter.ffmpeg_codec_name_to_codec_name("video", "libx264")
        assert result == "h264"

    def test_ffmpeg_codec_name_unknown(self):
        result = Converter.ffmpeg_codec_name_to_codec_name("video", "nonexistent")
        assert result is None

    def test_decoder_known(self):
        d = Converter.decoder("h264_cuvid")
        assert d is not None

    def test_decoder_unknown_returns_base(self):
        from converter.avcodecs import BaseDecoder

        d = Converter.decoder("nonexistent_decoder")
        assert isinstance(d, BaseDecoder)
