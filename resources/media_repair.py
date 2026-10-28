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
  primary video stream, in the container and (H.264, HEVC, MPEG-2) in the
  bitstream itself. The resampled rows stay in the bitstream; removing them
  needs a re-encode, which this module never does.

Files with none of these problems are reported as ``normal`` and left alone.
Every probed file also gets a one-line FFprobe summary (:func:`summarize`).
The repaired copy is written like any conversion output: into the configured
``output-directory`` (next to the original when unset, subject to
``output-directory-space-ratio``) with the ``temp-extension`` (``.sma`` when
unset, as the storage janitor assumes). It is re-probed, and only moved back
over the original when its streams (including Dolby Vision signalling),
chapters, duration and size match the source and it diagnoses as normal; any
failure leaves the original untouched. A hardlinked original is rewritten in
place so every link sees the repair.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from fractions import Fraction

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

# The repaired file may be this much smaller than the source minus the
# estimated size of the dropped streams (a fraction, plus fixed slack for
# container overhead and streams whose size cannot be estimated) before the
# remux is judged to have lost packets.
SIZE_TOLERANCE = 0.9
SIZE_SLACK_BYTES = 16 * 1024 * 1024

# Bitstream filters that write the corrected aspect into the video bitstream
# (VUI / sequence header): codec -> (filter, option, "sar" or "dar" value).
# -aspect alone only sets the container PAR (MP4 pasp, Matroska display
# size), which players that read the bitstream ignore.
_ASPECT_BSF = {
  "h264": ("h264_metadata", "sample_aspect_ratio", "sar"),
  "hevc": ("hevc_metadata", "sample_aspect_ratio", "sar"),
  "mpeg2video": ("mpeg2_metadata", "display_aspect_ratio", "dar"),
}

# Audio codecs the MP4 muxer only writes with -strict experimental.
_MP4_EXPERIMENTAL_AUDIO = frozenset({"truehd"})

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
  # Output video stream number of the primary video (the -aspect target).
  video_out: int = 0
  # -bsf value that writes the corrected aspect into the bitstream, or None.
  aspect_bsf: str | None = None
  # -strict level the muxer needs to write the kept streams, or None.
  strict: str | None = None

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
  probe: str = ""  # one-line FFprobe summary (summarize); empty when the probe failed


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


def _aspect_bsf(stream, dar: str) -> str | None:
  """-bsf value that writes *dar* into the bitstream of *stream*, or None when
  its codec has no metadata filter for it (the container PAR still applies)."""
  spec = _ASPECT_BSF.get(stream.codec)
  if not spec:
    return None
  name, option, kind = spec
  dar_w, dar_h = (int(x) for x in dar.split(":"))
  ratio = Fraction(dar_w, dar_h)
  if kind == "sar":
    ratio *= Fraction(stream.video_height, stream.video_width)
  return "%s=%s=%d/%d" % (name, option, ratio.numerator, ratio.denominator)


def _strict(info, keep: list[int], mp4_family: bool) -> str | None:
  """-strict level needed to mux the kept streams, as the conversion pipeline
  sets it: experimental for TrueHD in MP4, unofficial so the muxer keeps a
  Dolby Vision configuration record (MP4 dvcC/dvvC)."""
  kept = [s for s in info.streams if s.index in keep]
  if mp4_family and any(s.type == "audio" and s.codec in _MP4_EXPERIMENTAL_AUDIO for s in kept):
    return "experimental"
  if any(s.type == "video" and getattr(s, "dv_profile", None) is not None for s in kept):
    return "unofficial"
  return None


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
  video = info.video
  plan.dar = _hybrid_dar(video)
  if plan.dar:
    kept_videos = [s.index for s in info.streams if s.index in plan.keep and s.type == "video"]
    plan.video_out = kept_videos.index(video.index) if video.index in kept_videos else 0
    plan.aspect_bsf = _aspect_bsf(video, plan.dar)
  plan.strict = _strict(info, plan.keep, mp4_family)
  return plan


def build_opts(plan: RepairPlan, source: str) -> list[str]:
  """FFmpeg options for the repair remux. Stream copy only."""
  opts = ["-i", source]
  for index in plan.keep:
    opts.extend(["-map", "0:%d" % index])
  opts.extend(["-c", "copy"])
  if plan.dar:
    opts.extend(["-aspect:v:%d" % plan.video_out, plan.dar])
    if plan.aspect_bsf:
      opts.extend(["-bsf:v:%d" % plan.video_out, plan.aspect_bsf])
  if plan.strict:
    opts.extend(["-strict", plan.strict])
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
  return [
    (
      s.type,
      s.codec,
      s.video_width,
      s.video_height,
      s.audio_channels,
      s.metadata.get("language"),
      bool(getattr(s, "default", False)),
      bool(getattr(s, "forced", False)),
      getattr(s, "dv_profile", None),
    )
    for s in streams
  ]


def _describe_signature(signature: list[tuple]) -> str:
  """Compact one-line view of a stream signature, e.g. ``[video/hevc 3840x2160, audio/eac3 6ch eng default]``."""
  parts = []
  for kind, codec, width, height, channels, language, default, forced, dv_profile in signature:
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
    if dv_profile is not None:
      item += " dv%s" % dv_profile
    parts.append(item)
  return "[%s]" % ", ".join(parts)


# Transfer characteristics that mark a video stream as HDR.
_HDR_TRANSFERS = {"smpte2084": "HDR10/PQ", "arib-std-b67": "HLG"}


def _duration(seconds) -> str:
  seconds = int(round(seconds))
  return "%d:%02d:%02d" % (seconds // 3600, seconds // 60 % 60, seconds % 60)


def _size(size) -> str:
  unit = "B"
  for unit in ("B", "KiB", "MiB", "GiB"):
    if size < 1024 or unit == "GiB":
      break
    size /= 1024.0
  return "%d B" % size if unit == "B" else "%.1f %s" % (size, unit)


def _describe_stream(s) -> str:
  item = "#%s %s/%s" % (s.index, s.type, s.codec)
  if s.type == "video":
    if s.video_width and s.video_height:
      item += " %sx%s" % (s.video_width, s.video_height)
    if s.sample_aspect_ratio not in (None, "1:1", "0:1"):
      item += " SAR %s" % s.sample_aspect_ratio
    if s.fps and not s.attached_pic:  # a still image reports the 90k timebase as its rate
      item += " %sfps" % ("%.3f" % s.fps).rstrip("0").rstrip(".")
    if s.pix_fmt:
      item += " %s" % s.pix_fmt
    hdr = _HDR_TRANSFERS.get(s.color.get("transfer"))
    if hdr:
      item += " %s" % hdr
    if s.attached_pic:
      item += " attached_pic"
  elif s.type == "audio" and s.audio_channels:
    item += " %sch" % s.audio_channels
  elif s.type in ("data", "attachment"):
    if s.codec_tag and s.codec_tag != "[0][0][0][0]":
      item += " tag=%s" % s.codec_tag
    for key in ("filename", "mimetype"):
      if s.metadata.get(key):
        item += " %s" % s.metadata[key]
  if s.bitrate:
    item += " %d kb/s" % (s.bitrate / 1000)
  if s.type in ("audio", "subtitle"):
    if s.metadata.get("language"):
      item += " %s" % s.metadata["language"]
    for flag in ("default", "forced"):
      if s.disposition.get(flag):
        item += " %s" % flag
  return item


def summarize(info) -> str:
  """One-line FFprobe summary of *info*: container, duration, size, overall
  bitrate, then every stream, e.g.
  ``matroska,webm 0:42:10 3.1 GiB 10.4 Mb/s | #0 video/hevc 3840x2160 23.976fps yuv420p10le HDR10/PQ | #1 audio/eac3 6ch eng default``."""
  fmt = info.format
  head = [getattr(fmt, "format", None) or "unknown"]
  if getattr(fmt, "duration", None):
    head.append(_duration(fmt.duration))
  if getattr(fmt, "size", None):
    head.append(_size(fmt.size))
  if getattr(fmt, "bitrate", None):
    head.append("%.1f Mb/s" % (fmt.bitrate / 1e6))
  return " | ".join([" ".join(head)] + [_describe_stream(s) for s in info.streams])


def _duration_of(info, indexes: list[int] | None = None) -> float | None:
  """Longest duration among *indexes* (all streams when None); the container
  duration when no stream reports one (Matroska streams usually do not)."""
  durations = [s.duration for s in info.streams if s.duration and (indexes is None or s.index in indexes)]
  return max(durations) if durations else info.format.duration


def _dropped_bytes(source, keep: list[int]) -> float:
  """Estimated size of the streams the repair drops (bitrate x duration)."""
  total = 0.0
  for s in source.streams:
    if s.index not in keep and s.bitrate:
      total += s.bitrate * (s.duration or source.format.duration or 0) / 8
  return total


def verify(plan: RepairPlan, source, output) -> None:
  """Raise RepairError unless *output* is the expected stream-copy of *source*.

  *output* is judged as the source's container (``plan.path``), not by its
  in-transit temp extension. Durations compare the kept streams, so dropping a
  data or artwork stream longer than the programme is not a mismatch.
  Chapters compare by count (``chapters``, set by :meth:`RepairProcessor.probe`),
  not by chapter tracks: the MP4 muxer writes a fresh chapter text track from
  the chapter list, also when the source kept its chapters elsewhere (a Nero
  ``chpl`` atom, or a ``tx3g`` track FFprobe reports as a subtitle)."""
  expected, actual = _signature(source, plan.keep), _signature(output)
  if actual != expected:
    raise RepairError("repaired streams do not match the source: expected %s, got %s" % (_describe_signature(expected), _describe_signature(actual)))
  src_dur, out_dur = _duration_of(source, plan.keep), _duration_of(output)
  if src_dur and (out_dur is None or abs(src_dur - out_dur) > DURATION_TOLERANCE):
    raise RepairError("repaired duration %s differs from source %s" % (out_dur, src_dur))
  src_chapters, out_chapters = getattr(source, "chapters", None), getattr(output, "chapters", None)
  if src_chapters is not None and out_chapters is not None and src_chapters != out_chapters:
    raise RepairError("repaired file has %d chapters, source has %d" % (out_chapters, src_chapters))
  src_size, out_size = getattr(source.format, "size", None), getattr(output.format, "size", None)
  if src_size and out_size:
    minimum = (src_size - _dropped_bytes(source, plan.keep)) * SIZE_TOLERANCE - SIZE_SLACK_BYTES
    if out_size < minimum:
      raise RepairError("repaired file is %d bytes, expected at least %d; the remux lost data" % (out_size, minimum))
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
    info.chapters = self.count_chapters(path)
    return info

  def count_chapters(self, path: str) -> int | None:
    """Chapters FFprobe reads from *path*, or None when it cannot tell."""
    try:
      out = self.ffmpeg._get_stdout([self.ffmpeg.ffprobe_path, "-v", "error", "-show_chapters", path])
    except Exception:
      self.log.debug("Unable to count chapters of %s." % path, exc_info=True)
      return None
    return out.count("[CHAPTER]")

  def _rewrite_in_place(self, tmp: str, path: str) -> None:
    """Copy *tmp* over the contents of *path*, keeping its inode so every
    hardlink to it sees the repair. Not atomic: a failure part-way leaves
    *path* damaged, so *tmp* is kept for recovery."""
    shutil.copyfile(tmp, path)
    if os.path.getsize(path) != os.path.getsize(tmp):
      raise OSError("size after rewrite differs from %s" % tmp)
    os.remove(tmp)

  def getTempFile(self, path: str) -> str:
    """In-transit output path for *path*, following MediaProcessor.getOutputFile:
    ``<output-directory>/<name>.<temp-extension>``, or next to *path* when no
    output directory is configured. A ``.N`` counter avoids collisions."""
    output_dir = self.settings.output_dir or os.path.dirname(os.path.abspath(path))
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
    probe = summarize(source)
    self.log.debug("Probed %s: keep streams %s." % (path, ",".join(str(i) for i in plan.keep)))
    if not plan.needs_repair:
      return RepairResult(path, STATUS_NORMAL, probe=probe)
    detail = plan.describe()
    if dry_run:
      return RepairResult(path, STATUS_WOULD_REPAIR, detail, probe)
    if not any(s.index in plan.keep and s.type in ("video", "audio") for s in source.streams):
      return RepairResult(path, STATUS_FAILED, "no audio or video stream would survive the repair", probe)
    if not self.outputDirHasFreeSpace(path):
      # Like the conversion pipeline, never fall back to writing beside the source.
      return RepairResult(path, STATUS_FAILED, "insufficient free space in output directory %s (%s)" % (self.settings.output_dir, detail), probe)

    tmp = None
    rewriting = False
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
      links = os.stat(path).st_nlink
      if links > 1:
        # Replacing the file would split it from its other hardlinks (e.g. a
        # seeding copy), which would keep the damage and double the disk use.
        self.log.info("%s has %d hardlinks, rewriting it in place." % (path, links))
        rewriting = True
        self._rewrite_in_place(tmp, path)
      else:
        self.log.debug("Verified %s against source, replacing original." % tmp)
        self.setPermissions(tmp)
        self._atomic_move(tmp, path)
    except Exception as exc:
      self.log.debug("Repair of %s failed." % path, exc_info=True)
      if rewriting and tmp and os.path.exists(tmp):
        return RepairResult(path, STATUS_FAILED, "%s; in-place rewrite failed, original may be damaged, verified copy kept at %s (%s)" % (exc, tmp, detail), probe)
      try:
        if tmp and os.path.exists(tmp):
          os.remove(tmp)
      except OSError:
        pass
      return RepairResult(path, STATUS_FAILED, "%s (%s)" % (exc, detail), probe)
    return RepairResult(path, STATUS_REPAIRED, detail, probe)


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
  "summarize",
  "verify",
]
