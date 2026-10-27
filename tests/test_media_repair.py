"""Tests for resources/media_repair.py — stream-copy-only media repair."""

import json
import os
import subprocess
from types import SimpleNamespace
from unittest import mock

import pytest

from resources import media_repair as mr
from resources.media_repair import (
  RepairError,
  RepairPlan,
  assert_stream_copy,
  build_opts,
  diagnose,
  iter_targets,
  probe,
  repair_file,
  verify,
)


def _video(index, codec="h264", width=1920, height=1080, sar="1:1", attached_pic=0):
  return {
    "index": index,
    "codec_type": "video",
    "codec_name": codec,
    "width": width,
    "height": height,
    "sample_aspect_ratio": sar,
    "disposition": {"attached_pic": attached_pic},
  }


def _audio(index, codec="aac", channels=2):
  return {"index": index, "codec_type": "audio", "codec_name": codec, "channels": channels, "disposition": {}}


def _sub(index, codec="mov_text"):
  return {"index": index, "codec_type": "subtitle", "codec_name": codec, "disposition": {}}


def _data(index, codec="bin_data", tag="tmcd"):
  return {"index": index, "codec_type": "data", "codec_name": codec, "codec_tag_string": tag}


def _attachment(index, mimetype=None, filename=None):
  tags = {}
  if mimetype:
    tags["mimetype"] = mimetype
  if filename:
    tags["filename"] = filename
  return {"index": index, "codec_type": "attachment", "codec_name": "none", "tags": tags}


def _info(streams, chapters=0, duration="100.0"):
  return {"streams": streams, "chapters": [{"id": i} for i in range(chapters)], "format": {"duration": duration}}


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------


class TestDiagnose:
  def test_normal_file_needs_no_repair(self):
    plan = diagnose("/m/a.mp4", _info([_video(0), _audio(1), _sub(2)]))
    assert not plan.needs_repair
    assert plan.keep == [0, 1, 2]
    assert plan.describe() == "normal"

  def test_chapter_track_in_mp4_is_normal(self):
    plan = diagnose("/m/a.mp4", _info([_video(0), _audio(1), _data(2, "bin_data", "text")], chapters=3))
    assert not plan.needs_repair
    # Never mapped: the muxer rewrites it from the chapter list.
    assert plan.keep == [0, 1]

  def test_bin_data_without_chapters_is_dropped(self):
    plan = diagnose("/m/a.mp4", _info([_video(0), _audio(1), _data(2, "bin_data", "text")]))
    assert plan.drop_data == [2]

  def test_bin_data_in_mkv_is_dropped_even_with_chapters(self):
    plan = diagnose("/m/a.mkv", _info([_video(0), _audio(1), _data(2, "bin_data", "text")], chapters=2))
    assert plan.drop_data == [2]

  def test_tmcd_and_unknown_type_streams_are_dropped(self):
    streams = [_video(0), _audio(1), _data(2, "none", "tmcd"), {"index": 3, "codec_type": "unknown"}, {"index": 4}]
    plan = diagnose("/m/a.mp4", _info(streams))
    assert plan.drop_data == [2, 3, 4]
    assert plan.keep == [0, 1]
    assert plan.describe() == "data streams 2,3,4"

  def test_attached_picture_is_dropped(self):
    plan = diagnose("/m/a.mp4", _info([_video(0), _audio(1), _video(2, "mjpeg", 600, 900, attached_pic=1)]))
    assert plan.drop_artwork == [2]
    assert plan.keep == [0, 1]

  def test_demoted_poster_is_dropped(self):
    # Fallout S01E01 layout: the old repair remux turned the poster into a
    # real mp4v video track with no attached_pic flag.
    streams = [_video(0, "hevc", 3840, 2176, "136:135"), _audio(1, "eac3", 6), _sub(2), _video(3, "mjpeg", 1000, 1500, "8:3")]
    plan = diagnose("/m/a.mp4", _info(streams))
    assert plan.drop_artwork == [3]
    assert plan.dar is None  # aspect already corrected
    assert plan.keep == [0, 1, 2]

  def test_leading_poster_does_not_become_primary(self):
    streams = [_video(0, "png", 600, 900), _video(1, "h264", 1920, 1088), _audio(2)]
    plan = diagnose("/m/a.mp4", _info(streams))
    assert plan.drop_artwork == [0]
    assert plan.dar == "1920:1080"

  def test_image_only_video_is_kept_as_primary(self):
    # A genuine MJPEG movie has no other video stream; never strip its picture.
    plan = diagnose("/m/a.mov", _info([_video(0, "mjpeg", 640, 480), _audio(1)]))
    assert not plan.needs_repair

  def test_non_image_secondary_video_is_kept(self):
    plan = diagnose("/m/a.mkv", _info([_video(0), _video(1, "h264", 1280, 720), _audio(2)]))
    assert not plan.needs_repair
    assert plan.keep == [0, 1, 2]

  def test_image_attachment_dropped_font_kept(self):
    streams = [
      _video(0),
      _audio(1),
      _attachment(2, "font/ttf", "f.ttf"),
      _attachment(3, "image/jpeg", "cover.jpg"),
      _attachment(4, None, "poster.PNG"),
    ]
    plan = diagnose("/m/a.mkv", _info(streams))
    assert plan.drop_artwork == [3, 4]
    assert plan.keep == [0, 1, 2]

  @pytest.mark.parametrize("sar", [None, "1:1", "0:1"])
  def test_stretched_dims_get_corrective_dar(self, sar):
    plan = diagnose("/m/a.mp4", _info([_video(0, "hevc", 3840, 2176, sar), _audio(1)]))
    assert plan.dar == "3840:2160"
    assert plan.describe() == "display aspect 3840:2160"

  def test_corrected_or_standard_dims_left_alone(self):
    assert diagnose("/m/a.mp4", _info([_video(0, "hevc", 3840, 2176, "136:135")])).dar is None
    assert diagnose("/m/a.mp4", _info([_video(0, "hevc", 3840, 2160)])).dar is None
    assert diagnose("/m/a.mp4", _info([{**_video(0, "hevc", 3840, 2176), "width": None}])).dar is None

  def test_no_video_stream(self):
    plan = diagnose("/m/a.mp4", _info([_audio(0)]))
    assert not plan.needs_repair


# ---------------------------------------------------------------------------
# build_opts / assert_stream_copy
# ---------------------------------------------------------------------------


class TestBuildOpts:
  def test_mp4_opts_are_stream_copy_with_scoped_aspect(self):
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
    ]

  def test_mkv_opts_skip_mp4_muxer_flags(self):
    plan = RepairPlan(path="/m/a.mkv", keep=[0, 1], drop_artwork=[2])
    assert build_opts(plan, "/m/a.mkv") == ["-i", "/m/a.mkv", "-map", "0:0", "-map", "0:1", "-c", "copy"]

  @pytest.mark.parametrize(
    "opts",
    [
      ["-i", "x", "-c:v", "libx264"],
      ["-i", "x", "-c", "copy", "-vf", "scale=1920:1080"],
      ["-i", "x", "-c", "copy", "-b:v", "5M"],
      ["-i", "x", "-c", "libx265"],
      ["-i", "x", "-c", "copy", "-c", "aac"],
      ["-i", "x", "-map", "0:0"],
    ],
  )
  def test_guard_rejects_anything_but_stream_copy(self, opts):
    with pytest.raises(RepairError):
      assert_stream_copy(opts)

  def test_guard_accepts_option_values_that_look_like_flags(self):
    assert_stream_copy(["-i", "-odd-name.mp4", "-c", "copy"])


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


class TestProbe:
  def _run(self, monkeypatch, returncode=0, stdout="", stderr="", exc=None):
    def fake_run(cmd, **kwargs):
      if exc:
        raise exc
      return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(mr.subprocess, "run", fake_run)

  def test_returns_parsed_json(self, monkeypatch):
    self._run(monkeypatch, stdout=json.dumps(_info([_video(0)])))
    assert probe("/m/a.mp4", "ffprobe")["streams"][0]["codec_name"] == "h264"

  @pytest.mark.parametrize(
    "kwargs,message",
    [
      ({"exc": OSError("no binary")}, "ffprobe failed"),
      ({"exc": subprocess.TimeoutExpired("ffprobe", 1)}, "ffprobe failed"),
      ({"returncode": 1, "stderr": "moov atom not found"}, "moov atom not found"),
      ({"stdout": "not json"}, "invalid JSON"),
      ({"stdout": json.dumps({"streams": []})}, "no streams"),
    ],
  )
  def test_failures_raise_repair_error(self, monkeypatch, kwargs, message):
    self._run(monkeypatch, **kwargs)
    with pytest.raises(RepairError, match=message):
      probe("/m/a.mp4", "ffprobe")


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


class TestVerify:
  SOURCE = _info([_video(0, "h264", 1920, 1088), _audio(1), _video(2, "mjpeg", 600, 900), _data(3)], chapters=2)
  PLAN = RepairPlan(path="/m/a.mp4", keep=[0, 1], drop_data=[3], drop_artwork=[2], dar="1920:1080")

  def _output(self, **overrides):
    streams = overrides.pop("streams", [_video(0, "h264", 1920, 1088, "136:135"), _audio(1), _data(2, "bin_data", "text")])
    data = _info(streams, chapters=overrides.pop("chapters", 2), duration=overrides.pop("duration", "100.0"))
    return data

  def test_matching_output_passes(self):
    verify(self.PLAN, self.SOURCE, self._output(), "/m/a.mp4")

  def test_stream_mismatch_fails(self):
    with pytest.raises(RepairError, match="do not match"):
      verify(self.PLAN, self.SOURCE, self._output(streams=[_video(0, "hevc", 1920, 1088, "136:135"), _audio(1)]), "/m/a.mp4")

  @pytest.mark.parametrize("duration", ["90.0", None, "garbage"])
  def test_duration_drift_fails(self, duration):
    with pytest.raises(RepairError, match="duration"):
      verify(self.PLAN, self.SOURCE, self._output(duration=duration), "/m/a.mp4")

  def test_chapter_loss_fails(self):
    with pytest.raises(RepairError, match="chapter"):
      verify(self.PLAN, self.SOURCE, self._output(chapters=0), "/m/a.mp4")

  def test_output_still_damaged_fails(self):
    # Aspect fix did not land: output still has square pixels.
    with pytest.raises(RepairError, match="still needs repair"):
      verify(self.PLAN, self.SOURCE, self._output(streams=[_video(0, "h264", 1920, 1088, "1:1"), _audio(1)]), "/m/a.mp4")


# ---------------------------------------------------------------------------
# repair_file
# ---------------------------------------------------------------------------


DAMAGED = _info([_video(0, "h264", 1920, 1088), _audio(1), _video(2, "mjpeg", 600, 900), _data(3, "none", "tmcd")])
REPAIRED = _info([_video(0, "h264", 1920, 1088, "136:135"), _audio(1)])


class FakeFFMpeg:
  ffprobe_path = "/usr/bin/ffprobe"

  def __init__(self, output=b"repaired", error=None):
    self.output = output
    self.error = error
    self.calls = []

  def convert(self, outfile, opts, timeout=None):
    self.calls.append((outfile, list(opts), timeout))
    with open(outfile, "wb") as f:
      f.write(self.output)
    if self.error:
      raise self.error
    yield 0, "cmd"


def _patch_probe(monkeypatch, source, output=None):
  def fake_probe(path, ffprobe_path):
    if mr.TEMP_MARKER in os.path.basename(path):
      if isinstance(output, Exception):
        raise output
      return output
    if isinstance(source, Exception):
      raise source
    return source

  monkeypatch.setattr(mr, "probe", fake_probe)


class TestRepairFile:
  def test_normal_file_untouched(self, tmp_path, monkeypatch):
    p = tmp_path / "a.mp4"
    p.write_bytes(b"original")
    _patch_probe(monkeypatch, _info([_video(0), _audio(1)]))
    ff = FakeFFMpeg()
    result = repair_file(str(p), ff)
    assert result.status == mr.STATUS_NORMAL
    assert ff.calls == []
    assert p.read_bytes() == b"original"

  def test_dry_run_reports_without_writing(self, tmp_path, monkeypatch):
    p = tmp_path / "a.mp4"
    p.write_bytes(b"original")
    _patch_probe(monkeypatch, DAMAGED)
    ff = FakeFFMpeg()
    result = repair_file(str(p), ff, dry_run=True)
    assert result.status == mr.STATUS_WOULD_REPAIR
    assert result.detail == "data streams 3; artwork streams 2; display aspect 1920:1080"
    assert ff.calls == []

  def test_probe_failure_reported(self, tmp_path, monkeypatch):
    p = tmp_path / "a.mp4"
    p.write_bytes(b"original")
    _patch_probe(monkeypatch, RepairError("ffprobe exited 1: broken"))
    result = repair_file(str(p), FakeFFMpeg())
    assert result.status == mr.STATUS_FAILED
    assert "broken" in result.detail

  def test_successful_repair_swaps_in_verified_copy(self, tmp_path, monkeypatch):
    p = tmp_path / "a.mp4"
    p.write_bytes(b"original")
    os.chmod(p, 0o640)
    _patch_probe(monkeypatch, DAMAGED, REPAIRED)
    ff = FakeFFMpeg()
    result = repair_file(str(p), ff)
    assert result.status == mr.STATUS_REPAIRED
    assert p.read_bytes() == b"repaired"
    assert os.stat(p).st_mode & 0o777 == 0o640
    outfile, opts, timeout = ff.calls[0]
    assert outfile == str(tmp_path / ".a.repairing.mp4")
    assert opts[opts.index("-c") + 1] == "copy"
    assert timeout == 0
    assert os.listdir(tmp_path) == ["a.mp4"]

  def test_verification_failure_keeps_original(self, tmp_path, monkeypatch):
    p = tmp_path / "a.mp4"
    p.write_bytes(b"original")
    _patch_probe(monkeypatch, DAMAGED, _info([_video(0, "h264", 1920, 1088, "136:135")]))
    result = repair_file(str(p), FakeFFMpeg())
    assert result.status == mr.STATUS_FAILED
    assert "do not match" in result.detail
    assert p.read_bytes() == b"original"
    assert os.listdir(tmp_path) == ["a.mp4"]

  def test_ffmpeg_failure_keeps_original(self, tmp_path, monkeypatch):
    p = tmp_path / "a.mp4"
    p.write_bytes(b"original")
    _patch_probe(monkeypatch, DAMAGED)
    log = mock.MagicMock()
    result = repair_file(str(p), FakeFFMpeg(error=RuntimeError("mux failed")), logger=log)
    assert result.status == mr.STATUS_FAILED
    assert "mux failed" in result.detail
    assert p.read_bytes() == b"original"
    assert os.listdir(tmp_path) == ["a.mp4"]
    log.debug.assert_called()

  def test_refuses_when_nothing_playable_survives(self, tmp_path, monkeypatch):
    p = tmp_path / "a.mp4"
    p.write_bytes(b"original")
    _patch_probe(monkeypatch, _info([_video(0, "mjpeg", 600, 900, attached_pic=1), _data(1, "none", "tmcd")]))
    ff = FakeFFMpeg()
    result = repair_file(str(p), ff)
    assert result.status == mr.STATUS_FAILED
    assert "no audio or video" in result.detail
    assert ff.calls == []


# ---------------------------------------------------------------------------
# iter_targets
# ---------------------------------------------------------------------------


def test_iter_targets_walks_dirs_and_filters(tmp_path):
  (tmp_path / "show" / "S01").mkdir(parents=True)
  (tmp_path / ".hidden").mkdir()
  for name in ["show/S01/b.mp4", "show/S01/a.mkv", "show/S01/a.srt", "show/S01/.a.repairing.mp4", "show/.dot.mp4", ".hidden/x.mp4", "top.MOV"]:
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
