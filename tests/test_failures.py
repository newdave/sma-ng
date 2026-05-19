"""Unit tests for resources.processor.failures.

Covers the parse_ffmpeg_failure classifier against captured stderr
fixtures plus regression tests for tail-only matching and pattern
ordering.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resources.processor.failures import (
  TAIL_BYTES,
  AttemptRecord,
  FfmpegFailureClass,
  parse_ffmpeg_failure,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ffmpeg_stderr"


@pytest.mark.parametrize(
  ("fixture", "expected"),
  [
    ("device_open_failed.txt", FfmpegFailureClass.DEVICE_OPEN_FAILED),
    ("decoder_init_failed.txt", FfmpegFailureClass.DECODER_INIT_FAILED),
    ("encoder_init_failed.txt", FfmpegFailureClass.ENCODER_INIT_FAILED),
    ("filter_init_failed.txt", FfmpegFailureClass.FILTER_INIT_FAILED),
    ("runtime_error.txt", FfmpegFailureClass.RUNTIME_ERROR),
  ],
)
def test_fixture_classification(fixture: str, expected: FfmpegFailureClass) -> None:
  """Each captured stderr fixture maps to its expected class."""
  text = (FIXTURE_DIR / fixture).read_text()
  assert parse_ffmpeg_failure(text) == expected


def test_tail_only_matching_with_progress_noise() -> None:
  """A multi-megabyte progress prelude must not hide a small error tail."""
  prelude = "frame=  100 fps= 45 q=23.0 size=    1024kB time=00:00:04.00 bitrate=2097.2kbits/s speed= 1.8x\n" * 20000
  tail = (FIXTURE_DIR / "device_open_failed.txt").read_text()
  # Prepended progress is far larger than TAIL_BYTES; class must still match.
  assert len(prelude) > TAIL_BYTES * 10
  assert parse_ffmpeg_failure(prelude + tail) == FfmpegFailureClass.DEVICE_OPEN_FAILED


def test_other_fallthrough_on_unknown_text() -> None:
  assert parse_ffmpeg_failure("the cake is a lie") == FfmpegFailureClass.OTHER


def test_other_on_empty_or_none() -> None:
  assert parse_ffmpeg_failure("") == FfmpegFailureClass.OTHER
  assert parse_ffmpeg_failure(None) == FfmpegFailureClass.OTHER


def test_bytes_input_is_decoded() -> None:
  raw = (FIXTURE_DIR / "runtime_error.txt").read_bytes()
  assert parse_ffmpeg_failure(raw) == FfmpegFailureClass.RUNTIME_ERROR


def test_invalid_bytes_returns_other_not_crash() -> None:
  # `errors="replace"` should swallow invalid sequences; OTHER is fine.
  assert parse_ffmpeg_failure(b"\x80\x81\x82 cake is also a lie") == FfmpegFailureClass.OTHER


def test_device_open_pattern_beats_generic_decoder_pattern() -> None:
  """Regression: VA-API init failures must not be misclassified as decoder.

  The device-open pattern is checked first so a tail like
  "VA-API ... failed to initialize" doesn't fall through to the generic
  DECODER_INIT_FAILED bucket.
  """
  hybrid = "[AVHWDeviceContext @ 0x55] VA-API drm/i915 device failed to initialize\nDecoder hevc_qsv not found\n"
  assert parse_ffmpeg_failure(hybrid) == FfmpegFailureClass.DEVICE_OPEN_FAILED


def test_attempt_record_is_frozen() -> None:
  record = AttemptRecord(tier="hw", failure_class=FfmpegFailureClass.RUNTIME_ERROR, duration_ms=123)
  with pytest.raises((AttributeError, TypeError)):
    record.tier = "sw_decode"  # type: ignore[misc]


def test_failure_class_values_are_stable() -> None:
  """External dashboards consume these strings. Names must not drift."""
  assert FfmpegFailureClass.DEVICE_OPEN_FAILED.value == "device_open_failed"
  assert FfmpegFailureClass.DECODER_INIT_FAILED.value == "decoder_init_failed"
  assert FfmpegFailureClass.ENCODER_INIT_FAILED.value == "encoder_init_failed"
  assert FfmpegFailureClass.FILTER_INIT_FAILED.value == "filter_init_failed"
  assert FfmpegFailureClass.RUNTIME_ERROR.value == "runtime_error"
  assert FfmpegFailureClass.OTHER.value == "other"


# ---------------------------------------------------------------------------
# diagnose_ffmpeg_failure — structured cause + hypothesis layer
# ---------------------------------------------------------------------------

from resources.processor.failures import (  # noqa: E402
  FailureDiagnosis,
  FfmpegFailureCause,
  diagnose_ffmpeg_failure,
)


class TestDiagnoseFfmpegFailure:
  def test_qsv_alignment_detected(self):
    stderr = "[hevc_qsv @ 0x7f] width 1920 height 872 not aligned\nConversion failed!\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.QSV_ALIGNMENT
    assert "aligned" in d.hypothesis.lower()
    assert "not aligned" in d.signal_line

  def test_gpu_hang_detected(self):
    stderr = "Encoding frame 12345...\n[hevc_qsv] MFX_ERR_GPU_HANG: GPU hang\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.QSV_GPU_HANG
    assert "GPU hang" in d.signal_line or "MFX_ERR_GPU_HANG" in d.signal_line

  def test_autoscale_failure_detected(self):
    stderr = "Impossible to convert between the formats supported by the filter 'auto_scale_0' and the filter 'Parsed_vpp_qsv_0'\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.QSV_AUTOSCALE_FAILURE

  def test_hevc_ref_frame_limit_detected(self):
    stderr = "More than 3 reference frames are not supported by this profile\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.HEVC_REF_FRAME_LIMIT

  def test_disk_full_detected(self):
    stderr = "av_interleaved_write_frame(): No space left on device\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.DISK_FULL

  def test_permission_denied_detected(self):
    stderr = "[mp4 @ 0x7f] Could not write header: Permission denied\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.PERMISSION_DENIED

  def test_source_unavailable_detected(self):
    stderr = "[matroska,webm @ 0x7f] Read error: Input/output error\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.SOURCE_UNAVAILABLE

  def test_truncated_input_detected(self):
    stderr = "Truncating packet of size 1234567 to 65536\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.INPUT_TRUNCATED

  def test_unsupported_profile_detected(self):
    stderr = "Encoder profile main10 not supported\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.QSV_UNSUPPORTED_PROFILE

  def test_surface_pool_exhausted_detected(self):
    stderr = "no free surfaces available in the pool\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.QSV_SURFACE_POOL_EXHAUSTED

  def test_unknown_for_unmatched_stderr(self):
    d = diagnose_ffmpeg_failure("ffmpeg version 8.1\nsome weird new error wording\n")
    assert d.cause == FfmpegFailureCause.UNKNOWN

  def test_empty_stderr(self):
    d = diagnose_ffmpeg_failure("")
    assert d.cause == FfmpegFailureCause.UNKNOWN
    assert d.signal_line == ""

  def test_none_stderr(self):
    d = diagnose_ffmpeg_failure(None)
    assert d.cause == FfmpegFailureCause.UNKNOWN

  def test_bytes_stderr_decoded(self):
    stderr = b"width 1920 height 872 not aligned\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.QSV_ALIGNMENT

  def test_non_string_input_unknown(self):
    assert diagnose_ffmpeg_failure(123).cause == FfmpegFailureCause.UNKNOWN  # type: ignore[arg-type]

  def test_as_log_dict_round_trip(self):
    d = FailureDiagnosis(
      failure_class=FfmpegFailureClass.RUNTIME_ERROR,
      cause=FfmpegFailureCause.QSV_GPU_HANG,
      hypothesis="GPU hang",
      signal_line="MFX_ERR_GPU_HANG",
    )
    out = d.as_log_dict()
    assert out["failure_class"] == "runtime_error"
    assert out["cause"] == "qsv_gpu_hang"
    assert out["hypothesis"] == "GPU hang"
    assert out["signal"] == "MFX_ERR_GPU_HANG"

  def test_nvenc_session_limit_detected(self):
    stderr = "[h264_nvenc @ 0x55] OpenEncodeSessionEx failed: out of memory (10): (no details)\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.NVENC_SESSION_LIMIT

  def test_vaapi_profile_lost_detected(self):
    stderr = "[vaapi @ 0x55] VAAPI ERROR: VA_STATUS_ERROR_PROFILE_LOST\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.VAAPI_PROFILE_LOST

  def test_av1_oom_detected(self):
    stderr = "[libsvtav1 @ 0x55] SVT-AV1: out of memory allocating frame buffer\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.AV1_ENCODER_OOM

  def test_strict_flag_required_detected(self):
    stderr = "[opus @ 0x55] Codec is experimental but experimental codecs are not enabled, add -strict experimental flag to use it\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.STRICT_FLAG_REQUIRED

  def test_pts_dts_nonmonotonic_detected(self):
    stderr = "[mp4 @ 0x55] Application provided invalid, non monotonic dts to muxer in stream 0: 12345 < 12346\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.PTS_DTS_NONMONOTONIC

  def test_bframe_copy_incompatible_detected(self):
    stderr = "[mp4 @ 0x55] track 1: codec frame size is not set\nToo many B-frames in stream copy mode\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.BFRAME_COPY_INCOMPATIBLE

  def test_invalid_frame_type_detected_as_pix_fmt(self):
    stderr = "[hevc_qsv @ 0x5f0] Invalid FrameType:0.\n[vost#0:0/hevc_qsv @ 0xff] Error submitting video frame to the encoder\nConversion failed!\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.QSV_UNSUPPORTED_PIX_FMT

  def test_audio_sample_rate_mismatch_detected(self):
    stderr = "[libfdk_aac @ 0x55] Invalid sample rate 96000\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.AUDIO_SAMPLE_RATE_MISMATCH

  def test_image_sub_to_text_detected(self):
    stderr = "[mov_text @ 0x55] Subtitle hdmv_pgs_subtitle cannot be muxed as mov_text\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.IMAGE_SUBTITLE_TO_TEXT

  def test_attachment_mux_fail_detected(self):
    stderr = "[mp4 @ 0x55] Could not find tag for codec none in stream #5, codec attachment not supported by mp4\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.ATTACHMENT_MUX_FAIL

  def test_dolby_vision_strict_detected(self):
    stderr = "[mp4 @ 0x55] dvhe profile 7 not supported without -strict unofficial\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.cause == FfmpegFailureCause.DOLBY_VISION_REQUIRES_STRICT

  def test_signal_line_extracts_full_line(self):
    stderr = "ffmpeg version 8.1 boilerplate\n[hevc_qsv @ 0xff] width 1920 height 872 not aligned to 16\nmore noise after\n"
    d = diagnose_ffmpeg_failure(stderr)
    assert d.signal_line == "[hevc_qsv @ 0xff] width 1920 height 872 not aligned to 16"
