"""Tests for resources/media_repair.py — stream-copy-only media repair."""

import os
from types import SimpleNamespace
from unittest import mock

import pytest

from converter.ffmpeg import MediaInfo
from resources import media_repair as mr
from resources.media_repair import RepairError, RepairPlan, RepairProcessor, build_opts, diagnose, iter_targets, summarize, verify


def _video(index, codec="h264", width=1920, height=1080, sar="1:1", attached_pic=0, tag="avc1"):
  return {
    "index": index,
    "codec_type": "video",
    "codec_name": codec,
    "codec_tag_string": tag,
    "width": width,
    "height": height,
    "sample_aspect_ratio": sar if sar is not None else "N/A",
    "DISPOSITION:attached_pic": attached_pic,
  }


def _audio(index, codec="aac", channels=2):
  return {"index": index, "codec_type": "audio", "codec_name": codec, "channels": channels}


def _sub(index, codec="mov_text"):
  return {"index": index, "codec_type": "subtitle", "codec_name": codec}


def _data(index, codec="bin_data", tag="tmcd"):
  return {"index": index, "codec_type": "data", "codec_name": codec, "codec_tag_string": tag}


def _attachment(index, mimetype=None, filename=None, codec="ttf"):
  s = {"index": index, "codec_type": "attachment", "codec_name": codec}
  if mimetype:
    s["TAG:mimetype"] = mimetype
  if filename:
    s["TAG:filename"] = filename
  return s


def _info(streams, path="/m/a.mp4", duration="100.0"):
  """MediaInfo parsed from ffprobe text, as FFMpeg.probe(posters_as_video=False) builds it."""
  raw = []
  for s in streams:
    raw.append("[STREAM]")
    raw.extend("%s=%s" % kv for kv in s.items())
    raw.append("[/STREAM]")
  raw.extend(["[FORMAT]", "duration=%s" % duration, "[/FORMAT]"])
  info = MediaInfo(posters_as_video=False)
  info.parse_ffprobe("\n".join(raw))
  info.path = path
  return info


CHAPTER_TRACK = _data(2, "bin_data", "text")


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------


class TestDiagnose:
  def test_normal_file_needs_no_repair(self):
    plan = diagnose(_info([_video(0), _audio(1), _sub(2)]))
    assert not plan.needs_repair
    assert plan.keep == [0, 1, 2]
    assert plan.describe() == "normal"

  def test_chapter_track_in_mp4_is_normal(self):
    plan = diagnose(_info([_video(0), _audio(1), CHAPTER_TRACK]))
    assert not plan.needs_repair
    # Never mapped: the muxer rewrites it from the chapter list.
    assert plan.keep == [0, 1]

  def test_text_data_track_in_mkv_is_dropped(self):
    assert diagnose(_info([_video(0), _audio(1), CHAPTER_TRACK], path="/m/a.mkv")).drop_data == [2]

  def test_tmcd_and_bin_data_dropped(self):
    plan = diagnose(_info([_video(0), _audio(1), _data(2, "bin_data", "gpmd"), _data(3, "bin_data", "tmcd")]))
    assert plan.drop_data == [2, 3]
    assert plan.describe() == "data streams 2,3"

  def test_unknown_streams_dropped(self):
    streams = [_video(0), _audio(1), _data(2, "unknown", "tmcd"), {"index": 3, "codec_type": "unknown", "codec_name": "x"}, _audio(4, "none"), _sub(5, "unknown")]
    plan = diagnose(_info(streams))
    assert plan.drop_unknown == [2, 3, 4, 5]
    assert plan.keep == [0, 1]
    assert plan.describe() == "unknown streams 2,3,4,5"

  def test_attached_picture_is_dropped(self):
    plan = diagnose(_info([_video(0), _audio(1), _video(2, "mjpeg", 600, 900, attached_pic=1)]))
    assert plan.drop_artwork == [2]
    assert plan.keep == [0, 1]

  def test_demoted_poster_is_dropped(self):
    # Fallout S01E01 layout: the old repair remux turned the poster into a
    # real mp4v video track with no attached_pic flag.
    streams = [_video(0, "hevc", 3840, 2176, "136:135", tag="hvc1"), _audio(1, "eac3", 6), _sub(2), _video(3, "mjpeg", 1000, 1500, "8:3", tag="mp4v")]
    plan = diagnose(_info(streams))
    assert plan.drop_artwork == [3]
    assert plan.dar is None  # aspect already corrected
    assert plan.keep == [0, 1, 2]

  def test_leading_poster_does_not_become_primary(self):
    plan = diagnose(_info([_video(0, "png", 600, 900), _video(1, "h264", 1920, 1088), _audio(2)]))
    assert plan.drop_artwork == [0]
    assert plan.dar == "1920:1080"

  def test_image_only_video_is_kept_as_primary(self):
    # A genuine MJPEG movie has no other video stream; never strip its picture.
    assert not diagnose(_info([_video(0, "mjpeg", 640, 480), _audio(1)], path="/m/a.mov")).needs_repair

  def test_non_image_secondary_video_is_kept(self):
    plan = diagnose(_info([_video(0), _video(1, "h264", 1280, 720), _audio(2)], path="/m/a.mkv"))
    assert not plan.needs_repair
    assert plan.keep == [0, 1, 2]

  def test_image_attachment_dropped_font_kept(self):
    streams = [_video(0), _audio(1), _attachment(2, "font/ttf", "f.ttf"), _attachment(3, "image/jpeg", "cover.jpg", "mjpeg"), _attachment(4, None, "poster.PNG", "png")]
    plan = diagnose(_info(streams, path="/m/a.mkv"))
    assert plan.drop_artwork == [3, 4]
    assert plan.keep == [0, 1, 2]

  @pytest.mark.parametrize("sar", [None, "1:1", "0:1"])
  def test_stretched_dims_get_corrective_dar(self, sar):
    plan = diagnose(_info([_video(0, "hevc", 3840, 2176, sar), _audio(1)]))
    assert plan.dar == "3840:2160"
    assert plan.describe() == "display aspect 3840:2160"

  def test_corrected_or_standard_dims_left_alone(self):
    assert diagnose(_info([_video(0, "hevc", 3840, 2176, "136:135")])).dar is None
    assert diagnose(_info([_video(0, "hevc", 3840, 2160)])).dar is None

  def test_no_video_stream(self):
    assert not diagnose(_info([_audio(0)])).needs_repair


class TestMuxerNeeds:
  @pytest.mark.parametrize(
    "codec,width,height,expected",
    [
      ("h264", 1920, 1088, "h264_metadata=sample_aspect_ratio=136/135"),
      ("hevc", 3840, 2176, "hevc_metadata=sample_aspect_ratio=136/135"),
      ("hevc", 1920, 1072, "hevc_metadata=sample_aspect_ratio=134/135"),
      ("mpeg2video", 1920, 1088, "mpeg2_metadata=display_aspect_ratio=16/9"),
      ("av1", 1920, 1088, None),
    ],
  )
  def test_aspect_bsf_per_codec(self, codec, width, height, expected):
    plan = diagnose(_info([_video(0, codec, width, height), _audio(1)]))
    assert plan.dar
    assert plan.aspect_bsf == expected

  def test_aspect_targets_primary_video_output_number(self):
    # A second real video stream ahead of the primary in output order is not
    # possible with index maps; the primary is the first kept video here.
    plan = diagnose(_info([_video(0, "mjpeg", 600, 900, attached_pic=1), _video(1, "h264", 1920, 1088), _audio(2)]))
    assert plan.keep == [1, 2] and plan.video_out == 0

  def test_no_aspect_fix_means_no_bsf(self):
    plan = diagnose(_info([_video(0), _audio(1)]))
    assert plan.aspect_bsf is None and plan.video_out == 0

  @pytest.mark.parametrize(
    "path,streams,expected",
    [
      ("/m/a.mp4", [_video(0), _audio(1, "truehd", 8)], "experimental"),
      ("/m/a.mkv", [_video(0), _audio(1, "truehd", 8)], None),
      ("/m/a.mp4", [{**_video(0, "hevc"), "dv_profile": 5}, _audio(1)], "unofficial"),
      ("/m/a.mkv", [{**_video(0, "hevc"), "dv_profile": 8}, _audio(1)], "unofficial"),
      ("/m/a.mp4", [{**_video(0, "hevc"), "dv_profile": 8}, _audio(1, "truehd", 8)], "experimental"),
      ("/m/a.mp4", [_video(0), _audio(1, "eac3", 6)], None),
    ],
  )
  def test_strict_level(self, path, streams, expected):
    assert diagnose(_info(streams, path=path)).strict == expected

  def test_dropped_truehd_needs_no_strict(self):
    plan = diagnose(_info([_video(0), _audio(1), {**_audio(2, "truehd"), "codec_type": "audio", "codec_name": "none"}]))
    assert plan.strict is None


# ---------------------------------------------------------------------------
# build_opts
# ---------------------------------------------------------------------------


class TestBuildOpts:
  def test_mp4_opts_are_stream_copy_with_scoped_aspect_and_explicit_muxer(self):
    plan = RepairPlan(path="/m/a.mp4", keep=[0, 1, 3], drop_data=[4], drop_artwork=[2], dar="1920:1080")
    assert build_opts(plan, "/m/a.mp4") == [
      "-i",
      "/m/a.mp4",
      "-map",
      "0:0",
      "-map",
      "0:1",
      "-map",
      "0:3",
      "-c",
      "copy",
      "-aspect:v:0",
      "1920:1080",
      "-write_tmcd",
      "0",
      "-movflags",
      "+faststart",
      "-f",
      "mp4",
    ]

  def test_aspect_bsf_and_strict_follow_the_plan(self):
    plan = RepairPlan(path="/m/a.mp4", keep=[0, 1, 2], dar="1920:1080", video_out=1, aspect_bsf="h264_metadata=sample_aspect_ratio=136/135", strict="unofficial")
    opts = build_opts(plan, "/m/a.mp4")
    assert opts[opts.index("-aspect:v:1") + 1] == "1920:1080"
    assert opts[opts.index("-bsf:v:1") + 1] == "h264_metadata=sample_aspect_ratio=136/135"
    assert opts[opts.index("-strict") + 1] == "unofficial"

  def test_mkv_opts_skip_mp4_muxer_flags(self):
    plan = RepairPlan(path="/m/a.mkv", keep=[0, 1], drop_artwork=[2])
    assert build_opts(plan, "/m/a.mkv") == ["-i", "/m/a.mkv", "-map", "0:0", "-map", "0:1", "-c", "copy", "-f", "matroska"]


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


class TestVerify:
  SOURCE_STREAMS = [_video(0, "h264", 1920, 1088), _audio(1), CHAPTER_TRACK, _video(3, "mjpeg", 600, 900), _data(4)]
  SOURCE = _info(SOURCE_STREAMS)
  PLAN = RepairPlan(path="/m/a.mp4", keep=[0, 1], drop_data=[4], drop_artwork=[3], dar="1920:1080")

  def _output(self, streams=None, duration="100.0"):
    if streams is None:
      streams = [_video(0, "h264", 1920, 1088, "136:135"), _audio(1), CHAPTER_TRACK]
    return _info(streams, path="/m/a.sma", duration=duration)

  def test_matching_output_passes(self):
    verify(self.PLAN, self.SOURCE, self._output())

  def test_stream_mismatch_fails(self):
    with pytest.raises(RepairError, match=r"do not match the source: expected \[video/h264 1920x1088, audio/.*got \[video/hevc 1920x1088"):
      verify(self.PLAN, self.SOURCE, self._output([_video(0, "hevc", 1920, 1088, "136:135"), _audio(1), CHAPTER_TRACK]))

  @pytest.mark.parametrize("duration", ["90.0", "N/A"])
  def test_duration_drift_fails(self, duration):
    with pytest.raises(RepairError, match="duration"):
      verify(self.PLAN, self.SOURCE, self._output(duration=duration))

  def test_chapter_loss_fails(self):
    output = self._output([_video(0, "h264", 1920, 1088, "136:135"), _audio(1)])
    source = _info(self.SOURCE_STREAMS)
    source.chapters, output.chapters = 12, 0
    with pytest.raises(RepairError, match="0 chapters, source has 12"):
      verify(self.PLAN, source, output)

  def test_new_chapter_track_with_same_chapters_passes(self):
    # The source kept its chapters outside a QuickTime text track (Nero chpl
    # or a tx3g track); the muxer writes a text track for the same chapters.
    source = _info([_video(0, "h264", 1920, 1088), _audio(1), _video(3, "mjpeg", 600, 900, attached_pic=1)])
    plan = diagnose(source)
    output = self._output()
    source.chapters = output.chapters = 8
    verify(plan, source, output)

  def test_unknown_chapter_count_is_not_compared(self):
    output = self._output([_video(0, "h264", 1920, 1088, "136:135"), _audio(1)])
    output.chapters = 3
    verify(self.PLAN, _info(self.SOURCE_STREAMS), output)

  def test_dolby_vision_loss_fails(self):
    source = _info([{**_video(0, "hevc", 3840, 2160), "dv_profile": 5}, _audio(1), _data(2)])
    plan = diagnose(source)
    output = _info([_video(0, "hevc", 3840, 2160), _audio(1)], path="/m/a.sma")
    with pytest.raises(RepairError, match=r"expected \[video/hevc 3840x2160 dv5"):
      verify(plan, source, output)

  def test_long_dropped_stream_does_not_fail_duration(self):
    source = _info([{**_video(0), "duration": "100.0"}, {**_audio(1), "duration": "100.0"}, {**_data(2), "duration": "160.0"}], duration="160.0")
    plan = diagnose(source)
    verify(plan, source, _info([_video(0), _audio(1)], path="/m/a.sma", duration="100.2"))

  def test_truncated_output_fails_size_check(self):
    source = _info([{**_video(0), "bit_rate": "8000000"}, _audio(1), {**_data(2), "bit_rate": "8000"}])
    plan = diagnose(source)
    output = _info([{**_video(0), "bit_rate": "8000000"}, _audio(1)], path="/m/a.sma")
    source.format.size, output.format.size = 4e9, 2e9
    with pytest.raises(RepairError, match="the remux lost data"):
      verify(plan, source, output)

  def test_size_check_allows_dropped_streams(self):
    # 100 s of a 40 Mb/s unknown stream (500 MB) is expected to go.
    source = _info([_video(0), _audio(1), {**_video(2, "unknown"), "codec_name": "none", "bit_rate": "40000000"}])
    plan = diagnose(source)
    output = _info([_video(0), _audio(1)], path="/m/a.sma")
    source.format.size, output.format.size = 1.5e9, 1.0e9
    verify(plan, source, output)

  def test_language_change_fails(self):
    source = _info([_video(0), {**_audio(1), "TAG:language": "eng"}, _data(2)])
    plan = RepairPlan(path="/m/a.mp4", keep=[0, 1], drop_data=[2])
    output = _info([_video(0), {**_audio(1), "TAG:language": "und"}], path="/m/a.sma")
    with pytest.raises(RepairError, match=r"do not match the source: expected \[video/h264 1920x1080, audio/aac 2ch eng\], got .*2ch und\]"):
      verify(plan, source, output)

  def test_default_disposition_change_fails(self):
    source = _info([_video(0), {**_audio(1), "DISPOSITION:default": 1}, _data(2)])
    plan = RepairPlan(path="/m/a.mp4", keep=[0, 1], drop_data=[2])
    output = _info([_video(0), {**_audio(1), "DISPOSITION:default": 0}], path="/m/a.sma")
    with pytest.raises(RepairError, match=r"expected \[.*audio/aac 2ch default\], got \[.*audio/aac 2ch\]"):
      verify(plan, source, output)

  def test_matching_language_and_default_pass(self):
    audio = {**_audio(1), "TAG:language": "eng", "DISPOSITION:default": 1, "DISPOSITION:forced": 1}
    source = _info([_video(0), audio, _data(2)])
    plan = RepairPlan(path="/m/a.mp4", keep=[0, 1], drop_data=[2])
    verify(plan, source, _info([_video(0), audio], path="/m/a.sma"))

  def test_output_still_damaged_fails(self):
    # Aspect fix did not land: output still has square pixels.
    with pytest.raises(RepairError, match="still needs repair"):
      verify(self.PLAN, self.SOURCE, self._output([_video(0, "h264", 1920, 1088, "1:1"), _audio(1), CHAPTER_TRACK]))


# ---------------------------------------------------------------------------
# RepairProcessor
# ---------------------------------------------------------------------------


DAMAGED = [_video(0, "h264", 1920, 1088), _audio(1), _video(2, "mjpeg", 600, 900, tag="mp4v"), _data(3, "unknown", "tmcd")]
REPAIRED = [_video(0, "h264", 1920, 1088, "136:135"), _audio(1)]


class FakeFFMpeg:
  """Stands in for converter.ffmpeg.FFMpeg: probe returns the source or the
  repaired layout depending on the path, convert writes the output file."""

  ffprobe_path = "/usr/bin/ffprobe"

  def __init__(self, source, output=None, convert_error=None, chapters=(0, 0)):
    self.source = source
    self.output = output
    self.convert_error = convert_error
    self.chapters = chapters  # (source, output) chapter counts; an Exception fails the count
    self.calls = []

  def _get_stdout(self, cmds):
    if isinstance(self.chapters, Exception):
      raise self.chapters
    assert cmds[:4] == [self.ffprobe_path, "-v", "error", "-show_chapters"]
    count = self.chapters[1] if cmds[-1].endswith(".sma") else self.chapters[0]
    return "[CHAPTER]\nid=0\n[/CHAPTER]\n" * count

  def probe(self, path, posters_as_video=True, framedata=True):
    assert posters_as_video is False and framedata is False
    streams = self.output if path.endswith(".sma") else self.source
    if isinstance(streams, Exception):
      raise streams
    return _info(streams, path=path) if streams is not None else None

  def convert(self, outfile, opts, timeout=None):
    self.calls.append((outfile, list(opts), timeout))
    with open(outfile, "wb") as f:
      f.write(b"repaired")
    if self.convert_error:
      raise self.convert_error
    yield 0, ["ffmpeg", "-i", opts[1]]
    yield 42, "frame="


def _settings(temp_extension=None, output_dir=None, output_dir_ratio=None):
  return SimpleNamespace(
    ffmpeg="/usr/bin/ffmpeg",
    ffprobe="/usr/bin/ffprobe",
    temp_extension=temp_extension,
    output_dir=output_dir,
    output_dir_ratio=output_dir_ratio,
    permissions={"chmod": 0o640, "uid": -1, "gid": -1},
  )


def _processor(ff, settings=None, logger=None):
  with mock.patch.object(mr, "FFMpeg", return_value=ff):
    return RepairProcessor(settings or _settings(), logger=logger)


@pytest.fixture
def media(tmp_path):
  p = tmp_path / "a.mp4"
  p.write_bytes(b"original")
  return p


class TestSummarize:
  def test_format_and_every_stream(self):
    video = dict(_video(0, "hevc", 3840, 2160), r_frame_rate="24000/1001", pix_fmt="yuv420p10le", color_transfer="smpte2084", bit_rate="15000000")
    audio = dict(_audio(1, "eac3", 6), **{"TAG:language": "eng", "DISPOSITION:default": 1})
    sub = dict(_sub(2, "subrip"), **{"TAG:language": "eng", "DISPOSITION:forced": 1})
    font = _attachment(4, mimetype="font/ttf", filename="a.ttf")
    info = _info([video, audio, sub, _data(3), font], duration="2530.4")
    info.format.format, info.format.size, info.format.bitrate = "matroska,webm", 3.3e9, 10.4e6
    assert summarize(info) == (
      "matroska,webm 0:42:10 3.1 GiB 10.4 Mb/s"
      " | #0 video/hevc 3840x2160 23.976fps yuv420p10le HDR10/PQ 15000 kb/s"
      " | #1 audio/eac3 6ch eng default"
      " | #2 subtitle/subrip eng forced"
      " | #3 data/bin_data tag=tmcd"
      " | #4 attachment/ttf a.ttf font/ttf"
    )

  def test_anamorphic_hlg_and_artwork(self):
    hlg = dict(_video(0, sar="4:3"), r_frame_rate="25/1", color_transfer="arib-std-b67")
    info = _info([hlg, _video(1, "mjpeg", attached_pic=1)], duration="59.6")
    info.format.size = 512
    assert summarize(info) == "unknown 0:01:00 512 B | #0 video/h264 1920x1080 SAR 4:3 25fps HLG | #1 video/mjpeg 1920x1080 attached_pic"

  @pytest.mark.parametrize("size,expected", [(2048, "2.0 KiB"), (5 * 1024**2, "5.0 MiB"), (3 * 1024**4, "3072.0 GiB")])
  def test_size_units(self, size, expected):
    assert mr._size(size) == expected


class TestRepairProcessor:
  def test_uses_ffmpeg_paths_from_settings(self):
    with mock.patch.object(mr, "FFMpeg") as ff:
      RepairProcessor(_settings())
    ff.assert_called_once_with(ffmpeg_path="/usr/bin/ffmpeg", ffprobe_path="/usr/bin/ffprobe")

  def test_normal_file_untouched(self, media):
    ff = FakeFFMpeg([_video(0), _audio(1)])
    result = _processor(ff).repair_file(str(media))
    assert result.status == mr.STATUS_NORMAL
    assert result.probe == "unknown 0:01:40 | #0 video/h264 1920x1080 | #1 audio/aac 2ch"
    assert ff.calls == []
    assert media.read_bytes() == b"original"

  def test_dry_run_reports_without_writing(self, media):
    ff = FakeFFMpeg(DAMAGED)
    result = _processor(ff).repair_file(str(media), dry_run=True)
    assert result.status == mr.STATUS_WOULD_REPAIR
    assert result.detail == "unknown streams 3; artwork streams 2; display aspect 1920:1080"
    assert ff.calls == []

  @pytest.mark.parametrize("source,message", [(RuntimeError("broken"), "broken"), (None, "no streams")])
  def test_probe_failure_reported(self, media, source, message):
    ff = FakeFFMpeg([])
    ff.source = source
    result = _processor(ff).repair_file(str(media))
    assert result.status == mr.STATUS_FAILED
    assert message in result.detail
    assert result.probe == ""

  def test_successful_repair_uses_temp_extension_and_settings_permissions(self, media, tmp_path):
    ff = FakeFFMpeg(DAMAGED, REPAIRED)
    result = _processor(ff).repair_file(str(media))
    assert result.status == mr.STATUS_REPAIRED
    assert media.read_bytes() == b"repaired"
    assert os.stat(media).st_mode & 0o777 == 0o640
    outfile, opts, timeout = ff.calls[0]
    assert outfile == str(tmp_path / "a.sma")
    assert opts[-2:] == ["-f", "mp4"]
    assert timeout == 0
    assert os.listdir(tmp_path) == ["a.mp4"]

  def test_writes_in_transit_copy_to_output_directory(self, media, tmp_path):
    transcode = tmp_path / "transcode" / "new"
    ff = FakeFFMpeg(DAMAGED, REPAIRED)
    result = _processor(ff, _settings(output_dir=str(transcode))).repair_file(str(media))
    assert result.status == mr.STATUS_REPAIRED
    assert ff.calls[0][0] == str(transcode / "a.sma")
    assert media.read_bytes() == b"repaired"
    assert os.listdir(transcode) == []

  def test_insufficient_output_directory_space_refuses_without_fallback(self, media, tmp_path):
    ff = FakeFFMpeg(DAMAGED, REPAIRED)
    rp = _processor(ff, _settings(output_dir=str(tmp_path / "transcode"), output_dir_ratio=1.0))
    with mock.patch.object(mr.shutil, "disk_usage", return_value=SimpleNamespace(free=1)):
      result = rp.repair_file(str(media))
    assert result.status == mr.STATUS_FAILED
    assert "insufficient free space" in result.detail
    assert ff.calls == []
    assert media.read_bytes() == b"original"

  def test_space_check_error_fails_open(self, media, tmp_path):
    rp = _processor(FakeFFMpeg(DAMAGED, REPAIRED), _settings(output_dir=str(tmp_path / "t"), output_dir_ratio=1.0))
    with mock.patch.object(mr.shutil, "disk_usage", side_effect=OSError("gone")):
      assert rp.outputDirHasFreeSpace(str(media)) is True

  def test_cross_device_move_copies_via_smatmp(self, media, tmp_path):
    ff = FakeFFMpeg(DAMAGED, REPAIRED)
    rp = _processor(ff, _settings(output_dir=str(tmp_path / "transcode")))
    real_replace = os.replace

    def replace(src, dst):
      if src.endswith(".sma"):
        raise OSError(18, "Invalid cross-device link")
      assert src == str(media) + ".smatmp"
      real_replace(src, dst)

    with mock.patch.object(mr.os, "replace", side_effect=replace):
      assert rp.repair_file(str(media)).status == mr.STATUS_REPAIRED
    assert media.read_bytes() == b"repaired"
    assert os.listdir(tmp_path / "transcode") == []
    assert sorted(os.listdir(tmp_path)) == ["a.mp4", "transcode"]

  def test_cross_device_copy_failure_keeps_original(self, media, tmp_path):
    rp = _processor(FakeFFMpeg(DAMAGED, REPAIRED), _settings(output_dir=str(tmp_path / "transcode")))
    with mock.patch.object(mr.os, "replace", side_effect=OSError(18, "Invalid cross-device link")):
      result = rp.repair_file(str(media))
    assert result.status == mr.STATUS_FAILED
    assert media.read_bytes() == b"original"
    assert os.listdir(tmp_path / "transcode") == []
    assert sorted(os.listdir(tmp_path)) == ["a.mp4", "transcode"]

  def test_configured_temp_extension_and_collision_counter(self, media, tmp_path):
    (tmp_path / "a.tmpx").write_bytes(b"other job")
    rp = _processor(FakeFFMpeg(DAMAGED), _settings(temp_extension="tmpx"))
    assert rp.getTempFile(str(media)) == str(tmp_path / "a.2.tmpx")

  def test_relative_path_temp_file_goes_next_to_it(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _processor(FakeFFMpeg(DAMAGED)).getTempFile("a.mp4") == str(tmp_path / "a.sma")

  def test_logs_start_command_progress_and_verification(self, media, monkeypatch):
    monkeypatch.setattr(mr, "PROGRESS_INTERVAL", 0)
    log = mock.MagicMock()
    assert _processor(FakeFFMpeg(DAMAGED, REPAIRED), logger=log).repair_file(str(media)).status == mr.STATUS_REPAIRED
    log.info.assert_called_once()
    assert log.info.call_args.args[0].startswith("Repairing %s" % media)
    debug = [c.args[0] for c in log.debug.call_args_list]
    assert any(m.startswith("Probed ") for m in debug)
    assert "FFmpeg command: ffmpeg -i %s" % media in debug
    assert any("42s copied" in m for m in debug)
    assert any(m.startswith("Verified ") for m in debug)

  def test_verification_failure_keeps_original(self, media, tmp_path):
    result = _processor(FakeFFMpeg(DAMAGED, [_video(0, "h264", 1920, 1088, "136:135")])).repair_file(str(media))
    assert result.status == mr.STATUS_FAILED
    assert "do not match" in result.detail
    assert media.read_bytes() == b"original"
    assert os.listdir(tmp_path) == ["a.mp4"]

  def test_chapters_counted_on_source_and_output(self, media):
    result = _processor(FakeFFMpeg(DAMAGED, REPAIRED, chapters=(8, 8))).repair_file(str(media))
    assert result.status == mr.STATUS_REPAIRED

  def test_chapter_loss_keeps_original(self, media, tmp_path):
    result = _processor(FakeFFMpeg(DAMAGED, REPAIRED, chapters=(8, 0))).repair_file(str(media))
    assert result.status == mr.STATUS_FAILED
    assert result.detail.startswith("repaired file has 0 chapters, source has 8")
    assert media.read_bytes() == b"original"
    assert not (tmp_path / "a.sma").exists()

  def test_chapter_count_error_skips_the_comparison(self, media):
    result = _processor(FakeFFMpeg(DAMAGED, REPAIRED, chapters=RuntimeError("ffprobe died"))).repair_file(str(media))
    assert result.status == mr.STATUS_REPAIRED

  def test_hardlinked_original_is_rewritten_in_place(self, media, tmp_path):
    link = tmp_path / "seed.mp4"
    os.link(media, link)
    inode = os.stat(media).st_ino
    result = _processor(FakeFFMpeg(DAMAGED, REPAIRED)).repair_file(str(media))
    assert result.status == mr.STATUS_REPAIRED
    assert os.stat(media).st_ino == inode
    assert link.read_bytes() == b"repaired"
    assert not (tmp_path / "a.sma").exists()

  def test_failed_in_place_rewrite_keeps_verified_copy(self, media, tmp_path):
    os.link(media, tmp_path / "seed.mp4")
    with mock.patch.object(mr.shutil, "copyfile", side_effect=OSError("disk gone")):
      result = _processor(FakeFFMpeg(DAMAGED, REPAIRED)).repair_file(str(media))
    assert result.status == mr.STATUS_FAILED
    assert "original may be damaged, verified copy kept at %s" % (tmp_path / "a.sma") in result.detail
    assert (tmp_path / "a.sma").read_bytes() == b"repaired"

  def test_in_place_rewrite_size_mismatch_fails(self, media, tmp_path):
    media.write_bytes(b"original, longer than the copy")
    os.link(media, tmp_path / "seed.mp4")
    with mock.patch.object(mr.shutil, "copyfile"):
      result = _processor(FakeFFMpeg(DAMAGED, REPAIRED)).repair_file(str(media))
    assert result.status == mr.STATUS_FAILED
    assert "size after rewrite differs" in result.detail
    assert (tmp_path / "a.sma").exists()

  def test_ffmpeg_failure_keeps_original(self, media, tmp_path):
    result = _processor(FakeFFMpeg(DAMAGED, convert_error=RuntimeError("mux failed"))).repair_file(str(media))
    assert result.status == mr.STATUS_FAILED
    assert "mux failed" in result.detail
    assert media.read_bytes() == b"original"
    assert os.listdir(tmp_path) == ["a.mp4"]

  def test_refuses_when_nothing_playable_survives(self, media):
    ff = FakeFFMpeg([_video(0, "mjpeg", 600, 900, attached_pic=1), _data(1, "unknown", "tmcd")])
    result = _processor(ff).repair_file(str(media))
    assert result.status == mr.STATUS_FAILED
    assert "no audio or video" in result.detail
    assert ff.calls == []

  def test_permission_failure_is_logged_not_fatal(self, media):
    log = mock.MagicMock()
    rp = _processor(FakeFFMpeg(DAMAGED, REPAIRED), logger=log)
    with mock.patch.object(mr.os, "chmod", side_effect=PermissionError("denied")):
      assert rp.repair_file(str(media)).status == mr.STATUS_REPAIRED
    log.exception.assert_called_once()


# ---------------------------------------------------------------------------
# iter_targets
# ---------------------------------------------------------------------------


def test_iter_targets_walks_dirs_and_filters(tmp_path):
  (tmp_path / "show" / "S01").mkdir(parents=True)
  (tmp_path / ".hidden").mkdir()
  for name in ["show/S01/b.mp4", "show/S01/a.mkv", "show/S01/a.srt", "show/S01/b.sma", "show/.dot.mp4", ".hidden/x.mp4", "top.MOV"]:
    (tmp_path / name).write_bytes(b"x")
  single = tmp_path / "show" / "S01" / "b.mp4"
  skipped = tmp_path / "show" / "S01" / "a.srt"
  found = list(iter_targets([str(tmp_path), str(single), str(skipped)]))
  assert found == [
    str(tmp_path / "top.MOV"),
    str(tmp_path / "show" / "S01" / "a.mkv"),
    str(tmp_path / "show" / "S01" / "b.mp4"),
    str(single),
  ]
