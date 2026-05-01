"""Finding-kind enum and probe-time constants for the library auditor."""

from __future__ import annotations

from enum import Enum


class FindingKind(str, Enum):
  FFPROBE_FAILED = "ffprobe_failed"
  ORPHAN_SIDECAR = "orphan_sidecar"
  LEFTOVER_TMP = "leftover_tmp"
  PRECONV_ORIGINAL = "preconv_original"
  DUPLICATE_ID = "duplicate_id"


# Per-kind hint stored on each library_audit_queue row so the worker knows
# which probe to run without re-deciding from the path alone.
KIND_HINT_MEDIA = "media"  # → ffprobe_check (and id-record if MP4)
KIND_HINT_SIDECAR = "sidecar"  # → orphan check
KIND_HINT_TMP = "tmp"  # → tmp_artifact_check
KIND_HINT_PRECONV = "preconv"  # → preconv_original_check


SIDECAR_EXTS = frozenset({".srt", ".sub", ".idx", ".ass", ".ssa", ".vtt", ".nfo", ".jpg", ".jpeg", ".png"})
TMP_EXTS = frozenset({".tmp", ".partial"})
TMP_SUFFIXES = (".2.mp4", ".3.mp4", ".4.mp4", ".5.mp4")  # recycle-bin collision leftovers
MEDIA_CONTAINER_EXTS = frozenset({".mp4", ".mkv", ".avi", ".mov", ".m4v", ".ts", ".m2ts", ".wmv", ".flv", ".webm"})
NON_MP4_CONTAINERS = frozenset({".mkv", ".avi", ".mov", ".m4v", ".ts", ".m2ts", ".wmv", ".flv", ".webm"})


__all__ = [
  "FindingKind",
  "KIND_HINT_MEDIA",
  "KIND_HINT_SIDECAR",
  "KIND_HINT_TMP",
  "KIND_HINT_PRECONV",
  "SIDECAR_EXTS",
  "TMP_EXTS",
  "TMP_SUFFIXES",
  "MEDIA_CONTAINER_EXTS",
  "NON_MP4_CONTAINERS",
]
