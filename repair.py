#!/opt/sma/venv/bin/python3
"""Repair damaged media in place without transcoding.

Probes each file (or every media file under each directory) and fixes only
stream-copy damage: data streams (tmcd timecode tracks, stray bin_data),
unknown streams, artwork (attached pictures, cover art demoted to a real video
track, image attachments) and the display aspect ratio of outputs stretched by
the former QSV alignment bug. Files that probe as normal are skipped. See
resources/media_repair.py for the rules.

Each file is repaired with the settings the daemon would convert it with:
without ``-c``, ``sma-ng.yml`` routing picks the config and profile per path,
so ffmpeg/ffprobe, ``output-directory``, ``temp-extension`` and permissions
match a conversion of that file. Plex is refreshed for every directory that
had a file repaired unless ``--no-plex`` is given.
"""

import argparse
import logging
import os
import sys

from autoprocess.plex import refreshPlex
from resources.log import getLogger
from resources.media_repair import (
  STATUS_FAILED,
  STATUS_NORMAL,
  STATUS_REPAIRED,
  STATUS_WOULD_REPAIR,
  RepairProcessor,
  iter_targets,
)
from resources.readsettings import ReadSettings

log = getLogger("REPAIR")

logging.getLogger("requests").setLevel(logging.WARNING)


def _load_path_config_manager():
  """PathConfigManager for sma-ng.yml routing, or None when it cannot be loaded."""
  try:
    from resources.daemon.config import PathConfigManager

    return PathConfigManager(logger=log)
  except Exception:
    log.debug("sma-ng.yml routing not available; using a single config for all paths")
    return None


def _plex_configured(settings) -> bool:
  """True when at least one Plex server is set up for refreshes."""
  instances = getattr(settings, "plex_instances", None)
  if isinstance(instances, list) and instances:
    return any(i.get("refresh", False) for i in instances)
  legacy = getattr(settings, "Plex", None) or {}
  return bool(legacy.get("host") and legacy.get("token"))


def _refresh_plex(repaired):
  """Refresh Plex once per directory that had a file repaired. *repaired* maps
  each repaired path to the RepairProcessor (and so settings) that fixed it."""
  refreshed = set()
  for path, rp in repaired.items():
    directory = os.path.dirname(path)
    if directory in refreshed or not _plex_configured(rp.settings):
      continue
    refreshed.add(directory)
    try:
      refreshPlex(rp.settings, path, log)
    except Exception:
      log.exception("Plex refresh failed for directory: %s" % directory)


def main(argv=None):
  """Parse CLI arguments, repair every damaged file, and exit.

  Exit codes: 0 when nothing failed, 1 when any repair failed or an
  unexpected error occurs.
  """
  parser = argparse.ArgumentParser(description="SMA-NG repair tool — stream-copy fixes for damaged media; never transcodes")
  parser.add_argument("paths", nargs="+", metavar="PATH", help="Media file or directory (repeatable)")
  parser.add_argument("-n", "--dry-run", action="store_true", help="Report what would be repaired; change nothing")
  parser.add_argument("-c", "--config", metavar="PATH", help="Alternate sma-ng.yml path (disables routing)")
  parser.add_argument("--no-plex", action="store_true", help="Skip the Plex refresh after repairs")
  parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
  args = parser.parse_args(argv)

  if args.verbose:
    # logging.ini pins the console handler at INFO, so the handlers need lowering too.
    for logger in (logging.getLogger(), log):
      logger.setLevel(logging.DEBUG)
      for handler in logger.handlers:
        handler.setLevel(logging.DEBUG)

  counts = {STATUS_NORMAL: 0, STATUS_REPAIRED: 0, STATUS_WOULD_REPAIR: 0, STATUS_FAILED: 0}
  repaired = {}
  try:
    missing = [p for p in args.paths if not os.path.exists(p)]
    if missing:
      for p in missing:
        log.error("Path does not exist: %s" % p)
      sys.exit(1)

    # An explicit config is used for everything; otherwise route each file
    # to the config + profile the daemon would convert it with.
    pcm = None if args.config else _load_path_config_manager()
    rp_cache = {}

    def _get_rp(path):
      if pcm:
        key = (pcm.get_config_for_path(path), pcm.get_profile_for_path(path))
      else:
        key = (args.config, None)
      if key not in rp_cache:
        rp_cache[key] = RepairProcessor(ReadSettings(key[0], logger=log, profile=key[1]), logger=log)
      return rp_cache[key]

    for target in iter_targets(args.paths):
      rp = _get_rp(target)
      result = rp.repair_file(target, dry_run=args.dry_run)
      counts[result.status] += 1
      if result.status == STATUS_NORMAL:
        log.debug("Normal, skipped: %s" % target)
      elif result.status == STATUS_WOULD_REPAIR:
        log.info("Would repair %s: %s [dry-run]" % (target, result.detail))
      elif result.status == STATUS_REPAIRED:
        log.info("Repaired %s: %s" % (target, result.detail))
        repaired[target] = rp
      else:
        log.error("Repair failed for %s: %s" % (target, result.detail))
  except SystemExit:
    raise
  except KeyboardInterrupt:
    log.info("Interrupted.")
    sys.exit(1)
  except Exception:
    log.exception("Unexpected error during repair")
    sys.exit(1)

  if repaired and not args.no_plex:
    _refresh_plex(repaired)

  log.info("Repair finished: %d repaired, %d would repair, %d normal, %d failed" % (counts[STATUS_REPAIRED], counts[STATUS_WOULD_REPAIR], counts[STATUS_NORMAL], counts[STATUS_FAILED]))
  sys.exit(1 if counts[STATUS_FAILED] else 0)


if __name__ == "__main__":
  main()
