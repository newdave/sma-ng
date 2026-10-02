"""Unit tests for resources/library_audit/repair.py (hybrid-aspect remux)."""

import os
import unittest.mock as mock

from resources.library_audit.repair import repair_hybrid_aspect


def _patch_ffmpeg(monkeypatch, convert=None):
  """Patch repair.FFMpeg with a fake whose ``convert`` is *convert* (a
  generator-style callable taking (outfile, opts, timeout=...)). Returns a
  dict capturing constructor kwargs and convert call args."""
  calls = {"init": None, "outfile": None, "opts": None, "timeout": None}

  def default_convert(outfile, opts, timeout=None):
    with open(outfile, "wb") as f:
      f.write(b"remuxed")
    yield 1.0, "frame=1"

  inner = convert or default_convert

  class FakeFFMpeg:
    def __init__(self, **kwargs):
      calls["init"] = kwargs

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
  # Remux is a stream-copy with the corrective display aspect ratio.
  assert calls["outfile"] == str(p)
  assert calls["opts"] == ["-i", str(p) + ".aspectfix", "-map", "0", "-c", "copy", "-aspect", "1920:1080"]
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
  assert calls["opts"][1] == str(p) + ".aspectfix.3"
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
