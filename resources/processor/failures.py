"""FFmpeg failure classification for the transcode pipeline.

Inhabitant zero of the `resources.processor` package introduced by
docs/prps/qsv-pipeline-phase1-foundation.md. Provides a coarse but
stable taxonomy over FFmpeg stderr tails so the convert loop can:

- emit per-attempt structured telemetry with a named cause;
- drive the fallback-policy decision (HW_ONLY vs SW_DECODE_ONLY vs
  AGGRESSIVE) without re-parsing strings on every site;
- surface per-tier counters on /health.

The module is a leaf: it MUST NOT import from `resources.mediaprocessor`
or `resources.daemon` to keep import order simple.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# ffmpeg stderr is unbounded — progress output can run to tens of MB before
# the actual error tail. Only consider the last ~8KB.
TAIL_BYTES = 8192


class FfmpegFailureClass(str, Enum):
  """Coarse failure buckets for a failed ffmpeg run.

  Stable, low-cardinality identifiers chosen for /health aggregation. New
  classes may be added; existing values MUST NOT be renamed because
  external dashboards consume them.
  """

  DEVICE_OPEN_FAILED = "device_open_failed"
  DECODER_INIT_FAILED = "decoder_init_failed"
  ENCODER_INIT_FAILED = "encoder_init_failed"
  FILTER_INIT_FAILED = "filter_init_failed"
  RUNTIME_ERROR = "runtime_error"
  OTHER = "other"


@dataclass(frozen=True)
class AttemptRecord:
  """One tier of the fallback ladder. Emitted as structured log + /health metric."""

  tier: str  # "hw" | "sw_decode" | "full_sw"
  failure_class: FfmpegFailureClass | None  # None on success
  duration_ms: int


# Order matters: more-specific classes first. DEVICE_OPEN_FAILED must beat
# the generic decoder-init pattern on inputs like
# "VA-API ... failed to initialize" (which would otherwise be swallowed as
# DECODER_INIT_FAILED because the next ffmpeg log line is about the decoder).
_PATTERNS: tuple[tuple[re.Pattern[str], FfmpegFailureClass], ...] = (
  (
    re.compile(
      r"VA-API.*failed to initialize"
      r"|cannot open device.*/dev/dri"
      r"|Failed to open the?\s*(?:DRM|VA-API|QSV|MFX)? ?device"
      r"|Device creation failed"
      r"|qsv: .*MFXInit"
      r"|Cannot load libva"
      r"|No such file or directory.*/dev/dri",
      re.IGNORECASE,
    ),
    FfmpegFailureClass.DEVICE_OPEN_FAILED,
  ),
  (
    re.compile(
      r"Error parsing global options"
      r"|Unknown decoder"
      r"|Decoder.*not found"
      r"|hwaccel.*not (?:available|supported)"
      r"|Decoder \w+_qsv does not support"
      r"|Failed setup for format qsv",
      re.IGNORECASE,
    ),
    FfmpegFailureClass.DECODER_INIT_FAILED,
  ),
  (
    re.compile(
      r"Error initializing output stream"
      r"|encoder \S+ failed"
      r"|Could not open encoder"
      r"|impossible to convert between"
      r"|Specified pixel format .* is invalid"
      r"|Provided packet is too small",
      re.IGNORECASE,
    ),
    FfmpegFailureClass.ENCODER_INIT_FAILED,
  ),
  (
    re.compile(
      r"Error reinitializing filters"
      r"|No such filter"
      r"|Failed to configure (?:input|output) pad"
      r"|Cannot create the complex filtergraph"
      r"|Error opening filter",
      re.IGNORECASE,
    ),
    FfmpegFailureClass.FILTER_INIT_FAILED,
  ),
  (
    re.compile(
      r"Conversion failed!"
      r"|Error while decoding stream"
      r"|Invalid data found"
      r"|Error submitting (?:a packet|the frame) for (?:decoding|encoding)"
      r"|Error muxing a packet",
      re.IGNORECASE,
    ),
    FfmpegFailureClass.RUNTIME_ERROR,
  ),
)


def parse_ffmpeg_failure(stderr: str | bytes | None) -> FfmpegFailureClass:
  """Classify an ffmpeg stderr blob into a coarse failure bucket.

  Only the last TAIL_BYTES characters are inspected — earlier ffmpeg output
  is progress noise. Returns OTHER for any input that doesn't match a known
  pattern, is empty, or isn't a string/bytes blob (deliberate: drift in
  upstream ffmpeg messages should surface as an `other` metric, not a
  crash).
  """
  if stderr is None:
    return FfmpegFailureClass.OTHER

  if isinstance(stderr, bytes):
    try:
      text = stderr.decode("utf-8", errors="replace")
    except Exception:
      return FfmpegFailureClass.OTHER
  elif isinstance(stderr, str):
    text = stderr
  else:
    # Anything else (int, list, mock, …) is treated as unclassifiable.
    return FfmpegFailureClass.OTHER

  if not text:
    return FfmpegFailureClass.OTHER

  tail = text[-TAIL_BYTES:] if len(text) > TAIL_BYTES else text

  for pattern, klass in _PATTERNS:
    if pattern.search(tail):
      return klass
  return FfmpegFailureClass.OTHER


__all__ = ["TAIL_BYTES", "AttemptRecord", "FfmpegFailureClass", "parse_ffmpeg_failure"]
