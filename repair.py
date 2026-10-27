#!/opt/sma/venv/bin/python3
"""Repair damaged media in place without transcoding.

Probes each file (or every media file under each directory) with FFprobe and
fixes only stream-copy damage: data streams (tmcd timecode tracks, stray
bin_data, unknown-type streams), artwork (attached pictures, cover art demoted
to a real video track, image attachments) and the display aspect ratio of
outputs stretched by the former QSV alignment bug. Files that probe as normal
are skipped. See resources/media_repair.py for the rules.
"""

import argparse
import logging
import os
import sys

from converter.ffmpeg import FFMpeg
from resources.log import getLogger
from resources.media_repair import (
  STATUS_FAILED,
  STATUS_NORMAL,
  STATUS_REPAIRED,
  STATUS_WOULD_REPAIR,
  iter_targets,
  repair_file,
)

log = getLogger("REPAIR")


def _ffmpeg_paths(config):
  """Return (ffmpeg, ffprobe) paths from sma-ng.yml, or (None, None) to use PATH."""
  try:
    from resources.readsettings import ReadSettings

    settings = ReadSettings(config)
    return getattr(settings, "ffmpeg", None) or None, getattr(settings, "ffprobe", None) or None
  except Exception:
    if config:
      raise
    log.debug("sma-ng.yml not available; using ffmpeg/ffprobe from PATH")
    return None, None


def main(argv=None):
  """Parse CLI arguments, repair every damaged file, and exit.

  Exit codes: 0 when nothing failed, 1 when any repair failed, 2 for bad input.
  """
  parser = argparse.ArgumentParser(description="SMA-NG repair tool — stream-copy fixes for damaged media; never transcodes")
  parser.add_argument("paths", nargs="+", metavar="PATH", help="Media file or directory (repeatable)")
  parser.add_argument("-n", "--dry-run", action="store_true", help="Report what would be repaired; change nothing")
  parser.add_argument("-c", "--config", metavar="PATH", help="sma-ng.yml to read ffmpeg/ffprobe paths from")
  parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
  args = parser.parse_args(argv)

  if args.verbose:
    logging.getLogger().setLevel(logging.DEBUG)
    log.setLevel(logging.DEBUG)

  missing = [p for p in args.paths if not os.path.exists(p)]
  if missing:
    for p in missing:
      log.error("Path does not exist: %s" % p)
    sys.exit(2)

  try:
    ffmpeg_path, ffprobe_path = _ffmpeg_paths(args.config)
    ffmpeg = FFMpeg(ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path)
  except Exception as exc:
    log.error("Cannot locate ffmpeg/ffprobe: %s" % exc)
    sys.exit(2)

  counts = {STATUS_NORMAL: 0, STATUS_REPAIRED: 0, STATUS_WOULD_REPAIR: 0, STATUS_FAILED: 0}
  try:
    for target in iter_targets(args.paths):
      result = repair_file(target, ffmpeg, dry_run=args.dry_run, logger=log)
      counts[result.status] += 1
      if result.status == STATUS_NORMAL:
        log.debug("Normal, skipped: %s" % target)
      elif result.status == STATUS_WOULD_REPAIR:
        log.info("Would repair %s: %s [dry-run]" % (target, result.detail))
      elif result.status == STATUS_REPAIRED:
        log.info("Repaired %s: %s" % (target, result.detail))
      else:
        log.error("Repair failed for %s: %s" % (target, result.detail))
  except KeyboardInterrupt:
    log.info("Interrupted.")
    sys.exit(1)

  log.info("Repair finished: %d repaired, %d would repair, %d normal, %d failed" % (counts[STATUS_REPAIRED], counts[STATUS_WOULD_REPAIR], counts[STATUS_NORMAL], counts[STATUS_FAILED]))
  sys.exit(1 if counts[STATUS_FAILED] else 0)


if __name__ == "__main__":
  main()
