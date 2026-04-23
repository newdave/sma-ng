"""Tests for resources/webhook_client.py - webhook submission and polling."""

import os
from unittest.mock import MagicMock, patch

from resources.webhook_client import (
  _headers,
  check_bypass,
  check_daemon_health,
  get_api_key,
  get_daemon_url,
  get_job_status,
  submit_and_wait,
  submit_job,
  submit_path,
  wait_for_completion,
)


class TestDaemonURL:
  """Test daemon URL construction from env vars."""

  def test_default_url(self):
    with patch.dict(os.environ, {}, clear=True):
      url = get_daemon_url()
      assert url == "http://127.0.0.1:8585"

  def test_custom_host(self):
    with patch.dict(os.environ, {"SMA_DAEMON_HOST": "10.0.0.5"}):
      url = get_daemon_url()
      assert "10.0.0.5" in url

  def test_custom_port(self):
    with patch.dict(os.environ, {"SMA_DAEMON_PORT": "9090"}):
      url = get_daemon_url()
      assert "9090" in url

  def test_custom_host_and_port(self):
    with patch.dict(os.environ, {"SMA_DAEMON_HOST": "sma.local", "SMA_DAEMON_PORT": "7777"}):
      assert get_daemon_url() == "http://sma.local:7777"


class TestAPIKey:
  """Test API key retrieval."""

  def test_no_key(self):
    with patch.dict(os.environ, {}, clear=True):
      assert get_api_key() == ""

  def test_key_from_env(self):
    with patch.dict(os.environ, {"SMA_DAEMON_API_KEY": "secret123"}):
      assert get_api_key() == "secret123"


class TestHeaders:
  """Test header construction."""

  def test_headers_without_key(self):
    with patch.dict(os.environ, {}, clear=True):
      h = _headers()
      assert h["Content-Type"] == "application/json"
      assert "X-API-Key" not in h

  def test_headers_with_key(self):
    with patch.dict(os.environ, {"SMA_DAEMON_API_KEY": "mykey"}):
      h = _headers()
      assert h["X-API-Key"] == "mykey"

  def test_user_agent(self):
    h = _headers()
    assert "SMA-NG" in h["User-Agent"]


class TestSubmitJob:
  """Test job submission to daemon."""

  @patch("resources.webhook_client.requests")
  def test_successful_submit(self, mock_requests):
    mock_response = MagicMock()
    mock_response.status_code = 202
    mock_response.json.return_value = {"job_id": 42, "status": "queued", "config": "/config/test.ini"}
    mock_requests.post.return_value = mock_response
    mock_requests.ConnectionError = ConnectionError

    result = submit_job("/path/to/file.mkv")
    assert result is not None
    assert result["job_id"] == 42

    call_args = mock_requests.post.call_args
    payload = call_args[1]["json"]
    assert payload["path"] == "/path/to/file.mkv"

  @patch("resources.webhook_client.requests")
  def test_submit_with_config(self, mock_requests):
    mock_response = MagicMock()
    mock_response.status_code = 202
    mock_response.json.return_value = {"job_id": 1}
    mock_requests.post.return_value = mock_response
    mock_requests.ConnectionError = ConnectionError

    submit_job("/file.mkv", config="/custom/config.ini")

    payload = mock_requests.post.call_args[1]["json"]
    assert payload["config"] == "/custom/config.ini"

  @patch("resources.webhook_client.requests")
  def test_submit_with_args(self, mock_requests):
    mock_response = MagicMock()
    mock_response.status_code = 202
    mock_response.json.return_value = {"job_id": 1}
    mock_requests.post.return_value = mock_response
    mock_requests.ConnectionError = ConnectionError

    submit_job("/file.mkv", args=["-tmdb", "603"])

    payload = mock_requests.post.call_args[1]["json"]
    assert payload["args"] == ["-tmdb", "603"]

  @patch("resources.webhook_client.requests")
  def test_submit_error_response(self, mock_requests):
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.json.return_value = {"error": "Path does not exist"}
    mock_requests.post.return_value = mock_response
    mock_requests.ConnectionError = ConnectionError

    result = submit_job("/nonexistent/file.mkv")
    assert result is None

  @patch("resources.webhook_client.requests")
  def test_submit_connection_error(self, mock_requests):
    mock_requests.post.side_effect = ConnectionError("Connection refused")
    mock_requests.ConnectionError = ConnectionError

    result = submit_job("/file.mkv")
    assert result is None

  @patch("resources.webhook_client.requests", None)
  def test_submit_no_requests_module(self):
    result = submit_job("/file.mkv")
    assert result is None


class TestGetJobStatus:
  """Test job status retrieval."""

  @patch("resources.webhook_client.requests")
  def test_get_existing_job(self, mock_requests):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": 42, "status": "running", "path": "/file.mkv"}
    mock_requests.get.return_value = mock_response

    result = get_job_status(42)
    assert result["status"] == "running"

  @patch("resources.webhook_client.requests")
  def test_get_nonexistent_job(self, mock_requests):
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_requests.get.return_value = mock_response

    result = get_job_status(9999)
    assert result is None

  @patch("resources.webhook_client.requests")
  def test_get_job_connection_error(self, mock_requests):
    mock_requests.get.side_effect = Exception("timeout")

    result = get_job_status(42)
    assert result is None


class TestWaitForCompletion:
  """Test job polling logic."""

  @patch("resources.webhook_client.get_job_status")
  @patch("resources.webhook_client.time")
  def test_immediate_completion(self, mock_time, mock_status):
    mock_time.time.return_value = 100
    mock_status.return_value = {"id": 1, "status": "completed"}

    result = wait_for_completion(1, poll_interval=1)
    assert result["status"] == "completed"

  @patch("resources.webhook_client.get_job_status")
  @patch("resources.webhook_client.time")
  def test_polls_until_complete(self, mock_time, mock_status):
    mock_time.time.side_effect = [100, 100, 105, 110]
    mock_status.side_effect = [
      {"id": 1, "status": "pending"},
      {"id": 1, "status": "running"},
      {"id": 1, "status": "completed"},
    ]

    result = wait_for_completion(1, poll_interval=1)
    assert result["status"] == "completed"
    assert mock_status.call_count == 3

  @patch("resources.webhook_client.get_job_status")
  @patch("resources.webhook_client.time")
  def test_returns_on_failure(self, mock_time, mock_status):
    mock_time.time.return_value = 100
    mock_status.return_value = {"id": 1, "status": "failed", "error": "Conversion error"}

    result = wait_for_completion(1, poll_interval=1)
    assert result["status"] == "failed"

  @patch("resources.webhook_client.get_job_status")
  @patch("resources.webhook_client.time")
  def test_timeout(self, mock_time, mock_status):
    # Time progresses past timeout
    mock_time.time.side_effect = [100, 100, 200]
    mock_status.return_value = {"id": 1, "status": "running"}

    result = wait_for_completion(1, poll_interval=1, timeout=30)
    assert result is None

  @patch("resources.webhook_client.get_job_status")
  @patch("resources.webhook_client.time")
  def test_lost_contact(self, mock_time, mock_status):
    mock_time.time.return_value = 100
    mock_status.return_value = None

    result = wait_for_completion(1, poll_interval=1)
    assert result is None

  @patch("resources.webhook_client.get_job_status")
  @patch("resources.webhook_client.time")
  def test_unknown_status(self, mock_time, mock_status):
    mock_time.time.return_value = 100
    mock_status.return_value = {"id": 1, "status": "cancelled"}

    result = wait_for_completion(1, poll_interval=1)
    assert result is None


class TestSubmitAndWait:
  """Test combined submit + wait."""

  @patch("resources.webhook_client.wait_for_completion")
  @patch("resources.webhook_client.submit_job")
  def test_success(self, mock_submit, mock_wait):
    mock_submit.return_value = {"job_id": 42, "status": "queued"}
    mock_wait.return_value = {"id": 42, "status": "completed"}

    result = submit_and_wait("/file.mkv")
    assert result["status"] == "completed"
    mock_wait.assert_called_once_with(42, logger=None, poll_interval=5, timeout=0)

  @patch("resources.webhook_client.submit_job")
  def test_submit_failure(self, mock_submit):
    mock_submit.return_value = None

    result = submit_and_wait("/file.mkv")
    assert result is None

  @patch("resources.webhook_client.submit_job")
  def test_no_job_id(self, mock_submit):
    mock_submit.return_value = {"status": "queued"}  # Missing job_id

    result = submit_and_wait("/file.mkv")
    assert result is None

  @patch("resources.webhook_client.wait_for_completion")
  @patch("resources.webhook_client.submit_job")
  def test_passes_args(self, mock_submit, mock_wait):
    mock_submit.return_value = {"job_id": 10}
    mock_wait.return_value = {"id": 10, "status": "completed"}

    submit_and_wait("/file.mkv", config="/c.ini", args=["-a"], poll_interval=2, timeout=60)

    mock_submit.assert_called_once_with("/file.mkv", config="/c.ini", args=["-a"], logger=None)
    mock_wait.assert_called_once_with(10, logger=None, poll_interval=2, timeout=60)


class TestCheckDaemonHealth:
  """Test daemon health check."""

  @patch("resources.webhook_client.requests")
  def test_healthy(self, mock_requests):
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": "ok"}
    mock_requests.get.return_value = mock_response

    assert check_daemon_health() is True

  @patch("resources.webhook_client.requests")
  def test_unhealthy_response(self, mock_requests):
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": "error"}
    mock_requests.get.return_value = mock_response

    assert check_daemon_health() is False

  @patch("resources.webhook_client.requests")
  def test_connection_error(self, mock_requests):
    mock_requests.get.side_effect = Exception("refused")

    assert check_daemon_health() is False


class TestCheckBypass:
  """Test bypass category/label checking."""

  def test_match(self):
    assert check_bypass(["sonarr", "bypass"], "bypass-movies") is True

  def test_no_match(self):
    assert check_bypass(["bypass"], "sonarr") is False

  def test_empty_list(self):
    assert check_bypass([], "anything") is False

  def test_empty_strings_ignored(self):
    assert check_bypass(["", "bypass"], "bypass") is True

  def test_prefix_match(self):
    assert check_bypass(["tv"], "tv-sonarr") is True


class TestSubmitPath:
  """Test file/directory submission helper."""

  @patch("resources.webhook_client.submit_job")
  def test_single_file(self, mock_submit, tmp_path):
    f = tmp_path / "movie.mkv"
    f.touch()
    mock_submit.return_value = {"job_id": 1}
    count = submit_path(str(f))
    assert count == 1
    mock_submit.assert_called_once()

  @patch("resources.webhook_client.submit_job")
  def test_directory(self, mock_submit, tmp_path):
    (tmp_path / "a.mkv").touch()
    (tmp_path / "b.mkv").touch()
    mock_submit.return_value = {"job_id": 1}
    count = submit_path(str(tmp_path))
    assert count == 2

  @patch("resources.webhook_client.submit_job")
  def test_missing_path_returns_zero(self, mock_submit):
    count = submit_path("/nonexistent/path")
    assert count == 0
    mock_submit.assert_not_called()
