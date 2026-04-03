import json
import socket
import sqlite3
import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager

from resources.daemon.constants import DATABASE_PATH, STATUS_COMPLETED, STATUS_FAILED, STATUS_PENDING, STATUS_RUNNING
from resources.log import getLogger

log = getLogger("DAEMON")


class BaseJobDatabase(ABC):
    """Abstract interface shared by JobDatabase (SQLite) and PostgreSQLJobDatabase."""

    #: True for the PostgreSQL backend; False for SQLite.
    #: Use this instead of isinstance(db, PostgreSQLJobDatabase) at call sites.
    is_distributed: bool = False

    @abstractmethod
    def close(self): ...

    @abstractmethod
    def add_job(self, path, config, args=None, max_retries=0): ...

    @abstractmethod
    def find_active_job(self, path): ...

    @abstractmethod
    def claim_next_job(self, worker_id, node_id, exclude_configs=None): ...

    @abstractmethod
    def complete_job(self, job_id): ...

    @abstractmethod
    def fail_job(self, job_id, error=None): ...

    @abstractmethod
    def get_job(self, job_id): ...

    @abstractmethod
    def get_jobs(self, status=None, config=None, limit=100, offset=0): ...

    @abstractmethod
    def get_stats(self): ...

    @abstractmethod
    def get_running_jobs(self): ...

    @abstractmethod
    def cleanup_old_jobs(self, days=30): ...

    @abstractmethod
    def pending_count(self): ...

    @abstractmethod
    def pending_count_for_config(self, config): ...

    @abstractmethod
    def requeue_job(self, job_id): ...

    @abstractmethod
    def requeue_failed_jobs(self, config=None): ...

    @abstractmethod
    def cancel_job(self, job_id): ...

    @abstractmethod
    def set_job_priority(self, job_id, priority): ...

    @abstractmethod
    def filter_unscanned(self, paths): ...

    @abstractmethod
    def record_scanned(self, paths): ...


class JobDatabase(BaseJobDatabase):
    """SQLite database for persistent job queue storage."""

    def __init__(self, db_path=DATABASE_PATH, logger=None):
        self.db_path = db_path
        self.log = logger or log
        self._local = threading.local()
        self._all_connections = []  # track every connection across all threads
        self._conn_lock = threading.Lock()
        self._init_db()

    def _get_connection(self):
        """Get thread-local database connection."""
        if not hasattr(self._local, "connection") or self._local.connection is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
            conn.row_factory = sqlite3.Row
            self._local.connection = conn
            with self._conn_lock:
                self._all_connections.append(conn)
        return self._local.connection

    def close(self):
        """Close all database connections (across all threads)."""
        with self._conn_lock:
            conns = list(self._all_connections)
            self._all_connections.clear()
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        self._local.connection = None

    @contextmanager
    def _cursor(self):
        """Context manager for database cursor with auto-commit."""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    def _init_db(self):
        """Initialize database schema."""
        with self._cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    config TEXT NOT NULL,
                    args TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'pending',
                    worker_id INTEGER,
                    node_id TEXT,
                    error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP
                )
            """)
            # Migrate existing databases that lack columns added in later versions
            for col_sql in [
                "ALTER TABLE jobs ADD COLUMN node_id TEXT",
                "ALTER TABLE jobs ADD COLUMN retry_count INTEGER DEFAULT 0",
                "ALTER TABLE jobs ADD COLUMN max_retries INTEGER DEFAULT 0",
                "ALTER TABLE jobs ADD COLUMN next_attempt_at TIMESTAMP",
                "ALTER TABLE jobs ADD COLUMN priority INTEGER DEFAULT 0",
            ]:
                try:
                    cursor.execute(col_sql)
                except sqlite3.OperationalError:
                    pass  # Column already exists
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_config ON jobs(config)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at)
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scanned_files (
                    path       TEXT PRIMARY KEY,
                    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        self.log.info("Database initialized: %s" % self.db_path)

        # Reset any jobs that were running when daemon stopped
        self._reset_running_jobs()

    def _reset_running_jobs(self):
        """Reset jobs that were running when daemon stopped back to pending."""
        with self._cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET status = ?, worker_id = NULL, node_id = NULL, started_at = NULL
                WHERE status = ?
            """,
                (STATUS_PENDING, STATUS_RUNNING),
            )
            if cursor.rowcount > 0:
                self.log.info("Reset %d interrupted jobs to pending" % cursor.rowcount)

    def add_job(self, path, config, args=None, max_retries=0):
        """Add a new job to the queue. Returns job ID, or None if a duplicate is already pending/running."""
        args_json = json.dumps(args or [])
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM jobs WHERE path = ? AND status IN (?, ?) LIMIT 1
            """,
                (path, STATUS_PENDING, STATUS_RUNNING),
            )
            existing = cursor.fetchone()
            if existing:
                self.log.debug("Duplicate job for path: %s (existing job %d)" % (path, existing["id"]))
                return None
            cursor.execute(
                """
                INSERT INTO jobs (path, config, args, status, max_retries)
                VALUES (?, ?, ?, ?, ?)
            """,
                (path, config, args_json, STATUS_PENDING, max_retries),
            )
            job_id = cursor.lastrowid
        self.log.debug("Added job %d: %s" % (job_id, path))
        return job_id

    def find_active_job(self, path):
        """Find a pending or running job for the given path, if any."""
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM jobs WHERE path = ? AND status IN (?, ?) LIMIT 1
            """,
                (path, STATUS_PENDING, STATUS_RUNNING),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def claim_next_job(self, worker_id, node_id, exclude_configs=None):
        """Atomically claim the next pending job for this worker.

        exclude_configs: set of config paths already held by a running job —
        jobs for those configs are skipped so a free worker can pick up work
        for a different config rather than blocking on a locked one.

        Returns job dict or None.
        """
        with self._cursor() as cursor:
            if exclude_configs:
                placeholders = ",".join("?" * len(exclude_configs))
                cursor.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = ? AND config NOT IN (%s)
                      AND (next_attempt_at IS NULL OR next_attempt_at <= CURRENT_TIMESTAMP)
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                """
                    % placeholders,
                    (STATUS_PENDING, *exclude_configs),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = ?
                      AND (next_attempt_at IS NULL OR next_attempt_at <= CURRENT_TIMESTAMP)
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                """,
                    (STATUS_PENDING,),
                )
            row = cursor.fetchone()
            if row is None:
                return None
            job = dict(row)
            # Use conditional UPDATE to guard against a concurrent claim on the same row
            cursor.execute(
                """
                UPDATE jobs
                SET status = ?, worker_id = ?, node_id = ?, started_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = ?
            """,
                (STATUS_RUNNING, worker_id, node_id, job["id"], STATUS_PENDING),
            )
            if cursor.rowcount == 0:
                return None  # Another worker claimed it first
        self.log.debug("Worker %d claimed job %d: %s" % (worker_id, job["id"], job["path"]))
        return job

    def get_pending_jobs(self):
        """Get all pending jobs ordered by creation time."""
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM jobs
                WHERE status = ?
                ORDER BY created_at ASC
            """,
                (STATUS_PENDING,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_next_pending_job(self):
        """Get the next pending job (FIFO)."""
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM jobs
                WHERE status = ?
                ORDER BY created_at ASC
                LIMIT 1
            """,
                (STATUS_PENDING,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def start_job(self, job_id, worker_id):
        """Mark a job as running."""
        with self._cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET status = ?, worker_id = ?, started_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (STATUS_RUNNING, worker_id, job_id),
            )
        self.log.debug("Job %d started by worker %d" % (job_id, worker_id))

    def complete_job(self, job_id):
        """Mark a job as completed."""
        with self._cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET status = ?, completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (STATUS_COMPLETED, job_id),
            )
        self.log.debug("Job %d completed" % job_id)

    def fail_job(self, job_id, error=None):
        """Mark a job as failed, or requeue with exponential backoff if retries remain."""
        with self._cursor() as cursor:
            cursor.execute("SELECT retry_count, max_retries FROM jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            if row and row["retry_count"] < row["max_retries"]:
                retry_count = row["retry_count"] + 1
                delay = 2**retry_count * 60  # 2m, 4m, 8m, 16m, ...
                cursor.execute(
                    """
                    UPDATE jobs
                    SET status = ?, retry_count = ?, error = ?,
                        next_attempt_at = datetime('now', '+' || ? || ' seconds'),
                        started_at = NULL, completed_at = NULL, worker_id = NULL, node_id = NULL
                    WHERE id = ?
                """,
                    (STATUS_PENDING, retry_count, error, delay, job_id),
                )
                self.log.debug("Job %d failed (attempt %d/%d), retrying in %ds" % (job_id, retry_count, row["max_retries"], delay))
            else:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET status = ?, error = ?, completed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """,
                    (STATUS_FAILED, error, job_id),
                )
                self.log.debug("Job %d failed: %s" % (job_id, error))

    def get_job(self, job_id):
        """Get a specific job by ID."""
        with self._cursor() as cursor:
            cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_jobs(self, status=None, config=None, limit=100, offset=0):
        """Get jobs with optional filtering."""
        query = "SELECT * FROM jobs WHERE 1=1"
        params = []

        if status:
            query += " AND status = ?"
            params.append(status)
        if config:
            query += " AND config = ?"
            params.append(config)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._cursor() as cursor:
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_stats(self):
        """Get job statistics."""
        with self._cursor() as cursor:
            cursor.execute("""
                SELECT status, COUNT(*) as count
                FROM jobs
                GROUP BY status
            """)
            stats = {row["status"]: row["count"] for row in cursor.fetchall()}

            cursor.execute("SELECT COUNT(*) as total FROM jobs")
            stats["total"] = cursor.fetchone()["total"]

            return stats

    def get_running_jobs(self):
        """Get all currently running jobs."""
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM jobs
                WHERE status = ?
            """,
                (STATUS_RUNNING,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def cleanup_old_jobs(self, days=30):
        """Remove completed/failed jobs older than specified days."""
        with self._cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM jobs
                WHERE status IN (?, ?)
                AND completed_at < datetime('now', '-' || ? || ' days')
            """,
                (STATUS_COMPLETED, STATUS_FAILED, days),
            )
            deleted = cursor.rowcount
        if deleted > 0:
            self.log.info("Cleaned up %d old jobs" % deleted)
        return deleted

    def pending_count(self):
        """Get count of pending jobs."""
        with self._cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as count FROM jobs WHERE status = ?", (STATUS_PENDING,))
            return cursor.fetchone()["count"]

    def pending_count_for_config(self, config):
        """Get count of pending jobs for a specific config."""
        with self._cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as count FROM jobs WHERE status = ? AND config = ?", (STATUS_PENDING, config))
            return cursor.fetchone()["count"]

    def requeue_job(self, job_id):
        """Reset a failed job back to pending. Returns True if the job was requeued."""
        with self._cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET status = ?, worker_id = NULL, node_id = NULL,
                    error = NULL, started_at = NULL, completed_at = NULL
                WHERE id = ? AND status = ?
            """,
                (STATUS_PENDING, job_id, STATUS_FAILED),
            )
            requeued = cursor.rowcount > 0
        if requeued:
            self.log.info("Requeued failed job %d" % job_id)
        return requeued

    def requeue_failed_jobs(self, config=None):
        """Reset all failed jobs (optionally filtered by config) back to pending."""
        sql = """
            UPDATE jobs
            SET status = ?, worker_id = NULL, node_id = NULL,
                error = NULL, started_at = NULL, completed_at = NULL
            WHERE status = ?
        """
        params = [STATUS_PENDING, STATUS_FAILED]
        if config:
            sql += " AND config = ?"
            params.append(config)
        with self._cursor() as cursor:
            cursor.execute(sql, params)
            count = cursor.rowcount
        if count > 0:
            self.log.info("Requeued %d failed jobs" % count)
        return count

    def cancel_job(self, job_id):
        """Cancel a pending or running job. Returns True if the job was updated."""
        with self._cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET status = 'cancelled', error = 'Cancelled by user',
                    completed_at = datetime('now')
                WHERE id = ? AND status IN (?, ?)
            """,
                (job_id, STATUS_PENDING, STATUS_RUNNING),
            )
            cancelled = cursor.rowcount > 0
        if cancelled:
            self.log.info("Cancelled job %d" % job_id)
        return cancelled

    def set_job_priority(self, job_id, priority):
        """Set the priority of a pending job. Returns True if the job was updated."""
        with self._cursor() as cursor:
            cursor.execute(
                "UPDATE jobs SET priority = ? WHERE id = ? AND status = ?",
                (priority, job_id, STATUS_PENDING),
            )
            updated = cursor.rowcount > 0
        if updated:
            self.log.info("Set priority %d for job %d" % (priority, job_id))
        return updated

    def filter_unscanned(self, paths):
        """Return the subset of paths not yet recorded in scanned_files."""
        if not paths:
            return []
        with self._cursor() as cursor:
            placeholders = ",".join("?" * len(paths))
            cursor.execute(
                "SELECT path FROM scanned_files WHERE path IN (%s)" % placeholders,
                paths,
            )
            already = {row["path"] for row in cursor.fetchall()}
        return [p for p in paths if p not in already]

    def record_scanned(self, paths):
        """Record paths as scanned. Ignores duplicates."""
        if not paths:
            return
        with self._cursor() as cursor:
            cursor.executemany(
                "INSERT OR IGNORE INTO scanned_files (path) VALUES (?)",
                [(p,) for p in paths],
            )


class PostgreSQLJobDatabase(BaseJobDatabase):
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
        self._node_id = socket.gethostname()
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
                        running_jobs INTEGER NOT NULL DEFAULT 0,
                        pending_jobs INTEGER NOT NULL DEFAULT 0
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
        self.log.info("PostgreSQL database initialized: %s" % self.db_url)
        self._reset_running_jobs()

    def _reset_running_jobs(self):
        """Reset only this node's interrupted running jobs back to pending on startup."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = %s, worker_id = NULL, node_id = NULL, started_at = NULL
                    WHERE status = %s AND node_id = %s
                """,
                    (STATUS_PENDING, STATUS_RUNNING, self._node_id),
                )
                count = cur.rowcount
        if count > 0:
            self.log.info("Reset %d interrupted jobs to pending (node: %s)" % (count, self._node_id))

    def add_job(self, path, config, args=None, max_retries=0):
        """Add a job to the queue. Returns job ID, or None if a duplicate is already pending/running."""
        args_json = json.dumps(args or [])
        with self._conn() as conn:
            with conn.cursor() as cur:
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

        This is the key distributed-safe operation: the SELECT and UPDATE happen in
        a single transaction. Any other node/worker that tries to claim the same row
        will skip it instantly due to SKIP LOCKED, preventing duplicate processing.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
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

    def complete_job(self, job_id):
        """Mark a job as completed."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE jobs SET status = %s, completed_at = NOW() WHERE id = %s", (STATUS_COMPLETED, job_id))
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

    def get_jobs(self, status=None, config=None, limit=100, offset=0):
        """Get jobs with optional filtering."""
        query = "SELECT * FROM jobs WHERE 1=1"
        params = []
        if status:
            query += " AND status = %s"
            params.append(status)
        if config:
            query += " AND config = %s"
            params.append(config)
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

    def heartbeat(self, node_id, host, workers, started_at):
        """Upsert this node's heartbeat row in cluster_nodes.

        started_at is set on INSERT and never overwritten on UPDATE, so it
        always reflects when this daemon process started. A change in
        started_at between heartbeats indicates the node was restarted.

        Returns any pending_command that was set for this node (and clears it),
        or None if there is no pending command.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cluster_nodes (node_id, host, workers, last_seen, started_at, status, running_jobs, pending_jobs)
                    VALUES (
                        %s, %s, %s, NOW(), %s, 'online',
                        (SELECT COUNT(*) FROM jobs WHERE status = 'running' AND node_id = %s),
                        (SELECT COUNT(*) FROM jobs WHERE status = 'pending')
                    )
                    ON CONFLICT (node_id) DO UPDATE SET
                        host            = EXCLUDED.host,
                        workers         = EXCLUDED.workers,
                        last_seen       = NOW(),
                        started_at      = EXCLUDED.started_at,
                        status          = 'online',
                        running_jobs    = EXCLUDED.running_jobs,
                        pending_jobs    = EXCLUDED.pending_jobs
                    RETURNING pending_command
                """,
                    (node_id, host, workers, started_at, node_id),
                )
                row = cur.fetchone()
                command = row["pending_command"] if row else None
                if command:
                    cur.execute(
                        "UPDATE cluster_nodes SET pending_command = NULL WHERE node_id = %s",
                        (node_id,),
                    )
                return command

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
                    cur.execute(
                        """
                        UPDATE jobs
                        SET status = %s, worker_id = NULL, node_id = NULL, started_at = NULL
                        WHERE status = %s AND node_id = %s
                    """,
                        (STATUS_PENDING, STATUS_RUNNING, stale_node_id),
                    )
                    job_count = cur.rowcount
                    cur.execute(
                        """
                        UPDATE cluster_nodes SET status = 'offline' WHERE node_id = %s
                    """,
                        (stale_node_id,),
                    )
                    recovered.append((stale_node_id, job_count))

        for stale_node_id, job_count in recovered:
            self.log.warning("Node %s declared stale — requeued %d running jobs" % (stale_node_id, job_count))
        return recovered

    def mark_node_offline(self, node_id):
        """Mark this node as offline and requeue any jobs it was running."""
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
                requeued = cur.rowcount
                cur.execute("UPDATE cluster_nodes SET status = 'offline' WHERE node_id = %s", (node_id,))
        if requeued:
            self.log.info("Requeued %d running jobs on shutdown" % requeued)

    def send_node_command(self, node_id, command):
        """Set pending_command on one or all online nodes.

        node_id may be a specific node ID string, or None to broadcast to all
        online nodes. command should be 'restart' or 'shutdown'.
        Returns the list of node_ids that were targeted.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                if node_id:
                    cur.execute(
                        "UPDATE cluster_nodes SET pending_command = %s WHERE node_id = %s RETURNING node_id",
                        (command, node_id),
                    )
                else:
                    cur.execute(
                        "UPDATE cluster_nodes SET pending_command = %s WHERE status = 'online' RETURNING node_id",
                        (command,),
                    )
                return [r["node_id"] for r in cur.fetchall()]

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
