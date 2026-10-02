"""Per-file probes invoked by the audit worker."""

from __future__ import annotations

import os
from typing import Any

from converter.ffmpeg import FFMpeg
from resources.library_audit.kinds import (
  MEDIA_CONTAINER_EXTS,
  NON_MP4_CONTAINERS,
  SIDECAR_EXTS,
  TMP_EXTS,
  TMP_SUFFIXES,
)


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
    if ffmpeg_dir:
      ffmpeg = FFMpeg(
        ffmpeg_path=os.path.join(ffmpeg_dir, "ffmpeg"),
        ffprobe_path=os.path.join(ffmpeg_dir, "ffprobe"),
      )
    else:
      ffmpeg = FFMpeg()
    info = ffmpeg.probe(path)
  except Exception as exc:
    return {"reason": "probe_exception", "error": str(exc)[:512], "size_bytes": size}
  if info is None:
    return {"reason": "probe_returned_none", "size_bytes": size}
  return None


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


def hybrid_aspect_check(path: str, ffmpeg_dir: str | None = None) -> dict[str, Any] | None:
  """Detect an SMA-produced MP4 whose picture was stretched by the former
  QSV alignment bug. Returns finding-details, or ``None`` when the file is
  not a hybrid (wrong extension, unprobeable, dims not in the signature
  table, or already carrying a corrective non-square PAR).
  """
  if not path.lower().endswith(".mp4") or not os.path.isfile(path):
    return None
  try:
    if ffmpeg_dir:
      ffmpeg = FFMpeg(
        ffmpeg_path=os.path.join(ffmpeg_dir, "ffmpeg"),
        ffprobe_path=os.path.join(ffmpeg_dir, "ffprobe"),
      )
    else:
      ffmpeg = FFMpeg()
    info = ffmpeg.probe(path)
  except Exception:
    return None
  if info is None or info.video is None:
    return None
  width = getattr(info.video, "video_width", None)
  height = getattr(info.video, "video_height", None)
  if not isinstance(width, int) or not isinstance(height, int):
    return None
  true_height = HYBRID_DIMS_TABLE.get((width, height))
  if not true_height:
    return None
  sar = getattr(info.video, "sample_aspect_ratio", None)
  if sar and sar != "1:1":
    # A non-square PAR means the display aspect is already corrected
    # (either a prior remux repair or a genuinely anamorphic encode).
    return None
  return {
    "reason": "stretched_dims",
    "width": width,
    "height": height,
    "true_height": true_height,
    "dar": "%d:%d" % (width, true_height),
  }


def is_sidecar(path: str) -> bool:
  return os.path.splitext(path)[1].lower() in SIDECAR_EXTS


def is_tmp_artifact(path: str) -> bool:
  ext = os.path.splitext(path)[1].lower()
  if ext in TMP_EXTS:
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


def tmp_artifact_check(path: str) -> dict[str, Any] | None:
  if not is_tmp_artifact(path):
    return None
  if not os.path.isfile(path):
    return None
  try:
    age = os.path.getmtime(path)
  except OSError:
    age = None
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
  "is_sidecar",
  "is_tmp_artifact",
  "preconv_original_check",
  "sidecar_orphan_check",
  "tmp_artifact_check",
]
