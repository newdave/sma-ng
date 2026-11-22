"""Shared queue-admission helpers for the scanner and the webhook/queue handlers.

Every path that creates a job goes through these helpers so a file is judged
the same way no matter how it was discovered: paths are normalised before the
duplicate check, same-extension and recycle-bin files are skipped, and files
completed within the configured window are not queued again.
"""

import os

SKIP_SAME_EXTENSION = "same_as_output_extension"
SKIP_RECYCLE_BIN = "recycle_bin"


def normalise_job_path(path):
  """Collapse duplicate separators, ``.`` and ``..`` segments and trailing slashes."""
  if not path or not isinstance(path, str):
    return path
  return os.path.normpath(path)


def dedupe_window_seconds(path_config_manager):
  """Seconds a completed job blocks re-queueing its path (0 disables)."""
  value = getattr(path_config_manager, "job_dedupe_window_seconds", 0)
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return 0
  return max(0, int(value))


def skip_reason(path_config_manager, path):
  """Return why *path* must not be queued, or None. Only a real ``True`` counts as a hit."""
  skip_same_ext = getattr(path_config_manager, "should_skip_same_extension", None)
  if callable(skip_same_ext) and skip_same_ext(path) is True:
    return SKIP_SAME_EXTENSION
  is_recycle = getattr(path_config_manager, "is_recycle_bin_path", None)
  if callable(is_recycle) and is_recycle(path) is True:
    return SKIP_RECYCLE_BIN
  return None


def queue_job(job_db, path_config_manager, path, config, args=None, **kwargs):
  """Add a job with the shared dedupe rules. Returns the job id, or None when it was a duplicate."""
  return job_db.add_job(
    normalise_job_path(path),
    config,
    args,
    dedupe_window_seconds=dedupe_window_seconds(path_config_manager),
    **kwargs,
  )
