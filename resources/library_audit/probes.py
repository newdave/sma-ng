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
  except Exception as exc:  # noqa: BLE001 - probe wraps subprocess
    return {"reason": "probe_exception", "error": str(exc)[:512], "size_bytes": size}
  if info is None:
    return {"reason": "probe_returned_none", "size_bytes": size}
  return None


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
  "ffprobe_check",
  "sidecar_orphan_check",
  "tmp_artifact_check",
  "preconv_original_check",
  "is_sidecar",
  "is_tmp_artifact",
]
