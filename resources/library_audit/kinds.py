"""Finding-kind enum and probe-time constants for the library auditor."""

from __future__ import annotations

from enum import Enum


class FindingKind(str, Enum):
  FFPROBE_FAILED = "ffprobe_failed"
  ORPHAN_SIDECAR = "orphan_sidecar"
  LEFTOVER_TMP = "leftover_tmp"
  PRECONV_ORIGINAL = "preconv_original"
  DUPLICATE_ID = "duplicate_id"
  HYBRID_ASPECT = "hybrid_aspect"
  STREAM_DAMAGE = "stream_damage"


# Per-kind hint stored on each library_audit_queue row so the worker knows
# which probe to run without re-deciding from the path alone.
KIND_HINT_MEDIA = "media"  # → ffprobe_check (and id-record if MP4)
KIND_HINT_SIDECAR = "sidecar"  # → orphan check
KIND_HINT_TMP = "tmp"  # → tmp_artifact_check
KIND_HINT_PRECONV = "preconv"  # → preconv_original_check


SIDECAR_EXTS = frozenset({".srt", ".sub", ".idx", ".ass", ".ssa", ".vtt", ".nfo", ".jpg", ".jpeg", ".png"})
TMP_EXTS = frozenset({".tmp", ".partial", ".tag"})
# `.tag` covers SMA's tag-step leftovers (Converter.tag renames the freshly
# converted output to `<file>.<ext>.tag` before re-muxing it; older releases
# left these behind on tagging failure).
TMP_SUFFIXES = (".2.mp4", ".3.mp4", ".4.mp4", ".5.mp4")  # recycle-bin collision leftovers
# In-transit SMA output: the default temp-extension (the configured one is
# matched too) and `.smatmp` atomic-copy partials, left by an interrupted
# conversion or repair. Only reported once older than
# IN_TRANSIT_MIN_AGE_SECONDS (the storage janitor's default max age) so a
# file still being written is never flagged.
IN_TRANSIT_EXTS = frozenset({".sma", ".smatmp"})
IN_TRANSIT_MIN_AGE_SECONDS = 21600
MEDIA_CONTAINER_EXTS = frozenset({".mp4", ".mkv", ".avi", ".mov", ".m4v", ".ts", ".m2ts", ".wmv", ".flv", ".webm"})
NON_MP4_CONTAINERS = frozenset({".mkv", ".avi", ".mov", ".m4v", ".ts", ".m2ts", ".wmv", ".flv", ".webm"})


__all__ = [
  "IN_TRANSIT_EXTS",
  "IN_TRANSIT_MIN_AGE_SECONDS",
  "KIND_HINT_MEDIA",
  "KIND_HINT_PRECONV",
  "KIND_HINT_SIDECAR",
  "KIND_HINT_TMP",
  "MEDIA_CONTAINER_EXTS",
  "NON_MP4_CONTAINERS",
  "SIDECAR_EXTS",
  "TMP_EXTS",
  "TMP_SUFFIXES",
  "FindingKind",
]
