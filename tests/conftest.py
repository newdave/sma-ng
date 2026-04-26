"""Shared fixtures for SMA-NG test suite."""

import logging
import os
import sys

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converter.ffmpeg import MediaFormatInfo, MediaInfo, MediaStreamInfo


@pytest.fixture
def make_stream():
  """Factory fixture for creating MediaStreamInfo objects."""

  def _make(type="video", codec="h264", index=0, **kwargs):
    s = MediaStreamInfo()
    s.type = type
    s.codec = codec
    s.index = index
    s.metadata = kwargs.pop("metadata", {"language": "eng"})
    s.disposition = kwargs.pop("disposition", {"default": True, "forced": False})
    for k, v in kwargs.items():
      setattr(s, k, v)
    return s

  return _make


@pytest.fixture
def make_format():
  """Factory fixture for creating MediaFormatInfo objects."""

  def _make(**kwargs):
    f = MediaFormatInfo()
    f.format = kwargs.get("format", "matroska,webm")
    f.bitrate = kwargs.get("bitrate", 10000000.0)
    f.duration = kwargs.get("duration", 7200.0)
    return f

  return _make


@pytest.fixture
def make_media_info(make_stream, make_format):
  """Factory fixture for creating MediaInfo objects with sensible defaults."""

  def _make(video_codec="h264", video_bitrate=8000000, video_width=1920, video_height=1080, audio_codec="aac", audio_channels=2, audio_bitrate=128000, subtitle_codec=None, total_bitrate=10000000):
    info = MediaInfo()
    info.format = make_format(bitrate=total_bitrate)

    video = make_stream(
      type="video",
      codec=video_codec,
      index=0,
      bitrate=video_bitrate,
      video_width=video_width,
      video_height=video_height,
      fps=23.976,
      pix_fmt="yuv420p",
      profile="main",
      video_level=4.1,
      field_order="progressive",
      metadata={},
      disposition={"default": True, "forced": False},
    )
    video.framedata = {}
    info.streams.append(video)

    audio = make_stream(
      type="audio",
      codec=audio_codec,
      index=1,
      bitrate=audio_bitrate,
      audio_channels=audio_channels,
      audio_samplerate=48000,
      metadata={"language": "eng"},
      disposition={"default": True, "forced": False},
    )
    info.streams.append(audio)

    if subtitle_codec:
      sub = make_stream(type="subtitle", codec=subtitle_codec, index=2, metadata={"language": "eng"}, disposition={"default": False, "forced": False})
      info.streams.append(sub)

    return info

  return _make


@pytest.fixture
def tmp_yaml(tmp_path):
  """Create a temporary ``sma-ng.yml`` for tests.

  Returns a callable accepting:

  * ``overrides``: a nested dict deep-merged onto the four-bucket default
    YAML (e.g. ``{"base": {"video": {"codec": ["h264"]}}}``).
  * ``gpu``: shorthand for ``overrides={"base": {"video": {"gpu": ...}}}``.

  Defaults mirror the legacy ``tmp_yaml`` fixture's expectations so existing
  test assertions continue to hold (audio.codec=[aac], universal audio
  enabled, single-instance Sonarr/Radarr/Plex with localhost credentials,
  etc.).
  """

  def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
      if isinstance(v, dict) and isinstance(dst.get(k), dict):
        _deep_merge(dst[k], v)
      else:
        dst[k] = v

  def _make(overrides=None, gpu=None, content=None):
    if content is not None:
      raise TypeError("tmp_yaml no longer accepts an INI string `content`. Pass `overrides=` (nested dict) instead, or build YAML inline.")

    data: dict = {
      "daemon": {},
      "base": {
        "converter": {
          "ffmpeg": "ffmpeg",
          "ffprobe": "ffprobe",
          "output-format": "mp4",
          "output-extension": "mp4",
          "ignored-extensions": ["nfo", "ds_store"],
          "delete-original": True,
          "regex-directory-replace": r"[^\w\-_\. ]",
        },
        "permissions": {"chmod": "0664", "uid": -1, "gid": -1},
        "metadata": {
          "tag": True,
          "tag-language": "eng",
          "download-artwork": "false",
          "strip-metadata": True,
        },
        "video": {
          "codec": ["h265", "h264"],
          "preset": "medium",
          "prioritize-source-pix-fmt": True,
        },
        "hdr": {
          "space": ["bt2020nc"],
          "transfer": ["smpte2084"],
          "primaries": ["bt2020"],
        },
        "audio": {
          "codec": ["aac"],
          "default-language": "eng",
          "channel-bitrate": 128,
          "aac-adtstoasc": True,
          "universal": {
            "enabled": True,
            "first-stream-only": True,
          },
        },
        "subtitle": {
          "codec": ["mov_text"],
          "default-language": "eng",
          "embed-subs": True,
        },
      },
      "services": {
        "sonarr": {"main": {"url": "http://localhost:8989", "apikey": ""}},
        "radarr": {"main": {"url": "http://localhost:7878", "apikey": ""}},
        "plex": {"main": {"url": "http://localhost:32400", "token": ""}},
      },
    }

    if gpu:
      data["base"]["video"]["gpu"] = gpu
    if overrides:
      _deep_merge(data, overrides)

    from resources import yamlconfig

    yaml_path = str(tmp_path / "sma-ng.yml")
    yamlconfig.write(yaml_path, data)
    return yaml_path

  return _make


@pytest.fixture
def daemon_log(caplog):
  """Capture log records emitted by the DAEMON logger and its children.

  The DAEMON logger has propagate=False (set by fileConfig), so caplog's
  default root-level handler never sees its records.  This fixture injects
  caplog's handler directly onto the DAEMON logger so records are captured
  regardless of propagation settings.

  Usage::

      def test_something(daemon_log):
          do_thing()
          assert "expected message" in daemon_log.text
          assert any(r.levelno == logging.ERROR for r in daemon_log.records)
  """
  daemon_logger = logging.getLogger("DAEMON")
  original_level = daemon_logger.level
  daemon_logger.setLevel(logging.DEBUG)
  daemon_logger.addHandler(caplog.handler)
  try:
    with caplog.at_level(logging.DEBUG, logger="DAEMON"):
      yield caplog
  finally:
    daemon_logger.removeHandler(caplog.handler)
    daemon_logger.setLevel(original_level)


@pytest.fixture
def job_db():
  """Yield an open PostgreSQLJobDatabase and close it after the test."""
  import os

  db_url = os.environ.get("TEST_DB_URL")
  if not db_url:
    pytest.skip("TEST_DB_URL not set")
  from daemon import PostgreSQLJobDatabase

  db = PostgreSQLJobDatabase(db_url)
  yield db
  db.close()
