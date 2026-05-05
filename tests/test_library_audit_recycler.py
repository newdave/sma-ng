"""Tests for ``resources.library_audit.recycler``.

The recycler is the auto-fix sidecar mover used by the library auditor.
It mirrors :py:meth:`resources.mediaprocessor.MediaProcessor._recycle_to_bin`,
so behaviour parity is exercised here for the helper that the auditor
actually calls.
"""

from __future__ import annotations

import os

import pytest

from resources.library_audit.recycler import _next_collision_dst, move_to_recycle_bin

# ---------------------------------------------------------------------------
# _next_collision_dst()
# ---------------------------------------------------------------------------


class TestNextCollisionDst:
  def test_no_collision_returns_basename(self, tmp_path):
    out = _next_collision_dst(str(tmp_path), "movie.mkv")
    assert out == str(tmp_path / "movie.mkv")

  def test_first_collision_appends_dot2(self, tmp_path):
    (tmp_path / "movie.mkv").write_bytes(b"x")
    out = _next_collision_dst(str(tmp_path), "movie.mkv")
    assert out == str(tmp_path / "movie.2.mkv")

  def test_chains_until_free_slot(self, tmp_path):
    (tmp_path / "movie.mkv").write_bytes(b"x")
    (tmp_path / "movie.2.mkv").write_bytes(b"x")
    (tmp_path / "movie.3.mkv").write_bytes(b"x")
    out = _next_collision_dst(str(tmp_path), "movie.mkv")
    assert out == str(tmp_path / "movie.4.mkv")

  def test_extensionless_basename(self, tmp_path):
    (tmp_path / "README").write_bytes(b"x")
    out = _next_collision_dst(str(tmp_path), "README")
    assert out == str(tmp_path / "README.2")

  def test_multi_dot_basename_only_splits_last(self, tmp_path):
    # `os.path.splitext("foo.bar.mkv")` → ("foo.bar", ".mkv")
    (tmp_path / "foo.bar.mkv").write_bytes(b"x")
    out = _next_collision_dst(str(tmp_path), "foo.bar.mkv")
    assert out == str(tmp_path / "foo.bar.2.mkv")


# ---------------------------------------------------------------------------
# move_to_recycle_bin()
# ---------------------------------------------------------------------------


class TestMoveToRecycleBin:
  def test_returns_none_when_no_bin_configured(self, tmp_path):
    src = tmp_path / "src.mkv"
    src.write_bytes(b"x")
    assert move_to_recycle_bin(str(src), None) is None
    # source must be left alone — None means dry-run, not "still delete"
    assert src.exists()

  def test_returns_none_when_no_bin_configured_empty_string(self, tmp_path):
    src = tmp_path / "src.mkv"
    src.write_bytes(b"x")
    assert move_to_recycle_bin(str(src), "") is None
    assert src.exists()

  def test_returns_none_when_source_missing(self, tmp_path):
    bin_dir = tmp_path / "bin"
    assert move_to_recycle_bin(str(tmp_path / "missing.mkv"), str(bin_dir)) is None
    # bin dir is NOT created when the source doesn't exist
    assert not bin_dir.exists()

  def test_returns_none_when_source_is_directory(self, tmp_path):
    src_dir = tmp_path / "subdir"
    src_dir.mkdir()
    bin_dir = tmp_path / "bin"
    # `os.path.isfile` is False for directories — caller should pass files
    assert move_to_recycle_bin(str(src_dir), str(bin_dir)) is None
    assert src_dir.exists()

  def test_happy_path_moves_file(self, tmp_path):
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"data")
    bin_dir = tmp_path / "bin"
    dst = move_to_recycle_bin(str(src), str(bin_dir))
    assert dst == str(bin_dir / "movie.mkv")
    assert os.path.isfile(dst)
    assert open(dst, "rb").read() == b"data"
    assert not src.exists()

  def test_creates_bin_directory_if_missing(self, tmp_path):
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"data")
    bin_dir = tmp_path / "deeply" / "nested" / "bin"
    assert not bin_dir.exists()
    dst = move_to_recycle_bin(str(src), str(bin_dir))
    assert dst is not None
    assert os.path.isfile(dst)

  def test_collision_in_bin_uses_dot2(self, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "movie.mkv").write_bytes(b"old")

    src = tmp_path / "movie.mkv"
    src.write_bytes(b"new")
    dst = move_to_recycle_bin(str(src), str(bin_dir))
    assert dst == str(bin_dir / "movie.2.mkv")
    assert open(dst, "rb").read() == b"new"
    assert open(bin_dir / "movie.mkv", "rb").read() == b"old"
    assert not src.exists()

  def test_copy_failure_propagates_and_preserves_source(self, tmp_path, monkeypatch):
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"data")
    bin_dir = tmp_path / "bin"

    import shutil as _shutil

    def boom(*_a, **_kw):
      raise OSError("simulated copy failure")

    monkeypatch.setattr(_shutil, "copy2", boom)

    with pytest.raises(OSError, match="simulated copy failure"):
      move_to_recycle_bin(str(src), str(bin_dir))

    # Source MUST still exist — we should never delete on failed copy.
    assert src.exists()
    # Tmp file should have been cleaned up if it was ever written.
    leftover = list(bin_dir.glob("*.smatmp")) if bin_dir.exists() else []
    assert leftover == []

  def test_replace_failure_cleans_up_tmpfile(self, tmp_path, monkeypatch):
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"data")
    bin_dir = tmp_path / "bin"

    def boom_replace(_a, _b):
      raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
      move_to_recycle_bin(str(src), str(bin_dir))

    assert src.exists()
    leftover = list(bin_dir.glob("*.smatmp"))
    assert leftover == [], f"leftover tmp files: {leftover}"

  def test_tmpfile_unlink_failure_is_swallowed(self, tmp_path, monkeypatch):
    """If both the copy AND the cleanup fail, the original copy
    exception is still raised — the cleanup is best-effort."""
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"data")
    bin_dir = tmp_path / "bin"

    import shutil as _shutil

    def boom_copy(*_a, **_kw):
      # Create a tmp file then fail, so cleanup has something to unlink
      raise OSError("copy failed")

    real_remove = os.remove

    def picky_remove(p):
      if p.endswith(".smatmp"):
        raise OSError("simulated cleanup failure")
      return real_remove(p)

    monkeypatch.setattr(_shutil, "copy2", boom_copy)
    monkeypatch.setattr(os, "remove", picky_remove)

    # The original copy failure should still be the one that propagates,
    # not the cleanup failure (which is swallowed).
    with pytest.raises(OSError, match="copy failed"):
      move_to_recycle_bin(str(src), str(bin_dir))
