"""Tests for ``resources.daemon.log_archiver.LogArchiver``.

Drives the archiver against a mocked job_db so the gzipped JSONL writer +
the `prune_old_files` walk are exercised without touching a real DB.
"""

from __future__ import annotations

import datetime
import gzip
import json
import logging
import os
import time
from unittest.mock import MagicMock

import pytest

from resources.daemon.log_archiver import LogArchiver


@pytest.fixture
def fake_job_db():
  db = MagicMock()
  db.get_logs_for_archival.return_value = []
  db.delete_logs_before.return_value = 0
  return db


@pytest.fixture
def archiver(tmp_path):
  log = logging.getLogger("test.archiver")
  return LogArchiver(
    archive_dir=str(tmp_path / "archive"),
    archive_after_days=30,
    delete_after_days=90,
    logger=log,
  )


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


class TestRun:
  def test_run_no_records(self, archiver, fake_job_db):
    archiver.run(fake_job_db)
    fake_job_db.delete_logs_before.assert_not_called()

  def test_run_archives_and_deletes(self, archiver, fake_job_db, tmp_path):
    ts = datetime.datetime(2026, 5, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
    fake_job_db.get_logs_for_archival.return_value = [
      {"id": 1, "node_id": "n1", "level": "INFO", "logger": "app", "message": "hi", "timestamp": ts},
      {"id": 2, "node_id": "n1", "level": "ERROR", "logger": "app", "message": "oops", "timestamp": ts},
    ]
    fake_job_db.delete_logs_before.return_value = 2
    archiver.run(fake_job_db)
    fake_job_db.delete_logs_before.assert_called_once_with(30)
    archive_path = tmp_path / "archive" / "n1" / "2026-05-01.jsonl.gz"
    assert archive_path.exists()
    with gzip.open(archive_path, "rt") as f:
      lines = f.read().strip().split("\n")
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["node_id"] == "n1"
    assert rec["timestamp"] == ts.isoformat()

  def test_run_swallows_archival_exception(self, archiver, fake_job_db):
    fake_job_db.get_logs_for_archival.side_effect = RuntimeError("db down")
    # Should NOT raise.
    archiver.run(fake_job_db)

  def test_run_swallows_prune_exception(self, archiver, fake_job_db, monkeypatch):
    """Prune step is best-effort even when scandir fails."""
    monkeypatch.setattr(
      "os.scandir",
      MagicMock(side_effect=OSError("permission denied")),
    )
    archiver.run(fake_job_db)

  def test_run_skips_prune_when_delete_disabled(self, archiver, fake_job_db, monkeypatch):
    archiver._delete_after_days = 0
    pruned = MagicMock()
    monkeypatch.setattr(archiver, "_prune_old_files", pruned)
    archiver.run(fake_job_db)
    pruned.assert_not_called()


# ---------------------------------------------------------------------------
# _write_archive() — happy + failure paths
# ---------------------------------------------------------------------------


class TestWriteArchive:
  def test_write_archive_creates_file(self, archiver, tmp_path):
    ts = datetime.datetime(2026, 5, 1, 10, 0)
    records = [
      {"id": 1, "node_id": "n1", "level": "INFO", "message": "x", "timestamp": ts},
    ]
    ok = archiver._write_archive("n1", ts.date(), records)
    assert ok is True
    archive_path = tmp_path / "archive" / "n1" / "2026-05-01.jsonl.gz"
    assert archive_path.exists()

  def test_write_archive_handles_naive_date(self, archiver, tmp_path):
    """date may already BE a date object (no .date() method)."""
    d = datetime.date(2026, 5, 1)
    records = [
      {"id": 1, "node_id": "n1", "level": "INFO", "message": "x", "timestamp": d},
    ]
    ok = archiver._write_archive("n1", d, records)
    assert ok is True
    # Verify date passed through (no further isoformat)
    with gzip.open(tmp_path / "archive" / "n1" / "2026-05-01.jsonl.gz", "rt") as f:
      rec = json.loads(f.read().strip())
    assert rec["timestamp"] == d.isoformat()

  def test_write_archive_failure_returns_false(self, archiver, monkeypatch):
    def boom(*_a, **_kw):
      raise OSError("disk full")

    monkeypatch.setattr("gzip.open", boom)
    ok = archiver._write_archive("n1", datetime.date(2026, 5, 1), [{"id": 1}])
    assert ok is False

  def test_write_archive_cleans_up_tmpfile_on_failure(self, archiver, tmp_path, monkeypatch):
    """When os.replace fails, the .tmp file should be removed."""
    real_replace = os.replace

    def picky_replace(src, dst):
      raise OSError("rename failed")

    monkeypatch.setattr(os, "replace", picky_replace)
    ok = archiver._write_archive("n1", datetime.date(2026, 5, 1), [{"id": 1, "node_id": "n1", "message": "x", "timestamp": "2026-05-01"}])
    assert ok is False
    leftover = list((tmp_path / "archive" / "n1").glob("*.tmp"))
    assert leftover == []

  def test_write_archive_when_db_returns_no_archival_records_no_delete(self, archiver, fake_job_db):
    fake_job_db.get_logs_for_archival.return_value = []
    archiver.run(fake_job_db)
    fake_job_db.delete_logs_before.assert_not_called()

  def test_write_failure_skips_delete(self, archiver, fake_job_db, monkeypatch):
    """If any archive fails to write, the DB rows are NOT deleted."""
    ts = datetime.datetime(2026, 5, 1)
    fake_job_db.get_logs_for_archival.return_value = [
      {"id": 1, "node_id": "n1", "level": "INFO", "message": "x", "timestamp": ts},
    ]
    monkeypatch.setattr(archiver, "_write_archive", lambda *a, **k: False)
    archiver.run(fake_job_db)
    fake_job_db.delete_logs_before.assert_not_called()


# ---------------------------------------------------------------------------
# _prune_old_files()
# ---------------------------------------------------------------------------


class TestPruneOldFiles:
  def test_prune_returns_zero_when_dir_missing(self, archiver):
    assert archiver._prune_old_files() == 0

  def test_prune_deletes_old_files(self, archiver, tmp_path):
    archive_dir = tmp_path / "archive" / "n1"
    archive_dir.mkdir(parents=True)
    old_file = archive_dir / "2025-01-01.jsonl.gz"
    new_file = archive_dir / "2026-05-01.jsonl.gz"
    old_file.write_bytes(b"old")
    new_file.write_bytes(b"new")
    # Make old_file 100 days old; new_file is now (within delete_after_days=90)
    old_time = time.time() - 100 * 86400
    os.utime(old_file, (old_time, old_time))
    deleted = archiver._prune_old_files()
    assert deleted == 1
    assert not old_file.exists()
    assert new_file.exists()

  def test_prune_skips_non_directory_entries(self, archiver, tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    # A regular file at the top level (not a node directory) is skipped.
    (archive_dir / "stray.txt").write_bytes(b"x")
    assert archiver._prune_old_files() == 0

  def test_prune_skips_non_gz_files(self, archiver, tmp_path):
    archive_dir = tmp_path / "archive" / "n1"
    archive_dir.mkdir(parents=True)
    (archive_dir / "notes.txt").write_bytes(b"old")
    old_time = time.time() - 200 * 86400
    os.utime(archive_dir / "notes.txt", (old_time, old_time))
    assert archiver._prune_old_files() == 0

  def test_prune_swallows_oserror(self, archiver, tmp_path, monkeypatch):
    archive_dir = tmp_path / "archive" / "n1"
    archive_dir.mkdir(parents=True)
    f = archive_dir / "old.jsonl.gz"
    f.write_bytes(b"x")
    old_time = time.time() - 200 * 86400
    os.utime(f, (old_time, old_time))

    real_unlink = os.unlink

    def picky_unlink(p):
      if str(p).endswith("old.jsonl.gz"):
        raise OSError("locked")
      return real_unlink(p)

    monkeypatch.setattr(os, "unlink", picky_unlink)
    # Should NOT raise; failure is logged + swallowed.
    out = archiver._prune_old_files()
    assert out == 0
