"""In-place repair for hybrid-aspect (stretched) MP4s.

The lossless repair path: remux the file with stream-copy and stamp the true
display aspect ratio (``-aspect W:H``) into the container, so players render
the picture undistorted without a re-encode. Storage dimensions are left
unchanged — a subsequent :func:`resources.library_audit.probes.hybrid_aspect_check`
sees the non-square PAR and no longer flags the file.

Follows the same rename-aside/restore-on-failure pattern as
``Converter.tag``: the original is moved to ``<file>.aspectfix``, the remux
writes back to the original name, and any failure removes the partial output
and restores the original.
"""

from __future__ import annotations

import os

from converter.ffmpeg import FFMpeg


def repair_hybrid_aspect(path: str, dar: str, ffmpeg_dir: str | None = None, logger=None) -> bool:
  """Remux *path* in place with a corrective display aspect ratio *dar*
  (``"W:H"``, e.g. ``"1920:1080"``). Returns True on success.
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

  aside = path + ".aspectfix"
  i = 2
  while os.path.isfile(aside):
    aside = path + ".aspectfix.%d" % i
    i += 1

  os.rename(path, aside)
  success = False
  try:
    opts = [
      "-i",
      aside,
      "-map",
      "0",
      "-c",
      "copy",
      "-aspect",
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
