"""Output-directory storage helpers for the SMA-NG daemon.

Two pure helpers:

* :func:`sweep_output_directory` removes leftover transcode artefacts
  (configured ``temp_extension`` files, ``*.smatmp`` atomic-copy partials,
  and zero-byte ``*.mp4`` finals) older than ``max_age_seconds``.
* :func:`output_dir_usage` is a thin wrapper around :func:`shutil.disk_usage`
  that tolerates ENOENT / OSError by returning zeros so the Prometheus
  scrape loop never crashes on a missing or unreadable output directory.

Both functions are intentionally side-effect-free apart from filesystem
operations (in the sweeper's case) and have no daemon-internal state, so
they can be exercised directly from tests.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass

from resources.log import getLogger

log = getLogger("DAEMON")


@dataclass(frozen=True)
class SweptSummary:
  """Counts and freed bytes returned by :func:`sweep_output_directory`."""

  sma_count: int = 0
  smatmp_count: int = 0
  empty_mp4_count: int = 0
  freed_bytes: int = 0


@dataclass(frozen=True)
class DiskUsage:
  """Bytes triple returned by :func:`output_dir_usage`."""

  total: int = 0
  used: int = 0
  free: int = 0


def _normalise_temp_ext(temp_ext: str) -> str:
  """Return ``temp_ext`` as ``.<ext>`` with a leading dot, or ``""`` if blank."""
  if not temp_ext:
    return ""
  if temp_ext.startswith("."):
    return temp_ext
  return "." + temp_ext


def sweep_output_directory(output_dir: str, temp_ext: str, max_age_seconds: int) -> SweptSummary:
  """Remove orphaned transcode artefacts from *output_dir*.

  Sweeps three classes of files older than *max_age_seconds*:

  * Files ending in the configured ``temp_extension`` (default ``.sma``) —
    abandoned mid-transcode outputs.
  * Files ending in ``.smatmp`` — partial atomic copies left when
    ``shutil.copy`` was interrupted.
  * Zero-byte ``*.mp4`` files — finals where QTFS moov relocation or atomic
    rename was killed before any bytes were written.

  Missing directories / permission errors return a zero summary and log a
  WARNING; the daemon must never crash the janitor loop on a transient FS
  issue.
  """
  if not output_dir or max_age_seconds is None or max_age_seconds <= 0:
    return SweptSummary()

  norm_temp_ext = _normalise_temp_ext(temp_ext) or ".sma"
  now = time.time()
  cutoff = now - float(max_age_seconds)

  sma_count = 0
  smatmp_count = 0
  empty_mp4_count = 0
  freed_bytes = 0

  try:
    entries = list(os.scandir(output_dir))
  except FileNotFoundError:
    log.warning("StorageJanitor: output_dir does not exist: %s" % output_dir)
    return SweptSummary()
  except (PermissionError, OSError) as exc:
    log.warning("StorageJanitor: cannot scan %s: %s" % (output_dir, exc))
    return SweptSummary()

  for entry in entries:
    try:
      if not entry.is_file(follow_symlinks=False):
        continue
    except OSError:
      continue
    name_lower = entry.name.lower()
    kind: str | None = None
    if name_lower.endswith(norm_temp_ext.lower()):
      kind = "sma"
    elif name_lower.endswith(".smatmp"):
      kind = "smatmp"
    elif name_lower.endswith(".mp4"):
      kind = "empty_mp4"
    else:
      continue

    try:
      st = entry.stat(follow_symlinks=False)
    except OSError:
      continue

    if st.st_mtime > cutoff:
      continue

    if kind == "empty_mp4" and st.st_size > 0:
      continue

    try:
      os.remove(entry.path)
    except OSError as exc:
      log.warning("StorageJanitor: failed to remove %s: %s" % (entry.path, exc))
      continue

    freed_bytes += int(st.st_size)
    if kind == "sma":
      sma_count += 1
    elif kind == "smatmp":
      smatmp_count += 1
    elif kind == "empty_mp4":
      empty_mp4_count += 1

  return SweptSummary(
    sma_count=sma_count,
    smatmp_count=smatmp_count,
    empty_mp4_count=empty_mp4_count,
    freed_bytes=freed_bytes,
  )


def output_dir_usage(output_dir: str) -> DiskUsage:
  """Return total/used/free bytes for the filesystem containing *output_dir*.

  Missing directory / permission error → ``DiskUsage(0, 0, 0)``. Prometheus
  scrapes call this on every collect(); we MUST NOT raise.
  """
  if not output_dir:
    return DiskUsage()
  try:
    usage = shutil.disk_usage(output_dir)
  except FileNotFoundError:
    return DiskUsage()
  except (PermissionError, OSError):
    return DiskUsage()
  return DiskUsage(total=int(usage.total), used=int(usage.used), free=int(usage.free))


def clear_output_directory(output_dir: str) -> int:
  """Remove every entry under *output_dir* (files, dirs, symlinks) and
  recreate the directory itself empty. Returns the number of bytes freed.

  Intended for daemon startup when the operator wants a clean slate —
  any leftover partial transcode is unrecoverable across a daemon
  restart anyway (no resume support), so wiping them frees space and
  removes confusion. Missing / unreadable dir is a no-op (returns 0).
  """
  if not output_dir:
    return 0
  freed = 0
  try:
    entries = list(os.scandir(output_dir))
  except FileNotFoundError:
    return 0
  except (PermissionError, OSError) as exc:
    log.warning("clear_output_directory: cannot scan %s: %s" % (output_dir, exc))
    return 0
  for entry in entries:
    try:
      st = entry.stat(follow_symlinks=False)
      size = int(st.st_size)
    except OSError:
      size = 0
    try:
      if entry.is_dir(follow_symlinks=False):
        shutil.rmtree(entry.path, ignore_errors=True)
      else:
        os.remove(entry.path)
      freed += size
    except OSError as exc:
      log.warning("clear_output_directory: failed to remove %s: %s" % (entry.path, exc))
  return freed


__all__ = ["DiskUsage", "SweptSummary", "clear_output_directory", "output_dir_usage", "sweep_output_directory"]
