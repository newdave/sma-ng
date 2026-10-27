"""Per-file probes invoked by the audit worker."""

from __future__ import annotations

import os
import time
from typing import Any

from converter.ffmpeg import FFMpeg
from resources.library_audit.kinds import (
  IN_TRANSIT_EXTS,
  IN_TRANSIT_MIN_AGE_SECONDS,
  MEDIA_CONTAINER_EXTS,
  NON_MP4_CONTAINERS,
  SIDECAR_EXTS,
  TMP_EXTS,
  TMP_SUFFIXES,
  FindingKind,
)
from resources.media_repair import HYBRID_DIMS_TABLE, REPAIRABLE_EXTS, diagnose


def ffprobe_check(path: str, ffmpeg_dir: str | None = None) -> dict[str, Any] | None:
  """Run FFprobe on *path*. Return ``None`` when readable, finding-details otherwise."""
  if not os.path.isfile(path):
    return {"reason": "missing", "size_bytes": 0}
  try:
    size = os.path.getsize(path)
  except OSError:
    size = 0
  if size == 0:
    return {"reason": "empty", "size_bytes": 0}
  ext = os.path.splitext(path)[1].lower()
  if ext not in MEDIA_CONTAINER_EXTS:
    return None  # not a media file — skip
  try:
    info = _ffmpeg(ffmpeg_dir).probe(path, framedata=False)
  except Exception as exc:
    return {"reason": "probe_exception", "error": str(exc)[:512], "size_bytes": size}
  if info is None:
    return {"reason": "probe_returned_none", "size_bytes": size}
  return None


def _ffmpeg(ffmpeg_dir: str | None) -> FFMpeg:
  if ffmpeg_dir:
    return FFMpeg(ffmpeg_path=os.path.join(ffmpeg_dir, "ffmpeg"), ffprobe_path=os.path.join(ffmpeg_dir, "ffprobe"))
  return FFMpeg()


def repair_check(path: str, ffmpeg_dir: str | None = None) -> tuple[FindingKind, dict[str, Any]] | None:
  """Stream damage that :class:`resources.media_repair.RepairProcessor` fixes
  by stream copy. Returns ``(kind, details)`` or ``None`` when the file is
  normal, not a repairable container, or unprobeable (``ffprobe_check``
  reports those).

  - ``hybrid_aspect``: the primary picture matches the stretched-output
    signature table (see ``resources.media_repair.HYBRID_DIMS_TABLE``).
    Reason ``stretched_dims`` when it still has square pixels;
    ``demoted_poster`` when an older repair already corrected the aspect but
    turned the cover art into a real video track.
  - ``stream_damage``: anything else the repair drops — data streams,
    unknown streams and artwork (see :func:`resources.media_repair.diagnose`).
  """
  if os.path.splitext(path)[1].lower() not in REPAIRABLE_EXTS or not os.path.isfile(path):
    return None
  try:
    info = _ffmpeg(ffmpeg_dir).probe(path, posters_as_video=False, framedata=False)
  except Exception:
    return None
  if info is None or not info.streams:
    return None
  plan = diagnose(info)
  if not plan.needs_repair:
    return None
  video = info.video
  true_height = HYBRID_DIMS_TABLE.get((video.video_width, video.video_height)) if video is not None else None
  # Only a poster demoted to a real video track (no attached_pic flag) marks
  # the old hybrid remux; an intact cover is ordinary stream damage.
  demoted = any(s.type == "video" and not s.attached_pic for s in info.cover_art)
  if video is not None and true_height and (plan.dar or demoted):
    return FindingKind.HYBRID_ASPECT, {
      "reason": "stretched_dims" if plan.dar else "demoted_poster",
      "width": video.video_width,
      "height": video.video_height,
      "true_height": true_height,
      "dar": "%d:%d" % (video.video_width, true_height),
      "repair": plan.describe(),
    }
  return FindingKind.STREAM_DAMAGE, {
    "reason": "stream_damage",
    "repair": plan.describe(),
    "data_streams": plan.drop_data,
    "unknown_streams": plan.drop_unknown,
    "artwork_streams": plan.drop_artwork,
  }


def hybrid_aspect_check(path: str, ffmpeg_dir: str | None = None) -> dict[str, Any] | None:
  """Details of a ``hybrid_aspect`` finding for *path* (see :func:`repair_check`), or ``None``."""
  result = repair_check(path, ffmpeg_dir=ffmpeg_dir)
  if result is None or result[0] != FindingKind.HYBRID_ASPECT:
    return None
  return result[1]


def is_sidecar(path: str) -> bool:
  return os.path.splitext(path)[1].lower() in SIDECAR_EXTS


def _in_transit_exts(temp_extension: str | None) -> set[str]:
  exts = set(IN_TRANSIT_EXTS)
  if temp_extension:
    ext = "." + temp_extension.lstrip(".").lower()
    # A temp-extension equal to a media container would flag the library itself.
    if ext not in MEDIA_CONTAINER_EXTS:
      exts.add(ext)
  return exts


def is_in_transit(path: str, temp_extension: str | None = None) -> bool:
  """True for SMA in-transit output: the configured temp-extension, ``.sma`` or a ``.smatmp`` partial."""
  return os.path.splitext(path)[1].lower() in _in_transit_exts(temp_extension)


def is_tmp_artifact(path: str, temp_extension: str | None = None) -> bool:
  ext = os.path.splitext(path)[1].lower()
  if ext in TMP_EXTS or is_in_transit(path, temp_extension):
    return True
  return any(path.endswith(suf) for suf in TMP_SUFFIXES)


def sidecar_orphan_check(path: str) -> dict[str, Any] | None:
  """Sidecar with no matching parent media file in the same directory.

  Match is by basename: ``Movie.en.srt`` matches ``Movie.mkv``/``Movie.mp4``.
  Returns finding-details when orphaned; ``None`` when a matching parent is
  found (or when *path* is not actually a sidecar).
  """
  if not is_sidecar(path):
    return None
  if not os.path.isfile(path):
    return None
  directory = os.path.dirname(path)
  base = os.path.basename(path)
  stem = os.path.splitext(base)[0]
  # Strip optional language code: "Movie.en" → "Movie", "Movie" stays "Movie".
  if "." in stem:
    head, _sep, _tail = stem.rpartition(".")
    candidate_stems = {stem, head} if head else {stem}
    del _sep, _tail
  else:
    candidate_stems = {stem}
  try:
    siblings = os.listdir(directory)
  except OSError:
    return None
  sibling_stems = {os.path.splitext(s)[0] for s in siblings if os.path.splitext(s)[1].lower() in MEDIA_CONTAINER_EXTS}
  if candidate_stems & sibling_stems:
    return None
  return {"reason": "no_parent_media", "directory": directory, "stem": stem}


def tmp_artifact_check(path: str, temp_extension: str | None = None, min_age_seconds: int | None = None) -> dict[str, Any] | None:
  """Leftover transcoder artifact. In-transit output (see :func:`is_in_transit`)
  is only reported once its mtime is older than *min_age_seconds* (default
  ``IN_TRANSIT_MIN_AGE_SECONDS``), so a file still being written is skipped."""
  if not is_tmp_artifact(path, temp_extension):
    return None
  if not os.path.isfile(path):
    return None
  try:
    age = os.path.getmtime(path)
  except OSError:
    age = None
  if is_in_transit(path, temp_extension):
    min_age = min_age_seconds or IN_TRANSIT_MIN_AGE_SECONDS
    if age is None or time.time() - age < min_age:
      return None
    return {"reason": "in_transit_leftover", "mtime": age}
  return {"reason": "leftover_artifact", "mtime": age}


def preconv_original_check(path: str) -> dict[str, Any] | None:
  """Non-MP4 file that has a same-stem MP4 sibling in the same directory.

  Caller (engine) must verify the MP4 sibling actually probes cleanly before
  promoting this to a finding. We only return the candidate match here.
  """
  if not os.path.isfile(path):
    return None
  ext = os.path.splitext(path)[1].lower()
  if ext not in NON_MP4_CONTAINERS:
    return None
  directory = os.path.dirname(path)
  stem = os.path.splitext(os.path.basename(path))[0]
  mp4_sibling = os.path.join(directory, stem + ".mp4")
  if os.path.isfile(mp4_sibling):
    return {"reason": "preconv_original", "mp4_sibling": mp4_sibling}
  return None


__all__ = [
  "HYBRID_DIMS_TABLE",
  "ffprobe_check",
  "hybrid_aspect_check",
  "is_in_transit",
  "is_sidecar",
  "is_tmp_artifact",
  "preconv_original_check",
  "repair_check",
  "sidecar_orphan_check",
  "tmp_artifact_check",
]
