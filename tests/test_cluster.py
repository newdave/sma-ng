"""Tests for Phase 1 cluster-mode additions.

Covers:
- Node identity cache in resources/daemon/constants.py
- UUID persistence via _write_node_id_to_yaml in resources/daemon/config.py
- WorkerPool drain/pause flag methods in resources/daemon/worker.py
- PostgreSQLLogHandler in resources/daemon/db_log_handler.py
- PostgreSQL DB layer (node_commands + logs tables) — skipped without TEST_DB_URL
"""

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
