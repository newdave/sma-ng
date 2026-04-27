"""Tests for autoprocess/autoscan.py."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from autoprocess.autoscan import _apply_path_mapping, triggerAutoscan


def _instance(**overrides):
  base = {
    "section": "main",
    "host": "autoscan.local",
    "port": 3030,
    "ssl": False,
    "webroot": "",
    "username": "",
    "password": "",
    "path": "/library/Media",
    "ignore-certs": False,
    "path-mapping": {},
  }
  base.update(overrides)
  return base


def _settings(*instances):
  return SimpleNamespace(autoscan_instances=list(instances))


class TestApplyPathMapping:
  def test_no_mapping_returns_input(self):
    assert _apply_path_mapping("/a/b/c", {}) == "/a/b/c"

  def test_longest_prefix_wins(self):
    mapping = {"/a": "/X", "/a/b": "/Y"}
    assert _apply_path_mapping("/a/b/c", mapping) == "/Y/c"

  def test_no_match_returns_input(self):
    assert _apply_path_mapping("/q/r", {"/a": "/X"}) == "/q/r"


class TestTriggerAutoscan:
  @patch("autoprocess.autoscan.requests.post")
  def test_no_instances_no_request(self, mock_post):
    triggerAutoscan(_settings(), "/library/Media/Show/file.mp4", MagicMock())
    mock_post.assert_not_called()

  @patch("autoprocess.autoscan.requests.post")
  def test_path_gate_skips_non_matching(self, mock_post):
    triggerAutoscan(
      _settings(_instance(path="/library/Media")),
      "/different/dir/file.mp4",
      MagicMock(),
    )
    mock_post.assert_not_called()

  @patch("autoprocess.autoscan.requests.post")
  def test_basic_post_to_triggers_manual(self, mock_post):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    triggerAutoscan(
      _settings(_instance()),
      "/library/Media/Show/file.mp4",
      MagicMock(),
    )
    args, kwargs = mock_post.call_args
    assert args[0] == "http://autoscan.local:3030/triggers/manual"
    assert kwargs["params"] == {"dir": "/library/Media/Show"}
    assert kwargs["auth"] is None
    assert kwargs["verify"] is True
    assert kwargs["timeout"] == 10

  @patch("autoprocess.autoscan.requests.post")
  def test_basic_auth_when_creds_set(self, mock_post):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    triggerAutoscan(
      _settings(_instance(username="u", password="p")),
      "/library/Media/Show/file.mp4",
      MagicMock(),
    )
    auth = mock_post.call_args[1]["auth"]
    assert auth is not None
    assert (auth.username, auth.password) == ("u", "p")

  @patch("autoprocess.autoscan.requests.post")
  def test_no_auth_when_only_username(self, mock_post):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    triggerAutoscan(
      _settings(_instance(username="u")),
      "/library/Media/Show/file.mp4",
      MagicMock(),
    )
    assert mock_post.call_args[1]["auth"] is None

  @patch("autoprocess.autoscan.requests.post")
  def test_ignore_certs_disables_verify(self, mock_post):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    triggerAutoscan(
      _settings(_instance(ssl=True, **{"ignore-certs": True})),
      "/library/Media/Show/file.mp4",
      MagicMock(),
    )
    assert mock_post.call_args[1]["verify"] is False

  @patch("autoprocess.autoscan.requests.post")
  def test_path_mapping_rewrites_dir(self, mock_post):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    triggerAutoscan(
      _settings(_instance(**{"path-mapping": {"/library/Media": "/data/Media"}})),
      "/library/Media/Show/file.mp4",
      MagicMock(),
    )
    assert mock_post.call_args[1]["params"]["dir"] == "/data/Media/Show"

  @patch("autoprocess.autoscan.requests.post")
  def test_one_failure_does_not_block_others(self, mock_post):
    good = MagicMock(status_code=200, text="ok")
    mock_post.side_effect = [Exception("boom"), good]
    triggerAutoscan(
      _settings(
        _instance(host="a.local"),
        _instance(host="b.local", section="other"),
      ),
      "/library/Media/Show/file.mp4",
      MagicMock(),
    )
    assert mock_post.call_count == 2

  @patch("autoprocess.autoscan.requests.post")
  def test_https_uses_https_scheme(self, mock_post):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    triggerAutoscan(
      _settings(_instance(ssl=True, port=443, webroot="/api")),
      "/library/Media/Show/file.mp4",
      MagicMock(),
    )
    assert mock_post.call_args[0][0] == "https://autoscan.local:443/api/triggers/manual"

  @patch("autoprocess.autoscan.requests.post")
  def test_non_2xx_logs_warning_does_not_raise(self, mock_post):
    mock_post.return_value = MagicMock(status_code=401, text="unauthorized")
    log = MagicMock()
    triggerAutoscan(_settings(_instance()), "/library/Media/Show/file.mp4", log)
    log.warning.assert_called()
