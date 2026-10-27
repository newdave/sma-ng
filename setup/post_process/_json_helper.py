#!/usr/bin/env python3
"""JSON helper for the SMA-NG *_post_process.sh scripts.

Keeps JSON handling out of shell scripts. Uses only the standard library so it
runs with the system ``python3`` on download-client hosts.

Subcommands:

    get KEY [DEFAULT]       Read a JSON object from stdin, print obj[KEY] (or DEFAULT).
                            Exits 1 when stdin is not valid JSON.
    path-payload PATH       Print {"path": PATH} as JSON.
    payload PATH ARGS_JSON  Print {"path": PATH, "args": <ARGS_JSON>} as JSON.
    sonarr-args TVDB SEASON EPISODES IMDB
                            Print the Sonarr webhook args list as JSON. EPISODES is a
                            comma-separated list; empty TVDB/IMDB values are skipped.
    radarr-args TMDB IMDB   Print the Radarr webhook args list as JSON. Empty values are skipped.
"""

from __future__ import annotations

import json
import sys


def _get(argv: list[str]) -> int:
  key = argv[0]
  default = argv[1] if len(argv) > 1 else ""
  try:
    data = json.load(sys.stdin)
    value = data.get(key, default)
  except (ValueError, AttributeError):
    return 1
  print(value)
  return 0


def _sonarr_args(tvdb: str, season: str, episodes: str, imdb: str) -> list[str]:
  args: list[str] = []
  if tvdb:
    args += ["-tvdb", tvdb, "-s", season]
    for ep in episodes.split(","):
      ep = ep.strip()
      if ep:
        args += ["-e", ep]
  if imdb:
    args += ["-imdb", imdb]
  return args


def _radarr_args(tmdb: str, imdb: str) -> list[str]:
  args: list[str] = []
  if tmdb:
    args += ["-tmdb", tmdb]
  if imdb:
    args += ["-imdb", imdb]
  return args


def main(argv: list[str]) -> int:
  if not argv:
    print(__doc__, file=sys.stderr)
    return 2
  cmd, rest = argv[0], argv[1:]
  if cmd == "get" and rest:
    return _get(rest)
  if cmd == "path-payload" and len(rest) == 1:
    print(json.dumps({"path": rest[0]}))
    return 0
  if cmd == "payload" and len(rest) == 2:
    print(json.dumps({"path": rest[0], "args": json.loads(rest[1])}))
    return 0
  if cmd == "sonarr-args" and len(rest) == 4:
    print(json.dumps(_sonarr_args(*rest)))
    return 0
  if cmd == "radarr-args" and len(rest) == 2:
    print(json.dumps(_radarr_args(*rest)))
    return 0
  print(__doc__, file=sys.stderr)
  return 2


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
