"""Reliability/secret-hygiene tests for daemon.py setting resolution, database URL
redaction and the SQLite guarded complete/fail transitions."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import daemon as daemon_entry
from resources.daemon.constants import STATUS_COMPLETED, STATUS_FAILED, STATUS_PENDING
from resources.daemon.db import SQLiteJobDatabase, redact_db_url

# ---------------------------------------------------------------------------
# redact_db_url
# ---------------------------------------------------------------------------


class TestRedactDbUrl:
  @pytest.mark.parametrize(
    "url,expected",
    [
      ("postgresql://sma:hunter2@db.example:5432/sma", "postgresql://db.example:5432/sma"),
      ("postgresql://sma:hunter2@db.example/sma?sslmode=require&password=x", "postgresql://db.example/sma"),
      ("postgres://user@10.0.0.5/jobs", "postgres://10.0.0.5/jobs"),
      ("postgresql://u:p@[::1]:5432/sma", "postgresql://[::1]:5432/sma"),
      ("sqlite:////data/sma-ng.db", "sqlite:////data/sma-ng.db"),
      ("host=db user=sma password=hunter2 dbname=sma", "<redacted DSN>"),
      ("", ""),
      (None, ""),
    ],
  )
  def test_redaction(self, url, expected):
    assert redact_db_url(url) == expected

  def test_never_contains_password(self):
    assert "hunter2" not in redact_db_url("postgresql://sma:hunter2@h/sma")
    assert "sma:" not in redact_db_url("postgresql://sma:hunter2@h/sma")

  def test_create_job_database_label_is_redacted(self):
    with patch("daemon.PostgreSQLJobDatabase", return_value=MagicMock()):
      _db, label = daemon_entry._create_job_database("postgresql://sma:hunter2@db/sma", MagicMock())
    assert label == "PostgreSQL: postgresql://db/sma"


# ---------------------------------------------------------------------------
# daemon.py: CLI > sma-ng.yml > default resolution
# ---------------------------------------------------------------------------


def _pcm(job_timeout_seconds=7200, heartbeat_interval=None, stale_seconds=None):
  pcm = MagicMock()
  pcm.api_key = None
  pcm.basic_auth = None
  pcm.ffmpeg_dir = None
  pcm.db_url = "postgresql://sma:hunter2@db.example/sma"
  pcm.smoke_test = False
  pcm.job_timeout_seconds = job_timeout_seconds
  pcm.progress_log_interval = 30
  pcm.scan_paths = []
  pcm.workers = 2
  pcm.get_all_configs.return_value = []
  daemon_cfg = SimpleNamespace()
  if heartbeat_interval is not None:
    daemon_cfg.heartbeat_interval = heartbeat_interval
  if stale_seconds is not None:
    daemon_cfg.stale_seconds = stale_seconds
  pcm._cfg = SimpleNamespace(daemon=daemon_cfg)
  return pcm


def _run_main(argv, pcm):
  server = MagicMock()
  server.serve_forever.side_effect = KeyboardInterrupt
  job_db = MagicMock()
  job_db.is_distributed = False
  with patch("sys.argv", ["daemon.py"] + argv):
    with patch("daemon.PathConfigManager", return_value=pcm), patch("daemon.ConfigLogManager", return_value=MagicMock()):
      with patch("daemon.ConfigLockManager", return_value=MagicMock()), patch("daemon.PostgreSQLJobDatabase", return_value=job_db):
        with patch("daemon.DaemonServer", return_value=server) as ds, patch("daemon._validate_hwaccel"):
          try:
            daemon_entry.main()
          except (KeyboardInterrupt, SystemExit):
            pass
  return ds.call_args.kwargs


class TestDaemonSettingResolution:
  def test_cli_zero_job_timeout_overrides_config(self):
    kwargs = _run_main(["--job-timeout", "0"], _pcm(job_timeout_seconds=7200))
    assert kwargs["job_timeout_seconds"] == 0

  def test_config_job_timeout_used_without_cli(self):
    kwargs = _run_main([], _pcm(job_timeout_seconds=5400))
    assert kwargs["job_timeout_seconds"] == 5400

  def test_heartbeat_and_stale_from_config(self):
    kwargs = _run_main([], _pcm(heartbeat_interval=15, stale_seconds=90))
    assert kwargs["heartbeat_interval"] == 15
    assert kwargs["stale_seconds"] == 90

  def test_cli_overrides_config_heartbeat_and_stale(self):
    kwargs = _run_main(["--heartbeat-interval", "10", "--stale-seconds", "45"], _pcm(heartbeat_interval=15, stale_seconds=90))
    assert kwargs["heartbeat_interval"] == 10
    assert kwargs["stale_seconds"] == 45

  def test_defaults_when_unset_everywhere(self):
    pcm = _pcm()
    pcm.heartbeat_interval = None
    pcm.stale_seconds = None
    kwargs = _run_main([], pcm)
    assert kwargs["heartbeat_interval"] == 30
    assert kwargs["stale_seconds"] == 120

  def test_startup_logs_do_not_leak_db_credentials_or_node_id(self):
    pcm = _pcm()
    pcm.node_id = "node-secret-123"
    fake_log = MagicMock()
    with patch("daemon.log", fake_log), patch("resources.daemon.constants.resolve_node_id", return_value="node-secret-123"):
      _run_main([], pcm)
    text = " ".join(str(a) for c in fake_log.method_calls for a in c.args)
    assert "hunter2" not in text
    assert "node-secret-123" not in text
    assert "postgresql://db.example/sma" in text

  def test_schema_defaults(self):
    from resources.config_schema import DaemonConfig

    cfg = DaemonConfig()
    assert cfg.heartbeat_interval == 30
    assert cfg.stale_seconds == 120


# ---------------------------------------------------------------------------
# SQLite guarded complete_job / fail_job
# ---------------------------------------------------------------------------


def _db(tmp_path):
  return SQLiteJobDatabase(f"sqlite:///{tmp_path / 'sma-ng.db'}")


class TestSQLiteGuardedTransitions:
  def test_complete_running_job_returns_true(self, tmp_path):
    db = _db(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml")
    db.claim_next_job(worker_id=1, node_id="node-a")
    assert db.complete_job(jid, node_id="node-a") is True
    assert db.get_job(jid)["status"] == STATUS_COMPLETED
    db.close()

  def test_complete_after_cancel_is_discarded(self, tmp_path):
    db = _db(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml")
    db.claim_next_job(worker_id=1, node_id="node-a")
    assert db.cancel_job(jid) is True
    assert db.complete_job(jid, node_id="node-a") is False
    assert db.get_job(jid)["status"] == "cancelled"
    db.close()

  def test_fail_after_cancel_is_discarded(self, tmp_path):
    db = _db(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml", max_retries=3)
    db.claim_next_job(worker_id=1, node_id="node-a")
    db.cancel_job(jid)
    assert db.fail_job(jid, "boom", node_id="node-a") is False
    row = db.get_job(jid)
    assert row["status"] == "cancelled"
    assert row["retry_count"] == 0
    db.close()

  def test_complete_by_other_node_is_discarded(self, tmp_path):
    db = _db(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml")
    db.claim_next_job(worker_id=1, node_id="node-b")
    assert db.complete_job(jid, node_id="node-a") is False
    assert db.fail_job(jid, "x", node_id="node-a") is False
    assert db.get_job(jid)["status"] == "running"
    db.close()

  def test_requeued_job_is_not_overwritten(self, tmp_path):
    db = _db(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml")
    db.claim_next_job(worker_id=1, node_id="node-a")
    db._requeue_running_jobs_for_node("node-a")
    assert db.complete_job(jid, node_id="node-a") is False
    assert db.get_job(jid)["status"] == STATUS_PENDING
    db.close()

  def test_fail_running_job_returns_true(self, tmp_path):
    db = _db(tmp_path)
    jid = db.add_job("/m/a.mkv", "/c.yml")
    db.claim_next_job(worker_id=1, node_id="node-a")
    assert db.fail_job(jid, "boom", node_id="node-a") is True
    assert db.get_job(jid)["status"] == STATUS_FAILED
    db.close()
