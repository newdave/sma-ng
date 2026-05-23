"""AuditEngine — per-unit probing, finding upsert, and (optional) auto-fix.

Used in two modes:

* Daemon: instantiated by :class:`resources.daemon.threads.LibraryAuditWorkerThread`
  with ``job_db`` and ``path_config_manager``; each claimed queue unit is fed
  to :py:meth:`probe_one` and the result is upserted via
  ``job_db.upsert_finding``.
* CLI: :func:`run_audit_inline` walks the roots in-process, prints findings,
  and exits non-zero when any finding is produced. No DB is touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from resources.library_audit.enumerator import enumerate_paths
from resources.library_audit.kinds import (
  KIND_HINT_MEDIA,
  KIND_HINT_PRECONV,
  KIND_HINT_SIDECAR,
  KIND_HINT_TMP,
  FindingKind,
)
from resources.library_audit.probes import (
  ffprobe_check,
  preconv_original_check,
  sidecar_orphan_check,
  tmp_artifact_check,
)
from resources.library_audit.recycler import move_to_recycle_bin
from resources.library_audit.tag_reader import derive_media_id, read_media_ids


@dataclass
class Finding:
  kind: FindingKind
  path: str
  details: dict[str, Any] = field(default_factory=dict)


class AuditEngine:
  """Daemon-side glue between the audit queue and the findings table."""

  def __init__(
    self,
    job_db,
    path_config_manager,
    logger,
    *,
    ffmpeg_dir: str | None = None,
    dry_run: bool = True,
    auto_fix=None,
  ):
    self.job_db = job_db
    self.pcm = path_config_manager
    self.log = logger
    self.ffmpeg_dir = ffmpeg_dir
    self.dry_run = dry_run
    self.auto_fix = auto_fix  # AuditAutoFix instance or None

  # ------------------------------------------------------------------
  # Probing
  # ------------------------------------------------------------------

  def probe_one(self, unit: dict) -> Finding | None:
    """Run the right probe for *unit*'s kind_hint and return a Finding (or None)."""
    path = unit["path"]
    hint = unit["kind_hint"]
    audit_id = unit.get("audit_id")
    try:
      if hint == KIND_HINT_MEDIA:
        return self._probe_media(path, audit_id)
      if hint == KIND_HINT_PRECONV:
        return self._probe_preconv(path, audit_id)
      if hint == KIND_HINT_SIDECAR:
        details = sidecar_orphan_check(path)
        if details is None:
          return None
        return Finding(FindingKind.ORPHAN_SIDECAR, path, details)
      if hint == KIND_HINT_TMP:
        details = tmp_artifact_check(path)
        if details is None:
          return None
        return Finding(FindingKind.LEFTOVER_TMP, path, details)
    except Exception:
      self.log.exception("Audit probe failed for %s" % path)
      return None
    return None

  def _probe_media(self, path: str, audit_id: int | None) -> Finding | None:
    fail = ffprobe_check(path, ffmpeg_dir=self.ffmpeg_dir)
    if fail is not None:
      return Finding(FindingKind.FFPROBE_FAILED, path, fail)
    # Side effect: record media id for cross-path duplicate rollup.
    if audit_id and path.lower().endswith(".mp4"):
      try:
        ids = read_media_ids(path)
        media_id = derive_media_id(ids)
        if media_id:
          self.job_db.record_media_id(audit_id, path, media_id)
      except Exception:
        self.log.exception("Audit id-record failed for %s" % path)
    return None

  def _probe_preconv(self, path: str, audit_id: int | None) -> Finding | None:
    candidate = preconv_original_check(path)
    if candidate is None:
      # Treat as a regular media probe — some non-mp4 files have no mp4 sibling.
      return self._probe_media(path, audit_id)
    mp4_sibling = candidate["mp4_sibling"]
    sibling_fail = ffprobe_check(mp4_sibling, ffmpeg_dir=self.ffmpeg_dir)
    if sibling_fail is not None:
      # The mp4 is bad; the original is NOT stale — skip.
      return None
    return Finding(FindingKind.PRECONV_ORIGINAL, path, candidate)

  # ------------------------------------------------------------------
  # Persistence + auto-fix
  # ------------------------------------------------------------------

  def upsert(self, finding: Finding, audit_id: int | None) -> int:
    return self.job_db.upsert_finding(finding.kind.value, finding.path, finding.details, audit_id)

  def maybe_auto_fix(self, finding: Finding) -> str:
    """Return ``"queued"|"recycled"|"skipped"|"dry_run"`` describing the action taken."""
    if self.dry_run or self.auto_fix is None:
      return "dry_run"
    kind = finding.kind
    if kind == FindingKind.FFPROBE_FAILED and getattr(self.auto_fix, "ffprobe_failed", False):
      return self._queue_conversion(finding.path)
    if kind == FindingKind.ORPHAN_SIDECAR and getattr(self.auto_fix, "orphan_sidecar", False):
      return self._recycle(finding.path)
    if kind == FindingKind.LEFTOVER_TMP and getattr(self.auto_fix, "leftover_tmp", False):
      return self._recycle(finding.path)
    if kind == FindingKind.PRECONV_ORIGINAL and getattr(self.auto_fix, "preconv_original", False):
      return self._recycle(finding.path)
    return "skipped"

  def _queue_conversion(self, path: str) -> str:
    config = self.pcm.get_config_for_path(path)
    args = self.pcm.get_args_for_path(path)
    job_id = self.job_db.add_job(path, config, args, request_source="audit")
    if job_id is not None:
      self.log.info("Audit auto-queued conversion job %d for %s" % (job_id, path))
      from resources.daemon import metrics_prom

      metrics_prom.record_job_enqueued("audit", None)
      return "queued"
    return "skipped"

  def _recycle(self, path: str) -> str:
    bin_dir = self.pcm.get_recycle_bin(self.pcm.default_config)
    try:
      dst = move_to_recycle_bin(path, bin_dir)
    except OSError as exc:
      self.log.warning("Audit recycle failed for %s: %s" % (path, exc))
      return "skipped"
    if dst:
      self.log.info("Audit recycled %s -> %s" % (path, dst))
      return "recycled"
    return "skipped"

  # ------------------------------------------------------------------
  # Duplicate-id rollup (run by enumerator after probing finishes)
  # ------------------------------------------------------------------

  def rollup_duplicate_ids(self, audit_id: int) -> int:
    """Aggregate the recorded media ids and write DUPLICATE_ID findings."""
    pairs = self.job_db.find_duplicate_media_ids(audit_id)
    written = 0
    for media_id, paths in pairs.items():
      details = {"media_id": media_id, "paths": list(paths)}
      for p in paths:
        self.job_db.upsert_finding(FindingKind.DUPLICATE_ID.value, p, details, audit_id)
        written += 1
    self.job_db.purge_audit_media_ids(audit_id)
    return written


# ---------------------------------------------------------------------------
# CLI path: in-process, no DB
# ---------------------------------------------------------------------------


def run_audit_inline(roots: list[str], settings, logger, ffmpeg_dir: str | None = None) -> int:
  """Walk *roots* in-process and print one line per finding. Returns non-zero
  when any finding was emitted (suitable for ``manual.py --audit`` exit code).
  """
  skip_dirs = list(settings.skip_dirs) if settings is not None else None
  count = 0
  observed_ids: dict[str, list[str]] = {}
  for path, hint in enumerate_paths(roots, skip_dirs=skip_dirs):
    finding = _inline_probe(path, hint, ffmpeg_dir, observed_ids)
    if finding is None:
      continue
    count += 1
    print("%s\t%s\t%s" % (finding.kind.value, finding.path, _short_details(finding.details)))
  for media_id, paths in observed_ids.items():
    if len(paths) <= 1:
      continue
    for p in paths:
      count += 1
      print("%s\t%s\tmedia_id=%s n=%d" % (FindingKind.DUPLICATE_ID.value, p, media_id, len(paths)))
  if count == 0:
    logger.info("Audit complete — no findings across %d root(s)" % len(roots))
  else:
    logger.warning("Audit complete — %d finding(s) across %d root(s)" % (count, len(roots)))
  return 1 if count > 0 else 0


def _inline_probe(path: str, hint: str, ffmpeg_dir: str | None, observed_ids: dict[str, list[str]]) -> Finding | None:
  if hint == KIND_HINT_MEDIA:
    fail = ffprobe_check(path, ffmpeg_dir=ffmpeg_dir)
    if fail is not None:
      return Finding(FindingKind.FFPROBE_FAILED, path, fail)
    if path.lower().endswith(".mp4"):
      ids = read_media_ids(path)
      mid = derive_media_id(ids)
      if mid:
        observed_ids.setdefault(mid, []).append(path)
    return None
  if hint == KIND_HINT_PRECONV:
    cand = preconv_original_check(path)
    if cand is None:
      fail = ffprobe_check(path, ffmpeg_dir=ffmpeg_dir)
      if fail is not None:
        return Finding(FindingKind.FFPROBE_FAILED, path, fail)
      return None
    sibling_fail = ffprobe_check(cand["mp4_sibling"], ffmpeg_dir=ffmpeg_dir)
    if sibling_fail is not None:
      return None
    return Finding(FindingKind.PRECONV_ORIGINAL, path, cand)
  if hint == KIND_HINT_SIDECAR:
    details = sidecar_orphan_check(path)
    if details is None:
      return None
    return Finding(FindingKind.ORPHAN_SIDECAR, path, details)
  if hint == KIND_HINT_TMP:
    details = tmp_artifact_check(path)
    if details is None:
      return None
    return Finding(FindingKind.LEFTOVER_TMP, path, details)
  return None


def _short_details(details: dict[str, Any]) -> str:
  if not details:
    return ""
  return ", ".join("%s=%s" % (k, v) for k, v in details.items() if k in ("reason", "size_bytes", "mp4_sibling", "directory"))


__all__ = ["AuditEngine", "Finding", "run_audit_inline"]
