"""Tests for ConfigWatcherThread (auto-reload sma-ng.yml on file change)."""

from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from resources.config_schema import ConfigWatchSettings
from resources.daemon.threads import ConfigWatcherThread


def _make_settings(interval_seconds: float = 0.05, debounce_seconds: float = 0.05, enabled: bool = True):
  # ConfigWatchSettings expects ints; the watcher clamps to >= 1 / 0 anyway.
  # For test speed we feed it tiny values; the watcher's max(1, int(...))
  # clamps interval to 1s but float interval is honored via _stop_event.wait
  # in the run loop directly. Use a simple namespace to bypass int-only fields.
  return SimpleNamespace(enabled=enabled, interval_seconds=interval_seconds, debounce_seconds=debounce_seconds)


class _FakePcm:
  def __init__(self, path):
    self._config_file = path


def _start_watcher(server, pcm, interval=0.05, debounce=0.05):
  settings = _make_settings(interval_seconds=interval, debounce_seconds=debounce)
  watcher = ConfigWatcherThread(server=server, path_config_manager=pcm, settings=settings, logger=logging.getLogger("test.watcher"))
  # Override the interval-clamping so the test isn't bound to a 1s floor.
  watcher.interval = interval
  watcher.debounce = debounce
  watcher.start()
  return watcher


def _wait_until(predicate, timeout=2.0, poll=0.02):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    if predicate():
      return True
    time.sleep(poll)
  return False


@pytest.fixture
def cfg_file(tmp_path):
  p = tmp_path / "sma-ng.yml"
  p.write_text("daemon: {}\nbase: {}\n")
  return p


def test_change_detected_triggers_reload(cfg_file):
  server = MagicMock()
  server.reload_config.return_value = True
  pcm = _FakePcm(str(cfg_file))
  watcher = _start_watcher(server, pcm, interval=0.05, debounce=0.05)
  try:
    time.sleep(0.1)  # let the watcher capture initial state
    cfg_file.write_text("daemon: {}\nbase: {video: {codec: [hevc]}}\n")
    assert _wait_until(lambda: server.reload_config.called, timeout=2.0)
  finally:
    watcher.stop()
    watcher.join(timeout=1.0)
  assert server.reload_config.call_count == 1


def test_no_change_no_reload(cfg_file):
  server = MagicMock()
  pcm = _FakePcm(str(cfg_file))
  watcher = _start_watcher(server, pcm, interval=0.05, debounce=0.05)
  try:
    time.sleep(0.4)  # multiple ticks
  finally:
    watcher.stop()
    watcher.join(timeout=1.0)
  server.reload_config.assert_not_called()


def test_debounce_coalesces_rapid_changes(cfg_file):
  server = MagicMock()
  server.reload_config.return_value = True
  pcm = _FakePcm(str(cfg_file))
  watcher = _start_watcher(server, pcm, interval=0.05, debounce=0.2)
  try:
    time.sleep(0.1)
    # Rapid touches inside the debounce window
    for i in range(3):
      cfg_file.write_text("daemon: {}\nbase: {}\n# touch %d\n" % i)
      time.sleep(0.05)
    # Now wait longer than debounce for the reload to fire
    assert _wait_until(lambda: server.reload_config.called, timeout=2.0)
  finally:
    watcher.stop()
    watcher.join(timeout=1.0)
  assert server.reload_config.call_count == 1


def test_missing_file_does_not_crash(cfg_file):
  server = MagicMock()
  server.reload_config.return_value = True
  pcm = _FakePcm(str(cfg_file))
  watcher = _start_watcher(server, pcm, interval=0.05, debounce=0.05)
  try:
    time.sleep(0.1)
    cfg_file.unlink()
    time.sleep(0.3)  # several ticks with file missing
    assert watcher.is_alive(), "watcher must survive a missing file"
    server.reload_config.assert_not_called()
    # Recreate; should trigger reload.
    cfg_file.write_text("daemon: {}\nbase: {}\n# back\n")
    assert _wait_until(lambda: server.reload_config.called, timeout=2.0)
  finally:
    watcher.stop()
    watcher.join(timeout=1.0)


def test_reload_failure_does_not_busy_loop(cfg_file):
  server = MagicMock()
  server.reload_config.return_value = False  # simulate failed reload
  pcm = _FakePcm(str(cfg_file))
  watcher = _start_watcher(server, pcm, interval=0.05, debounce=0.05)
  try:
    time.sleep(0.1)
    cfg_file.write_text("daemon: {}\nbase: {}\n# bad\n")
    assert _wait_until(lambda: server.reload_config.called, timeout=2.0)
    # Keep stat unchanged for several ticks; reload must not be called again.
    time.sleep(0.4)
  finally:
    watcher.stop()
    watcher.join(timeout=1.0)
  assert server.reload_config.call_count == 1


def test_reload_exception_is_caught(cfg_file):
  server = MagicMock()
  server.reload_config.side_effect = RuntimeError("boom")
  pcm = _FakePcm(str(cfg_file))
  watcher = _start_watcher(server, pcm, interval=0.05, debounce=0.05)
  try:
    time.sleep(0.1)
    cfg_file.write_text("daemon: {}\nbase: {}\n# trigger\n")
    assert _wait_until(lambda: server.reload_config.called, timeout=2.0)
    assert watcher.is_alive(), "watcher must survive a raised reload"
  finally:
    watcher.stop()
    watcher.join(timeout=1.0)


def test_disabled_skips_thread_at_server_init():
  # Schema-level: enabled=False produces a setting that the server
  # init path is responsible for skipping. Verified at the schema level
  # here; server-side gating is exercised by tests/test_daemon.py.
  s = ConfigWatchSettings(enabled=False)
  assert s.enabled is False
  assert s.interval_seconds == 5
  assert s.debounce_seconds == 2


def test_zero_interval_setting():
  s = ConfigWatchSettings(interval_seconds=0)
  assert s.interval_seconds == 0
  # The watcher's __init__ clamps to >= 1, but the server init guard
  # (interval_seconds > 0) skips construction entirely.


def test_lock_serializes_with_manual_reload(cfg_file):
  """A pre-acquired _reload_lock blocks the watcher's reload until released."""
  server = MagicMock()
  # We don't actually exercise DaemonServer's lock here (that's in
  # tests/test_daemon.py / test_server.py). This is a tiny integration
  # check that the watcher calls reload_config (which the server
  # implementation wraps in a lock).
  server.reload_config.return_value = True
  pcm = _FakePcm(str(cfg_file))

  hold = threading.Lock()
  hold.acquire()

  def gated_reload():
    # Block until released, then return True.
    with hold:
      return True

  server.reload_config.side_effect = gated_reload

  watcher = _start_watcher(server, pcm, interval=0.05, debounce=0.05)
  try:
    time.sleep(0.1)
    cfg_file.write_text("daemon: {}\nbase: {}\n# touch\n")
    # Wait until the watcher has called reload_config (it is now blocked).
    assert _wait_until(lambda: server.reload_config.called, timeout=2.0)
    assert server.reload_config.call_count == 1
    # Release the simulated lock; the call returns True.
    hold.release()
    time.sleep(0.2)
    # Still only one call — no spurious double-trigger.
    assert server.reload_config.call_count == 1
  finally:
    if hold.locked():
      hold.release()
    watcher.stop()
    watcher.join(timeout=1.0)
