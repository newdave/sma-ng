#!/opt/sma/venv/bin/python3
"""Rename media files using SMA-NG naming templates without converting.

Supports single files and recursive directory trees.  Optionally updates
.plexmatch sidecars and triggers a Plex library refresh after renaming.

When no ``-c`` config is given, reads ``daemon.json`` (if present) to route
each file to the correct per-directory ``autoProcess.ini``, matching the
same logic the daemon uses for conversions.
"""

import argparse
import logging
import os
import sys

from resources.log import getLogger
from resources.readsettings import ReadSettings
from resources.rename_util import RenameProcessor

log = getLogger("RENAME")

logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("rebulk").setLevel(logging.WARNING)


def _print_results(results):
  renamed = 0
  unchanged = 0
  for r in results:
    old = r.get("old", "")
    new = r.get("new", "")
    dry_run = r.get("dry_run", False)
    changed = r.get("changed", False)
    if dry_run and changed:
      print("DRY-RUN: %s -> %s" % (old, new))
      renamed += 1
    elif changed:
      print("RENAMED: %s -> %s" % (old, new))
      renamed += 1
    else:
      print("UNCHANGED: %s" % old)
      unchanged += 1
  print("Renamed %d file(s), %d unchanged." % (renamed, unchanged))
  return renamed, unchanged


def _load_path_config_manager():
  """Try to load PathConfigManager from daemon.json. Returns instance or None."""
  try:
    from resources.daemon.config import PathConfigManager

    pcm = PathConfigManager()
    if pcm.path_configs:
      log.debug("Loaded daemon.json path routing (%d path(s))" % len(pcm.path_configs))
      return pcm
  except Exception:
    log.debug("daemon.json not available; using single config for all paths")
  return None


def main():
  """Parse CLI arguments and drive the rename workflow.

  Handles single files and directories. Exits with code 1 if no files
  were processed when the path was valid, or if an unexpected error occurs.
  """
  parser = argparse.ArgumentParser(description="SMA-NG rename tool — renames media files using naming templates")
  parser.add_argument("path", help="File or directory to rename")

  id_group = parser.add_argument_group("identification")
  id_group.add_argument("--tmdb", dest="tmdbid", metavar="ID", help="TMDB ID override")
  id_group.add_argument("--tvdb", dest="tvdbid", metavar="ID", help="TVDB ID override")
  id_group.add_argument("--imdb", dest="imdbid", metavar="ID", help="IMDB ID override")
  id_group.add_argument("-s", "--season", metavar="N", type=int, help="Season number (TV)")
  id_group.add_argument("-e", "--episode", metavar="N", type=int, action="append", help="Episode number (repeatable for multi-ep)")

  type_group = parser.add_mutually_exclusive_group()
  type_group.add_argument("--movie", action="store_true", help="Treat input as movie")
  type_group.add_argument("--tv", action="store_true", help="Treat input as TV show")

  parser.add_argument("--arr-rename", action="store_true", help="Delegate rename to Sonarr/Radarr's RenameFiles command instead of SMA templates")
  parser.add_argument("--dry-run", action="store_true", help="Print what would be renamed; make no changes")
  parser.add_argument("--no-plexmatch", action="store_true", help="Skip .plexmatch updates")
  parser.add_argument("--no-plex", action="store_true", help="Skip Plex library refresh")
  parser.add_argument("-c", "--config", metavar="PATH", help="Alternate autoProcess.ini path (disables daemon.json routing)")
  parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

  args = parser.parse_args()

  if args.verbose:
    logging.getLogger().setLevel(logging.DEBUG)
    log.setLevel(logging.DEBUG)

  try:
    if not os.path.exists(args.path):
      log.error("Path does not exist: %s" % args.path)
      sys.exit(1)

    # When an explicit config is given, use it for everything (old behaviour).
    # Otherwise try to load daemon.json for per-file path routing.
    pcm = None if args.config else _load_path_config_manager()

    # Cache ReadSettings + RenameProcessor instances by resolved config path
    # so each unique config is only parsed once across the whole directory walk.
    _rp_cache = {}

    def _get_rp(filepath):
      """Return a RenameProcessor appropriate for *filepath*."""
      if pcm:
        cfg = pcm.get_config_for_path(filepath)
      else:
        cfg = args.config  # None → ReadSettings uses env/default
      if cfg not in _rp_cache:
        _rp_cache[cfg] = RenameProcessor(ReadSettings(cfg), logger=log)
      return _rp_cache[cfg]

    # Explicit --movie / --tv overrides daemon.json default_args.
    explicit_type_hint = None
    if args.movie:
      explicit_type_hint = "movie"
    elif args.tv:
      explicit_type_hint = "tv"

    def _type_hint_for(filepath):
      """Resolve type_hint: explicit CLI flag > daemon.json default_args."""
      if explicit_type_hint:
        return explicit_type_hint
      if pcm:
        default_args = pcm.get_args_for_path(filepath)
        if "--movie" in default_args:
          return "movie"
        if "--tv" in default_args:
          return "tv"
      return None

    common_kwargs = dict(
      dry_run=args.dry_run,
      tmdbid=args.tmdbid,
      tvdbid=args.tvdbid,
      imdbid=args.imdbid,
      season=args.season,
      episode=args.episode,
      use_arr=args.arr_rename,
    )

    if os.path.isfile(args.path):
      rp = _get_rp(args.path)
      result = rp.rename_file(args.path, type_hint=_type_hint_for(args.path), **common_kwargs)
      results = [result]
      # Use whichever rp was used for plexmatch/plex (single file → single rp)
      primary_rp = rp
    else:
      results = _rename_directory(args.path, _get_rp, _type_hint_for, common_kwargs)
      # For plexmatch/plex we need a representative rp; use the first cached one.
      primary_rp = next(iter(_rp_cache.values())) if _rp_cache else _get_rp(args.path)

    _print_results(results)

    if not args.no_plexmatch:
      primary_rp.update_plexmatch_for_results(results)

    if not args.no_plex:
      primary_rp.refresh_plex_for_results(results)

    if not results:
      log.error("No files were processed under path: %s" % args.path)
      sys.exit(1)

  except SystemExit:
    raise
  except KeyboardInterrupt:
    log.info("Interrupted.")
    sys.exit(1)
  except Exception:
    log.exception("Unexpected error during rename")
    sys.exit(1)


def _iter_media_files(dirpath, media_exts):
  """Yield media file paths under *dirpath* using ``find`` for speed on large
  or slow (e.g. unionfs) mounts, falling back to ``os.walk`` if unavailable.

  ``find`` streams results immediately without buffering the entire tree,
  which is significantly faster than os.walk on filesystems with many
  directories.
  """
  import subprocess

  # Build find expression: ( -name "*.mp4" -o -name "*.mkv" ... )
  # No shell=True so parens are passed directly as find tokens, no escaping needed.
  name_parts = []
  for ext in sorted(media_exts):
    if name_parts:
      name_parts.append("-o")
    name_parts += ["-name", "*" + ext]
  find_args = ["find", dirpath, "("] + name_parts + [")", "-not", "-name", ".*", "-type", "f"]

  try:
    log.debug("find cmd: %s" % " ".join(find_args))
    proc = subprocess.Popen(find_args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    for line in proc.stdout:
      yield line.rstrip(b"\n").decode("utf-8", errors="replace")
    proc.wait()
  except Exception:
    # Fall back to os.walk
    for root, dirs, files in os.walk(dirpath):
      dirs[:] = [d for d in dirs if not d.startswith(".")]
      for filename in files:
        if filename.startswith("."):
          continue
        if os.path.splitext(filename)[1].lower() in media_exts:
          yield os.path.join(root, filename)


def _rename_directory(dirpath, get_rp, type_hint_for, common_kwargs):
  """Walk *dirpath* and rename each media file using per-file config routing."""
  _fallback_exts = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".ts", ".m2ts", ".flv", ".webm"}

  # Resolve media extensions from the first available settings object.
  # All configs in the same run typically share the same extension list.
  try:
    _rp = get_rp(dirpath)
    raw = getattr(_rp.settings, "input_extension", None)
    media_exts = set("." + e.lstrip(".") for e in raw) if raw else _fallback_exts
  except Exception:
    media_exts = _fallback_exts

  results = []
  for filepath in _iter_media_files(dirpath, media_exts):
    rp = get_rp(filepath)
    result = rp.rename_file(filepath, type_hint=type_hint_for(filepath), **common_kwargs)
    results.append(result)
  return results


if __name__ == "__main__":
  main()
