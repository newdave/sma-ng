"""Walk audit roots and yield ``(path, kind_hint)`` work units."""

from __future__ import annotations

import os
from collections.abc import Iterator

from resources.library_audit.kinds import (
  KIND_HINT_MEDIA,
  KIND_HINT_PRECONV,
  KIND_HINT_SIDECAR,
  KIND_HINT_TMP,
  MEDIA_CONTAINER_EXTS,
  NON_MP4_CONTAINERS,
  SIDECAR_EXTS,
  TMP_EXTS,
  TMP_SUFFIXES,
)


def _is_skip_dir(name: str, skip_set: set[str]) -> bool:
  """Case-insensitive directory-name match (Plex extras dirs)."""
  return name.lower() in skip_set


def enumerate_paths(
  roots: list[str],
  skip_dirs: list[str] | None = None,
  is_recycle_bin_path=None,
) -> Iterator[tuple[str, str]]:
  """Yield ``(absolute_path, kind_hint)`` tuples.

  *roots* is a list of directory paths. *skip_dirs* is a list of directory
  basenames that should be ignored anywhere in the tree (matched
  case-insensitively). *is_recycle_bin_path* is an optional callable that
  takes a path and returns True when the path lives inside a configured
  recycle-bin directory; recycle-bin contents are not audited.
  """
  skip_set = {s.lower() for s in (skip_dirs or [])}
  for root in roots:
    if not root or not os.path.isdir(root):
      continue
    if is_recycle_bin_path and is_recycle_bin_path(root):
      continue
    yield from _walk_one(root, skip_set, is_recycle_bin_path)


def _walk_one(root: str, skip_set: set[str], is_recycle_bin_path):
  stack = [root]
  while stack:
    current = stack.pop()
    try:
      with os.scandir(current) as it:
        subdirs = []
        for entry in it:
          if entry.name.startswith("."):
            continue
          try:
            is_dir = entry.is_dir(follow_symlinks=False)
          except OSError:
            continue
          if is_dir:
            if _is_skip_dir(entry.name, skip_set):
              continue
            if is_recycle_bin_path and is_recycle_bin_path(entry.path):
              continue
            subdirs.append(entry.path)
            continue
          hint = _classify(entry.name)
          if hint is not None:
            yield (entry.path, hint)
        stack.extend(reversed(subdirs))
    except (PermissionError, OSError):
      continue


def _classify(filename: str) -> str | None:
  ext = os.path.splitext(filename)[1].lower()
  if ext in SIDECAR_EXTS:
    return KIND_HINT_SIDECAR
  if ext in TMP_EXTS or any(filename.endswith(suf) for suf in TMP_SUFFIXES):
    return KIND_HINT_TMP
  if ext == ".mp4":
    return KIND_HINT_MEDIA
  if ext in NON_MP4_CONTAINERS:
    return KIND_HINT_PRECONV  # may fall through to media probe if no mp4 sibling
  if ext in MEDIA_CONTAINER_EXTS:
    return KIND_HINT_MEDIA
  return None


__all__ = ["enumerate_paths"]
