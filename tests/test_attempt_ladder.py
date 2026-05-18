"""Tests for MediaProcessor._attempt_ladder fallback tiers.

Builds a minimal MediaProcessor via ``__new__`` so we don't need a real
config; injects a ``run_fn`` stub that raises FFMpegConvertError on the
chosen tiers and asserts which tier the ladder lands on for each
fallback-policy setting.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converter import FFMpegConvertError
from resources.config_schema import FallbackPolicy
from resources.mediaprocessor import MediaProcessor


def _make_mp(policy):
  """Build a MediaProcessor bare enough for _attempt_ladder."""
  mp = MediaProcessor.__new__(MediaProcessor)
  mp.settings = MagicMock()
  mp.settings.fallback_policy = policy
  mp.log = MagicMock()
  # The ladder calls removeFile on outputfile if it exists; stub it.
  mp.removeFile = MagicMock()
  return mp


def _err(msg="boom", output="unknown decoder error"):
  return FFMpegConvertError(msg, "ffmpeg", output)


class TestAttemptLadderTier1Success:
  def test_hw_success_only_records_one_tier(self):
    mp = _make_mp(FallbackPolicy.AGGRESSIVE)
    calls = []

    def run_fn(preopts):
      calls.append(list(preopts))
      return None

    preopts = ["-vcodec", "hevc_qsv", "-hwaccel", "qsv"]
    options = {"video": {"codec": "h265qsv"}}
    mp._attempt_ladder(preopts, options, None, run_fn)
    # Exactly one run, with the original preopts.
    assert len(calls) == 1
    assert calls[0] == preopts
    # Original options dict unchanged.
    assert options["video"]["codec"] == "h265qsv"


class TestAttemptLadderTier2SoftwareDecode:
  def test_hw_fails_sw_decode_succeeds(self):
    mp = _make_mp(FallbackPolicy.AGGRESSIVE)
    attempts = []

    def run_fn(preopts):
      attempts.append(list(preopts))
      if len(attempts) == 1:
        raise _err("hw fail")
      return None

    preopts = ["-vcodec", "hevc_qsv", "-hwaccel", "qsv", "-i", "in.mkv"]
    options = {"video": {"codec": "h265qsv"}}
    mp._attempt_ladder(preopts, options, None, run_fn)
    assert len(attempts) == 2
    # Tier 2 strips -vcodec hevc_qsv pair from preopts.
    assert "-vcodec" not in attempts[1]
    assert "hevc_qsv" not in attempts[1]
    # -hwaccel should still be present (only sw decode, hw encode kept).
    assert "-hwaccel" in attempts[1]
    # Encoder codec must NOT have been swapped to software yet.
    assert options["video"]["codec"] == "h265qsv"

  def test_hw_only_policy_does_not_descend(self):
    mp = _make_mp(FallbackPolicy.HW_ONLY)

    def run_fn(_preopts):
      raise _err("hw fail")

    with pytest.raises(FFMpegConvertError):
      mp._attempt_ladder(["-vcodec", "hevc_qsv"], {"video": {"codec": "h265qsv"}}, None, run_fn)

  def test_sw_decode_fails_with_no_vcodec_preopts_raises_first_err(self):
    """If there's no `-vcodec` in preopts to strip, tier 2 can't be
    constructed and the original tier-1 error should be re-raised."""
    mp = _make_mp(FallbackPolicy.AGGRESSIVE)

    def run_fn(_preopts):
      raise _err("hw fail, no vcodec present")

    # No -vcodec/-c:v in preopts -> _strip_hw_decoder_from_preopts returns None
    with pytest.raises(FFMpegConvertError):
      mp._attempt_ladder(["-hwaccel", "qsv"], {"video": {"codec": "h265qsv"}}, None, run_fn)


class TestAttemptLadderTier3FullSoftware:
  def test_full_sw_success(self):
    mp = _make_mp(FallbackPolicy.AGGRESSIVE)
    attempts = []

    def run_fn(preopts):
      attempts.append(list(preopts))
      if len(attempts) < 3:
        raise _err("tier %d fail" % len(attempts))
      return None

    preopts = [
      "-vcodec",
      "hevc_qsv",
      "-hwaccel",
      "qsv",
      "-hwaccel_output_format",
      "qsv",
      "-i",
      "in.mkv",
    ]
    options = {"video": {"codec": "h265qsv"}}
    mp._attempt_ladder(preopts, options, None, run_fn)
    assert len(attempts) == 3
    # Final tier strips QSV pipeline flags entirely.
    for flag in ("-vcodec", "-hwaccel", "-hwaccel_output_format"):
      assert flag not in attempts[2]
    # And swaps the encoder to software.
    assert options["video"]["codec"] == "h265"

  def test_sw_decode_only_policy_stops_after_tier2(self):
    mp = _make_mp(FallbackPolicy.SW_DECODE_ONLY)
    attempts = []

    def run_fn(preopts):
      attempts.append(list(preopts))
      raise _err("tier %d fail" % len(attempts))

    with pytest.raises(FFMpegConvertError):
      mp._attempt_ladder(
        ["-vcodec", "hevc_qsv", "-hwaccel", "qsv"],
        {"video": {"codec": "h265qsv"}},
        None,
        run_fn,
      )
    # tier1 + tier2 only.
    assert len(attempts) == 2

  def test_tier3_unmappable_codec_raises_second_err(self):
    """If options['video']['codec'] isn't a known QSV codec the swap
    fails and the tier-2 error should be re-raised."""
    mp = _make_mp(FallbackPolicy.AGGRESSIVE)

    def run_fn(_preopts):
      raise _err("fail")

    preopts = ["-vcodec", "hevc_qsv", "-hwaccel", "qsv"]
    # Encoder is some non-QSV name -> _swap_qsv_codec_to_sw returns None
    options = {"video": {"codec": "libx264"}}
    with pytest.raises(FFMpegConvertError):
      mp._attempt_ladder(preopts, options, None, run_fn)
    # Codec not rewritten.
    assert options["video"]["codec"] == "libx264"


class TestStripHelpers:
  def test_strip_hw_decoder_handles_c_v_alias(self):
    from resources.mediaprocessor import _strip_hw_decoder_from_preopts as f

    assert f(["-c:v", "h264_qsv", "-i", "x"]) == ["-i", "x"]

  def test_strip_qsv_input_pipeline_returns_none_when_nothing_stripped(self):
    from resources.mediaprocessor import _strip_qsv_input_pipeline_from_preopts as f

    assert f(["-i", "x"]) is None
    assert f([]) is None
    assert f(None) is None

  def test_swap_qsv_codec_list_variant(self):
    from resources.mediaprocessor import _swap_qsv_codec_to_sw as f

    opts = {"video": {"codec": ["hevc_qsv", "libx265"]}}
    original = f(opts)
    assert original == "hevc_qsv"
    # Only first entry rewritten; rest of the fallback chain preserved.
    assert opts["video"]["codec"] == ["h265", "libx265"]

  def test_swap_qsv_codec_no_video_block(self):
    from resources.mediaprocessor import _swap_qsv_codec_to_sw as f

    assert f({}) is None
    assert f({"video": {}}) is None
    assert f({"video": "not a dict"}) is None

  def test_swap_qsv_codec_unknown_codec(self):
    from resources.mediaprocessor import _swap_qsv_codec_to_sw as f

    opts = {"video": {"codec": "libx264"}}
    assert f(opts) is None
    # Untouched.
    assert opts["video"]["codec"] == "libx264"

  def test_swap_qsv_codec_removes_qsv_pix_fmt(self):
    from resources.mediaprocessor import _swap_qsv_codec_to_sw as f

    opts = {"video": {"codec": "hevc_qsv", "qsv_pix_fmt": "nv12"}}
    f(opts)
    assert "qsv_pix_fmt" not in opts["video"]
