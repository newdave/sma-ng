"""Stream-copy repair for damaged media. Never transcodes.

Probes each file and fixes only four kinds of damage, all by remuxing with
``-c copy``:

- **data streams**: ``tmcd`` timecode tracks and stray ``bin_data`` tracks. The QuickTime chapter text track (codec tag
  ``text``) is not damage: it is how MP4 stores chapters and the muxer
  rewrites it from the chapter list on every remux.
- **unknown streams**: streams FFprobe cannot classify or identify a codec
  for (:func:`converter.ffmpeg.is_unknown_stream`).
- **artwork**: everything in :attr:`converter.ffmpeg.MediaInfo.cover_art`,
  the same definition the conversion and tagging remuxes use.
- **aspect ratio**: outputs stretched by the former QSV alignment bug (e.g.
  3840x2176 with square pixels) get a corrective display aspect ratio on the
  primary video stream. The resampled rows stay in the bitstream; removing
  them needs a re-encode, which this module never does.

Files with none of these problems are reported as ``normal`` and left alone.
The repaired copy is written like any conversion output: into the configured
``output-directory`` (next to the original when unset, subject to
``output-directory-space-ratio``) with the ``temp-extension`` (``.sma`` when
unset, as the storage janitor assumes). It is re-probed, and only moved back
over the original
when its streams match the source and it diagnoses as normal; any failure
leaves the original untouched.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field

from converter.ffmpeg import FFMpeg, is_unknown_stream

REPAIRABLE_EXTS = frozenset({".mp4", ".m4v", ".mov", ".mkv"})
MP4_FAMILY_EXTS = frozenset({".mp4", ".m4v", ".mov"})
DEFAULT_TEMP_EXTENSION = "sma"

# Muxer per source extension; the in-transit name has no media extension, so
# ffmpeg cannot infer it.
_MUXERS = {".mp4": "mp4", ".m4v": "mp4", ".mov": "mov", ".mkv": "matroska"}

KEPT_STREAM_TYPES = ("video", "audio", "subtitle", "attachment")

# Stretched-output signatures left behind by the (now fixed) QSV alignment
# bug: standard-resolution sources whose height was force-scaled to a
# mod-16/mod-32 boundary (ceil for "encoder alignment", floor for the
# max-width downscale fold). Maps (stored_width, stored_height) -> the true
# height the picture was stretched from. These heights essentially never
# occur naturally at these widths, so matching the table (plus a 1:1/absent
# SAR — a corrective DAR remux leaves a non-square PAR behind) identifies a
# hybrid-aspect file with no realistic false positives.
_HYBRID_STANDARD_DIMS = ((1920, 1080), (3840, 2160), (1280, 720))
HYBRID_DIMS_TABLE: dict[tuple[int, int], int] = {}
for _w, _h in _HYBRID_STANDARD_DIMS:
  for _align in (16, 32):
    for _stored in (((_h + _align - 1) // _align) * _align, (_h // _align) * _align):
      if _stored != _h:
        HYBRID_DIMS_TABLE[(_w, _stored)] = _h
del _w, _h, _align, _stored

# Seconds between debug progress lines while the remux runs.
PROGRESS_INTERVAL = 30.0

# Duration drift tolerated between source and repaired output (seconds).
DURATION_TOLERANCE = 1.0

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
  drop_unknown: list[int] = field(default_factory=list)
  drop_artwork: list[int] = field(default_factory=list)
  dar: str | None = None

  @property
  def needs_repair(self) -> bool:
    return bool(self.drop_data or self.drop_unknown or self.drop_artwork or self.dar)

  def describe(self) -> str:
    parts = []
    if self.drop_data:
      parts.append("data streams %s" % ",".join(str(i) for i in self.drop_data))
    if self.drop_unknown:
      parts.append("unknown streams %s" % ",".join(str(i) for i in self.drop_unknown))
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


def _ext(path: str) -> str:
  return os.path.splitext(path)[1].lower()


def _is_chapter_track(stream, mp4_family: bool) -> bool:
  return mp4_family and stream.type == "data" and stream.codec_tag == "text"


def _hybrid_dar(stream) -> str | None:
  """Corrective DAR for a stretched primary video stream, or None."""
  if stream is None or not stream.video_width or not stream.video_height:
    return None
  true_height = HYBRID_DIMS_TABLE.get((stream.video_width, stream.video_height))
  if not true_height:
    return None
  if stream.sample_aspect_ratio not in (None, "1:1", "0:1"):
    return None  # display aspect already corrected (or genuinely anamorphic)
  return "%d:%d" % (stream.video_width, true_height)


def diagnose(info, path: str | None = None) -> RepairPlan:
  """Classify every stream of *info* (a probed :class:`MediaInfo`) as kept or
  dropped. *path* names the container when *info* was probed from an
  in-transit file whose extension is the temp extension."""
  path = path or info.path
  mp4_family = _ext(path) in MP4_FAMILY_EXTS
  cover_art = info.cover_art
  plan = RepairPlan(path=path)
  for s in info.streams:
    if s in cover_art:
      plan.drop_artwork.append(s.index)
    elif is_unknown_stream(s):
      plan.drop_unknown.append(s.index)
    elif s.type in KEPT_STREAM_TYPES:
      plan.keep.append(s.index)
    elif not _is_chapter_track(s, mp4_family):
      # The chapter track is never mapped either, but it is not damage.
      plan.drop_data.append(s.index)
  plan.dar = _hybrid_dar(info.video)
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
  opts.extend(["-f", _MUXERS[_ext(plan.path)]])
  return opts


def _signature(info, indexes: list[int] | None = None) -> list[tuple]:
  streams = info.streams
  if indexes is not None:
    by_index = {s.index: s for s in streams}
    streams = [by_index[i] for i in indexes if i in by_index]
  else:
    streams = [s for s in streams if s.type in KEPT_STREAM_TYPES]
  return [(s.type, s.codec, s.video_width, s.video_height, s.audio_channels, s.metadata.get("language"), bool(getattr(s, "default", False)), bool(getattr(s, "forced", False))) for s in streams]


def _describe_signature(signature: list[tuple]) -> str:
  """Compact one-line view of a stream signature, e.g. ``[video/hevc 3840x2160, audio/eac3 6ch eng default]``."""
  parts = []
  for kind, codec, width, height, channels, language, default, forced in signature:
    item = "%s/%s" % (kind, codec)
    if width and height:
      item += " %sx%s" % (width, height)
    if channels:
      item += " %sch" % channels
    if language:
      item += " %s" % language
    if default:
      item += " default"
    if forced:
      item += " forced"
    parts.append(item)
  return "[%s]" % ", ".join(parts)


def _chapter_tracks(info, path: str) -> int:
  mp4_family = _ext(path) in MP4_FAMILY_EXTS
  return sum(1 for s in info.streams if _is_chapter_track(s, mp4_family))


def verify(plan: RepairPlan, source, output) -> None:
  """Raise RepairError unless *output* is the expected stream-copy of *source*.

  *output* is judged as the source's container (``plan.path``), not by its
  in-transit temp extension."""
  expected, actual = _signature(source, plan.keep), _signature(output)
  if actual != expected:
    raise RepairError("repaired streams do not match the source: expected %s, got %s" % (_describe_signature(expected), _describe_signature(actual)))
  src_dur, out_dur = source.format.duration, output.format.duration
  if src_dur and (out_dur is None or abs(src_dur - out_dur) > DURATION_TOLERANCE):
    raise RepairError("repaired duration %s differs from source %s" % (out_dur, src_dur))
  if _chapter_tracks(output, plan.path) != _chapter_tracks(source, plan.path):
    raise RepairError("repaired chapter track count differs from source")
  leftover = diagnose(output, plan.path)
  if leftover.needs_repair:
    raise RepairError("repaired file still needs repair: %s" % leftover.describe())


class RepairProcessor:
  """Diagnose and repair media files in place using SMA settings for the
  ffmpeg/ffprobe binaries, the in-transit ``output-directory`` and
  ``temp-extension``, and output permissions."""

  def __init__(self, settings, logger=None, ffmpeg_dir=None):
    """*ffmpeg_dir* (the daemon's ``ffmpeg-dir``) overrides the binaries in *settings*."""
    self.settings = settings
    self.log = logger or logging.getLogger(__name__)
    if ffmpeg_dir:
      self.ffmpeg = FFMpeg(ffmpeg_path=os.path.join(ffmpeg_dir, "ffmpeg"), ffprobe_path=os.path.join(ffmpeg_dir, "ffprobe"))
    else:
      self.ffmpeg = FFMpeg(ffmpeg_path=settings.ffmpeg, ffprobe_path=settings.ffprobe)

  def probe(self, path: str):
    info = self.ffmpeg.probe(path, posters_as_video=False, framedata=False)
    if info is None or not info.streams:
      raise RepairError("ffprobe found no streams")
    return info

  def getTempFile(self, path: str) -> str:
    """In-transit output path for *path*, following MediaProcessor.getOutputFile:
    ``<output-directory>/<name>.<temp-extension>``, or next to *path* when no
    output directory is configured. A ``.N`` counter avoids collisions."""
    output_dir = self.settings.output_dir or os.path.dirname(path)
    os.makedirs(output_dir, exist_ok=True)
    base = os.path.join(output_dir, os.path.splitext(os.path.basename(path))[0])
    temp_extension = self.settings.temp_extension or DEFAULT_TEMP_EXTENSION
    tmp = "%s.%s" % (base, temp_extension)
    number = 2
    while os.path.exists(tmp):
      tmp = "%s.%d.%s" % (base, number, temp_extension)
      number += 1
    return tmp

  def outputDirHasFreeSpace(self, path: str) -> bool:
    """Same gate as MediaProcessor.outputDirHasFreeSpace: output-directory must
    hold ``size * output-directory-space-ratio``. Passes when either is unset
    or the check cannot be performed."""
    if self.settings.output_dir and self.settings.output_dir_ratio:
      try:
        needed = os.path.getsize(path) * self.settings.output_dir_ratio
        if shutil.disk_usage(self.settings.output_dir).free <= needed:
          self.log.info("Output-directory does not have enough free space (%s needed) [output-directory-space-ratio]." % needed)
          return False
      except Exception:
        self.log.exception("Unable to check free space on output directory %s [output-directory-space-ratio]." % self.settings.output_dir)
    return True

  def _atomic_move(self, src: str, dst: str) -> None:
    """Move *src* over *dst* as MediaProcessor._atomic_move does: rename on the
    same filesystem, otherwise copy to ``<dst>.smatmp`` and rename into place."""
    try:
      os.replace(src, dst)
      return
    except OSError:
      pass
    dst_tmp = dst + ".smatmp"
    try:
      shutil.copy2(src, dst_tmp)
      self.setPermissions(dst_tmp)
      os.replace(dst_tmp, dst)
    except Exception:
      try:
        if os.path.exists(dst_tmp):
          os.remove(dst_tmp)
      except OSError:
        pass
      raise
    os.remove(src)

  def setPermissions(self, path: str) -> None:
    """Apply chmod/chown from settings, as MediaProcessor.setPermissions does."""
    try:
      os.chmod(path, self.settings.permissions.get("chmod", int("0664", 8)))
      if os.name != "nt":
        os.chown(path, self.settings.permissions.get("uid", -1), self.settings.permissions.get("gid", -1))
    except Exception:
      self.log.exception("Unable to set new file permissions.")

  def repair_file(self, path: str, dry_run: bool = False) -> RepairResult:
    """Diagnose *path* and, unless it is normal, repair it in place by stream copy."""
    try:
      source = self.probe(path)
    except Exception as exc:
      return RepairResult(path, STATUS_FAILED, str(exc))
    plan = diagnose(source)
    self.log.debug("Probed %s: streams %s; keep %s." % (path, _describe_signature(_signature(source, [s.index for s in source.streams])), ",".join(str(i) for i in plan.keep)))
    if not plan.needs_repair:
      return RepairResult(path, STATUS_NORMAL)
    detail = plan.describe()
    if dry_run:
      return RepairResult(path, STATUS_WOULD_REPAIR, detail)
    if not any(s.index in plan.keep and s.type in ("video", "audio") for s in source.streams):
      return RepairResult(path, STATUS_FAILED, "no audio or video stream would survive the repair")
    if not self.outputDirHasFreeSpace(path):
      # Like the conversion pipeline, never fall back to writing beside the source.
      return RepairResult(path, STATUS_FAILED, "insufficient free space in output directory %s (%s)" % (self.settings.output_dir, detail))

    tmp = None
    try:
      tmp = self.getTempFile(path)
      self.log.info("Repairing %s: %s." % (path, detail))
      last_report = time.monotonic()
      for step, (timecode, debug) in enumerate(self.ffmpeg.convert(tmp, build_opts(plan, path), timeout=0)):
        if step == 0:
          # FFMpeg.convert yields the generated command line before spawning ffmpeg.
          self.log.debug("FFmpeg command: %s" % (" ".join(debug) if isinstance(debug, list) else debug))
        elif time.monotonic() - last_report >= PROGRESS_INTERVAL:
          self.log.debug("Remuxing %s: %ss copied." % (path, timecode))
          last_report = time.monotonic()
      verify(plan, source, self.probe(tmp))
      self.log.debug("Verified %s against source, replacing original." % tmp)
      self.setPermissions(tmp)
      self._atomic_move(tmp, path)
    except Exception as exc:
      try:
        if tmp and os.path.exists(tmp):
          os.remove(tmp)
      except OSError:
        pass
      self.log.debug("Repair of %s failed." % path, exc_info=True)
      return RepairResult(path, STATUS_FAILED, "%s (%s)" % (exc, detail))
    return RepairResult(path, STATUS_REPAIRED, detail)


def iter_targets(paths: list[str]):
  """Yield repairable media files under *paths* (files or directories), sorted
  per directory. Hidden files and directories are skipped."""
  for path in paths:
    if os.path.isfile(path):
      if _ext(path) in REPAIRABLE_EXTS:
        yield path
      continue
    for root, dirs, files in os.walk(path):
      dirs[:] = sorted(d for d in dirs if not d.startswith("."))
      for name in sorted(files):
        if not name.startswith(".") and _ext(name) in REPAIRABLE_EXTS:
          yield os.path.join(root, name)


__all__ = [
  "HYBRID_DIMS_TABLE",
  "REPAIRABLE_EXTS",
  "RepairError",
  "RepairPlan",
  "RepairProcessor",
  "RepairResult",
  "build_opts",
  "diagnose",
  "iter_targets",
  "verify",
]
