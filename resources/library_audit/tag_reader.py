"""Read TMDB/TVDB/IMDB ids from MP4 files written by SMA-NG.

The current writer (resources/metadata.py) stores ids in dedicated freeform
atoms ``----:com.apple.iTunes:{TMDB,TVDB,IMDB}``. Older files written before
that change won't have these atoms and will return ``{}`` here — they simply
won't contribute to duplicate-by-id detection until they're re-tagged.
"""

from __future__ import annotations

from typing import Any

try:
  from mutagen.mp4 import MP4, MP4StreamInfoError  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - mutagen is a hard dependency in setup/requirements.txt
  MP4 = None  # type: ignore[misc, assignment]

  class MP4StreamInfoError(Exception):  # type: ignore[no-redef]
    pass


_ATOM_TMDB = "----:com.apple.iTunes:TMDB"
_ATOM_TVDB = "----:com.apple.iTunes:TVDB"
_ATOM_IMDB = "----:com.apple.iTunes:IMDB"
_ATOM_STIK = "stik"
_ATOM_TVSH = "tvsh"
_ATOM_TVSN = "tvsn"
_ATOM_TVES = "tves"


def _decode_freeform(value: Any) -> str | None:
  """mutagen returns freeform values as ``[bytes]`` or ``[MP4FreeForm]``."""
  if not value:
    return None
  raw = value[0] if isinstance(value, (list, tuple)) else value
  if isinstance(raw, (bytes, bytearray)):
    try:
      return raw.decode("utf-8", errors="replace").strip() or None
    except Exception:
      return None
  text = str(raw).strip()
  return text or None


def read_media_ids(path: str) -> dict[str, Any]:
  """Return ``{"tmdb_id"?, "tvdb_id"?, "imdb_id"?, "media_type"?, "tvsh"?, "season"?, "episode"?}``.

  Returns ``{}`` for non-MP4 containers, unreadable files, or files without
  any of the SMA-NG id atoms.
  """
  if MP4 is None:
    return {}
  try:
    video = MP4(path)
  except (MP4StreamInfoError, FileNotFoundError, KeyError, OSError):
    return {}
  except Exception:  # type: ignore[unreachable]  # pragma: no cover
    return {}

  tags = video.tags or {}
  out: dict[str, Any] = {}

  tmdb = _decode_freeform(tags.get(_ATOM_TMDB))
  if tmdb:
    out["tmdb_id"] = tmdb
  tvdb = _decode_freeform(tags.get(_ATOM_TVDB))
  if tvdb:
    out["tvdb_id"] = tvdb
  imdb = _decode_freeform(tags.get(_ATOM_IMDB))
  if imdb:
    out["imdb_id"] = imdb

  stik = tags.get(_ATOM_STIK)
  if stik:
    code = stik[0] if isinstance(stik, (list, tuple)) else stik
    if code == 9:
      out["media_type"] = "movie"
    elif code == 10:
      out["media_type"] = "tv"

  tvsh = tags.get(_ATOM_TVSH)
  if tvsh:
    raw = tvsh[0] if isinstance(tvsh, (list, tuple)) else tvsh
    if isinstance(raw, (bytes, bytearray)):
      out["tvsh"] = raw.decode("utf-8", errors="replace")
    else:
      out["tvsh"] = str(raw)

  tvsn = tags.get(_ATOM_TVSN)
  if tvsn:
    try:
      out["season"] = int(tvsn[0] if isinstance(tvsn, (list, tuple)) else tvsn)
    except (TypeError, ValueError):
      pass
  tves = tags.get(_ATOM_TVES)
  if tves:
    try:
      out["episode"] = int(tves[0] if isinstance(tves, (list, tuple)) else tves)
    except (TypeError, ValueError):
      pass

  return out


def derive_media_id(ids: dict[str, Any]) -> str | None:
  """Pick the most stable identifier for cross-path duplicate matching.

  Movie: ``movie:tmdb:<id>`` (preferred) or ``movie:imdb:<id>``.
  TV    : ``tv:tmdb:<id>:s<n>e<n>`` (per-episode dedupe) or ``tv:imdb:<id>:...``.
  Returns ``None`` when nothing useful is present.
  """
  mtype = ids.get("media_type") or ("tv" if "tvsh" in ids else None)
  tmdb = ids.get("tmdb_id")
  imdb = ids.get("imdb_id")
  tvdb = ids.get("tvdb_id")

  if mtype == "tv":
    season = ids.get("season")
    episode = ids.get("episode")
    if season is None or episode is None:
      return None
    suffix = "s%02de%02d" % (int(season), int(episode))
    if tmdb:
      return "tv:tmdb:%s:%s" % (tmdb, suffix)
    if tvdb:
      return "tv:tvdb:%s:%s" % (tvdb, suffix)
    if imdb:
      return "tv:imdb:%s:%s" % (imdb, suffix)
    return None

  # Default to movie semantics when stik=9 or no TV markers.
  if tmdb:
    return "movie:tmdb:%s" % tmdb
  if imdb:
    return "movie:imdb:%s" % imdb
  return None


__all__ = ["derive_media_id", "read_media_ids"]
