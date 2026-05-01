"""Standalone recycle-bin helper for the auditor's auto-clean path.

Mirrors :py:meth:`resources.mediaprocessor.MediaProcessor._recycle_to_bin` so
sidecars/leftovers are moved (atomic copy + unlink) rather than ``os.unlink``ed
outright. The auditor never holds a ``MediaProcessor`` instance, so the logic
is duplicated here in standalone form.
"""

from __future__ import annotations

import os
import shutil


def _next_collision_dst(directory: str, basename: str) -> str:
  base, ext = os.path.splitext(basename)
  candidate = os.path.join(directory, basename)
  i = 2
  while os.path.exists(candidate):
    candidate = os.path.join(directory, "%s.%d%s" % (base, i, ext))
    i += 1
  return candidate


def move_to_recycle_bin(path: str, recycle_bin: str | None) -> str | None:
  """Atomic copy *path* to *recycle_bin* then unlink the source.

  Returns the destination path on success, ``None`` when no recycle-bin
  is configured (caller should treat that as dry-run) or when *path*
  does not exist.

  Raises :class:`OSError` on copy/unlink failure so the caller can mark
  the finding as un-auto-fixable.
  """
  if not recycle_bin:
    return None
  if not os.path.isfile(path):
    return None
  os.makedirs(recycle_bin, exist_ok=True)
  dst = _next_collision_dst(recycle_bin, os.path.basename(path))
  dst_tmp = dst + ".smatmp"
  try:
    shutil.copy2(path, dst_tmp)
    os.replace(dst_tmp, dst)
  except Exception:
    try:
      if os.path.exists(dst_tmp):
        os.remove(dst_tmp)
    except Exception:
      pass
    raise
  os.remove(path)
  return dst


__all__ = ["move_to_recycle_bin"]
