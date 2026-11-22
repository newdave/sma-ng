from unittest import mock

from resources.daemon.queueing import (
  DUPLICATE,
  NO_ROUTING_MATCH,
  QUEUED,
  REFUSED,
  SKIP_RECENTLY_COMPLETED,
  SKIP_RECYCLE_BIN,
  SKIP_SAME_EXTENSION,
  SKIPPED,
  dedupe_window_seconds,
  duplicate_outcome,
  normalise_job_path,
  queue_job,
  queue_media_path,
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


class TestQueueMediaPath:
  """Shared admission path for the scanner and the webhook/queue handlers."""

  @staticmethod
  def _pcm(**kw):
    pcm = mock.Mock()
    pcm.should_skip_same_extension.return_value = False
    pcm.is_recycle_bin_path.return_value = False
    pcm.strict_routing = False
    pcm.has_routing_match.return_value = True
    pcm.get_config_for_path.return_value = "/c.yml"
    pcm.get_profile_for_path.return_value = "hd"
    pcm.get_args_for_path.return_value = []
    pcm.job_dedupe_window_seconds = 60
    for k, v in kw.items():
      setattr(pcm, k, v)
    return pcm

  def test_queued(self):
    db = mock.MagicMock()
    db.add_job.return_value = 7
    with mock.patch("resources.daemon.queueing.metrics_prom.record_job_enqueued") as rec:
      out = queue_media_path(db, self._pcm(), "/m//a.mkv", extra_args=["-tmdb", "1"], request_source="sonarr")
    assert (out.status, out.path, out.job_id, out.config, out.profile) == (QUEUED, "/m/a.mkv", 7, "/c.yml", "hd")
    db.add_job.assert_called_once_with("/m/a.mkv", "/c.yml", ["-tmdb", "1", "--profile", "hd"], dedupe_window_seconds=60, max_retries=0, request_source="sonarr", request_profile="hd")
    rec.assert_called_once_with("sonarr", "hd")

  def test_skip_reason(self):
    db = mock.MagicMock()
    pcm = self._pcm()
    pcm.is_recycle_bin_path.return_value = True
    out = queue_media_path(db, pcm, "/m/a.mkv")
    assert (out.status, out.reason) == (SKIPPED, SKIP_RECYCLE_BIN)
    db.add_job.assert_not_called()

  def test_strict_routing_refuses_unless_config_override(self, tmp_path):
    db = mock.MagicMock()
    db.add_job.return_value = 1
    pcm = self._pcm(strict_routing=True)
    pcm.has_routing_match.return_value = False
    out = queue_media_path(db, pcm, "/m/a.mkv")
    assert (out.status, out.reason) == (REFUSED, NO_ROUTING_MATCH)
    db.add_job.assert_not_called()
    override = tmp_path / "o.yml"
    override.write_text("")
    out = queue_media_path(db, pcm, "/m/a.mkv", config_override=str(override))
    assert (out.status, out.config, out.profile) == (QUEUED, str(override), None)

  def test_default_args_prepended(self):
    db = mock.MagicMock()
    db.add_job.return_value = 1
    pcm = self._pcm()
    pcm.get_args_for_path.return_value = ["--nomove"]
    pcm.get_profile_for_path.return_value = None
    queue_media_path(db, pcm, "/m/a.mkv", extra_args=["-tmdb", "1"])
    assert db.add_job.call_args.args[2] == ["--nomove", "-tmdb", "1"]

  def test_explicit_profile_arg_not_duplicated(self):
    db = mock.MagicMock()
    db.add_job.return_value = 1
    queue_media_path(db, self._pcm(), "/m/a.mkv", extra_args=["--profile", "sd"])
    assert db.add_job.call_args.args[2] == ["--profile", "sd"]

  def test_profile_lookup_error_queues_without_profile(self):
    db = mock.MagicMock()
    db.add_job.return_value = 1
    pcm = self._pcm()
    pcm.get_profile_for_path.side_effect = RuntimeError("boom")
    assert queue_media_path(db, pcm, "/m/a.mkv").profile is None

  def test_recently_completed(self):
    db = mock.MagicMock()
    db.add_job.return_value = None
    db.find_active_job.return_value = None
    db.find_recent_completed_job.return_value = {"id": 3}
    out = queue_media_path(db, self._pcm(), "/m/a.mkv")
    assert (out.status, out.reason, out.job_id) == (SKIPPED, SKIP_RECENTLY_COMPLETED, 3)

  def test_duplicate_looks_up_the_normalised_path(self):
    # Behaviour change (SMANG-32): the active-job lookup used the raw path, so
    # an unnormalised submission reported a duplicate with no job id.
    db = mock.MagicMock()
    db.add_job.return_value = None
    db.find_active_job.side_effect = lambda p: {"id": 4} if p == "/m/a.mkv" else None
    out = queue_media_path(db, self._pcm(), "/m//a.mkv")
    assert (out.status, out.job_id, out.path) == (DUPLICATE, 4, "/m/a.mkv")

  def test_duplicate_outcome_normalises_active_lookup(self):
    db = mock.MagicMock()
    db.find_active_job.side_effect = lambda p: {"id": 4} if p == "/m/a.mkv" else None
    assert duplicate_outcome(db, mock.Mock(job_dedupe_window_seconds=0), "/m//a.mkv") == (DUPLICATE, 4)
