"""Shared queue-admission helpers for the scanner and the webhook/queue handlers.

Every path that creates a job goes through these helpers so a file is judged
the same way no matter how it was discovered: paths are normalised before the
duplicate check, same-extension and recycle-bin files are skipped, and files
completed within the configured window are not queued again.
"""

import os
from dataclasses import dataclass

from resources.daemon import metrics_prom

SKIP_SAME_EXTENSION = "same_as_output_extension"
SKIP_RECYCLE_BIN = "recycle_bin"
SKIP_RECENTLY_COMPLETED = "recently_completed"
NO_ROUTING_MATCH = "no_routing_match"
DUPLICATE = "duplicate"

# queue_media_path outcome statuses (DUPLICATE doubles as one).
QUEUED = "queued"
SKIPPED = "skipped"
REFUSED = "refused"


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


def duplicate_outcome(job_db, path_config_manager, path, kind="convert"):
  """Explain why ``queue_job`` returned None: ``(DUPLICATE | SKIP_RECENTLY_COMPLETED, job_id or None)``."""
  path = normalise_job_path(path)
  existing = job_db.find_active_job(path, kind=kind) if kind != "convert" else job_db.find_active_job(path)
  if existing:
    return DUPLICATE, existing["id"]
  window = dedupe_window_seconds(path_config_manager)
  if window > 0:
    recent = job_db.find_recent_completed_job(path, kind, window)
    if recent:
      return SKIP_RECENTLY_COMPLETED, recent["id"]
  return DUPLICATE, None


def resolve_profile(path_config_manager, path):
  """Routing profile for *path*, or None. A failing lookup queues the job without one."""
  getter = getattr(path_config_manager, "get_profile_for_path", None)
  if not callable(getter):
    return None
  try:
    profile = getter(path)
  except Exception:
    return None
  return profile if isinstance(profile, str) and profile else None


def merge_default_args(path_config_manager, path, extra_args):
  """Prepend ``daemon.default-args`` to *extra_args*; a flag the caller passes drops the default."""
  getter = getattr(path_config_manager, "get_args_for_path", None)
  default_args = getter(path) if callable(getter) else None
  if not isinstance(default_args, (list, tuple)) or not default_args:
    return list(extra_args)
  caller_flags = {a.lstrip("-") for a in extra_args if a.startswith("-")}
  filtered_defaults = [a for a in default_args if not a.startswith("-") or a.lstrip("-") not in caller_flags]
  return filtered_defaults + list(extra_args)


def merge_profile_arg(args, profile):
  """Append ``--profile <profile>`` unless the caller already supplied one."""
  merged = list(args)
  if profile and "--profile" not in merged and "-p" not in merged:
    merged.extend(["--profile", profile])
  return merged


@dataclass(frozen=True)
class QueueOutcome:
  """Result of ``queue_media_path``: ``status`` is QUEUED, SKIPPED, REFUSED or DUPLICATE."""

  status: str
  path: str
  reason: str | None = None
  job_id: int | None = None
  config: str | None = None
  profile: str | None = None


def queue_media_path(job_db, path_config_manager, path, *, extra_args=(), config_override=None, max_retries=0, request_source=None):
  """Admit one media file: normalise, skip/strict-routing checks, resolve config/profile/args, dedupe, add the job.

  Shared by the scanner and the webhook/queue handlers; callers keep their own
  logging, responses and bookkeeping. Strict routing is waived when an explicit
  *config_override* is given, which also suppresses the routing profile.
  """
  pcm = path_config_manager
  path = normalise_job_path(path)
  reason = skip_reason(pcm, path)
  if reason:
    return QueueOutcome(SKIPPED, path, reason)
  if getattr(pcm, "strict_routing", False) and not config_override:
    has_match = getattr(pcm, "has_routing_match", None)
    if callable(has_match) and not has_match(path):
      return QueueOutcome(REFUSED, path, NO_ROUTING_MATCH)
  if config_override and os.path.exists(config_override):
    config = config_override
  else:
    config = pcm.get_config_for_path(path)
  profile = None if config_override else resolve_profile(pcm, path)
  args = merge_profile_arg(merge_default_args(pcm, path, extra_args), profile)
  job_id = queue_job(job_db, pcm, path, config, args, max_retries=max_retries, request_source=request_source, request_profile=profile)
  if job_id is None:
    outcome, existing_id = duplicate_outcome(job_db, pcm, path)
    if outcome == SKIP_RECENTLY_COMPLETED:
      return QueueOutcome(SKIPPED, path, outcome, existing_id, config, profile)
    return QueueOutcome(DUPLICATE, path, None, existing_id, config, profile)
  metrics_prom.record_job_enqueued(request_source, profile)
  return QueueOutcome(QUEUED, path, None, job_id, config, profile)
