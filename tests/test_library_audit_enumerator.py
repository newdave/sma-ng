"""Tests for ``resources.library_audit.enumerator``."""

from __future__ import annotations

import os

import pytest

from resources.library_audit.enumerator import enumerate_paths
from resources.library_audit.kinds import (
  KIND_HINT_MEDIA,
  KIND_HINT_PRECONV,
  KIND_HINT_SIDECAR,
  KIND_HINT_TMP,
)


class TestEnumeratePaths:
  def test_empty_roots(self):
    assert list(enumerate_paths([])) == []

  def test_skips_missing_root(self):
    assert list(enumerate_paths(["/nonexistent/path"])) == []

  def test_skips_empty_string_root(self):
    assert list(enumerate_paths([""])) == []

  def test_skips_non_directory_root(self, tmp_path):
    f = tmp_path / "file.mkv"
    f.write_bytes(b"")
    assert list(enumerate_paths([str(f)])) == []

  def test_classifies_mp4_as_media(self, tmp_path):
    (tmp_path / "movie.mp4").write_bytes(b"")
    out = list(enumerate_paths([str(tmp_path)]))
    assert (str(tmp_path / "movie.mp4"), KIND_HINT_MEDIA) in out

  def test_classifies_mkv_as_preconv(self, tmp_path):
    (tmp_path / "movie.mkv").write_bytes(b"")
    out = list(enumerate_paths([str(tmp_path)]))
    assert (str(tmp_path / "movie.mkv"), KIND_HINT_PRECONV) in out

  def test_classifies_srt_as_sidecar(self, tmp_path):
    (tmp_path / "movie.srt").write_bytes(b"")
    out = list(enumerate_paths([str(tmp_path)]))
    assert (str(tmp_path / "movie.srt"), KIND_HINT_SIDECAR) in out

  def test_classifies_tmp_extension(self, tmp_path):
    (tmp_path / "movie.tmp").write_bytes(b"")
    out = list(enumerate_paths([str(tmp_path)]))
    assert (str(tmp_path / "movie.tmp"), KIND_HINT_TMP) in out

  def test_classifies_tag_extension(self, tmp_path):
    (tmp_path / "movie.mp4.tag").write_bytes(b"")
    out = list(enumerate_paths([str(tmp_path)]))
    # .tag is in TMP_EXTS
    assert (str(tmp_path / "movie.mp4.tag"), KIND_HINT_TMP) in out

  def test_skips_dotfiles(self, tmp_path):
    (tmp_path / ".hidden.mp4").write_bytes(b"")
    out = list(enumerate_paths([str(tmp_path)]))
    assert out == []

  def test_skips_unknown_extension(self, tmp_path):
    (tmp_path / "notes.txt").write_bytes(b"")
    out = list(enumerate_paths([str(tmp_path)]))
    assert out == []

  def test_recurses_into_subdirs(self, tmp_path):
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "movie.mp4").write_bytes(b"")
    out = list(enumerate_paths([str(tmp_path)]))
    assert (str(sub / "movie.mp4"), KIND_HINT_MEDIA) in out

  def test_skips_named_directories(self, tmp_path):
    extras = tmp_path / "Behind the Scenes"
    extras.mkdir()
    (extras / "feature.mp4").write_bytes(b"")
    (tmp_path / "main.mp4").write_bytes(b"")
    out = list(enumerate_paths([str(tmp_path)], skip_dirs=["Behind the Scenes"]))
    paths = [p for p, _ in out]
    assert str(tmp_path / "main.mp4") in paths
    assert str(extras / "feature.mp4") not in paths

  def test_skips_named_dirs_case_insensitive(self, tmp_path):
    sub = tmp_path / "EXTRAS"
    sub.mkdir()
    (sub / "x.mp4").write_bytes(b"")
    out = list(enumerate_paths([str(tmp_path)], skip_dirs=["extras"]))
    assert out == []

  def test_skips_recycle_bin_path_at_root(self, tmp_path):
    (tmp_path / "movie.mp4").write_bytes(b"")
    out = list(enumerate_paths([str(tmp_path)], is_recycle_bin_path=lambda p: True))
    assert out == []

  def test_skips_recycle_bin_subdir(self, tmp_path):
    bin_dir = tmp_path / "trash"
    bin_dir.mkdir()
    (bin_dir / "old.mp4").write_bytes(b"")
    (tmp_path / "current.mp4").write_bytes(b"")
    out = list(
      enumerate_paths(
        [str(tmp_path)],
        is_recycle_bin_path=lambda p: "trash" in p,
      )
    )
    paths = [p for p, _ in out]
    assert str(tmp_path / "current.mp4") in paths
    assert str(bin_dir / "old.mp4") not in paths

  def test_handles_unreadable_directory(self, tmp_path, monkeypatch):
    """OSError from scandir is caught silently — enumerator continues."""

    real_scandir = os.scandir

    def picky_scandir(path, *args, **kwargs):
      if "blocked" in str(path):
        raise PermissionError("EACCES")
      return real_scandir(path, *args, **kwargs)

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "x.mp4").write_bytes(b"")
    (tmp_path / "ok.mp4").write_bytes(b"")
    monkeypatch.setattr(os, "scandir", picky_scandir)
    out = list(enumerate_paths([str(tmp_path)]))
    paths = [p for p, _ in out]
    assert str(tmp_path / "ok.mp4") in paths

  def test_handles_oserror_on_is_dir(self, tmp_path, monkeypatch):
    """os.DirEntry.is_dir raising OSError is silently skipped."""
    f = tmp_path / "x.mp4"
    f.write_bytes(b"")

    real_scandir = os.scandir

    class _BrokenEntry:
      def __init__(self, real):
        self._real = real
        self.name = real.name
        self.path = real.path

      def is_dir(self, follow_symlinks=False):
        raise OSError("simulated EIO")

    def patched_scandir(path):
      class _It:
        def __enter__(self_inner):
          self_inner._gen = iter([_BrokenEntry(e) for e in real_scandir(path)])
          return self_inner._gen

        def __exit__(self_inner, *_a):
          return False

      return _It()

    monkeypatch.setattr(os, "scandir", patched_scandir)
    out = list(enumerate_paths([str(tmp_path)]))
    # Both files (one with broken is_dir, one normal) are skipped
    # because the OSError on is_dir() bails out before classification.
    assert out == []
