#!/usr/bin/env python3
"""
scripts/plexmatch.py — Generate or update .plexmatch sidecars without converting.

Usage:
    python scripts/plexmatch.py -i <file_or_dir> [options]

Options:
    -i, --input <path>      File or directory to process (required)
    -tmdb <id>              TMDB ID override
    -tvdb <id>              TVDB ID override
    -imdb <id>              IMDB ID override (tt prefix optional)
    -s, --season <n>        Season number (TV)
    -e, --episode <n>       Episode number (TV)
    -c, --config <path>     Override autoProcess.ini location (also: $SMA_CONFIG)
    --movie                 Force movie type when auto-detection is ambiguous
    --tv                    Force TV type when auto-detection is ambiguous
    -r, --recursive         Process all media files under a directory
    -h, --help              Show this help

Environment:
    SMA_CONFIG              Path to autoProcess.ini (overrides default location)

Each input file is processed independently: metadata is fetched from TMDB using
guessit filename inference (or the supplied IDs), then update_plexmatch() writes
or updates the .plexmatch sidecar in the appropriate directory.

No conversion, no FFmpeg, no tagging — plexmatch only.
"""

import argparse
import logging
import os
import sys

# Ensure repo root is on sys.path when run directly
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

MEDIA_EXTENSIONS = {
  "mp4",
  "mkv",
  "avi",
  "mov",
  "wmv",
  "m4v",
  "ts",
  "m2ts",
  "flv",
  "webm",
  "mpg",
  "mpeg",
  "divx",
  "xvid",
}


def _setup_logging(verbose: bool) -> logging.Logger:
  level = logging.DEBUG if verbose else logging.INFO
  logging.basicConfig(format="%(levelname)s: %(message)s", level=level)
  return logging.getLogger("plexmatch")


def _collect_files(path: str, recursive: bool) -> list:
  """Return a list of media file paths under *path*."""
  if os.path.isfile(path):
    return [path]
  if not os.path.isdir(path):
    print("Error: not a file or directory: %s" % path, file=sys.stderr)
    sys.exit(1)
  results = []
  if recursive:
    for root, _dirs, files in os.walk(path):
      for fname in sorted(files):
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
        if ext in MEDIA_EXTENSIONS:
          results.append(os.path.join(root, fname))
  else:
    for fname in sorted(os.listdir(path)):
      fpath = os.path.join(path, fname)
      if os.path.isfile(fpath):
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
        if ext in MEDIA_EXTENSIONS:
          results.append(fpath)
  return results


def _process_file(filepath, settings, args, log):
  """Fetch metadata for *filepath* and write its .plexmatch sidecar."""
  from resources.metadata import MediaType, Metadata, update_plexmatch

  try:
    import guessit as _guessit
  except ImportError:
    _guessit = None

  tmdbid = args.tmdb
  tvdbid = args.tvdb
  imdbid = args.imdb
  season = args.season
  episode = args.episode
  type_hint = "movie" if args.movie else ("tv" if args.tv else None)

  tagdata = None

  # --- Try guessit inference first when no explicit IDs are given ---
  if _guessit and not any([tmdbid, tvdbid, imdbid]):
    try:
      from manual import guessInfo

      tagdata = guessInfo(
        filepath,
        settings,
        tmdbid=tmdbid,
        tvdbid=tvdbid,
        imdbid=imdbid,
        season=season,
        episode=episode,
        language=settings.taglanguage or None,
        type_hint=type_hint,
      )
    except Exception:
      log.debug("guessit inference failed for %s", filepath, exc_info=True)

  # --- Fall back to explicit IDs ---
  if not tagdata and any([tmdbid, tvdbid, imdbid]):
    try:
      if type_hint == "movie" or (not type_hint and not season):
        tagdata = Metadata(
          MediaType.Movie,
          tmdbid=tmdbid,
          imdbid=imdbid,
          language=settings.taglanguage or None,
          logger=log,
        )
      else:
        tagdata = Metadata(
          MediaType.TV,
          tmdbid=tmdbid,
          tvdbid=tvdbid,
          imdbid=imdbid,
          season=season,
          episode=episode,
          language=settings.taglanguage or None,
          logger=log,
        )
    except Exception:
      log.exception("Metadata lookup failed for %s", filepath)

  if not tagdata:
    log.warning("Could not determine metadata for %s — skipping", filepath)
    return False

  # Force plexmatch enabled regardless of config, since that's the whole point
  settings.plexmatch_enabled = True
  update_plexmatch(filepath, tagdata, settings, log=log)
  return True


def main():
  parser = argparse.ArgumentParser(
    description="Generate or update .plexmatch sidecars without converting.",
    add_help=False,
  )
  parser.add_argument("-i", "--input", dest="input", required=True, help="File or directory to process")
  parser.add_argument("-tmdb", dest="tmdb", default=None, type=int, help="TMDB ID override")
  parser.add_argument("-tvdb", dest="tvdb", default=None, type=int, help="TVDB ID override")
  parser.add_argument("-imdb", dest="imdb", default=None, help="IMDB ID override")
  parser.add_argument("-s", "--season", dest="season", default=None, type=int, help="Season number (TV)")
  parser.add_argument("-e", "--episode", dest="episode", default=None, type=int, help="Episode number (TV)")
  parser.add_argument("-c", "--config", dest="config", default=None, help="Path to autoProcess.ini")
  parser.add_argument("--movie", action="store_true", help="Force movie type")
  parser.add_argument("--tv", action="store_true", help="Force TV type")
  parser.add_argument("-r", "--recursive", action="store_true", help="Recurse into subdirectories")
  parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
  parser.add_argument("-h", "--help", action="help", help="Show this help")

  args = parser.parse_args()

  if args.movie and args.tv:
    parser.error("--movie and --tv are mutually exclusive")

  log = _setup_logging(args.verbose)

  # Override config path if supplied
  if args.config:
    os.environ["SMA_CONFIG"] = args.config

  from resources.readsettings import ReadSettings

  try:
    settings = ReadSettings()
  except Exception:
    log.exception("Failed to load settings")
    sys.exit(1)

  files = _collect_files(args.input, args.recursive)
  if not files:
    log.error("No media files found at %s", args.input)
    sys.exit(1)

  ok = 0
  fail = 0
  for filepath in files:
    log.info("Processing: %s", filepath)
    if _process_file(filepath, settings, args, log):
      ok += 1
    else:
      fail += 1

  if len(files) > 1:
    log.info("Done. %d succeeded, %d skipped/failed.", ok, fail)

  sys.exit(1 if fail and not ok else 0)


if __name__ == "__main__":
  main()
