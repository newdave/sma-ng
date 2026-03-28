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

    def test_invalid_crf_uses_default(self):
        codec = H264QSVCodec()
        opts = codec.parse_options({'codec': 'h264qsv', 'crf': 0})
        idx = opts.index('-global_quality')
        assert opts[idx + 1] == str(H264QSVCodec.hw_quality_default)

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


class TestH265Codec:
    """Test H.265 software codec."""

    def test_basic(self):
        codec = H265Codec()
        opts = codec.parse_options({'codec': 'h265', 'crf': 22})
        assert '-vcodec' in opts
        assert 'libx265' in opts

    def test_tag(self):
        codec = H265Codec()
        opts = codec.parse_options({'codec': 'h265'})
        assert '-tag:v' in opts
        assert 'hvc1' in opts

    def test_level(self):
        codec = H265Codec()
        opts = codec.parse_options({'codec': 'h265', 'level': 5.0})
        # H265 stores level directly
        assert '-level' in opts


class TestNVEncCodecs:
    """Test NVIDIA hardware codecs."""

    def test_nvenc_h264_basic(self):
        codec = NVEncH264Codec()
        opts = codec.parse_options({'codec': 'h264_nvenc', 'crf': 25})
        assert '-vcodec' in opts
        assert 'h264_nvenc' in opts

    def test_nvenc_h265_basic(self):
        codec = NVEncH265Codec()
        opts = codec.parse_options({'codec': 'h265_nvenc', 'crf': 25})
        assert '-vcodec' in opts
        assert 'hevc_nvenc' in opts


class TestCopyCodecs:
    """Test copy codec variants."""

    def test_audio_copy(self):
        from converter.avcodecs import AudioCopyCodec
        codec = AudioCopyCodec()
        opts = codec.parse_options({'codec': 'copy', 'map': 1, 'language': 'eng'})
        assert '-c:a:0' in opts
        assert 'copy' in opts
        assert any('language=eng' in o for o in opts)

    def test_audio_copy_no_language(self):
        from converter.avcodecs import AudioCopyCodec
        codec = AudioCopyCodec()
        opts = codec.parse_options({'codec': 'copy', 'map': 1})
        assert any('language=und' in o for o in opts)

    def test_audio_copy_with_title(self):
        from converter.avcodecs import AudioCopyCodec
        codec = AudioCopyCodec()
        opts = codec.parse_options({'codec': 'copy', 'map': 0, 'title': 'Stereo'})
        assert any('title=Stereo' in o for o in opts)

    def test_audio_copy_with_bsf(self):
        from converter.avcodecs import AudioCopyCodec
        codec = AudioCopyCodec()
        opts = codec.parse_options({'codec': 'copy', 'map': 0, 'bsf': 'aac_adtstoasc'})
        assert '-bsf:a:0' in opts

    def test_video_copy(self):
        from converter.avcodecs import VideoCopyCodec
        codec = VideoCopyCodec()
        opts = codec.parse_options({'codec': 'copy', 'map': 0})
        assert '-vcodec' in opts
        assert 'copy' in opts

    def test_video_copy_with_title(self):
        from converter.avcodecs import VideoCopyCodec
        codec = VideoCopyCodec()
        opts = codec.parse_options({'codec': 'copy', 'map': 0, 'title': 'FHD'})
        assert any('title=FHD' in o for o in opts)

    def test_video_copy_fps(self):
        from converter.avcodecs import VideoCopyCodec
        codec = VideoCopyCodec()
        opts = codec.parse_options({'codec': 'copy', 'map': 0, 'fps': 24.0})
        assert '-r:v' in opts

    def test_subtitle_copy(self):
        from converter.avcodecs import SubtitleCopyCodec
        codec = SubtitleCopyCodec()
        opts = codec.parse_options({'codec': 'copy', 'map': 2, 'language': 'fra'})
        assert '-c:s:0' in opts
        assert 'copy' in opts
        assert any('language=fra' in o for o in opts)

    def test_subtitle_copy_no_language(self):
        from converter.avcodecs import SubtitleCopyCodec
        codec = SubtitleCopyCodec()
        opts = codec.parse_options({'codec': 'copy', 'map': 2})
        assert any('language=und' in o for o in opts)

    def test_attachment_copy(self):
        from converter.avcodecs import AttachmentCopyCodec
        codec = AttachmentCopyCodec()
        opts = codec.parse_options({'codec': 'copy', 'map': 5, 'filename': 'font.ttf', 'mimetype': 'font/ttf'})
        assert '-c:t:0' in opts
        assert 'copy' in opts
        assert any('filename=font.ttf' in o for o in opts)


class TestNullCodecs:
    """Test null codec variants."""

    def test_audio_null(self):
        from converter.avcodecs import AudioNullCodec
        codec = AudioNullCodec()
        assert codec.parse_options({}) == ['-an']

    def test_video_null(self):
        from converter.avcodecs import VideoNullCodec
        codec = VideoNullCodec()
        assert codec.parse_options({}) == ['-vn']

    def test_subtitle_null(self):
        from converter.avcodecs import SubtitleNullCodec
        codec = SubtitleNullCodec()
        assert codec.parse_options({}) == ['-sn']


class TestBaseCodecHelpers:
    def test_safe_disposition(self):
        codec = BaseCodec()
        dispo = codec.safe_disposition('+default')
        assert '+default' in dispo
        # All other dispositions should have - prefix
        assert '-forced' in dispo
        assert '-comment' in dispo

    def test_safe_disposition_empty(self):
        codec = BaseCodec()
        dispo = codec.safe_disposition('')
        # All dispositions should have - prefix
        assert '-default' in dispo

    def test_safe_disposition_none(self):
        codec = BaseCodec()
        dispo = codec.safe_disposition(None)
        assert '-default' in dispo

    def test_supports_bit_depth(self):
        codec = BaseCodec()
        assert codec.supportsBitDepth(8) is True
        assert codec.supportsBitDepth(10) is True

    def test_safe_framedata(self):
        codec = BaseCodec()
        assert codec.safe_framedata({}) == ""


class TestVideoCodecAspectCorrections:
    def test_no_source_info(self):
        codec = H264Codec()
        w, h, filters = codec._aspect_corrections(0, 0, 1920, 1080, 'stretch')
        assert w == 1920
        assert h == 1080
        assert filters is None

    def test_no_dimensions(self):
        codec = H264Codec()
        w, h, filters = codec._aspect_corrections(1920, 1080, 0, 0, 'stretch')
        assert w == 0 and h == 0 and filters is None

    def test_width_only(self):
        codec = H264Codec()
        w, h, filters = codec._aspect_corrections(1920, 1080, 1280, 0, 'stretch')
        assert w == 1280
        assert h == 720  # Preserves 16:9

    def test_height_only(self):
        codec = H264Codec()
        w, h, filters = codec._aspect_corrections(1920, 1080, 0, 720, 'stretch')
        assert h == 720
        assert w == 1280  # Preserves 16:9

    def test_same_aspect(self):
        codec = H264Codec()
        w, h, filters = codec._aspect_corrections(1920, 1080, 1280, 720, 'stretch')
        assert filters is None

    def test_stretch_mode(self):
        codec = H264Codec()
        w, h, filters = codec._aspect_corrections(1920, 1080, 800, 600, 'stretch')
        assert filters is None

    def test_crop_mode(self):
        codec = H264Codec()
        w, h, filters = codec._aspect_corrections(1920, 1080, 800, 600, 'crop')
        assert filters is not None
        assert 'crop=' in filters

    def test_pad_mode(self):
        codec = H264Codec()
        # target is wider than source
        w, h, filters = codec._aspect_corrections(1280, 720, 800, 600, 'pad')
        assert filters is not None
        assert 'pad=' in filters


class TestAudioCodecEdgeCases:
    def test_channels_out_of_range(self):
        codec = AacCodec()
        opts = codec.parse_options({'codec': 'aac', 'channels': 0})
        assert '-ac:a:0' not in opts

    def test_bitrate_clamped_low(self):
        codec = AacCodec()
        opts = codec.parse_options({'codec': 'aac', 'bitrate': 1})
        assert any('8k' in o for o in opts)

    def test_bitrate_clamped_high(self):
        codec = AacCodec()
        opts = codec.parse_options({'codec': 'aac', 'bitrate': 9999})
        assert any('1536k' in o for o in opts)

    def test_samplerate_out_of_range(self):
        codec = AacCodec()
        opts = codec.parse_options({'codec': 'aac', 'samplerate': 500})
        assert '-ar:a:0' not in opts

    def test_language_too_long(self):
        codec = AacCodec()
        opts = codec.parse_options({'codec': 'aac', 'language': 'english'})
        # Language should be dropped, defaults to und
        assert any('language=und' in o for o in opts)

    def test_flac_basic(self):
        codec = FlacCodec()
        opts = codec.parse_options({'codec': 'flac', 'channels': 2})
        assert '-c:a:0' in opts
        assert 'flac' in opts

    def test_opus_basic(self):
        codec = OpusCodec()
        opts = codec.parse_options({'codec': 'opus', 'channels': 2})
        assert '-c:a:0' in opts

    def test_audio_map_and_source(self):
        codec = AacCodec()
        opts = codec.parse_options({'codec': 'aac', 'map': 1, 'source': 0})
        assert any('0:1' in o for o in opts)

    def test_audio_with_filter(self):
        codec = AacCodec()
        opts = codec.parse_options({'codec': 'aac', 'filter': 'aresample=48000'})
        assert '-filter:a:0' in opts

    def test_audio_with_profile(self):
        codec = AacCodec()
        opts = codec.parse_options({'codec': 'aac', 'profile': 'aac_low'})
        assert '-profile:a:0' in opts


class TestVideoCodecParsing:
    def test_bitrate_without_crf(self):
        codec = H264Codec()
        opts = codec.parse_options({'codec': 'h264', 'bitrate': 5000})
        assert '-vb' in opts
        assert any('5000k' in o for o in opts)

    def test_filter_consolidation(self):
        codec = H264Codec()
        opts = codec.parse_options({
            'codec': 'h264', 'filter': 'yadif', 'width': 1920, 'height': 1080,
            'src_width': 3840, 'src_height': 2160
        })
        # Should consolidate multiple -vf into one
        assert opts.count('-vf') == 1

    def test_field_order(self):
        codec = H264Codec()
        opts = codec.parse_options({'codec': 'h264', 'field_order': 'progressive'})
        assert '-field_order' in opts

    def test_invalid_field_order_removed(self):
        codec = H264Codec()
        opts = codec.parse_options({'codec': 'h264', 'field_order': 'invalid'})
        assert '-field_order' not in opts

    def test_bsf_passthrough(self):
        codec = H264Codec()
        opts = codec.parse_options({'codec': 'h264', 'bsf': 'h264_mp4toannexb'})
        assert '-bsf:v' in opts

    def test_video_title(self):
        codec = H264Codec()
        opts = codec.parse_options({'codec': 'h264', 'title': 'FHD'})
        assert any('title=FHD' in o for o in opts)

    def test_video_no_title(self):
        codec = H264Codec()
        opts = codec.parse_options({'codec': 'h264'})
        assert any('title=' == o for o in opts)

    def test_pix_fmt(self):
        codec = H264Codec()
        opts = codec.parse_options({'codec': 'h264', 'pix_fmt': 'yuv420p'})
        assert '-pix_fmt' in opts
        assert 'yuv420p' in opts

    def test_crf_out_of_range_removed(self):
        codec = H264Codec()
        opts = codec.parse_options({'codec': 'h264', 'crf': 99})
        assert '-crf' not in opts
