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

    def run_fn(preopts, _options=None):
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

    def run_fn(preopts, options=None):
      attempts.append((list(preopts), options))
      # tier 1 fails; tier 2 (hw_alt) gets a deep-copied options whose
      # codec was rewritten to hevc_vaapi — fail that one too so we land
      # at tier 3 (sw_decode).
      vcodec = (options or {}).get("video", {}).get("codec")
      if vcodec == "hevc_vaapi":
        raise _err("hw_alt fail")
      if len(attempts) == 1:
        raise _err("hw fail")
      return None

    preopts = ["-vcodec", "hevc_qsv", "-hwaccel", "qsv", "-i", "in.mkv"]
    options = {"video": {"codec": "h265qsv"}}
    mp._attempt_ladder(preopts, options, None, run_fn)
    # tier 1 (hw) + tier 2 (hw_alt) + tier 3 (sw_decode succeeds).
    assert len(attempts) == 3
    sw_preopts = attempts[2][0]
    # sw_decode tier strips -vcodec hevc_qsv pair from preopts.
    assert "-vcodec" not in sw_preopts
    assert "hevc_qsv" not in sw_preopts
    # -hwaccel should still be present (only sw decode, hw encode kept).
    assert "-hwaccel" in sw_preopts
    # Outer options codec must NOT have been swapped (hw_alt deep-copied).
    assert options["video"]["codec"] == "h265qsv"

  def test_hw_only_policy_does_not_descend(self):
    mp = _make_mp(FallbackPolicy.HW_ONLY)

    def run_fn(_preopts, _options=None):
      raise _err("hw fail")

    with pytest.raises(FFMpegConvertError):
      mp._attempt_ladder(["-vcodec", "hevc_qsv"], {"video": {"codec": "h265qsv"}}, None, run_fn)

  def test_sw_decode_fails_with_no_vcodec_preopts_raises_first_err(self):
    """If there's no `-vcodec` in preopts to strip, tier 3 (sw_decode) can't be
    constructed and the most recent error should be re-raised."""
    mp = _make_mp(FallbackPolicy.AGGRESSIVE)

    def run_fn(_preopts, _options=None):
      raise _err("fail with no vcodec present")

    # No -vcodec/-c:v in preopts -> _strip_hw_decoder_from_preopts returns None
    # codec is a non-QSV one so hw_alt is also skipped.
    with pytest.raises(FFMpegConvertError):
      mp._attempt_ladder(["-hwaccel", "qsv"], {"video": {"codec": "libx264"}}, None, run_fn)


class TestAttemptLadderTier3FullSoftware:
  def test_full_sw_success(self):
    mp = _make_mp(FallbackPolicy.AGGRESSIVE)
    attempts = []

    def run_fn(preopts, options=None):
      attempts.append((list(preopts), (options or {}).get("video", {}).get("codec")))
      # Fail all but the last tier (full_sw).
      if len(attempts) < 4:
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
    # hw, hw_alt, sw_decode, full_sw
    assert len(attempts) == 4
    # Final tier strips QSV pipeline flags entirely.
    final_preopts = attempts[-1][0]
    for flag in ("-vcodec", "-hwaccel", "-hwaccel_output_format"):
      assert flag not in final_preopts
    # And swaps the encoder to software on the outer options.
    assert options["video"]["codec"] == "h265"

  def test_sw_decode_only_policy_stops_after_sw_decode(self):
    mp = _make_mp(FallbackPolicy.SW_DECODE_ONLY)
    attempts = []

    def run_fn(preopts, options=None):
      attempts.append(list(preopts))
      raise _err("tier %d fail" % len(attempts))

    with pytest.raises(FFMpegConvertError):
      mp._attempt_ladder(
        ["-vcodec", "hevc_qsv", "-hwaccel", "qsv"],
        {"video": {"codec": "h265qsv"}},
        None,
        run_fn,
      )
    # hw, hw_alt, sw_decode — no full_sw under SW_DECODE_ONLY.
    assert len(attempts) == 3

  def test_full_sw_unmappable_codec_raises_sw_decode_err(self):
    """If options['video']['codec'] isn't a known QSV codec the swap
    fails and the most recent error should be re-raised."""
    mp = _make_mp(FallbackPolicy.AGGRESSIVE)

    def run_fn(_preopts, _options=None):
      raise _err("fail")

    preopts = ["-vcodec", "hevc_qsv", "-hwaccel", "qsv"]
    # Encoder is some non-QSV name -> _swap_qsv_codec_to_sw and
    # _swap_qsv_codec_to_vaapi both return None.
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


class TestAttemptLadderTier2HwAlt:
  """The hw_alt tier swaps the QSV encoder for hevc_vaapi (or h264_vaapi
  etc.) while preserving the QSV decoder. Sits between hw and sw_decode."""

  def _preopts(self):
    return [
      "-vcodec",
      "hevc_qsv",
      "-hwaccel",
      "qsv",
      "-hwaccel_output_format",
      "qsv",
      "-qsv_device",
      "/dev/dri/renderD128",
      "-i",
      "in.mkv",
    ]

  def test_success_on_hw_alt_records_two_tiers(self):
    mp = _make_mp(FallbackPolicy.AGGRESSIVE)
    attempts = []

    def run_fn(preopts, options=None):
      vcodec = (options or {}).get("video", {}).get("codec")
      attempts.append((list(preopts), vcodec))
      if vcodec == "h265qsv":
        raise _err("hw fail")
      return None

    options = {"video": {"codec": "h265qsv"}}
    mp._attempt_ladder(self._preopts(), options, None, run_fn)
    assert len(attempts) == 2
    assert attempts[0][1] == "h265qsv"
    assert attempts[1][1] == "hevc_vaapi"
    # hw_alt preserves QSV decode preopts and appends VAAPI device init.
    alt_preopts = attempts[1][0]
    assert "-hwaccel" in alt_preopts and "qsv" in alt_preopts
    assert "-init_hw_device" in alt_preopts
    assert "vaapi=vaapi0:/dev/dri/renderD128" in alt_preopts
    # Outer options dict is NOT mutated by hw_alt (deep copy).
    assert options["video"]["codec"] == "h265qsv"

  def test_skip_hw_alt_when_source_not_qsv(self):
    """Non-QSV source codec must skip the hw_alt tier and fall through
    to sw_decode."""
    mp = _make_mp(FallbackPolicy.AGGRESSIVE)
    attempts = []

    def run_fn(preopts, options=None):
      vcodec = (options or {}).get("video", {}).get("codec")
      attempts.append(vcodec)
      if len(attempts) < 2:
        raise _err("hw fail")
      return None

    options = {"video": {"codec": "libx264"}}
    mp._attempt_ladder(["-vcodec", "h264", "-i", "in.mkv"], options, None, run_fn)
    # hw fails, hw_alt skipped (no QSV mapping), sw_decode runs and succeeds.
    assert len(attempts) == 2
    # All attempts saw the same (non-rewritten) codec — no hw_alt swap occurred.
    assert all(c == "libx264" for c in attempts)

  def test_hw_alt_policy_stops_after_hw_alt_failure(self):
    mp = _make_mp(FallbackPolicy.HW_ALT)
    attempts = []

    def run_fn(preopts, options=None):
      attempts.append((options or {}).get("video", {}).get("codec"))
      raise _err("everything fails")

    with pytest.raises(FFMpegConvertError):
      mp._attempt_ladder(self._preopts(), {"video": {"codec": "h265qsv"}}, None, run_fn)
    # Exactly two tiers attempted: hw, hw_alt.
    assert attempts == ["h265qsv", "hevc_vaapi"]

  def test_aggressive_continues_to_sw_decode_after_hw_alt_failure(self):
    mp = _make_mp(FallbackPolicy.AGGRESSIVE)
    attempts = []

    def run_fn(preopts, options=None):
      vcodec = (options or {}).get("video", {}).get("codec")
      attempts.append(vcodec)
      # Succeed on sw_decode (third call sees outer codec h265qsv with
      # -vcodec stripped from preopts).
      if len(attempts) >= 3:
        return None
      raise _err("fail %d" % len(attempts))

    options = {"video": {"codec": "h265qsv"}}
    mp._attempt_ladder(self._preopts(), options, None, run_fn)
    # hw (h265qsv) -> hw_alt (hevc_vaapi, deep-copied) -> sw_decode (h265qsv).
    assert attempts == ["h265qsv", "hevc_vaapi", "h265qsv"]

  def test_hw_only_policy_raises_before_hw_alt(self):
    mp = _make_mp(FallbackPolicy.HW_ONLY)
    attempts = []

    def run_fn(preopts, options=None):
      attempts.append((options or {}).get("video", {}).get("codec"))
      raise _err("hw fail")

    with pytest.raises(FFMpegConvertError):
      mp._attempt_ladder(self._preopts(), {"video": {"codec": "h265qsv"}}, None, run_fn)
    assert attempts == ["h265qsv"]
