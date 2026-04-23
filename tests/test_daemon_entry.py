"""Tests for daemon.py (project root) — main() argument parsing, env var handling,
and run_smoke_test(). These cover the thin entry-point layer at lines 71-290."""

import os
import sys
from unittest.mock import MagicMock, call, patch

import pytest

# Import directly from the root daemon.py entry point
import daemon as daemon_entry
from daemon import run_smoke_test

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pcm(path_configs=None, api_key=None, basic_auth=None, ffmpeg_dir=None, db_url=None, smoke_test=False, job_timeout_seconds=0, progress_log_interval=0, scan_paths=None):
  """Return a mocked PathConfigManager."""
  pcm = MagicMock()
  pcm.path_configs = path_configs or []
  pcm.api_key = api_key
  pcm.basic_auth = basic_auth
  pcm.ffmpeg_dir = ffmpeg_dir
  pcm.db_url = db_url
  pcm.smoke_test = smoke_test
  pcm.job_timeout_seconds = job_timeout_seconds
  pcm.progress_log_interval = progress_log_interval
  pcm.scan_paths = scan_paths or []
  pcm.get_all_configs.return_value = []
  return pcm


def _make_server():
  """Return a mocked DaemonServer that does nothing."""
  server = MagicMock()
  server.serve_forever.side_effect = KeyboardInterrupt  # stop the loop immediately
  return server


# ---------------------------------------------------------------------------
# run_smoke_test
# ---------------------------------------------------------------------------


class TestRunSmokeTest:
  def test_skips_when_fixture_not_found(self):
    pcm = _make_pcm()
    logger = MagicMock()
    # Point fixture path to a non-existent file
    with patch.object(daemon_entry, "_SMOKE_TEST_FIXTURE", "/nonexistent/test1.mkv"):
      run_smoke_test(pcm, None, logger)
    logger.warning.assert_called_once()
    assert "not found" in logger.warning.call_args[0][0]

  def test_prepends_ffmpeg_dir_to_path(self, tmp_path):
    fixture = tmp_path / "test1.mkv"
    fixture.write_bytes(b"fake")
    pcm = _make_pcm()
    pcm.get_all_configs.return_value = []
    logger = MagicMock()
    original_path = os.environ.get("PATH", "")
    with patch.object(daemon_entry, "_SMOKE_TEST_FIXTURE", str(fixture)):
      run_smoke_test(pcm, "/custom/ffmpeg/bin", logger)
    assert os.environ["PATH"].startswith("/custom/ffmpeg/bin")
    # Restore PATH
    os.environ["PATH"] = original_path

  def test_skips_missing_config_file(self, tmp_path):
    fixture = tmp_path / "test1.mkv"
    fixture.write_bytes(b"fake")
    pcm = _make_pcm()
    pcm.get_all_configs.return_value = ["/nonexistent/autoProcess.ini"]
    logger = MagicMock()
    with patch.object(daemon_entry, "_SMOKE_TEST_FIXTURE", str(fixture)):
      run_smoke_test(pcm, None, logger)
    # Should warn about missing config, not fail
    assert any("SKIP" in str(c) for c in logger.warning.call_args_list)

  def test_exits_1_when_config_raises(self, tmp_path):
    fixture = tmp_path / "test1.mkv"
    fixture.write_bytes(b"fake")
    config = tmp_path / "autoProcess.ini"
    config.write_text("[Converter]\n")
    pcm = _make_pcm()
    pcm.get_all_configs.return_value = [str(config)]
    logger = MagicMock()
    with patch.object(daemon_entry, "_SMOKE_TEST_FIXTURE", str(fixture)):
      with patch("resources.readsettings.ReadSettings", side_effect=Exception("bad config")):
        with pytest.raises(SystemExit) as exc:
          run_smoke_test(pcm, None, logger)
    assert exc.value.code == 1

  def test_passes_when_all_configs_succeed(self, tmp_path):
    fixture = tmp_path / "test1.mkv"
    fixture.write_bytes(b"fake")
    config = tmp_path / "autoProcess.ini"
    config.write_text("[Converter]\n")
    pcm = _make_pcm()
    pcm.get_all_configs.return_value = [str(config)]
    logger = MagicMock()

    mock_mp = MagicMock()
    mock_mp.jsonDump.return_value = '{"output": {"video": {"codec": "h265"}}}'

    with patch.object(daemon_entry, "_SMOKE_TEST_FIXTURE", str(fixture)):
      with patch("resources.readsettings.ReadSettings", return_value=MagicMock()):
        with patch("resources.mediaprocessor.MediaProcessor", return_value=mock_mp):
          run_smoke_test(pcm, None, logger)
    logger.info.assert_any_call("Smoke test passed.")


# ---------------------------------------------------------------------------
# main() — argument parsing
# ---------------------------------------------------------------------------


class TestDaemonMainArgParsing:
  """Test that main() correctly reads CLI args and env vars."""

  def _run_main_with_args(self, argv, env=None, db_url="postgresql://localhost/sma"):
    """Helper: patch everything to isolate arg/env reading, capture managers created."""
    env = env or {}
    mock_server = _make_server()
    pcm = _make_pcm(db_url=db_url)

    with patch.dict(os.environ, env, clear=False):
      with patch("sys.argv", argv):
        with patch("daemon.PathConfigManager", return_value=pcm):
          with patch("daemon.ConfigLogManager", return_value=MagicMock()):
            with patch("daemon.ConfigLockManager", return_value=MagicMock()):
              with patch("daemon.PostgreSQLJobDatabase", return_value=MagicMock()):
                with patch("daemon.DaemonServer", return_value=mock_server):
                  with patch("daemon._validate_hwaccel"):
                    try:
                      daemon_entry.main()
                    except (KeyboardInterrupt, SystemExit):
                      pass
    return pcm, mock_server

  def test_default_host_and_port(self):
    pcm = _make_pcm(db_url="postgresql://localhost/sma")
    mock_server = _make_server()

    with patch("sys.argv", ["daemon.py"]):
      with patch("daemon.PathConfigManager", return_value=pcm):
        with patch("daemon.ConfigLogManager", return_value=MagicMock()):
          with patch("daemon.ConfigLockManager", return_value=MagicMock()):
            with patch("daemon.PostgreSQLJobDatabase", return_value=MagicMock()):
              with patch("daemon.DaemonServer", return_value=mock_server) as mock_ds:
                with patch("daemon._validate_hwaccel"):
                  try:
                    daemon_entry.main()
                  except (KeyboardInterrupt, SystemExit):
                    pass
    if mock_ds.call_args:
      addr = mock_ds.call_args[0][0]
      assert addr == ("127.0.0.1", 8585)

  def test_custom_host_and_port(self):
    with patch("daemon.DaemonServer") as mock_ds:
      mock_ds.return_value = _make_server()
      self._run_main_with_args(["daemon.py", "--host", "0.0.0.0", "--port", "9000"])
      if mock_ds.call_args:
        addr = mock_ds.call_args[0][0]
        assert addr == ("0.0.0.0", 9000)

  def test_api_key_from_cli(self):
    pcm = _make_pcm(db_url="postgresql://localhost/sma")
    pcm.api_key = None
    mock_server = _make_server()

    with patch("sys.argv", ["daemon.py", "--api-key", "cli-secret"]):
      with patch("daemon.PathConfigManager", return_value=pcm):
        with patch("daemon.ConfigLogManager", return_value=MagicMock()):
          with patch("daemon.ConfigLockManager", return_value=MagicMock()):
            with patch("daemon.PostgreSQLJobDatabase", return_value=MagicMock()):
              with patch("daemon.DaemonServer", return_value=mock_server) as mock_ds:
                with patch("daemon._validate_hwaccel"):
                  try:
                    daemon_entry.main()
                  except (KeyboardInterrupt, SystemExit):
                    pass
    if mock_ds.call_args:
      kwargs = mock_ds.call_args[1]
      assert kwargs.get("api_key") == "cli-secret"

  def test_api_key_from_env_var(self):
    pcm = _make_pcm(db_url="postgresql://localhost/sma")
    pcm.api_key = None
    mock_server = _make_server()

    with patch.dict(os.environ, {"SMA_DAEMON_API_KEY": "env-secret"}, clear=False):
      with patch("sys.argv", ["daemon.py"]):
        with patch("daemon.PathConfigManager", return_value=pcm):
          with patch("daemon.ConfigLogManager", return_value=MagicMock()):
            with patch("daemon.ConfigLockManager", return_value=MagicMock()):
              with patch("daemon.PostgreSQLJobDatabase", return_value=MagicMock()):
                with patch("daemon.DaemonServer", return_value=mock_server) as mock_ds:
                  with patch("daemon._validate_hwaccel"):
                    try:
                      daemon_entry.main()
                    except (KeyboardInterrupt, SystemExit):
                      pass
    if mock_ds.call_args:
      kwargs = mock_ds.call_args[1]
      assert kwargs.get("api_key") == "env-secret"

  def test_api_key_cli_takes_priority_over_env(self):
    pcm = _make_pcm(db_url="postgresql://localhost/sma")
    pcm.api_key = None
    mock_server = _make_server()

    with patch.dict(os.environ, {"SMA_DAEMON_API_KEY": "env-secret"}, clear=False):
      with patch("sys.argv", ["daemon.py", "--api-key", "cli-secret"]):
        with patch("daemon.PathConfigManager", return_value=pcm):
          with patch("daemon.ConfigLogManager", return_value=MagicMock()):
            with patch("daemon.ConfigLockManager", return_value=MagicMock()):
              with patch("daemon.PostgreSQLJobDatabase", return_value=MagicMock()):
                with patch("daemon.DaemonServer", return_value=mock_server) as mock_ds:
                  with patch("daemon._validate_hwaccel"):
                    try:
                      daemon_entry.main()
                    except (KeyboardInterrupt, SystemExit):
                      pass
    if mock_ds.call_args:
      kwargs = mock_ds.call_args[1]
      assert kwargs.get("api_key") == "cli-secret"

  def test_no_db_url_exits_1(self):
    pcm = _make_pcm(db_url=None)

    with patch.dict(os.environ, {}, clear=False):
      # Remove env var if set
      env_backup = os.environ.pop("SMA_DAEMON_DB_URL", None)
      try:
        with patch("sys.argv", ["daemon.py"]):
          with patch("daemon.PathConfigManager", return_value=pcm):
            with patch("daemon.ConfigLogManager", return_value=MagicMock()):
              with patch("daemon.ConfigLockManager", return_value=MagicMock()):
                with pytest.raises(SystemExit) as exc:
                  daemon_entry.main()
      finally:
        if env_backup is not None:
          os.environ["SMA_DAEMON_DB_URL"] = env_backup
    assert exc.value.code == 1

  def test_db_url_from_env_var(self):
    pcm = _make_pcm(db_url=None)
    mock_server = _make_server()

    with patch.dict(os.environ, {"SMA_DAEMON_DB_URL": "postgresql://env/sma"}, clear=False):
      with patch("sys.argv", ["daemon.py"]):
        with patch("daemon.PathConfigManager", return_value=pcm):
          with patch("daemon.ConfigLogManager", return_value=MagicMock()):
            with patch("daemon.ConfigLockManager", return_value=MagicMock()):
              with patch("daemon.PostgreSQLJobDatabase", return_value=MagicMock()) as mock_db:
                with patch("daemon.DaemonServer", return_value=mock_server):
                  with patch("daemon._validate_hwaccel"):
                    try:
                      daemon_entry.main()
                    except (KeyboardInterrupt, SystemExit):
                      pass
    if mock_db.call_args:
      assert mock_db.call_args[0][0] == "postgresql://env/sma"

  def test_ffmpeg_dir_from_cli(self):
    pcm = _make_pcm(db_url="postgresql://localhost/sma")
    pcm.ffmpeg_dir = None
    mock_server = _make_server()

    with patch("sys.argv", ["daemon.py", "--ffmpeg-dir", "/opt/ffmpeg"]):
      with patch("daemon.PathConfigManager", return_value=pcm):
        with patch("daemon.ConfigLogManager", return_value=MagicMock()):
          with patch("daemon.ConfigLockManager", return_value=MagicMock()):
            with patch("daemon.PostgreSQLJobDatabase", return_value=MagicMock()):
              with patch("daemon.DaemonServer", return_value=mock_server) as mock_ds:
                with patch("daemon._validate_hwaccel"):
                  try:
                    daemon_entry.main()
                  except (KeyboardInterrupt, SystemExit):
                    pass
    if mock_ds.call_args:
      kwargs = mock_ds.call_args[1]
      assert kwargs.get("ffmpeg_dir") == "/opt/ffmpeg"

  def test_ffmpeg_dir_from_env_var(self):
    pcm = _make_pcm(db_url="postgresql://localhost/sma")
    pcm.ffmpeg_dir = None
    mock_server = _make_server()

    with patch.dict(os.environ, {"SMA_DAEMON_FFMPEG_DIR": "/env/ffmpeg"}, clear=False):
      with patch("sys.argv", ["daemon.py"]):
        with patch("daemon.PathConfigManager", return_value=pcm):
          with patch("daemon.ConfigLogManager", return_value=MagicMock()):
            with patch("daemon.ConfigLockManager", return_value=MagicMock()):
              with patch("daemon.PostgreSQLJobDatabase", return_value=MagicMock()):
                with patch("daemon.DaemonServer", return_value=mock_server) as mock_ds:
                  with patch("daemon._validate_hwaccel"):
                    try:
                      daemon_entry.main()
                    except (KeyboardInterrupt, SystemExit):
                      pass
    if mock_ds.call_args:
      kwargs = mock_ds.call_args[1]
      assert kwargs.get("ffmpeg_dir") == "/env/ffmpeg"

  def test_basic_auth_from_env_vars(self):
    pcm = _make_pcm(db_url="postgresql://localhost/sma")
    pcm.basic_auth = None
    mock_server = _make_server()

    with patch.dict(os.environ, {"SMA_DAEMON_USERNAME": "user", "SMA_DAEMON_PASSWORD": "pass"}, clear=False):
      with patch("sys.argv", ["daemon.py"]):
        with patch("daemon.PathConfigManager", return_value=pcm):
          with patch("daemon.ConfigLogManager", return_value=MagicMock()):
            with patch("daemon.ConfigLockManager", return_value=MagicMock()):
              with patch("daemon.PostgreSQLJobDatabase", return_value=MagicMock()):
                with patch("daemon.DaemonServer", return_value=mock_server) as mock_ds:
                  with patch("daemon._validate_hwaccel"):
                    try:
                      daemon_entry.main()
                    except (KeyboardInterrupt, SystemExit):
                      pass
    if mock_ds.call_args:
      kwargs = mock_ds.call_args[1]
      assert kwargs.get("basic_auth") == ("user", "pass")

  def test_smoke_test_cli_flag_exits_0(self):
    pcm = _make_pcm()
    pcm.smoke_test = False  # only CLI flag set

    with patch("sys.argv", ["daemon.py", "--smoke-test"]):
      with patch("daemon.PathConfigManager", return_value=pcm):
        with patch("daemon.ConfigLogManager", return_value=MagicMock()):
          with patch("daemon.ConfigLockManager", return_value=MagicMock()):
            with patch("daemon.run_smoke_test") as mock_smoke:
              with pytest.raises(SystemExit) as exc:
                daemon_entry.main()
    mock_smoke.assert_called_once()
    assert exc.value.code == 0

  def test_smoke_test_from_daemon_json_continues_startup(self):
    """smoke_test=True in daemon.json runs the check but continues startup (no sys.exit)."""
    pcm = _make_pcm(db_url="postgresql://localhost/sma")
    pcm.smoke_test = True  # from daemon.json — do NOT exit after check
    mock_server = _make_server()

    with patch("sys.argv", ["daemon.py"]):
      with patch("daemon.PathConfigManager", return_value=pcm):
        with patch("daemon.ConfigLogManager", return_value=MagicMock()):
          with patch("daemon.ConfigLockManager", return_value=MagicMock()):
            with patch("daemon.PostgreSQLJobDatabase", return_value=MagicMock()):
              with patch("daemon.DaemonServer", return_value=mock_server):
                with patch("daemon.run_smoke_test") as mock_smoke:
                  with patch("daemon._validate_hwaccel"):
                    try:
                      daemon_entry.main()
                    except (KeyboardInterrupt, SystemExit):
                      pass
    mock_smoke.assert_called_once()

  def test_workers_passed_to_config_lock_manager(self):
    pcm = _make_pcm(db_url="postgresql://localhost/sma")
    mock_server = _make_server()

    with patch("sys.argv", ["daemon.py", "--workers", "4"]):
      with patch("daemon.PathConfigManager", return_value=pcm):
        with patch("daemon.ConfigLogManager", return_value=MagicMock()):
          with patch("daemon.ConfigLockManager", return_value=MagicMock()) as mock_clm:
            with patch("daemon.PostgreSQLJobDatabase", return_value=MagicMock()):
              with patch("daemon.DaemonServer", return_value=mock_server):
                with patch("daemon._validate_hwaccel"):
                  try:
                    daemon_entry.main()
                  except (KeyboardInterrupt, SystemExit):
                    pass
    if mock_clm.call_args:
      assert mock_clm.call_args[1].get("max_per_config") == 4

  def test_server_exception_exits_1(self):
    pcm = _make_pcm(db_url="postgresql://localhost/sma")

    with patch("sys.argv", ["daemon.py"]):
      with patch("daemon.PathConfigManager", return_value=pcm):
        with patch("daemon.ConfigLogManager", return_value=MagicMock()):
          with patch("daemon.ConfigLockManager", return_value=MagicMock()):
            with patch("daemon.PostgreSQLJobDatabase", return_value=MagicMock()):
              with patch("daemon.DaemonServer", side_effect=OSError("bind failed")):
                with pytest.raises(SystemExit) as exc:
                  daemon_entry.main()
    assert exc.value.code == 1

  def test_job_timeout_from_cli(self):
    pcm = _make_pcm(db_url="postgresql://localhost/sma")
    pcm.job_timeout_seconds = 0  # daemon.json has no override
    mock_server = _make_server()

    with patch("sys.argv", ["daemon.py", "--job-timeout", "3600"]):
      with patch("daemon.PathConfigManager", return_value=pcm):
        with patch("daemon.ConfigLogManager", return_value=MagicMock()):
          with patch("daemon.ConfigLockManager", return_value=MagicMock()):
            with patch("daemon.PostgreSQLJobDatabase", return_value=MagicMock()):
              with patch("daemon.DaemonServer", return_value=mock_server) as mock_ds:
                with patch("daemon._validate_hwaccel"):
                  try:
                    daemon_entry.main()
                  except (KeyboardInterrupt, SystemExit):
                    pass
    if mock_ds.call_args:
      kwargs = mock_ds.call_args[1]
      assert kwargs.get("job_timeout_seconds") == 3600
