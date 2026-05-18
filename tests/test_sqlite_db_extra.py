"""Extra SQLite DB coverage tests for resources.daemon.db.SQLiteJobDatabase.

These cover the broad set of helper methods that were previously untested:
heartbeat/cluster_nodes, scanned_files, get_jobs filters, get_running_jobs,
cleanup_old_jobs, pending_count*, requeue_failed_jobs, delete_*, priority,
URL parsing edge cases, and the cluster/audit stub returns.
"""

import datetime as _dt

import pytest

from resources.daemon import db as db_mod
from resources.daemon.constants import (
  STATUS_COMPLETED,
  STATUS_FAILED,
  STATUS_PENDING,
  STATUS_RUNNING,
  set_node_id_cache,
)
from resources.daemon.db import SQLiteJobDatabase, _sqlite_path_from_url


def _db(tmp_path, name="sma-ng.db"):
  return SQLiteJobDatabase(f"sqlite:///{tmp_path / name}")


class TestURLParser:
  def test_wrong_scheme_raises(self):
    with pytest.raises(ValueError, match="sqlite"):
      _sqlite_path_from_url("postgresql:///x")

  def test_non_local_host_raises(self):
    with pytest.raises(ValueError, match="local"):
      _sqlite_path_from_url("sqlite://remote/x.db")

  def test_empty_path_raises(self):
    with pytest.raises(ValueError, match="file path"):
      _sqlite_path_from_url("sqlite://")

  def test_localhost_accepted(self):
    assert _sqlite_path_from_url("sqlite://localhost/tmp/x.db") == "/tmp/x.db"


class TestSQLiteHelpers:
  def test_find_active_job_returns_dict_then_none(self, tmp_path):
    db = _db(tmp_path)
    db.add_job("/m/a.mkv", "/c.yml")
    row = db.find_active_job("/m/a.mkv")
    assert row and row["path"] == "/m/a.mkv"
    assert db.find_active_job("/m/missing.mkv") is None
    db.close()

  def test_claim_with_exclude_configs_and_none_when_empty(self, tmp_path):
    db = _db(tmp_path)
    db.add_job("/m/a.mkv", "/c1.yml")
    db.add_job("/m/b.mkv", "/c2.yml")
    # Excluding c1 only leaves c2
    job = db.claim_next_job(worker_id=1, node_id="n", exclude_configs=["/c1.yml"])
    assert job["config"] == "/c2.yml"
    # Exclude both -> no claimable job
    db.add_job("/m/c.mkv", "/c1.yml")
    job2 = db.claim_next_job(worker_id=1, node_id="n", exclude_configs=["/c1.yml", "/c2.yml"])
    assert job2 is None
    # And empty queue -> None
    db2 = _db(tmp_path, "empty.db")
    assert db2.claim_next_job(worker_id=1, node_id="n") is None
    db.close()
    db2.close()

  def test_pending_helpers_and_get_next_pending_job(self, tmp_path):
    db = _db(tmp_path)
    assert db.get_pending_jobs() == []
    assert db.get_next_pending_job() is None
    assert db.pending_count() == 0
    db.add_job("/m/a.mkv", "/c.yml")
    db.add_job("/m/b.mkv", "/c2.yml")
    assert len(db.get_pending_jobs()) == 2
    nxt = db.get_next_pending_job()
    assert nxt["path"] == "/m/a.mkv"
    assert db.pending_count() == 2
    assert db.pending_count_for_config("/c.yml") == 1
    db.close()

  def test_start_job_marks_running(self, tmp_path):
    db = _db(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml")
    db.start_job(jid, worker_id=42)
    job = db.get_job(jid)
    assert job["status"] == STATUS_RUNNING
    assert job["worker_id"] == 42
    db.close()

  def test_fail_job_retry_branch_then_final_failure(self, tmp_path):
    db = _db(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml", max_retries=2)
    db.claim_next_job(worker_id=1, node_id="n")
    db.fail_job(jid, "transient")
    # Should be pending again with retry_count incremented
    row = db.get_job(jid)
    assert row["status"] == STATUS_PENDING
    assert row["retry_count"] == 1
    assert row["next_attempt_at"] is not None
    # Second failure -> still pending (retry 2)
    db.start_job(jid, 1)
    db.fail_job(jid, "transient2")
    assert db.get_job(jid)["retry_count"] == 2
    # Third failure -> exhausted, FAILED
    db.start_job(jid, 1)
    db.fail_job(jid, "fatal")
    assert db.get_job(jid)["status"] == STATUS_FAILED
    db.close()

  def test_get_jobs_filters(self, tmp_path):
    db = _db(tmp_path)
    db.add_job("/m/Aaa.mkv", "/c.yml")
    db.add_job("/m/Bbb.mkv", "/c2.yml")
    db.claim_next_job(worker_id=1, node_id="n")
    # status filter
    running = db.get_jobs(status=STATUS_RUNNING)
    assert len(running) == 1
    # config filter
    by_cfg = db.get_jobs(config="/c2.yml")
    assert len(by_cfg) == 1 and by_cfg[0]["path"] == "/m/Bbb.mkv"
    # path substring filter (case-insensitive)
    by_path = db.get_jobs(path="bbb")
    assert len(by_path) == 1 and by_path[0]["path"] == "/m/Bbb.mkv"
    # limit + offset
    paged = db.get_jobs(limit=1, offset=1)
    assert len(paged) == 1
    db.close()

  def test_running_jobs_listed(self, tmp_path):
    db = _db(tmp_path)
    db.add_job("/m/a.mkv", "/c.yml")
    db.claim_next_job(worker_id=1, node_id="n")
    running = db.get_running_jobs()
    assert len(running) == 1
    db.close()

  def test_cleanup_old_jobs(self, tmp_path):
    db = _db(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml")
    db.claim_next_job(worker_id=1, node_id="n")
    db.complete_job(jid)
    # Manually backdate completed_at
    old = (_dt.datetime.now(_dt.UTC) - _dt.timedelta(days=60)).replace(microsecond=0).isoformat()
    with db._conn() as conn:
      conn.execute("UPDATE jobs SET completed_at = ? WHERE id = ?", (old, jid))
    assert db.cleanup_old_jobs(days=30) == 1
    assert db.get_job(jid) is None
    # Nothing left to clean
    assert db.cleanup_old_jobs(days=30) == 0
    db.close()

  def test_get_metrics_unavailable_under_sqlite(self, tmp_path):
    db = _db(tmp_path)
    m = db.get_metrics("7d")
    assert m["available"] is False
    assert m["window"] == "7d"
    db.close()

  def test_get_stats_counts_by_status(self, tmp_path):
    db = _db(tmp_path)
    db.add_job("/m/a.mkv", "/c.yml")
    db.add_job("/m/b.mkv", "/c.yml")
    stats = db.get_stats()
    assert stats["total"] == 2
    assert stats.get(STATUS_PENDING) == 2
    db.close()

  def test_requeue_failed_jobs_global_and_by_config(self, tmp_path):
    db = _db(tmp_path)
    j1 = db.add_job("/m/a.mkv", "/c1.yml")
    db.claim_next_job(worker_id=1, node_id="n")
    db.fail_job(j1, "boom")
    j2 = db.add_job("/m/b.mkv", "/c2.yml")
    db.claim_next_job(worker_id=1, node_id="n")
    db.fail_job(j2, "boom")
    # config filter requeues only one
    assert db.requeue_failed_jobs(config="/c1.yml") == 1
    assert db.get_job(j1)["status"] == STATUS_PENDING
    assert db.get_job(j2)["status"] == STATUS_FAILED
    # global requeue grabs the rest
    assert db.requeue_failed_jobs() == 1
    assert db.get_job(j2)["status"] == STATUS_PENDING
    # Nothing to do
    assert db.requeue_failed_jobs() == 0
    db.close()

  def test_priority_and_cancellation(self, tmp_path):
    db = _db(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml")
    assert db.set_job_priority(jid, 5) is True
    assert db.get_job(jid)["priority"] == 5
    # Priority only updates pending rows: claim and try again
    db.claim_next_job(worker_id=1, node_id="n")
    assert db.set_job_priority(jid, 9) is False
    # Cancel running job
    assert db.cancel_job(jid) is True
    assert db.get_job(jid)["status"] == "cancelled"
    # Already cancelled -> no-op
    assert db.cancel_job(jid) is False
    db.close()

  def test_delete_jobs_helpers(self, tmp_path):
    db = _db(tmp_path)
    j1 = db.add_job("/m/a.mkv", "/c.yml")
    j2 = db.add_job("/m/b.mkv", "/c.yml")
    # delete_jobs with empty input
    assert db.delete_jobs([]) == []
    # delete_jobs with mix of real and bogus ids
    deleted = db.delete_jobs([j1, 99999])
    assert deleted == [j1]
    assert db.get_job(j1) is None
    # delete_all_jobs
    n = db.delete_all_jobs()
    assert n == 1
    assert db.get_job(j2) is None
    db.close()

  def test_delete_failed_jobs_and_offline_nodes(self, tmp_path):
    db = _db(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml")
    db.claim_next_job(worker_id=1, node_id="n")
    db.fail_job(jid, "boom")
    assert db.delete_failed_jobs() == 1
    # No-op
    assert db.delete_failed_jobs() == 0

    # Register two nodes via heartbeat then offline one
    db.heartbeat("n1", "h1", 1, _dt.datetime.now(_dt.UTC))
    db.heartbeat("n2", "h2", 1, _dt.datetime.now(_dt.UTC))
    assert db.set_node_status("n2", "offline") is True
    deleted = db.delete_offline_nodes()
    assert deleted == 1
    # No more offline rows -> deletes 0
    assert db.delete_offline_nodes() == 0
    db.close()

  def test_heartbeat_and_cluster_nodes_view(self, tmp_path):
    db = _db(tmp_path)
    started = _dt.datetime.now(_dt.UTC).replace(microsecond=0)
    db.heartbeat("n1", "host-1", 2, started, version="1.2.3", hwaccel="qsv", node_name="alpha")
    # Upsert path: second call updates host/workers
    db.heartbeat("n1", "host-1b", 3, started.isoformat(), version="1.2.4", hwaccel="vaapi", node_name="alpha")
    db.add_job("/m/a.mkv", "/c.yml")
    db.claim_next_job(worker_id=1, node_id="n1")
    nodes = db.get_cluster_nodes()
    by_id = {n["node_id"]: n for n in nodes}
    assert "n1" in by_id
    n1 = by_id["n1"]
    assert n1["workers"] == 3
    assert n1["version"] == "1.2.4"
    assert n1["approval_status"] == "approved"
    # active_jobs surfaced for the running node
    assert any(j["path"] == "/m/a.mkv" for j in n1["active_jobs"])
    # Delete node
    assert db.delete_node("n1") is True
    db.close()

  def test_mark_node_offline_requeues_jobs(self, tmp_path):
    set_node_id_cache("nodeA")
    try:
      db = _db(tmp_path)
      jid = db.add_job("/m/a.mkv", "/c.yml")
      db.claim_next_job(worker_id=1, node_id="nodeB")
      db.heartbeat("nodeB", "h", 1, _dt.datetime.now(_dt.UTC))
      db.mark_node_offline("nodeB", remove=False)
      # Job back to pending, node set offline
      assert db.get_job(jid)["status"] == STATUS_PENDING
      with db._conn() as conn:
        row = conn.execute("SELECT status FROM cluster_nodes WHERE node_id = ?", ("nodeB",)).fetchone()
      assert row["status"] == "offline"
      # remove=True deletes row
      db.mark_node_offline("nodeB", remove=True)
      with db._conn() as conn:
        row = conn.execute("SELECT * FROM cluster_nodes WHERE node_id = ?", ("nodeB",)).fetchone()
      assert row is None
      db.close()
    finally:
      set_node_id_cache("")

  def test_cluster_stub_returns(self, tmp_path):
    """SQLite mode returns inert defaults for distributed features."""
    db = _db(tmp_path)
    assert db.is_node_approved("any") is True
    assert db.set_node_approval("any") is None
    assert db.recover_stale_nodes() == []
    assert db.send_node_command("n", "cmd") == []
    assert db.poll_node_command("n") is None
    assert db.ack_node_command(1, "done") is None
    assert db.insert_logs([{"a": 1}]) is None
    assert db.cleanup_old_logs(7) == 0
    assert db.get_logs() == []
    assert db.get_cluster_config() is None
    assert db.set_cluster_config({"x": 1}) is None
    assert db.expire_offline_nodes(7) == []
    assert db.cleanup_orphaned_commands(["n"]) == 0
    assert db.get_logs_for_archival(7) == []
    assert db.delete_logs_before(7) == 0
    assert db.list_audit_runs() == []
    assert db.get_audit_run(1) is None
    assert db.get_findings() == []
    assert db.get_finding(1) is None
    assert db.set_finding_status(1, "ack") is None
    db.close()

  def test_filter_unscanned_empty_input(self, tmp_path):
    db = _db(tmp_path)
    assert db.filter_unscanned([]) == []
    # record_scanned no-op with empty
    db.record_scanned([])
    db.close()

  def test_requeue_job_only_failed(self, tmp_path):
    db = _db(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml")
    # Pending -> requeue_job returns False (only requeues FAILED)
    assert db.requeue_job(jid) is False
    db.close()
