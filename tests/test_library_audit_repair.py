"""Unit tests for resources/library_audit/repair.py (hybrid-aspect remux)."""

import os
import unittest.mock as mock
from types import SimpleNamespace

from resources.library_audit.repair import repair_hybrid_aspect


def _patch_ffmpeg(monkeypatch, convert=None, probe=None):
  """Patch repair.FFMpeg with a fake whose ``convert`` is *convert* (a
  generator-style callable taking (outfile, opts, timeout=...)) and whose
  ``probe`` returns *probe* (an exception instance is raised instead).
  Returns a dict capturing constructor kwargs and convert call args."""
  calls = {"init": None, "outfile": None, "opts": None, "timeout": None}

  def default_convert(outfile, opts, timeout=None):
    with open(outfile, "wb") as f:
      f.write(b"remuxed")
    yield 1.0, "frame=1"

  inner = convert or default_convert

  class FakeFFMpeg:
    def __init__(self, **kwargs):
      calls["init"] = kwargs

    def probe(self, fname):
      if isinstance(probe, Exception):
        raise probe
      return probe

    def convert(self, outfile, opts, timeout=None):
      calls["outfile"] = outfile
      calls["opts"] = list(opts)
      calls["timeout"] = timeout
      return inner(outfile, opts, timeout=timeout)

  monkeypatch.setattr("resources.library_audit.repair.FFMpeg", FakeFFMpeg)
  return calls


def test_repair_success_remuxes_and_removes_aside(tmp_path, monkeypatch):
  p = tmp_path / "movie.mp4"
  p.write_bytes(b"original")
  calls = _patch_ffmpeg(monkeypatch)
  log = mock.MagicMock()

  assert repair_hybrid_aspect(str(p), "1920:1080", logger=log) is True
  # Output written back at the original name; aside copy removed.
  assert p.read_bytes() == b"remuxed"
  assert not os.path.exists(str(p) + ".aspectfix")
  # Remux is a stream-copy with the corrective display aspect ratio; data,
  # unknown-type and attached-picture streams are never copied.
  assert calls["outfile"] == str(p)
  assert calls["opts"] == [
    "-i",
    str(p) + ".aspectfix",
    "-map",
    "0:V?",
    "-map",
    "0:a?",
    "-map",
    "0:s?",
    "-c",
    "copy",
    "-aspect:v:0",
    "1920:1080",
  ]
  assert calls["timeout"] == 0
  log.info.assert_called()


def test_repair_uses_ffmpeg_dir_binaries(tmp_path, monkeypatch):
  p = tmp_path / "movie.mp4"
  p.write_bytes(b"original")
  calls = _patch_ffmpeg(monkeypatch)

  assert repair_hybrid_aspect(str(p), "1280:720", ffmpeg_dir="/opt/ff") is True
  assert calls["init"] == {
    "ffmpeg_path": os.path.join("/opt/ff", "ffmpeg"),
    "ffprobe_path": os.path.join("/opt/ff", "ffprobe"),
  }


def test_repair_failure_restores_original(tmp_path, monkeypatch):
  p = tmp_path / "movie.mp4"
  p.write_bytes(b"original")

  def exploding_convert(outfile, opts, timeout=None):
    # Simulate ffmpeg dying partway: partial output exists, then the error.
    with open(outfile, "wb") as f:
      f.write(b"partial")
    raise RuntimeError("muxer blew up")
    yield  # unreachable; makes this a generator like the real convert

  _patch_ffmpeg(monkeypatch, convert=exploding_convert)
  log = mock.MagicMock()

  assert repair_hybrid_aspect(str(p), "1920:1080", logger=log) is False
  # Partial output removed, original restored at its old name, aside gone.
  assert p.read_bytes() == b"original"
  assert not os.path.exists(str(p) + ".aspectfix")
  log.error.assert_called()


def test_repair_missing_file_returns_false(tmp_path, monkeypatch):
  def never(**_kwargs):
    raise AssertionError("FFMpeg must not be constructed for a missing file")

  monkeypatch.setattr("resources.library_audit.repair.FFMpeg", never)
  log = mock.MagicMock()
  assert repair_hybrid_aspect(str(tmp_path / "nope.mp4"), "1920:1080", logger=log) is False
  log.warning.assert_called()


def test_repair_aside_collision_gets_numbered_suffix(tmp_path, monkeypatch):
  p = tmp_path / "movie.mp4"
  p.write_bytes(b"original")
  stale = tmp_path / "movie.mp4.aspectfix"
  stale.write_bytes(b"stale leftover")
  stale2 = tmp_path / "movie.mp4.aspectfix.2"
  stale2.write_bytes(b"older leftover")
  calls = _patch_ffmpeg(monkeypatch)

  assert repair_hybrid_aspect(str(p), "1920:1080") is True
  # The remux input skipped both occupied aside names.
  assert calls["opts"][calls["opts"].index("-i") + 1] == str(p) + ".aspectfix.3"
  # Pre-existing leftovers are untouched; the new aside was removed.
  assert stale.read_bytes() == b"stale leftover"
  assert stale2.read_bytes() == b"older leftover"
  assert not os.path.exists(str(p) + ".aspectfix.3")
  assert p.read_bytes() == b"remuxed"


def test_repair_works_without_logger(tmp_path, monkeypatch):
  p = tmp_path / "movie.mp4"
  p.write_bytes(b"original")

  def exploding_convert(outfile, opts, timeout=None):
    raise RuntimeError("boom")
    yield  # unreachable; makes this a generator like the real convert

  _patch_ffmpeg(monkeypatch, convert=exploding_convert)
  # No logger passed: both the failure and restore paths must not raise.
  assert repair_hybrid_aspect(str(p), "1920:1080") is False
  assert p.read_bytes() == b"original"


def test_repair_aspect_scoped_to_primary_video_and_never_maps_cover_art(tmp_path, monkeypatch):
  # Regression: "-map 0 ... -aspect W:H" stamped the DAR onto the poster too
  # and remuxed it as a second real (mp4v) video track.
  p = tmp_path / "movie.mp4"
  p.write_bytes(b"original")
  calls = _patch_ffmpeg(monkeypatch)

  assert repair_hybrid_aspect(str(p), "16:9") is True
  opts = calls["opts"]
  assert "-aspect" not in opts
  assert opts[opts.index("-aspect:v:0") + 1] == "16:9"
  maps = [opts[i + 1] for i, o in enumerate(opts) if o == "-map"]
  assert maps == ["0:V?", "0:a?", "0:s?"]


def _stream(index, type_, codec, attached_pic=0):
  return SimpleNamespace(index=index, type=type_, codec=codec, attached_pic=attached_pic)


def test_repair_maps_primary_video_by_index_and_drops_demoted_poster(tmp_path, monkeypatch):
  # A file the old "-map 0" remux already damaged: the poster lost its
  # attached_pic flag and became a real video track, so 0:V would keep it.
  p = tmp_path / "movie.mp4"
  p.write_bytes(b"original")
  info = SimpleNamespace(
    streams=[
      _stream(0, "video", "hevc"),
      _stream(1, "audio", "eac3"),
      _stream(2, "subtitle", "mov_text"),
      _stream(3, "video", "mjpeg"),
      _stream(4, "data", "bin_data"),
    ],
    video=None,
  )
  calls = _patch_ffmpeg(monkeypatch, probe=info)

  assert repair_hybrid_aspect(str(p), "3840:2160") is True
  opts = calls["opts"]
  maps = [opts[i + 1] for i, o in enumerate(opts) if o == "-map"]
  assert maps == ["0:0", "0:a?", "0:s?"]
  assert opts[opts.index("-aspect:v:0") + 1] == "3840:2160"


def test_repair_primary_video_skips_leading_poster(tmp_path, monkeypatch):
  p = tmp_path / "movie.mp4"
  p.write_bytes(b"original")
  info = SimpleNamespace(streams=[_stream(0, "video", "png"), _stream(1, "video", "h264")], video=None)
  calls = _patch_ffmpeg(monkeypatch, probe=info)

  assert repair_hybrid_aspect(str(p), "1920:1080") is True
  assert calls["opts"][calls["opts"].index("-map") + 1] == "0:1"


def test_repair_probe_failure_falls_back_to_type_map(tmp_path, monkeypatch):
  p = tmp_path / "movie.mp4"
  p.write_bytes(b"original")
  calls = _patch_ffmpeg(monkeypatch, probe=RuntimeError("ffprobe died"))

  assert repair_hybrid_aspect(str(p), "1920:1080") is True
  assert calls["opts"][calls["opts"].index("-map") + 1] == "0:V?"
