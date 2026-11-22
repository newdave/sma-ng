"""A claim token stops a stale run of a requeued job from finalising a same-node reclaim (SMANG-9)."""

import threading
from unittest import mock

from resources.daemon.constants import STATUS_COMPLETED, STATUS_PENDING, STATUS_RUNNING
from resources.daemon.db import SQLiteJobDatabase
from resources.daemon.worker import ABORT_REASSIGNED

from .test_db_job_kinds import _pg, _pg_db, _sql
from .test_interrupted_requeue import _make_worker


def _sqlite(tmp_path):
  return SQLiteJobDatabase("sqlite:///%s" % (tmp_path / "x.db"))


class TestSQLiteClaimToken:
  def test_claim_sets_a_fresh_token_each_time(self, tmp_path):
    db = _sqlite(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml")
    first = db.claim_next_job(worker_id=1, node_id="n1")["claim_token"]
    assert db.requeue_interrupted_job(jid, "r", node_id="n1") is True
    second = db.claim_next_job(worker_id=2, node_id="n1")["claim_token"]
    assert first and second and first != second

  def test_stale_run_cannot_finalise_same_node_reclaim(self, tmp_path):
    db = _sqlite(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml", max_retries=0)
    stale = db.claim_next_job(worker_id=1, node_id="n1")["claim_token"]
    assert db.requeue_interrupted_job(jid, "r", node_id="n1", claim_token=stale) is True
    current = db.claim_next_job(worker_id=2, node_id="n1")["claim_token"]
    # Same node owns the row again, so only the token tells the runs apart.
    assert db.complete_job(jid, node_id="n1", claim_token=stale) is False
    assert db.fail_job(jid, "boom", node_id="n1", claim_token=stale) is False
    assert db.requeue_interrupted_job(jid, "r", node_id="n1", claim_token=stale) is False
    assert db.get_job(jid)["status"] == STATUS_RUNNING
    assert db.complete_job(jid, node_id="n1", claim_token=current) is True
    assert db.get_job(jid)["status"] == STATUS_COMPLETED

  def test_fail_job_with_current_token_still_works(self, tmp_path):
    db = _sqlite(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml", max_retries=0)
    token = db.claim_next_job(worker_id=1, node_id="n1")["claim_token"]
    assert db.fail_job(jid, "boom", node_id="n1", claim_token=token) is True

  def test_two_claimers_on_one_requeued_job_one_wins(self, tmp_path):
    path = "sqlite:///%s" % (tmp_path / "x.db")
    seed = SQLiteJobDatabase(path)
    jid = seed.add_job("/m/a.mkv", "/c.yml")
    seed.claim_next_job(worker_id=1, node_id="n1")
    assert seed.requeue_interrupted_job(jid, "r", node_id="n1") is True
    assert seed.get_job(jid)["status"] == STATUS_PENDING

    barrier = threading.Barrier(2)
    results = []

    def claimer(worker_id):
      db = SQLiteJobDatabase(path)  # own connection, as a second process would have
      barrier.wait()
      results.append(db.claim_next_job(worker_id=worker_id, node_id="n1"))

    threads = [threading.Thread(target=claimer, args=(w,)) for w in (2, 3)]
    for t in threads:
      t.start()
    for t in threads:
      t.join()
    winners = [r for r in results if r]
    assert len(results) == 2 and len(winners) == 1
    row = seed.get_job(jid)
    assert row["status"] == STATUS_RUNNING and row["claim_token"] == winners[0]["claim_token"]

  def test_claim_update_is_conditional_on_pending(self, tmp_path):
    db = _sqlite(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml")
    db.claim_next_job(worker_id=1, node_id="n1")
    seen = []
    real = db._conn_obj

    class Spy:
      def __getattr__(self, name):
        return getattr(real, name)

      def execute(self, sql, params=()):
        seen.append(sql)
        if sql.lstrip().startswith("SELECT id FROM jobs"):
          # Lose the race: the SELECT still reports the row that was just taken.
          return real.execute("SELECT %d AS id" % jid)
        return real.execute(sql, params)

    db._conn_obj = Spy()
    assert db.claim_next_job(worker_id=2, node_id="n2") is None
    assert any("AND status = ?" in s for s in seen)
    assert real.execute("SELECT node_id FROM jobs WHERE id = ?", (jid,)).fetchone()["node_id"] == "n1"


class TestPostgresClaimToken:
  def _claim(self, rowcount):
    cur, modules = _pg(fetchone={"id": 7, "my_running": 0, "idle_peer": False})
    cur.rowcount = rowcount
    db = _pg_db(cur, modules)
    with mock.patch.object(db, "get_job", return_value={"id": 7}) as get_job:
      result = db.claim_next_job(worker_id=1, node_id="n1")
    return cur, result, get_job

  def test_claim_is_skip_locked_and_conditional(self):
    cur, result, get_job = self._claim(rowcount=1)
    assert any("FOR UPDATE SKIP LOCKED" in s for s in _sql(cur))
    update = next(c for c in cur.execute.call_args_list if c.args and "UPDATE jobs" in c.args[0])
    assert "claim_token = %s" in update.args[0] and "AND status = %s" in update.args[0]
    assert update.args[1][-1] == STATUS_PENDING
    token = update.args[1][3]
    assert isinstance(token, str) and len(token) == 32
    assert result == {"id": 7}
    get_job.assert_called_once_with(7)

  def test_lost_claim_returns_none(self):
    _cur, result, get_job = self._claim(rowcount=0)
    assert result is None
    get_job.assert_not_called()

  def test_finalisers_match_on_claim_token(self):
    for call in (
      lambda db: db.complete_job(7, node_id="n1", claim_token="tok"),
      lambda db: db.requeue_interrupted_job(7, "r", node_id="n1", claim_token="tok"),
      lambda db: db.fail_job(7, "boom", node_id="n1", claim_token="tok"),
    ):
      cur, modules = _pg(fetchone=None)
      call(_pg_db(cur, modules))
      stmt = next(c for c in cur.execute.call_args_list if c.args and "claim_token = %s" in c.args[0])
      assert "tok" in stmt.args[1] and "n1" in stmt.args[1]


class TestWorkerClaimToken:
  def test_watchdog_stops_run_when_same_node_reclaimed_with_new_token(self):
    db = mock.MagicMock()
    worker = _make_worker(db)
    worker._claim_token = "old"
    db.get_job.return_value = {"status": "running", "node_id": worker.node_id, "claim_token": "new"}
    assert worker._check_job_ownership(1) == ABORT_REASSIGNED

  def test_watchdog_keeps_run_with_matching_token(self):
    db = mock.MagicMock()
    worker = _make_worker(db)
    worker._claim_token = "same"
    db.get_job.return_value = {"status": "running", "node_id": worker.node_id, "claim_token": "same"}
    assert worker._check_job_ownership(1) is None

  def test_owner_kwargs_carry_token_only_when_claimed(self):
    worker = _make_worker(mock.MagicMock())
    assert worker._owner_kwargs() == {"node_id": worker.node_id}
    worker._claim_token = "tok"
    assert worker._owner_kwargs() == {"node_id": worker.node_id, "claim_token": "tok"}
