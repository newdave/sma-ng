"""Library audit subsystem.

Locates corrupt media (FFprobe failures), orphan sidecars, leftover tmp/partial
artifacts, leftover pre-conversion originals, and TMDB/TVDB-ID duplicates across
configured paths. Distributed across the SMA-NG cluster via PostgreSQL queue +
``FOR UPDATE SKIP LOCKED`` claim semantics — every live node contributes probes.

Public API:

* :class:`AuditEngine` — daemon-side glue (probe one queue unit, upsert finding,
  optional auto-fix).
* :func:`run_audit_inline` — CLI path; in-process enumerate + probe with no DB.
* :class:`FindingKind` — enum of the canonical finding ``kind`` values.
"""

from resources.library_audit.engine import AuditEngine, run_audit_inline
from resources.library_audit.kinds import FindingKind

__all__ = ["AuditEngine", "FindingKind", "run_audit_inline"]
