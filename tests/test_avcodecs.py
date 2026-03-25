"""Tests for converter/avcodecs.py - codec option parsing and FFmpeg flag generation."""
import pytest
from converter.avcodecs import (
    VideoCodec, AudioCodec, SubtitleCodec, BaseCodec,
    H264Codec, H264QSVCodec, H264VAAPICodec,
    H265Codec, H265QSVCodec, H265VAAPICodec,
    AV1QSVCodec, AV1VAAPICodec, Vp9QSVCodec,
    NVEncH264Codec, NVEncH265Codec,
    AacCodec, Ac3Codec, FlacCodec, OpusCodec,
    MOVTextCodec, SrtCodec,
    video_codec_list, audio_codec_list, subtitle_codec_list,
)


class TestBaseCodecSafeOptions:
    """Test that safe_options correctly filters and type-casts options."""

    def test_filters_unknown_keys(self):
        codec = H264Codec()
        safe = codec.safe_options({'codec': 'h264', 'unknown_key': 'value', 'crf': 23})
        assert 'unknown_key' not in safe
        assert safe['crf'] == 23

    def test_type_casts_values(self):
        codec = H264Codec()
        safe = codec.safe_options({'codec': 'h264', 'crf': '23', 'bitrate': '5000'})
        assert safe['crf'] == 23
        assert safe['bitrate'] == 5000

    def test_skips_none_values(self):
        codec = H264Codec()
        safe = codec.safe_options({'codec': 'h264', 'crf': None})
        assert 'crf' not in safe

    def test_invalid_type_cast_skipped(self):
        codec = H264Codec()
        safe = codec.safe_options({'codec': 'h264', 'crf': 'not_a_number'})
        assert 'crf' not in safe


class TestH264Codec:
    """Test H.264 software codec."""

    def test_parse_options_basic(self):
        codec = H264Codec()
        opts = codec.parse_options({'codec': 'h264', 'crf': 23})
        assert '-vcodec' in opts
        assert 'libx264' in opts
        assert '-tag:v' in opts
        assert 'avc1' in opts

    def test_width_rounding(self):
        codec = H264Codec()
        opts = codec.parse_options({'codec': 'h264', 'width': 1919, 'height': 1080})
        # Width should be rounded to even
        assert '-vf' in opts
        vf_idx = opts.index('-vf')
        assert '1920' in opts[vf_idx + 1]

    def test_level_filtering(self):
        codec = H264Codec()
        opts = codec.parse_options({'codec': 'h264', 'level': 4.1})
        assert '-level' in opts

    def test_preset_passthrough(self):
        codec = H264Codec()
        opts = codec.parse_options({'codec': 'h264', 'preset': 'veryfast'})
        assert '-preset' in opts
        assert 'veryfast' in opts

    def test_invalid_codec_raises(self):
        codec = H264Codec()
        with pytest.raises(ValueError):
            codec.parse_options({'codec': 'wrong'})


class TestH264QSVCodec:
    """Test H.264 QSV hardware codec."""

    def test_device_passthrough(self):
        codec = H264QSVCodec()
        opts = codec.parse_options({'codec': 'h264qsv', 'device': 'sma', 'crf': 25})
        assert '-filter_hw_device' in opts
        assert 'sma' in opts

    def test_crf_to_global_quality(self):
        codec = H264QSVCodec()
        opts = codec.parse_options({'codec': 'h264qsv', 'crf': 25})
        assert '-global_quality' in opts
        assert '25' in opts
        # CRF should not be in output (converted to global_quality)
        assert '-crf' not in opts

    def test_look_ahead_appended(self):
        codec = H264QSVCodec()
        opts = codec.parse_options({'codec': 'h264qsv'})
        assert '-look_ahead' in opts
        assert '0' in opts

    def test_encoder_options_include_device(self):
        assert 'device' in H264QSVCodec.encoder_options
        assert 'decode_device' in H264QSVCodec.encoder_options

    def test_scale_filter_is_qsv(self):
        assert H264QSVCodec.scale_filter == 'scale_qsv'

    def test_invalid_crf_removed(self):
        codec = H264QSVCodec()
        opts = codec.parse_options({'codec': 'h264qsv', 'crf': 0})
        assert '-global_quality' not in opts

    def test_decode_device_hwdownload(self):
        codec = H264QSVCodec()
        opts = codec.parse_options({'codec': 'h264qsv', 'device': 'sma2', 'decode_device': 'sma'})
        assert 'hwdownload,format=nv12,hwupload' in ' '.join(opts)

    def test_scaling(self):
        codec = H264QSVCodec()
        opts = codec.parse_options({'codec': 'h264qsv', 'width': 1280, 'height': 720})
        vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == '-vf']
        assert any('scale_qsv' in v for v in vf_parts)


class TestH264VAAPICodec:
    """Test H.264 VAAPI hardware codec."""

    def test_crf_to_qp(self):
        codec = H264VAAPICodec()
        opts = codec.parse_options({'codec': 'h264vaapi', 'crf': 30})
        assert '-qp' in opts
        assert '30' in opts

    def test_fallback_init_hw_device(self):
        """When no device configured, should use -init_hw_device fallback."""
        codec = H264VAAPICodec()
        opts = codec.parse_options({'codec': 'h264vaapi'})
        assert '-init_hw_device' in opts
        assert any('vaapi=vaapi0:/dev/dri/renderD128' in o for o in opts)

    def test_no_vaapi_device_flag(self):
        """Should NOT use deprecated -vaapi_device."""
        codec = H264VAAPICodec()
        opts = codec.parse_options({'codec': 'h264vaapi'})
        assert '-vaapi_device' not in opts

    def test_format_hwupload_chain(self):
        codec = H264VAAPICodec()
        opts = codec.parse_options({'codec': 'h264vaapi'})
        vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == '-vf']
        assert any('hwupload' in v for v in vf_parts)

    def test_device_overrides_fallback(self):
        codec = H264VAAPICodec()
        opts = codec.parse_options({'codec': 'h264vaapi', 'device': 'sma'})
        assert '-filter_hw_device' in opts
        assert '-init_hw_device' not in opts


class TestH265QSVCodec:
    """Test HEVC QSV codec."""

    def test_encoder_options_include_device(self):
        assert 'device' in H265QSVCodec.encoder_options
        assert 'decode_device' in H265QSVCodec.encoder_options

    def test_crf_to_global_quality(self):
        codec = H265QSVCodec()
        opts = codec.parse_options({'codec': 'h265qsv', 'crf': 22})
        assert '-global_quality' in opts
        assert '22' in opts

    def test_ffmpeg_codec_name(self):
        assert H265QSVCodec.ffmpeg_codec_name == 'hevc_qsv'


class TestH265VAAPICodec:
    """Test HEVC VAAPI codec."""

    def test_init_hw_device_fallback(self):
        codec = H265VAAPICodec()
        opts = codec.parse_options({'codec': 'h265vaapi'})
        assert '-init_hw_device' in opts
        assert '-vaapi_device' not in opts

    def test_pix_fmt_passthrough(self):
        codec = H265VAAPICodec()
        opts = codec.parse_options({'codec': 'h265vaapi', 'pix_fmt': 'p010le'})
        vf_str = ' '.join(opts)
        assert 'p010le' in vf_str


class TestAV1QSVCodec:
    """Test AV1 QSV codec."""

    def test_has_device_options(self):
        assert 'device' in AV1QSVCodec.encoder_options
        assert 'decode_device' in AV1QSVCodec.encoder_options

    def test_scale_filter(self):
        assert AV1QSVCodec.scale_filter == 'scale_qsv'

    def test_crf_to_global_quality(self):
        codec = AV1QSVCodec()
        opts = codec.parse_options({'codec': 'av1qsv', 'crf': 28})
        assert '-global_quality' in opts
        assert '28' in opts

    def test_look_ahead(self):
        codec = AV1QSVCodec()
        opts = codec.parse_options({'codec': 'av1qsv'})
        assert '-look_ahead' in opts

    def test_ffmpeg_codec_name(self):
        assert AV1QSVCodec.ffmpeg_codec_name == 'av1_qsv'


class TestAV1VAAPICodec:
    """Test AV1 VAAPI codec."""

    def test_has_device_options(self):
        assert 'device' in AV1VAAPICodec.encoder_options
        assert 'decode_device' in AV1VAAPICodec.encoder_options

    def test_scale_filter(self):
        assert AV1VAAPICodec.scale_filter == 'scale_vaapi'

    def test_crf_to_qp(self):
        codec = AV1VAAPICodec()
        opts = codec.parse_options({'codec': 'av1vaapi', 'crf': 30})
        assert '-qp' in opts

    def test_init_hw_device_fallback(self):
        codec = AV1VAAPICodec()
        opts = codec.parse_options({'codec': 'av1vaapi'})
        assert '-init_hw_device' in opts
        assert '-vaapi_device' not in opts


class TestVp9QSVCodec:
    """Test VP9 QSV codec."""

    def test_has_device_options(self):
        assert 'device' in Vp9QSVCodec.encoder_options

    def test_crf_to_global_quality(self):
        codec = Vp9QSVCodec()
        opts = codec.parse_options({'codec': 'vp9qsv', 'crf': 28})
        assert '-global_quality' in opts

    def test_works_without_framedata(self):
        """VP9 base class expects framedata; QSV should handle its absence."""
        codec = Vp9QSVCodec()
        opts = codec.parse_options({'codec': 'vp9qsv', 'crf': 25})
        assert '-vcodec' in opts


class TestAudioCodecs:
    """Test audio codec option parsing."""

    def test_aac_basic(self):
        codec = AacCodec()
        opts = codec.parse_options({'codec': 'aac', 'channels': 2, 'bitrate': 128000})
        assert '-c:a:0' in opts
        assert 'aac' in opts

    def test_ac3_basic(self):
        codec = Ac3Codec()
        opts = codec.parse_options({'codec': 'ac3', 'channels': 6})
        assert '-c:a:0' in opts
        assert 'ac3' in opts

    def test_language_metadata(self):
        codec = AacCodec()
        opts = codec.parse_options({'codec': 'aac', 'language': 'eng'})
        assert any('language=eng' in o for o in opts)

    def test_disposition(self):
        codec = AacCodec()
        opts = codec.parse_options({'codec': 'aac', 'disposition': '+default'})
        assert any('disposition' in o for o in opts)


class TestSubtitleCodecs:
    """Test subtitle codec option parsing."""

    def test_mov_text(self):
        codec = MOVTextCodec()
        opts = codec.parse_options({'codec': 'mov_text', 'language': 'eng'})
        assert '-c:s:0' in opts
        assert 'mov_text' in opts

    def test_srt(self):
        codec = SrtCodec()
        opts = codec.parse_options({'codec': 'srt', 'language': 'eng'})
        assert 'srt' in opts

    def test_title_set(self):
        codec = MOVTextCodec()
        opts = codec.parse_options({'codec': 'mov_text', 'title': 'Full', 'language': 'eng'})
        assert any('title=Full' in o for o in opts)
        assert any('handler_name=Full' in o for o in opts)

    def test_empty_title_blanked(self):
        codec = MOVTextCodec()
        opts = codec.parse_options({'codec': 'mov_text', 'language': 'eng'})
        assert any('title=' == o for o in opts)

    def test_undefined_language_default(self):
        codec = MOVTextCodec()
        opts = codec.parse_options({'codec': 'mov_text'})
        assert any('language=und' in o for o in opts)


class TestCodecLists:
    """Test that codec registries are populated."""

    def test_video_codecs_not_empty(self):
        assert len(video_codec_list) > 0

    def test_audio_codecs_not_empty(self):
        assert len(audio_codec_list) > 0

    def test_subtitle_codecs_not_empty(self):
        assert len(subtitle_codec_list) > 0

    def test_h264qsv_in_video_list(self):
        names = [c.codec_name for c in video_codec_list]
        assert 'h264qsv' in names

    def test_av1qsv_in_video_list(self):
        names = [c.codec_name for c in video_codec_list]
        assert 'av1qsv' in names

    def test_no_duplicate_codec_names(self):
        names = [c.codec_name for c in video_codec_list]
        assert len(names) == len(set(names))
