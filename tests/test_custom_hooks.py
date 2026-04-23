"""Tests for config/custom.py and resources/custom.py."""

import importlib
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _make_mp(is_hdr=False, valid_frame_data=True):
  mp = MagicMock()
  mp.isHDR.return_value = is_hdr
  mp.hasValidFrameData.return_value = valid_frame_data
  return mp


class TestConfigCustom:
  def test_validation_returns_true_for_non_hdr_video(self):
    from config.custom import validation

    mp = _make_mp(is_hdr=False)
    info = SimpleNamespace(video=SimpleNamespace(framedata={"hdr": True}))

    assert validation(mp, info, "/media/file.mkv", None) is True
    mp.hasValidFrameData.assert_not_called()

  def test_validation_returns_frame_data_result_for_hdr_video(self):
    from config.custom import validation

    mp = _make_mp(is_hdr=True, valid_frame_data=False)
    info = SimpleNamespace(video=SimpleNamespace(framedata={"mastering-display": "x"}))

    assert validation(mp, info, "/media/file.mkv", None) is False
    mp.hasValidFrameData.assert_called_once_with(info.video.framedata)

  def test_block_copy_hooks_return_false(self):
    from config.custom import blockAudioCopy, blockVideoCopy

    mp = _make_mp()
    stream = SimpleNamespace()

    assert blockVideoCopy(mp, stream, "/media/file.mkv") is False
    assert blockAudioCopy(mp, stream, "/media/file.mkv") is False

  def test_skip_hooks_return_false(self):
    from config.custom import skipStream, skipUA

    mp = _make_mp()
    stream = SimpleNamespace()
    info = SimpleNamespace()

    assert skipStream(mp, stream, info, "/media/file.mkv", None) is False
    assert skipUA(mp, stream, info, "/media/file.mkv", None) is False

  def test_stream_title_audio_uses_channel_count(self):
    from config.custom import streamTitle

    mp = _make_mp()
    audio_stream = SimpleNamespace(type="audio")

    assert streamTitle(mp, audio_stream, {"channels": 6}) == "Surround"
    assert streamTitle(mp, audio_stream, {"channels": 2}) == "Stereo"

  def test_stream_title_non_audio_returns_none(self):
    from config.custom import streamTitle

    mp = _make_mp()

    assert streamTitle(mp, SimpleNamespace(type="video"), {}) is None
    assert streamTitle(mp, SimpleNamespace(type="subtitle"), {}) is None
    assert streamTitle(mp, SimpleNamespace(type="attachment"), {}) is None


class TestResourcesCustom:
  def test_imports_custom_hooks_when_available(self):
    import resources.custom as custom_hooks

    importlib.reload(custom_hooks)

    assert callable(custom_hooks.validation)
    assert callable(custom_hooks.blockVideoCopy)
    assert callable(custom_hooks.blockAudioCopy)
    assert callable(custom_hooks.skipStream)
    assert callable(custom_hooks.skipUA)
    assert callable(custom_hooks.streamTitle)

  def test_sets_hooks_to_none_when_config_custom_unavailable(self):
    custom_path = Path(__file__).resolve().parent.parent / "resources" / "custom.py"

    real_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
      if name == "config.custom":
        raise ImportError("missing custom hooks")
      return real_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=fake_import):
      module_globals = runpy.run_path(str(custom_path))

    assert module_globals["validation"] is None
    assert module_globals["blockVideoCopy"] is None
    assert module_globals["blockAudioCopy"] is None
    assert module_globals["skipStream"] is None
    assert module_globals["skipUA"] is None
    assert module_globals["streamTitle"] is None
