"""Stream-copy repair for damaged media. Never transcodes.

Probes each file with FFprobe and fixes only three kinds of damage, all by
remuxing with ``-c copy``:

- **data streams**: ``tmcd`` timecode tracks, stray ``bin_data`` tracks and
  streams FFprobe cannot classify. The QuickTime chapter text track is not
  damage: it is how MP4 stores chapters and the muxer rewrites it from the
  chapter list on every remux.
- **artwork**: attached pictures, cover art that an older repair remux demoted
  to a real (``mp4v``-tagged) video track, and image attachments (MKV).
  Font attachments are kept.
- **aspect ratio**: outputs stretched by the former QSV alignment bug (e.g.
  3840x2176 with square pixels) get a corrective display aspect ratio on the
  primary video stream. The resampled rows stay in the bitstream; removing
  them needs a re-encode, which this module never does.

Files with none of these problems are reported as ``normal`` and left alone.
A repaired copy is written next to the original, re-probed, and only swapped
in when its kept streams match the source and it diagnoses as normal; any
failure leaves the original untouched.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

from resources.library_audit.probes import HYBRID_DIMS_TABLE

REPAIRABLE_EXTS = frozenset({".mp4", ".m4v", ".mov", ".mkv"})
MP4_FAMILY_EXTS = frozenset({".mp4", ".m4v", ".mov"})
TEMP_MARKER = ".repairing"

# Still-image codecs that only appear as video streams when they carry cover art.
IMAGE_VIDEO_CODECS = frozenset({"mjpeg", "png", "bmp", "gif", "webp", "tiff"})
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
KNOWN_STREAM_TYPES = frozenset({"video", "audio", "subtitle", "attachment", "data"})

# The only ffmpeg options a repair may emit. Anything else (encoders, filters,
# bitrate, scaling) would mean re-encoding and is rejected before ffmpeg runs.
_ALLOWED_OPTIONS = frozenset({"-i", "-map", "-c", "-aspect:v:0", "-write_tmcd", "-movflags"})

# Duration drift tolerated between source and repaired output (seconds).
_DURATION_TOLERANCE = 1.0

STATUS_NORMAL = "normal"
STATUS_REPAIRED = "repaired"
STATUS_WOULD_REPAIR = "would_repair"
STATUS_FAILED = "failed"


class RepairError(Exception):
  """Raised when a repair cannot be planned, run or verified."""


@dataclass
class RepairPlan:
  """What is wrong with one file and which streams survive the remux."""

  path: str
  keep: list[int] = field(default_factory=list)
  drop_data: list[int] = field(default_factory=list)
  drop_artwork: list[int] = field(default_factory=list)
  dar: str | None = None

  @property
  def needs_repair(self) -> bool:
    return bool(self.drop_data or self.drop_artwork or self.dar)

  def describe(self) -> str:
    parts = []
    if self.drop_data:
      parts.append("data streams %s" % ",".join(str(i) for i in self.drop_data))
    if self.drop_artwork:
      parts.append("artwork streams %s" % ",".join(str(i) for i in self.drop_artwork))
    if self.dar:
      parts.append("display aspect %s" % self.dar)
    return "; ".join(parts) if parts else "normal"


@dataclass
class RepairResult:
  path: str
  status: str
  detail: str = ""


def probe(path: str, ffprobe_path: str, timeout: int = 120) -> dict[str, Any]:
  """Return FFprobe's JSON view (format, streams, chapters) of *path*."""
  cmd = [ffprobe_path, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "-show_chapters", path]
  try:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
  except (OSError, subprocess.TimeoutExpired) as exc:
    raise RepairError("ffprobe failed: %s" % exc) from exc
  if proc.returncode != 0:
    raise RepairError("ffprobe exited %d: %s" % (proc.returncode, proc.stderr.strip()[:300]))
  try:
    data = json.loads(proc.stdout or "{}")
  except ValueError as exc:
    raise RepairError("ffprobe returned invalid JSON: %s" % exc) from exc
  if not data.get("streams"):
    raise RepairError("ffprobe found no streams")
  return data


def _ext(path: str) -> str:
  return os.path.splitext(path)[1].lower()


def _attached_pic(stream: dict) -> bool:
  return bool((stream.get("disposition") or {}).get("attached_pic"))


def _primary_video_index(streams: list[dict]) -> int | None:
  """Index of the main picture: the first non-attached, non-still-image video
  stream, falling back to the first non-attached video stream."""
  videos = [s for s in streams if s.get("codec_type") == "video" and not _attached_pic(s)]
  for s in videos:
    if s.get("codec_name") not in IMAGE_VIDEO_CODECS:
      return s.get("index")
  return videos[0].get("index") if videos else None


def _is_artwork(stream: dict, primary: int | None) -> bool:
  kind = stream.get("codec_type")
  if kind == "video":
    if stream.get("index") == primary:
      return False
    return _attached_pic(stream) or stream.get("codec_name") in IMAGE_VIDEO_CODECS
  if kind == "attachment":
    tags = {k.lower(): str(v).lower() for k, v in (stream.get("tags") or {}).items()}
    return tags.get("mimetype", "").startswith("image/") or tags.get("filename", "").endswith(IMAGE_EXTENSIONS)
  return False


def _is_chapter_track(stream: dict, has_chapters: bool, mp4_family: bool) -> bool:
  return mp4_family and has_chapters and stream.get("codec_type") == "data" and stream.get("codec_name") == "bin_data"


def _hybrid_dar(stream: dict | None) -> str | None:
  """Corrective DAR for a stretched primary video stream, or None."""
  if not stream:
    return None
  width, height = stream.get("width"), stream.get("height")
  if not isinstance(width, int) or not isinstance(height, int):
    return None
  true_height = HYBRID_DIMS_TABLE.get((width, height))
  if not true_height:
    return None
  sar = stream.get("sample_aspect_ratio")
  if sar and sar not in ("1:1", "0:1"):
    return None  # display aspect already corrected (or genuinely anamorphic)
  return "%d:%d" % (width, true_height)


def diagnose(path: str, data: dict[str, Any]) -> RepairPlan:
  """Classify every stream in *data* (FFprobe JSON) as kept or dropped."""
  streams = data.get("streams") or []
  mp4_family = _ext(path) in MP4_FAMILY_EXTS
  has_chapters = bool(data.get("chapters"))
  primary = _primary_video_index(streams)
  plan = RepairPlan(path=path)
  for s in streams:
    index = s.get("index")
    kind = s.get("codec_type")
    if kind not in KNOWN_STREAM_TYPES:
      plan.drop_data.append(index)
    elif kind == "data":
      # The chapter track is rewritten by the muxer from the chapter list, so
      # it is never mapped, but it is not damage either.
      if not _is_chapter_track(s, has_chapters, mp4_family):
        plan.drop_data.append(index)
    elif _is_artwork(s, primary):
      plan.drop_artwork.append(index)
    else:
      plan.keep.append(index)
  primary_stream = next((s for s in streams if s.get("index") == primary), None)
  plan.dar = _hybrid_dar(primary_stream)
  return plan


def build_opts(plan: RepairPlan, source: str) -> list[str]:
  """FFmpeg options for the repair remux. Stream copy only."""
  opts = ["-i", source]
  for index in plan.keep:
    opts.extend(["-map", "0:%d" % index])
  opts.extend(["-c", "copy"])
  if plan.dar:
    opts.extend(["-aspect:v:0", plan.dar])
  if _ext(plan.path) in MP4_FAMILY_EXTS:
    # Without -write_tmcd 0 the muxer rebuilds a dropped tmcd track from the
    # video's timecode tag; +faststart keeps moov at the front like SMA output.
    opts.extend(["-write_tmcd", "0", "-movflags", "+faststart"])
  assert_stream_copy(opts)
  return opts


def assert_stream_copy(opts: list[str]) -> None:
  """Raise RepairError unless *opts* is a pure stream-copy remux."""
  flags = [o for i, o in enumerate(opts) if o.startswith("-") and (i == 0 or opts[i - 1] not in _ALLOWED_OPTIONS)]
  bad = [f for f in flags if f not in _ALLOWED_OPTIONS]
  if bad:
    raise RepairError("refusing non stream-copy options: %s" % " ".join(bad))
  codecs = [opts[i + 1] for i, o in enumerate(opts[:-1]) if o == "-c"]
  if codecs != ["copy"]:
    raise RepairError("refusing codec selection other than a single '-c copy'")


def _signature(data: dict[str, Any], indexes: list[int] | None = None) -> list[tuple]:
  streams = data.get("streams") or []
  if indexes is not None:
    by_index = {s.get("index"): s for s in streams}
    streams = [by_index[i] for i in indexes if i in by_index]
  else:
    streams = [s for s in streams if s.get("codec_type") in ("video", "audio", "subtitle", "attachment")]
  return [(s.get("codec_type"), s.get("codec_name"), s.get("width"), s.get("height"), s.get("channels")) for s in streams]


def _duration(data: dict[str, Any]) -> float | None:
  value = (data.get("format") or {}).get("duration")
  try:
    return float(value) if value is not None else None
  except (TypeError, ValueError):
    return None


def verify(plan: RepairPlan, source: dict[str, Any], output: dict[str, Any], output_path: str) -> None:
  """Raise RepairError unless *output* is the expected stream-copy of *source*."""
  if _signature(output) != _signature(source, plan.keep):
    raise RepairError("repaired streams do not match the source")
  src_dur, out_dur = _duration(source), _duration(output)
  if src_dur and (out_dur is None or abs(src_dur - out_dur) > _DURATION_TOLERANCE):
    raise RepairError("repaired duration %s differs from source %s" % (out_dur, src_dur))
  if len(output.get("chapters") or []) != len(source.get("chapters") or []):
    raise RepairError("repaired chapter count differs from source")
  leftover = diagnose(output_path, output)
  if leftover.needs_repair:
    raise RepairError("repaired file still needs repair: %s" % leftover.describe())


def temp_path_for(path: str) -> str:
  directory, name = os.path.split(path)
  stem, ext = os.path.splitext(name)
  return os.path.join(directory, ".%s%s%s" % (stem, TEMP_MARKER, ext))


def _copy_ownership(src: str, dst: str) -> None:
  try:
    st = os.stat(src)
    os.chmod(dst, st.st_mode & 0o7777)
    if hasattr(os, "chown"):
      os.chown(dst, st.st_uid, st.st_gid)
  except OSError:
    pass


def repair_file(path: str, ffmpeg, dry_run: bool = False, logger=None) -> RepairResult:
  """Diagnose *path* and, unless it is normal, repair it in place by stream copy.

  *ffmpeg* is a :class:`converter.ffmpeg.FFMpeg` (its ``ffprobe_path`` is used
  for probing and its ``convert`` for the remux).
  """
  try:
    source = probe(path, ffmpeg.ffprobe_path)
  except RepairError as exc:
    return RepairResult(path, STATUS_FAILED, str(exc))
  plan = diagnose(path, source)
  if not plan.needs_repair:
    return RepairResult(path, STATUS_NORMAL)
  detail = plan.describe()
  if dry_run:
    return RepairResult(path, STATUS_WOULD_REPAIR, detail)
  kept_types = {s.get("codec_type") for s in source["streams"] if s.get("index") in plan.keep}
  if not kept_types & {"video", "audio"}:
    return RepairResult(path, STATUS_FAILED, "no audio or video stream would survive the repair")

  tmp = temp_path_for(path)
  try:
    opts = build_opts(plan, path)
    for _timecode, _debug in ffmpeg.convert(tmp, opts, timeout=0):
      pass
    verify(plan, source, probe(tmp, ffmpeg.ffprobe_path), path)
    _copy_ownership(path, tmp)
    os.replace(tmp, path)
  except Exception as exc:
    try:
      if os.path.exists(tmp):
        os.remove(tmp)
    except OSError:
      pass
    if logger:
      logger.debug("Repair of %s failed" % path, exc_info=True)
    return RepairResult(path, STATUS_FAILED, "%s (%s)" % (exc, detail))
  return RepairResult(path, STATUS_REPAIRED, detail)


def iter_targets(paths: list[str]):
  """Yield repairable media files under *paths* (files or directories), sorted
  per directory. Hidden files and in-progress repair temp files are skipped."""
  for path in paths:
    if os.path.isfile(path):
      if _ext(path) in REPAIRABLE_EXTS:
        yield path
      continue
    for root, dirs, files in os.walk(path):
      dirs[:] = sorted(d for d in dirs if not d.startswith("."))
      for name in sorted(files):
        if name.startswith(".") or TEMP_MARKER in name:
          continue
        if _ext(name) in REPAIRABLE_EXTS:
          yield os.path.join(root, name)


__all__ = [
  "REPAIRABLE_EXTS",
  "RepairError",
  "RepairPlan",
  "RepairResult",
  "assert_stream_copy",
  "build_opts",
  "diagnose",
  "iter_targets",
  "probe",
  "repair_file",
  "verify",
]
