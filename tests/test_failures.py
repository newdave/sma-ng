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
