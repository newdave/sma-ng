#!/usr/bin/env python3
"""Helpers for SMA trigger scripts.

This module keeps JSON parsing and payload construction out of shell scripts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _json_get(args: argparse.Namespace) -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(args.default)
        return 0

    value = payload
    for part in args.field.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            value = None
            break

    if value is None:
        print(args.default)
    elif isinstance(value, (dict, list)):
        print(json.dumps(value))
    else:
        print(value)
    return 0


def _build_generic(args: argparse.Namespace) -> int:
    payload = {"path": args.path}
    if args.arg:
        payload["args"] = args.arg
    if args.config:
        payload["config"] = args.config
    print(json.dumps(payload))
    return 0


def _build_radarr_env(_: argparse.Namespace) -> int:
    movie = {}
    tmdb_id = os.environ.get("radarr_movie_tmdbid", "").strip()
    if tmdb_id:
        movie["tmdbId"] = int(tmdb_id)
    imdb_id = os.environ.get("radarr_movie_imdbid", "").strip()
    if imdb_id:
        movie["imdbId"] = imdb_id

    payload = {
        "eventType": "Download",
        "movie": movie,
        "movieFile": {"path": os.environ.get("radarr_moviefile_path", "")},
    }
    config = os.environ.get("SMA_CONFIG", "").strip()
    if config:
        payload["config"] = config
    print(json.dumps(payload))
    return 0


def _build_sonarr_env(_: argparse.Namespace) -> int:
    series = {}
    tvdb_id = os.environ.get("sonarr_series_tvdbid", "").strip()
    if tvdb_id:
        series["tvdbId"] = int(tvdb_id)
    imdb_id = os.environ.get("sonarr_series_imdbid", "").strip()
    if imdb_id:
        series["imdbId"] = imdb_id

    season = os.environ.get("sonarr_episodefile_seasonnumber", "").strip()
    episodes = []
    for episode in os.environ.get("sonarr_episodefile_episodenumbers", "").split(","):
        episode = episode.strip()
        if not episode:
            continue
        entry = {"episodeNumber": int(episode)}
        if season:
            entry["seasonNumber"] = int(season)
        episodes.append(entry)

    payload = {
        "eventType": "Download",
        "series": series,
        "episodes": episodes,
        "episodeFile": {"path": os.environ.get("sonarr_episodefile_path", "")},
    }
    config = os.environ.get("SMA_CONFIG", "").strip()
    if config:
        payload["config"] = config
    print(json.dumps(payload))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JSON helpers for SMA trigger scripts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    get_parser = subparsers.add_parser("get", help="Read a field from JSON on stdin")
    get_parser.add_argument("--field", required=True)
    get_parser.add_argument("--default", default="")
    get_parser.set_defaults(func=_json_get)

    generic_parser = subparsers.add_parser("build-generic", help="Build generic webhook JSON")
    generic_parser.add_argument("--path", required=True)
    generic_parser.add_argument("--config", default="")
    generic_parser.add_argument("--arg", action="append", default=[])
    generic_parser.set_defaults(func=_build_generic)

    radarr_parser = subparsers.add_parser("build-radarr-env", help="Build Radarr webhook JSON from environment")
    radarr_parser.set_defaults(func=_build_radarr_env)

    sonarr_parser = subparsers.add_parser("build-sonarr-env", help="Build Sonarr webhook JSON from environment")
    sonarr_parser.set_defaults(func=_build_sonarr_env)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
