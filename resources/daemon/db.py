import copy
import json
from contextlib import contextmanager

import yaml as _yaml

from resources.daemon.constants import SECRET_KEYS, STATUS_COMPLETED, STATUS_FAILED, STATUS_PENDING, STATUS_RUNNING, resolve_node_id
from resources.log import getLogger

log = getLogger("DAEMON")

_METRICS_WINDOW_MAP = {
  "24h": {"trunc": "hour", "filter": "24 hours", "series_offset": "23 hours", "step": "1 hour", "throughput_hours": 24.0},
  "7d": {"trunc": "day", "filter": "7 days", "series_offset": "6 days", "step": "1 day", "throughput_hours": 168.0},
  "30d": {"trunc": "day", "filter": "30 days", "series_offset": "29 days", "step": "1 day", "throughput_hours": 720.0},
}


class PostgreSQLJobDatabase:
  """PostgreSQL-backed job queue for distributed multi-node operation.

  Uses SELECT FOR UPDATE SKIP LOCKED to atomically claim jobs, ensuring
  no two nodes ever process the same file. Requires psycopg2-binary.

  Usage:
      db = PostgreSQLJobDatabase("postgresql://user:pass@host/sma")
      SMA_DAEMON_DB_URL=postgresql://user:pass@host/sma python daemon.py
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
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_completed ON jobs(status, completed_at)")
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

  def add_job(self, path, config, args=None, max_retries=0):
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
        cur.execute("INSERT INTO jobs (path, config, args, status, max_retries) VALUES (%s, %s, %s, %s, %s) RETURNING id", (path, config, args_json, STATUS_PENDING, max_retries))
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

  def claim_next_job(self, worker_id, node_id, exclude_configs=None):
    """Atomically claim the next pending job using SELECT FOR UPDATE SKIP LOCKED.

    exclude_configs: set of config paths already held by a running job —
    jobs for those configs are skipped so a free worker can pick up work
    for a different config rather than blocking on a locked one.

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

        if exclude_configs:
          cur.execute(
            """
                        SELECT id, path, config, args
                        FROM jobs
                        WHERE status = %s AND config != ALL(%s)
                          AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
                        ORDER BY priority DESC, created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    """,
            (STATUS_PENDING, list(exclude_configs)),
          )
        else:
          cur.execute(
            """
                        SELECT id, path, config, args
                        FROM jobs
                        WHERE status = %s
                          AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
                        ORDER BY priority DESC, created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    """,
            (STATUS_PENDING,),
          )
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

  def complete_job(self, job_id, input_size=None, output_size=None):
    """Mark a job as completed, optionally recording input/output file sizes."""
    with self._conn() as conn:
      with conn.cursor() as cur:
        cur.execute(
          "UPDATE jobs SET status = %s, completed_at = NOW(), input_size_bytes = %s, output_size_bytes = %s WHERE id = %s",
          (STATUS_COMPLETED, input_size, output_size, job_id),
        )
    self.log.debug("Job %d completed" % job_id)

  def fail_job(self, job_id, error=None):
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
                            started_at = NULL, completed_at = NULL, worker_id = NULL, node_id = NULL
                        WHERE id = %s
                    """,
            (STATUS_PENDING, retry_count, error, delay, job_id),
          )
          self.log.debug("Job %d failed (attempt %d/%d), retrying in %ds" % (job_id, retry_count, row["max_retries"], delay))
        else:
          cur.execute("UPDATE jobs SET status = %s, error = %s, completed_at = NOW() WHERE id = %s", (STATUS_FAILED, error, job_id))
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
                        )                                                                        AS avg_compression_pct
                    FROM jobs
                    WHERE 1=1 {filter_clause}
                """)
        kpi_row = cur.fetchone()

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
      "throughput_per_hour": round(completed / th_hours, 2) if th_hours else None,
    }
    return {
      "available": True,
      "window": window,
      "kpis": kpis,
      "timeseries": timeseries,
      "nodes": nodes,
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
    return None

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
