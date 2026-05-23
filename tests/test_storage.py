"""Tests for resources/daemon/storage.py — orphan sweep + disk-usage helpers."""

from __future__ import annotations

import os
import time

import pytest

from resources.daemon import storage


def _touch(path, *, size: int = 0, age_seconds: float = 0.0) -> str:
  """Write *size* bytes at *path* and backdate its mtime by *age_seconds*."""
  with open(path, "wb") as f:
    if size > 0:
      f.write(b"x" * size)
  if age_seconds > 0:
    past = time.time() - age_seconds
    os.utime(path, (past, past))
  return path


class TestSweepOutputDirectory:
  def test_happy_path_removes_orphans(self, tmp_path):
    sma = _touch(str(tmp_path / "a.sma"), size=10, age_seconds=3600)
    smatmp = _touch(str(tmp_path / "b.smatmp"), size=20, age_seconds=3600)
    empty_mp4 = _touch(str(tmp_path / "c.mp4"), size=0, age_seconds=3600)
    keep_mp4 = _touch(str(tmp_path / "d.mp4"), size=500, age_seconds=3600)
    keep_other = _touch(str(tmp_path / "e.txt"), size=100, age_seconds=3600)

    summary = storage.sweep_output_directory(str(tmp_path), "sma", max_age_seconds=60)

    assert summary.sma_count == 1
    assert summary.smatmp_count == 1
    assert summary.empty_mp4_count == 1
    assert summary.freed_bytes == 30  # 10 + 20 + 0
    assert not os.path.exists(sma)
    assert not os.path.exists(smatmp)
    assert not os.path.exists(empty_mp4)
    assert os.path.exists(keep_mp4)
    assert os.path.exists(keep_other)

  def test_missing_directory_returns_zero_summary(self):
    summary = storage.sweep_output_directory("/nonexistent/path/xyz123", "sma", max_age_seconds=60)
    assert summary == storage.SweptSummary()

  def test_recent_files_are_skipped(self, tmp_path):
    young_sma = _touch(str(tmp_path / "young.sma"), size=10, age_seconds=0)
    old_sma = _touch(str(tmp_path / "old.sma"), size=10, age_seconds=7200)
    summary = storage.sweep_output_directory(str(tmp_path), "sma", max_age_seconds=3600)
    assert summary.sma_count == 1
    assert os.path.exists(young_sma)
    assert not os.path.exists(old_sma)

  def test_nonempty_mp4_preserved(self, tmp_path):
    big_mp4 = _touch(str(tmp_path / "big.mp4"), size=1024, age_seconds=86400)
    summary = storage.sweep_output_directory(str(tmp_path), "sma", max_age_seconds=60)
    assert summary.empty_mp4_count == 0
    assert os.path.exists(big_mp4)

  def test_zero_max_age_is_noop(self, tmp_path):
    _touch(str(tmp_path / "f.sma"), size=10, age_seconds=86400)
    summary = storage.sweep_output_directory(str(tmp_path), "sma", max_age_seconds=0)
    assert summary == storage.SweptSummary()
    assert os.path.exists(str(tmp_path / "f.sma"))

  def test_custom_temp_extension_with_dot(self, tmp_path):
    custom = _touch(str(tmp_path / "g.tmp"), size=5, age_seconds=3600)
    # standard .sma file should be ignored when temp_ext overrides it.
    extra = _touch(str(tmp_path / "h.sma"), size=5, age_seconds=3600)
    summary = storage.sweep_output_directory(str(tmp_path), ".tmp", max_age_seconds=60)
    assert summary.sma_count == 1
    assert not os.path.exists(custom)
    assert os.path.exists(extra)

  def test_empty_temp_extension_defaults_to_sma(self, tmp_path):
    p = _touch(str(tmp_path / "i.sma"), size=4, age_seconds=3600)
    summary = storage.sweep_output_directory(str(tmp_path), "", max_age_seconds=60)
    assert summary.sma_count == 1
    assert not os.path.exists(p)

  def test_permission_error_returns_zero(self, tmp_path, monkeypatch):
    def _boom(_):
      raise PermissionError("nope")

    monkeypatch.setattr(storage.os, "scandir", _boom)
    summary = storage.sweep_output_directory(str(tmp_path), "sma", max_age_seconds=60)
    assert summary == storage.SweptSummary()

  def test_empty_output_dir_argument_returns_zero(self):
    assert storage.sweep_output_directory("", "sma", 60) == storage.SweptSummary()


class TestOutputDirUsage:
  def test_returns_three_tuple_for_existing_directory(self, tmp_path):
    usage = storage.output_dir_usage(str(tmp_path))
    assert usage.total > 0
    assert usage.free >= 0

  def test_missing_directory_returns_zeros(self):
    usage = storage.output_dir_usage("/definitely/not/here/xyz123")
    assert usage == storage.DiskUsage()

  def test_empty_string_returns_zeros(self):
    assert storage.output_dir_usage("") == storage.DiskUsage()

  def test_permission_error_returns_zeros(self, tmp_path, monkeypatch):
    def _boom(_):
      raise PermissionError("denied")

    monkeypatch.setattr(storage.shutil, "disk_usage", _boom)
    assert storage.output_dir_usage(str(tmp_path)) == storage.DiskUsage()


@pytest.mark.parametrize("kind", ["sma", "smatmp", "empty_mp4"])
def test_sweep_summary_fields_are_addressable(kind):
  """SweptSummary exposes per-kind counts as attributes."""
  s = storage.SweptSummary(sma_count=1, smatmp_count=2, empty_mp4_count=3, freed_bytes=42)
  assert s.sma_count == 1
  assert s.smatmp_count == 2
  assert s.empty_mp4_count == 3
  assert s.freed_bytes == 42
  assert getattr(s, f"{kind}_count") in (1, 2, 3)
