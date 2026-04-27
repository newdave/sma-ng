"""Tests for resources/daemon/threads.py - RecycleBinCleaner and Scanner."""

import logging
import os
import time
import unittest.mock as mock

import pytest

from resources.daemon.threads import HeartbeatThread, RecycleBinCleanerThread, ScannerThread

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
# RecycleBinCleanerThread._delete_file
# ---------------------------------------------------------------------------


class TestRecycleBinDelete:
  def test_deletes_existing_file(self, tmp_path):
    f = tmp_path / "old.mkv"
    f.write_bytes(b"data")
    cleaner = _make_cleaner()
    result = cleaner._delete_file(str(f))
    assert result is True
    assert not f.exists()

  def test_returns_false_for_missing_file(self):
    cleaner = _make_cleaner()
    result = cleaner._delete_file("/nonexistent/file.mkv")
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

  def test_scans_mp4_files_when_allowed(self, tmp_path):
    media = tmp_path / "episode.mp4"
    media.write_bytes(b"")
    db = mock.MagicMock()
    db.filter_unscanned.return_value = [str(media)]
    db.add_job.return_value = 12
    path_config_manager = mock.MagicMock()
    path_config_manager.media_extensions = frozenset([".mp4", ".mkv"])
    path_config_manager.get_config_for_path.return_value = "/default.ini"
    scanner = _make_scanner([{"path": str(tmp_path)}], job_db=db, path_config_manager=path_config_manager)
    result = scanner._scan({"path": str(tmp_path)})
    assert result == 1
    db.filter_unscanned.assert_called_once()
    db.add_job.assert_called_once_with(str(media), "/default.ini", [])

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

  def test_does_not_rewrite_path_when_no_rewrite_config(self, tmp_path):
    media = tmp_path / "ep.mkv"
    media.write_bytes(b"")
    db = mock.MagicMock()
    db.filter_unscanned.return_value = [str(media)]
    db.add_job.return_value = 1
    scanner = _make_scanner([{"path": str(tmp_path)}], job_db=db)
    scanner._scan({"path": str(tmp_path)})
    submitted_path = db.add_job.call_args[0][0]
    assert submitted_path == str(media)

  def test_does_not_rewrite_when_path_does_not_start_with_rewrite_from(self, tmp_path):
    media = tmp_path / "ep.mkv"
    media.write_bytes(b"")
    db = mock.MagicMock()
    db.filter_unscanned.return_value = [str(media)]
    db.add_job.return_value = 1
    scanner = _make_scanner([{"path": str(tmp_path)}], job_db=db)
    entry = {
      "path": str(tmp_path),
      "rewrite_from": "/different/prefix",
      "rewrite_to": "/remote/media",
    }
    scanner._scan(entry)
    submitted_path = db.add_job.call_args[0][0]
    assert submitted_path == str(media)

  def test_add_job_returns_none_still_records_scanned(self, tmp_path):
    media = tmp_path / "ep.mkv"
    media.write_bytes(b"")
    db = mock.MagicMock()
    db.filter_unscanned.return_value = [str(media)]
    db.add_job.return_value = None
    scanner = _make_scanner([{"path": str(tmp_path)}], job_db=db)
    result = scanner._scan({"path": str(tmp_path)})
    assert result == 0
    db.record_scanned.assert_called_once()

  def test_scan_handles_permission_error_on_subdir(self, tmp_path):
    subdir = tmp_path / "restricted"
    subdir.mkdir()
    db = mock.MagicMock()
    db.filter_unscanned.return_value = []
    scanner = _make_scanner([{"path": str(tmp_path)}], job_db=db)
    with mock.patch("os.scandir", side_effect=[PermissionError("denied")]):
      result = scanner._scan({"path": str(tmp_path)})
    assert result == 0


# ---------------------------------------------------------------------------
# ScannerThread.run
# ---------------------------------------------------------------------------


class TestScannerThreadRun:
  def test_run_exits_immediately_with_no_scan_paths(self):
    scanner = _make_scanner([])
    scanner.run()  # should return without blocking

  def test_run_calls_scan_and_notifies_when_jobs_found(self, tmp_path):
    db = mock.MagicMock()
    server = mock.MagicMock()
    scanner = _make_scanner([{"path": str(tmp_path), "interval": 3600}], job_db=db)
    scanner.server = server

    call_count = [0]

    def fake_scan(entry):
      call_count[0] += 1
      scanner.running = False
      return 1

    scanner._scan = fake_scan
    with mock.patch.object(scanner._stop_event, "wait"):
      scanner.run()

    assert call_count[0] == 1
    server.notify_workers.assert_called_once()

  def test_run_does_not_notify_when_no_jobs_found(self, tmp_path):
    server = mock.MagicMock()
    scanner = _make_scanner([{"path": str(tmp_path), "interval": 3600}])
    scanner.server = server

    call_count = [0]

    def fake_scan(entry):
      call_count[0] += 1
      scanner.running = False
      return 0

    scanner._scan = fake_scan
    with mock.patch.object(scanner._stop_event, "wait"):
      scanner.run()

    server.notify_workers.assert_not_called()

  def test_run_catches_exception_in_scan(self, tmp_path):
    scanner = _make_scanner([{"path": str(tmp_path), "interval": 3600}])

    call_count = [0]

    def fake_scan(entry):
      call_count[0] += 1
      scanner.running = False
      raise RuntimeError("oops")

    scanner._scan = fake_scan
    with mock.patch.object(scanner._stop_event, "wait"):
      scanner.run()  # must not raise

    assert call_count[0] == 1

  def test_stop_sets_running_false_and_fires_event(self):
    scanner = _make_scanner([])
    scanner.stop()
    assert scanner.running is False
    assert scanner._stop_event.is_set()

  def test_run_respects_per_path_interval(self, tmp_path):
    scan_path = {"path": str(tmp_path), "interval": 9999}
    scanner = _make_scanner([scan_path])

    iterations = [0]

    def fake_scan(entry):
      iterations[0] += 1
      if iterations[0] >= 1:
        scanner.running = False
      return 0

    scanner._scan = fake_scan
    with mock.patch.object(scanner._stop_event, "wait"):
      scanner.run()

    # After first scan the next_run is set far in the future; _scan called only once
    assert iterations[0] == 1


# ---------------------------------------------------------------------------
# HeartbeatThread
# ---------------------------------------------------------------------------


def _make_heartbeat(job_db=None, server=None, interval=5, stale_seconds=60):
  if job_db is None:
    job_db = mock.MagicMock()
    job_db.is_distributed = True
    job_db.heartbeat.return_value = None
    job_db.poll_node_command.return_value = None
    job_db.recover_stale_nodes.return_value = []
  if server is None:
    server = mock.MagicMock()
  log = logging.getLogger("test.heartbeat")
  return HeartbeatThread(
    job_db=job_db,
    node_id="test-node",
    host="127.0.0.1",
    worker_count=2,
    server=server,
    interval=interval,
    stale_seconds=stale_seconds,
    logger=log,
    started_at=None,
  )


class TestHeartbeatThread:
  def test_run_exits_immediately_when_not_distributed(self):
    db = mock.MagicMock()
    db.is_distributed = False
    ht = _make_heartbeat(job_db=db)
    ht.run()  # should return without blocking

  def test_run_calls_heartbeat_each_iteration(self):
    db = mock.MagicMock()
    db.is_distributed = True
    db.heartbeat.return_value = None
    db.recover_stale_nodes.return_value = []
    ht = _make_heartbeat(job_db=db)

    call_count = [0]

    def fake_wait(timeout=None) -> bool:
      call_count[0] += 1
      if call_count[0] >= 2:
        ht.running = False
      return False

    ht._stop_event.wait = fake_wait
    ht.run()
    assert db.heartbeat.call_count >= 1

  def test_run_triggers_restart_on_restart_command(self):
    db = mock.MagicMock()
    db.is_distributed = True
    db.heartbeat.return_value = None
    db.poll_node_command.return_value = {"id": 1, "command": "restart"}
    db.recover_stale_nodes.return_value = []
    server = mock.MagicMock()
    ht = _make_heartbeat(job_db=db, server=server)

    with mock.patch("threading.Thread") as mock_thread:
      mock_thread.return_value = mock.MagicMock()
      ht.run()

    mock_thread.assert_called_once()
    call_kwargs = mock_thread.call_args[1]
    assert call_kwargs["target"] == server.graceful_restart

  def test_run_triggers_shutdown_on_shutdown_command(self):
    db = mock.MagicMock()
    db.is_distributed = True
    db.heartbeat.return_value = None
    db.poll_node_command.return_value = {"id": 2, "command": "shutdown"}
    db.recover_stale_nodes.return_value = []
    server = mock.MagicMock()
    ht = _make_heartbeat(job_db=db, server=server)

    with mock.patch("threading.Thread") as mock_thread:
      mock_thread.return_value = mock.MagicMock()
      ht.run()

    mock_thread.assert_called_once()
    call_kwargs = mock_thread.call_args[1]
    assert call_kwargs["target"] == server.shutdown

  def test_run_notifies_workers_when_stale_jobs_recovered(self):
    db = mock.MagicMock()
    db.is_distributed = True
    db.heartbeat.return_value = None
    db.recover_stale_nodes.return_value = [("stale-node", 3)]
    server = mock.MagicMock()
    ht = _make_heartbeat(job_db=db, server=server)

    call_count = [0]

    def fake_wait(timeout=None) -> bool:
      call_count[0] += 1
      ht.running = False
      return False

    ht._stop_event.wait = fake_wait
    ht.run()
    server.notify_workers.assert_called_once()

  def test_run_does_not_notify_workers_when_no_stale_jobs(self):
    db = mock.MagicMock()
    db.is_distributed = True
    db.heartbeat.return_value = None
    db.recover_stale_nodes.return_value = [("old-node", 0)]
    server = mock.MagicMock()
    ht = _make_heartbeat(job_db=db, server=server)

    call_count = [0]

    def fake_wait(timeout=None) -> bool:
      call_count[0] += 1
      ht.running = False
      return False

    ht._stop_event.wait = fake_wait
    ht.run()
    server.notify_workers.assert_not_called()

  def test_run_catches_heartbeat_exception(self):
    db = mock.MagicMock()
    db.is_distributed = True
    db.heartbeat.side_effect = RuntimeError("db gone")
    ht = _make_heartbeat(job_db=db)

    call_count = [0]

    def fake_wait(timeout=None) -> bool:
      call_count[0] += 1
      ht.running = False
      return False

    ht._stop_event.wait = fake_wait
    ht.run()  # must not raise

  def test_stop_sets_running_false_and_fires_event(self):
    ht = _make_heartbeat()
    ht.stop()
    assert ht.running is False
    assert ht._stop_event.is_set()

  def test_heartbeat_called_with_version_and_hwaccel(self):
    db = mock.MagicMock()
    db.is_distributed = True
    db.heartbeat.return_value = None
    db.poll_node_command.return_value = None
    db.recover_stale_nodes.return_value = []
    log = logging.getLogger("test.heartbeat")
    ht = HeartbeatThread(
      job_db=db,
      node_id="test-node",
      host="127.0.0.1",
      worker_count=2,
      server=mock.MagicMock(),
      interval=5,
      stale_seconds=60,
      logger=log,
      started_at=None,
      version="1.2.3",
      hwaccel="nvenc",
      log_ttl_days=7,
    )

    def fake_wait(timeout=None) -> bool:
      ht.running = False
      return False

    ht._stop_event.wait = fake_wait
    ht.run()
    db.heartbeat.assert_called_once_with("test-node", "127.0.0.1", 2, None, version="1.2.3", hwaccel="nvenc", node_name=None)

  def test_cleanup_old_logs_called_when_log_ttl_days_set(self):
    db = mock.MagicMock()
    db.is_distributed = True
    db.heartbeat.return_value = None
    db.poll_node_command.return_value = None
    db.recover_stale_nodes.return_value = []
    log = logging.getLogger("test.heartbeat")
    ht = HeartbeatThread(
      job_db=db,
      node_id="test-node",
      host="127.0.0.1",
      worker_count=2,
      server=mock.MagicMock(),
      interval=5,
      stale_seconds=60,
      logger=log,
      started_at=None,
      log_ttl_days=14,
    )

    def fake_wait(timeout=None) -> bool:
      ht.running = False
      return False

    ht._stop_event.wait = fake_wait
    ht.run()
    db.cleanup_old_logs.assert_called_once_with(14)

  def test_cleanup_old_logs_not_called_when_log_ttl_days_zero(self):
    db = mock.MagicMock()
    db.is_distributed = True
    db.heartbeat.return_value = None
    db.poll_node_command.return_value = None
    db.recover_stale_nodes.return_value = []
    log = logging.getLogger("test.heartbeat")
    ht = HeartbeatThread(
      job_db=db,
      node_id="test-node",
      host="127.0.0.1",
      worker_count=2,
      server=mock.MagicMock(),
      interval=5,
      stale_seconds=60,
      logger=log,
      started_at=None,
      log_ttl_days=0,
    )

    def fake_wait(timeout=None) -> bool:
      ht.running = False
      return False

    ht._stop_event.wait = fake_wait
    ht.run()
    db.cleanup_old_logs.assert_not_called()

  def test_poll_node_command_not_called_when_not_distributed(self):
    db = mock.MagicMock()
    db.is_distributed = False
    db.heartbeat.return_value = None
    db.recover_stale_nodes.return_value = []
    ht = _make_heartbeat(job_db=db)
    ht.run()  # exits immediately
    db.poll_node_command.assert_not_called()


# ---------------------------------------------------------------------------
# HeartbeatThread._execute_command
# ---------------------------------------------------------------------------


class TestExecuteCommand:
  def test_drain_sets_drain_mode(self):
    server = mock.MagicMock()
    db = mock.MagicMock()
    ht = _make_heartbeat(job_db=db, server=server)
    result = ht._execute_command({"id": 10, "command": "drain"})
    server.worker_pool.set_drain_mode.assert_called_once()
    db.set_node_status.assert_called_once_with(ht.node_id, "draining")
    db.ack_node_command.assert_called_once_with(10, "done")
    assert result is False

  def test_pause_sets_paused(self):
    server = mock.MagicMock()
    db = mock.MagicMock()
    ht = _make_heartbeat(job_db=db, server=server)
    result = ht._execute_command({"id": 11, "command": "pause"})
    server.worker_pool.set_paused.assert_called_once()
    db.set_node_status.assert_called_once_with(ht.node_id, "paused")
    db.ack_node_command.assert_called_once_with(11, "done")
    assert result is False

  def test_resume_clears_paused_and_drain_mode(self):
    server = mock.MagicMock()
    db = mock.MagicMock()
    ht = _make_heartbeat(job_db=db, server=server)
    result = ht._execute_command({"id": 12, "command": "resume"})
    server.worker_pool.clear_paused.assert_called_once()
    server.worker_pool.clear_drain_mode.assert_called_once()
    db.set_node_status.assert_called_once_with(ht.node_id, "online")
    db.ack_node_command.assert_called_once_with(12, "done")
    assert result is False

  def test_restart_acks_and_returns_true(self):
    server = mock.MagicMock()
    db = mock.MagicMock()
    ht = _make_heartbeat(job_db=db, server=server)
    with mock.patch("threading.Thread") as mock_thread:
      mock_thread.return_value = mock.MagicMock()
      result = ht._execute_command({"id": 13, "command": "restart"})
    db.set_node_status.assert_called_once_with(ht.node_id, "restarting")
    db.ack_node_command.assert_called_once_with(13, "done")
    assert result is True
    call_kwargs = mock_thread.call_args[1]
    assert call_kwargs["target"] == server.graceful_restart

  def test_shutdown_acks_and_returns_true(self):
    server = mock.MagicMock()
    db = mock.MagicMock()
    ht = _make_heartbeat(job_db=db, server=server)
    with mock.patch("threading.Thread") as mock_thread:
      mock_thread.return_value = mock.MagicMock()
      result = ht._execute_command({"id": 14, "command": "shutdown"})
    db.set_node_status.assert_called_once_with(ht.node_id, "offline")
    db.ack_node_command.assert_called_once_with(14, "done")
    assert result is True
    call_kwargs = mock_thread.call_args[1]
    assert call_kwargs["target"] == server.shutdown

  def test_unknown_command_logs_warning_and_returns_false(self):
    server = mock.MagicMock()
    db = mock.MagicMock()
    log = mock.MagicMock()
    ht = _make_heartbeat(job_db=db, server=server)
    ht.log = log
    result = ht._execute_command({"id": 15, "command": "explode"})
    log.warning.assert_called_once()
    db.ack_node_command.assert_called_once_with(15, "done")
    assert result is False

  def test_exception_acks_failed(self):
    server = mock.MagicMock()
    server.worker_pool.set_drain_mode.side_effect = RuntimeError("pool broken")
    db = mock.MagicMock()
    ht = _make_heartbeat(job_db=db, server=server)
    result = ht._execute_command({"id": 16, "command": "drain"})
    db.ack_node_command.assert_called_once_with(16, "failed")
    assert result is False

  def test_exception_ack_failure_is_swallowed(self):
    server = mock.MagicMock()
    server.worker_pool.set_drain_mode.side_effect = RuntimeError("broken")
    db = mock.MagicMock()
    db.ack_node_command.side_effect = RuntimeError("db gone too")
    ht = _make_heartbeat(job_db=db, server=server)
    result = ht._execute_command({"id": 17, "command": "drain"})  # must not raise
    assert result is False


# ---------------------------------------------------------------------------
# RecycleBinCleanerThread.run
# ---------------------------------------------------------------------------


class TestRecycleBinCleanerRun:
  def test_run_exits_when_both_thresholds_zero(self):
    cleaner = _make_cleaner(max_age_days=0, min_free_gb=0)
    cleaner.run()  # should return immediately

  def test_run_cleans_configured_directories(self, tmp_path):
    pcm = mock.MagicMock()
    pcm.get_all_configs.return_value = ["/cfg.ini"]
    pcm.get_recycle_bin.return_value = str(tmp_path)
    cleaner = _make_cleaner(path_config_manager=pcm, max_age_days=30, min_free_gb=0)

    call_count = [0]

    def fake_clean(directory):
      call_count[0] += 1
      cleaner.running = False
      return 0

    cleaner._clean_directory = fake_clean
    cleaner._stop_event.wait = mock.MagicMock()
    cleaner.run()
    assert call_count[0] == 1

  def test_run_catches_exception_in_clean_directory(self, tmp_path):
    pcm = mock.MagicMock()
    pcm.get_all_configs.return_value = ["/cfg.ini"]
    pcm.get_recycle_bin.return_value = str(tmp_path)
    cleaner = _make_cleaner(path_config_manager=pcm, max_age_days=30, min_free_gb=0)

    call_count = [0]

    def fake_clean(directory):
      call_count[0] += 1
      cleaner.running = False
      raise OSError("disk error")

    cleaner._clean_directory = fake_clean
    cleaner._stop_event.wait = mock.MagicMock()
    cleaner.run()  # must not raise
    assert call_count[0] == 1

  def test_run_skips_none_recycle_bins(self):
    pcm = mock.MagicMock()
    pcm.get_all_configs.return_value = ["/cfg.ini"]
    pcm.get_recycle_bin.return_value = None
    cleaner = _make_cleaner(path_config_manager=pcm, max_age_days=30, min_free_gb=0)
    cleaner.running = False
    cleaner._stop_event.wait = mock.MagicMock()
    clean_called = [False]

    def fake_clean(directory):
      clean_called[0] = True
      return 0

    cleaner._clean_directory = fake_clean
    cleaner.run()
    assert not clean_called[0]

  def test_run_deduplicates_recycle_bins(self, tmp_path):
    pcm = mock.MagicMock()
    pcm.get_all_configs.return_value = ["/cfg1.ini", "/cfg2.ini"]
    pcm.get_recycle_bin.return_value = str(tmp_path)  # same bin for both
    cleaner = _make_cleaner(path_config_manager=pcm, max_age_days=30, min_free_gb=0)

    clean_calls = [0]

    def fake_clean(directory):
      clean_calls[0] += 1
      cleaner.running = False
      return 0

    cleaner._clean_directory = fake_clean
    cleaner._stop_event.wait = mock.MagicMock()
    cleaner.run()
    # Same directory listed twice but should only be cleaned once (set dedup)
    assert clean_calls[0] == 1

  def test_free_gb_cannot_determine_skips_space_check(self, tmp_path):
    cleaner = _make_cleaner(max_age_days=0, min_free_gb=1.0)
    with mock.patch.object(cleaner, "_free_gb", return_value=None):
      deleted = cleaner._clean_directory(str(tmp_path))
    assert deleted == 0
