"""FFmpeg failure classification for the transcode pipeline.

Inhabitant zero of the `resources.processor` package. Provides a coarse
but stable taxonomy over FFmpeg stderr tails so the convert loop can:

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

  tier: str  # "hw" | "hw_alt" | "sw_decode" | "full_sw"
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


class FfmpegFailureCause(str, Enum):
  """Specific, actionable failure causes layered under :class:`FfmpegFailureClass`.

  Where ``FfmpegFailureClass`` is the coarse bucket used for /health
  aggregation, ``FfmpegFailureCause`` is the precise diagnosis the
  pipeline can act on — alignment mismatch, VBV starvation, GPU hang,
  pool exhaustion, etc. Add new causes freely; never rename existing
  values (downstream dashboards consume them).

  When future operators (or assistants reading these logs) need to
  understand *why* a transcode died, this is the first field they should
  read. The accompanying ``hypotheses`` strings in
  :class:`FailureDiagnosis` explain what the operator can do about it.
  """

  # QSV / Intel-specific
  QSV_ALIGNMENT = "qsv_alignment"
  QSV_GPU_HANG = "qsv_gpu_hang"
  QSV_DEVICE_BUSY = "qsv_device_busy"
  QSV_SURFACE_POOL_EXHAUSTED = "qsv_surface_pool_exhausted"
  QSV_UNSUPPORTED_PROFILE = "qsv_unsupported_profile"
  QSV_UNSUPPORTED_PIX_FMT = "qsv_unsupported_pix_fmt"
  QSV_AUTOSCALE_FAILURE = "qsv_autoscale_failure"
  # NVENC / VAAPI / AMF
  NVENC_SESSION_LIMIT = "nvenc_session_limit"
  VAAPI_PROFILE_LOST = "vaapi_profile_lost"
  AV1_ENCODER_OOM = "av1_encoder_oom"
  # Codec / encoder
  HEVC_REF_FRAME_LIMIT = "hevc_ref_frame_limit"
  VBV_UNDERRUN = "vbv_underrun"
  BITRATE_TOO_LOW_FOR_RESOLUTION = "bitrate_too_low_for_resolution"
  STRICT_FLAG_REQUIRED = "strict_flag_required"
  PTS_DTS_NONMONOTONIC = "pts_dts_nonmonotonic"
  BFRAME_COPY_INCOMPATIBLE = "bframe_copy_incompatible"
  # Stream content
  INPUT_TRUNCATED = "input_truncated"
  AUDIO_CHANNEL_LAYOUT_MISMATCH = "audio_channel_layout_mismatch"
  AUDIO_SAMPLE_RATE_MISMATCH = "audio_sample_rate_mismatch"
  SUBTITLE_MUX_FAIL = "subtitle_mux_fail"
  IMAGE_SUBTITLE_TO_TEXT = "image_subtitle_to_text"
  ATTACHMENT_MUX_FAIL = "attachment_mux_fail"
  HDR_TAGGING_MISMATCH = "hdr_tagging_mismatch"
  DOLBY_VISION_REQUIRES_STRICT = "dolby_vision_requires_strict"
  # Environment
  DISK_FULL = "disk_full"
  PERMISSION_DENIED = "permission_denied"
  SOURCE_UNAVAILABLE = "source_unavailable"
  # Catch-all
  UNKNOWN = "unknown"


# (regex, cause, hypothesis). The first matching entry wins. Patterns are
# tuned against real ffmpeg 7.x/8.x stderr; if upstream wording changes
# the worst case is the diagnosis falls back to UNKNOWN.
_CAUSE_PATTERNS: tuple[tuple[re.Pattern[str], FfmpegFailureCause, str], ...] = (
  # ── QSV specifics ────────────────────────────────────────────────
  (
    re.compile(r"(?:width|height).*(?:not aligned|alignment)|MFX_ERR_INVALID_VIDEO_PARAM.*(?:width|height)", re.IGNORECASE),
    FfmpegFailureCause.QSV_ALIGNMENT,
    "Source dimensions are not aligned to the QSV encoder's required boundary (typically 16 for HEVC, 32 for 10-bit). Pad/scale to mod-16 via vpp_qsv=w=W:h=H.",
  ),
  (
    re.compile(r"MFX_ERR_GPU_HANG|GPU hang|device lost|VAAPI ERROR.*HW_HANG", re.IGNORECASE),
    FfmpegFailureCause.QSV_GPU_HANG,
    "QSV/VAAPI reported a GPU hang. Common causes: bitrate cap too low for resolution+preset, b-frames/refs beyond hardware limit, driver bug. Try raising maxrate/bufsize or relaxing preset.",
  ),
  (
    re.compile(r"MFX_WRN_DEVICE_BUSY|Device is busy", re.IGNORECASE),
    FfmpegFailureCause.QSV_DEVICE_BUSY,
    "QSV device returned busy. Reduce concurrent transcodes or lower extra_hw_frames.",
  ),
  (
    re.compile(r"no free surfaces|MFX_ERR_MORE_(?:DATA|SURFACE).*surface|Surface pool", re.IGNORECASE),
    FfmpegFailureCause.QSV_SURFACE_POOL_EXHAUSTED,
    "QSV surface pool exhausted. Increase -extra_hw_frames, or reduce -async_depth / look_ahead_depth.",
  ),
  (
    re.compile(r"(?:Dolby Vision|dvhe|dvh1).*(?:strict|unofficial)|(?:dvhe|dvh1|Dolby Vision).*profile.*not.*supported", re.IGNORECASE),
    FfmpegFailureCause.DOLBY_VISION_REQUIRES_STRICT,
    "Dolby Vision profile requires -strict unofficial in mp4. SMA-NG handles this when isDolbyVision detects the side-data.",
  ),
  (
    re.compile(r"profile.*(?:not supported|unsupported)|MFX_ERR_INVALID_VIDEO_PARAM.*profile", re.IGNORECASE),
    FfmpegFailureCause.QSV_UNSUPPORTED_PROFILE,
    "Encoder profile not supported (e.g. main10 on a hardware generation that only does main). Drop to main and 8-bit pix_fmt.",
  ),
  (
    re.compile(r"Specified pixel format .* is invalid|unsupported pixel format|Invalid FrameType", re.IGNORECASE),
    FfmpegFailureCause.QSV_UNSUPPORTED_PIX_FMT,
    "Pix-fmt not supported by the encoder, or the vpp_qsv filter handed the encoder a surface whose bit-depth doesn't match the chosen profile (e.g. P010 surfaces into an 8-bit Main profile). Pin `format=nv12` (or p010le) on vpp_qsv to match the encoder profile.",
  ),
  (
    re.compile(r"Impossible to convert between.*auto_scale", re.IGNORECASE),
    FfmpegFailureCause.QSV_AUTOSCALE_FAILURE,
    "ffmpeg 8.x inserted auto_scale between the decoder and QSV encoder; QSV surfaces can't pass through auto_scale. Inject vpp_qsv (already handled by _qsv_passthrough_filter).",
  ),
  # ── NVENC / VAAPI / AMF ──────────────────────────────────────────
  (
    re.compile(r"OpenEncodeSessionEx failed|Out of memory.*nvenc|maximum.*\d+.*concurrent.*encoding session", re.IGNORECASE),
    FfmpegFailureCause.NVENC_SESSION_LIMIT,
    "NVENC concurrent encoding-session limit reached (consumer GPUs cap at 3). Reduce daemon worker count or use Linux unlocked-NVENC patch.",
  ),
  (
    re.compile(r"VAAPI.*PROFILE_LOST|vaapi.*Invalid VA-API session", re.IGNORECASE),
    FfmpegFailureCause.VAAPI_PROFILE_LOST,
    "VAAPI session was invalidated, usually after a driver reload or suspend. Restart the daemon and retry.",
  ),
  (
    re.compile(r"SVT-AV1.*(?:out of memory|allocation failed)|libaom.*failed to allocate", re.IGNORECASE),
    FfmpegFailureCause.AV1_ENCODER_OOM,
    "AV1 software encoder ran out of memory. Lower preset (higher number = faster + less RAM), or transcode 2160p sources to lower resolution first.",
  ),
  # ── Codec / encoder ──────────────────────────────────────────────
  (
    re.compile(r"more (?:than \d+ )?reference frames|MaxNumRefFrame", re.IGNORECASE),
    FfmpegFailureCause.HEVC_REF_FRAME_LIMIT,
    "HEVC reference frame count exceeds hardware/profile limit. Lower -refs / ref_frames.",
  ),
  (
    re.compile(r"VBV (?:underflow|underrun)|buffer underflow|rc_buffer_size", re.IGNORECASE),
    FfmpegFailureCause.VBV_UNDERRUN,
    "VBV buffer underflowed. The maxrate/bufsize/preset combination starves the encoder. Raise maxrate, increase bufsize to 2x maxrate, or relax preset.",
  ),
  (
    re.compile(r"-strict.*experimental|requires -?strict.*(?:experimental|unofficial)|use.*-strict.*-?2|Use.*experimental.*flag", re.IGNORECASE),
    FfmpegFailureCause.STRICT_FLAG_REQUIRED,
    "Codec or container requires `-strict experimental` or `-strict unofficial`. SMA-NG adds this automatically for known cases (truehd/dts in mp4, Dolby Vision); if a new codec needs it the adaptive pre-flight should be extended.",
  ),
  (
    re.compile(r"non[- ]monotonic.*(?:DTS|PTS)|DTS.*<.*PTS|PTS.*<.*DTS|invalid (?:DTS|PTS)", re.IGNORECASE),
    FfmpegFailureCause.PTS_DTS_NONMONOTONIC,
    "PTS/DTS ordering went non-monotonic — usually a VFR Matroska source remuxed as mp4. SMA-NG should pass -fps_mode passthrough (or -vsync 0) to preserve frame timestamps.",
  ),
  (
    re.compile(r"too many B[- ]frames|B[- ]frames.*not (?:allowed|supported).*copy|cannot copy.*B[- ]frame", re.IGNORECASE),
    FfmpegFailureCause.BFRAME_COPY_INCOMPATIBLE,
    "Stream-copy chose a B-frame structure the target container can't take. Force a re-encode (vcodec != copy) for this stream.",
  ),
  # ── Stream content ───────────────────────────────────────────────
  (
    re.compile(r"Truncating packet|truncated.*input|premature end of file", re.IGNORECASE),
    FfmpegFailureCause.INPUT_TRUNCATED,
    "Source file ended unexpectedly. Likely the source mount went away mid-read or the file is incomplete. Verify the source path is still mounted and bytes match.",
  ),
  (
    re.compile(r"channel layout|MFX_WRN_OUT_OF_RANGE.*channel", re.IGNORECASE),
    FfmpegFailureCause.AUDIO_CHANNEL_LAYOUT_MISMATCH,
    "Audio channel layout couldn't be negotiated. Force -ac N or pick a different audio codec.",
  ),
  (
    re.compile(r"Invalid sample rate|sample rate \d+ Hz not supported|libfdk_aac.*sample.*rate", re.IGNORECASE),
    FfmpegFailureCause.AUDIO_SAMPLE_RATE_MISMATCH,
    "Audio sample rate not supported by encoder (e.g. libfdk_aac wants 48 kHz). Auto-resample via -ar 48000 on the audio stream.",
  ),
  (
    re.compile(r"image.*subtitle.*(?:cannot|not).*text|mov_text.*image|hdmv_pgs|dvd_subtitle.*mov_text", re.IGNORECASE),
    FfmpegFailureCause.IMAGE_SUBTITLE_TO_TEXT,
    "Tried to mux a bitmap subtitle (PGS / VOBSUB / DVB) into a text-subtitle codec (mov_text). Promote to external sidecar via OCR or drop the stream — direct conversion is impossible.",
  ),
  (
    re.compile(r"Subtitle encoding (?:not|currently) supported|subtitle\(s\) too large|Could not write header.*subtitle", re.IGNORECASE),
    FfmpegFailureCause.SUBTITLE_MUX_FAIL,
    "Subtitle stream couldn't be muxed (size/codec). Drop the offending sub or convert to a different format.",
  ),
  (
    re.compile(r"Attachment.*(?:not supported|cannot be muxed)|stream.*attachment.*invalid", re.IGNORECASE),
    FfmpegFailureCause.ATTACHMENT_MUX_FAIL,
    "Attachment stream (e.g. embedded font/cover art) can't be muxed into the target container. Skip with `-map -0:t`.",
  ),
  (
    re.compile(r"bt2020|smpte2084|HDR.*metadata|color_(?:primaries|transfer|space).*conflict", re.IGNORECASE),
    FfmpegFailureCause.HDR_TAGGING_MISMATCH,
    "HDR metadata conflict between input and output. Check that SDR coercion is enabled and the output isn't being tagged with bt2020/smpte2084 for a bt709 source.",
  ),
  # ── Environment ──────────────────────────────────────────────────
  (
    re.compile(r"No space left|ENOSPC", re.IGNORECASE),
    FfmpegFailureCause.DISK_FULL,
    "Output filesystem is full. Free space or change output_directory.",
  ),
  (
    re.compile(r"Permission denied|EACCES", re.IGNORECASE),
    FfmpegFailureCause.PERMISSION_DENIED,
    "Filesystem permission denied. Check base.permissions.chmod (must be quoted octal like '0664') and the SMA process UID/GID against the mount.",
  ),
  (
    re.compile(r"No such file or directory|ENOENT|Input/output error|Transport endpoint", re.IGNORECASE),
    FfmpegFailureCause.SOURCE_UNAVAILABLE,
    "Source vanished mid-transcode. Usually a mergerfs/rclone mount reconnect. Check that the source path still resolves.",
  ),
)


@dataclass(frozen=True)
class FailureDiagnosis:
  """Structured diagnosis of an ffmpeg failure.

  Designed to be JSON-serialised verbatim into the daemon log so future
  readers (humans or AI assistants) can act on the diagnosis without
  re-parsing prose stderr. Always populated — falls back to UNKNOWN
  cause when no pattern matched.
  """

  failure_class: FfmpegFailureClass
  cause: FfmpegFailureCause
  hypothesis: str
  signal_line: str  # the stderr line that matched, or "" if none

  def as_log_dict(self) -> dict:
    return {
      "failure_class": self.failure_class.value,
      "cause": self.cause.value,
      "hypothesis": self.hypothesis,
      "signal": self.signal_line,
    }


def diagnose_ffmpeg_failure(stderr: str | bytes | None) -> FailureDiagnosis:
  """Return a structured diagnosis layered over :func:`parse_ffmpeg_failure`.

  Walks ``_CAUSE_PATTERNS`` against the stderr tail and returns the first
  matching cause + a human-readable hypothesis. Falls back to UNKNOWN
  with an empty hypothesis when nothing matches — keep the diagnosis
  visible in the log even when the cause can't be pinned, so the
  operator at least sees the failure class and the signal line.
  """
  failure_class = parse_ffmpeg_failure(stderr)
  if stderr is None:
    return FailureDiagnosis(failure_class, FfmpegFailureCause.UNKNOWN, "", "")

  if isinstance(stderr, bytes):
    try:
      text = stderr.decode("utf-8", errors="replace")
    except Exception:
      return FailureDiagnosis(failure_class, FfmpegFailureCause.UNKNOWN, "", "")
  elif isinstance(stderr, str):
    text = stderr
  else:
    return FailureDiagnosis(failure_class, FfmpegFailureCause.UNKNOWN, "", "")

  if not text:
    return FailureDiagnosis(failure_class, FfmpegFailureCause.UNKNOWN, "", "")

  tail = text[-TAIL_BYTES:] if len(text) > TAIL_BYTES else text
  for pattern, cause, hypothesis in _CAUSE_PATTERNS:
    m = pattern.search(tail)
    if m:
      # Extract the whole stderr line containing the match so the log
      # shows a useful excerpt, not just the matched fragment.
      line_start = tail.rfind("\n", 0, m.start()) + 1
      line_end = tail.find("\n", m.end())
      line = tail[line_start : line_end if line_end != -1 else len(tail)].strip()
      return FailureDiagnosis(failure_class, cause, hypothesis, line)
  return FailureDiagnosis(failure_class, FfmpegFailureCause.UNKNOWN, "", "")


class FailureCategory(str, Enum):
  """Operator-facing failure bucket. Coarser than :class:`FfmpegFailureClass`
  and :class:`FfmpegFailureCause`; designed to answer "is this a config /
  source media / hardware / disk problem?" without operators learning the
  raw enum vocabulary.

  Bounded set: 6 values. Used as a Prometheus label — never add an
  unbounded variant. The mapping from every existing FfmpegFailureClass +
  FfmpegFailureCause + worker sentinel is enforced by a drift-guard test
  (see ``tests/test_failure_categorization.py``); a new enum value that
  isn't mapped lands as ``UNKNOWN`` AND fails CI.
  """

  CONFIG = "config"
  SOURCE_MEDIA = "source_media"
  HARDWARE = "hardware"
  DISK = "disk"
  SYSTEM = "system"
  UNKNOWN = "unknown"


# Worker-emitted sentinel strings for failures that never reach ffmpeg.
# Documented as part of the public contract because the worker passes
# them through to ``fail_job(failure_cause=...)``.
WORKER_SENTINEL_PATH_MISSING = "path_missing"
WORKER_SENTINEL_INVALID_ARGS = "invalid_args"
WORKER_SENTINEL_PROCESS_FAILED = "process_failed"
WORKER_SENTINEL_EXCEPTION = "exception"
# Pre-ffmpeg refusal: the worker checked the output filesystem and decided
# there isn't enough headroom to risk the job. Sibling of
# ``FfmpegFailureCause.DISK_FULL`` (which is an ffmpeg-stderr classification)
# but raised before ffmpeg is even invoked.
WORKER_SENTINEL_DISK_PRESSURE = "disk_pressure"

# Every value in :class:`FfmpegFailureClass`, :class:`FfmpegFailureCause`,
# and the worker sentinels must appear here. The drift-guard test
# enumerates the enums and fails if any value is missing or maps to
# UNKNOWN — that is the only safeguard against silent
# mis-categorisation when a new enum value lands later.
_FAILURE_CATEGORY_MAP: dict[str, FailureCategory] = {
  # ── FfmpegFailureClass ────────────────────────────────────────────
  FfmpegFailureClass.DEVICE_OPEN_FAILED.value: FailureCategory.HARDWARE,
  FfmpegFailureClass.DECODER_INIT_FAILED.value: FailureCategory.HARDWARE,
  FfmpegFailureClass.ENCODER_INIT_FAILED.value: FailureCategory.HARDWARE,
  FfmpegFailureClass.FILTER_INIT_FAILED.value: FailureCategory.HARDWARE,
  FfmpegFailureClass.RUNTIME_ERROR.value: FailureCategory.SYSTEM,
  FfmpegFailureClass.OTHER.value: FailureCategory.SYSTEM,
  # ── FfmpegFailureCause: QSV hardware faults ───────────────────────
  FfmpegFailureCause.QSV_ALIGNMENT.value: FailureCategory.HARDWARE,
  FfmpegFailureCause.QSV_GPU_HANG.value: FailureCategory.HARDWARE,
  FfmpegFailureCause.QSV_DEVICE_BUSY.value: FailureCategory.HARDWARE,
  FfmpegFailureCause.QSV_SURFACE_POOL_EXHAUSTED.value: FailureCategory.HARDWARE,
  # ── FfmpegFailureCause: profile / pix-fmt config mismatches ───────
  FfmpegFailureCause.QSV_UNSUPPORTED_PROFILE.value: FailureCategory.CONFIG,
  FfmpegFailureCause.QSV_UNSUPPORTED_PIX_FMT.value: FailureCategory.CONFIG,
  FfmpegFailureCause.QSV_AUTOSCALE_FAILURE.value: FailureCategory.CONFIG,
  # ── FfmpegFailureCause: other GPU vendor ──────────────────────────
  FfmpegFailureCause.NVENC_SESSION_LIMIT.value: FailureCategory.HARDWARE,
  FfmpegFailureCause.VAAPI_PROFILE_LOST.value: FailureCategory.HARDWARE,
  FfmpegFailureCause.AV1_ENCODER_OOM.value: FailureCategory.HARDWARE,
  # ── FfmpegFailureCause: codec / encoder config knobs ──────────────
  FfmpegFailureCause.HEVC_REF_FRAME_LIMIT.value: FailureCategory.CONFIG,
  FfmpegFailureCause.BITRATE_TOO_LOW_FOR_RESOLUTION.value: FailureCategory.CONFIG,
  FfmpegFailureCause.STRICT_FLAG_REQUIRED.value: FailureCategory.CONFIG,
  FfmpegFailureCause.BFRAME_COPY_INCOMPATIBLE.value: FailureCategory.CONFIG,
  FfmpegFailureCause.HDR_TAGGING_MISMATCH.value: FailureCategory.CONFIG,
  FfmpegFailureCause.DOLBY_VISION_REQUIRES_STRICT.value: FailureCategory.CONFIG,
  # ── FfmpegFailureCause: source-media issues ───────────────────────
  FfmpegFailureCause.VBV_UNDERRUN.value: FailureCategory.SOURCE_MEDIA,
  FfmpegFailureCause.PTS_DTS_NONMONOTONIC.value: FailureCategory.SOURCE_MEDIA,
  FfmpegFailureCause.INPUT_TRUNCATED.value: FailureCategory.SOURCE_MEDIA,
  FfmpegFailureCause.AUDIO_CHANNEL_LAYOUT_MISMATCH.value: FailureCategory.SOURCE_MEDIA,
  FfmpegFailureCause.AUDIO_SAMPLE_RATE_MISMATCH.value: FailureCategory.SOURCE_MEDIA,
  FfmpegFailureCause.SUBTITLE_MUX_FAIL.value: FailureCategory.SOURCE_MEDIA,
  FfmpegFailureCause.IMAGE_SUBTITLE_TO_TEXT.value: FailureCategory.SOURCE_MEDIA,
  FfmpegFailureCause.ATTACHMENT_MUX_FAIL.value: FailureCategory.SOURCE_MEDIA,
  FfmpegFailureCause.SOURCE_UNAVAILABLE.value: FailureCategory.SOURCE_MEDIA,
  # ── FfmpegFailureCause: filesystem ────────────────────────────────
  FfmpegFailureCause.DISK_FULL.value: FailureCategory.DISK,
  FfmpegFailureCause.PERMISSION_DENIED.value: FailureCategory.DISK,
  # ── FfmpegFailureCause: catch-all ─────────────────────────────────
  FfmpegFailureCause.UNKNOWN.value: FailureCategory.SYSTEM,
  # ── Worker sentinels ──────────────────────────────────────────────
  WORKER_SENTINEL_PATH_MISSING: FailureCategory.SOURCE_MEDIA,
  WORKER_SENTINEL_INVALID_ARGS: FailureCategory.CONFIG,
  WORKER_SENTINEL_PROCESS_FAILED: FailureCategory.SYSTEM,
  WORKER_SENTINEL_EXCEPTION: FailureCategory.SYSTEM,
  WORKER_SENTINEL_DISK_PRESSURE: FailureCategory.DISK,
}


def categorize_failure(cause_or_class: str | None) -> FailureCategory:
  """Resolve a raw failure value (FfmpegFailureClass / FfmpegFailureCause
  string, or worker sentinel) to an operator-facing :class:`FailureCategory`.

  Returns ``UNKNOWN`` only for ``None`` or unmapped strings — the
  drift-guard test ensures every documented enum value maps to a
  non-UNKNOWN category. An unmapped string in production is treated as
  drift to surface but not crash on.
  """
  if cause_or_class is None:
    return FailureCategory.UNKNOWN
  return _FAILURE_CATEGORY_MAP.get(str(cause_or_class), FailureCategory.UNKNOWN)


__all__ = [
  "TAIL_BYTES",
  "WORKER_SENTINEL_DISK_PRESSURE",
  "WORKER_SENTINEL_EXCEPTION",
  "WORKER_SENTINEL_INVALID_ARGS",
  "WORKER_SENTINEL_PATH_MISSING",
  "WORKER_SENTINEL_PROCESS_FAILED",
  "AttemptRecord",
  "FailureCategory",
  "FailureDiagnosis",
  "FfmpegFailureCause",
  "FfmpegFailureClass",
  "categorize_failure",
  "diagnose_ffmpeg_failure",
  "parse_ffmpeg_failure",
]
