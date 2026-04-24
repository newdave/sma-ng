"""Tests for yaml_merge.py."""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from yaml_merge import add_missing, parse_keys  # noqa: E402


def test_parse_keys_reads_yaml_sections(tmp_path):
  config = tmp_path / "sma-ng.yml"
  config.write_text("Converter:\n  ffmpeg: ffmpeg\nProfiles:\n  lq:\n    Video:\n      codec: [h264]\n")

  keys = parse_keys(str(config))

  assert keys["Converter"]["ffmpeg"] == "ffmpeg"
  assert keys["Profiles"]["lq"]["Video"]["codec"] == ["h264"]


def test_add_missing_adds_sample_keys(tmp_path):
  sample = tmp_path / "sample.yaml"
  live = tmp_path / "live.yaml"
  sample.write_text("Converter:\n  ffmpeg: ffmpeg\n  ffprobe: ffprobe\n")
  live.write_text("Converter:\n  ffmpeg: /usr/bin/ffmpeg\n")

  add_missing(str(live), str(sample))

  keys = parse_keys(str(live))
  assert keys["Converter"]["ffmpeg"] == "/usr/bin/ffmpeg"
  assert keys["Converter"]["ffprobe"] == "ffprobe"
