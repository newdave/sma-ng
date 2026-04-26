"""Smoke test: end-to-end transcode of a known fixture file.

Requires ffmpeg and ffprobe binaries on PATH. Marked with the ``smoke`` pytest
mark so they can be run explicitly or excluded from fast unit-test runs:

    pytest -m smoke          # run only smoke tests
    pytest -m "not smoke"    # skip smoke tests
"""

import json
import os

import pytest

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "test1.mkv")

pytestmark = pytest.mark.smoke


def _skip_if_no_fixture():
  if not os.path.exists(FIXTURE):
    pytest.skip("tests/fixtures/test1.mkv not found")


def _skip_if_no_ffmpeg():
  import shutil

  if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
    pytest.skip("ffmpeg/ffprobe not found on PATH")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_yaml, tmp_path):
  """Return a ReadSettings instance pointed at a minimal config."""
  from resources.readsettings import ReadSettings

  yml = tmp_yaml(
    overrides={
      "base": {
        "converter": {
          "output-directory": str(tmp_path),
          "delete-original": False,
        }
      }
    }
  )
  return ReadSettings(configFile=yml)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSmokeTranscode:
  """End-to-end smoke tests using tests/fixtures/test1.mkv.

  test1.mkv: msmpeg4v2 video (854x480), mp3 audio (stereo), ~87 s, ~2.1 Mbit/s.
  Both streams require transcoding — no bypass path is taken.
  """

  def test_option_generation(self, tmp_yaml, tmp_path):
    """jsonDump() must succeed and select a valid output codec without running FFmpeg."""
    _skip_if_no_fixture()
    _skip_if_no_ffmpeg()

    from resources.mediaprocessor import MediaProcessor

    settings = _make_settings(tmp_yaml, tmp_path)
    mp = MediaProcessor(settings)

    dump_str = mp.jsonDump(FIXTURE)
    dump = json.loads(dump_str)

    # Input section must identify the source streams
    assert "input" in dump
    assert "output" in dump

    output = dump["output"]
    # Video stream must be present and mapped to a transcode codec
    assert "video" in output
    assert output["video"]["codec"] not in ("", None)
    assert output["video"]["codec"] != "copy"

    # Audio stream must be present
    assert "audio" in output
    assert len(output["audio"]) >= 1
    assert output["audio"][0]["codec"] not in ("", None)

    # FFmpeg command must be generated
    assert "ffmpeg_commands" in dump
    assert len(dump["ffmpeg_commands"]) >= 1
    cmd = dump["ffmpeg_commands"][0]
    assert "ffmpeg" in cmd
    assert "-i" in cmd

  def test_full_transcode(self, tmp_yaml, tmp_path):
    """process() must transcode test1.mkv to an MP4 file without errors."""
    _skip_if_no_fixture()
    _skip_if_no_ffmpeg()

    from resources.mediaprocessor import MediaProcessor

    settings = _make_settings(tmp_yaml, tmp_path)
    mp = MediaProcessor(settings)

    result = mp.process(FIXTURE)

    assert result is not None, "process() returned None — transcode failed"

    output_path = result.get("output")
    assert output_path is not None, "process() result missing 'output' key"
    assert os.path.exists(output_path), f"Output file not found: {output_path}"

    # Output must be a non-empty MP4
    assert output_path.endswith(".mp4"), f"Expected .mp4 output, got: {output_path}"
    assert os.path.getsize(output_path) > 0, "Output file is empty"

  def test_output_streams(self, tmp_yaml, tmp_path):
    """Transcoded output must contain at least one video and one audio stream."""
    _skip_if_no_fixture()
    _skip_if_no_ffmpeg()

    from resources.mediaprocessor import MediaProcessor

    settings = _make_settings(tmp_yaml, tmp_path)
    mp = MediaProcessor(settings)

    result = mp.process(FIXTURE)
    assert result is not None

    output_path = result["output"]
    info = mp.isValidSource(output_path)
    assert info is not None, "ffprobe could not read the output file"

    has_video = any(s.type == "video" for s in info.streams)
    has_audio = any(s.type == "audio" for s in info.streams)
    assert has_video, "Output file has no video stream"
    assert has_audio, "Output file has no audio stream"
