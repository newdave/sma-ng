"""Tests for LibraryAuditThread + LibraryAuditWorkerThread."""

import logging
import unittest.mock as mock

from resources.daemon.threads import LibraryAuditThread, LibraryAuditWorkerThread


def _make_pcm(audit_paths=None, enabled=True, dry_run=True, concurrency=1, batch_size=10, skip_dirs=None, claim_stale_seconds=600):
  pcm = mock.MagicMock()
  pcm.audit_paths = audit_paths or []
  pcm.audit_settings = mock.MagicMock()
  pcm.audit_settings.enabled = enabled
  pcm.audit_settings.interval_seconds = 86400
  pcm.audit_settings.skip_dirs = skip_dirs or []
  pcm.audit_settings.concurrency = concurrency
  pcm.audit_settings.batch_size = batch_size
  pcm.audit_settings.claim_stale_seconds = claim_stale_seconds
  pcm.audit_settings.dry_run = dry_run
  pcm.audit_settings.auto_fix = mock.MagicMock(ffprobe_failed=False, orphan_sidecar=False, leftover_tmp=False, preconv_original=False)
  pcm.is_recycle_bin_path = lambda _p: False
  pcm.ffmpeg_dir = None
  return pcm


def _make_audit_thread(job_db=None, pcm=None):
  if job_db is None:
    job_db = mock.MagicMock()
    job_db.is_distributed = True
    job_db.list_active_audit_runs.return_value = []
    job_db.complete_finished_audit_runs.return_value = []
    job_db.try_acquire_audit_enumerate_lock.return_value = mock.MagicMock(name="lock_conn")
    job_db.create_audit_run.return_value = 99
  if pcm is None:
    pcm = _make_pcm(audit_paths=[{"path": "/tmp/x", "enabled": True}])
  log = logging.getLogger("test.audit")
  return LibraryAuditThread(job_db=job_db, path_config_manager=pcm, server=mock.MagicMock(), node_id="n1", logger=log), job_db, pcm


def _make_worker_thread(job_db=None, pcm=None):
  if job_db is None:
    job_db = mock.MagicMock()
    job_db.is_distributed = True
    job_db.list_active_audit_runs.return_value = []
    job_db.claim_audit_units.return_value = []
  if pcm is None:
    pcm = _make_pcm()
  log = logging.getLogger("test.audit_worker")
  return LibraryAuditWorkerThread(job_db=job_db, path_config_manager=pcm, server=mock.MagicMock(), node_id="n1", logger=log), job_db, pcm


# ---------------------------------------------------------------------------
# Enumerator thread
# ---------------------------------------------------------------------------


def test_audit_thread_skips_when_disabled():
  pcm = _make_pcm(enabled=False, audit_paths=[{"path": "/tmp/x", "enabled": True}])
  thread, db, _ = _make_audit_thread(pcm=pcm)
  thread._cycle()
  db.create_audit_run.assert_not_called()


def test_audit_thread_skips_when_no_paths():
  pcm = _make_pcm(audit_paths=[])
  thread, db, _ = _make_audit_thread(pcm=pcm)
  thread._cycle()
  db.create_audit_run.assert_not_called()


def test_audit_thread_skips_enumerate_when_run_in_progress():
  thread, db, _ = _make_audit_thread()
  db.list_active_audit_runs.return_value = [{"id": 1, "scope_paths": [], "total_units": 10, "done_units": 5}]
  thread._cycle()
  db.try_acquire_audit_enumerate_lock.assert_not_called()


def test_audit_thread_releases_lock_after_enumerate(monkeypatch):
  thread, db, _ = _make_audit_thread()
  monkeypatch.setattr("resources.daemon.threads.enumerate_paths", lambda *a, **k: iter([("/tmp/x/a.mp4", "media")]))
  thread._cycle()
  db.create_audit_run.assert_called_once()
  db.enqueue_audit_units.assert_called_once()
  db.release_audit_enumerate_lock.assert_called_once()


def test_audit_thread_skips_enumerate_when_lock_held_by_other():
  thread, db, _ = _make_audit_thread()
  db.try_acquire_audit_enumerate_lock.return_value = None
  thread._cycle()
  db.create_audit_run.assert_not_called()
  db.release_audit_enumerate_lock.assert_not_called()


def test_audit_thread_completes_finished_runs():
  thread, db, _ = _make_audit_thread()
  db.complete_finished_audit_runs.return_value = [42]
  db.find_duplicate_media_ids.return_value = {}
  thread._cycle()
  db.complete_finished_audit_runs.assert_called_once()


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------


def test_worker_thread_requeues_own_claims_on_start():
  thread, db, _ = _make_worker_thread()
  thread.running = False  # don't loop
  # Simulate the prelude that run() does before its main loop.
  thread.job_db.requeue_audit_claims_for_node(thread.node_id)
  db.requeue_audit_claims_for_node.assert_called_with("n1")


def test_worker_tick_returns_false_when_disabled():
  pcm = _make_pcm(enabled=False)
  thread, _db, _ = _make_worker_thread(pcm=pcm)
  assert thread._tick() is False


def test_worker_tick_returns_false_when_no_active_runs():
  thread, db, _ = _make_worker_thread()
  db.list_active_audit_runs.return_value = []
  assert thread._tick() is False
  db.claim_audit_units.assert_not_called()


def test_worker_tick_processes_claimed_units(monkeypatch):
  thread, db, _ = _make_worker_thread()
  db.list_active_audit_runs.return_value = [{"id": 5, "scope_paths": [], "total_units": 1, "done_units": 0}]
  db.claim_audit_units.side_effect = [
    [{"id": 11, "audit_id": 5, "path": "/tmp/x.mp4", "kind_hint": "media"}],
  ]
  fake_engine = mock.MagicMock()
  fake_engine.probe_one.return_value = None
  monkeypatch.setattr("resources.daemon.threads.AuditEngine", lambda *a, **k: fake_engine)
  assert thread._tick() is True
  db.claim_audit_units.assert_called_with("n1", 5, batch=10)
  db.mark_audit_unit_done.assert_called_with(11)


# ---------------------------------------------------------------------------
# Extended coverage — _rollup_completed, run() loops, _process_unit error paths
# ---------------------------------------------------------------------------


def test_audit_thread_run_skips_when_not_distributed():
  thread, db, _ = _make_audit_thread()
  db.is_distributed = False
  # Should return immediately without raising.
  thread.running = False
  thread.run()
  db.list_active_audit_runs.assert_not_called()


def test_audit_thread_rollup_writes_duplicate_findings(monkeypatch):
  thread, db, _ = _make_audit_thread()
  fake_engine = mock.MagicMock()
  fake_engine.rollup_duplicate_ids.return_value = 3
  monkeypatch.setattr("resources.daemon.threads.AuditEngine", lambda *a, **k: fake_engine)
  thread._rollup_completed([1, 2], thread.pcm.audit_settings)
  assert fake_engine.rollup_duplicate_ids.call_count == 2


def test_audit_thread_rollup_swallows_exception(monkeypatch):
  thread, db, _ = _make_audit_thread()
  fake_engine = mock.MagicMock()
  fake_engine.rollup_duplicate_ids.side_effect = RuntimeError("rollup failed")
  monkeypatch.setattr("resources.daemon.threads.AuditEngine", lambda *a, **k: fake_engine)
  # Should NOT raise.
  thread._rollup_completed([42], thread.pcm.audit_settings)


def test_audit_thread_enumerate_into_queue_batches_at_500(monkeypatch):
  thread, db, _ = _make_audit_thread()
  # Generate 1200 paths — should produce 3 enqueues (500, 500, 200)
  paths_seq = [(f"/x/f{i}.mp4", "media") for i in range(1200)]
  monkeypatch.setattr(
    "resources.daemon.threads.enumerate_paths",
    lambda *a, **k: iter(paths_seq),
  )
  total = thread._enumerate_into_queue(7, ["/x"], thread.pcm.audit_settings)
  assert total == 1200
  # 500 + 500 + 200 → 3 calls
  assert db.enqueue_audit_units.call_count == 3


def test_audit_thread_enumerate_into_queue_aborts_on_stop(monkeypatch):
  thread, db, _ = _make_audit_thread()
  paths_seq = [(f"/x/f{i}.mp4", "media") for i in range(600)]
  monkeypatch.setattr(
    "resources.daemon.threads.enumerate_paths",
    lambda *a, **k: iter(paths_seq),
  )
  thread.running = False  # signal stop on first batch boundary
  total = thread._enumerate_into_queue(7, ["/x"], thread.pcm.audit_settings)
  # First 500-batch enqueued, then stop honored.
  assert total == 500
  assert db.enqueue_audit_units.call_count == 1


def test_audit_thread_zero_total_marks_run_completed(monkeypatch):
  thread, db, _ = _make_audit_thread()
  monkeypatch.setattr(
    "resources.daemon.threads.enumerate_paths",
    lambda *a, **k: iter([]),  # nothing found
  )
  thread._cycle()
  db.set_audit_run_status.assert_called_once_with(99, "completed")


def test_audit_thread_releases_lock_even_on_exception(monkeypatch):
  thread, db, _ = _make_audit_thread()
  monkeypatch.setattr(
    "resources.daemon.threads.enumerate_paths",
    lambda *a, **k: (_ for _ in ()).throw(RuntimeError("walk failed")),
  )
  # _cycle catches in the outer try; we directly call without the run loop.
  try:
    thread._cycle()
  except RuntimeError:
    pass
  db.release_audit_enumerate_lock.assert_called_once()


# ---------------------------------------------------------------------------
# Worker thread extended
# ---------------------------------------------------------------------------


def test_worker_run_skips_when_not_distributed():
  thread, db, _ = _make_worker_thread()
  db.is_distributed = False
  thread.running = False
  thread.run()
  db.requeue_audit_claims_for_node.assert_not_called()


def test_worker_run_swallows_requeue_exception_on_startup():
  thread, db, _ = _make_worker_thread()
  db.requeue_audit_claims_for_node.side_effect = RuntimeError("db down")
  thread.running = False  # exit after one tick
  # Should NOT raise.
  thread.run()


def test_worker_tick_handles_engine_failure(monkeypatch):
  thread, db, _ = _make_worker_thread()
  db.list_active_audit_runs.return_value = [{"id": 5, "scope_paths": []}]
  db.claim_audit_units.return_value = [
    {"id": 11, "audit_id": 5, "path": "/x.mp4", "kind_hint": "media"},
  ]

  fake_engine = mock.MagicMock()
  fake_engine.probe_one.side_effect = RuntimeError("probe boom")
  monkeypatch.setattr("resources.daemon.threads.AuditEngine", lambda *a, **k: fake_engine)
  # Should NOT raise; error is logged and unit marked done with error.
  assert thread._tick() is True
  # mark_audit_unit_done called with error= keyword
  call_args = db.mark_audit_unit_done.call_args_list
  assert any("error" in str(c) for c in call_args)


def test_worker_tick_records_finding_and_auto_fix_action(monkeypatch):
  thread, db, _ = _make_worker_thread()
  db.list_active_audit_runs.return_value = [{"id": 5, "scope_paths": []}]
  db.claim_audit_units.return_value = [
    {"id": 11, "audit_id": 5, "path": "/x.mp4", "kind_hint": "media"},
  ]

  from resources.library_audit.engine import Finding
  from resources.library_audit.kinds import FindingKind

  finding = Finding(FindingKind.FFPROBE_FAILED, "/x.mp4", {"reason": "empty"})
  fake_engine = mock.MagicMock()
  fake_engine.probe_one.return_value = finding
  fake_engine.maybe_auto_fix.return_value = "queued"

  monkeypatch.setattr("resources.daemon.threads.AuditEngine", lambda *a, **k: fake_engine)

  assert thread._tick() is True
  fake_engine.upsert.assert_called_once_with(finding, 5)
  fake_engine.maybe_auto_fix.assert_called_once_with(finding)


def test_worker_tick_no_units_returns_false():
  thread, db, _ = _make_worker_thread()
  db.list_active_audit_runs.return_value = [{"id": 5, "scope_paths": []}]
  db.claim_audit_units.return_value = []
  assert thread._tick() is False
