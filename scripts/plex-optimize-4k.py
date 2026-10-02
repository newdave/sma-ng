#!/usr/bin/env python3
"""Queue Plex Media Optimizer jobs for 4K content.

Companion to JBOPS utility/media_manager.py and follows its conventions
(PLEX_URL / PLEX_TOKEN environment variables, --libraries selection).
JBOPS' own ``--action optimize`` is an unimplemented stub, and its
selectors cannot filter by resolution, so this script drives
python-plexapi's ``optimize()`` directly: every 4K movie/episode gets a
"Custom: <profile>" optimized version (default: Universal TV @ 8 Mbps
1080p), created by Plex itself next to the original under
``Plex Versions/``.

Items that already carry an optimized version are skipped, so the script
is safe to re-run (idempotent) and to schedule.

Examples:
  PLEX_URL=http://plex:32400 PLEX_TOKEN=xxx ./plex-optimize-4k.py --dry-run
  ./plex-optimize-4k.py --libraries Movies "TV Shows" --limit 10
  ./plex-optimize-4k.py --profile "Universal Mobile" --quality 4mbps-720p
"""

from __future__ import annotations

import argparse
import os
import sys

try:
  from plexapi import sync
  from plexapi.server import PlexServer
except ImportError:
  print("python-plexapi is required: pip install plexapi", file=sys.stderr)
  sys.exit(1)

# CLI-friendly names for the plexapi sync VIDEO_QUALITY_* indexes.
QUALITIES = {
  "20mbps-1080p": sync.VIDEO_QUALITY_20_MBPS_1080p,
  "12mbps-1080p": sync.VIDEO_QUALITY_12_MBPS_1080p,
  "10mbps-1080p": sync.VIDEO_QUALITY_10_MBPS_1080p,
  "8mbps-1080p": sync.VIDEO_QUALITY_8_MBPS_1080p,
  "4mbps-720p": sync.VIDEO_QUALITY_4_MBPS_720p,
  "3mbps-720p": sync.VIDEO_QUALITY_3_MBPS_720p,
  "2mbps-720p": sync.VIDEO_QUALITY_2_MBPS_720p,
  "original": sync.VIDEO_QUALITY_ORIGINAL,
}

DEVICE_PROFILES = [
  "Android",
  "iOS",
  "Universal Mobile",
  "Universal TV",
  "Windows",
  "Windows Phone",
  "Xbox One",
]


def is_4k(video) -> bool:
  return any((m.videoResolution or "").lower() == "4k" for m in video.media)


def has_optimized_version(video) -> bool:
  video.reload(checkFiles=False)
  return any(m.isOptimizedVersion for m in video.media)


def iter_videos(section, episodes: bool):
  if section.type == "movie":
    yield from section.search(filters={"resolution": "4k"})
  elif section.type == "show" and episodes:
    for show in section.all():
      for ep in show.episodes():
        if is_4k(ep):
          yield ep


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--url", default=os.environ.get("PLEX_URL"), help="Plex server URL (env: PLEX_URL)")
  parser.add_argument("--token", default=os.environ.get("PLEX_TOKEN"), help="Plex token (env: PLEX_TOKEN)")
  parser.add_argument("--libraries", nargs="+", default=None, help="Library names to scan (default: every movie and show library)")
  parser.add_argument("--profile", default="Universal TV", choices=DEVICE_PROFILES, help="Custom quality device profile (default: Universal TV)")
  parser.add_argument("--quality", default="8mbps-1080p", choices=sorted(QUALITIES), help="Target quality (default: 8mbps-1080p)")
  parser.add_argument("--no-episodes", action="store_true", help="Skip show libraries (movies only)")
  parser.add_argument("--limit", type=int, default=None, help="Stop after queueing this many items")
  parser.add_argument("--dry-run", action="store_true", help="List what would be queued without queueing")
  args = parser.parse_args()

  if not args.url or not args.token:
    parser.error("--url/--token or PLEX_URL/PLEX_TOKEN are required")

  plex = PlexServer(args.url, args.token)
  sections = [s for s in plex.library.sections() if s.type in ("movie", "show")]
  if args.libraries:
    wanted = {name.lower() for name in args.libraries}
    missing = wanted - {s.title.lower() for s in sections}
    if missing:
      parser.error("unknown libraries: %s" % ", ".join(sorted(missing)))
    sections = [s for s in sections if s.title.lower() in wanted]

  quality = QUALITIES[args.quality]
  queued = skipped = 0
  for section in sections:
    print("== %s (%s)" % (section.title, section.type))
    for video in iter_videos(section, episodes=not args.no_episodes):
      if args.limit is not None and queued >= args.limit:
        print("limit of %d reached" % args.limit)
        return 0
      label = "%s (%s)" % (video.title, getattr(video, "year", "") or video.ratingKey)
      if has_optimized_version(video):
        skipped += 1
        print("  skip (already optimized): %s" % label)
        continue
      if args.dry_run:
        queued += 1
        print("  would queue: %s" % label)
        continue
      video.optimize(deviceProfile=args.profile, videoQuality=quality)
      queued += 1
      print("  queued: %s" % label)

  verb = "would queue" if args.dry_run else "queued"
  print("done: %s %d item(s), skipped %d already-optimized" % (verb, queued, skipped))
  return 0


if __name__ == "__main__":
  sys.exit(main())
