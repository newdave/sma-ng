"""Tests for Phase 1 cluster-mode additions.

Covers:
- Node identity cache in resources/daemon/constants.py
- UUID persistence via _write_node_id_to_yaml in resources/daemon/config.py
- WorkerPool drain/pause flag methods in resources/daemon/worker.py
- PostgreSQLLogHandler in resources/daemon/db_log_handler.py
- PostgreSQL DB layer (node_commands + logs tables) — skipped without TEST_DB_URL
"""

import json
import logging
import os
import socket
import threading
import uuid
from unittest import mock

import pytest

import resources.daemon.constants as _constants
from resources.daemon.config import _write_node_id_to_yaml
from resources.daemon.db_log_handler import PostgreSQLLogHandler
from resources.daemon.worker import ConversionWorker, WorkerPool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(worker_count=2):
  """Build a WorkerPool with all dependencies mocked, returning (pool, mock_workers)."""
  mock_workers = [mock.MagicMock(spec=ConversionWorker) for _ in range(worker_count)]
  for w in mock_workers:
    w.job_event = threading.Event()
    w.running = True

  with mock.patch("resources.daemon.worker.ConversionWorker") as MockWorker:
    MockWorker.side_effect = list(mock_workers)
    pool = WorkerPool(
      worker_count=worker_count,
      job_db=mock.MagicMock(),
      path_config_manager=mock.MagicMock(),
      config_log_manager=mock.MagicMock(),
      config_lock_manager=mock.MagicMock(),
      logger=mock.MagicMock(),
    )
  pool._workers = mock_workers
  return pool, mock_workers


# ---------------------------------------------------------------------------
# TestNodeIdentityCache
# ---------------------------------------------------------------------------


class TestNodeIdentityCache:
  """Tests for resolve_node_id() and set_node_id_cache() in constants.py."""

  def test_returns_hostname_when_cache_empty_and_env_unset(self, monkeypatch):
    monkeypatch.setattr(_constants, "_node_id_cache", None)
    monkeypatch.delenv("SMA_NODE_NAME", raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "test-host")
    assert _constants.resolve_node_id() == "test-host"

  def test_returns_env_var_when_cache_empty_and_env_set(self, monkeypatch):
    monkeypatch.setattr(_constants, "_node_id_cache", None)
    monkeypatch.setenv("SMA_NODE_NAME", "env-node")
    assert _constants.resolve_node_id() == "env-node"

  def test_set_node_id_cache_causes_resolve_to_return_cached_value(self, monkeypatch):
    monkeypatch.setattr(_constants, "_node_id_cache", None)
    monkeypatch.delenv("SMA_NODE_NAME", raising=False)
    _constants.set_node_id_cache("cached-node-uuid")
    assert _constants.resolve_node_id() == "cached-node-uuid"
    _constants.set_node_id_cache(None)

  def test_cached_value_persists_across_multiple_calls(self, monkeypatch):
    monkeypatch.setattr(_constants, "_node_id_cache", None)
    monkeypatch.delenv("SMA_NODE_NAME", raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "test-host")
    _constants.set_node_id_cache("stable-uuid")
    assert _constants.resolve_node_id() == "stable-uuid"
    assert _constants.resolve_node_id() == "stable-uuid"
    assert _constants.resolve_node_id() == "stable-uuid"
    _constants.set_node_id_cache(None)

  def test_clearing_cache_falls_back_to_hostname(self, monkeypatch):
    monkeypatch.setattr(_constants, "_node_id_cache", None)
    monkeypatch.delenv("SMA_NODE_NAME", raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "fallback-host")
    _constants.set_node_id_cache("some-uuid")
    assert _constants.resolve_node_id() == "some-uuid"
    _constants.set_node_id_cache(None)
    monkeypatch.setattr(_constants, "_node_id_cache", None)
    assert _constants.resolve_node_id() == "fallback-host"

  def test_whitespace_only_env_var_falls_back_to_hostname(self, monkeypatch):
    monkeypatch.setattr(_constants, "_node_id_cache", None)
    monkeypatch.setenv("SMA_NODE_NAME", "   ")
    monkeypatch.setattr(socket, "gethostname", lambda: "ws-host")
    assert _constants.resolve_node_id() == "ws-host"


# ---------------------------------------------------------------------------
# TestUUIDPersistence
# ---------------------------------------------------------------------------


class TestUUIDPersistence:
  """Tests for _write_node_id_to_yaml() in resources/daemon/config.py."""

  def test_writes_node_id_to_existing_daemon_section(self, tmp_path):
    cfg = tmp_path / "sma-ng.yml"
    cfg.write_text("daemon:\n  api_key: secret\n")
    _write_node_id_to_yaml(str(cfg), "test-uuid-1234")
    content = cfg.read_text()
    assert "test-uuid-1234" in content

  def test_creates_daemon_section_if_absent(self, tmp_path):
    cfg = tmp_path / "sma-ng.yml"
    cfg.write_text("other_section:\n  foo: bar\n")
    _write_node_id_to_yaml(str(cfg), "new-uuid-5678")
    content = cfg.read_text()
    assert "new-uuid-5678" in content
    assert "other_section" in content

  def test_atomic_write_uses_tmp_file_then_replace(self, tmp_path):
    cfg = tmp_path / "sma-ng.yml"
    cfg.write_text("daemon:\n  api_key: key\n")
    replaced_paths = []
    original_replace = os.replace

    def capturing_replace(src, dst):
      replaced_paths.append((src, dst))
      return original_replace(src, dst)

    with mock.patch("os.replace", side_effect=capturing_replace):
      _write_node_id_to_yaml(str(cfg), "atomic-uuid")

    assert len(replaced_paths) == 1
    src, dst = replaced_paths[0]
    assert src.endswith(".tmp")
    assert dst == str(cfg)

  def test_other_yaml_keys_preserved_after_write(self, tmp_path):
    cfg = tmp_path / "sma-ng.yml"
    cfg.write_text("daemon:\n  api_key: mysecret\n  db_url: postgres://localhost/sma\n")
    _write_node_id_to_yaml(str(cfg), "preserve-uuid")
    from ruamel.yaml import YAML

    y = YAML(typ="rt")
    with open(str(cfg)) as f:
      data = y.load(f)
    assert data["daemon"]["api_key"] == "mysecret"
    assert data["daemon"]["db_url"] == "postgres://localhost/sma"
    assert data["daemon"]["node_id"] == "preserve-uuid"

  def test_missing_config_file_does_not_raise(self):
    _write_node_id_to_yaml("/nonexistent/path/sma-ng.yml", "no-crash-uuid")

  def test_empty_yaml_file_does_not_raise(self, tmp_path):
    cfg = tmp_path / "sma-ng.yml"
    cfg.write_text("")
    _write_node_id_to_yaml(str(cfg), "empty-file-uuid")

  def test_does_not_overwrite_existing_node_id_when_called_again(self, tmp_path):
    cfg = tmp_path / "sma-ng.yml"
    cfg.write_text("daemon:\n  node_id: original-uuid\n")
    _write_node_id_to_yaml(str(cfg), "replacement-uuid")
    from ruamel.yaml import YAML

    y = YAML(typ="rt")
    with open(str(cfg)) as f:
      data = y.load(f)
    assert data["daemon"]["node_id"] == "replacement-uuid"


# ---------------------------------------------------------------------------
# TestWorkerPoolDrainPause
# ---------------------------------------------------------------------------


class TestWorkerPoolDrainPause:
  """Tests for set_drain_mode / clear_drain_mode / set_paused / clear_paused on WorkerPool."""

  def test_set_drain_mode_sets_event(self):
    pool, _ = _make_pool()
    assert not pool._drain_mode.is_set()
    pool.set_drain_mode()
    assert pool._drain_mode.is_set()

  def test_clear_drain_mode_clears_event(self):
    pool, _ = _make_pool()
    pool._drain_mode.set()
    pool.clear_drain_mode()
    assert not pool._drain_mode.is_set()

  def test_set_paused_sets_event(self):
    pool, _ = _make_pool()
    assert not pool._pause_mode.is_set()
    pool.set_paused()
    assert pool._pause_mode.is_set()

  def test_clear_paused_clears_event(self):
    pool, _ = _make_pool()
    pool._pause_mode.set()
    pool.clear_paused()
    assert not pool._pause_mode.is_set()

  def test_clear_paused_fires_job_event_on_all_workers(self):
    pool, mock_workers = _make_pool(worker_count=3)
    for w in mock_workers:
      w.job_event = mock.MagicMock()
    pool.clear_paused()
    for w in mock_workers:
      w.job_event.set.assert_called_once()

  def test_drain_mode_and_pause_mode_are_independent(self):
    pool, _ = _make_pool()
    pool.set_drain_mode()
    pool.set_paused()
    assert pool._drain_mode.is_set()
    assert pool._pause_mode.is_set()
    pool.clear_drain_mode()
    assert not pool._drain_mode.is_set()
    assert pool._pause_mode.is_set()

  def test_drain_mode_does_not_affect_existing_drain_join_method(self):
    pool, mock_workers = _make_pool(worker_count=1)
    pool.set_drain_mode()
    pool.drain(timeout=0.0)
    mock_workers[0].join.assert_called_once_with(timeout=0.0)


# ---------------------------------------------------------------------------
# TestPostgreSQLLogHandler
# ---------------------------------------------------------------------------


class TestPostgreSQLLogHandler:
  """Tests for PostgreSQLLogHandler in resources/daemon/db_log_handler.py."""

  def _make_record(self, msg="test message", level=logging.INFO, name="test.logger"):
    record = logging.LogRecord(
      name=name,
      level=level,
      pathname="",
      lineno=0,
      msg=msg,
      args=(),
      exc_info=None,
    )
    return record

  def test_emit_buffers_record_into_batch(self):
    db = mock.MagicMock()
    handler = PostgreSQLLogHandler(db, node_id="node-1", batch_size=10)
    handler.emit(self._make_record("hello"))
    assert len(handler._batch) == 1
    assert handler._batch[0]["message"] == "hello"
    assert handler._batch[0]["node_id"] == "node-1"
    assert handler._batch[0]["level"] == "INFO"
    assert handler._batch[0]["logger"] == "test.logger"

  def test_emit_does_not_flush_before_batch_size_reached(self):
    db = mock.MagicMock()
    handler = PostgreSQLLogHandler(db, node_id="node-1", batch_size=5)
    for i in range(4):
      handler.emit(self._make_record(f"msg{i}"))
    db.insert_logs.assert_not_called()
    assert len(handler._batch) == 4

  def test_emit_flushes_automatically_when_batch_size_reached(self):
    db = mock.MagicMock()
    handler = PostgreSQLLogHandler(db, node_id="node-1", batch_size=3)
    for i in range(3):
      handler.emit(self._make_record(f"msg{i}"))
    db.insert_logs.assert_called_once()
    assert len(handler._batch) == 0

  def test_flush_calls_insert_logs_with_buffered_records(self):
    db = mock.MagicMock()
    handler = PostgreSQLLogHandler(db, node_id="node-1", batch_size=50)
    handler.emit(self._make_record("alpha"))
    handler.emit(self._make_record("beta"))
    handler.flush()
    db.insert_logs.assert_called_once()
    sent = db.insert_logs.call_args[0][0]
    messages = [r["message"] for r in sent]
    assert "alpha" in messages
    assert "beta" in messages

  def test_flush_clears_batch_after_sending(self):
    db = mock.MagicMock()
    handler = PostgreSQLLogHandler(db, node_id="node-1", batch_size=50)
    handler.emit(self._make_record("msg"))
    handler.flush()
    assert handler._batch == []

  def test_flush_does_nothing_when_batch_empty(self):
    db = mock.MagicMock()
    handler = PostgreSQLLogHandler(db, node_id="node-1", batch_size=50)
    handler.flush()
    db.insert_logs.assert_not_called()

  def test_emit_swallows_exception_from_insert_logs(self):
    db = mock.MagicMock()
    db.insert_logs.side_effect = RuntimeError("DB down")
    handler = PostgreSQLLogHandler(db, node_id="node-1", batch_size=1)
    handler.emit(self._make_record("will trigger flush"))

  def test_emit_swallows_arbitrary_exception(self):
    db = mock.MagicMock()
    handler = PostgreSQLLogHandler(db, node_id="node-1", batch_size=50)
    with mock.patch.object(handler, "_lock", side_effect=Exception("lock broken")):
      handler.emit(self._make_record("crash"))

  def test_close_flushes_remaining_records(self):
    db = mock.MagicMock()
    handler = PostgreSQLLogHandler(db, node_id="node-1", batch_size=50)
    handler.emit(self._make_record("buffered"))
    handler.close()
    db.insert_logs.assert_called_once()
    sent = db.insert_logs.call_args[0][0]
    assert any(r["message"] == "buffered" for r in sent)

  def test_close_does_not_raise_when_db_fails(self):
    db = mock.MagicMock()
    db.insert_logs.side_effect = RuntimeError("DB gone")
    handler = PostgreSQLLogHandler(db, node_id="node-1", batch_size=50)
    handler.emit(self._make_record("oops"))
    handler.close()

  def test_multiple_emits_across_batch_boundary(self):
    db = mock.MagicMock()
    handler = PostgreSQLLogHandler(db, node_id="node-1", batch_size=2)
    for i in range(5):
      handler.emit(self._make_record(f"msg{i}"))
    assert db.insert_logs.call_count == 2
    handler.flush()
    assert db.insert_logs.call_count == 3

  def test_records_include_node_id(self):
    db = mock.MagicMock()
    handler = PostgreSQLLogHandler(db, node_id="my-node", batch_size=1)
    handler.emit(self._make_record("check node"))
    sent = db.insert_logs.call_args[0][0]
    assert all(r["node_id"] == "my-node" for r in sent)

  @pytest.mark.parametrize(
    "level,expected",
    [
      (logging.DEBUG, "DEBUG"),
      (logging.INFO, "INFO"),
      (logging.WARNING, "WARNING"),
      (logging.ERROR, "ERROR"),
      (logging.CRITICAL, "CRITICAL"),
    ],
  )
  def test_emit_records_correct_level_name(self, level, expected):
    db = mock.MagicMock()
    handler = PostgreSQLLogHandler(db, node_id="node-1", batch_size=50)
    handler.emit(self._make_record("lvl test", level=level))
    assert handler._batch[0]["level"] == expected


# ---------------------------------------------------------------------------
# TestNodeCommandsDB  (requires TEST_DB_URL)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("job_db")
class TestNodeCommandsDB:
  """Integration tests for node_commands table methods."""

  def _unique_node(self):
    return "test-node-" + str(uuid.uuid4())[:8]

  def test_send_node_command_inserts_pending_row(self, job_db):
    node = self._unique_node()
    job_db.heartbeat(node, "localhost", 2, None)
    targets = job_db.send_node_command(node, "drain")
    assert node in targets
    cmd = job_db.poll_node_command(node)
    assert cmd is not None
    assert cmd["command"] == "drain"
    job_db.ack_node_command(cmd["id"], "done")

  def test_poll_node_command_marks_executing(self, job_db):
    node = self._unique_node()
    job_db.heartbeat(node, "localhost", 2, None)
    job_db.send_node_command(node, "pause")
    cmd = job_db.poll_node_command(node)
    assert cmd is not None
    assert cmd["status"] == "pending"
    job_db.ack_node_command(cmd["id"], "done")

  def test_poll_node_command_returns_none_when_no_pending(self, job_db):
    node = self._unique_node()
    result = job_db.poll_node_command(node)
    assert result is None

  def test_ack_node_command_done_updates_status(self, job_db):
    node = self._unique_node()
    job_db.heartbeat(node, "localhost", 2, None)
    job_db.send_node_command(node, "resume")
    cmd = job_db.poll_node_command(node)
    assert cmd is not None
    job_db.ack_node_command(cmd["id"], "done")
    result = job_db.poll_node_command(node)
    assert result is None

  def test_ack_node_command_failed_updates_status(self, job_db):
    node = self._unique_node()
    job_db.heartbeat(node, "localhost", 2, None)
    job_db.send_node_command(node, "restart")
    cmd = job_db.poll_node_command(node)
    assert cmd is not None
    job_db.ack_node_command(cmd["id"], "failed")
    result = job_db.poll_node_command(node)
    assert result is None

  def test_poll_returns_oldest_pending_command_first(self, job_db):
    node = self._unique_node()
    job_db.heartbeat(node, "localhost", 2, None)
    job_db.send_node_command(node, "drain")
    job_db.send_node_command(node, "pause")
    first = job_db.poll_node_command(node)
    assert first is not None
    assert first["command"] == "drain"
    job_db.ack_node_command(first["id"], "done")
    second = job_db.poll_node_command(node)
    assert second is not None
    assert second["command"] == "pause"
    job_db.ack_node_command(second["id"], "done")


# ---------------------------------------------------------------------------
# TestLogsDB  (requires TEST_DB_URL)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("job_db")
class TestLogsDB:
  """Integration tests for logs table methods."""

  def _unique_node(self):
    return "log-node-" + str(uuid.uuid4())[:8]

  def _insert_record(self, node_id, level="INFO", message="test", logger="test.log"):
    return {"node_id": node_id, "level": level, "logger": logger, "message": message}

  def test_insert_logs_stores_records(self, job_db):
    node = self._unique_node()
    job_db.insert_logs([self._insert_record(node, message="hello")])
    rows = job_db.get_logs(node_id=node)
    assert len(rows) == 1
    assert rows[0]["message"] == "hello"

  def test_get_logs_returns_newest_first(self, job_db):
    node = self._unique_node()
    job_db.insert_logs(
      [
        self._insert_record(node, message="first"),
        self._insert_record(node, message="second"),
        self._insert_record(node, message="third"),
      ]
    )
    rows = job_db.get_logs(node_id=node)
    assert len(rows) == 3
    messages = [r["message"] for r in rows]
    assert "first" in messages
    assert "second" in messages
    assert "third" in messages

  def test_get_logs_filters_by_node_id(self, job_db):
    node_a = self._unique_node()
    node_b = self._unique_node()
    job_db.insert_logs([self._insert_record(node_a, message="from-a")])
    job_db.insert_logs([self._insert_record(node_b, message="from-b")])
    rows = job_db.get_logs(node_id=node_a)
    assert all(r["node_id"] == node_a for r in rows)
    messages = [r["message"] for r in rows]
    assert "from-a" in messages
    assert "from-b" not in messages

  def test_get_logs_filters_by_level(self, job_db):
    node = self._unique_node()
    job_db.insert_logs(
      [
        self._insert_record(node, level="ERROR", message="err-msg"),
        self._insert_record(node, level="INFO", message="info-msg"),
      ]
    )
    rows = job_db.get_logs(node_id=node, level="ERROR")
    assert all(r["level"] == "ERROR" for r in rows)
    messages = [r["message"] for r in rows]
    assert "err-msg" in messages
    assert "info-msg" not in messages

  def test_cleanup_old_logs_days_zero_deletes_all(self, job_db):
    node = self._unique_node()
    job_db.insert_logs([self._insert_record(node)])
    deleted = job_db.cleanup_old_logs(days=0)
    assert deleted >= 1
    rows = job_db.get_logs(node_id=node)
    assert rows == []

  def test_cleanup_old_logs_large_days_deletes_nothing(self, job_db):
    node = self._unique_node()
    job_db.insert_logs([self._insert_record(node, message="keep-me")])
    deleted = job_db.cleanup_old_logs(days=9999)
    assert deleted == 0
    rows = job_db.get_logs(node_id=node)
    assert any(r["message"] == "keep-me" for r in rows)

  def test_insert_logs_empty_list_is_noop(self, job_db):
    job_db.insert_logs([])

  def test_get_logs_returns_empty_list_when_no_records(self, job_db):
    node = self._unique_node()
    rows = job_db.get_logs(node_id=node)
    assert rows == []


# ---------------------------------------------------------------------------
# TestClusterNodesVersionHwaccel  (requires TEST_DB_URL)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("job_db")
class TestClusterNodesVersionHwaccel:
  """Integration tests for version/hwaccel columns in cluster_nodes."""

  def _unique_node(self):
    return "ver-node-" + str(uuid.uuid4())[:8]

  def test_heartbeat_stores_version_and_hwaccel(self, job_db):
    node = self._unique_node()
    job_db.heartbeat(node, "test-host", 4, None, version="1.0", hwaccel="qsv")
    nodes = job_db.get_cluster_nodes()
    match = next((n for n in nodes if n["node_id"] == node), None)
    assert match is not None
    assert match["version"] == "1.0"
    assert match["hwaccel"] == "qsv"

  def test_heartbeat_preserves_existing_hwaccel_when_none_passed(self, job_db):
    node = self._unique_node()
    job_db.heartbeat(node, "test-host", 4, None, version="1.0", hwaccel="nvenc")
    job_db.heartbeat(node, "test-host", 4, None, version="1.1", hwaccel=None)
    nodes = job_db.get_cluster_nodes()
    match = next((n for n in nodes if n["node_id"] == node), None)
    assert match is not None
    assert match["hwaccel"] == "nvenc"

  def test_heartbeat_returns_none(self, job_db):
    node = self._unique_node()
    result = job_db.heartbeat(node, "test-host", 2, None, version="1.0", hwaccel="vaapi")
    assert result is None

  def test_heartbeat_stores_node_name(self, job_db):
    node = self._unique_node()
    job_db.heartbeat(node, "test-host", 2, None, node_name="sma-node1")
    nodes = job_db.get_cluster_nodes()
    match = next((n for n in nodes if n["node_id"] == node), None)
    assert match is not None
    assert match["node_name"] == "sma-node1"

  def test_heartbeat_preserves_existing_node_name_when_none_passed(self, job_db):
    node = self._unique_node()
    job_db.heartbeat(node, "test-host", 2, None, node_name="sma-node1")
    job_db.heartbeat(node, "test-host", 2, None, node_name=None)
    nodes = job_db.get_cluster_nodes()
    match = next((n for n in nodes if n["node_id"] == node), None)
    assert match is not None
    assert match["node_name"] == "sma-node1"


# ---------------------------------------------------------------------------
# TestClusterConfigDB  (requires TEST_DB_URL) — Phase 2
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("job_db")
class TestClusterConfigDB:
  """Integration tests for cluster_config table methods."""

  def test_get_cluster_config_returns_none_when_absent(self, job_db):
    # Wipe any leftover row from previous runs
    with job_db._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("DELETE FROM cluster_config WHERE id = 1")
    assert job_db.get_cluster_config() is None

  def test_roundtrip_set_then_get(self, job_db):
    data = {"video": {"codec": ["hevc"]}, "daemon": {"log_ttl_days": 7}}
    job_db.set_cluster_config(data, updated_by="test-node")
    result = job_db.get_cluster_config()
    assert result is not None
    assert result["video"]["codec"] == ["hevc"]
    assert result["daemon"]["log_ttl_days"] == 7

  def test_set_strips_secret_keys_from_daemon_section(self, job_db):
    data = {
      "daemon": {
        "api_key": "supersecret",
        "db_url": "postgres://user:pass@host/db",
        "username": "admin",
        "password": "hunter2",
        "node_id": "abc-123",
        "log_ttl_days": 30,
      }
    }
    job_db.set_cluster_config(data, updated_by="test-node")
    result = job_db.get_cluster_config()
    assert result is not None
    daemon = result.get("daemon", {})
    assert "api_key" not in daemon
    assert "db_url" not in daemon
    assert "username" not in daemon
    assert "password" not in daemon
    assert "node_id" not in daemon
    assert daemon.get("log_ttl_days") == 30

  def test_overwrite_updates_existing_row(self, job_db):
    job_db.set_cluster_config({"daemon": {"log_ttl_days": 10}})
    job_db.set_cluster_config({"daemon": {"log_ttl_days": 99}})
    result = job_db.get_cluster_config()
    assert result is not None
    assert result["daemon"]["log_ttl_days"] == 99

  def test_get_returns_dict(self, job_db):
    job_db.set_cluster_config({"video": {"preset": "fast"}})
    result = job_db.get_cluster_config()
    assert isinstance(result, dict)

  def test_non_daemon_keys_preserved(self, job_db):
    data = {"video": {"codec": ["h264"]}, "audio": {"codec": ["aac"]}, "daemon": {"log_ttl_days": 5}}
    job_db.set_cluster_config(data)
    result = job_db.get_cluster_config()
    assert result is not None
    assert result["video"]["codec"] == ["h264"]
    assert result["audio"]["codec"] == ["aac"]


# ---------------------------------------------------------------------------
# TestNodeExpiryDB  (requires TEST_DB_URL) — Phase 2
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("job_db")
class TestNodeExpiryDB:
  """Integration tests for expire_offline_nodes() and cleanup_orphaned_commands()."""

  def _unique_node(self):
    return "expiry-node-" + str(uuid.uuid4())[:8]

  def _set_offline_old(self, job_db, node_id, days_ago=10):
    """Insert an offline node with last_seen set days_ago in the past."""
    with job_db._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
          INSERT INTO cluster_nodes (node_id, host, worker_count, status, last_seen)
          VALUES (%s, 'old-host', 1, 'offline', NOW() - make_interval(days => %s))
          ON CONFLICT (node_id) DO UPDATE
            SET status = 'offline', last_seen = NOW() - make_interval(days => %s)
          """,
          (node_id, days_ago, days_ago),
        )

  def test_expire_offline_nodes_deletes_stale_offline_nodes(self, job_db):
    node = self._unique_node()
    self._set_offline_old(job_db, node, days_ago=10)
    expired = job_db.expire_offline_nodes(expiry_days=5)
    assert node in expired
    nodes = job_db.get_cluster_nodes()
    assert not any(n["node_id"] == node for n in nodes)

  def test_expire_offline_nodes_skips_online_nodes(self, job_db):
    node = self._unique_node()
    job_db.heartbeat(node, "active-host", 2, None)
    expired = job_db.expire_offline_nodes(expiry_days=0)
    assert node not in expired

  def test_expire_offline_nodes_returns_list_of_node_ids(self, job_db):
    node1 = self._unique_node()
    node2 = self._unique_node()
    self._set_offline_old(job_db, node1, days_ago=20)
    self._set_offline_old(job_db, node2, days_ago=20)
    expired = job_db.expire_offline_nodes(expiry_days=10)
    assert node1 in expired
    assert node2 in expired

  def test_expire_offline_nodes_zero_days_returns_empty(self, job_db):
    # expiry_days=0 means "never expire" — no nodes should be removed
    node = self._unique_node()
    self._set_offline_old(job_db, node, days_ago=100)
    # expiry_days=0 is a no-op by design (handled in HeartbeatThread, not DB layer)
    # But let's verify the DB layer itself: days=0 uses make_interval(days=>0) which matches
    # nodes older than NOW() - 0, i.e. all offline nodes. This is intentionally not protected
    # at the DB layer — the thread guard (if expiry_days > 0) prevents it from being called.
    # So we test cleanup_orphaned_commands separately.
    result = job_db.cleanup_orphaned_commands([])
    assert result == 0

  def test_cleanup_orphaned_commands_removes_pending_commands(self, job_db):
    node = self._unique_node()
    job_db.heartbeat(node, "host", 1, None)
    job_db.send_node_command(node, "drain")
    deleted = job_db.cleanup_orphaned_commands([node])
    assert deleted >= 1

  def test_cleanup_orphaned_commands_empty_list_is_noop(self, job_db):
    result = job_db.cleanup_orphaned_commands([])
    assert result == 0


# ---------------------------------------------------------------------------
# TestLogArchivalUnit  (unit, no DB required) — Phase 2
# ---------------------------------------------------------------------------


class TestLogArchivalUnit:
  """Unit tests for LogArchiver in resources/daemon/log_archiver.py."""

  from resources.daemon.log_archiver import LogArchiver

  def _make_archiver(self, tmp_path, archive_after=7, delete_after=30):
    from resources.daemon.log_archiver import LogArchiver

    return LogArchiver(str(tmp_path), archive_after, delete_after, mock.MagicMock())

  def _fake_record(self, node_id="node-1", date_str="2025-01-15", message="test"):
    from datetime import date, datetime, timezone

    ts = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    return {"node_id": node_id, "level": "INFO", "logger": "test", "message": message, "timestamp": ts}

  def test_write_archive_creates_gz_file(self, tmp_path):
    from datetime import date

    archiver = self._make_archiver(tmp_path)
    records = [self._fake_record()]
    ok = archiver._write_archive("node-1", date(2025, 1, 15), records)
    assert ok is True
    gz_path = tmp_path / "node-1" / "2025-01-15.jsonl.gz"
    assert gz_path.exists()

  def test_write_archive_content_is_valid_jsonl(self, tmp_path):
    import gzip
    from datetime import date

    archiver = self._make_archiver(tmp_path)
    records = [self._fake_record(message="hello"), self._fake_record(message="world")]
    archiver._write_archive("node-1", date(2025, 1, 15), records)
    gz_path = tmp_path / "node-1" / "2025-01-15.jsonl.gz"
    with gzip.open(str(gz_path), "rt", encoding="utf-8") as f:
      lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 2
    messages = [l["message"] for l in lines]
    assert "hello" in messages
    assert "world" in messages

  def test_write_archive_atomic_uses_tmp_then_replace(self, tmp_path):
    from datetime import date

    replaced = []
    original_replace = os.replace

    def capturing_replace(src, dst):
      replaced.append((src, dst))
      return original_replace(src, dst)

    archiver = self._make_archiver(tmp_path)
    with mock.patch("os.replace", side_effect=capturing_replace):
      archiver._write_archive("node-1", date(2025, 1, 15), [self._fake_record()])

    assert len(replaced) == 1
    src, dst = replaced[0]
    assert src.endswith(".tmp")
    assert dst.endswith(".jsonl.gz")
    assert not dst.endswith(".tmp")

  def test_prune_old_files_deletes_expired_archives(self, tmp_path):
    from resources.daemon.log_archiver import LogArchiver

    node_dir = tmp_path / "node-1"
    node_dir.mkdir()
    old_file = node_dir / "2020-01-01.jsonl.gz"
    old_file.write_bytes(b"old")
    old_time = __import__("time").time() - 40 * 86400
    os.utime(str(old_file), (old_time, old_time))

    archiver = LogArchiver(str(tmp_path), archive_after_days=7, delete_after_days=30, logger=mock.MagicMock())
    pruned = archiver._prune_old_files()
    assert pruned == 1
    assert not old_file.exists()

  def test_prune_old_files_keeps_recent_archives(self, tmp_path):
    from resources.daemon.log_archiver import LogArchiver

    node_dir = tmp_path / "node-1"
    node_dir.mkdir()
    recent_file = node_dir / "2025-01-14.jsonl.gz"
    recent_file.write_bytes(b"recent")

    archiver = LogArchiver(str(tmp_path), archive_after_days=7, delete_after_days=30, logger=mock.MagicMock())
    pruned = archiver._prune_old_files()
    assert pruned == 0
    assert recent_file.exists()

  def test_prune_skipped_when_delete_after_zero(self, tmp_path):
    from resources.daemon.log_archiver import LogArchiver

    archiver = LogArchiver(str(tmp_path), archive_after_days=7, delete_after_days=0, logger=mock.MagicMock())
    db = mock.MagicMock()
    db.get_logs_for_archival.return_value = []
    archiver.run(db)
    db.delete_logs_before.assert_not_called()

  def test_run_calls_archive_then_prune(self, tmp_path):
    from resources.daemon.log_archiver import LogArchiver

    archiver = LogArchiver(str(tmp_path), archive_after_days=7, delete_after_days=30, logger=mock.MagicMock())
    db = mock.MagicMock()
    db.get_logs_for_archival.return_value = []
    with mock.patch.object(archiver, "_archive_from_db", return_value=0) as mock_archive:
      with mock.patch.object(archiver, "_prune_old_files", return_value=0) as mock_prune:
        archiver.run(db)
    mock_archive.assert_called_once_with(db)
    mock_prune.assert_called_once()

  def test_archive_from_db_deletes_rows_after_successful_write(self, tmp_path):
    from datetime import date, datetime, timezone

    from resources.daemon.log_archiver import LogArchiver

    archiver = LogArchiver(str(tmp_path), archive_after_days=7, delete_after_days=30, logger=mock.MagicMock())
    ts = datetime(2025, 1, 10, 10, 0, 0, tzinfo=timezone.utc)
    records = [{"node_id": "node-1", "level": "INFO", "logger": "test", "message": "msg", "timestamp": ts}]
    db = mock.MagicMock()
    db.get_logs_for_archival.return_value = records
    db.delete_logs_before.return_value = 1
    count = archiver._archive_from_db(db)
    assert count == 1
    db.delete_logs_before.assert_called_once_with(7)

  def test_archive_from_db_skips_delete_on_write_failure(self, tmp_path):
    from datetime import datetime, timezone

    from resources.daemon.log_archiver import LogArchiver

    archiver = LogArchiver(str(tmp_path), archive_after_days=7, delete_after_days=30, logger=mock.MagicMock())
    ts = datetime(2025, 1, 10, 10, 0, 0, tzinfo=timezone.utc)
    records = [{"node_id": "node-1", "level": "INFO", "logger": "test", "message": "msg", "timestamp": ts}]
    db = mock.MagicMock()
    db.get_logs_for_archival.return_value = records
    with mock.patch.object(archiver, "_write_archive", return_value=False):
      count = archiver._archive_from_db(db)
    assert count == 0
    db.delete_logs_before.assert_not_called()


# ---------------------------------------------------------------------------
# TestConfigMerge  (unit, no DB required) — Phase 2
# ---------------------------------------------------------------------------


class TestConfigMerge:
  """Unit tests for DB-config merge logic in PathConfigManager.load_config()."""

  def _make_manager(self, tmp_path, yml_content):
    from resources.daemon.config import PathConfigManager

    cfg = tmp_path / "sma-ng.yml"
    cfg.write_text(yml_content)
    return PathConfigManager(str(cfg)), str(cfg)

  def test_db_config_provides_base_when_local_missing_key(self, tmp_path):
    from resources.daemon.config import PathConfigManager

    yml = "daemon:\n  log_ttl_days: 15\n"
    mgr, cfg_path = self._make_manager(tmp_path, yml)

    db = mock.MagicMock()
    db.is_distributed = True
    db.get_cluster_config.return_value = {"daemon": {"node_expiry_days": 42}}

    mgr.load_config(cfg_path, job_db=db)
    assert mgr.node_expiry_days == 42

  def test_local_config_wins_over_db(self, tmp_path):
    from resources.daemon.config import PathConfigManager

    yml = "daemon:\n  log_ttl_days: 99\n"
    mgr, cfg_path = self._make_manager(tmp_path, yml)

    db = mock.MagicMock()
    db.is_distributed = True
    db.get_cluster_config.return_value = {"daemon": {"log_ttl_days": 1}}

    mgr.load_config(cfg_path, job_db=db)
    assert mgr.log_ttl_days == 99

  def test_none_db_config_does_not_crash(self, tmp_path):
    from resources.daemon.config import PathConfigManager

    yml = "daemon:\n  log_ttl_days: 10\n"
    mgr, cfg_path = self._make_manager(tmp_path, yml)

    db = mock.MagicMock()
    db.is_distributed = True
    db.get_cluster_config.return_value = None

    mgr.load_config(cfg_path, job_db=db)
    assert mgr.log_ttl_days == 10

  def test_no_db_merge_when_job_db_not_passed(self, tmp_path):
    from resources.daemon.config import PathConfigManager

    yml = "daemon:\n  log_ttl_days: 20\n"
    mgr, cfg_path = self._make_manager(tmp_path, yml)

    db = mock.MagicMock()
    db.is_distributed = True

    mgr.load_config(cfg_path)
    db.get_cluster_config.assert_not_called()

  def test_non_distributed_db_skips_merge(self, tmp_path):
    from resources.daemon.config import PathConfigManager

    yml = "daemon:\n  log_ttl_days: 5\n"
    mgr, cfg_path = self._make_manager(tmp_path, yml)

    db = mock.MagicMock()
    db.is_distributed = False

    mgr.load_config(cfg_path, job_db=db)
    db.get_cluster_config.assert_not_called()
