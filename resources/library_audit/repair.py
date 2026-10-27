"""In-place repair for hybrid-aspect (stretched) MP4s.

The lossless repair path: remux the file with stream-copy and stamp the true
display aspect ratio (``-aspect:v:0 W:H``) into the container, so SAR-aware
players render the picture undistorted without a re-encode. The fix is
display-only: the resampled rows stay in the bitstream, and players that
ignore SAR still show the stretch. Storage dimensions are left unchanged — a subsequent :func:`resources.library_audit.probes.hybrid_aspect_check`
sees the non-square PAR and no longer flags the file.

Follows the same rename-aside/restore-on-failure pattern as
``Converter.tag``: the original is moved to ``<file>.aspectfix``, the remux
writes back to the original name, and any failure removes the partial output
and restores the original.
"""

from __future__ import annotations

import os

from converter.ffmpeg import FFMpeg
from resources.library_audit.probes import primary_video_stream


def repair_hybrid_aspect(path: str, dar: str, ffmpeg_dir: str | None = None, logger=None) -> bool:
  """Remux *path* in place with a corrective display aspect ratio *dar*
  (``"W:H"``, e.g. ``"1920:1080"``). Returns True on success.

  Only the primary video stream plus audio and subtitle streams are kept;
  cover art (including posters an earlier version of this remux demoted to a
  real video track), data and unknown-type streams are dropped. Re-running
  the repair on a file it already touched is safe.
  """
  if not os.path.isfile(path):
    if logger:
      logger.warning("Hybrid-aspect repair skipped, file missing: %s" % path)
    return False
  if ffmpeg_dir:
    ffmpeg = FFMpeg(
      ffmpeg_path=os.path.join(ffmpeg_dir, "ffmpeg"),
      ffprobe_path=os.path.join(ffmpeg_dir, "ffprobe"),
    )
  else:
    ffmpeg = FFMpeg()

  # Map the primary video stream by index: a poster demoted to a real video
  # track by the old "-map 0" remux has no attached_pic flag, so 0:V alone
  # would keep it. Fall back to 0:V when the probe gives nothing usable.
  video_map = "0:V?"
  try:
    info = ffmpeg.probe(path)
  except Exception:
    info = None
  if info is not None:
    video = primary_video_stream(info)
    if video is not None and isinstance(getattr(video, "index", None), int):
      video_map = "0:%d" % video.index

  aside = path + ".aspectfix"
  i = 2
  while os.path.isfile(aside):
    aside = path + ".aspectfix.%d" % i
    i += 1

  os.rename(path, aside)
  success = False
  try:
    # Map by type so data streams (tmcd, bin_data) and unknown-type streams
    # are never copied, and keep only the primary video stream: a plain
    # -map 0 remuxes the poster as a second real video track. Cover art is
    # always stripped, matching Converter.tag. The aspect fix is scoped to
    # the primary video stream; an unqualified -aspect applies to every
    # video output stream.
    opts = [
      "-i",
      aside,
      "-map",
      video_map,
      "-map",
      "0:a?",
      "-map",
      "0:s?",
      "-c",
      "copy",
      "-aspect:v:0",
      dar,
    ]
    for _timecode, _debug in ffmpeg.convert(path, opts, timeout=0):
      pass
    success = True
  except Exception as exc:
    if logger:
      logger.error("Hybrid-aspect repair failed for %s: %s" % (path, exc))
  finally:
    if success:
      try:
        os.remove(aside)
      except OSError:
        pass
      if logger:
        logger.info("Hybrid-aspect repair complete for %s (display aspect %s) [hybrid-aspect-repair]." % (path, dar))
    else:
      try:
        if os.path.isfile(path):
          os.remove(path)
      except OSError:
        pass
      try:
        os.rename(aside, path)
      except OSError:
        if logger:
          logger.error("Hybrid-aspect repair could not restore original %s from %s" % (path, aside))
  return success


__all__ = ["repair_hybrid_aspect"]
