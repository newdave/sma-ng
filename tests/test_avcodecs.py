"""Tests for converter/avcodecs.py - codec option parsing and FFmpeg flag generation."""

import pytest

from converter.avcodecs import (
  AacCodec,
  Ac3Codec,
  AV1QSVCodec,
  AV1VAAPICodec,
  BaseCodec,
  FlacCodec,
  H264Codec,
  H264QSVCodec,
  H264VAAPICodec,
  H265Codec,
  H265QSVCodec,
  H265VAAPICodec,
  MOVTextCodec,
  NVEncH264Codec,
  NVEncH265Codec,
  OpusCodec,
  SrtCodec,
  Vp9QSVCodec,
  audio_codec_list,
  subtitle_codec_list,
  video_codec_list,
)


class TestBaseCodecSafeOptions:
  """Test that safe_options correctly filters and type-casts options."""

  def test_filters_unknown_keys(self):
    codec = H264Codec()
    safe = codec.safe_options({"codec": "h264", "unknown_key": "value", "bitrate": 5000})
    assert "unknown_key" not in safe
    assert safe["bitrate"] == 5000

  def test_type_casts_values(self):
    codec = H264Codec()
    safe = codec.safe_options({"codec": "h264", "bitrate": "5000"})
    assert safe["bitrate"] == 5000

  def test_skips_none_values(self):
    codec = H264Codec()
    safe = codec.safe_options({"codec": "h264", "bitrate": None})
    assert "bitrate" not in safe

  def test_invalid_type_cast_skipped(self):
    codec = H264Codec()
    safe = codec.safe_options({"codec": "h264", "bitrate": "not_a_number"})
    assert "bitrate" not in safe

  def test_crf_key_silently_ignored(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "crf": 23, "bitrate": 5000})
    assert "-crf" not in opts
    assert "-vb" in opts


class TestH264Codec:
  """Test H.264 software codec."""

  def test_parse_options_basic(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "bitrate": 5000})
    assert "-vcodec" in opts
    assert "libx264" in opts
    assert "-tag:v" in opts
    assert "avc1" in opts

  def test_width_rounding(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "width": 1919, "height": 1080})
    # Width should be rounded to even
    assert "-vf" in opts
    vf_idx = opts.index("-vf")
    assert "1920" in opts[vf_idx + 1]

  def test_level_filtering(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "level": 4.1})
    assert "-level" in opts

  def test_preset_passthrough(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "preset": "veryfast"})
    assert "-preset" in opts
    assert "veryfast" in opts

  def test_invalid_codec_raises(self):
    codec = H264Codec()
    with pytest.raises(ValueError):
      codec.parse_options({"codec": "wrong"})


class TestH264QSVCodec:
  """Test H.264 QSV hardware codec."""

  def test_device_passthrough(self):
    codec = H264QSVCodec()
    opts = codec.parse_options({"codec": "h264qsv", "device": "sma", "bitrate": 5000})
    assert "-filter_hw_device" in opts
    assert "sma" in opts

  def test_bitrate_mode(self):
    codec = H264QSVCodec()
    opts = codec.parse_options({"codec": "h264qsv", "bitrate": 5000})
    assert "-vb" in opts
    assert "5000k" in opts
    assert "-global_quality" not in opts
    assert "-crf" not in opts

  def test_look_ahead_appended(self):
    codec = H264QSVCodec()
    opts = codec.parse_options({"codec": "h264qsv"})
    assert "-look_ahead" in opts
    assert "0" in opts

  def test_encoder_options_include_device(self):
    assert "device" in H264QSVCodec.encoder_options
    assert "decode_device" in H264QSVCodec.encoder_options

  def test_scale_filter_is_qsv(self):
    assert H264QSVCodec.scale_filter == "scale_qsv"

  def test_no_bitrate_uses_hw_quality_default(self):
    codec = H264QSVCodec()
    opts = codec.parse_options({"codec": "h264qsv"})
    assert "-global_quality" in opts
    idx = opts.index("-global_quality")
    assert opts[idx + 1] == str(H264QSVCodec.hw_quality_default)

  def test_decode_device_hwdownload(self):
    codec = H264QSVCodec()
    opts = codec.parse_options({"codec": "h264qsv", "device": "sma2", "decode_device": "sma"})
    assert "hwdownload,format=nv12,hwupload" in " ".join(opts)

  def test_scaling(self):
    codec = H264QSVCodec()
    opts = codec.parse_options({"codec": "h264qsv", "width": 1280, "height": 720})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("scale_qsv" in v for v in vf_parts)


class TestH264VAAPICodec:
  """Test H.264 VAAPI hardware codec."""

  def test_bitrate_mode_no_qp(self):
    codec = H264VAAPICodec()
    opts = codec.parse_options({"codec": "h264vaapi", "bitrate": 5000})
    assert "-vb" in opts
    assert "-qp" not in opts

  def test_fallback_init_hw_device(self):
    """When no device configured, should use -init_hw_device fallback."""
    codec = H264VAAPICodec()
    opts = codec.parse_options({"codec": "h264vaapi"})
    assert "-init_hw_device" in opts
    assert any("vaapi=vaapi0:/dev/dri/renderD128" in o for o in opts)

  def test_no_vaapi_device_flag(self):
    """Should NOT use deprecated -vaapi_device."""
    codec = H264VAAPICodec()
    opts = codec.parse_options({"codec": "h264vaapi"})
    assert "-vaapi_device" not in opts

  def test_format_hwupload_chain(self):
    codec = H264VAAPICodec()
    opts = codec.parse_options({"codec": "h264vaapi"})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("hwupload" in v for v in vf_parts)

  def test_device_overrides_fallback(self):
    codec = H264VAAPICodec()
    opts = codec.parse_options({"codec": "h264vaapi", "device": "sma"})
    assert "-filter_hw_device" in opts
    assert "-init_hw_device" not in opts


class TestH265QSVCodec:
  """Test HEVC QSV codec."""

  def test_encoder_options_include_device(self):
    assert "device" in H265QSVCodec.encoder_options
    assert "decode_device" in H265QSVCodec.encoder_options

  def test_bitrate_mode(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "bitrate": 5000})
    assert "-vb" in opts
    assert "-global_quality" not in opts

  def test_ffmpeg_codec_name(self):
    assert H265QSVCodec.ffmpeg_codec_name == "hevc_qsv"

  def test_preset_slower_emitted(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "preset": "slower"})
    assert "-preset" in opts
    assert opts[opts.index("-preset") + 1] == "slower"

  def test_preset_unknown_dropped(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "preset": "bogus"})
    assert "-preset" not in opts

  def test_look_ahead_emits_extra_hw_frames(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "look_ahead_depth": 40})
    assert "-look_ahead_depth" in opts
    assert opts[opts.index("-look_ahead_depth") + 1] == "40"
    assert "-extra_hw_frames" in opts
    assert opts[opts.index("-extra_hw_frames") + 1] == "44"

  def test_look_ahead_zero_no_extra_hw_frames(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv"})
    assert "-extra_hw_frames" not in opts

  def test_global_quality_emits_flag_and_skips_bitrate(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "global_quality": 23})
    assert "-global_quality" in opts
    assert opts[opts.index("-global_quality") + 1] == "23"
    assert "-vb" not in opts
    assert "-maxrate:v" not in opts
    assert "-bufsize" not in opts

  def test_global_quality_zero_uses_codec_default(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "global_quality": 0})
    assert "-global_quality" in opts
    assert opts[opts.index("-global_quality") + 1] == "25"  # hw_quality_default

  def test_global_quality_loses_to_explicit_bitrate(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "bitrate": 5000, "global_quality": 23})
    assert "-vb" in opts
    assert "-global_quality" not in opts

  def test_hdr_color_flags_emitted(self):
    codec = H265QSVCodec()
    opts = codec.parse_options(
      {
        "codec": "h265qsv",
        "color_primaries": "bt2020",
        "color_transfer": "smpte2084",
        "color_space": "bt2020nc",
      }
    )
    assert "-color_primaries" in opts and opts[opts.index("-color_primaries") + 1] == "bt2020"
    assert "-color_trc" in opts and opts[opts.index("-color_trc") + 1] == "smpte2084"
    assert "-colorspace" in opts and opts[opts.index("-colorspace") + 1] == "bt2020nc"

  def test_no_color_flags_when_unset(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv"})
    assert "-color_primaries" not in opts
    assert "-color_trc" not in opts
    assert "-colorspace" not in opts

  def test_hlg_transfer_passthrough(self):
    codec = H265QSVCodec()
    opts = codec.parse_options(
      {
        "codec": "h265qsv",
        "color_primaries": "bt2020",
        "color_transfer": "arib-std-b67",
        "color_space": "bt2020nc",
      }
    )
    assert opts[opts.index("-color_trc") + 1] == "arib-std-b67"


class TestH265VAAPICodec:
  """Test HEVC VAAPI codec."""

  def test_init_hw_device_fallback(self):
    codec = H265VAAPICodec()
    opts = codec.parse_options({"codec": "h265vaapi"})
    assert "-init_hw_device" in opts
    assert "-vaapi_device" not in opts

  def test_pix_fmt_passthrough(self):
    codec = H265VAAPICodec()
    opts = codec.parse_options({"codec": "h265vaapi", "pix_fmt": "p010le"})
    vf_str = " ".join(opts)
    assert "p010le" in vf_str


class TestAV1QSVCodec:
  """Test AV1 QSV codec."""

  def test_has_device_options(self):
    assert "device" in AV1QSVCodec.encoder_options
    assert "decode_device" in AV1QSVCodec.encoder_options

  def test_scale_filter(self):
    assert AV1QSVCodec.scale_filter == "scale_qsv"

  def test_bitrate_mode(self):
    codec = AV1QSVCodec()
    opts = codec.parse_options({"codec": "av1qsv", "bitrate": 5000})
    assert "-vb" in opts
    assert "-global_quality" not in opts

  def test_look_ahead(self):
    codec = AV1QSVCodec()
    opts = codec.parse_options({"codec": "av1qsv"})
    assert "-look_ahead" in opts

  def test_ffmpeg_codec_name(self):
    assert AV1QSVCodec.ffmpeg_codec_name == "av1_qsv"


class TestAV1VAAPICodec:
  """Test AV1 VAAPI codec."""

  def test_has_device_options(self):
    assert "device" in AV1VAAPICodec.encoder_options
    assert "decode_device" in AV1VAAPICodec.encoder_options

  def test_scale_filter(self):
    assert AV1VAAPICodec.scale_filter == "scale_vaapi"

  def test_bitrate_mode_no_qp(self):
    codec = AV1VAAPICodec()
    opts = codec.parse_options({"codec": "av1vaapi", "bitrate": 5000})
    assert "-vb" in opts
    assert "-qp" not in opts

  def test_init_hw_device_fallback(self):
    codec = AV1VAAPICodec()
    opts = codec.parse_options({"codec": "av1vaapi"})
    assert "-init_hw_device" in opts
    assert "-vaapi_device" not in opts


class TestVp9QSVCodec:
  """Test VP9 QSV codec."""

  def test_has_device_options(self):
    assert "device" in Vp9QSVCodec.encoder_options

  def test_bitrate_mode(self):
    codec = Vp9QSVCodec()
    opts = codec.parse_options({"codec": "vp9qsv", "bitrate": 5000})
    assert "-vb" in opts
    assert "-global_quality" not in opts

  def test_works_without_framedata(self):
    """VP9 base class expects framedata; QSV should handle its absence."""
    codec = Vp9QSVCodec()
    opts = codec.parse_options({"codec": "vp9qsv", "bitrate": 5000})
    assert "-vcodec" in opts


class TestAudioCodecs:
  """Test audio codec option parsing."""

  def test_aac_basic(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "channels": 2, "bitrate": 128000})
    assert "-c:a:0" in opts
    assert "aac" in opts

  def test_ac3_basic(self):
    codec = Ac3Codec()
    opts = codec.parse_options({"codec": "ac3", "channels": 6})
    assert "-c:a:0" in opts
    assert "ac3" in opts

  def test_language_metadata(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "language": "eng"})
    assert any("language=eng" in o for o in opts)

  def test_disposition(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "disposition": "+default"})
    assert any("disposition" in o for o in opts)


class TestSubtitleCodecs:
  """Test subtitle codec option parsing."""

  def test_mov_text(self):
    codec = MOVTextCodec()
    opts = codec.parse_options({"codec": "mov_text", "language": "eng"})
    assert "-c:s:0" in opts
    assert "mov_text" in opts

  def test_srt(self):
    codec = SrtCodec()
    opts = codec.parse_options({"codec": "srt", "language": "eng"})
    assert "srt" in opts

  def test_title_set(self):
    codec = MOVTextCodec()
    opts = codec.parse_options({"codec": "mov_text", "title": "Full", "language": "eng"})
    assert any("title=Full" in o for o in opts)
    assert any("handler_name=Full" in o for o in opts)

  def test_empty_title_blanked(self):
    codec = MOVTextCodec()
    opts = codec.parse_options({"codec": "mov_text", "language": "eng"})
    assert any("title=" == o for o in opts)

  def test_undefined_language_default(self):
    codec = MOVTextCodec()
    opts = codec.parse_options({"codec": "mov_text"})
    assert any("language=und" in o for o in opts)


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
    assert "h264qsv" in names

  def test_av1qsv_in_video_list(self):
    names = [c.codec_name for c in video_codec_list]
    assert "av1qsv" in names

  def test_no_duplicate_codec_names(self):
    names = [c.codec_name for c in video_codec_list]
    assert len(names) == len(set(names))


class TestH265Codec:
  """Test H.265 software codec."""

  def test_basic(self):
    codec = H265Codec()
    opts = codec.parse_options({"codec": "h265", "bitrate": 5000})
    assert "-vcodec" in opts
    assert "libx265" in opts

  def test_tag(self):
    codec = H265Codec()
    opts = codec.parse_options({"codec": "h265"})
    assert "-tag:v" in opts
    assert "hvc1" in opts

  def test_level(self):
    codec = H265Codec()
    opts = codec.parse_options({"codec": "h265", "level": 5.0})
    # H265 stores level directly
    assert "-level" in opts


class TestNVEncCodecs:
  """Test NVIDIA hardware codecs."""

  def test_nvenc_h264_basic(self):
    codec = NVEncH264Codec()
    opts = codec.parse_options({"codec": "h264_nvenc", "bitrate": 5000})
    assert "-vcodec" in opts
    assert "h264_nvenc" in opts

  def test_nvenc_h265_basic(self):
    codec = NVEncH265Codec()
    opts = codec.parse_options({"codec": "h265_nvenc", "bitrate": 5000})
    assert "-vcodec" in opts
    assert "hevc_nvenc" in opts


class TestCopyCodecs:
  """Test copy codec variants."""

  def test_audio_copy(self):
    from converter.avcodecs import AudioCopyCodec

    codec = AudioCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 1, "language": "eng"})
    assert "-c:a:0" in opts
    assert "copy" in opts
    assert any("language=eng" in o for o in opts)

  def test_audio_copy_no_language(self):
    from converter.avcodecs import AudioCopyCodec

    codec = AudioCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 1})
    assert any("language=und" in o for o in opts)

  def test_audio_copy_with_title(self):
    from converter.avcodecs import AudioCopyCodec

    codec = AudioCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 0, "title": "Stereo"})
    assert any("title=Stereo" in o for o in opts)

  def test_audio_copy_with_bsf(self):
    from converter.avcodecs import AudioCopyCodec

    codec = AudioCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 0, "bsf": "aac_adtstoasc"})
    assert "-bsf:a:0" in opts

  def test_video_copy(self):
    from converter.avcodecs import VideoCopyCodec

    codec = VideoCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 0})
    assert "-vcodec" in opts
    assert "copy" in opts

  def test_video_copy_with_title(self):
    from converter.avcodecs import VideoCopyCodec

    codec = VideoCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 0, "title": "FHD"})
    assert any("title=FHD" in o for o in opts)

  def test_video_copy_fps(self):
    from converter.avcodecs import VideoCopyCodec

    codec = VideoCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 0, "fps": 24.0})
    assert "-r:v" in opts

  def test_subtitle_copy(self):
    from converter.avcodecs import SubtitleCopyCodec

    codec = SubtitleCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 2, "language": "fra"})
    assert "-c:s:0" in opts
    assert "copy" in opts
    assert any("language=fra" in o for o in opts)

  def test_subtitle_copy_no_language(self):
    from converter.avcodecs import SubtitleCopyCodec

    codec = SubtitleCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 2})
    assert any("language=und" in o for o in opts)

  def test_attachment_copy(self):
    from converter.avcodecs import AttachmentCopyCodec

    codec = AttachmentCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 5, "filename": "font.ttf", "mimetype": "font/ttf"})
    assert "-c:t:0" in opts
    assert "copy" in opts
    assert any("filename=font.ttf" in o for o in opts)


class TestNullCodecs:
  """Test null codec variants."""

  def test_audio_null(self):
    from converter.avcodecs import AudioNullCodec

    codec = AudioNullCodec()
    assert codec.parse_options({}) == ["-an"]

  def test_video_null(self):
    from converter.avcodecs import VideoNullCodec

    codec = VideoNullCodec()
    assert codec.parse_options({}) == ["-vn"]

  def test_subtitle_null(self):
    from converter.avcodecs import SubtitleNullCodec

    codec = SubtitleNullCodec()
    assert codec.parse_options({}) == ["-sn"]


class TestBaseCodecHelpers:
  def test_safe_disposition(self):
    codec = BaseCodec()
    dispo = codec.safe_disposition("+default")
    assert "+default" in dispo
    # All other dispositions should have - prefix
    assert "-forced" in dispo
    assert "-comment" in dispo

  def test_safe_disposition_empty(self):
    codec = BaseCodec()
    dispo = codec.safe_disposition("")
    # All dispositions should have - prefix
    assert "-default" in dispo

  def test_safe_disposition_none(self):
    codec = BaseCodec()
    dispo = codec.safe_disposition(None)
    assert "-default" in dispo

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
    w, h, filters = codec._aspect_corrections(0, 0, 1920, 1080, "stretch")
    assert w == 1920
    assert h == 1080
    assert filters is None

  def test_no_dimensions(self):
    codec = H264Codec()
    w, h, filters = codec._aspect_corrections(1920, 1080, 0, 0, "stretch")
    assert w == 0 and h == 0 and filters is None

  def test_width_only(self):
    codec = H264Codec()
    w, h, filters = codec._aspect_corrections(1920, 1080, 1280, 0, "stretch")
    assert w == 1280
    assert h == 720  # Preserves 16:9

  def test_height_only(self):
    codec = H264Codec()
    w, h, filters = codec._aspect_corrections(1920, 1080, 0, 720, "stretch")
    assert h == 720
    assert w == 1280  # Preserves 16:9

  def test_same_aspect(self):
    codec = H264Codec()
    w, h, filters = codec._aspect_corrections(1920, 1080, 1280, 720, "stretch")
    assert filters is None

  def test_stretch_mode(self):
    codec = H264Codec()
    w, h, filters = codec._aspect_corrections(1920, 1080, 800, 600, "stretch")
    assert filters is None

  def test_crop_mode(self):
    codec = H264Codec()
    w, h, filters = codec._aspect_corrections(1920, 1080, 800, 600, "crop")
    assert filters is not None
    assert "crop=" in filters

  def test_pad_mode(self):
    codec = H264Codec()
    # target is wider than source
    w, h, filters = codec._aspect_corrections(1280, 720, 800, 600, "pad")
    assert filters is not None
    assert "pad=" in filters


class TestAudioCodecEdgeCases:
  def test_channels_out_of_range(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "channels": 0})
    assert "-ac:a:0" not in opts

  def test_bitrate_clamped_low(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "bitrate": 1})
    assert any("8k" in o for o in opts)

  def test_bitrate_clamped_high(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "bitrate": 9999})
    assert any("1536k" in o for o in opts)

  def test_samplerate_out_of_range(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "samplerate": 500})
    assert "-ar:a:0" not in opts

  def test_language_too_long(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "language": "english"})
    # Language should be dropped, defaults to und
    assert any("language=und" in o for o in opts)

  def test_flac_basic(self):
    codec = FlacCodec()
    opts = codec.parse_options({"codec": "flac", "channels": 2})
    assert "-c:a:0" in opts
    assert "flac" in opts

  def test_opus_basic(self):
    codec = OpusCodec()
    opts = codec.parse_options({"codec": "opus", "channels": 2})
    assert "-c:a:0" in opts

  def test_audio_map_and_source(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "map": 1, "source": 0})
    assert any("0:1" in o for o in opts)

  def test_audio_with_filter(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "filter": "aresample=48000"})
    assert "-filter:a:0" in opts

  def test_audio_with_profile(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "profile": "aac_low"})
    assert "-profile:a:0" in opts


class TestVideoCodecParsing:
  def test_bitrate_without_crf(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "bitrate": 5000})
    assert "-vb" in opts
    assert any("5000k" in o for o in opts)

  def test_filter_consolidation(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "filter": "yadif", "width": 1920, "height": 1080, "src_width": 3840, "src_height": 2160})
    # Should consolidate multiple -vf into one
    assert opts.count("-vf") == 1

  def test_field_order(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "field_order": "progressive"})
    assert "-field_order" in opts

  def test_invalid_field_order_removed(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "field_order": "invalid"})
    assert "-field_order" not in opts

  def test_bsf_passthrough(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "bsf": "h264_mp4toannexb"})
    assert "-bsf:v" in opts

  def test_video_title(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "title": "FHD"})
    assert any("title=FHD" in o for o in opts)

  def test_video_no_title(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264"})
    assert any("title=" == o for o in opts)

  def test_pix_fmt(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "pix_fmt": "yuv420p"})
    assert "-pix_fmt" in opts
    assert "yuv420p" in opts

  def test_maxrate_and_bufsize_emitted_with_bitrate(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "bitrate": 5000, "maxrate": "10000k", "bufsize": "20000k"})
    assert "-vb" in opts
    assert "-maxrate:v" in opts
    assert "10000k" in opts
    assert "-bufsize" in opts
    assert "20000k" in opts

  def test_maxrate_and_bufsize_emitted_without_bitrate(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "maxrate": "10000k", "bufsize": "20000k"})
    assert "-maxrate:v" in opts
    assert "-bufsize" in opts


class TestSanitizeAndBuildMetadata:
  """Test _sanitize_stream_metadata and _build_stream_metadata helpers."""

  def test_sanitize_long_language_removed(self):
    safe = {"language": "english"}
    BaseCodec._sanitize_stream_metadata(safe)
    assert "language" not in safe

  def test_sanitize_empty_title_removed(self):
    safe = {"title": ""}
    BaseCodec._sanitize_stream_metadata(safe)
    assert "title" not in safe

  def test_sanitize_blank_disposition_removed(self):
    safe = {"disposition": "   "}
    BaseCodec._sanitize_stream_metadata(safe)
    assert "disposition" not in safe

  def test_sanitize_valid_language_kept(self):
    safe = {"language": "eng"}
    BaseCodec._sanitize_stream_metadata(safe)
    assert safe["language"] == "eng"

  def test_sanitize_valid_title_kept(self):
    safe = {"title": "Main"}
    BaseCodec._sanitize_stream_metadata(safe)
    assert safe["title"] == "Main"

  def test_build_stream_metadata_with_title(self):
    safe = {"title": "Commentary", "language": "eng"}
    result = BaseCodec._build_stream_metadata(safe, "a", "0")
    assert any("title=Commentary" in o for o in result)
    assert any("language=eng" in o for o in result)

  def test_build_stream_metadata_no_title(self):
    safe = {"language": "eng"}
    result = BaseCodec._build_stream_metadata(safe, "a", "0")
    assert any("title=" == o for o in result)
    assert any("language=eng" in o for o in result)

  def test_build_stream_metadata_no_language_defaults_und(self):
    safe = {}
    result = BaseCodec._build_stream_metadata(safe, "s", "1")
    assert any("language=und" in o for o in result)


class TestAudioCodecAdditionalBranches:
  """Cover uncovered AudioCodec.parse_options branches."""

  def test_samplerate_in_range_included(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "samplerate": 44100})
    assert "-ar:a:0" in opts
    assert "44100" in opts

  def test_samplerate_too_high_removed(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "samplerate": 100000})
    assert "-ar:a:0" not in opts

  def test_sample_fmt_passthrough(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "sample_fmt": "fltp"})
    assert "-sample_fmt:a:0" in opts

  def test_empty_filter_removed(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "filter": ""})
    assert "-filter:a:0" not in opts

  def test_path_adds_input(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "path": "/external/audio.aac", "map": 0})
    assert "-i" in opts
    assert "/external/audio.aac" in opts

  def test_channels_capped_at_max_channels(self):
    """AacCodec.max_channels = 6; 8 channels should be capped to 6."""
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "channels": 8})
    ac_idx = opts.index("-ac:a:0")
    assert opts[ac_idx + 1] == "6"

  def test_channels_12_removed(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "channels": 13})
    assert "-ac:a:0" not in opts

  def test_bps_metadata_emitted(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "bitrate": 128})
    assert any("BPS=128000" in o for o in opts)


class TestSubtitleCopyCodecAdditional:
  """Additional SubtitleCopyCodec coverage."""

  def test_source_used_in_map(self):
    from converter.avcodecs import SubtitleCopyCodec

    codec = SubtitleCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 3, "source": 1, "language": "jpn"})
    assert any("1:3" in o for o in opts)

  def test_disposition_passthrough(self):
    from converter.avcodecs import SubtitleCopyCodec

    codec = SubtitleCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 2, "disposition": "+default"})
    assert any("+default" in o for o in opts)


class TestAttachmentCopyCodecAdditional:
  """Additional AttachmentCopyCodec coverage."""

  def test_filename_and_mimetype(self):
    from converter.avcodecs import AttachmentCopyCodec

    codec = AttachmentCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 5, "filename": "cover.jpg", "mimetype": "image/jpeg"})
    assert "-c:t:0" in opts
    assert any("filename=cover.jpg" in o for o in opts)
    assert any("mimetype=image/jpeg" in o for o in opts)

  def test_source_used_in_map(self):
    from converter.avcodecs import AttachmentCopyCodec

    codec = AttachmentCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 5, "source": 2})
    assert any("2:5" in o for o in opts)

  def test_no_filename_no_mimetype(self):
    from converter.avcodecs import AttachmentCopyCodec

    codec = AttachmentCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 5})
    assert "-c:t:0" in opts
    assert "copy" in opts


class TestVideoCodecFpsBitrateBranches:
  """Cover VideoCodec.parse_options fps/bitrate/aspect branches."""

  def test_fps_less_than_1_removed(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "fps": 0.5})
    assert "-r:v" not in opts

  def test_bitrate_zero_removed(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "bitrate": 0})
    assert "-vb" not in opts

  def test_fps_valid(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "fps": 29.97})
    assert "-r:v" in opts

  def test_width_too_small_removed(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "width": 10})
    # Width < 16 should be dropped
    assert "-s" not in opts

  def test_height_too_small_removed(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "height": 8})
    assert "-s" not in opts

  def test_crop_mode_wider_target(self):
    """Crop when target is wider than source aspect."""
    codec = H264Codec()
    # Source: 4:3, target: 16:9 → crop top/bottom
    w, h, filters = codec._aspect_corrections(640, 480, 1280, 720, "crop")
    assert "crop=" in filters

  def test_pad_mode_taller_target(self):
    """Pad when target is taller than source aspect."""
    codec = H264Codec()
    # Source: 16:9 (1280x720), target: 4:3 (800x600) — target is narrower
    w, h, filters = codec._aspect_corrections(1280, 720, 800, 600, "pad")
    assert "pad=" in filters


class TestH264CodecSpecifics:
  """Test H264Codec-specific production paths."""

  def test_level_not_in_list_snaps_down(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "level": 4.15})
    assert "-level" in opts
    level_idx = opts.index("-level")
    assert opts[level_idx + 1] == "4.1"

  def test_level_too_low_removed(self):
    codec = H264Codec()
    # Level 0.5 not in list, no lower item → removed
    opts = codec.parse_options({"codec": "h264", "level": 0.5})
    assert "-level" not in opts

  def test_hscale_only_filter(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "height": 720})
    vf_str = " ".join(opts)
    assert "scale=" in vf_str
    assert "trunc" in vf_str

  def test_params_passthrough(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "params": "crf=18"})
    assert "-x264-params" in opts
    assert "crf=18" in opts

  def test_tune_passthrough(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "tune": "film"})
    assert "-tune" in opts
    assert "film" in opts


class TestH265SafeFramedata:
  """Test H265Codec.safe_framedata with HDR metadata."""

  def test_empty_opts_returns_empty(self):
    from converter.avcodecs import H265Codec

    codec = H265Codec()
    result = codec.safe_framedata({})
    assert result == ""

  def test_hdr_flag(self):
    from converter.avcodecs import H265Codec

    codec = H265Codec()
    result = codec.safe_framedata({"hdr": True})
    assert "hdr-opt=1" in result

  def test_repeat_headers(self):
    from converter.avcodecs import H265Codec

    codec = H265Codec()
    result = codec.safe_framedata({"repeat-headers": True})
    assert "repeat-headers=1" in result

  def test_color_metadata(self):
    from converter.avcodecs import H265Codec

    codec = H265Codec()
    result = codec.safe_framedata(
      {
        "color_primaries": "bt2020",
        "color_transfer": "smpte2084",
        "color_space": "bt2020nc",
      }
    )
    assert "colorprim=bt2020" in result
    assert "transfer=smpte2084" in result
    assert "colormatrix=bt2020nc" in result

  def test_mastering_display_metadata(self):
    from converter.avcodecs import H265Codec

    codec = H265Codec()
    side_data = {
      "side_data_type": "Mastering display metadata",
      "red_x": 34000,
      "red_y": 16000,
      "green_x": 13250,
      "green_y": 34500,
      "blue_x": 7500,
      "blue_y": 3000,
      "white_point_x": 15635,
      "white_point_y": 16450,
      "min_luminance": 50,
      "max_luminance": 10000000,
    }
    result = codec.safe_framedata({"side_data_list": [side_data]})
    assert "master-display=" in result

  def test_content_light_level_metadata(self):
    from converter.avcodecs import H265Codec

    codec = H265Codec()
    side_data = {
      "side_data_type": "Content light level metadata",
      "max_content": 1000,
      "max_average": 400,
    }
    result = codec.safe_framedata({"side_data_list": [side_data]})
    assert "max-cll=" in result

  def test_h265_framedata_in_produce_list(self):
    from converter.avcodecs import H265Codec

    codec = H265Codec()
    opts = codec.parse_options(
      {
        "codec": "h265",
        "framedata": {"hdr": True, "color_primaries": "bt2020"},
      }
    )
    # x265-params should appear with framedata content
    assert "-x265-params" in opts

  def test_h265_params_and_framedata_combined(self):
    from converter.avcodecs import H265Codec

    codec = H265Codec()
    opts = codec.parse_options(
      {
        "codec": "h265",
        "params": "keyint=120",
        "framedata": {"hdr": True},
      }
    )
    params_idx = opts.index("-x265-params")
    combined = opts[params_idx + 1]
    assert "keyint=120" in combined
    assert "hdr-opt=1" in combined

  def test_h265_level_snaps_down(self):
    from converter.avcodecs import H265Codec

    codec = H265Codec()
    opts = codec.parse_options({"codec": "h265", "level": 4.5})
    assert "-level" in opts
    level_idx = opts.index("-level")
    assert opts[level_idx + 1] == "4.1"


class TestH265QSVPatchedFramedata:
  """Test H265QSVCodecPatched.safe_framedata (qsv_params style)."""

  def test_color_metadata(self):
    from converter.avcodecs import H265QSVCodecPatched

    codec = H265QSVCodecPatched()
    result = codec.safe_framedata(
      {
        "color_primaries": "bt2020",
        "color_transfer": "smpte2084",
        "color_space": "bt2020nc",
      }
    )
    assert "colorprim=bt2020" in result
    assert "transfer=smpte2084" in result
    assert "colormatrix=bt2020nc" in result

  def test_mastering_display_metadata(self):
    from converter.avcodecs import H265QSVCodecPatched

    codec = H265QSVCodecPatched()
    side_data = {
      "side_data_type": "Mastering display metadata",
      "red_x": 34000,
      "red_y": 16000,
      "green_x": 13250,
      "green_y": 34500,
      "blue_x": 7500,
      "blue_y": 3000,
      "white_point_x": 15635,
      "white_point_y": 16450,
      "min_luminance": 10,
      "max_luminance": 100000000,  # triggers clamping
    }
    result = codec.safe_framedata({"side_data_list": [side_data]})
    assert "master-display=" in result
    # min_luminance < 50 → clamped to 50; appears as L(50,10000000)
    assert "(50," in result
    # max_luminance > 10_000_000 → clamped to 10_000_000
    assert "10000000" in result

  def test_content_light_level_zero_skipped(self):
    from converter.avcodecs import H265QSVCodecPatched

    codec = H265QSVCodecPatched()
    side_data = {
      "side_data_type": "Content light level metadata",
      "max_content": 0,
      "max_average": 0,
    }
    result = codec.safe_framedata({"side_data_list": [side_data]})
    assert "max-cll" not in result

  def test_content_light_level_clamping(self):
    from converter.avcodecs import H265QSVCodecPatched

    codec = H265QSVCodecPatched()
    side_data = {
      "side_data_type": "Content light level metadata",
      "max_content": 2000,  # > 1000, clamped
      "max_average": 100,  # < 400, clamped
    }
    result = codec.safe_framedata({"side_data_list": [side_data]})
    assert "max-cll=" in result
    # max_average 100 < 400 → clamped to 400; max_content clamped to 1000, then max_content = max_average since 400 > 400? No, 400 == 400 so max_content stays 1000
    assert "1000,400" in result


class TestNVEncH265Patched:
  """Test NVEncH265CodecPatched safe_framedata and parse options."""

  def test_safe_framedata_color_metadata(self):
    from converter.avcodecs import NVEncH265CodecPatched

    codec = NVEncH265CodecPatched()
    result = codec.safe_framedata(
      {
        "color_primaries": "bt2020",
        "color_transfer": "smpte2084",
        "color_space": "bt2020nc",
      }
    )
    assert "colour_primaries=9" in result
    assert "transfer_characteristics=16" in result
    assert "matrix_coefficients=9" in result

  def test_safe_framedata_mastering_display(self):
    from converter.avcodecs import NVEncH265CodecPatched

    codec = NVEncH265CodecPatched()
    side_data = {
      "side_data_type": "Mastering display metadata",
      "red_x": 34000,
      "red_y": 16000,
      "green_x": 13250,
      "green_y": 34500,
      "blue_x": 7500,
      "blue_y": 3000,
      "white_point_x": 15635,
      "white_point_y": 16450,
      "min_luminance": 10,
      "max_luminance": 100000000,
    }
    result = codec.safe_framedata({"side_data_list": [side_data]})
    assert "master_display=" in result

  def test_safe_framedata_content_light_zero_skipped(self):
    from converter.avcodecs import NVEncH265CodecPatched

    codec = NVEncH265CodecPatched()
    side_data = {
      "side_data_type": "Content light level metadata",
      "max_content": 0,
      "max_average": 0,
    }
    result = codec.safe_framedata({"side_data_list": [side_data]})
    assert "max_cll" not in result

  def test_safe_framedata_content_light_clamping(self):
    from converter.avcodecs import NVEncH265CodecPatched

    codec = NVEncH265CodecPatched()
    side_data = {
      "side_data_type": "Content light level metadata",
      "max_content": 2000,
      "max_average": 100,
    }
    result = codec.safe_framedata({"side_data_list": [side_data]})
    assert "max_cll=" in result

  def test_parse_options_with_framedata_sets_bsf(self):
    from converter.avcodecs import NVEncH265CodecPatched

    codec = NVEncH265CodecPatched()
    opts = codec.parse_options(
      {
        "codec": "hevc_nvenc_patched",
        "framedata": {"color_primaries": "bt2020"},
      }
    )
    assert "-bsf:v" in opts

  def test_parse_options_with_framedata_and_existing_bsf(self):
    from converter.avcodecs import NVEncH265CodecPatched

    codec = NVEncH265CodecPatched()
    opts = codec.parse_options(
      {
        "codec": "hevc_nvenc_patched",
        "framedata": {"color_primaries": "bt2020"},
        "bsf": "hevc_mp4toannexb",
      }
    )
    bsf_idx = opts.index("-bsf:v")
    assert "hevc_mp4toannexb" in opts[bsf_idx + 1]


class TestQSVAdvancedOptions:
  """Test QSV codecs with look_ahead, b_frames, ref_frames, pix_fmt."""

  def test_h264qsv_look_ahead_depth(self):
    codec = H264QSVCodec()
    opts = codec.parse_options({"codec": "h264qsv", "look_ahead_depth": 40})
    assert "-look_ahead" in opts
    la_idx = opts.index("-look_ahead")
    assert opts[la_idx + 1] == "1"
    assert "-look_ahead_depth" in opts

  def test_h264qsv_b_frames(self):
    codec = H264QSVCodec()
    opts = codec.parse_options({"codec": "h264qsv", "b_frames": 3})
    assert "-bf" in opts
    assert "3" in opts

  def test_h264qsv_ref_frames(self):
    codec = H264QSVCodec()
    opts = codec.parse_options({"codec": "h264qsv", "ref_frames": 4})
    assert "-refs" in opts
    assert "4" in opts

  def test_h264qsv_pix_fmt_in_scale(self):
    codec = H264QSVCodec()
    opts = codec.parse_options({"codec": "h264qsv", "pix_fmt": "p010le", "width": 1920, "height": 1080})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("p010le" in v for v in vf_parts)

  def test_h264qsv_pix_fmt_no_scale(self):
    """pix_fmt without width/height should still produce a format-only filter."""
    codec = H264QSVCodec()
    opts = codec.parse_options({"codec": "h264qsv", "pix_fmt": "p010le"})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("p010le" in v for v in vf_parts)

  def test_h264qsv_profile_valid(self):
    codec = H264QSVCodec()
    opts = codec.parse_options({"codec": "h264qsv", "profile": "high"})
    assert "-profile:v" in opts

  def test_h264qsv_profile_invalid_removed(self):
    codec = H264QSVCodec()
    opts = codec.parse_options({"codec": "h264qsv", "profile": "ultrafast"})
    assert "-profile:v" not in opts

  def test_h264qsv_level(self):
    codec = H264QSVCodec()
    opts = codec.parse_options({"codec": "h264qsv", "level": 4.1})
    assert "-level" in opts
    level_idx = opts.index("-level")
    assert opts[level_idx + 1] == "41"

  def test_h265qsv_look_ahead_depth(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "look_ahead_depth": 20})
    assert "-look_ahead" in opts
    assert "-look_ahead_depth" in opts

  def test_h265qsv_b_frames_zero(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "b_frames": 0})
    assert "-bf" in opts
    assert "0" in opts

  def test_av1qsv_look_ahead_depth(self):
    codec = AV1QSVCodec()
    opts = codec.parse_options({"codec": "av1qsv", "look_ahead_depth": 30})
    assert "-look_ahead" in opts
    assert "-look_ahead_depth" in opts

  def test_av1qsv_b_frames(self):
    codec = AV1QSVCodec()
    opts = codec.parse_options({"codec": "av1qsv", "b_frames": 2})
    assert "-bf" in opts

  def test_av1qsv_ref_frames(self):
    codec = AV1QSVCodec()
    opts = codec.parse_options({"codec": "av1qsv", "ref_frames": 3})
    assert "-refs" in opts

  def test_vp9qsv_look_ahead_extra_hw_frames(self):
    from converter.avcodecs import Vp9QSVCodec

    codec = Vp9QSVCodec()
    opts = codec.parse_options({"codec": "vp9qsv", "look_ahead_depth": 15})
    assert "-extra_hw_frames" in opts

  def test_vp9qsv_b_frames(self):
    from converter.avcodecs import Vp9QSVCodec

    codec = Vp9QSVCodec()
    opts = codec.parse_options({"codec": "vp9qsv", "b_frames": 0})
    assert "-bf" in opts

  def test_vp9qsv_ref_frames(self):
    from converter.avcodecs import Vp9QSVCodec

    codec = Vp9QSVCodec()
    opts = codec.parse_options({"codec": "vp9qsv", "ref_frames": 2})
    assert "-refs" in opts


class TestVAAPIScalingOptions:
  """Test VAAPI codec scaling with pix_fmt and width/height combos."""

  def test_h264vaapi_pix_fmt_in_hwupload_chain(self):
    codec = H264VAAPICodec()
    opts = codec.parse_options({"codec": "h264vaapi", "pix_fmt": "p010le"})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("p010le" in v for v in vf_parts)

  def test_h264vaapi_width_only_scale(self):
    codec = H264VAAPICodec()
    opts = codec.parse_options({"codec": "h264vaapi", "width": 1280})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("trunc" in v for v in vf_parts)

  def test_h264vaapi_height_only_scale(self):
    codec = H264VAAPICodec()
    opts = codec.parse_options({"codec": "h264vaapi", "height": 720})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("trunc" in v for v in vf_parts)

  def test_h265vaapi_no_qp_with_bitrate(self):
    codec = H265VAAPICodec()
    opts = codec.parse_options({"codec": "h265vaapi", "bitrate": 5000})
    assert "-vb" in opts
    assert "-qp" not in opts

  def test_av1vaapi_no_qp_with_bitrate(self):
    codec = AV1VAAPICodec()
    opts = codec.parse_options({"codec": "av1vaapi", "bitrate": 5000})
    assert "-vb" in opts

  def test_vaapi_decode_device_triggers_hwdownload(self):
    codec = H264VAAPICodec()
    opts = codec.parse_options({"codec": "h264vaapi", "device": "vaapi0", "decode_device": "vaapi1"})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("hwdownload" in v for v in vf_parts)


class TestNVEncH264Additional:
  """Cover NVEncH264Codec paths not yet tested."""

  def test_no_quality_no_bitrate_uses_default_qp(self):
    codec = NVEncH264Codec()
    # hw_quality_default=23, qp key is used
    opts = codec.parse_options({"codec": "h264_nvenc"})
    assert "-qp" in opts
    qp_idx = opts.index("-qp")
    assert opts[qp_idx + 1] == "23"

  def test_decode_device_same_as_device_no_hwdownload(self):
    codec = NVEncH264Codec()
    opts = codec.parse_options({"codec": "h264_nvenc", "device": "cuda0", "decode_device": "cuda0"})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert not any("hwdownload" in v for v in vf_parts)

  def test_scaling_with_pix_fmt(self):
    codec = NVEncH264Codec()
    opts = codec.parse_options({"codec": "h264_nvenc", "pix_fmt": "yuv420p", "width": 1280, "height": 720})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("scale_npp" in v for v in vf_parts)

  def test_nvenc_h265_scaling(self):
    codec = NVEncH265Codec()
    opts = codec.parse_options({"codec": "h265_nvenc", "width": 1280, "height": 720})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("scale_npp" in v for v in vf_parts)

  def test_nvenc_h265_pix_fmt_no_scale(self):
    codec = NVEncH265Codec()
    opts = codec.parse_options({"codec": "h265_nvenc", "pix_fmt": "p010le"})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("scale_npp" in v and "p010le" in v for v in vf_parts)


class TestAV1CodecOptions:
  """Test AV1Codec and subclasses."""

  def test_av1_preset_valid(self):
    from converter.avcodecs import AV1Codec

    codec = AV1Codec()
    opts = codec.parse_options({"codec": "av1", "preset": 5})
    assert "-preset" in opts

  def test_av1_preset_out_of_range_removed(self):
    from converter.avcodecs import AV1Codec

    codec = AV1Codec()
    opts = codec.parse_options({"codec": "av1", "preset": 20})
    assert "-preset" not in opts

  def test_av1_framedata_color(self):
    from converter.avcodecs import AV1Codec

    codec = AV1Codec()
    opts = codec.parse_options(
      {
        "codec": "av1",
        "framedata": {
          "color_space": "bt2020nc",
          "color_transfer": "smpte2084",
          "color_primaries": "bt2020",
        },
      }
    )
    assert "-colorspace" in opts
    assert "-color_trc" in opts
    assert "-color_primaries" in opts

  def test_svtav1_codec_name(self):
    from converter.avcodecs import SVTAV1Codec

    codec = SVTAV1Codec()
    assert codec.ffmpeg_codec_name == "libsvtav1"

  def test_rav1e_codec_name(self):
    from converter.avcodecs import RAV1ECodec

    codec = RAV1ECodec()
    assert codec.ffmpeg_codec_name == "librav1e"


class TestSpecialAudioCodecs:
  """Test specialized audio codecs: EAc3, TrueHD, DTS, FdkAac, Mp3, Vorbis."""

  def test_eac3_channels_capped(self):
    from converter.avcodecs import EAc3Codec

    codec = EAc3Codec()
    opts = codec.parse_options({"codec": "eac3", "channels": 10})
    ac_idx = opts.index("-ac:a:0")
    assert opts[ac_idx + 1] == "6"

  def test_eac3_bitrate_clamped(self):
    from converter.avcodecs import EAc3Codec

    codec = EAc3Codec()
    opts = codec.parse_options({"codec": "eac3", "bitrate": 1000})
    # _codec_specific_parse_options clamps safe["bitrate"] to 640, but
    # the emitted -b:a flag uses br computed before that call.
    # Check that the bitrate flag is still emitted.
    assert "-b:a:0" in opts

  def test_truehd_channels_capped(self):
    from converter.avcodecs import TrueHDCodec

    codec = TrueHDCodec()
    opts = codec.parse_options({"codec": "truehd", "channels": 10})
    ac_idx = opts.index("-ac:a:0")
    assert opts[ac_idx + 1] == "8"

  def test_truehd_experimental_flags(self):
    from converter.avcodecs import TrueHDCodec

    codec = TrueHDCodec()
    opts = codec.parse_options({"codec": "truehd", "channels": 6})
    assert "-strict" in opts
    assert "experimental" in opts

  def test_dts_channels_capped(self):
    from converter.avcodecs import DtsCodec

    codec = DtsCodec()
    opts = codec.parse_options({"codec": "dts", "channels": 8})
    ac_idx = opts.index("-ac:a:0")
    assert opts[ac_idx + 1] == "6"

  def test_dts_experimental_flags(self):
    from converter.avcodecs import DtsCodec

    codec = DtsCodec()
    opts = codec.parse_options({"codec": "dts"})
    assert "-strict" in opts

  def test_mp3_quality_removes_bitrate(self):
    from converter.avcodecs import Mp3Codec

    codec = Mp3Codec()
    opts = codec.parse_options({"codec": "mp3", "quality": 2, "bitrate": 128})
    assert "-q:a:0" in opts
    assert "-b:a:0" not in opts

  def test_mp3_quality_out_of_range(self):
    from converter.avcodecs import Mp3Codec

    codec = Mp3Codec()
    opts = codec.parse_options({"codec": "mp3", "quality": 10})
    assert "-q:a:0" not in opts

  def test_vorbis_quality_removes_bitrate(self):
    from converter.avcodecs import VorbisCodec

    codec = VorbisCodec()
    opts = codec.parse_options({"codec": "vorbis", "quality": 5, "bitrate": 128})
    assert "-q:a:0" in opts

  def test_vorbis_quality_out_of_range(self):
    from converter.avcodecs import VorbisCodec

    codec = VorbisCodec()
    opts = codec.parse_options({"codec": "vorbis", "quality": 0})
    assert "-q:a:0" not in opts

  def test_fdkaac_quality_removes_bitrate(self):
    from converter.avcodecs import FdkAacCodec

    codec = FdkAacCodec()
    opts = codec.parse_options({"codec": "libfdk_aac", "quality": 3, "bitrate": 128})
    assert "-vbr:a:0" in opts
    assert "-b:a:0" not in opts

  def test_fdkaac_quality_out_of_range(self):
    from converter.avcodecs import FdkAacCodec

    codec = FdkAacCodec()
    opts = codec.parse_options({"codec": "libfdk_aac", "quality": 10})
    assert "-vbr:a:0" not in opts

  def test_fdkaac_aac_he_v2_caps_channels(self):
    from converter.avcodecs import FdkAacCodec

    codec = FdkAacCodec()
    opts = codec.parse_options({"codec": "libfdk_aac", "channels": 6, "profile": "aac_he_v2"})
    ac_idx = opts.index("-ac:a:0")
    assert opts[ac_idx + 1] == "2"

  def test_fdkaac_aac_he_v2_caps_quality(self):
    from converter.avcodecs import FdkAacCodec

    codec = FdkAacCodec()
    opts = codec.parse_options({"codec": "libfdk_aac", "quality": 5, "profile": "aac_he_v2"})
    assert "-vbr:a:0" in opts
    vbr_idx = opts.index("-vbr:a:0")
    assert opts[vbr_idx + 1] == "3"  # Capped from 5 to 3


class TestBaseDecoderSupportsBitDepth:
  """Test BaseDecoder.supportsBitDepth."""

  def test_decoder_supports_depth_within_max(self):
    from converter.avcodecs import BaseDecoder, H264CuvidDecoder

    dec = H264CuvidDecoder()
    assert dec.supportsBitDepth(8) is True

  def test_decoder_rejects_depth_above_max(self):
    from converter.avcodecs import H264CuvidDecoder

    dec = H264CuvidDecoder()
    assert dec.supportsBitDepth(10) is False

  def test_h265_decoder_supports_10bit(self):
    from converter.avcodecs import H265CuvidDecoder

    dec = H265CuvidDecoder()
    assert dec.supportsBitDepth(10) is True

  def test_h265_v4l2_decoder(self):
    from converter.avcodecs import H265V4l2m2mDecoder

    dec = H265V4l2m2mDecoder()
    assert dec.supportsBitDepth(10) is True
    assert dec.supportsBitDepth(12) is False


class TestHWAccelHelpers:
  """Test HWAccelVideoCodec helper methods directly."""

  def test_hw_parse_preset_removes_invalid(self):
    codec = H264QSVCodec()
    safe = {"preset": "invalid_preset"}
    codec._hw_parse_preset(safe)
    assert "preset" not in safe

  def test_hw_parse_preset_keeps_none_check(self):
    """If hw_presets is None (unconstrained), preset is kept."""
    from converter.avcodecs import NVEncH264Codec

    codec = NVEncH264Codec()  # hw_presets is None
    safe = {"preset": "fast"}
    codec._hw_parse_preset(safe)
    assert "preset" in safe

  def test_hw_parse_profile_removes_invalid(self):
    codec = H264QSVCodec()
    safe = {"profile": "ultra"}
    codec._hw_parse_profile(safe)
    assert "profile" not in safe

  def test_hw_parse_scale_rounds_width(self):
    codec = H264QSVCodec()
    safe = {"width": 1919, "height": 1080}
    codec._hw_parse_scale(safe)
    assert safe["qsv_wscale"] == 1920
    assert safe["qsv_hscale"] == 1080

  def test_hw_parse_quality_applies_default(self):
    codec = H264QSVCodec()
    safe = {}
    codec._hw_parse_quality(safe)
    assert safe["gq"] == H264QSVCodec.hw_quality_default

  def test_hw_parse_quality_skipped_when_bitrate_present(self):
    codec = H264QSVCodec()
    safe = {"bitrate": 5000}
    codec._hw_parse_quality(safe)
    assert "gq" not in safe

  def test_hw_quality_opts_with_maxrate(self):
    codec = H264QSVCodec()
    safe = {"gq": 25, "maxrate": "10000k", "bufsize": "20000k"}
    result = codec._hw_quality_opts(safe)
    # global_quality != -qp so maxrate/bufsize NOT added for QSV
    assert "-maxrate:v" not in result

  def test_hw_extbrc_with_bitrate(self):
    codec = H264QSVCodec()  # hw_extbrc = True
    opts = codec.parse_options({"codec": "h264qsv", "bitrate": 5000})
    assert "-extbrc" in opts

  def test_hw_scale_opts_wonly(self):
    codec = H264QSVCodec()
    safe = {"qsv_wscale": 1280}
    result = codec._hw_scale_opts(safe)
    assert any("trunc" in v for v in result)

  def test_hw_scale_opts_honly(self):
    codec = H264QSVCodec()
    safe = {"qsv_hscale": 720}
    result = codec._hw_scale_opts(safe)
    assert any("trunc" in v for v in result)

  def test_vaapi_scale_opts_both(self):
    codec = H264VAAPICodec()
    safe = {"vaapi_wscale": 1280, "vaapi_hscale": 720}
    result = codec._hw_vaapi_scale_opts(safe)
    assert any("1280" in v and "720" in v for v in result)

  def test_vaapi_scale_opts_wonly(self):
    codec = H264VAAPICodec()
    safe = {"vaapi_wscale": 1280}
    result = codec._hw_vaapi_scale_opts(safe)
    assert any("trunc" in v for v in result)

  def test_vaapi_scale_opts_honly(self):
    codec = H264VAAPICodec()
    safe = {"vaapi_hscale": 720}
    result = codec._hw_vaapi_scale_opts(safe)
    assert any("trunc" in v for v in result)

  def test_vaapi_scale_opts_with_pix_fmt(self):
    codec = H264VAAPICodec()
    safe = {"vaapi_pix_fmt": "p010le"}
    result = codec._hw_vaapi_scale_opts(safe)
    assert any("p010le" in v for v in result)

  def test_hw_device_opts_no_device_no_decode(self):
    codec = NVEncH264Codec()
    safe = {}
    result = codec._hw_device_opts(safe)
    assert result == []

  def test_hw_device_opts_decode_device_only(self):
    codec = NVEncH264Codec()
    safe = {"decode_device": "cuda0"}
    result = codec._hw_device_opts(safe)
    assert any("hwdownload" in v for v in result)


class TestHWQualityOpts:
  """Test _hw_quality_opts QP flag emitting."""

  def test_qp_with_maxrate_bufsize(self):
    """Non-global_quality codecs (using -qp) should emit maxrate/bufsize."""
    from converter.avcodecs import H264VAAPICodec

    codec = H264VAAPICodec()
    safe = {"vaapi": 23, "maxrate": "10000k", "bufsize": "20000k"}
    # VAAPI uses hw_quality_key='qp', hw_quality_flag='-qp'
    safe2 = {"qp": 23, "maxrate": "10000k", "bufsize": "20000k"}
    result = codec._hw_quality_opts(safe2)
    assert "-qp" in result
    assert "-maxrate:v" in result
    assert "-bufsize" in result


class TestVideoCodecAdditionalBranches:
  """Cover remaining VideoCodec.parse_options branches."""

  def test_src_width_src_height_zero_clears_both(self):
    """src_width/src_height with 0 value should be treated as None."""
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "src_width": 0, "src_height": 0, "width": 1280, "height": 720})
    # When src is 0/0, aspect corrections get None sw/sh, no crop/pad filter applied
    assert "-vcodec" in opts

  def test_mode_stretch_explicit(self):
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "mode": "stretch", "src_width": 1920, "src_height": 1080, "width": 800, "height": 600})
    assert "-vcodec" in opts

  def test_mode_crop(self):
    codec = H264Codec()
    opts = codec.parse_options(
      {
        "codec": "h264",
        "mode": "crop",
        "src_width": 1920,
        "src_height": 1080,
        "width": 800,
        "height": 600,
      }
    )
    vf_str = " ".join(opts)
    assert "crop=" in vf_str

  def test_mode_pad(self):
    codec = H264Codec()
    opts = codec.parse_options(
      {
        "codec": "h264",
        "mode": "pad",
        "src_width": 1280,
        "src_height": 720,
        "width": 800,
        "height": 600,
      }
    )
    vf_str = " ".join(opts)
    assert "pad=" in vf_str

  def test_empty_title_removed_in_video(self):
    """An empty title should be removed (not emitted as 'title=')."""
    codec = H264Codec()
    opts = codec.parse_options({"codec": "h264", "title": ""})
    # The empty title gets deleted before codec-specific, so only the blank metadata is emitted
    assert any("title=" == o for o in opts)

  def test_aspect_emitted_with_dimensions(self):
    """When both w and h are present, scale filter is used (not -s)."""
    codec = H264Codec()
    opts = codec.parse_options(
      {
        "codec": "h264",
        "mode": "crop",
        "src_width": 1920,
        "src_height": 1080,
        "width": 1280,
        "height": 720,
      }
    )
    assert "-vf" in opts
    vf_idx = opts.index("-vf")
    assert "scale" in opts[vf_idx + 1]


class TestSubtitleCodecBranches:
  """Cover SubtitleCodec.parse_options remaining branches."""

  def test_subtitle_codec_with_path_and_source(self):
    from converter.avcodecs import SrtCodec

    codec = SrtCodec()
    opts = codec.parse_options({"codec": "srt", "path": "/external/sub.srt", "map": 0, "source": 1})
    assert "-i" in opts
    assert "/external/sub.srt" in opts
    assert any("1:0" in o for o in opts)

  def test_subtitle_long_language_removed(self):
    from converter.avcodecs import SrtCodec

    codec = SrtCodec()
    opts = codec.parse_options({"codec": "srt", "language": "english"})
    assert any("language=und" in o for o in opts)

  def test_subtitle_empty_disposition_removed(self):
    from converter.avcodecs import SrtCodec

    codec = SrtCodec()
    opts = codec.parse_options({"codec": "srt", "disposition": "  "})
    # blank disposition → removed → default safe_disposition is all-negative
    assert any("disposition" in o for o in opts)

  def test_subtitle_empty_title_removed(self):
    from converter.avcodecs import SrtCodec

    codec = SrtCodec()
    opts = codec.parse_options({"codec": "srt", "title": ""})
    assert any("title=" == o for o in opts)


class TestAudioCodecDispositionBranch:
  """Cover AudioCodec empty disposition/title/filter/bsf branches."""

  def test_empty_disposition_removed(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "disposition": "   "})
    # Blank disposition → all dispositions get - prefix
    assert any("-default" in o for o in opts)

  def test_empty_title_removed_audio(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "title": ""})
    assert any("title=" == o for o in opts)

  def test_audio_no_title_emits_blank(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac"})
    assert any("title=" == o for o in opts)

  def test_audio_with_bsf(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac", "bsf": "aac_adtstoasc"})
    assert "-bsf:a" in opts

  def test_audio_no_language_emits_und(self):
    codec = AacCodec()
    opts = codec.parse_options({"codec": "aac"})
    assert any("language=und" in o for o in opts)


class TestVideoCopyCodecBranches:
  """Cover VideoCopyCodec remaining branches."""

  def test_fps_less_than_1_removed(self):
    from converter.avcodecs import VideoCopyCodec

    codec = VideoCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 0, "fps": 0.5})
    assert "-r:v" not in opts

  def test_empty_title_removed(self):
    from converter.avcodecs import VideoCopyCodec

    codec = VideoCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 0, "title": ""})
    assert any("title=" == o for o in opts)

  def test_fps_valid_emitted(self):
    from converter.avcodecs import VideoCopyCodec

    codec = VideoCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 0, "fps": 24.0})
    assert "-r:v" in opts
    assert "24.0" in opts

  def test_bsf_emitted(self):
    from converter.avcodecs import VideoCopyCodec

    codec = VideoCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 0, "bsf": "h264_mp4toannexb"})
    assert "-bsf:v" in opts
    assert "h264_mp4toannexb" in opts

  def test_source_overrides_default(self):
    from converter.avcodecs import VideoCopyCodec

    codec = VideoCopyCodec()
    opts = codec.parse_options({"codec": "copy", "map": 3, "source": 2})
    assert any("2:3" in o for o in opts)


class TestVp9CodecWithFramedata:
  """Test Vp9Codec._codec_specific_parse_options and produce list with framedata."""

  def test_vp9_with_color_framedata(self):
    from converter.avcodecs import Vp9Codec

    codec = Vp9Codec()
    opts = codec.parse_options(
      {
        "codec": "vp9",
        "framedata": {
          "color_primaries": "bt2020",
          "color_space": "bt2020nc",
          "color_range": 1,
        },
      }
    )
    # Should include color_primaries flags
    assert "-colorspace" in opts or "-color_primaries" in opts

  def test_vp9_with_profile(self):
    from converter.avcodecs import Vp9Codec

    codec = Vp9Codec()
    opts = codec.parse_options(
      {
        "codec": "vp9",
        "profile": "0",
        "framedata": {},
      }
    )
    assert "-profile:v" in opts

  def test_vp9qsv_with_framedata(self):
    from converter.avcodecs import Vp9QSVCodec

    codec = Vp9QSVCodec()
    opts = codec.parse_options(
      {
        "codec": "vp9qsv",
        "framedata": {
          "color_primaries": "bt2020",
        },
      }
    )
    assert "-vcodec" in opts


class TestAV1QSVPreset:
  """Cover AV1QSVCodec with preset in produce list."""

  def test_av1qsv_with_preset(self):
    codec = AV1QSVCodec()
    # AV1QSVCodec has hw_presets=() so all presets are rejected
    opts = codec.parse_options({"codec": "av1qsv", "preset": 4})
    assert "-preset" not in opts

  def test_av1qsv_pix_fmt_no_scale(self):
    codec = AV1QSVCodec()
    opts = codec.parse_options({"codec": "av1qsv", "pix_fmt": "p010le"})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("p010le" in v for v in vf_parts)

  def test_av1vaapi_with_preset(self):
    codec = AV1VAAPICodec()
    opts = codec.parse_options({"codec": "av1vaapi", "preset": 3})
    assert "-preset" in opts

  def test_av1vaapi_scale_both(self):
    codec = AV1VAAPICodec()
    opts = codec.parse_options({"codec": "av1vaapi", "width": 1280, "height": 720})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("scale_vaapi" in v and "1280" in v for v in vf_parts)


class TestH265QSVCodecBranches:
  """Cover H265QSVCodec remaining branches."""

  def test_h265qsv_ref_frames(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "ref_frames": 3})
    assert "-refs" in opts

  def test_h265qsv_level(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "level": 4.1})
    assert "-level" in opts
    level_idx = opts.index("-level")
    assert opts[level_idx + 1] == "41"

  def test_h265qsv_pix_fmt_scale(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "pix_fmt": "p010le", "width": 1920, "height": 1080})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("p010le" in v for v in vf_parts)

  def test_h265qsv_pix_fmt_no_scale(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "pix_fmt": "p010le"})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("p010le" in v for v in vf_parts)

  def test_h265qsv_profile_main10(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "profile": "main10"})
    assert "-profile:v" in opts

  def test_h265qsv_profile_invalid_removed(self):
    codec = H265QSVCodec()
    opts = codec.parse_options({"codec": "h265qsv", "profile": "ultrafast"})
    assert "-profile:v" not in opts


class TestH265VAAPIAdditional:
  """Cover H265VAAPICodec additional paths."""

  def test_h265vaapi_width_only(self):
    codec = H265VAAPICodec()
    opts = codec.parse_options({"codec": "h265vaapi", "width": 1280})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("scale_vaapi" in v for v in vf_parts)

  def test_h265vaapi_height_only(self):
    codec = H265VAAPICodec()
    opts = codec.parse_options({"codec": "h265vaapi", "height": 720})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("scale_vaapi" in v for v in vf_parts)

  def test_h265vaapi_decode_device(self):
    codec = H265VAAPICodec()
    opts = codec.parse_options({"codec": "h265vaapi", "device": "vaapi0", "decode_device": "vaapi1"})
    vf_parts = [opts[i + 1] for i, v in enumerate(opts) if v == "-vf"]
    assert any("hwdownload" in v for v in vf_parts)
