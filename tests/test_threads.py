"""Tests for resources/daemon/threads.py - RecycleBinCleaner and Scanner."""

import logging
import os
import time
import unittest.mock as mock

import pytest

from resources.daemon.threads import RecycleBinCleanerThread, ScannerThread

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cleaner(path_config_manager=None, max_age_days=30, min_free_gb=0):
    if path_config_manager is None:
        path_config_manager = mock.MagicMock()
        path_config_manager.get_all_configs.return_value = []
        path_config_manager.get_recycle_bin.return_value = None
    log = logging.getLogger("test.cleaner")
    return RecycleBinCleanerThread(
        path_config_manager=path_config_manager,
        max_age_days=max_age_days,
        min_free_gb=min_free_gb,
        logger=log,
    )


def _make_scanner(scan_paths, job_db=None, path_config_manager=None):
    if job_db is None:
        job_db = mock.MagicMock()
        job_db.filter_unscanned.return_value = []
    if path_config_manager is None:
        path_config_manager = mock.MagicMock()
        path_config_manager.media_extensions = frozenset([".mkv", ".avi", ".mov", ".ts", ".m4v", ".m2ts", ".wmv", ".flv", ".webm"])
        path_config_manager.get_config_for_path.return_value = "/default.ini"
    server = mock.MagicMock()
    log = logging.getLogger("test.scanner")
    return ScannerThread(
        scan_paths=scan_paths,
        job_db=job_db,
        server=server,
        path_config_manager=path_config_manager,
        logger=log,
    )


# ---------------------------------------------------------------------------
# RecycleBinCleanerThread._free_gb
# ---------------------------------------------------------------------------


class TestRecycleBinFreeGb:
    def test_returns_free_space_in_gib(self, tmp_path):
        cleaner = _make_cleaner()
        result = cleaner._free_gb(str(tmp_path))
        assert isinstance(result, float)
        assert result >= 0

    def test_returns_none_for_nonexistent_path(self):
        cleaner = _make_cleaner()
        result = cleaner._free_gb("/nonexistent/path/that/does/not/exist")
        assert result is None

    def test_statvfs_error_returns_none(self, tmp_path):
        cleaner = _make_cleaner()
        with mock.patch("os.statvfs", side_effect=OSError("no device")):
            result = cleaner._free_gb(str(tmp_path))
        assert result is None


# ---------------------------------------------------------------------------
# RecycleBinCleanerThread._list_media_files
# ---------------------------------------------------------------------------


class TestRecycleBinListMediaFiles:
    def test_returns_media_files_only(self, tmp_path):
        (tmp_path / "movie.mkv").write_bytes(b"")
        (tmp_path / "show.mp4").write_bytes(b"")  # .mp4 not in _MEDIA_EXTENSIONS
        (tmp_path / "info.nfo").write_bytes(b"")
        (tmp_path / "subtitle.srt").write_bytes(b"")
        cleaner = _make_cleaner()
        results = cleaner._list_media_files(str(tmp_path))
        names = [os.path.basename(p) for _, p in results]
        assert "movie.mkv" in names
        # Non-media files must not appear
        assert "info.nfo" not in names
        assert "subtitle.srt" not in names

    def test_returns_mtime_and_path_tuples(self, tmp_path):
        (tmp_path / "a.mkv").write_bytes(b"")
        cleaner = _make_cleaner()
        results = cleaner._list_media_files(str(tmp_path))
        assert len(results) == 1
        mtime, path = results[0]
        assert isinstance(mtime, float)
        assert path.endswith("a.mkv")

    def test_empty_directory(self, tmp_path):
        cleaner = _make_cleaner()
        results = cleaner._list_media_files(str(tmp_path))
        assert results == []

    def test_nonexistent_directory(self):
        cleaner = _make_cleaner()
        results = cleaner._list_media_files("/nonexistent/directory")
        assert results == []

    def test_subdirectories_ignored(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "nested.mkv").write_bytes(b"")
        cleaner = _make_cleaner()
        results = cleaner._list_media_files(str(tmp_path))
        assert results == []


# ---------------------------------------------------------------------------
# RecycleBinCleanerThread._delete
# ---------------------------------------------------------------------------


class TestRecycleBinDelete:
    def test_deletes_existing_file(self, tmp_path):
        f = tmp_path / "old.mkv"
        f.write_bytes(b"data")
        cleaner = _make_cleaner()
        result = cleaner._delete(str(f))
        assert result is True
        assert not f.exists()

    def test_returns_false_for_missing_file(self):
        cleaner = _make_cleaner()
        result = cleaner._delete("/nonexistent/file.mkv")
        assert result is False


# ---------------------------------------------------------------------------
# RecycleBinCleanerThread._clean_directory
# ---------------------------------------------------------------------------


class TestRecycleBinCleanDirectory:
    def test_age_eviction_removes_old_files(self, tmp_path):
        old = tmp_path / "old.mkv"
        new = tmp_path / "new.mkv"
        old.write_bytes(b"old data")
        new.write_bytes(b"new data")
        # Set old file mtime to 60 days ago
        old_mtime = time.time() - 60 * 86400
        os.utime(str(old), (old_mtime, old_mtime))

        cleaner = _make_cleaner(max_age_days=30, min_free_gb=0)
        deleted = cleaner._clean_directory(str(tmp_path))

        assert deleted == 1
        assert not old.exists()
        assert new.exists()

    def test_age_eviction_keeps_recent_files(self, tmp_path):
        recent = tmp_path / "recent.mkv"
        recent.write_bytes(b"data")
        # Set mtime to 5 days ago (within 30-day threshold)
        recent_mtime = time.time() - 5 * 86400
        os.utime(str(recent), (recent_mtime, recent_mtime))

        cleaner = _make_cleaner(max_age_days=30, min_free_gb=0)
        deleted = cleaner._clean_directory(str(tmp_path))

        assert deleted == 0
        assert recent.exists()

    def test_space_pressure_eviction_when_low_free_space(self, tmp_path):
        old = tmp_path / "old.mkv"
        old.write_bytes(b"x" * 1024)
        cleaner = _make_cleaner(max_age_days=0, min_free_gb=0)

        # Simulate: free_gb returns 0.1 (below threshold) then 999 after delete
        with mock.patch.object(cleaner, "_free_gb", side_effect=[0.1, 999.0]):
            # min_free_gb > 0 to trigger space check
            cleaner.min_free_gb = 1.0
            deleted = cleaner._clean_directory(str(tmp_path))

        assert deleted == 1

    def test_space_pressure_stops_when_enough_free(self, tmp_path):
        for i in range(3):
            f = tmp_path / f"file{i}.mkv"
            f.write_bytes(b"data")
            # Make them all old enough to be candidates
            mtime = time.time() - 1
            os.utime(str(f), (mtime, mtime))

        cleaner = _make_cleaner(max_age_days=0, min_free_gb=1.0)
        # free_gb always above threshold — nothing should be deleted
        with mock.patch.object(cleaner, "_free_gb", return_value=100.0):
            deleted = cleaner._clean_directory(str(tmp_path))

        assert deleted == 0

    def test_nonexistent_directory_returns_zero(self):
        cleaner = _make_cleaner()
        deleted = cleaner._clean_directory("/nonexistent/recycle")
        assert deleted == 0

    def test_zero_age_and_zero_free_gb_deletes_nothing(self, tmp_path):
        (tmp_path / "file.mkv").write_bytes(b"data")
        cleaner = _make_cleaner(max_age_days=0, min_free_gb=0)
        deleted = cleaner._clean_directory(str(tmp_path))
        assert deleted == 0

    def test_oldest_files_deleted_first_under_space_pressure(self, tmp_path):
        now = time.time()
        older = tmp_path / "older.mkv"
        newer = tmp_path / "newer.mkv"
        older.write_bytes(b"data")
        newer.write_bytes(b"data")
        os.utime(str(older), (now - 200, now - 200))
        os.utime(str(newer), (now - 100, now - 100))

        cleaner = _make_cleaner(max_age_days=0, min_free_gb=1.0)
        # First call: low free space; second call (after deleting older): enough
        with mock.patch.object(cleaner, "_free_gb", side_effect=[0.5, 999.0]):
            deleted = cleaner._clean_directory(str(tmp_path))

        assert deleted == 1
        assert not older.exists()
        assert newer.exists()


# ---------------------------------------------------------------------------
# ScannerThread._scan
# ---------------------------------------------------------------------------


class TestScannerThreadScan:
    def test_returns_zero_for_nonexistent_path(self):
        scanner = _make_scanner([{"path": "/nonexistent/dir"}])
        result = scanner._scan({"path": "/nonexistent/dir"})
        assert result == 0

    def test_returns_zero_for_empty_directory(self, tmp_path):
        db = mock.MagicMock()
        db.filter_unscanned.return_value = []
        scanner = _make_scanner([{"path": str(tmp_path)}], job_db=db)
        result = scanner._scan({"path": str(tmp_path)})
        assert result == 0

    def test_skips_disabled_entry(self, tmp_path):
        (tmp_path / "movie.mkv").write_bytes(b"")
        db = mock.MagicMock()
        scanner = _make_scanner([{"path": str(tmp_path)}], job_db=db)
        result = scanner._scan({"path": str(tmp_path), "enabled": False})
        assert result == 0
        db.filter_unscanned.assert_not_called()

    def test_queues_new_unscanned_files(self, tmp_path):
        media = tmp_path / "show.mkv"
        media.write_bytes(b"")
        db = mock.MagicMock()
        db.filter_unscanned.return_value = [str(media)]
        db.add_job.return_value = 42
        scanner = _make_scanner([{"path": str(tmp_path)}], job_db=db)
        result = scanner._scan({"path": str(tmp_path)})
        assert result == 1
        db.add_job.assert_called_once()
        db.record_scanned.assert_called_once()

    def test_returns_zero_when_all_files_already_scanned(self, tmp_path):
        (tmp_path / "show.mkv").write_bytes(b"")
        db = mock.MagicMock()
        db.filter_unscanned.return_value = []
        scanner = _make_scanner([{"path": str(tmp_path)}], job_db=db)
        result = scanner._scan({"path": str(tmp_path)})
        assert result == 0
        db.add_job.assert_not_called()

    def test_skips_mp4_files(self, tmp_path):
        (tmp_path / "already_converted.mp4").write_bytes(b"")
        db = mock.MagicMock()
        db.filter_unscanned.return_value = []
        scanner = _make_scanner([{"path": str(tmp_path)}], job_db=db)
        result = scanner._scan({"path": str(tmp_path)})
        assert result == 0
        db.filter_unscanned.assert_not_called()

    def test_applies_path_rewrite(self, tmp_path):
        media = tmp_path / "show.mkv"
        media.write_bytes(b"")
        db = mock.MagicMock()
        db.filter_unscanned.return_value = [str(media)]
        db.add_job.return_value = 1
        scanner = _make_scanner([{"path": str(tmp_path)}], job_db=db)
        entry = {
            "path": str(tmp_path),
            "rewrite_from": str(tmp_path),
            "rewrite_to": "/remote/media",
        }
        result = scanner._scan(entry)
        assert result == 1
        # The submitted path should start with the rewritten prefix
        submitted_path = db.add_job.call_args[0][0]
        assert submitted_path.startswith("/remote/media")

    def test_records_all_candidates_as_scanned(self, tmp_path):
        for i in range(3):
            (tmp_path / f"ep{i}.mkv").write_bytes(b"")
        db = mock.MagicMock()
        # Two already scanned, one new
        all_paths = [str(tmp_path / f"ep{i}.mkv") for i in range(3)]
        db.filter_unscanned.return_value = [all_paths[0]]
        db.add_job.return_value = 99
        scanner = _make_scanner([{"path": str(tmp_path)}], job_db=db)
        scanner._scan({"path": str(tmp_path)})
        # record_scanned is called with the unscanned subset returned by filter_unscanned
        db.record_scanned.assert_called_once()
        recorded = db.record_scanned.call_args[0][0]
        assert len(recorded) == 1
        assert all_paths[0] in recorded

    def test_discovers_files_in_subdirectories(self, tmp_path):
        subdir = tmp_path / "Season 1"
        subdir.mkdir()
        (subdir / "s01e01.mkv").write_bytes(b"")
        db = mock.MagicMock()
        db.filter_unscanned.return_value = [str(subdir / "s01e01.mkv")]
        db.add_job.return_value = 5
        scanner = _make_scanner([{"path": str(tmp_path)}], job_db=db)
        result = scanner._scan({"path": str(tmp_path)})
        assert result == 1

    def test_notifies_server_when_jobs_queued(self, tmp_path):
        # Note: _scan itself does NOT call server.notify_workers(); that's done by the
        # ScannerThread.run() loop. Verify add_job is called and non-zero is returned
        # so run() knows to notify.
        (tmp_path / "movie.mkv").write_bytes(b"")
        db = mock.MagicMock()
        db.filter_unscanned.return_value = [str(tmp_path / "movie.mkv")]
        db.add_job.return_value = 10
        scanner = _make_scanner([{"path": str(tmp_path)}], job_db=db)
        result = scanner._scan({"path": str(tmp_path)})
        assert result > 0
