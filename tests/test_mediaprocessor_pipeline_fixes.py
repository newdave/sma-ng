"""Regression tests for conversion-pipeline fixes in MediaProcessor.

Covers: full-software fallback for every hardware encoder family (no hw
filters/preopts survive), hybrid-aspect dims override (stretch scale),
collision-safe output naming, per-job language/metadata isolation, the
encoding_tool already-processed check, None-safe duplicate sorting,
codec-aware SEI bitstream filtering, mp4 subtitle container filtering,
QTFS IO-error recovery, Dolby Vision re-encode warning, explicit vpp_qsv
scale folding, and Emby/Jellyfin deleted-path notification.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converter import FFMpegConvertError
from converter.avcodecs import video_codec_list
from resources.config_schema import FallbackPolicy
from resources.mediaprocessor import MediaProcessor, _rewrite_options_for_full_sw, _swap_hw_codec_to_sw

HW_TOKENS = ("qsv", "vaapi", "cuda", "npp", "nvenc", "hwupload", "hwmap", "hwdownload", "videotoolbox")
HW_PREOPT_FLAGS = ("-hwaccel", "-hwaccel_output_format", "-init_hw_device", "-filter_hw_device", "-qsv_device", "-vaapi_device", "-hwaccel_device")


def _video_argv(video):
  """Render the video stream options to an ffmpeg argv slice via the codec class."""
  codec = video["codec"]
  cls = next(c for c in video_codec_list if c.codec_name == codec)
  return cls().parse_options(dict(video))


def _ladder_mp(policy=FallbackPolicy.AGGRESSIVE):
  mp = MediaProcessor.__new__(MediaProcessor)
  mp.settings = MagicMock()
  mp.settings.fallback_policy = policy
  mp.log = MagicMock()
  mp.removeFile = MagicMock()
  return mp


def _run_to_full_sw(preopts, options):
  """Drive the ladder failing every tier except the software-encoder one."""
  mp = _ladder_mp()
  attempts = []

  def run_fn(pre, opts=None):
    opts = opts if opts is not None else options
    attempts.append((list(pre), opts))
    codec = opts["video"]["codec"]
    if codec in ("h264", "h265", "svtav1"):
      return None
    raise FFMpegConvertError("fail", "ffmpeg", "boom")

  mp._attempt_ladder(preopts, options, None, run_fn)
  return mp, attempts


def _assert_no_hw(preopts, argv):
  for flag in HW_PREOPT_FLAGS:
    assert flag not in preopts
  joined = " ".join(str(x) for x in argv)
  for token in HW_TOKENS:
    assert token not in joined, (token, joined)


class TestFullSoftwareTierAllHwFamilies:
  def test_qsv_full_sw_strips_vpp_qsv_and_device(self):
    preopts = ["-qsv_device", "/dev/dri/renderD128", "-hwaccel", "qsv", "-hwaccel_output_format", "qsv", "-vcodec", "hevc_qsv"]
    options = {
      "video": {
        "codec": "h265qsv",
        "filter": "vpp_qsv=w=1280:h=720:format=p010le",
        "pix_fmt": "p010le",
        "params": "-low_power 1 -look_ahead_depth 40",
        "preset": "veryslow",
        "global_quality": 22,
      }
    }
    mp, attempts = _run_to_full_sw(preopts, options)
    final_pre, final_opts = attempts[-1]
    assert final_opts["video"]["codec"] == "h265"
    argv = _video_argv(final_opts["video"])
    _assert_no_hw(final_pre, argv)
    vf = argv[argv.index("-vf") + 1]
    assert "scale=1280:720" in vf
    assert "format=yuv420p10le" in vf
    assert "-x265-params" not in argv  # hw flag-style params dropped
    assert argv[argv.index("-preset") + 1] == "veryslow"
    warnings = " ".join(c.args[0] for c in mp.log.warning.call_args_list if c.args)
    assert "full software pipeline" in warnings

  def test_vaapi_full_sw_without_hw_decoder_skips_straight_to_software(self):
    preopts = ["-init_hw_device", "vaapi=sma:/dev/dri/renderD128"]
    options = {"video": {"codec": "hevc_vaapi", "device": "sma", "filter": "scale_vaapi=w=1280:h=720:format=p010", "pix_fmt": "yuv420p10le"}}
    _mp, attempts = _run_to_full_sw(preopts, options)
    # hw, then full_sw (no -vcodec to strip so sw_decode is skipped; not QSV so no hw_alt).
    assert len(attempts) == 2
    final_pre, final_opts = attempts[-1]
    assert final_opts["video"]["codec"] == "h265"
    assert "device" not in final_opts["video"]
    argv = _video_argv(final_opts["video"])
    _assert_no_hw(final_pre, argv)
    assert "scale=1280:720" in argv[argv.index("-vf") + 1]

  def test_nvenc_full_sw_rewrites_positional_scale_cuda(self):
    preopts = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-vcodec", "hevc_cuvid"]
    options = {"video": {"codec": "hevc_nvenc", "filter": "scale_cuda=1280:720,hwdownload,format=nv12", "preset": "p7", "tune": "hq"}}
    _mp, attempts = _run_to_full_sw(preopts, options)
    final_pre, final_opts = attempts[-1]
    assert final_opts["video"]["codec"] == "h265"
    argv = _video_argv(final_opts["video"])
    _assert_no_hw(final_pre, argv)
    vf = argv[argv.index("-vf") + 1]
    assert vf.startswith("scale=1280:720")
    assert "format=yuv420p" in vf
    assert "-preset" not in argv
    assert "-tune" not in argv

  @pytest.mark.parametrize(
    ("hw", "sw"),
    [("h264_nvenc", "h264"), ("h264vaapi", "h264"), ("av1_vaapi", "svtav1"), ("av1nvenc", "svtav1"), ("hevc_videotoolbox", "h265"), ("h264qsv", "h264")],
  )
  def test_swap_maps_every_family(self, hw, sw):
    opts = {"video": {"codec": hw, "device": "sma", "global_quality": 20}}
    assert _swap_hw_codec_to_sw(opts) == hw
    assert opts["video"]["codec"] == sw
    assert "device" not in opts["video"]
    assert "global_quality" not in opts["video"]

  def test_operator_preopts_kept_when_no_hw_flags(self):
    """No hw preopts to strip must not wipe operator preopts."""
    options = {"video": {"codec": "h264_nvenc"}}
    _mp, attempts = _run_to_full_sw(["-probesize", "50M"], options)
    assert attempts[-1][0] == ["-probesize", "50M"]

  def test_sw_decode_only_policy_never_swaps_without_hw_decoder(self):
    mp = _ladder_mp(FallbackPolicy.SW_DECODE_ONLY)
    options = {"video": {"codec": "hevc_vaapi"}}

    def run_fn(_pre, _opts=None):
      raise FFMpegConvertError("fail", "ffmpeg", "boom")

    with pytest.raises(FFMpegConvertError):
      mp._attempt_ladder(["-init_hw_device", "vaapi=sma:/dev/dri/renderD128"], options, None, run_fn)
    assert options["video"]["codec"] == "hevc_vaapi"

  def test_rewrite_keeps_x26x_style_params_and_sw_filters(self):
    opts = {"video": {"codec": "h265", "filter": "yadif,hwupload,tonemap_vaapi=format=nv12", "params": "aq-mode=3:psy-rd=1"}}
    notes = _rewrite_options_for_full_sw(opts)
    assert opts["video"]["filter"] == "yadif"
    assert opts["video"]["params"] == "aq-mode=3:psy-rd=1"
    assert any("tonemap_vaapi" in n for n in notes)


# ---------------------------------------------------------------------------
# generateOptions-backed tests
# ---------------------------------------------------------------------------


def _gen_mp(tmp_yaml, **settings_overrides):
  with patch("resources.readsettings.ReadSettings._validate_binaries"):
    from resources.readsettings import ReadSettings

    settings = ReadSettings(tmp_yaml())
  settings.vcodec = ["h264"]
  for key, val in settings_overrides.items():
    setattr(settings, key, val)
  conv = MagicMock()
  conv.ffmpeg.codecs = {"h264": {"encoders": ["libx264"]}, "hevc": {"encoders": ["libx265"]}, "aac": {"encoders": ["aac"]}}
  conv.ffmpeg.pix_fmts = {"yuv420p": 8}
  mp = MediaProcessor.__new__(MediaProcessor)
  mp.settings = settings
  mp.converter = conv
  mp.log = MagicMock()
  mp.deletesubs = set()
  from resources.subtitles import SubtitleProcessor

  mp.subtitles = SubtitleProcessor(mp)
  return mp


def _generate(mp, info, path="/fake/input.mkv"):
  with patch("resources.mediaprocessor.Converter.encoder", return_value=None), patch("resources.mediaprocessor.Converter.codec_name_to_ffprobe_codec_name", side_effect=lambda c: c):
    options, *_ = mp.generateOptions(path, info=info)
  return options


class TestDimsOverrideStretch:
  def test_hybrid_override_scales_exactly_without_pad(self, tmp_yaml, make_media_info):
    mp = _gen_mp(tmp_yaml, video_dims_override=(1920, 1080))
    info = make_media_info(video_codec="h264", video_width=1920, video_height=1088)
    options = _generate(mp, info)
    video = options["video"]
    assert video["mode"] == "stretch"
    argv = _video_argv({**video, "src_width": 1920, "src_height": 1088})
    vf = argv[argv.index("-vf") + 1]
    assert "scale=1920:1080" in vf
    assert "pad=" not in vf


class TestBitstreamSeiFilter:
  def test_hevc_copy_uses_hevc_sei_types(self, tmp_yaml, make_media_info):
    mp = _gen_mp(tmp_yaml, removebvs=True)
    mp.settings.vcodec = ["hevc"]
    mp.hasBitstreamVideoSubs = MagicMock(return_value=True)
    info = make_media_info(video_codec="hevc")
    options = _generate(mp, info)
    assert options["video"]["codec"] == "copy"
    assert options["video"]["bsf"] == "filter_units=remove_types=39|40"

  def test_h264_uses_sei_type_6(self, tmp_yaml, make_media_info):
    mp = _gen_mp(tmp_yaml, removebvs=True)
    mp.hasBitstreamVideoSubs = MagicMock(return_value=True)
    info = make_media_info(video_codec="h264")
    options = _generate(mp, info)
    assert options["video"]["bsf"] == "filter_units=remove_types=6"


class TestDolbyVisionWarning:
  def test_reencoded_dv_source_warns(self, tmp_yaml, make_media_info):
    mp = _gen_mp(tmp_yaml, force_reencode=True)
    info = make_media_info(video_codec="h264")
    info.video.framedata = {"side_data_list": [{"side_data_type": "Dolby Vision Metadata"}]}
    _generate(mp, info)
    warnings = [c.args[0] for c in mp.log.warning.call_args_list if c.args]
    assert any("[dolby-vision]" in w for w in warnings)


class TestPerJobIsolation:
  def test_original_language_does_not_leak_between_files(self, tmp_yaml, make_media_info):
    mp = _gen_mp(tmp_yaml, audio_original_language=True, subtitle_original_language=True)
    mp.settings.awl = ["eng"]
    mp.settings.swl = ["eng"]
    mp.settings.adl = None
    mp.settings.sdl = None
    info = make_media_info()

    awl, swl = mp.safeLanguage(info, MagicMock(original_language="jpn"))
    assert "jpn" in awl
    assert mp.settings.awl == ["eng"]
    assert mp.settings.swl == ["eng"]
    assert mp.settings.adl == "jpn"  # job-scoped default while this file runs

    awl, swl = mp.safeLanguage(make_media_info(), MagicMock(original_language="fra"))
    assert "jpn" not in awl and "fra" in awl
    assert mp.settings.adl == "fra"

    mp.safeLanguage(make_media_info(), None)
    assert mp.settings.adl is None
    assert mp.settings.sdl is None

  def test_external_metadata_does_not_flip_strip_metadata_setting(self, tmp_yaml, make_media_info):
    mp = _gen_mp(tmp_yaml, strip_metadata=True)
    mp.scanForExternalMetadata = MagicMock(return_value="/fake/input.txt")
    options = _generate(mp, make_media_info())
    assert options["_keep_metadata"] is True
    assert mp.settings.strip_metadata is True
    mp.scanForExternalMetadata = MagicMock(return_value=None)
    assert "_keep_metadata" not in _generate(mp, make_media_info())


class TestExplicitQsvScaleFold:
  def test_width_folds_into_explicit_vpp_qsv(self, make_media_info):
    mp = MediaProcessor.__new__(MediaProcessor)
    mp.log = MagicMock()
    info = make_media_info(video_width=3840, video_height=2160)
    video = {"codec": "h265qsv", "filter": "vpp_qsv=format=p010le", "width": 1920, "pix_fmt": "p010le"}
    mp._fold_scale_into_explicit_qsv_filter(info, video)
    assert video["filter"] == "vpp_qsv=format=p010le:w=1920:h=1080"
    assert "width" not in video and "height" not in video
    argv = _video_argv(video)
    joined = " ".join(str(x) for x in argv)
    assert joined.count("vpp_qsv") + joined.count("scale_qsv") == 1

  def test_no_qsv_segment_leaves_options(self, make_media_info):
    mp = MediaProcessor.__new__(MediaProcessor)
    mp.log = MagicMock()
    video = {"codec": "h265qsv", "filter": "yadif", "width": 1920}
    mp._fold_scale_into_explicit_qsv_filter(make_media_info(), video)
    assert video == {"codec": "h265qsv", "filter": "yadif", "width": 1920}


# ---------------------------------------------------------------------------
# Output naming / overwrite safety
# ---------------------------------------------------------------------------


def _convert_mp(tmp_path, temp_extension=None, delete=True):
  mp = MediaProcessor.__new__(MediaProcessor)
  mp.settings = MagicMock()
  mp.settings.output_extension = "mp4"
  mp.settings.output_dir = None
  mp.settings.temp_extension = temp_extension
  mp.settings.delete = delete
  mp.settings.strip_metadata = False
  mp.settings.burn_subtitles = False
  mp.settings.fallback_policy = FallbackPolicy.AGGRESSIVE
  mp.log = MagicMock()
  mp.converter = MagicMock()
  mp.setPermissions = MagicMock()
  return mp


def _fake_convert(written):
  def fake(outputfile, opts, timeout=None, preopts=None, postopts=None, strip_metadata=False):
    written.append((outputfile, strip_metadata))
    with open(outputfile, "wb") as fh:
      fh.write(b"new")
    yield None, ["ffmpeg"]
    yield 100, ""

  return fake


class TestOutputCollision:
  def test_temp_extension_never_replaces_existing_final_file(self, tmp_path):
    mp = _convert_mp(tmp_path, temp_extension="part")
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"src")
    existing = tmp_path / "movie.mp4"
    existing.write_bytes(b"unrelated")
    written = []
    mp.converter.convert = _fake_convert(written)
    result, _ = mp.convert({"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": []}, [], [], False, None)
    assert result == str(tmp_path / "movie.2.mp4")
    assert written[0][0] == str(tmp_path / "movie.2.part")
    assert existing.read_bytes() == b"unrelated"

  def test_delete_true_numbers_instead_of_removing_existing(self, tmp_path):
    mp = _convert_mp(tmp_path, delete=True)
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"src")
    existing = tmp_path / "movie.mp4"
    existing.write_bytes(b"unrelated")
    mp.converter.convert = _fake_convert([])
    result, _ = mp.convert({"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": []}, [], [], False, None)
    assert result == str(tmp_path / "movie.2.mp4")
    assert existing.read_bytes() == b"unrelated"

  def test_keep_metadata_option_overrides_strip_per_job(self, tmp_path):
    mp = _convert_mp(tmp_path)
    mp.settings.strip_metadata = True
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"src")
    written = []
    mp.converter.convert = _fake_convert(written)
    mp.convert({"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": [], "_keep_metadata": True}, [], [], False, None)
    assert written[0][1] is False

  def test_unexpected_error_removes_partial_output(self, tmp_path):
    mp = _convert_mp(tmp_path, temp_extension="part")
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"src")
    mp.removeFile = MagicMock(side_effect=lambda p, *a, **k: os.remove(p) or True)

    def boom(outputfile, opts, timeout=None, preopts=None, postopts=None, strip_metadata=False):
      with open(outputfile, "wb") as fh:
        fh.write(b"partial")
      yield None, ["ffmpeg"]
      raise OSError("disk full")

    mp.converter.convert = boom
    result, inp = mp.convert({"source": [str(src)], "audio": [{"codec": "aac"}], "video": {}, "subtitle": []}, [], [], False, None)
    assert result is None
    assert inp == str(src)
    assert not (tmp_path / "movie.part").exists()


class TestRestoreFromOutputCollision:
  def _mp(self, tmp_path, delete):
    mp = MediaProcessor.__new__(MediaProcessor)
    mp.settings = MagicMock()
    mp.settings.output_dir = str(tmp_path / "out")
    mp.settings.moveto = None
    mp.settings.delete = delete
    mp.settings.output_extension = "mp4"
    mp.log = MagicMock()
    mp.removeFile = MagicMock(side_effect=lambda p, *a, **k: os.remove(p) or True)
    (tmp_path / "out").mkdir()
    (tmp_path / "in").mkdir()
    return mp

  def test_delete_false_keeps_same_named_original(self, tmp_path):
    mp = self._mp(tmp_path, delete=False)
    src = tmp_path / "in" / "movie.mp4"
    src.write_bytes(b"original")
    out = tmp_path / "out" / "movie.mp4"
    out.write_bytes(b"converted")
    result = mp.restoreFromOutput(str(src), str(out))
    assert result == str(tmp_path / "in" / "movie.2.mp4")
    assert src.read_bytes() == b"original"
    assert (tmp_path / "in" / "movie.2.mp4").read_bytes() == b"converted"

  def test_delete_true_replaces_input_by_design(self, tmp_path):
    mp = self._mp(tmp_path, delete=True)
    src = tmp_path / "in" / "movie.mp4"
    src.write_bytes(b"original")
    out = tmp_path / "out" / "movie.mp4"
    out.write_bytes(b"converted")
    result = mp.restoreFromOutput(str(src), str(out))
    assert result == str(src)
    assert src.read_bytes() == b"converted"

  def test_unrelated_existing_file_is_never_replaced(self, tmp_path):
    mp = self._mp(tmp_path, delete=True)
    src = tmp_path / "in" / "movie.mkv"
    src.write_bytes(b"source")
    other = tmp_path / "in" / "movie.mp4"
    other.write_bytes(b"unrelated")
    out = tmp_path / "out" / "movie.mp4"
    out.write_bytes(b"converted")
    result = mp.restoreFromOutput(str(src), str(out))
    assert result == str(tmp_path / "in" / "movie.2.mp4")
    assert other.read_bytes() == b"unrelated"

  def test_identical_bypass_copy_is_not_duplicated(self, tmp_path):
    mp = self._mp(tmp_path, delete=False)
    src = tmp_path / "in" / "movie.mp4"
    src.write_bytes(b"same")
    out = tmp_path / "out" / "movie.mp4"
    out.write_bytes(b"same")
    result = mp.restoreFromOutput(str(src), str(out))
    assert result == str(src)
    assert not out.exists()
    assert not (tmp_path / "in" / "movie.2.mp4").exists()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


class TestAlreadyProcessedCheck:
  def _mp(self):
    mp = MediaProcessor.__new__(MediaProcessor)
    mp.settings = MagicMock()
    mp.settings.output_extension = "mp4"
    mp.settings.force_convert = False
    mp.settings.process_same_extensions = True
    mp.settings.bypass_copy_all = False
    mp.log = MagicMock()
    return mp

  @pytest.mark.parametrize("meta", [{"encoding_tool": "sma-ng", "encoder": "lavf61.7.100"}, {"encoder": "sma-ng"}, {"encoding_tool": "SMA-NG"}])
  def test_detects_stamp(self, meta):
    info = MagicMock()
    info.format.metadata = meta
    assert self._mp().canBypassConvert("/x/movie.mp4", info) is True

  def test_lavf_only_is_not_processed(self):
    info = MagicMock()
    info.format.metadata = {"encoder": "lavf61.7.100"}
    assert self._mp().canBypassConvert("/x/movie.mp4", info) is False


class TestDuplicateStreamSortNoneBitrate:
  def test_none_bitrate_sorts_last(self):
    mp = MediaProcessor.__new__(MediaProcessor)
    mp.getSourceStream = MagicMock(return_value=MagicMock(disposition={"default": False}))
    opts = [{"codec": "copy", "bitrate": None, "map": 1}, {"codec": "copy", "bitrate": 640, "map": 2}]
    mp.duplicateStreamSort(opts, MagicMock())
    assert [o["map"] for o in opts] == [2, 1]


class TestMp4SubtitleContainerFilter:
  def _mp(self, fmt="mp4"):
    mp = MediaProcessor.__new__(MediaProcessor)
    mp.settings = MagicMock()
    mp.settings.output_format = fmt
    mp.settings.embedsubs = True
    mp.settings.embedimgsubs = True
    mp.log = MagicMock()
    return mp

  def test_srt_pool_entry_skipped_for_mov_text(self):
    assert self._mp()._select_subtitle_codec("subrip", False, True, scodecs=["srt", "mov_text"], scodecs_image=[]) == "mov_text"

  def test_text_falls_back_to_mov_text(self):
    assert self._mp()._select_subtitle_codec("ass", False, True, scodecs=["ass", "srt"], scodecs_image=[]) == "mov_text"

  def test_pgs_copy_not_embeddable_in_mp4(self):
    assert self._mp()._select_subtitle_codec("hdmv_pgs_subtitle", True, True, scodecs=[], scodecs_image=["hdmv_pgs_subtitle"]) is None

  def test_image_falls_through_to_dvdsub(self):
    assert self._mp()._select_subtitle_codec("hdmv_pgs_subtitle", True, True, scodecs=[], scodecs_image=["pgs", "dvdsub"]) == "dvdsub"

  def test_mov_text_copy_kept(self):
    assert self._mp()._select_subtitle_codec("mov_text", False, True, scodecs=["mov_text"], scodecs_image=[]) == "copy"

  def test_mkv_output_unfiltered(self):
    assert self._mp("mkv")._select_subtitle_codec("subrip", False, True, scodecs=["srt"], scodecs_image=[]) == "srt"


class TestQtfsIoError:
  def test_io_error_keeps_file_and_cleans_partial(self, tmp_path):
    mp = MediaProcessor.__new__(MediaProcessor)
    mp.settings = MagicMock()
    mp.settings.relocate_moov = True
    mp.settings.output_format = "mp4"
    mp.log = MagicMock()
    mp.removeFile = MagicMock(return_value=True)
    f = tmp_path / "movie.mp4"
    f.write_bytes(b"data")

    def fail(src, dst):
      with open(dst, "wb") as fh:
        fh.write(b"half")
      raise OSError("No space left on device")

    with patch("qtfaststart.processor.process", side_effect=fail):
      assert mp.QTFS(str(f)) == str(f)
    assert f.read_bytes() == b"data"
    assert not (tmp_path / "movie.mp4.QTFS").exists()


class TestDefaultDispositionSentinel:
  def test_disposition_gets_valid_default_flag(self):
    mp = MediaProcessor.__new__(MediaProcessor)
    mp.settings = MagicMock()
    mp.settings.adl = "eng"
    mp.settings.audio_sorting_default = []
    mp.log = MagicMock()
    stream = {"language": "eng", "codec": "aac", "channels": 2, "disposition": "+comment"}
    mp.setDefaultAudioStream([stream])
    assert "DOESTHISEVENWORK" not in stream["disposition"]
    assert "+default" in stream["disposition"]


class TestPostDeletedPath:
  def _mp(self):
    mp = MediaProcessor.__new__(MediaProcessor)
    mp.settings = MagicMock()
    mp.settings.postprocess = False
    mp.settings.Plex = {}
    mp.settings.autoscan_instances = None
    mp.settings.emby_instances = [{"url": "x"}]
    mp.settings.jellyfin_instances = [{"url": "y"}]
    mp.log = MagicMock()
    return mp

  def test_missing_different_input_reported_deleted(self, tmp_path):
    out = tmp_path / "movie.mp4"
    out.write_bytes(b"x")
    gone = str(tmp_path / "movie.mkv")
    with patch("resources.mediaprocessor.emby_refresh.refreshEmby") as emby, patch("resources.mediaprocessor.jellyfin_refresh.refreshJellyfin") as jf:
      self._mp().post([str(out)], "movie", input_path=gone)
    assert emby.call_args.kwargs["deleted_path"] == gone
    assert jf.call_args.kwargs["deleted_path"] == gone

  def test_existing_or_same_input_not_reported(self, tmp_path):
    out = tmp_path / "movie.mp4"
    out.write_bytes(b"x")
    kept = tmp_path / "movie.mkv"
    kept.write_bytes(b"x")
    with patch("resources.mediaprocessor.emby_refresh.refreshEmby") as emby, patch("resources.mediaprocessor.jellyfin_refresh.refreshJellyfin") as jf:
      self._mp().post([str(out)], "movie", input_path=str(kept))
      self._mp().post([str(out)], "movie", input_path=str(out))
    assert all(c.kwargs["deleted_path"] is None for c in emby.call_args_list)
    assert all(c.kwargs["deleted_path"] is None for c in jf.call_args_list)
