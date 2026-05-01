"""Tests for autoprocess/emby.py + autoprocess/jellyfin.py + the shared helper.

Covers the same shape as tests/test_autoscan.py: path gating, path-mapping,
auth header, ssl/verify, multi-instance fan-out, and graceful failure.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from autoprocess._media_server import _apply_path_mapping, trigger_refresh
from autoprocess.emby import refreshEmby
from autoprocess.jellyfin import refreshJellyfin


def _instance(kind="emby", **overrides):
  base = {
    "section": "main",
    "kind": kind,
    "host": "media.local",
    "port": 8096,
    "ssl": False,
    "webroot": "",
    "apikey": "K1",
    "path": "/library/Media",
    "refresh": True,
    "ignore-certs": False,
    "path-mapping": {},
  }
  base.update(overrides)
  return base


def _settings(emby=(), jellyfin=()):
  return SimpleNamespace(
    emby_instances=list(emby),
    jellyfin_instances=list(jellyfin),
  )


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


class TestApplyPathMapping:
  def test_no_mapping_returns_input(self):
    assert _apply_path_mapping("/a/b/c", {}) == "/a/b/c"

  def test_longest_prefix_wins(self):
    assert _apply_path_mapping("/a/b/c", {"/a": "/X", "/a/b": "/Y"}) == "/Y/c"

  def test_no_match_returns_input(self):
    assert _apply_path_mapping("/q/r", {"/a": "/X"}) == "/q/r"


class TestTriggerRefresh:
  @patch("autoprocess._media_server.requests.post")
  def test_no_instances_no_request(self, mock_post):
    trigger_refresh([], "/library/Media/Show/file.mp4", product_label="Emby", logger=MagicMock())
    mock_post.assert_not_called()

  @patch("autoprocess._media_server.requests.post")
  def test_path_gate_skips_non_matching(self, mock_post):
    trigger_refresh([_instance(path="/library/Media")], "/different/dir/file.mp4", product_label="Emby", logger=MagicMock())
    mock_post.assert_not_called()

  @patch("autoprocess._media_server.requests.post")
  def test_skips_when_apikey_missing(self, mock_post):
    log = MagicMock()
    trigger_refresh([_instance(apikey="")], "/library/Media/Show/file.mp4", product_label="Emby", logger=log)
    mock_post.assert_not_called()
    log.warning.assert_called()

  @patch("autoprocess._media_server.requests.post")
  def test_post_payload_shape(self, mock_post):
    mock_post.return_value = MagicMock(status_code=204, text="")
    trigger_refresh(
      [_instance()],
      "/library/Media/Show/file.mp4",
      product_label="Emby",
      logger=MagicMock(),
    )
    args, kwargs = mock_post.call_args
    assert args[0] == "http://media.local:8096/Library/Media/Updated"
    assert kwargs["json"] == {"Updates": [{"Path": "/library/Media/Show", "UpdateType": "Modified"}]}
    assert kwargs["headers"]["X-Emby-Token"] == "K1"
    assert kwargs["verify"] is True
    assert kwargs["timeout"] == 10

  @patch("autoprocess._media_server.requests.post")
  def test_ignore_certs_disables_verify(self, mock_post):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    trigger_refresh(
      [_instance(ssl=True, **{"ignore-certs": True})],
      "/library/Media/Show/file.mp4",
      product_label="Emby",
      logger=MagicMock(),
    )
    assert mock_post.call_args[1]["verify"] is False

  @patch("autoprocess._media_server.requests.post")
  def test_path_mapping_rewrites_dir(self, mock_post):
    mock_post.return_value = MagicMock(status_code=204, text="")
    trigger_refresh(
      [_instance(**{"path-mapping": {"/library/Media": "/data/Media"}})],
      "/library/Media/Show/file.mp4",
      product_label="Emby",
      logger=MagicMock(),
    )
    assert mock_post.call_args[1]["json"]["Updates"][0]["Path"] == "/data/Media/Show"

  @patch("autoprocess._media_server.requests.post")
  def test_one_failure_does_not_block_others(self, mock_post):
    mock_post.side_effect = [Exception("boom"), MagicMock(status_code=200, text="")]
    trigger_refresh(
      [
        _instance(host="a.local"),
        _instance(host="b.local", section="other"),
      ],
      "/library/Media/Show/file.mp4",
      product_label="Emby",
      logger=MagicMock(),
    )
    assert mock_post.call_count == 2

  @patch("autoprocess._media_server.requests.post")
  def test_https_uses_https_scheme(self, mock_post):
    mock_post.return_value = MagicMock(status_code=204, text="")
    trigger_refresh(
      [_instance(ssl=True, port=443, webroot="/api")],
      "/library/Media/Show/file.mp4",
      product_label="Emby",
      logger=MagicMock(),
    )
    assert mock_post.call_args[0][0] == "https://media.local:443/api/Library/Media/Updated"

  @patch("autoprocess._media_server.requests.post")
  def test_non_2xx_logs_warning(self, mock_post):
    mock_post.return_value = MagicMock(status_code=401, text="unauthorized")
    log = MagicMock()
    trigger_refresh([_instance()], "/library/Media/Show/file.mp4", product_label="Emby", logger=log)
    log.warning.assert_called()


# ---------------------------------------------------------------------------
# refreshEmby / refreshJellyfin entry points
# ---------------------------------------------------------------------------


class TestRefreshEmby:
  @patch("autoprocess._media_server.requests.post")
  def test_skips_when_refresh_disabled(self, mock_post):
    refreshEmby(_settings(emby=[_instance(refresh=False)]), "/library/Media/Show/file.mp4", MagicMock())
    mock_post.assert_not_called()

  @patch("autoprocess._media_server.requests.post")
  def test_skips_when_no_instances(self, mock_post):
    refreshEmby(_settings(), "/library/Media/Show/file.mp4", MagicMock())
    mock_post.assert_not_called()

  @patch("autoprocess._media_server.requests.post")
  def test_invokes_helper_when_enabled(self, mock_post):
    mock_post.return_value = MagicMock(status_code=204, text="")
    refreshEmby(_settings(emby=[_instance()]), "/library/Media/Show/file.mp4", MagicMock())
    mock_post.assert_called_once()


class TestRefreshJellyfin:
  @patch("autoprocess._media_server.requests.post")
  def test_skips_when_refresh_disabled(self, mock_post):
    refreshJellyfin(_settings(jellyfin=[_instance(kind="jellyfin", refresh=False)]), "/library/Media/Show/file.mp4", MagicMock())
    mock_post.assert_not_called()

  @patch("autoprocess._media_server.requests.post")
  def test_invokes_helper_when_enabled(self, mock_post):
    mock_post.return_value = MagicMock(status_code=204, text="")
    refreshJellyfin(_settings(jellyfin=[_instance(kind="jellyfin")]), "/library/Media/Show/file.mp4", MagicMock())
    mock_post.assert_called_once()
