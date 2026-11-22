from unittest import mock

from resources.daemon.queueing import (
  DUPLICATE,
  SKIP_RECENTLY_COMPLETED,
  SKIP_RECYCLE_BIN,
  SKIP_SAME_EXTENSION,
  dedupe_window_seconds,
  duplicate_outcome,
  normalise_job_path,
  queue_job,
  skip_reason,
)


def test_normalise_job_path():
  assert normalise_job_path("/a//b/./c/../d.mkv") == "/a/b/d.mkv"
  assert normalise_job_path("/a/b/") == "/a/b"
  assert normalise_job_path("") == ""
  assert normalise_job_path(None) is None


def test_dedupe_window_coerces_non_numbers_to_zero():
  assert dedupe_window_seconds(mock.MagicMock()) == 0
  assert dedupe_window_seconds(mock.Mock(job_dedupe_window_seconds=-5)) == 0
  assert dedupe_window_seconds(mock.Mock(job_dedupe_window_seconds=60)) == 60


def test_skip_reason_order_and_none():
  pcm = mock.Mock()
  pcm.should_skip_same_extension.return_value = False
  pcm.is_recycle_bin_path.return_value = False
  assert skip_reason(pcm, "/a.mkv") is None
  pcm.is_recycle_bin_path.return_value = True
  assert skip_reason(pcm, "/a.mkv") == SKIP_RECYCLE_BIN
  pcm.should_skip_same_extension.return_value = True
  assert skip_reason(pcm, "/a.mkv") == SKIP_SAME_EXTENSION


def test_queue_job_normalises_and_passes_window():
  db = mock.Mock()
  db.add_job.return_value = 9
  pcm = mock.Mock(job_dedupe_window_seconds=100)
  assert queue_job(db, pcm, "/a//b.mkv", "/c.yml", ["x"], request_source="scan") == 9
  db.add_job.assert_called_once_with("/a/b.mkv", "/c.yml", ["x"], dedupe_window_seconds=100, request_source="scan")


class TestDuplicateOutcome:
  def test_active_job_is_duplicate(self):
    db = mock.MagicMock()
    db.find_active_job.return_value = {"id": 4}
    pcm = mock.Mock(job_dedupe_window_seconds=100)
    assert duplicate_outcome(db, pcm, "/a//b.mkv") == (DUPLICATE, 4)
    db.find_recent_completed_job.assert_not_called()

  def test_recent_completion_is_distinct_reason(self):
    db = mock.MagicMock()
    db.find_active_job.return_value = None
    db.find_recent_completed_job.return_value = {"id": 9}
    pcm = mock.Mock(job_dedupe_window_seconds=100)
    assert duplicate_outcome(db, pcm, "/a/b.mkv", kind="convert") == (SKIP_RECENTLY_COMPLETED, 9)
    db.find_recent_completed_job.assert_called_once_with("/a/b.mkv", "convert", 100)

  def test_window_disabled_never_reports_recent(self):
    db = mock.MagicMock()
    db.find_active_job.return_value = None
    assert duplicate_outcome(db, mock.Mock(job_dedupe_window_seconds=0), "/a/b.mkv") == (DUPLICATE, None)
    db.find_recent_completed_job.assert_not_called()
