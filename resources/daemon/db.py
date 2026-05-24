import copy
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlparse

import yaml as _yaml

from resources.daemon.constants import (
  AUDIT_RUN_COMPLETED,
  AUDIT_RUN_ENUMERATING,
  AUDIT_RUN_PROBING,
  AUDIT_RUN_QUEUED,
  AUDIT_UNIT_CLAIMED,
  AUDIT_UNIT_DONE,
  AUDIT_UNIT_ERROR,
  AUDIT_UNIT_PENDING,
  SECRET_KEYS,
  STATUS_COMPLETED,
  STATUS_FAILED,
  STATUS_OPEN,
  STATUS_PENDING,
  STATUS_RUNNING,
  resolve_node_id,
)
from resources.log import getLogger

log = getLogger("DAEMON")

# Cap stored ffmpeg stderr per job at 1 MiB. ffmpeg can emit unbounded
# stderr (e.g. on a tight retry loop or with -loglevel debug). Storing
# the raw blob would bloat the jobs row to the point of slowing
# unrelated queries; 1 MiB is enough to keep the encoder banner, the
# offending option line, and several hundred lines of context.
_FFMPEG_STDERR_MAX_BYTES = 1024 * 1024

_METRICS_WINDOW_MAP = {
  "24h": {"trunc": "hour", "filter": "24 hours", "series_offset": "23 hours", "step": "1 hour", "throughput_hours": 24.0},
  "7d": {"trunc": "day", "filter": "7 days", "series_offset": "6 days", "step": "1 day", "throughput_hours": 168.0},
  "30d": {"trunc": "day", "filter": "30 days", "series_offset": "29 days", "step": "1 day", "throughput_hours": 720.0},
}

# Additive `jobs` columns introduced by the metrics-expansion PRP. Both
# backends migrate idempotently using their backend-specific helpers; the
# Postgres type column accepts any text the backend understands.
# See docs/prps/metrics-expansion.md for the full design rationale.
_METRICS_EXPANSION_COLUMNS: tuple[tuple[str, str, str], ...] = (
  # (name,                        sqlite_type, postgres_type)
  ("encoder_backend", "TEXT", "TEXT"),
  ("encoder_name", "TEXT", "TEXT"),
  ("source_duration_seconds", "REAL", "DOUBLE PRECISION"),
  ("failure_category", "TEXT", "TEXT"),
  ("failure_cause", "TEXT", "TEXT"),
  ("source_video_codec", "TEXT", "TEXT"),
  ("source_video_width", "INTEGER", "INTEGER"),
  ("source_video_height", "INTEGER", "INTEGER"),
  ("source_audio_codec", "TEXT", "TEXT"),
  ("source_audio_channels", "INTEGER", "INTEGER"),
  ("source_hdr", "TEXT", "TEXT"),
  ("dest_video_codec", "TEXT", "TEXT"),
  ("dest_video_width", "INTEGER", "INTEGER"),
  ("dest_video_height", "INTEGER", "INTEGER"),
  ("dest_audio_codec", "TEXT", "TEXT"),
  ("dest_audio_channels", "INTEGER", "INTEGER"),
  ("dest_hdr", "TEXT", "TEXT"),
  ("request_source", "TEXT", "TEXT"),
  ("request_profile", "TEXT", "TEXT"),
)


def _utc_now():
  return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sqlite_path_from_url(db_url):
  parsed = urlparse(db_url)
  if parsed.scheme != "sqlite":
    raise ValueError("SQLite database URL must use sqlite:///path or sqlite:////absolute/path")
  if parsed.netloc and parsed.netloc != "localhost":
    raise ValueError("SQLite database URL must be local, got host: %s" % parsed.netloc)
  path = unquote(parsed.path or "")
  if not path:
    raise ValueError("SQLite database URL must include a file path")
  if parsed.netloc == "localhost":
    return path
  return path


def _profiles_at_cap(conn, profile_caps, *, is_sqlite):
  """Return the set of profile names whose running-job count already meets
  or exceeds the configured ``max_concurrent`` cap.

  ``profile_caps`` is the ``{profile: cap}`` dict produced by
  ``PathConfigManager.profile_concurrency_caps``. Empty/None → no caps,
  no work performed. The count is cluster-wide (we don't filter by
  ``node_id``) so the cap behaves the same on a single node and on a
  Postgres-backed multi-worker pool.
  """
  if not profile_caps:
    return set()
  placeholders = ", ".join("?" if is_sqlite else "%s" for _ in profile_caps)
  sql = "SELECT request_profile, COUNT(*) AS n FROM jobs WHERE status = " + ("?" if is_sqlite else "%s") + " AND request_profile IN (" + placeholders + ") GROUP BY request_profile"
  params = [STATUS_RUNNING, *profile_caps.keys()]
  if is_sqlite:
    rows = conn.execute(sql, params).fetchall()
    running = {r["request_profile"]: int(r["n"]) for r in rows}
  else:
    with conn.cursor() as cur:
      cur.execute(sql, params)
      running = {r["request_profile"]: int(r["n"]) for r in cur.fetchall()}
  return {name for name, cap in profile_caps.items() if running.get(name, 0) >= cap}


class SQLiteJobDatabase:
  """SQLite-backed job queue for single-node deployments.

  This backend is intentionally local-only: it supports persistent jobs,
  scanner de-duplication, and local job controls, but does not expose
  distributed cluster coordination, cluster logs, or metrics.
  """

  is_distributed: bool = False

  def __init__(self, db_url, logger=None):
    self.db_url = db_url
    self.path = _sqlite_path_from_url(db_url)
    self.log = logger or log
    self._node_id = resolve_node_id()
    self._lock = threading.RLock()
    os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
    self._conn_obj = sqlite3.connect(self.path, check_same_thread=False)
    self._conn_obj.row_factory = sqlite3.Row
    self._conn_obj.execute("PRAGMA journal_mode=WAL")
    self._conn_obj.execute("PRAGMA foreign_keys=ON")
    self._init_db()

  @contextmanager
  def _conn(self):
    with self._lock:
      try:
        yield self._conn_obj
        self._conn_obj.commit()
      except Exception:
        self._conn_obj.rollback()
        raise

  def close(self):
    self._conn_obj.close()

  def _init_db(self):
    with self._conn() as conn:
      conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL,
          config TEXT NOT NULL,
          args TEXT DEFAULT '[]',
          status TEXT DEFAULT 'pending',
          worker_id INTEGER,
          node_id TEXT,
          error TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          started_at TEXT,
          completed_at TEXT,
          retry_count INTEGER DEFAULT 0,
          max_retries INTEGER DEFAULT 0,
          next_attempt_at TEXT,
          priority INTEGER DEFAULT 0,
          input_size_bytes INTEGER,
          output_size_bytes INTEGER,
          ffmpeg_stderr TEXT,
          encoder_backend TEXT,
          encoder_name TEXT,
          source_duration_seconds REAL,
          failure_category TEXT,
          failure_cause TEXT,
          source_video_codec TEXT,
          source_video_width INTEGER,
          source_video_height INTEGER,
          source_audio_codec TEXT,
          source_audio_channels INTEGER,
          source_hdr TEXT,
          dest_video_codec TEXT,
          dest_video_width INTEGER,
          dest_video_height INTEGER,
          dest_audio_codec TEXT,
          dest_audio_channels INTEGER,
          dest_hdr TEXT,
          request_source TEXT,
          request_profile TEXT
        )
      """)
      # Idempotent migration: older deployments may already have a `jobs`
      # table created before ffmpeg_stderr existed. PRAGMA table_info is
      # the SQLite-portable way to introspect schema without an ALTER race.
      existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
      if "ffmpeg_stderr" not in existing_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN ffmpeg_stderr TEXT")
      for col_name, sqlite_type, _pg_type in _METRICS_EXPANSION_COLUMNS:
        if col_name not in existing_cols:
          conn.execute("ALTER TABLE jobs ADD COLUMN %s %s" % (col_name, sqlite_type))
      conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
      conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_config ON jobs(config)")
      conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at)")
      conn.execute("""
        CREATE TABLE IF NOT EXISTS scanned_files (
          path TEXT PRIMARY KEY,
          scanned_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
      """)
      conn.execute("""
        CREATE TABLE IF NOT EXISTS cluster_nodes (
          node_id TEXT PRIMARY KEY,
          host TEXT NOT NULL,
          workers INTEGER NOT NULL DEFAULT 0,
          last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          status TEXT NOT NULL DEFAULT 'online',
          running_jobs INTEGER NOT NULL DEFAULT 0,
          pending_jobs INTEGER NOT NULL DEFAULT 0,
          version TEXT,
          hwaccel TEXT,
          node_name TEXT
        )
      """)
    self._reset_running_jobs()

  def _reset_running_jobs(self):
    count = self._requeue_running_jobs_for_node(self._node_id)
    if count > 0:
      self.log.info("Reset %d interrupted jobs to pending (node: %s)" % (count, self._node_id))

  def _requeue_running_jobs_for_node(self, node_id):
    with self._conn() as conn:
      cur = conn.execute(
        """
        UPDATE jobs
        SET status = ?, worker_id = NULL, node_id = NULL, started_at = NULL
        WHERE status = ? AND node_id = ?
        """,
        (STATUS_PENDING, STATUS_RUNNING, node_id),
      )
      return cur.rowcount

  def add_job(self, path, config, args=None, max_retries=0, *, request_source=None, request_profile=None):
    args_json = json.dumps(args or [])
    with self._conn() as conn:
      row = conn.execute(
        "SELECT id FROM jobs WHERE path = ? AND status IN (?, ?) LIMIT 1",
        (path, STATUS_PENDING, STATUS_RUNNING),
      ).fetchone()
      if row:
        self.log.debug("Duplicate job for path: %s (existing job %d)" % (path, row["id"]))
        return None
      cur = conn.execute(
        "INSERT INTO jobs (path, config, args, status, max_retries, request_source, request_profile) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (path, config, args_json, STATUS_PENDING, max_retries, request_source, request_profile),
      )
      job_id = cur.lastrowid
    self.log.debug("Added job %d: %s" % (job_id, path))
    return job_id

  def find_active_job(self, path):
    with self._conn() as conn:
      row = conn.execute("SELECT * FROM jobs WHERE path = ? AND status IN (?, ?) LIMIT 1", (path, STATUS_PENDING, STATUS_RUNNING)).fetchone()
      return dict(row) if row else None

  def claim_next_job(self, worker_id, node_id, exclude_configs=None, profile_caps=None):
    now = _utc_now()
    with self._conn() as conn:
      over_capped = _profiles_at_cap(conn, profile_caps, is_sqlite=True)
      params = [STATUS_PENDING, now]
      sql = """
        SELECT id FROM jobs
        WHERE status = ? AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
      """
      if exclude_configs:
        placeholders = ", ".join("?" for _ in exclude_configs)
        sql += " AND config NOT IN (%s)" % placeholders
        params.extend(list(exclude_configs))
      if over_capped:
        placeholders = ", ".join("?" for _ in over_capped)
        sql += " AND (request_profile IS NULL OR request_profile NOT IN (%s))" % placeholders
        params.extend(list(over_capped))
      sql += " ORDER BY priority DESC, created_at ASC LIMIT 1"
      row = conn.execute(sql, params).fetchone()
      if row is None:
        return None
      job_id = row["id"]
      conn.execute(
        "UPDATE jobs SET status = ?, worker_id = ?, node_id = ?, started_at = ? WHERE id = ?",
        (STATUS_RUNNING, worker_id, node_id, now, job_id),
      )
    return self.get_job(job_id)

  def get_pending_jobs(self):
    with self._conn() as conn:
      return [dict(r) for r in conn.execute("SELECT * FROM jobs WHERE status = ? ORDER BY created_at ASC", (STATUS_PENDING,)).fetchall()]

  def get_next_pending_job(self):
    with self._conn() as conn:
      row = conn.execute("SELECT * FROM jobs WHERE status = ? ORDER BY created_at ASC LIMIT 1", (STATUS_PENDING,)).fetchone()
      return dict(row) if row else None

  def start_job(self, job_id, worker_id):
    with self._conn() as conn:
      conn.execute("UPDATE jobs SET status = ?, worker_id = ?, started_at = ? WHERE id = ?", (STATUS_RUNNING, worker_id, _utc_now(), job_id))
    self.log.debug("Job %d started by worker %d" % (job_id, worker_id))

  def complete_job(
    self,
    job_id,
    input_size=None,
    output_size=None,
    source_duration_seconds=None,
    encoder_backend=None,
    encoder_name=None,
  ):
    with self._conn() as conn:
      conn.execute(
        """UPDATE jobs
              SET status = ?,
                  completed_at = ?,
                  input_size_bytes = ?,
                  output_size_bytes = ?,
                  source_duration_seconds = ?,
                  encoder_backend = ?,
                  encoder_name = ?
            WHERE id = ?""",
        (
          STATUS_COMPLETED,
          _utc_now(),
          input_size,
          output_size,
          source_duration_seconds,
          encoder_backend,
          encoder_name,
          job_id,
        ),
      )
    self.log.debug("Job %d completed" % job_id)

  def update_job_ffmpeg_stderr(self, job_id: int, stderr: str | None) -> None:
    """Store the full ffmpeg stderr blob for *job_id*.

    Truncates to ``_FFMPEG_STDERR_MAX_BYTES`` (keeping the tail, since
    the meaningful error is usually at the end of an ffmpeg run) so a
    runaway stderr doesn't blow up the row.
    """
    if stderr is None:
      return
    payload = stderr if isinstance(stderr, str) else str(stderr)
    encoded = payload.encode("utf-8", errors="replace")
    if len(encoded) > _FFMPEG_STDERR_MAX_BYTES:
      payload = encoded[-_FFMPEG_STDERR_MAX_BYTES:].decode("utf-8", errors="replace")
    with self._conn() as conn:
      conn.execute("UPDATE jobs SET ffmpeg_stderr = ? WHERE id = ?", (payload, job_id))

  def defer_job(self, job_id, delay_seconds, reason=None):
    """Push a running job back to pending with a delay, without bumping retry_count.

    Used by the worker's pre-ffmpeg gates (e.g. output-filesystem pressure)
    that want to defer the job instead of consuming a retry slot.
    """
    now = datetime.now(UTC).replace(microsecond=0)
    next_attempt = (now + timedelta(seconds=max(int(delay_seconds), 0))).isoformat()
    with self._conn() as conn:
      conn.execute(
        """
        UPDATE jobs
        SET status = ?, next_attempt_at = ?, error = ?,
            started_at = NULL, completed_at = NULL, worker_id = NULL, node_id = NULL
        WHERE id = ?
        """,
        (STATUS_PENDING, next_attempt, reason, job_id),
      )
    self.log.debug("Job %d deferred for %ds (%s)" % (job_id, delay_seconds, reason or ""))

  def fail_job(self, job_id, error=None, failure_category=None, failure_cause=None):
    now = datetime.now(UTC).replace(microsecond=0)
    with self._conn() as conn:
      row = conn.execute("SELECT retry_count, max_retries FROM jobs WHERE id = ?", (job_id,)).fetchone()
      if row and row["retry_count"] < row["max_retries"]:
        retry_count = row["retry_count"] + 1
        delay = 2**retry_count * 60
        next_attempt = (now + timedelta(seconds=delay)).isoformat()
        conn.execute(
          """
          UPDATE jobs
          SET status = ?, retry_count = ?, error = ?, next_attempt_at = ?,
              failure_category = ?, failure_cause = ?,
              started_at = NULL, completed_at = NULL, worker_id = NULL, node_id = NULL
          WHERE id = ?
          """,
          (STATUS_PENDING, retry_count, error, next_attempt, failure_category, failure_cause, job_id),
        )
        self.log.debug("Job %d failed (attempt %d/%d), retrying in %ds" % (job_id, retry_count, row["max_retries"], delay))
      else:
        conn.execute(
          """UPDATE jobs SET status = ?, error = ?, completed_at = ?,
                                failure_category = ?, failure_cause = ?
                          WHERE id = ?""",
          (STATUS_FAILED, error, now.isoformat(), failure_category, failure_cause, job_id),
        )
        self.log.debug("Job %d failed: %s" % (job_id, error))

  def get_job(self, job_id):
    with self._conn() as conn:
      row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
      return dict(row) if row else None

  def get_jobs(self, status=None, config=None, path=None, limit=100, offset=0):
    query = "SELECT * FROM jobs WHERE 1=1"
    params = []
    if status:
      query += " AND status = ?"
      params.append(status)
    if config:
      query += " AND config = ?"
      params.append(config)
    if path:
      query += " AND LOWER(path) LIKE LOWER(?)"
      params.append("%" + path + "%")
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with self._conn() as conn:
      return [dict(r) for r in conn.execute(query, params).fetchall()]

  def get_stats(self):
    with self._conn() as conn:
      stats = {r["status"]: r["count"] for r in conn.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status").fetchall()}
      stats["total"] = conn.execute("SELECT COUNT(*) AS total FROM jobs").fetchone()["total"]
    return stats

  def get_metrics(self, window: str = "24h") -> dict:
    return {"available": False, "reason": "Metrics require PostgreSQL cluster mode.", "window": window}

  def get_running_jobs(self):
    with self._conn() as conn:
      return [dict(r) for r in conn.execute("SELECT * FROM jobs WHERE status = ?", (STATUS_RUNNING,)).fetchall()]

  def cleanup_old_jobs(self, days=30):
    cutoff = (datetime.now(UTC) - timedelta(days=days)).replace(microsecond=0).isoformat()
    with self._conn() as conn:
      cur = conn.execute("DELETE FROM jobs WHERE status IN (?, ?) AND completed_at < ?", (STATUS_COMPLETED, STATUS_FAILED, cutoff))
      deleted = cur.rowcount
    if deleted > 0:
      self.log.info("Cleaned up %d old jobs" % deleted)
    return deleted

  def pending_count(self):
    with self._conn() as conn:
      return conn.execute("SELECT COUNT(*) AS count FROM jobs WHERE status = ?", (STATUS_PENDING,)).fetchone()["count"]

  def pending_count_for_config(self, config):
    with self._conn() as conn:
      return conn.execute("SELECT COUNT(*) AS count FROM jobs WHERE status = ? AND config = ?", (STATUS_PENDING, config)).fetchone()["count"]

  def heartbeat(self, node_id, host, workers, started_at, version=None, hwaccel=None, node_name=None):
    now = _utc_now()
    started = started_at.isoformat() if hasattr(started_at, "isoformat") else str(started_at)
    with self._conn() as conn:
      conn.execute(
        """
        INSERT INTO cluster_nodes (node_id, host, workers, last_seen, started_at, status, running_jobs, pending_jobs, version, hwaccel, node_name)
        VALUES (?, ?, ?, ?, ?, 'online',
          (SELECT COUNT(*) FROM jobs WHERE status = 'running' AND node_id = ?),
          (SELECT COUNT(*) FROM jobs WHERE status = 'pending'),
          ?, ?, ?)
        ON CONFLICT(node_id) DO UPDATE SET
          host = excluded.host,
          workers = excluded.workers,
          last_seen = excluded.last_seen,
          status = excluded.status,
          running_jobs = excluded.running_jobs,
          pending_jobs = excluded.pending_jobs,
          version = COALESCE(excluded.version, cluster_nodes.version),
          hwaccel = COALESCE(excluded.hwaccel, cluster_nodes.hwaccel),
          node_name = COALESCE(excluded.node_name, cluster_nodes.node_name)
        """,
        (node_id, host, workers, now, started, node_id, version, hwaccel, node_name or None),
      )
    return

  def is_node_approved(self, node_id):
    return True

  def set_node_approval(self, node_id, approved=True, actor=None, note=None):
    return None

  def delete_node(self, node_id):
    with self._conn() as conn:
      cur = conn.execute("DELETE FROM cluster_nodes WHERE node_id = ?", (node_id,))
      return cur.rowcount > 0

  def set_node_status(self, node_id, status):
    with self._conn() as conn:
      cur = conn.execute("UPDATE cluster_nodes SET status = ? WHERE node_id = ?", (status, node_id))
      return cur.rowcount > 0

  def get_cluster_nodes(self):
    self.heartbeat(self._node_id, self._node_id, 0, _utc_now())
    with self._conn() as conn:
      nodes = [dict(r) for r in conn.execute("SELECT * FROM cluster_nodes ORDER BY last_seen DESC").fetchall()]
      active = [dict(r) for r in conn.execute("SELECT node_id, id AS job_id, path, config FROM jobs WHERE status = ?", (STATUS_RUNNING,)).fetchall()]
    jobs_by_node = {}
    for row in active:
      jobs_by_node.setdefault(row["node_id"], []).append({"job_id": row["job_id"], "path": row["path"], "config": row["config"]})
    for node in nodes:
      node["uptime_seconds"] = 0
      node["approval_status"] = "approved"
      node["active_jobs"] = jobs_by_node.get(node["node_id"], [])
    return nodes

  def recover_stale_nodes(self, stale_seconds=120):
    return []

  def mark_node_offline(self, node_id, remove=False):
    requeued = self._requeue_running_jobs_for_node(node_id)
    with self._conn() as conn:
      if remove:
        conn.execute("DELETE FROM cluster_nodes WHERE node_id = ?", (node_id,))
      else:
        conn.execute("UPDATE cluster_nodes SET status = 'offline' WHERE node_id = ?", (node_id,))
    if requeued:
      self.log.info("Requeued %d running jobs on shutdown" % requeued)

  def send_node_command(self, node_id, command, requested_by=None):
    return []

  def poll_node_command(self, node_id):
    return None

  def ack_node_command(self, cmd_id, status):
    return None

  def insert_logs(self, records):
    return None

  def cleanup_old_logs(self, days):
    return 0

  def get_logs(self, node_id=None, level=None, limit=100, offset=0):
    return []

  def get_cluster_config(self):
    return None

  def set_cluster_config(self, config_dict, updated_by=None):
    return None

  def expire_offline_nodes(self, expiry_days):
    return []

  def cleanup_orphaned_commands(self, node_ids):
    return 0

  def get_logs_for_archival(self, before_days):
    return []

  def delete_logs_before(self, before_days):
    return 0

  def requeue_job(self, job_id):
    with self._conn() as conn:
      cur = conn.execute(
        """
        UPDATE jobs
        SET status = ?, worker_id = NULL, node_id = NULL, error = NULL, started_at = NULL, completed_at = NULL
        WHERE id = ? AND status = ?
        """,
        (STATUS_PENDING, job_id, STATUS_FAILED),
      )
      requeued = cur.rowcount > 0
    if requeued:
      self.log.info("Requeued failed job %d" % job_id)
    return requeued

  def requeue_failed_jobs(self, config=None):
    sql = """
      UPDATE jobs
      SET status = ?, worker_id = NULL, node_id = NULL, error = NULL, started_at = NULL, completed_at = NULL
      WHERE status = ?
    """
    params = [STATUS_PENDING, STATUS_FAILED]
    if config:
      sql += " AND config = ?"
      params.append(config)
    with self._conn() as conn:
      cur = conn.execute(sql, params)
      count = cur.rowcount
    if count > 0:
      self.log.info("Requeued %d failed jobs" % count)
    return count

  def cancel_job(self, job_id):
    with self._conn() as conn:
      cur = conn.execute(
        "UPDATE jobs SET status = 'cancelled', error = 'Cancelled by user', completed_at = ? WHERE id = ? AND status IN (?, ?)",
        (_utc_now(), job_id, STATUS_PENDING, STATUS_RUNNING),
      )
      cancelled = cur.rowcount > 0
    if cancelled:
      self.log.info("Cancelled job %d" % job_id)
    return cancelled

  def set_job_priority(self, job_id, priority):
    with self._conn() as conn:
      cur = conn.execute("UPDATE jobs SET priority = ? WHERE id = ? AND status = ?", (priority, job_id, STATUS_PENDING))
      updated = cur.rowcount > 0
    if updated:
      self.log.info("Set priority %d for job %d" % (priority, job_id))
    return updated

  def delete_failed_jobs(self):
    with self._conn() as conn:
      cur = conn.execute("DELETE FROM jobs WHERE status = ?", (STATUS_FAILED,))
      deleted = cur.rowcount
    if deleted > 0:
      self.log.info("Deleted %d failed jobs" % deleted)
    return deleted

  def delete_jobs(self, job_ids):
    ids = [int(j) for j in (job_ids or [])]
    if not ids:
      return []
    placeholders = ", ".join("?" for _ in ids)
    with self._conn() as conn:
      existing = [row["id"] for row in conn.execute("SELECT id FROM jobs WHERE id IN (%s)" % placeholders, ids).fetchall()]
      if existing:
        conn.execute("DELETE FROM jobs WHERE id IN (%s)" % placeholders, ids)
    if existing:
      self.log.info("Deleted %d jobs by id" % len(existing))
    return existing

  def delete_offline_nodes(self):
    with self._conn() as conn:
      cur = conn.execute("DELETE FROM cluster_nodes WHERE status != 'online'")
      deleted = cur.rowcount
    if deleted > 0:
      self.log.info("Deleted %d offline nodes" % deleted)
    return deleted

  def delete_all_jobs(self):
    with self._conn() as conn:
      cur = conn.execute("DELETE FROM jobs")
      deleted = cur.rowcount
    self.log.info("Deleted all jobs (%d rows)" % deleted)
    return deleted

  def filter_unscanned(self, paths):
    if not paths:
      return []
    placeholders = ", ".join("?" for _ in paths)
    with self._conn() as conn:
      already = {row["path"] for row in conn.execute("SELECT path FROM scanned_files WHERE path IN (%s)" % placeholders, paths).fetchall()}
    return [p for p in paths if p not in already]

  def record_scanned(self, paths):
    if not paths:
      return
    with self._conn() as conn:
      conn.executemany("INSERT OR IGNORE INTO scanned_files (path) VALUES (?)", [(p,) for p in paths])

  def list_audit_runs(self, limit=50, offset=0):
    return []

  def get_audit_run(self, audit_id):
    return None

  def get_findings(self, status=None, kind=None, path=None, limit=50, offset=0):
    return []

  def get_finding(self, finding_id):
    return None

  def set_finding_status(self, finding_id, status):
    return None


class PostgreSQLJobDatabase:
  """PostgreSQL-backed job queue for distributed multi-node operation.

  Uses SELECT FOR UPDATE SKIP LOCKED to atomically claim jobs, ensuring
  no two nodes ever process the same file. Requires psycopg2-binary.

  Usage:
      db = PostgreSQLJobDatabase("postgresql://user:pass@host/sma")
      Set daemon.db_url in sma-ng.yml before starting daemon.py.
  """

  is_distributed: bool = True

  def __init__(self, db_url, logger=None, max_connections=10):
    try:
      import psycopg2
      import psycopg2.extras
      import psycopg2.pool
    except ImportError:
      raise ImportError("psycopg2 is required for PostgreSQL support. Install with: pip install psycopg2-binary")
    self.db_url = db_url
    self.log = logger or log
    self._node_id = resolve_node_id()
    self._pool = psycopg2.pool.ThreadedConnectionPool(
      minconn=1,
      maxconn=max_connections,
      dsn=db_url,
      cursor_factory=psycopg2.extras.RealDictCursor,
    )
    self._init_db()

  @contextmanager
  def _conn(self):
    """Check out a connection from the pool, auto-commit or rollback."""
    conn = self._pool.getconn()
    try:
      yield conn
      conn.commit()
    except Exception:
      conn.rollback()
      raise
    finally:
      self._pool.putconn(conn)

  def close(self):
    """Close all connections in the pool."""
    self._pool.closeall()

  def _init_db(self):
    """Create schema if it does not exist, then recover this node's interrupted jobs."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS jobs (
                        id           SERIAL PRIMARY KEY,
                        path         TEXT NOT NULL,
                        config       TEXT NOT NULL,
                        args         TEXT DEFAULT '[]',
                        status       TEXT DEFAULT 'pending',
                        worker_id    INTEGER,
                        node_id      TEXT,
                        error        TEXT,
                        ffmpeg_stderr TEXT,
                        created_at   TIMESTAMPTZ DEFAULT NOW(),
                        started_at   TIMESTAMPTZ,
                        completed_at TIMESTAMPTZ
                    )
                """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_config  ON jobs(config)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at)")
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS cluster_nodes (
                        node_id      TEXT PRIMARY KEY,
                        host         TEXT NOT NULL,
                        workers      INTEGER NOT NULL DEFAULT 0,
                        last_seen    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        status       TEXT NOT NULL DEFAULT 'online',
                approval_status TEXT NOT NULL DEFAULT 'pending',
                approved_by  TEXT,
                approved_at  TIMESTAMPTZ,
                approval_note TEXT,
                        running_jobs INTEGER NOT NULL DEFAULT 0,
                pending_jobs INTEGER NOT NULL DEFAULT 0,
                command_requested_at TIMESTAMPTZ,
                command_requested_by TEXT,
                last_command TEXT,
                last_command_at TIMESTAMPTZ
                    )
                """)
        # Migrations: add columns to existing tables
        cur.execute("""
                    ALTER TABLE cluster_nodes
                    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                """)
        cur.execute("""
                    ALTER TABLE cluster_nodes
                    ADD COLUMN IF NOT EXISTS pending_command TEXT
                """)
        cur.execute("""
              ALTER TABLE cluster_nodes
              ADD COLUMN IF NOT EXISTS approval_status TEXT
            """)
        cur.execute("""
              ALTER TABLE cluster_nodes
              ADD COLUMN IF NOT EXISTS approved_by TEXT
            """)
        cur.execute("""
              ALTER TABLE cluster_nodes
              ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ
            """)
        cur.execute("""
              ALTER TABLE cluster_nodes
              ADD COLUMN IF NOT EXISTS approval_note TEXT
            """)
        cur.execute("""
              ALTER TABLE cluster_nodes
              ADD COLUMN IF NOT EXISTS command_requested_at TIMESTAMPTZ
            """)
        cur.execute("""
              ALTER TABLE cluster_nodes
              ADD COLUMN IF NOT EXISTS command_requested_by TEXT
            """)
        cur.execute("""
              ALTER TABLE cluster_nodes
              ADD COLUMN IF NOT EXISTS last_command TEXT
            """)
        cur.execute("""
              ALTER TABLE cluster_nodes
              ADD COLUMN IF NOT EXISTS last_command_at TIMESTAMPTZ
            """)
        # Existing clusters should continue to run after upgrade; only newly inserted
        # nodes default to pending approval.
        cur.execute("UPDATE cluster_nodes SET approval_status = 'approved' WHERE approval_status IS NULL")
        cur.execute("ALTER TABLE cluster_nodes ALTER COLUMN approval_status SET DEFAULT 'pending'")
        cur.execute("ALTER TABLE cluster_nodes ALTER COLUMN approval_status SET NOT NULL")
        for col_sql in [
          "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0",
          "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS max_retries INTEGER DEFAULT 0",
          "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ",
          "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 0",
          "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ffmpeg_stderr TEXT",
        ]:
          cur.execute(col_sql)
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS scanned_files (
                        path       TEXT PRIMARY KEY,
                        scanned_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
        cur.execute("""
                    ALTER TABLE cluster_nodes ADD COLUMN IF NOT EXISTS version TEXT
                """)
        cur.execute("""
                    ALTER TABLE cluster_nodes ADD COLUMN IF NOT EXISTS hwaccel TEXT
                """)
        cur.execute("""
                    ALTER TABLE cluster_nodes ADD COLUMN IF NOT EXISTS node_name TEXT
                """)
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS node_commands (
                        id         SERIAL PRIMARY KEY,
                        node_id    TEXT NOT NULL,
                        command    TEXT NOT NULL,
                        issued_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        status     TEXT NOT NULL DEFAULT 'pending',
                        issued_by  TEXT
                    )
                """)
        cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_node_commands_node_pending
                        ON node_commands (node_id, status)
                        WHERE status = 'pending'
                """)
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS logs (
                        id        BIGSERIAL PRIMARY KEY,
                        node_id   TEXT NOT NULL,
                        level     TEXT NOT NULL,
                        logger    TEXT,
                        message   TEXT NOT NULL,
                        timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)
        cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_logs_node_ts ON logs (node_id, timestamp DESC)
                """)
        cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs (timestamp DESC)
                """)
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS cluster_config (
                        id         INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                        config     TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_by TEXT
                    )
                """)
        cur.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS input_size_bytes BIGINT")
        cur.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS output_size_bytes BIGINT")
        # Metrics-expansion columns (additive, all nullable, all backends).
        for col_name, _sqlite_type, pg_type in _METRICS_EXPANSION_COLUMNS:
          cur.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS %s %s" % (col_name, pg_type))
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_completed ON jobs(status, completed_at)")
        # ---- library audit ---------------------------------------------------
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS library_audit_runs (
                        id           BIGSERIAL PRIMARY KEY,
                        status       TEXT NOT NULL DEFAULT 'queued',
                        triggered_by TEXT,
                        scope_paths  TEXT[] NOT NULL,
                        started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        completed_at TIMESTAMPTZ,
                        total_units  INTEGER NOT NULL DEFAULT 0,
                        done_units   INTEGER NOT NULL DEFAULT 0,
                        error        TEXT
                    )
                """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_runs_status ON library_audit_runs(status)")
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS library_audit_queue (
                        id          BIGSERIAL PRIMARY KEY,
                        audit_id    BIGINT NOT NULL REFERENCES library_audit_runs(id) ON DELETE CASCADE,
                        path        TEXT NOT NULL,
                        kind_hint   TEXT NOT NULL,
                        status      TEXT NOT NULL DEFAULT 'pending',
                        claimed_by  TEXT,
                        claimed_at  TIMESTAMPTZ,
                        finished_at TIMESTAMPTZ,
                        error       TEXT
                    )
                """)
        cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_audit_queue_pending
                        ON library_audit_queue (audit_id, status)
                        WHERE status = 'pending'
                """)
        cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_audit_queue_claimed
                        ON library_audit_queue (status, claimed_at)
                        WHERE status = 'claimed'
                """)
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS library_findings (
                        id            BIGSERIAL PRIMARY KEY,
                        kind          TEXT NOT NULL,
                        path          TEXT NOT NULL,
                        details       JSONB NOT NULL DEFAULT '{}'::jsonb,
                        status        TEXT NOT NULL DEFAULT 'open',
                        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        acked_at      TIMESTAMPTZ,
                        resolved_at   TIMESTAMPTZ,
                        audit_id      BIGINT REFERENCES library_audit_runs(id) ON DELETE SET NULL,
                        UNIQUE (kind, path)
                    )
                """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_findings_status ON library_findings (status, kind)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_findings_path ON library_findings (path)")
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS library_audit_media_ids (
                        audit_id BIGINT NOT NULL REFERENCES library_audit_runs(id) ON DELETE CASCADE,
                        path     TEXT NOT NULL,
                        media_id TEXT NOT NULL,
                        PRIMARY KEY (audit_id, path)
                    )
                """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_media_ids_run_mid ON library_audit_media_ids (audit_id, media_id)")
    self.log.info("PostgreSQL database initialized: %s" % self.db_url)
    self._reset_running_jobs()

  def _reset_running_jobs(self):
    """Reset only this node's interrupted running jobs back to pending on startup."""
    count = self._requeue_running_jobs_for_node(self._node_id)
    if count > 0:
      self.log.info("Reset %d interrupted jobs to pending (node: %s)" % (count, self._node_id))

  def _requeue_running_jobs_for_node(self, node_id):
    """Reset all running jobs for *node_id* to pending. Returns the number of rows updated."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    UPDATE jobs
                    SET status = %s, worker_id = NULL, node_id = NULL, started_at = NULL
                    WHERE status = %s AND node_id = %s
                    """,
          (STATUS_PENDING, STATUS_RUNNING, node_id),
        )
        return cur.rowcount

  def _lock_job_path(self, cur, path):
    """Serialize add_job decisions for the same media path within a transaction.

    Multiple nodes can discover the same file at nearly the same time via
    scanners or duplicate webhook deliveries. A transaction-scoped advisory
    lock closes that race so only one transaction can decide whether a
    pending/running row already exists for a given path.
    """
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (path,))

  def add_job(self, path, config, args=None, max_retries=0, *, request_source=None, request_profile=None):
    """Add a job to the queue. Returns job ID, or None if a duplicate is already pending/running."""
    args_json = json.dumps(args or [])
    with self._conn() as conn:
      with conn.cursor() as cur:
        self._lock_job_path(cur, path)
        cur.execute("SELECT id FROM jobs WHERE path = %s AND status IN (%s, %s) LIMIT 1", (path, STATUS_PENDING, STATUS_RUNNING))
        existing = cur.fetchone()
        if existing:
          self.log.debug("Duplicate job for path: %s (existing job %d)" % (path, existing["id"]))
          return None
        cur.execute(
          "INSERT INTO jobs (path, config, args, status, max_retries, request_source, request_profile) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
          (path, config, args_json, STATUS_PENDING, max_retries, request_source, request_profile),
        )
        job_id = cur.fetchone()["id"]
    self.log.debug("Added job %d: %s" % (job_id, path))
    return job_id

  def find_active_job(self, path):
    """Find a pending or running job for the given path, if any."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE path = %s AND status IN (%s, %s) LIMIT 1", (path, STATUS_PENDING, STATUS_RUNNING))
        row = cur.fetchone()
        return dict(row) if row else None

  def claim_next_job(self, worker_id, node_id, exclude_configs=None, profile_caps=None):
    """Atomically claim the next pending job using SELECT FOR UPDATE SKIP LOCKED.

    exclude_configs: set of config paths already held by a running job —
    jobs for those configs are skipped so a free worker can pick up work
    for a different config rather than blocking on a locked one.

    profile_caps: optional ``{profile_name: max_concurrent}`` map. Pending
    jobs whose ``request_profile`` already has cap-many running peers
    (cluster-wide) are skipped.

    Load balancing: if this node already has a running job AND another
    online+approved peer node has zero running jobs (with at least one
    worker), this call returns None so the idle peer can claim the job
    on its next poll instead. With only one node, the check is a no-op.

    This is the key distributed-safe operation: the SELECT and UPDATE happen in
    a single transaction. Any other node/worker that tries to claim the same row
    will skip it instantly due to SKIP LOCKED, preventing duplicate processing.
    """
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
          SELECT
            (SELECT COUNT(*) FROM jobs WHERE status = 'running' AND node_id = %s) AS my_running,
            EXISTS (
              SELECT 1 FROM cluster_nodes cn
              WHERE cn.node_id <> %s
                AND cn.status = 'online'
                AND cn.approval_status = 'approved'
                AND cn.workers > 0
                AND NOT EXISTS (
                  SELECT 1 FROM jobs WHERE status = 'running' AND node_id = cn.node_id
                )
            ) AS idle_peer
          """,
          (node_id, node_id),
        )
        balance = cur.fetchone()
        if balance and balance.get("my_running", 0) > 0 and balance.get("idle_peer"):
          return None

        over_capped = _profiles_at_cap(conn, profile_caps, is_sqlite=False)
        clauses = ["status = %s", "(next_attempt_at IS NULL OR next_attempt_at <= NOW())"]
        params: list = [STATUS_PENDING]
        if exclude_configs:
          clauses.append("config != ALL(%s)")
          params.append(list(exclude_configs))
        if over_capped:
          clauses.append("(request_profile IS NULL OR request_profile != ALL(%s))")
          params.append(list(over_capped))
        sql = "SELECT id, path, config, args FROM jobs WHERE " + " AND ".join(clauses) + " ORDER BY priority DESC, created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
        cur.execute(sql, tuple(params))
        row = cur.fetchone()
        if row is None:
          return None
        job_id = row["id"]
        cur.execute(
          """
                    UPDATE jobs
                    SET status = %s, worker_id = %s, node_id = %s, started_at = NOW()
                    WHERE id = %s
                """,
          (STATUS_RUNNING, worker_id, node_id, job_id),
        )
    # Fetch the full job row outside the transaction for the caller
    return self.get_job(job_id)

  def get_pending_jobs(self):
    """Get all pending jobs ordered by creation time."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE status = %s ORDER BY created_at ASC", (STATUS_PENDING,))
        return [dict(r) for r in cur.fetchall()]

  def get_next_pending_job(self):
    """Get the next pending job without claiming it (read-only)."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE status = %s ORDER BY created_at ASC LIMIT 1", (STATUS_PENDING,))
        row = cur.fetchone()
        return dict(row) if row else None

  def start_job(self, job_id, worker_id):
    """Mark a job as running (not used by workers — they use claim_next_job)."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    UPDATE jobs SET status = %s, worker_id = %s, started_at = NOW() WHERE id = %s
                """,
          (STATUS_RUNNING, worker_id, job_id),
        )
    self.log.debug("Job %d started by worker %d" % (job_id, worker_id))

  def complete_job(
    self,
    job_id,
    input_size=None,
    output_size=None,
    source_duration_seconds=None,
    encoder_backend=None,
    encoder_name=None,
  ):
    """Mark a job as completed, optionally recording size + duration + encoder."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """UPDATE jobs
                SET status = %s,
                    completed_at = NOW(),
                    input_size_bytes = %s,
                    output_size_bytes = %s,
                    source_duration_seconds = %s,
                    encoder_backend = %s,
                    encoder_name = %s
              WHERE id = %s""",
          (
            STATUS_COMPLETED,
            input_size,
            output_size,
            source_duration_seconds,
            encoder_backend,
            encoder_name,
            job_id,
          ),
        )
    self.log.debug("Job %d completed" % job_id)

  def update_job_ffmpeg_stderr(self, job_id: int, stderr: str | None) -> None:
    """Store the full ffmpeg stderr blob for *job_id*.

    Truncates to ``_FFMPEG_STDERR_MAX_BYTES`` (tail-preserving) so a
    runaway stderr doesn't blow up the row. PostgreSQL TEXT is unbounded
    but multi-MB blobs slow detoasting on queries that return the row.
    """
    if stderr is None:
      return
    payload = stderr if isinstance(stderr, str) else str(stderr)
    encoded = payload.encode("utf-8", errors="replace")
    if len(encoded) > _FFMPEG_STDERR_MAX_BYTES:
      payload = encoded[-_FFMPEG_STDERR_MAX_BYTES:].decode("utf-8", errors="replace")
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET ffmpeg_stderr = %s WHERE id = %s", (payload, job_id))

  def defer_job(self, job_id, delay_seconds, reason=None):
    """Push a running job back to pending with a delay, without bumping retry_count.

    Used by the worker's pre-ffmpeg gates (e.g. output-filesystem pressure)
    that want to defer the job instead of consuming a retry slot.
    """
    delay = max(int(delay_seconds), 0)
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
          UPDATE jobs
          SET status = %s, error = %s,
              next_attempt_at = NOW() + interval '%s seconds',
              started_at = NULL, completed_at = NULL, worker_id = NULL, node_id = NULL
          WHERE id = %s
          """,
          (STATUS_PENDING, reason, delay, job_id),
        )
    self.log.debug("Job %d deferred for %ds (%s)" % (job_id, delay, reason or ""))

  def fail_job(self, job_id, error=None, failure_category=None, failure_cause=None):
    """Mark a job as failed, or requeue with exponential backoff if retries remain."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT retry_count, max_retries FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        if row and row["retry_count"] < row["max_retries"]:
          retry_count = row["retry_count"] + 1
          delay = 2**retry_count * 60
          cur.execute(
            """
                        UPDATE jobs
                        SET status = %s, retry_count = %s, error = %s,
                            next_attempt_at = NOW() + interval '%s seconds',
                            failure_category = %s, failure_cause = %s,
                            started_at = NULL, completed_at = NULL, worker_id = NULL, node_id = NULL
                        WHERE id = %s
                    """,
            (STATUS_PENDING, retry_count, error, delay, failure_category, failure_cause, job_id),
          )
          self.log.debug("Job %d failed (attempt %d/%d), retrying in %ds" % (job_id, retry_count, row["max_retries"], delay))
        else:
          cur.execute(
            """UPDATE jobs SET status = %s, error = %s, completed_at = NOW(),
                                  failure_category = %s, failure_cause = %s
                            WHERE id = %s""",
            (STATUS_FAILED, error, failure_category, failure_cause, job_id),
          )
          self.log.debug("Job %d failed: %s" % (job_id, error))

  def get_job(self, job_id):
    """Get a specific job by ID."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        return dict(row) if row else None

  def get_jobs(self, status=None, config=None, path=None, limit=100, offset=0):
    """Get jobs with optional filtering."""
    query = "SELECT * FROM jobs WHERE 1=1"
    params = []
    if status:
      query += " AND status = %s"
      params.append(status)
    if config:
      query += " AND config = %s"
      params.append(config)
    if path:
      query += " AND path ILIKE %s"
      params.append("%" + path + "%")
    query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]

  def get_stats(self):
    """Get job statistics."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status")
        stats = {r["status"]: r["count"] for r in cur.fetchall()}
        cur.execute("SELECT COUNT(*) AS total FROM jobs")
        stats["total"] = cur.fetchone()["total"]
    return stats

  def get_metrics(self, window: str = "24h") -> dict:
    """Return cluster-wide job metrics for the requested time window.

    window: "24h" | "7d" | "30d" | "all"
    Returns a dict with keys: available, window, kpis, timeseries, nodes.
    """
    wdef = _METRICS_WINDOW_MAP.get(window)
    filter_clause = f"AND completed_at >= NOW() - INTERVAL '{wdef['filter']}'" if wdef else ""

    with self._conn() as conn:
      with conn.cursor() as cur:
        # Snapshot: real-time pending/running/total (no time filter)
        cur.execute("""
                    SELECT
                        COUNT(*) FILTER (WHERE status = 'pending')  AS pending,
                        COUNT(*) FILTER (WHERE status = 'running')  AS running,
                        COUNT(*)                                     AS total
                    FROM jobs
                """)
        snap = cur.fetchone()

        # Windowed KPI: completed/failed within the selected window (or all-time)
        cur.execute(f"""
                    SELECT
                        COUNT(*) FILTER (WHERE status = 'completed')                            AS completed,
                        COUNT(*) FILTER (WHERE status = 'failed')                               AS failed,
                        COUNT(*) FILTER (WHERE status = 'cancelled')                            AS cancelled,
                        ROUND(
                            COUNT(*) FILTER (WHERE status = 'failed')::NUMERIC
                            / NULLIF(COUNT(*) FILTER (WHERE status IN ('completed', 'failed')), 0) * 100,
                            2
                        )                                                                        AS failure_rate_pct,
                        AVG(
                            CASE WHEN status = 'completed'
                                      AND started_at IS NOT NULL
                                      AND completed_at IS NOT NULL
                            THEN EXTRACT(EPOCH FROM (completed_at - started_at)) END
                        )                                                                        AS avg_duration_seconds,
                        PERCENTILE_CONT(0.95) WITHIN GROUP (
                            ORDER BY CASE WHEN status = 'completed'
                                               AND started_at IS NOT NULL
                                               AND completed_at IS NOT NULL
                                     THEN EXTRACT(EPOCH FROM (completed_at - started_at)) END
                        )                                                                        AS p95_duration_seconds,
                        AVG(
                            CASE WHEN status = 'completed'
                                      AND input_size_bytes > 0
                                      AND output_size_bytes IS NOT NULL
                            THEN (1.0 - output_size_bytes::FLOAT / input_size_bytes) * 100 END
                        )                                                                        AS avg_compression_pct,
                        COALESCE(SUM(
                          CASE WHEN status = 'completed'
                                    AND input_size_bytes IS NOT NULL
                                    AND output_size_bytes IS NOT NULL
                                    AND input_size_bytes > output_size_bytes
                          THEN input_size_bytes - output_size_bytes END
                        ), 0)                                                                    AS bytes_saved_total,
                        COALESCE(SUM(
                          CASE WHEN status = 'completed'
                                    AND input_size_bytes IS NOT NULL
                                    AND output_size_bytes IS NOT NULL
                                    AND output_size_bytes > input_size_bytes
                          THEN output_size_bytes - input_size_bytes END
                        ), 0)                                                                    AS bytes_grown_total,
                        COALESCE(SUM(
                          CASE WHEN status = 'completed'
                                    AND source_duration_seconds IS NOT NULL
                                    AND source_duration_seconds > 0
                          THEN source_duration_seconds END
                        ), 0) / 60.0                                                             AS minutes_transcoded_total
                    FROM jobs
                    WHERE 1=1 {filter_clause}
                """)
        kpi_row = cur.fetchone()

        # Per-encoder-backend breakdown over the same window.
        cur.execute(f"""
                    SELECT
                        COALESCE(encoder_backend, 'unknown')                                    AS encoder_backend,
                        COUNT(*) FILTER (WHERE status = 'completed')                            AS count,
                        COALESCE(SUM(
                          CASE WHEN status = 'completed'
                                    AND input_size_bytes IS NOT NULL
                                    AND output_size_bytes IS NOT NULL
                                    AND input_size_bytes > output_size_bytes
                          THEN input_size_bytes - output_size_bytes END
                        ), 0)                                                                    AS bytes_saved,
                        COALESCE(SUM(
                          CASE WHEN status = 'completed'
                                    AND source_duration_seconds IS NOT NULL
                                    AND source_duration_seconds > 0
                          THEN source_duration_seconds END
                        ), 0) / 60.0                                                             AS minutes
                    FROM jobs
                    WHERE status = 'completed' {filter_clause}
                    GROUP BY COALESCE(encoder_backend, 'unknown')
                    ORDER BY count DESC
                """)
        encoders = {
          row["encoder_backend"]: {
            "count": int(row["count"] or 0),
            "bytes_saved": int(row["bytes_saved"] or 0),
            "minutes": round(float(row["minutes"] or 0), 2),
          }
          for row in cur.fetchall()
        }

        # Per-failure-category breakdown over the same window.
        cur.execute(f"""
                    SELECT
                        COALESCE(failure_category, 'unknown')                                   AS failure_category,
                        COUNT(*)                                                                AS count
                    FROM jobs
                    WHERE status = 'failed' {filter_clause}
                    GROUP BY COALESCE(failure_category, 'unknown')
                    ORDER BY count DESC
                """)
        failures = {row["failure_category"]: {"count": int(row["count"] or 0)} for row in cur.fetchall()}

        # Time-series: zero-filled buckets per hour (24h) or per day (7d/30d)
        if wdef:
          trunc = wdef["trunc"]
          cur.execute(f"""
                        WITH ts AS (
                            SELECT generate_series(
                                date_trunc('{trunc}', NOW()) - INTERVAL '{wdef["series_offset"]}',
                                date_trunc('{trunc}', NOW()),
                                INTERVAL '{wdef["step"]}'
                            ) AS bucket
                        )
                        SELECT
                            ts.bucket,
                            COALESCE(COUNT(j.id) FILTER (WHERE j.status = 'completed'), 0) AS completed,
                            COALESCE(COUNT(j.id) FILTER (WHERE j.status = 'failed'),    0) AS failed
                        FROM ts
                        LEFT JOIN jobs j
                            ON  date_trunc('{trunc}', j.completed_at) = ts.bucket
                            AND j.status IN ('completed', 'failed')
                        GROUP BY ts.bucket
                        ORDER BY ts.bucket
                    """)
          timeseries = [
            {
              "bucket": row["bucket"].isoformat(),
              "completed": int(row["completed"]),
              "failed": int(row["failed"]),
            }
            for row in cur.fetchall()
          ]
        else:
          timeseries = []

        # Per-node breakdown
        cur.execute(f"""
                    SELECT
                        j.node_id,
                        COALESCE(n.node_name, j.node_id)                                AS node_name,
                        COUNT(*) FILTER (WHERE j.status = 'completed')                  AS completed,
                        COUNT(*) FILTER (WHERE j.status = 'failed')                     AS failed,
                        AVG(
                            CASE WHEN j.status = 'completed'
                                      AND j.started_at IS NOT NULL
                                      AND j.completed_at IS NOT NULL
                            THEN EXTRACT(EPOCH FROM (j.completed_at - j.started_at)) END
                        )                                                                AS avg_duration_seconds
                    FROM jobs j
                    LEFT JOIN cluster_nodes n ON n.node_id = j.node_id
                    WHERE j.node_id IS NOT NULL {filter_clause}
                    GROUP BY j.node_id, n.node_name
                    ORDER BY completed DESC
                """)
        nodes = [
          {
            "node_id": row["node_id"],
            "node_name": row["node_name"],
            "completed": int(row["completed"]),
            "failed": int(row["failed"]),
            "avg_duration_seconds": float(row["avg_duration_seconds"]) if row["avg_duration_seconds"] is not None else None,
          }
          for row in cur.fetchall()
        ]

    completed = int(kpi_row["completed"] or 0)
    th_hours = wdef["throughput_hours"] if wdef else None
    kpis = {
      "completed": completed,
      "failed": int(kpi_row["failed"] or 0),
      "cancelled": int(kpi_row["cancelled"] or 0),
      "pending": int(snap["pending"] or 0),
      "running": int(snap["running"] or 0),
      "total": int(snap["total"] or 0),
      "failure_rate_pct": float(kpi_row["failure_rate_pct"]) if kpi_row["failure_rate_pct"] is not None else None,
      "avg_duration_seconds": float(kpi_row["avg_duration_seconds"]) if kpi_row["avg_duration_seconds"] is not None else None,
      "p95_duration_seconds": float(kpi_row["p95_duration_seconds"]) if kpi_row["p95_duration_seconds"] is not None else None,
      "avg_compression_pct": float(kpi_row["avg_compression_pct"]) if kpi_row["avg_compression_pct"] is not None else None,
      "bytes_saved_total": int(kpi_row["bytes_saved_total"] or 0),
      "bytes_grown_total": int(kpi_row["bytes_grown_total"] or 0),
      "minutes_transcoded_total": round(float(kpi_row["minutes_transcoded_total"] or 0), 2),
      "throughput_per_hour": round(completed / th_hours, 2) if th_hours else None,
    }
    return {
      "available": True,
      "window": window,
      "kpis": kpis,
      "timeseries": timeseries,
      "nodes": nodes,
      "encoders": encoders,
      "failures": failures,
    }

  def get_running_jobs(self):
    """Get all currently running jobs."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE status = %s", (STATUS_RUNNING,))
        return [dict(r) for r in cur.fetchall()]

  def cleanup_old_jobs(self, days=30):
    """Remove completed/failed jobs older than specified days."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    DELETE FROM jobs
                    WHERE status IN (%s, %s)
                    AND completed_at < NOW() - make_interval(days => %s)
                """,
          (STATUS_COMPLETED, STATUS_FAILED, days),
        )
        deleted = cur.rowcount
    if deleted > 0:
      self.log.info("Cleaned up %d old jobs" % deleted)
    return deleted

  def pending_count(self):
    """Get count of pending jobs."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM jobs WHERE status = %s", (STATUS_PENDING,))
        return cur.fetchone()["count"]

  def pending_count_for_config(self, config):
    """Get count of pending jobs for a specific config."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM jobs WHERE status = %s AND config = %s", (STATUS_PENDING, config))
        return cur.fetchone()["count"]

  def heartbeat(self, node_id, host, workers, started_at, version=None, hwaccel=None, node_name=None):
    """Upsert this node's heartbeat row in cluster_nodes.

    started_at is set on INSERT and never overwritten on UPDATE, so it
    always reflects when this daemon process started. A change in
    started_at between heartbeats indicates the node was restarted.

    Preserves 'draining' and 'paused' status on conflict — only resets to
    'online' if the node was in a neutral state.

    Returns None. Commands are now dispatched via the node_commands table.
    """
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    INSERT INTO cluster_nodes (node_id, host, workers, last_seen, started_at,
                        status, running_jobs, pending_jobs, version, hwaccel, node_name)
                    VALUES (
                        %s, %s, %s, NOW(), %s, 'online',
                        (SELECT COUNT(*) FROM jobs WHERE status = 'running' AND node_id = %s),
                        (SELECT COUNT(*) FROM jobs WHERE status = 'pending'),
                        %s, %s, %s
                    )
                    ON CONFLICT (node_id) DO UPDATE SET
                        host         = EXCLUDED.host,
                        workers      = EXCLUDED.workers,
                        last_seen    = NOW(),
                        status       = CASE
                            WHEN cluster_nodes.status IN ('draining', 'paused') THEN cluster_nodes.status
                            ELSE 'online'
                        END,
                        running_jobs = EXCLUDED.running_jobs,
                        pending_jobs = EXCLUDED.pending_jobs,
                        version      = COALESCE(EXCLUDED.version, cluster_nodes.version),
                        hwaccel      = COALESCE(EXCLUDED.hwaccel, cluster_nodes.hwaccel),
                        node_name    = COALESCE(EXCLUDED.node_name, cluster_nodes.node_name)
                """,
          (node_id, host, workers, started_at, node_id, version, hwaccel, node_name or None),
        )
    return

  def is_node_approved(self, node_id):
    """Return True when node approval_status is approved."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT approval_status FROM cluster_nodes WHERE node_id = %s", (node_id,))
        row = cur.fetchone()
        return bool(row and row.get("approval_status") == "approved")

  def set_node_approval(self, node_id, approved=True, actor=None, note=None):
    """Set node approval state and return the updated node row."""
    status = "approved" if approved else "rejected"
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
          UPDATE cluster_nodes
          SET approval_status = %s,
              approved_by = %s,
              approved_at = NOW(),
              approval_note = %s
          WHERE node_id = %s
          RETURNING *
          """,
          (status, actor, note, node_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None

  def delete_node(self, node_id):
    """Delete a cluster node row by id. Returns True if deleted."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("DELETE FROM cluster_nodes WHERE node_id = %s", (node_id,))
        return cur.rowcount > 0

  def set_node_status(self, node_id, status):
    """Set cluster_nodes.status for a node. Returns True if a row was updated.

    Used by drain/pause/resume command execution so the DB reflects the
    in-memory worker-pool gate. The heartbeat upsert preserves
    'draining'/'paused' on conflict, so once set here the status persists
    across heartbeats until explicitly cleared (by a 'resume' command).
    """
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("UPDATE cluster_nodes SET status = %s WHERE node_id = %s", (status, node_id))
        return cur.rowcount > 0

  def get_cluster_nodes(self):
    """Return all rows from cluster_nodes ordered by last_seen descending.

    Includes uptime_seconds (seconds since daemon start) derived from started_at,
    and an active_jobs list of {job_id, path, config} for each running job on the node.
    """
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("""
                    SELECT *,
                           EXTRACT(EPOCH FROM (NOW() - started_at))::INT AS uptime_seconds
                    FROM cluster_nodes
                    ORDER BY last_seen DESC
                """)
        nodes = [dict(r) for r in cur.fetchall()]
        if not nodes:
          return nodes
        node_ids = [n["node_id"] for n in nodes]
        cur.execute(
          """
                    SELECT node_id, id AS job_id, path, config
                    FROM jobs
                    WHERE status = 'running' AND node_id = ANY(%s)
                    ORDER BY started_at
                    """,
          (node_ids,),
        )
        jobs_by_node = {}
        for row in cur.fetchall():
          jobs_by_node.setdefault(row["node_id"], []).append({"job_id": row["job_id"], "path": row["path"], "config": row["config"]})
        for node in nodes:
          node["active_jobs"] = jobs_by_node.get(node["node_id"], [])
        return nodes

  def recover_stale_nodes(self, stale_seconds=120):
    """Mark nodes that haven't sent a heartbeat as offline and requeue their jobs.

    Returns a list of (node_id, recovered_job_count) tuples for any nodes cleaned up.
    """
    recovered = []
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    SELECT node_id FROM cluster_nodes
                    WHERE status = 'online'
                    AND last_seen < NOW() - make_interval(secs => %s)
                """,
          (stale_seconds,),
        )
        stale_nodes = [r["node_id"] for r in cur.fetchall()]

        for stale_node_id in stale_nodes:
          job_count = self._requeue_running_jobs_for_node(stale_node_id)
          cur.execute("UPDATE cluster_nodes SET status = 'offline' WHERE node_id = %s", (stale_node_id,))
          recovered.append((stale_node_id, job_count))

    for stale_node_id, job_count in recovered:
      self.log.warning("Node %s declared stale — requeued %d running jobs" % (stale_node_id, job_count))
    return recovered

  def mark_node_offline(self, node_id, remove=False):
    """Mark this node offline or remove it from cluster_nodes.

    When remove=True, the node row is deleted entirely. This is intended for
    clean daemon shutdown/restart so the node disappears from cluster status
    immediately instead of lingering as offline.
    """
    requeued = self._requeue_running_jobs_for_node(node_id)
    with self._conn() as conn:
      with conn.cursor() as cur:
        if remove:
          cur.execute("DELETE FROM cluster_nodes WHERE node_id = %s", (node_id,))
        else:
          cur.execute("UPDATE cluster_nodes SET status = 'offline' WHERE node_id = %s", (node_id,))
    if requeued:
      self.log.info("Requeued %d running jobs on shutdown" % requeued)
    if remove:
      self.log.info("Removed node %s from cluster registry" % node_id)

  def send_node_command(self, node_id, command, requested_by=None):
    """Insert a command into node_commands for one or all online nodes.

    node_id may be a specific node ID string, or None to broadcast to all
    online nodes. command should be 'restart', 'shutdown', 'drain', 'pause',
    or 'resume'.
    Returns the list of node_ids that were targeted.
    """
    with self._conn() as conn:
      with conn.cursor() as cur:
        if node_id:
          cur.execute(
            """
            INSERT INTO node_commands (node_id, command, issued_by)
            VALUES (%s, %s, %s)
            """,
            (node_id, command, requested_by),
          )
          return [node_id]
        else:
          cur.execute(
            "SELECT node_id FROM cluster_nodes WHERE status = 'online'",
          )
          targets = [r["node_id"] for r in cur.fetchall()]
          if targets:
            cur.executemany(
              "INSERT INTO node_commands (node_id, command, issued_by) VALUES (%s, %s, %s)",
              [(nid, command, requested_by) for nid in targets],
            )
          return targets

  def poll_node_command(self, node_id):
    """Claim the oldest pending command for node_id, marking it 'executing'.

    Uses SELECT FOR UPDATE SKIP LOCKED so concurrent callers cannot claim
    the same row. Returns the claimed row as a dict, or None if no pending
    command exists.
    """
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
          SELECT * FROM node_commands
          WHERE node_id = %s AND status = 'pending'
          ORDER BY issued_at ASC
          LIMIT 1
          FOR UPDATE SKIP LOCKED
          """,
          (node_id,),
        )
        row = cur.fetchone()
        if row is None:
          return None
        cur.execute(
          "UPDATE node_commands SET status = 'executing' WHERE id = %s",
          (row["id"],),
        )
        return dict(row)

  def ack_node_command(self, cmd_id, status):
    """Update the status of a node_commands row by id."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          "UPDATE node_commands SET status = %s WHERE id = %s",
          (status, cmd_id),
        )

  def insert_logs(self, records):
    """Bulk insert log records into the logs table.

    Each record is a dict with keys: node_id, level, logger, message.
    """
    if not records:
      return
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.executemany(
          "INSERT INTO logs (node_id, level, logger, message) VALUES (%s, %s, %s, %s)",
          [(r["node_id"], r["level"], r.get("logger"), r["message"]) for r in records],
        )

  def cleanup_old_logs(self, days):
    """Delete log entries older than *days* days. Returns count deleted."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          "DELETE FROM logs WHERE timestamp < NOW() - make_interval(days => %s)",
          (days,),
        )
        return cur.rowcount

  def get_logs(self, node_id=None, level=None, limit=100, offset=0):
    """Return log entries with optional node_id and level filters.

    Results are ordered newest-first. limit and offset support pagination.
    """
    query = "SELECT id, node_id, level, logger, message, timestamp FROM logs WHERE 1=1"
    params = []
    if node_id:
      query += " AND node_id = %s"
      params.append(node_id)
    if level:
      query += " AND level = %s"
      params.append(level)
    query += " ORDER BY timestamp DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]

  def get_cluster_config(self):
    """Return the cluster-wide base config dict, or None if not set."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT config FROM cluster_config WHERE id = 1")
        row = cur.fetchone()
        if row is None:
          return None
        return _yaml.safe_load(row["config"]) or {}

  def set_cluster_config(self, config_dict, updated_by=None):
    """Upsert the cluster-wide base config, stripping secrets from daemon: section."""
    data = copy.deepcopy(config_dict)
    daemon = data.get("daemon", {})
    for key in list(daemon):
      if key in SECRET_KEYS:
        del daemon[key]
    config_str = _yaml.safe_dump(data)
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
          INSERT INTO cluster_config (id, config, updated_at, updated_by)
          VALUES (1, %s, NOW(), %s)
          ON CONFLICT (id) DO UPDATE
              SET config = EXCLUDED.config,
                  updated_at = NOW(),
                  updated_by = EXCLUDED.updated_by
          """,
          (config_str, updated_by),
        )

  def expire_offline_nodes(self, expiry_days):
    """Hard-delete offline nodes whose last_seen is older than expiry_days.

    Cleans up orphaned node_commands rows first.
    Returns list of deleted node_ids.
    """
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
          SELECT node_id FROM cluster_nodes
          WHERE status = 'offline'
          AND last_seen < NOW() - make_interval(days => %s)
          """,
          (expiry_days,),
        )
        expired = [r["node_id"] for r in cur.fetchall()]
    if not expired:
      return []
    self.cleanup_orphaned_commands(expired)
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("DELETE FROM cluster_nodes WHERE node_id = ANY(%s)", (expired,))
    for nid in expired:
      self.log.info("Expired offline node: %s" % nid)
    return expired

  def cleanup_orphaned_commands(self, node_ids):
    """Delete node_commands rows for node_ids that no longer exist in cluster_nodes."""
    if not node_ids:
      return 0
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("DELETE FROM node_commands WHERE node_id = ANY(%s)", (node_ids,))
        return cur.rowcount

  def get_logs_for_archival(self, before_days):
    """Return log rows older than before_days, ordered by node_id then timestamp."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
          SELECT id, node_id, level, logger, message, timestamp
          FROM logs
          WHERE timestamp < NOW() - make_interval(days => %s)
          ORDER BY node_id, timestamp
          """,
          (before_days,),
        )
        return [dict(r) for r in cur.fetchall()]

  def delete_logs_before(self, before_days):
    """Delete log rows older than before_days. Returns count deleted."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          "DELETE FROM logs WHERE timestamp < NOW() - make_interval(days => %s)",
          (before_days,),
        )
        return cur.rowcount

  def requeue_job(self, job_id):
    """Reset a failed job back to pending. Returns True if the job was requeued."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    UPDATE jobs
                    SET status = %s, worker_id = NULL, node_id = NULL,
                        error = NULL, started_at = NULL, completed_at = NULL
                    WHERE id = %s AND status = %s
                """,
          (STATUS_PENDING, job_id, STATUS_FAILED),
        )
        requeued = cur.rowcount > 0
    if requeued:
      self.log.info("Requeued failed job %d" % job_id)
    return requeued

  def requeue_failed_jobs(self, config=None):
    """Reset all failed jobs (optionally filtered by config) back to pending."""
    sql = """
            UPDATE jobs
            SET status = %s, worker_id = NULL, node_id = NULL,
                error = NULL, started_at = NULL, completed_at = NULL
            WHERE status = %s
        """
    params = [STATUS_PENDING, STATUS_FAILED]
    if config:
      sql += " AND config = %s"
      params.append(config)
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(sql, params)
        count = cur.rowcount
    if count > 0:
      self.log.info("Requeued %d failed jobs" % count)
    return count

  def cancel_job(self, job_id):
    """Cancel a pending or running job. Returns True if the job was updated."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    UPDATE jobs
                    SET status = 'cancelled', error = 'Cancelled by user',
                        completed_at = NOW()
                    WHERE id = %s AND status IN (%s, %s)
                    RETURNING id
                """,
          (job_id, STATUS_PENDING, STATUS_RUNNING),
        )
        cancelled = cur.fetchone() is not None
    if cancelled:
      self.log.info("Cancelled job %d" % job_id)
    return cancelled

  def set_job_priority(self, job_id, priority):
    """Set the priority of a pending job. Returns True if the job was updated."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          "UPDATE jobs SET priority = %s WHERE id = %s AND status = %s RETURNING id",
          (priority, job_id, STATUS_PENDING),
        )
        updated = cur.fetchone() is not None
    if updated:
      self.log.info("Set priority %d for job %d" % (priority, job_id))
    return updated

  def delete_failed_jobs(self):
    """Delete all jobs with status 'failed'. Returns count deleted."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("DELETE FROM jobs WHERE status = %s", (STATUS_FAILED,))
        deleted = cur.rowcount
    if deleted > 0:
      self.log.info("Deleted %d failed jobs" % deleted)
    return deleted

  def delete_jobs(self, job_ids):
    """Delete a specific set of jobs by id. Returns the list of ids actually deleted.

    The caller is responsible for cancelling any running subprocesses
    before calling this — this method only removes the row.
    """
    ids = [int(j) for j in (job_ids or [])]
    if not ids:
      return []
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("DELETE FROM jobs WHERE id = ANY(%s) RETURNING id", (ids,))
        deleted = [row["id"] for row in cur.fetchall()]
    if deleted:
      self.log.info("Deleted %d jobs by id" % len(deleted))
    return deleted

  def delete_offline_nodes(self):
    """Delete cluster_nodes rows where status is not 'online'. Returns count deleted."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("DELETE FROM cluster_nodes WHERE status != 'online'")
        deleted = cur.rowcount
    if deleted > 0:
      self.log.info("Deleted %d offline nodes" % deleted)
    return deleted

  def delete_all_jobs(self):
    """Delete every row from the jobs table. Returns count deleted."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("DELETE FROM jobs")
        deleted = cur.rowcount
    self.log.info("Deleted all jobs (%d rows)" % deleted)
    return deleted

  def filter_unscanned(self, paths):
    """Return the subset of paths not yet recorded in scanned_files."""
    if not paths:
      return []
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          "SELECT path FROM scanned_files WHERE path = ANY(%s)",
          (paths,),
        )
        already = {row["path"] for row in cur.fetchall()}
    return [p for p in paths if p not in already]

  def record_scanned(self, paths):
    """Record paths as scanned. Ignores duplicates."""
    if not paths:
      return
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.executemany(
          "INSERT INTO scanned_files (path) VALUES (%s) ON CONFLICT DO NOTHING",
          [(p,) for p in paths],
        )

  # ------------------------------------------------------------------
  # Library audit (distributed scanner)
  # ------------------------------------------------------------------

  _AUDIT_ENUMERATE_LOCK_KEY = 4242000001  # arbitrary stable bigint for pg_advisory lock

  def create_audit_run(self, scope_paths, triggered_by):
    """Create a new audit run; returns its id."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          "INSERT INTO library_audit_runs (status, triggered_by, scope_paths) VALUES (%s, %s, %s) RETURNING id",
          (AUDIT_RUN_ENUMERATING, triggered_by, list(scope_paths)),
        )
        return cur.fetchone()["id"]

  def set_audit_run_status(self, audit_id, status, error=None):
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          "UPDATE library_audit_runs SET status = %s, error = COALESCE(%s, error) WHERE id = %s",
          (status, error, audit_id),
        )

  def enqueue_audit_units(self, audit_id, units):
    """``units`` is iterable of (path, kind_hint). Returns row count inserted."""
    rows = list(units)
    if not rows:
      return 0
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.executemany(
          "INSERT INTO library_audit_queue (audit_id, path, kind_hint) VALUES (%s, %s, %s)",
          [(audit_id, p, k) for (p, k) in rows],
        )
        cur.execute(
          "UPDATE library_audit_runs SET total_units = total_units + %s, status = %s WHERE id = %s",
          (len(rows), AUDIT_RUN_PROBING, audit_id),
        )
    return len(rows)

  def claim_audit_units(self, node_id, audit_id, batch=50):
    """Atomic batch claim. Mirrors claim_next_job's FOR UPDATE SKIP LOCKED pattern."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    UPDATE library_audit_queue q
                       SET status = %s, claimed_by = %s, claimed_at = NOW()
                      FROM (
                            SELECT id FROM library_audit_queue
                             WHERE audit_id = %s AND status = %s
                             ORDER BY id LIMIT %s
                             FOR UPDATE SKIP LOCKED
                           ) sub
                     WHERE q.id = sub.id
                    RETURNING q.id, q.audit_id, q.path, q.kind_hint
                    """,
          (AUDIT_UNIT_CLAIMED, node_id, audit_id, AUDIT_UNIT_PENDING, batch),
        )
        return [dict(r) for r in cur.fetchall()]

  def mark_audit_unit_done(self, unit_id, error=None):
    status = AUDIT_UNIT_ERROR if error else AUDIT_UNIT_DONE
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    UPDATE library_audit_queue
                       SET status = %s, finished_at = NOW(), error = %s
                     WHERE id = %s
                    RETURNING audit_id
                    """,
          (status, error, unit_id),
        )
        row = cur.fetchone()
        if row:
          cur.execute(
            "UPDATE library_audit_runs SET done_units = done_units + 1 WHERE id = %s",
            (row["audit_id"],),
          )

  def release_stale_audit_claims(self, stale_seconds):
    """Reset claimed-but-orphaned units back to pending. Returns row count."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    UPDATE library_audit_queue
                       SET status = %s, claimed_by = NULL, claimed_at = NULL
                     WHERE status = %s
                       AND claimed_at < NOW() - make_interval(secs => %s)
                    """,
          (AUDIT_UNIT_PENDING, AUDIT_UNIT_CLAIMED, int(stale_seconds)),
        )
        return cur.rowcount

  def requeue_audit_claims_for_node(self, node_id):
    """On worker startup, undo any claims this node still owns from a prior run."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    UPDATE library_audit_queue
                       SET status = %s, claimed_by = NULL, claimed_at = NULL
                     WHERE status = %s AND claimed_by = %s
                    """,
          (AUDIT_UNIT_PENDING, AUDIT_UNIT_CLAIMED, node_id),
        )
        return cur.rowcount

  def list_active_audit_runs(self):
    """Runs that still have pending or claimed units to probe."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    SELECT id, scope_paths, total_units, done_units
                      FROM library_audit_runs
                     WHERE status IN (%s, %s, %s)
                     ORDER BY id
                    """,
          (AUDIT_RUN_QUEUED, AUDIT_RUN_ENUMERATING, AUDIT_RUN_PROBING),
        )
        return [dict(r) for r in cur.fetchall()]

  def complete_finished_audit_runs(self):
    """Flip any probing run with zero pending+claimed units to completed.

    Only the enumerator (holding the advisory lock) should call this.
    """
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    UPDATE library_audit_runs r
                       SET status = %s, completed_at = NOW()
                     WHERE r.status = %s
                       AND NOT EXISTS (
                           SELECT 1 FROM library_audit_queue q
                            WHERE q.audit_id = r.id
                              AND q.status IN (%s, %s)
                       )
                    RETURNING r.id
                    """,
          (AUDIT_RUN_COMPLETED, AUDIT_RUN_PROBING, AUDIT_UNIT_PENDING, AUDIT_UNIT_CLAIMED),
        )
        return [r["id"] for r in cur.fetchall()]

  def get_audit_run(self, audit_id):
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT * FROM library_audit_runs WHERE id = %s", (audit_id,))
        row = cur.fetchone()
        if not row:
          return None
        cur.execute(
          """
                    SELECT claimed_by, COUNT(*) AS units
                      FROM library_audit_queue
                     WHERE audit_id = %s AND status IN (%s, %s)
                     GROUP BY claimed_by
                    """,
          (audit_id, AUDIT_UNIT_CLAIMED, AUDIT_UNIT_DONE),
        )
        per_node = [dict(r) for r in cur.fetchall()]
        out = dict(row)
        out["per_node_progress"] = per_node
        return out

  def list_audit_runs(self, limit=50, offset=0):
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          "SELECT * FROM library_audit_runs ORDER BY id DESC LIMIT %s OFFSET %s",
          (limit, offset),
        )
        return [dict(r) for r in cur.fetchall()]

  def try_acquire_audit_enumerate_lock(self):
    """Best-effort exclusive lock so only one node enumerates. Session-scoped.

    Returns the open connection on success (caller must release); None when
    another node already holds it.
    """
    conn = self._pool.getconn()
    try:
      with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (self._AUDIT_ENUMERATE_LOCK_KEY,))
        ok = cur.fetchone()["ok"]
      if not ok:
        self._pool.putconn(conn)
        return None
      return conn
    except Exception:
      self._pool.putconn(conn)
      raise

  def release_audit_enumerate_lock(self, conn):
    if conn is None:
      return
    try:
      with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (self._AUDIT_ENUMERATE_LOCK_KEY,))
      conn.commit()
    except Exception:
      try:
        conn.rollback()
      except Exception:
        pass
    finally:
      self._pool.putconn(conn)

  def upsert_finding(self, kind, path, details, audit_id=None):
    """Insert or refresh a finding; reopens dismissed/resolved findings if they recur.

    Returns the finding id.
    """
    payload = json.dumps(details or {})
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    INSERT INTO library_findings (kind, path, details, status, audit_id)
                    VALUES (%s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (kind, path) DO UPDATE
                       SET last_seen_at = NOW(),
                           details      = EXCLUDED.details,
                           audit_id     = EXCLUDED.audit_id,
                           status       = CASE
                                            WHEN library_findings.status IN ('dismissed', 'resolved')
                                              THEN %s
                                            ELSE library_findings.status
                                          END
                    RETURNING id
                    """,
          (kind, path, payload, STATUS_OPEN, audit_id, STATUS_OPEN),
        )
        return cur.fetchone()["id"]

  def get_findings(self, status=None, kind=None, path=None, limit=50, offset=0):
    where = []
    params = []
    if status:
      where.append("status = %s")
      params.append(status)
    if kind:
      where.append("kind = %s")
      params.append(kind)
    if path:
      where.append("path = %s")
      params.append(path)
    sql = "SELECT * FROM library_findings"
    if where:
      sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY last_seen_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

  def get_finding(self, finding_id):
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT * FROM library_findings WHERE id = %s", (finding_id,))
        row = cur.fetchone()
        return dict(row) if row else None

  def set_finding_status(self, finding_id, status):
    """Transition a finding through the open → acked/dismissed/resolved lifecycle."""
    ts_field = {
      "acked": "acked_at",
      "resolved": "resolved_at",
    }.get(status)
    sql = "UPDATE library_findings SET status = %s"
    params = [status]
    if ts_field:
      sql += ", %s = NOW()" % ts_field
    sql += " WHERE id = %s"
    params.append(finding_id)
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount

  def record_media_id(self, audit_id, path, media_id):
    """Upsert an observed (audit_id, path) → media_id for later duplicate-ID rollup."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    INSERT INTO library_audit_media_ids (audit_id, path, media_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (audit_id, path) DO UPDATE SET media_id = EXCLUDED.media_id
                    """,
          (audit_id, path, media_id),
        )

  def find_duplicate_media_ids(self, audit_id):
    """Return {media_id: [paths]} for ids observed at ≥2 paths in the run."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          """
                    SELECT media_id, ARRAY_AGG(path ORDER BY path) AS paths
                      FROM library_audit_media_ids
                     WHERE audit_id = %s
                     GROUP BY media_id
                    HAVING COUNT(*) > 1
                    """,
          (audit_id,),
        )
        return {r["media_id"]: r["paths"] for r in cur.fetchall()}

  def purge_audit_media_ids(self, audit_id):
    """Drop the scratch media-id rows once duplicate rollup has finished."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute("DELETE FROM library_audit_media_ids WHERE audit_id = %s", (audit_id,))
        return cur.rowcount
