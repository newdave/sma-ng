#!/usr/bin/env python3
"""
SMA-NG Daemon - HTTP webhook server for triggering media conversions.

Listens for HTTP POST requests containing absolute file/directory paths
and spawns conversion processes using manual.py.

Features:
- Path-based configuration selection via config/daemon.json
- Per-config logging to separate files in logs/ directory
- Only one process per config runs at a time (others queue)
- SQLite persistence for job queue (survives restarts)
- API key authentication for webhook endpoints

Usage:
    python daemon.py                    # Uses default settings
    python daemon.py --port 8585        # Override port
    python daemon.py --host 0.0.0.0     # Listen on all interfaces
    python daemon.py --api-key SECRET   # Require API key for requests
"""

import argparse
import configparser
import json
import logging
import os
import re as _re
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from logging.handlers import RotatingFileHandler
from urllib.parse import parse_qs, urlparse

from resources.log import getLogger

# Main daemon logger
log = getLogger("DAEMON")

# Default paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DAEMON_CONFIG = os.path.join(SCRIPT_DIR, "config", "daemon.json")
DEFAULT_PROCESS_CONFIG = os.path.join(SCRIPT_DIR, "config", "autoProcess.ini")
LOGS_DIR = os.path.join(SCRIPT_DIR, "logs")
DATABASE_PATH = os.path.join(SCRIPT_DIR, "config", "daemon.db")

# Job statuses
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


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
        self._init_db()

    def _get_connection(self):
        """Get thread-local database connection."""
        if not hasattr(self._local, "connection") or self._local.connection is None:
            self._local.connection = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
            self._local.connection.row_factory = sqlite3.Row
        return self._local.connection

    def close(self):
        """Close the thread-local database connection."""
        if hasattr(self._local, "connection") and self._local.connection is not None:
            self._local.connection.close()
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
                        host         = EXCLUDED.host,
                        workers      = EXCLUDED.workers,
                        last_seen    = NOW(),
                        started_at   = EXCLUDED.started_at,
                        status       = 'online',
                        running_jobs = EXCLUDED.running_jobs,
                        pending_jobs = EXCLUDED.pending_jobs
                """,
                    (node_id, host, workers, started_at, node_id),
                )

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


class ConfigLockManager:
    """
    Manages per-config concurrency using semaphores.

    Up to `max_per_config` jobs for the same config can run simultaneously.
    Jobs for different configs can always run in parallel (up to worker count).

    Locking strategy:
    - `_master_lock` protects `_config_sems` and `_active_configs` dict mutations.
    - Semaphore acquisition happens *outside* `_master_lock` to avoid deadlock;
      the waiting count is therefore advisory (used only for logging).
    - `_active_configs[config_path]` is a dict keyed by job_id for O(1) insert/remove.
    """

    def __init__(self, max_per_config=1, logger=None):
        self.log = logger or log
        self.max_per_config = max_per_config
        self._master_lock = threading.Lock()
        self._config_sems = {}  # config_path -> Semaphore
        self._active_configs = {}  # config_path -> {job_id: job_path}
        self._waiting_counts = {}  # config_path -> advisory waiting count (for logging)

    def _get_sem(self, config_path):
        """Get or create a semaphore for a config (thread-safe)."""
        with self._master_lock:
            if config_path not in self._config_sems:
                self._config_sems[config_path] = threading.Semaphore(self.max_per_config)
                self._waiting_counts[config_path] = 0
                self._active_configs[config_path] = {}
            return self._config_sems[config_path]

    def acquire(self, config_path, job_id, job_path):
        """
        Acquire a slot for a config. Blocks until a slot is available.
        Returns True when acquired.
        """
        sem = self._get_sem(config_path)

        with self._master_lock:
            self._waiting_counts[config_path] = self._waiting_counts.get(config_path, 0) + 1
            active = self._active_configs.get(config_path, {})
            if active:
                self.log.info("Job %d waiting for config slot: %s (%d/%d slots in use)" % (job_id, os.path.basename(config_path), len(active), self.max_per_config))

        sem.acquire()

        with self._master_lock:
            self._waiting_counts[config_path] -= 1
            self._active_configs.setdefault(config_path, {})[job_id] = job_path

        self.log.debug("Job %d acquired slot for config: %s" % (job_id, os.path.basename(config_path)))
        return True

    def release(self, config_path, job_id):
        """Release a slot for a config."""
        sem = self._get_sem(config_path)

        with self._master_lock:
            self._active_configs.get(config_path, {}).pop(job_id, None)

        sem.release()
        self.log.debug("Job %d released slot for config: %s" % (job_id, os.path.basename(config_path)))

    def get_status(self):
        """Get current lock status for all configs."""
        with self._master_lock:
            active = {}
            for config, jobs in self._active_configs.items():
                if jobs:
                    active[config] = [{"job_id": jid, "path": p} for jid, p in jobs.items()]
            return {"active": active, "waiting": {k: v for k, v in self._waiting_counts.items() if v > 0}}

    def is_locked(self, config_path):
        """Check if a config has any active jobs."""
        with self._master_lock:
            return bool(self._active_configs.get(config_path))

    def get_locked_configs(self):
        """Return config paths where all concurrency slots are full."""
        with self._master_lock:
            return {c for c, jobs in self._active_configs.items() if len(jobs) >= self.max_per_config}

    def get_active_jobs(self, config_path):
        """Get active jobs for a config as a list of dicts."""
        with self._master_lock:
            return [{"job_id": jid, "path": p} for jid, p in self._active_configs.get(config_path, {}).items()]


class ConfigLogManager:
    """Manages separate log files for each configuration."""

    def __init__(self, logs_dir=LOGS_DIR):
        self.logs_dir = logs_dir
        self.loggers = {}
        self.lock = threading.Lock()

        # Ensure logs directory exists
        if not os.path.isdir(self.logs_dir):
            os.makedirs(self.logs_dir)

    def _config_to_logname(self, config_path):
        """Convert config path to log filename."""
        basename = os.path.basename(config_path)
        name, _ = os.path.splitext(basename)
        return name

    def get_logger(self, config_path):
        """Get or create a logger for a specific config file."""
        with self.lock:
            if config_path in self.loggers:
                return self.loggers[config_path]

            log_name = self._config_to_logname(config_path)
            log_file = os.path.join(self.logs_dir, f"{log_name}.log")

            logger = logging.getLogger(f"sma.{log_name}")
            logger.setLevel(logging.DEBUG)

            if not logger.handlers:
                file_handler = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
                file_handler.setLevel(logging.DEBUG)
                formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)

            self.loggers[config_path] = logger
            return logger

    def get_log_file(self, config_path):
        """Get the log file path for a config."""
        log_name = self._config_to_logname(config_path)
        return os.path.join(self.logs_dir, f"{log_name}.log")


class PathConfigManager:
    """Manages path-to-config mappings for different media directories."""

    def __init__(self, config_file=None, logger=None):
        self.log = logger or log
        self.path_configs = []
        self.path_rewrites = []  # Can be set from daemon.json
        self.default_config = DEFAULT_PROCESS_CONFIG
        self.default_args = []  # Top-level default args for the default config
        self.api_key = None  # Can be set from daemon.json
        self.db_url = None  # Can be set from daemon.json
        self.ffmpeg_dir = None  # Can be set from daemon.json
        self.job_timeout_seconds = 0  # Can be set from daemon.json (0 = no timeout)
        self.media_extensions = frozenset([".mp4", ".mkv", ".avi", ".mov", ".ts"])
        self.scan_paths = []  # Can be set from daemon.json
        self._config_file = None  # Resolved path of loaded config file

        if config_file and os.path.exists(config_file):
            self._config_file = config_file
            self.load_config(config_file)
        elif os.path.exists(DEFAULT_DAEMON_CONFIG):
            self._config_file = DEFAULT_DAEMON_CONFIG
            self.load_config(self._config_file)
        else:
            self.log.info("No daemon config found, using default autoProcess.ini for all paths")

    def load_config(self, config_file):
        """Load path mappings from daemon.json config file."""
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)

            self.default_config = config.get("default_config", DEFAULT_PROCESS_CONFIG)
            if not os.path.isabs(self.default_config):
                self.default_config = os.path.join(SCRIPT_DIR, self.default_config)

            # Load API key from config (can be overridden by CLI/env)
            self.api_key = config.get("api_key")

            # Load PostgreSQL URL from config (can be overridden by CLI/env)
            self.db_url = config.get("db_url")

            # Load FFmpeg directory from config (can be overridden by CLI/env)
            self.ffmpeg_dir = config.get("ffmpeg_dir")

            # Load job timeout in seconds (0 means no timeout)
            self.job_timeout_seconds = int(config.get("job_timeout_seconds", 0) or 0)

            # Load media extensions inclusion list for directory scanning
            raw_exts = config.get("media_extensions")
            if raw_exts is not None:
                self.media_extensions = frozenset(("." + e.lower().lstrip(".")) for e in raw_exts if e)

            # Load top-level default args (applied when no path_config matches)
            raw_default_args = config.get("default_args", [])
            if isinstance(raw_default_args, str):
                raw_default_args = raw_default_args.split()
            self.default_args = raw_default_args

            # Load webhook path rewrites (prefix substitutions applied to incoming webhook paths)
            raw_rewrites = config.get("path_rewrites", [])
            self.path_rewrites = [{"from": r["from"].rstrip("/"), "to": r["to"].rstrip("/")} for r in raw_rewrites if r.get("from") and r.get("to")]
            if self.path_rewrites:
                self.log.info("Path rewrites (%d):" % len(self.path_rewrites))
                for r in self.path_rewrites:
                    self.log.info("  %s -> %s" % (r["from"], r["to"]))

            # Load scheduled scan paths
            self.scan_paths = config.get("scan_paths", [])

            raw_configs = config.get("path_configs", [])

            for entry in raw_configs:
                path = entry.get("path", "").rstrip("/")
                config_path = entry.get("config", "")

                if not path or not config_path:
                    continue

                if not os.path.isabs(config_path):
                    config_path = os.path.join(SCRIPT_DIR, config_path)

                default_args = entry.get("default_args", [])
                if isinstance(default_args, str):
                    default_args = default_args.split()

                self.path_configs.append({"path": os.path.normpath(path), "config": config_path, "default_args": default_args})

            # Sort by path length descending (longest prefix match first)
            self.path_configs.sort(key=lambda x: len(x["path"]), reverse=True)

            self.log.info("Loaded daemon config from %s" % config_file)
            self.log.info("Default config: %s" % self.default_config)
            self.log.info("Path mappings (%d):" % len(self.path_configs))
            for entry in self.path_configs:
                self.log.info("  %s -> %s" % (entry["path"], entry["config"]))

        except Exception as e:
            self.log.exception("Error loading daemon config: %s" % e)

    def get_config_for_path(self, file_path):
        """Get the appropriate config file for a given file path."""
        file_path = os.path.abspath(file_path)

        for entry in self.path_configs:
            if file_path.startswith(entry["path"] + "/") or file_path == entry["path"]:
                config_path = entry["config"]
                if os.path.exists(config_path):
                    self.log.debug("Path %s matched %s -> %s" % (file_path, entry["path"], config_path))
                    return config_path
                else:
                    self.log.warning("Config file not found: %s, using default" % config_path)

        self.log.debug("Path %s using default config: %s" % (file_path, self.default_config))
        return self.default_config

    def get_args_for_path(self, file_path):
        """Get the default args list for a given file path based on path_configs."""
        file_path = os.path.abspath(file_path)

        for entry in self.path_configs:
            if file_path.startswith(entry["path"] + "/") or file_path == entry["path"]:
                if os.path.exists(entry["config"]):
                    return list(entry.get("default_args", []))

        return list(self.default_args)

    def rewrite_path(self, path):
        """Apply the first matching path_rewrites prefix substitution, or return path unchanged."""
        for r in self.path_rewrites:
            prefix = r["from"]
            if path == prefix or path.startswith(prefix + "/"):
                return r["to"] + path[len(prefix) :]
        return path

    def get_all_configs(self):
        """Return list of all unique config files."""
        configs = {self.default_config}
        for entry in self.path_configs:
            configs.add(entry["config"])
        return list(configs)

    def get_recycle_bin(self, config_path):
        """Return the recycle-bin path from an autoProcess.ini, or None."""
        try:
            cp = configparser.ConfigParser()
            cp.read(config_path)
            val = cp.get("Converter", "recycle-bin", fallback="").strip()
            return os.path.abspath(val) if val else None
        except Exception:
            return None

    def is_recycle_bin_path(self, path):
        """Return True if path is inside any configured recycle-bin directory."""
        path = os.path.normpath(os.path.abspath(path))
        for config_path in self.get_all_configs():
            recycle_bin = self.get_recycle_bin(config_path)
            if recycle_bin and (path == recycle_bin or path.startswith(recycle_bin + os.sep)):
                return True
        return False


class ConversionWorker(threading.Thread):
    """Background worker thread that processes conversion jobs from the database."""

    def __init__(self, worker_id, job_db, path_config_manager, config_log_manager, config_lock_manager, logger, ffmpeg_dir=None, job_timeout_seconds=0, job_processes=None, job_progress=None):
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self.node_id = socket.gethostname()
        self.job_db = job_db
        self.job_event = threading.Event()  # per-worker event; set by notify_workers()
        self.path_config_manager = path_config_manager
        self.config_log_manager = config_log_manager
        self.config_lock_manager = config_lock_manager
        self.log = logger
        self.script_path = os.path.join(SCRIPT_DIR, "manual.py")
        self.ffmpeg_dir = ffmpeg_dir
        self.job_timeout_seconds = job_timeout_seconds  # 0 means no timeout
        self.running = True
        self.current_job_id = None
        self._job_processes = job_processes if job_processes is not None else {}
        self._job_progress = job_progress if job_progress is not None else {}

    def stop(self):
        """Signal worker to stop."""
        self.running = False
        self.job_event.set()

    def run(self):
        while self.running:
            # Wait for a wakeup on this worker's own event or periodic timeout.
            self.job_event.wait(timeout=5.0)
            self.job_event.clear()

            if not self.running:
                break

            # Drain all available jobs before going back to sleep.
            while self.running:
                locked = self.config_lock_manager.get_locked_configs()
                job = self.job_db.claim_next_job(self.worker_id, self.node_id, exclude_configs=locked or None)
                if job:
                    self.process_job(job)
                else:
                    break

    def process_job(self, job):
        job_id = job["id"]
        self.current_job_id = job_id
        path = job["path"]
        args = json.loads(job["args"]) if job["args"] else []
        config_file = job["config"]

        if not os.path.exists(path):
            self.log.error("Job %d: Path does not exist: %s" % (job_id, path))
            self.job_db.fail_job(job_id, "Path does not exist")
            return

        # Job is already marked running by claim_next_job()

        # Check if job was cancelled before we even start (e.g. cancelled while pending)
        current = self.job_db.get_job(job_id)
        if current and current.get("status") == "cancelled":
            self.log.info("Job %d was cancelled before processing started" % job_id)
            self.current_job_id = None
            return

        # Acquire lock for this config (blocks if another job is using it)
        self.log.info("Worker %d acquiring lock for job %d: %s" % (self.worker_id, job_id, os.path.basename(config_file)))
        self.config_lock_manager.acquire(config_file, job_id, path)

        # Check again after acquiring lock (may have been cancelled while waiting)
        current = self.job_db.get_job(job_id)
        if current and current.get("status") == "cancelled":
            self.log.info("Job %d was cancelled while waiting for lock" % job_id)
            self.config_lock_manager.release(config_file, job_id)
            self.current_job_id = None
            return

        try:
            success = self._run_conversion(job_id, path, config_file, args)
            if success:
                self.job_db.complete_job(job_id)
            else:
                # Don't overwrite a cancelled status set during conversion
                current = self.job_db.get_job(job_id)
                if current and current.get("status") != "cancelled":
                    self.job_db.fail_job(job_id, "Conversion process failed")
        except Exception as e:
            self.log.exception("Job %d failed: %s" % (job_id, e))
            current = self.job_db.get_job(job_id)
            if current and current.get("status") != "cancelled":
                self.job_db.fail_job(job_id, str(e))
        finally:
            self.config_lock_manager.release(config_file, job_id)
            self.current_job_id = None

    def _run_conversion(self, job_id, path, config_file, extra_args):
        """Run the actual conversion process. Returns True on success."""
        config_logger = self.config_log_manager.get_logger(config_file)
        log_file = self.config_log_manager.get_log_file(config_file)

        self.log.info("Worker %d processing job %d: %s" % (self.worker_id, job_id, path))
        self.log.info("Using config: %s (log: %s)" % (config_file, log_file))

        config_logger.info("=" * 60)
        config_logger.info("Job %d started: %s" % (job_id, path))
        config_logger.info("Config: %s" % config_file)
        config_logger.info("Worker: %d" % self.worker_id)
        config_logger.info("Timestamp: %s" % datetime.now().isoformat())
        config_logger.info("=" * 60)

        cmd = [sys.executable, self.script_path, "-a", "-i", path, "-c", config_file] + extra_args

        env = os.environ.copy()
        if self.ffmpeg_dir:
            env["PATH"] = self.ffmpeg_dir + os.pathsep + env.get("PATH", "")

        _ffmpeg_time_re = _re.compile(r"time=(\d+:\d+:\d+)")

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            self._job_processes[job_id] = process

            for line in process.stdout:
                line = line.strip()
                if line:
                    config_logger.info(line)
                    self.log.info("[%s] %s" % (os.path.basename(config_file), line))
                    m = _ffmpeg_time_re.search(line)
                    if m:
                        self._job_progress[job_id] = m.group(1)

            try:
                timeout = self.job_timeout_seconds if self.job_timeout_seconds > 0 else None
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                msg = "Job %d timed out after %ds: %s" % (job_id, self.job_timeout_seconds, path)
                self.log.error(msg)
                config_logger.error(msg)
                return False

            if process.returncode == 0:
                msg = "Job %d completed successfully: %s" % (job_id, path)
                self.log.info(msg)
                config_logger.info(msg)
                return True
            else:
                msg = "Job %d exited with code %d: %s" % (job_id, process.returncode, path)
                self.log.error(msg)
                config_logger.error(msg)
                return False

        except Exception as e:
            msg = "Job %d failed: %s" % (job_id, e)
            self.log.exception(msg)
            config_logger.exception(msg)
            return False
        finally:
            self._job_processes.pop(job_id, None)
            self._job_progress.pop(job_id, None)
            config_logger.info("Job %d finished: %s" % (job_id, path))
            config_logger.info("")


DOCS_PATH = os.path.join(SCRIPT_DIR, "docs", "README.md")


def _render_markdown_to_html(md_text):
    """Minimal Markdown to HTML renderer for documentation display."""
    lines = md_text.split("\n")
    html_parts = []
    in_code = False
    in_table = False
    in_list = False
    list_type = None

    for line in lines:
        # Fenced code blocks
        if line.strip().startswith("```"):
            if in_code:
                html_parts.append("</code></pre>")
                in_code = False
            else:
                lang = line.strip()[3:].strip()
                html_parts.append('<pre class="bg-gray-800 rounded-lg p-4 overflow-x-auto my-4 border border-gray-700"><code class="text-sm text-green-300">')
                in_code = True
            continue
        if in_code:
            html_parts.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            continue

        # Close table if line doesn't look like table
        if in_table and not line.strip().startswith("|"):
            html_parts.append("</tbody></table></div>")
            in_table = False

        # Close list if line doesn't continue it
        if in_list and line.strip() and not _re.match(r"^(\s*[-*]\s|^\s*\d+\.\s)", line):
            html_parts.append("</%s>" % list_type)
            in_list = False

        stripped = line.strip()

        # Blank line
        if not stripped:
            if in_list:
                html_parts.append("</%s>" % list_type)
                in_list = False
            continue

        # Headings
        hm = _re.match(r"^(#{1,6})\s+(.*)", stripped)
        if hm:
            level = len(hm.group(1))
            text = _inline(hm.group(2))
            slug = _re.sub(r"[^\w-]", "", hm.group(2).lower().replace(" ", "-"))
            sizes = {1: "text-3xl", 2: "text-2xl", 3: "text-xl", 4: "text-lg", 5: "text-base", 6: "text-sm"}
            mt = "mt-10" if level <= 2 else "mt-6"
            html_parts.append('<h%d id="%s" class="%s %s font-bold text-white mb-3">%s</h%d>' % (level, slug, sizes.get(level, "text-base"), mt, text, level))
            continue

        # Horizontal rule
        if _re.match(r"^-{3,}$", stripped):
            html_parts.append('<hr class="border-gray-700 my-8">')
            continue

        # Table
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if all(_re.match(r"^[-:]+$", c) for c in cells):
                continue  # separator row
            if not in_table:
                in_table = True
                html_parts.append('<div class="overflow-x-auto my-4"><table class="w-full text-sm"><thead><tr class="border-b border-gray-700">')
                for c in cells:
                    html_parts.append('<th class="text-left py-2 px-3 text-gray-400">%s</th>' % _inline(c))
                html_parts.append('</tr></thead><tbody class="divide-y divide-gray-700/50">')
            else:
                html_parts.append('<tr class="hover:bg-gray-800/50">')
                for c in cells:
                    html_parts.append('<td class="py-2 px-3 text-gray-300">%s</td>' % _inline(c))
                html_parts.append("</tr>")
            continue

        # Unordered list
        lm = _re.match(r"^(\s*)[-*]\s+(.*)", line)
        if lm:
            if not in_list:
                in_list = True
                list_type = "ul"
                html_parts.append('<ul class="list-disc list-inside space-y-1 my-3 text-gray-300">')
            html_parts.append("<li>%s</li>" % _inline(lm.group(2)))
            continue

        # Ordered list
        lm = _re.match(r"^(\s*)\d+\.\s+(.*)", line)
        if lm:
            if not in_list:
                in_list = True
                list_type = "ol"
                html_parts.append('<ol class="list-decimal list-inside space-y-1 my-3 text-gray-300">')
            html_parts.append("<li>%s</li>" % _inline(lm.group(2)))
            continue

        # Paragraph
        html_parts.append('<p class="text-gray-300 my-2 leading-relaxed">%s</p>' % _inline(stripped))

    # Close open blocks
    if in_code:
        html_parts.append("</code></pre>")
    if in_table:
        html_parts.append("</tbody></table></div>")
    if in_list:
        html_parts.append("</%s>" % list_type)

    return "\n".join(html_parts)


def _inline(text):
    """Process inline Markdown formatting."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Bold
    text = _re.sub(r"\*\*(.+?)\*\*", r'<strong class="text-white">\1</strong>', text)
    # Italic
    text = _re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Inline code
    text = _re.sub(r"`([^`]+)`", r'<code class="bg-gray-800 text-blue-300 px-1.5 py-0.5 rounded text-xs">\1</code>', text)
    # Links
    text = _re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" class="text-blue-400 hover:underline">\1</a>', text)
    return text


DOCS_TEMPLATE_PATH = os.path.join(SCRIPT_DIR, "resources", "docs.html")
DASHBOARD_HTML_PATH = os.path.join(SCRIPT_DIR, "resources", "dashboard.html")


def _load_dashboard_html():
    with open(DASHBOARD_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _load_docs_template():
    with open(DOCS_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        return f.read()


class WebhookHandler(BaseHTTPRequestHandler):
    """HTTP request handler for webhook endpoints."""

    # Endpoints that don't require authentication
    PUBLIC_ENDPOINTS = ["/", "/dashboard", "/health", "/status", "/docs", "/favicon.png"]

    def log_message(self, format, *args):
        self.server.logger.debug("%s - %s" % (self.address_string(), format % args))

    def send_json_response(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode("utf-8"))

    def send_html_response(self, status_code, html):
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def wants_html(self):
        """Check if client prefers HTML (browser) over JSON (API)."""
        accept = self.headers.get("Accept", "")
        return "text/html" in accept and "application/json" not in accept

    def check_auth(self):
        """
        Check if request is authenticated.
        Returns True if authenticated or no API key is configured.
        Returns False and sends 401 response if authentication fails.
        """
        api_key = self.server.api_key
        if not api_key:
            # No API key configured, allow all requests
            return True

        # Check X-API-Key header
        request_key = self.headers.get("X-API-Key")

        # Also check Authorization header (Bearer token)
        if not request_key:
            auth_header = self.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                request_key = auth_header[7:]

        if request_key == api_key:
            return True

        # Authentication failed
        self.server.logger.warning("Unauthorized request from %s" % self.address_string())
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("WWW-Authenticate", "Bearer")
        self.end_headers()
        self.wfile.write(json.dumps({"error": "Unauthorized", "message": "Valid API key required"}).encode("utf-8"))
        return False

    def is_public_endpoint(self, path):
        """Check if the endpoint is public (doesn't require auth)."""
        return path in self.PUBLIC_ENDPOINTS

    def _read_json_paths(self):
        """Read a JSON body of the form {"paths": [...]} and return the list.

        Returns the paths list on success.  On parse failure, sends a 400
        response and returns None — callers must check for None and return.
        """
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            data = json.loads(body)
            paths = data.get("paths", [])
            if not isinstance(paths, list):
                raise ValueError("paths must be a list")
            return paths
        except (json.JSONDecodeError, ValueError) as e:
            self.send_json_response(400, {"error": str(e)})
            return None

    # ------------------------------------------------------------------
    # GET route handlers
    # ------------------------------------------------------------------

    def _get_health(self):
        lock_status = self.server.config_lock_manager.get_status()
        stats = self.server.job_db.get_stats()
        now = datetime.now(timezone.utc)
        uptime = int((now - self.server.started_at).total_seconds())
        self.send_json_response(
            200,
            {
                "status": "ok",
                "node": self.server.node_id,
                "started_at": self.server.started_at.isoformat(),
                "uptime_seconds": uptime,
                "workers": self.server.worker_count,
                "jobs": stats,
                "active": lock_status["active"],
                "waiting": lock_status["waiting"],
            },
        )

    def _get_status(self):
        # Cluster-wide status — only meaningful with PostgreSQL backend
        if self.server.job_db.is_distributed:
            # Run staleness check on every status request so the response
            # reflects current reality rather than waiting for the next
            # heartbeat cycle.
            recovered = self.server.job_db.recover_stale_nodes(self.server.stale_seconds)
            for stale_id, job_count in recovered:
                self.server.logger.warning("Status check: recovered %d jobs from stale node %s" % (job_count, stale_id))
            if any(job_count > 0 for _, job_count in recovered):
                self.server.notify_workers()
            nodes = self.server.job_db.get_cluster_nodes()
            stats = self.server.job_db.get_stats()
            self.send_json_response(200, {"cluster": nodes, "jobs": stats})
        else:
            # SQLite single-node — return local health with explanatory note
            lock_status = self.server.config_lock_manager.get_status()
            stats = self.server.job_db.get_stats()
            self.send_json_response(
                200,
                {
                    "status": "ok",
                    "node": self.server.node_id,
                    "note": "Cluster status requires PostgreSQL backend (set SMA_DAEMON_DB_URL)",
                    "workers": self.server.worker_count,
                    "jobs": stats,
                    "active": lock_status["active"],
                    "waiting": lock_status["waiting"],
                },
            )

    def _get_jobs(self, query):
        status = query.get("status", [None])[0]
        config = query.get("config", [None])[0]
        limit = int(query.get("limit", [100])[0])
        offset = int(query.get("offset", [0])[0])
        jobs = self.server.job_db.get_jobs(status=status, config=config, limit=limit, offset=offset)
        self.send_json_response(200, {"jobs": jobs, "count": len(jobs), "limit": limit, "offset": offset})

    def _get_job(self, path):
        try:
            job_id = int(path.split("/")[-1])
            job = self.server.job_db.get_job(job_id)
            if job:
                if job.get("status") == STATUS_RUNNING:
                    job["progress"] = self.server._job_progress.get(job_id)
                self.send_json_response(200, job)
            else:
                self.send_json_response(404, {"error": "Job not found"})
        except ValueError:
            self.send_json_response(400, {"error": "Invalid job ID"})

    def _get_browse(self, query):
        """List directories and media files under a path, constrained to configured roots."""
        path = query.get("path", [""])[0].strip()
        pcm = self.server.path_config_manager

        # Collect valid root prefixes: the configured path_config paths themselves,
        # plus every ancestor directory of each, so navigation down from "/" works.
        allowed_roots = set()
        for entry in pcm.path_configs:
            p = entry["path"]
            allowed_roots.add(p)
            # Add all parent directories so the user can navigate into the root
            parts = p.rstrip("/").split("/")
            for i in range(1, len(parts)):
                allowed_roots.add("/".join(parts[:i]) or "/")

        def is_allowed(check_path):
            check_path = os.path.normpath(check_path)
            for root in allowed_roots:
                root_norm = os.path.normpath(root)
                if check_path == root_norm or check_path.startswith(root_norm + os.sep):
                    return True
            return False

        if not path:
            # Return the top-level configured path prefixes as starting points
            dirs = sorted(set(os.path.normpath(e["path"]) for e in pcm.path_configs if os.path.isdir(e["path"])))
            return self.send_json_response(200, {"dirs": dirs, "files": []})

        path = os.path.normpath(path)

        if not is_allowed(path):
            return self.send_json_response(403, {"error": "Path is outside configured media roots"})

        if not os.path.isdir(path):
            return self.send_json_response(404, {"error": "Directory not found"})

        try:
            dirs, files = [], []
            with os.scandir(path) as it:
                for entry in sorted(it, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())):
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(os.path.join(path, entry.name))
                    elif entry.is_file(follow_symlinks=False):
                        ext = os.path.splitext(entry.name)[1].lower()
                        if ext in pcm.media_extensions:
                            files.append(os.path.join(path, entry.name))
            self.send_json_response(200, {"dirs": dirs, "files": files})
        except PermissionError:
            self.send_json_response(403, {"error": "Permission denied"})

    def _get_configs(self):
        configs_with_status = [
            {
                "path": entry["path"],
                "config": entry["config"],
                "default_args": entry.get("default_args", []),
                "log_file": self.server.config_log_manager.get_log_file(entry["config"]),
                "active_jobs": self.server.config_lock_manager.get_active_jobs(entry["config"]),
                "pending_jobs": self.server.job_db.pending_count_for_config(entry["config"]),
            }
            for entry in self.server.path_config_manager.path_configs
        ]
        pcm = self.server.path_config_manager
        default_config = pcm.default_config
        self.send_json_response(
            200,
            {
                "default_config": default_config,
                "default_args": pcm.default_args,
                "default_log": self.server.config_log_manager.get_log_file(default_config),
                "default_active_jobs": self.server.config_lock_manager.get_active_jobs(default_config),
                "default_pending_jobs": self.server.job_db.pending_count_for_config(default_config),
                "path_configs": configs_with_status,
                "logs_directory": self.server.config_log_manager.logs_dir,
            },
        )

    def _get_scan(self, query):
        # Filter a list of paths to those not yet recorded as scanned.
        # Usage: GET /scan?path=/a/b.mkv&path=/c/d.mkv
        # For large path lists use POST /scan/filter instead.
        paths = query.get("path", [])
        unscanned = self.server.job_db.filter_unscanned(paths)
        self.send_json_response(200, {"unscanned": unscanned, "total": len(paths), "already_scanned": len(paths) - len(unscanned)})

    def do_HEAD(self):
        """Respond to HEAD requests (used by browsers and health-check tools)."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def _get_root(self, _path, _query):
        self.send_response(301)
        self.send_header("Location", "/dashboard")
        self.end_headers()

    def _get_dashboard(self, _path, _query):
        api_key = self.server.api_key or ""
        key_script = "<script>window.SMA_API_KEY=%s;</script>" % json.dumps(api_key)
        self.send_html_response(200, _load_dashboard_html().replace("</head>", key_script + "</head>", 1))

    def _get_docs(self, _path, _query):
        try:
            with open(DOCS_PATH, "r", encoding="utf-8") as f:
                md_content = f.read()
            self.send_html_response(200, _load_docs_template() % _render_markdown_to_html(md_content))
        except FileNotFoundError:
            self.send_html_response(404, "<h1>Documentation not found</h1><p>docs/README.md missing</p>")

    def _get_stats(self, _path, _query):
        self.send_json_response(200, self.server.job_db.get_stats())

    def _get_favicon(self, _path, _query):
        favicon = os.path.join(SCRIPT_DIR, "logo.png")
        try:
            with open(favicon, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_json_response(404, {"error": "favicon not found"})

    _GET_ROUTES = {
        "/": "_get_root",
        "/dashboard": "_get_dashboard",
        "/docs": "_get_docs",
        "/health": lambda self, p, q: self._get_health(),
        "/status": lambda self, p, q: self._get_status(),
        "/jobs": lambda self, p, q: self._get_jobs(q),
        "/configs": lambda self, p, q: self._get_configs(),
        "/stats": "_get_stats",
        "/scan": lambda self, p, q: self._get_scan(q),
        "/browse": lambda self, p, q: self._get_browse(q),
        "/favicon.png": "_get_favicon",
    }

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        # Check authentication for non-public endpoints
        if not self.is_public_endpoint(parsed.path) and not self.check_auth():
            return

        handler = self._GET_ROUTES.get(parsed.path)
        if handler is not None:
            if isinstance(handler, str):
                getattr(self, handler)(parsed.path, query)
            else:
                handler(self, parsed.path, query)
        elif parsed.path.startswith("/jobs/"):
            self._get_job(parsed.path)
        else:
            self.send_json_response(404, {"error": "Not found"})

    # ------------------------------------------------------------------
    # POST route handlers
    # ------------------------------------------------------------------

    def _post_cleanup(self, query):
        days = int(query.get("days", [30])[0])
        deleted = self.server.job_db.cleanup_old_jobs(days)
        self.send_json_response(200, {"deleted": deleted, "days": days})

    def _post_jobs_requeue_bulk(self, query):
        config = query.get("config", [None])[0]
        count = self.server.job_db.requeue_failed_jobs(config=config)
        if count > 0:
            self.server.notify_workers()
        self.send_json_response(200, {"requeued": count})

    def _post_job_requeue(self, path):
        try:
            job_id = int(path.split("/")[-2])
            requeued = self.server.job_db.requeue_job(job_id)
            if requeued:
                self.server.notify_workers()
                self.send_json_response(200, {"requeued": True, "job_id": job_id})
            else:
                job = self.server.job_db.get_job(job_id)
                if job is None:
                    self.send_json_response(404, {"error": "Job not found"})
                else:
                    self.send_json_response(409, {"error": "Job cannot be requeued", "status": job["status"], "note": "Only failed jobs can be requeued"})
        except ValueError:
            self.send_json_response(400, {"error": "Invalid job ID"})

    def _post_job_cancel(self, path):
        try:
            job_id = int(path.split("/")[-2])
            cancelled = self.server.cancel_job(job_id)
            if cancelled:
                self.send_json_response(200, {"cancelled": True, "job_id": job_id})
            else:
                job = self.server.job_db.get_job(job_id)
                if job is None:
                    self.send_json_response(404, {"error": "Job not found"})
                else:
                    self.send_json_response(409, {"error": "Job cannot be cancelled", "status": job["status"], "note": "Only pending or running jobs can be cancelled"})
        except ValueError:
            self.send_json_response(400, {"error": "Invalid job ID"})

    def _post_job_priority(self, path):
        try:
            job_id = int(path.split("/")[-2])
        except ValueError:
            self.send_json_response(400, {"error": "Invalid job ID"})
            return
        content_length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(content_length) if content_length else b"{}")
        except (json.JSONDecodeError, ValueError):
            self.send_json_response(400, {"error": "Invalid JSON body"})
            return
        if "priority" not in body:
            self.send_json_response(400, {"error": "Missing 'priority' field"})
            return
        try:
            priority = int(body["priority"])
        except (TypeError, ValueError):
            self.send_json_response(400, {"error": "'priority' must be an integer"})
            return
        updated = self.server.job_db.set_job_priority(job_id, priority)
        if updated:
            self.send_json_response(200, {"job_id": job_id, "priority": priority})
        else:
            job = self.server.job_db.get_job(job_id)
            if job is None:
                self.send_json_response(404, {"error": "Job not found"})
            else:
                self.send_json_response(409, {"error": "Priority can only be set on pending jobs", "status": job["status"]})

    def _post_scan_filter(self):
        paths = self._read_json_paths()
        if paths is None:
            return
        unscanned = self.server.job_db.filter_unscanned(paths)
        self.send_json_response(200, {"unscanned": unscanned, "total": len(paths), "already_scanned": len(paths) - len(unscanned)})

    def _post_scan_record(self):
        paths = self._read_json_paths()
        if paths is None:
            return
        self.server.job_db.record_scanned(paths)
        self.send_json_response(200, {"recorded": len(paths)})

    def _post_shutdown(self, _path, _query):
        active = self.server.config_lock_manager.get_status()["active"]
        count = sum(len(v) for v in active.values())
        self.send_json_response(202, {"status": "shutting_down", "active_jobs": count})
        self.wfile.flush()
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _post_restart(self, _path, _query):
        active = self.server.config_lock_manager.get_status()["active"]
        count = sum(len(v) for v in active.values())
        self.send_json_response(202, {"status": "restarting", "active_jobs": count})
        self.wfile.flush()
        threading.Thread(target=self.server.graceful_restart, daemon=True).start()

    def _post_reload(self, _path, _query):
        threading.Thread(target=self.server.reload_config, daemon=True).start()
        self.send_json_response(200, {"status": "reloading"})

    _POST_ROUTES = {
        "/": lambda self, p, q: self._handle_webhook(),
        "/webhook": lambda self, p, q: self._handle_webhook(),
        "/convert": lambda self, p, q: self._handle_webhook(),
        "/shutdown": "_post_shutdown",
        "/restart": "_post_restart",
        "/reload": "_post_reload",
        "/cleanup": lambda self, p, q: self._post_cleanup(q),
        "/jobs/requeue": lambda self, p, q: self._post_jobs_requeue_bulk(q),
        "/scan/filter": lambda self, p, q: self._post_scan_filter(),
        "/scan/record": lambda self, p, q: self._post_scan_record(),
    }

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        # All POST endpoints require authentication
        if not self.check_auth():
            return

        handler = self._POST_ROUTES.get(parsed.path)
        if handler is not None:
            if isinstance(handler, str):
                getattr(self, handler)(parsed.path, query)
            else:
                handler(self, parsed.path, query)
        elif parsed.path.startswith("/jobs/") and parsed.path.endswith("/requeue"):
            self._post_job_requeue(parsed.path)
        elif parsed.path.startswith("/jobs/") and parsed.path.endswith("/cancel"):
            self._post_job_cancel(parsed.path)
        elif parsed.path.startswith("/jobs/") and parsed.path.endswith("/priority"):
            self._post_job_priority(parsed.path)
        else:
            self.send_json_response(404, {"error": "Not found"})

    def _collect_media_files(self, directory):
        """Recursively collect media files from a directory.

        Only files whose extension is in path_config_manager.media_extensions
        are included. Hidden directories and dotfiles are skipped.
        ffprobe validation happens later inside manual.py when each job runs.
        """
        allowed = self.server.path_config_manager.media_extensions
        candidates = []
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if fname.startswith("."):
                    continue
                if os.path.splitext(fname)[1].lower() in allowed:
                    candidates.append(os.path.join(root, fname))
        return sorted(candidates)

    def _parse_webhook_body(self):
        """Parse request body into (path, extra_args, config_override, max_retries).

        Returns (None, [], None, 0) with an error response already sent on failure.
        """
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_json_response(400, {"error": "Empty request body"})
            return None, [], None, 0

        body = self.rfile.read(content_length).decode("utf-8").strip()
        path = None
        extra_args = []
        config_override = None
        max_retries = 0

        if "application/json" in self.headers.get("Content-Type", ""):
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    path = data.get("path") or data.get("file") or data.get("input")
                    extra_args = data.get("args", [])
                    config_override = data.get("config")
                    max_retries = int(data.get("max_retries", 0))
                    if isinstance(extra_args, str):
                        extra_args = extra_args.split()
                elif isinstance(data, str):
                    path = data
            except (json.JSONDecodeError, ValueError, TypeError):
                path = body
        else:
            path = body

        if not path:
            self.send_json_response(400, {"error": "No path provided"})
            return None, [], None, 0

        return path, extra_args, config_override, max_retries

    def _resolve_config(self, path, config_override):
        """Return the config file to use for path, respecting any override."""
        if config_override and os.path.exists(config_override):
            return config_override
        return self.server.path_config_manager.get_config_for_path(path)

    def _merge_args(self, path, extra_args):
        """Merge per-path default_args with request args.

        Default args are prepended; request args are appended. If a flag
        appears in both, the request arg takes precedence (default is dropped).
        This also handles the --tv/--movie mutual exclusivity.
        """
        default_args = self.server.path_config_manager.get_args_for_path(path)
        if not default_args:
            return list(extra_args)

        # Flags the caller explicitly provided — strip leading dashes for comparison
        caller_flags = {a.lstrip("-") for a in extra_args if a.startswith("-")}

        # Filter out any default flags already covered by the caller
        filtered_defaults = [a for a in default_args if not a.startswith("-") or a.lstrip("-") not in caller_flags]

        return filtered_defaults + list(extra_args)

    def _queue_directory(self, path, extra_args, config_override, max_retries=0):
        """Expand directory to media files, queue each, respond."""
        files = self._collect_media_files(path)
        if not files:
            self.send_json_response(200, {"status": "empty", "path": path, "message": "No media files found in directory"})
            return

        queued, duplicates = [], []
        for filepath in files:
            resolved_config = self._resolve_config(filepath, config_override)
            job_id = self.server.job_db.add_job(filepath, resolved_config, self._merge_args(filepath, extra_args), max_retries=max_retries)
            if job_id is None:
                existing = self.server.job_db.find_active_job(filepath)
                duplicates.append({"path": filepath, "job_id": existing["id"] if existing else None})
            else:
                queued.append({"job_id": job_id, "path": filepath, "config": resolved_config})

        if queued:
            self.server.notify_workers()
            self.server.logger.info("Directory %s: queued %d files, %d duplicates" % (path, len(queued), len(duplicates)))

        self.send_json_response(202, {"status": "queued", "directory": path, "queued": queued, "duplicates": duplicates, "queued_count": len(queued), "duplicate_count": len(duplicates)})

    def _queue_file(self, path, extra_args, config_override, max_retries=0):
        """Queue a single file job and respond."""
        resolved_config = self._resolve_config(path, config_override)
        job_id = self.server.job_db.add_job(path, resolved_config, self._merge_args(path, extra_args), max_retries=max_retries)

        if job_id is None:
            existing = self.server.job_db.find_active_job(path)
            self.server.logger.info("Duplicate job submission for: %s" % path)
            self.send_json_response(200, {"status": "duplicate", "job_id": existing["id"] if existing else None, "path": path, "config": resolved_config})
            return

        self.server.notify_workers()
        log_file = self.server.config_log_manager.get_log_file(resolved_config)
        config_busy = self.server.config_lock_manager.is_locked(resolved_config)
        pending = self.server.job_db.pending_count_for_config(resolved_config)
        self.server.logger.info("Queued job %d: %s (config: %s)" % (job_id, path, resolved_config))
        self.send_json_response(202, {"status": "queued", "job_id": job_id, "path": path, "config": resolved_config, "log_file": log_file, "config_busy": config_busy, "pending_jobs": pending})

    def _handle_webhook(self):
        try:
            path, extra_args, config_override, max_retries = self._parse_webhook_body()
            if path is None:
                return

            path = os.path.abspath(path)
            path = self.server.path_config_manager.rewrite_path(path)
            if not os.path.exists(path):
                self.send_json_response(400, {"error": "Path does not exist", "path": path})
                return

            if self.server.path_config_manager.is_recycle_bin_path(path):
                self.server.logger.warning("Rejected recycle-bin path: %s" % path)
                self.send_json_response(400, {"error": "Path is inside a recycle-bin directory", "path": path})
                return

            if os.path.isdir(path):
                self._queue_directory(path, extra_args, config_override, max_retries)
            else:
                self._queue_file(path, extra_args, config_override, max_retries)

        except Exception as e:
            self.server.logger.exception("Error handling request: %s" % e)
            self.send_json_response(500, {"error": str(e)})


class _StoppableThread(threading.Thread):
    """Base class for daemon threads that support a cooperative stop() method."""

    def __init__(self):
        super().__init__(daemon=True)
        self.running = True
        self._stop_event = threading.Event()

    def stop(self):
        self.running = False
        self._stop_event.set()


class HeartbeatThread(_StoppableThread):
    """Periodically updates this node's heartbeat in the cluster_nodes table
    and recovers jobs from nodes that have gone stale.

    Only active when using PostgreSQLJobDatabase (no-op for SQLite).
    """

    def __init__(self, job_db, node_id, host, worker_count, server, interval, stale_seconds, logger, started_at):
        super().__init__()
        self.job_db = job_db
        self.node_id = node_id
        self.host = host
        self.worker_count = worker_count
        self.server = server
        self.interval = interval
        self.stale_seconds = stale_seconds
        self.log = logger
        self.started_at = started_at

    def run(self):
        if not self.job_db.is_distributed:
            return  # Heartbeat only meaningful for the shared PG backend
        while self.running:
            try:
                self.job_db.heartbeat(self.node_id, self.host, self.worker_count, self.started_at)
                recovered = self.job_db.recover_stale_nodes(self.stale_seconds)
                for stale_id, job_count in recovered:
                    self.log.warning("Recovered %d jobs from stale node %s" % (job_count, stale_id))
                if any(job_count > 0 for _, job_count in recovered):
                    self.server.notify_workers()  # Wake workers to pick up requeued jobs
            except Exception:
                self.log.exception("Heartbeat error")
            self._stop_event.wait(timeout=self.interval)


class ScannerThread(_StoppableThread):
    """Periodically scans configured directories for new media files and queues them.

    Each entry in scan_paths may specify:
      - path       (required) directory to scan
      - interval   seconds between scans (default: 3600)
      - rewrite_from / rewrite_to   path prefix substitution applied before
                   submitting jobs, e.g. scan /mnt/local/Media but submit
                   paths as /mnt/unionfs/Media so config matching works.
    """

    def __init__(self, scan_paths, job_db, server, path_config_manager, logger):
        super().__init__()
        self.scan_paths = scan_paths  # list of dicts from daemon.json
        self.job_db = job_db
        self.server = server
        self.path_config_manager = path_config_manager
        self.log = logger
        # Per-entry next-run timestamps so each path has its own schedule.
        self._next_run = {}

    def run(self):
        if not self.scan_paths:
            return
        self.log.info("Scanner started — %d path(s) configured" % len(self.scan_paths))
        while self.running:
            now = time.monotonic()
            next_wake = now + 60  # re-evaluate at least every minute
            for entry in self.scan_paths:
                path = entry.get("path", "")
                interval = int(entry.get("interval", 3600))
                due = self._next_run.get(path, 0)
                if now >= due:
                    try:
                        queued = self._scan(entry)
                        if queued:
                            self.server.notify_workers()
                    except Exception:
                        self.log.exception("Scanner error for path: %s" % path)
                    self._next_run[path] = time.monotonic() + interval
                next_wake = min(next_wake, self._next_run[path])
            sleep_for = max(0, next_wake - time.monotonic())
            self._stop_event.wait(timeout=sleep_for)

    def _scan(self, entry):
        if not entry.get("enabled", True):
            self.log.debug("Scanner: skipping disabled path %s" % entry.get("path", ""))
            return 0

        scan_dir = entry.get("path", "")
        rewrite_from = entry.get("rewrite_from", "")
        rewrite_to = entry.get("rewrite_to", "")

        if not scan_dir or not os.path.isdir(scan_dir):
            self.log.warning("Scanner: path does not exist or is not a directory: %s" % scan_dir)
            return 0

        allowed = self.path_config_manager.media_extensions
        # Skip already-converted files; scanning .mp4 files serves no purpose since
        # SMA converts *to* mp4 — any .mp4 present is either already processed or
        # a non-SMA file that would just be re-queued on every scan.
        skip_extensions = frozenset([".mp4"])
        candidates = []
        for root, dirs, files in os.walk(scan_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if fname.startswith("."):
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext in allowed and ext not in skip_extensions:
                    candidates.append(os.path.join(root, fname))

        if not candidates:
            self.log.debug("Scanner: no media files found in %s" % scan_dir)
            return 0

        # Filter to only files not yet recorded as scanned
        unscanned = self.job_db.filter_unscanned(candidates)
        if not unscanned:
            self.log.debug("Scanner: all %d file(s) in %s already scanned" % (len(candidates), scan_dir))
            return 0

        self.log.info("Scanner: found %d new file(s) in %s" % (len(unscanned), scan_dir))
        queued = 0
        for filepath in unscanned:
            # Apply path rewrite before config resolution and job submission
            submit_path = filepath
            if rewrite_from and rewrite_to and filepath.startswith(rewrite_from):
                submit_path = rewrite_to + filepath[len(rewrite_from) :]

            resolved_config = self.path_config_manager.get_config_for_path(submit_path)
            job_id = self.job_db.add_job(submit_path, resolved_config, [])
            if job_id is not None:
                self.log.info("Scanner queued job %d: %s" % (job_id, submit_path))
                queued += 1

        # Record all candidates (including already-queued ones) as scanned so
        # we don't re-evaluate them on the next pass.
        self.job_db.record_scanned(unscanned)

        if queued:
            self.log.info("Scanner: queued %d new job(s) from %s" % (queued, scan_dir))
        return queued


class WorkerPool:
    """Manages a pool of ConversionWorker threads."""

    def __init__(self, worker_count, job_db, path_config_manager, config_log_manager, config_lock_manager, logger, ffmpeg_dir=None, job_timeout_seconds=0, job_processes=None, job_progress=None):
        self._workers = []
        self._worker_count = worker_count
        self._job_db = job_db
        self._path_config_manager = path_config_manager
        self._config_log_manager = config_log_manager
        self._config_lock_manager = config_lock_manager
        self._logger = logger
        self._ffmpeg_dir = ffmpeg_dir
        self._job_timeout_seconds = job_timeout_seconds
        self._job_processes = job_processes if job_processes is not None else {}
        self._job_progress = job_progress if job_progress is not None else {}
        self._start_workers()

    def _start_workers(self):
        for i in range(self._worker_count):
            worker = ConversionWorker(
                worker_id=i + 1,
                job_db=self._job_db,
                path_config_manager=self._path_config_manager,
                config_log_manager=self._config_log_manager,
                config_lock_manager=self._config_lock_manager,
                logger=self._logger,
                ffmpeg_dir=self._ffmpeg_dir,
                job_timeout_seconds=self._job_timeout_seconds,
                job_processes=self._job_processes,
                job_progress=self._job_progress,
            )
            worker.start()
            self._workers.append(worker)
            self._logger.debug("Started worker thread %d" % (i + 1))

    def notify(self):
        """Wake all workers."""
        for worker in self._workers:
            worker.job_event.set()

    def stop(self):
        """Signal all workers to stop."""
        for worker in self._workers:
            worker.stop()

    def drain(self, timeout=None):
        """Wait for all workers to finish (used during shutdown/restart)."""
        for worker in self._workers:
            worker.join(timeout=timeout)

    def restart(self, ffmpeg_dir=None, job_timeout_seconds=None):
        """Stop all workers and start fresh ones."""
        if ffmpeg_dir is not None:
            self._ffmpeg_dir = ffmpeg_dir
        if job_timeout_seconds is not None:
            self._job_timeout_seconds = job_timeout_seconds
        self.stop()
        self._workers = []
        self._start_workers()


class DaemonServer(HTTPServer):
    """HTTP server with job queue and worker threads."""

    def __init__(
        self,
        server_address,
        handler_class,
        job_db,
        path_config_manager,
        config_log_manager,
        config_lock_manager,
        logger,
        worker_count=2,
        api_key=None,
        heartbeat_interval=30,
        stale_seconds=120,
        ffmpeg_dir=None,
        cli_api_key=None,
        cli_ffmpeg_dir=None,
        job_timeout_seconds=0,
    ):
        super().__init__(server_address, handler_class)
        self.job_db = job_db
        self.path_config_manager = path_config_manager
        self.config_log_manager = config_log_manager
        self.config_lock_manager = config_lock_manager
        self.logger = logger
        self.worker_count = worker_count
        self.api_key = api_key
        self.stale_seconds = stale_seconds
        self.node_id = socket.gethostname()
        self.started_at = datetime.now(timezone.utc)
        self._cli_api_key = cli_api_key
        self._cli_ffmpeg_dir = cli_ffmpeg_dir
        self._job_processes = {}  # job_id -> Popen, for cancel support
        self._job_progress = {}  # job_id -> timecode string (e.g. "00:01:23")

        # Start worker threads via WorkerPool — each worker gets its own Event
        # so workers never race to clear a shared flag.
        self.worker_pool = WorkerPool(
            worker_count=worker_count,
            job_db=job_db,
            path_config_manager=path_config_manager,
            config_log_manager=config_log_manager,
            config_lock_manager=config_lock_manager,
            logger=logger,
            ffmpeg_dir=ffmpeg_dir,
            job_timeout_seconds=job_timeout_seconds,
            job_processes=self._job_processes,
            job_progress=self._job_progress,
        )

        # Wake all workers if there are jobs waiting from a previous run.
        pending = job_db.pending_count()
        if pending > 0:
            logger.info("Found %d pending jobs from previous run" % pending)
            self.notify_workers()

        # Start heartbeat thread (only does real work with PostgreSQL backend)
        self.heartbeat_thread = HeartbeatThread(
            job_db=job_db,
            node_id=self.node_id,
            host=server_address[0],
            worker_count=worker_count,
            server=self,
            interval=heartbeat_interval,
            stale_seconds=stale_seconds,
            logger=logger,
            started_at=self.started_at,
        )
        self.heartbeat_thread.start()
        logger.debug("Started heartbeat thread (interval: %ds, stale after: %ds)" % (heartbeat_interval, stale_seconds))

        # Start scanner thread if scan_paths are configured
        self.scanner_thread = ScannerThread(
            scan_paths=path_config_manager.scan_paths,
            job_db=job_db,
            server=self,
            path_config_manager=path_config_manager,
            logger=logger,
        )
        self.scanner_thread.start()

    def notify_workers(self):
        """Wake all worker threads by setting each worker's individual event."""
        self.worker_pool.notify()

    def cancel_job(self, job_id):
        """Cancel a job by terminating its process (if running) and updating the DB.

        Returns True if the job was cancelled (either by killing a running process
        or by marking a pending job as cancelled in the database).
        """
        # Terminate the subprocess if it is currently running
        process = self._job_processes.get(job_id)
        if process is not None:
            try:
                process.terminate()
            except Exception:
                pass
            # Also update DB status; the worker's finally block will clean up
            self.job_db.cancel_job(job_id)
            return True
        # Job is not actively running — mark it cancelled in DB if still pending
        return self.job_db.cancel_job(job_id)

    def reload_config(self):
        """Reload daemon.json in-place without stopping workers or active conversions."""
        if not self.path_config_manager._config_file:
            self.logger.warning("No daemon config file to reload.")
            return

        self.logger.info("Reloading configuration from %s..." % self.path_config_manager._config_file)

        # Reset mutable collections before re-loading so stale entries are cleared
        self.path_config_manager.path_configs = []
        self.path_config_manager.path_rewrites = []
        self.path_config_manager.scan_paths = []
        self.path_config_manager.load_config(self.path_config_manager._config_file)

        # Re-apply api_key priority: CLI arg > env var > config file
        self.api_key = self._cli_api_key or os.environ.get("SMA_DAEMON_API_KEY") or self.path_config_manager.api_key

        # Re-apply ffmpeg_dir priority: CLI arg > env var > config file
        new_ffmpeg_dir = self._cli_ffmpeg_dir or os.environ.get("SMA_DAEMON_FFMPEG_DIR") or self.path_config_manager.ffmpeg_dir
        for worker in self.worker_pool._workers:
            worker.ffmpeg_dir = new_ffmpeg_dir

        # Restart scanner thread with updated scan_paths
        self.scanner_thread.stop()
        self.scanner_thread.join(timeout=5)
        self.scanner_thread = ScannerThread(
            scan_paths=self.path_config_manager.scan_paths,
            job_db=self.job_db,
            server=self,
            path_config_manager=self.path_config_manager,
            logger=self.logger,
        )
        self.scanner_thread.start()

        self.logger.info("Configuration reloaded.")

    def graceful_restart(self):
        """Drain active conversions then re-exec the daemon process."""
        self.logger.info("Graceful restart — waiting for active conversions to finish...")

        self.worker_pool.stop()
        self.heartbeat_thread.stop()
        self.scanner_thread.stop()

        active = [w for w in self.worker_pool._workers if w.is_alive()]
        while active:
            names = [str(w.worker_id) for w in active if w.current_job_id]
            if names:
                self.logger.info("Waiting for worker(s) %s to finish..." % ", ".join(names))
            for w in active:
                w.join(timeout=10)
            active = [w for w in active if w.is_alive()]

        self.logger.info("All workers finished, restarting...")

        if self.job_db.is_distributed:
            try:
                self.job_db.mark_node_offline(self.node_id)
            except Exception:
                pass

        self.heartbeat_thread.join(timeout=5)
        self.scanner_thread.join(timeout=5)

        super().shutdown()

        os.execv(sys.executable, [sys.executable] + sys.argv)

    def shutdown(self):
        self.logger.info("Shutting down — waiting for active conversions to finish...")

        # Stop workers from picking up new jobs
        self.worker_pool.stop()
        self.heartbeat_thread.stop()
        self.scanner_thread.stop()

        # Wait for in-progress conversions to complete
        active = [w for w in self.worker_pool._workers if w.is_alive()]
        while active:
            names = [str(w.worker_id) for w in active if w.current_job_id]
            if names:
                self.logger.info("Waiting for worker(s) %s to finish..." % ", ".join(names))
            for w in active:
                w.join(timeout=10)
            active = [w for w in active if w.is_alive()]

        self.logger.info("All workers finished, shutting down.")

        # Mark this node offline in the cluster table on clean shutdown
        if self.job_db.is_distributed:
            try:
                self.job_db.mark_node_offline(self.node_id)
            except Exception:
                pass

        self.heartbeat_thread.join(timeout=5)
        self.scanner_thread.join(timeout=5)

        super().shutdown()


def _validate_hwaccel(path_config_manager, ffmpeg_dir, logger):
    """Probe hardware encoder availability for each unique config at startup.

    For each config that requests an hwaccel codec (nvenc, qsv, vaapi,
    videotoolbox), runs a quick ffmpeg null-encode and logs a warning if the
    encoder is not available. Does not block server startup.
    """
    _hwaccel_map = {
        "nvenc": "h264_nvenc",
        "qsv": "h264_qsv",
        "vaapi": "h264_vaapi",
        "videotoolbox": "h264_videotoolbox",
    }

    env = os.environ.copy()
    if ffmpeg_dir:
        env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")

    seen = set()
    for config_path in path_config_manager.get_all_configs():
        if not os.path.exists(config_path):
            continue
        try:
            cp = configparser.ConfigParser()
            cp.read(config_path)
            codec_val = cp.get("Video", "video-codec", fallback="").strip().lower()
        except Exception:
            continue

        for keyword, encoder in _hwaccel_map.items():
            if keyword in codec_val and encoder not in seen:
                seen.add(encoder)
                try:
                    result = subprocess.run(
                        ["ffmpeg", "-f", "lavfi", "-i", "nullsrc", "-t", "0.1", "-c:v", encoder, "-f", "null", "-", "-loglevel", "error"],
                        capture_output=True,
                        env=env,
                        timeout=15,
                    )
                    if result.returncode != 0:
                        logger.warning(
                            "Hardware encoder '%s' (from config %s) does not appear to be available. Conversions may fail. Check driver/SDK installation." % (encoder, os.path.basename(config_path))
                        )
                    else:
                        logger.info("Hardware encoder '%s' validated OK" % encoder)
                except FileNotFoundError:
                    logger.warning("ffmpeg not found in PATH — cannot validate hardware encoder '%s'" % encoder)
                except subprocess.TimeoutExpired:
                    logger.warning("Hardware encoder probe for '%s' timed out" % encoder)
                except Exception as exc:
                    logger.warning("Hardware encoder probe for '%s' failed: %s" % (encoder, exc))


def main():
    """Parse CLI arguments, configure the daemon, and start the HTTP server.

    Resolves configuration from CLI flags, environment variables, and
    ``daemon.json`` (in that priority order). Initialises the job database
    (SQLite or PostgreSQL), sets up per-config logging and concurrency locks,
    and then serves requests until interrupted.
    """
    parser = argparse.ArgumentParser(description="SMA-NG Daemon - HTTP webhook server for media conversion")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8585, help="Port to listen on (default: 8585)")
    parser.add_argument("--workers", type=int, default=1, help="Number of worker threads (default: 1)")
    parser.add_argument("-d", "--daemon-config", help="Path to daemon.json config file (path mappings)")
    parser.add_argument("--logs-dir", default=LOGS_DIR, help="Directory for per-config log files (default: logs/)")
    parser.add_argument("--db", default=DATABASE_PATH, help="Path to SQLite database (default: config/daemon.db)")
    parser.add_argument(
        "--ffmpeg-dir", help="Directory containing ffmpeg and ffprobe binaries. Prepended to PATH for each conversion subprocess. If omitted, relies on PATH already containing the binaries."
    )
    parser.add_argument("--heartbeat-interval", type=int, default=30, help="Seconds between cluster heartbeat updates (default: 30). Only used with PostgreSQL backend.")
    parser.add_argument(
        "--stale-seconds",
        type=int,
        default=120,
        help="Seconds without a heartbeat before a node is declared stale and its running jobs are requeued (default: 120). Only used with PostgreSQL backend.",
    )
    parser.add_argument("--api-key", help="API key for authentication (or set SMA_DAEMON_API_KEY env var)")
    parser.add_argument(
        "--job-timeout",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Maximum seconds a conversion job may run before being killed (default: 0, no timeout). Can also be set via daemon.json job_timeout_seconds.",
    )

    args = parser.parse_args()

    log.info("SMA-NG Daemon starting...")
    log.info("Python %s" % sys.version)

    # Initialize managers
    config_log_manager = ConfigLogManager(args.logs_dir)
    config_lock_manager = ConfigLockManager(max_per_config=args.workers, logger=log)
    path_config_manager = PathConfigManager(args.daemon_config, logger=log)

    # Determine API key (priority: CLI arg > env var > config file)
    api_key = args.api_key or os.environ.get("SMA_DAEMON_API_KEY") or path_config_manager.api_key

    # Determine database (priority: env var > config file > SQLite fallback)
    # Note: PostgreSQL URL is not accepted on the CLI to prevent credentials appearing in ps output.
    db_url = os.environ.get("SMA_DAEMON_DB_URL") or path_config_manager.db_url

    # Determine FFmpeg directory (priority: CLI --ffmpeg-dir > env var > config file)
    ffmpeg_dir = args.ffmpeg_dir or os.environ.get("SMA_DAEMON_FFMPEG_DIR") or path_config_manager.ffmpeg_dir

    # Determine job timeout (priority: CLI --job-timeout > daemon.json; 0 means no timeout)
    job_timeout_seconds = args.job_timeout or path_config_manager.job_timeout_seconds
    if db_url:
        job_db = PostgreSQLJobDatabase(db_url, logger=log)
        db_label = "PostgreSQL: %s" % db_url
    else:
        job_db = JobDatabase(args.db, logger=log)
        db_label = "SQLite: %s" % args.db

    log.info("Node: %s" % socket.gethostname())
    log.info("Database: %s" % db_label)
    if ffmpeg_dir:
        log.info("FFmpeg/FFprobe directory: %s" % ffmpeg_dir)
    if db_url:
        log.info("Heartbeat interval: %ds (stale after %ds)" % (args.heartbeat_interval, args.stale_seconds))
    log.info("Logs directory: %s" % config_log_manager.logs_dir)
    log.info("Concurrency: One process per config (jobs for same config queue)")
    if job_timeout_seconds:
        log.info("Job timeout: %ds" % job_timeout_seconds)
    else:
        log.info("Job timeout: disabled")
    if api_key:
        log.info("Authentication: ENABLED (API key required)")
    else:
        log.info("Authentication: DISABLED (no API key configured)")

    # Show config mappings
    log.info("Config to log file mappings:")
    for config_path in path_config_manager.get_all_configs():
        log_file = config_log_manager.get_log_file(config_path)
        exists = "OK" if os.path.exists(config_path) else "MISSING"
        log.info("  %s [%s] -> %s" % (config_path, exists, log_file))

    server_address = (args.host, args.port)

    try:
        server = DaemonServer(
            server_address,
            WebhookHandler,
            job_db,
            path_config_manager,
            config_log_manager,
            config_lock_manager,
            log,
            worker_count=args.workers,
            api_key=api_key,
            heartbeat_interval=args.heartbeat_interval,
            stale_seconds=args.stale_seconds,
            ffmpeg_dir=ffmpeg_dir,
            cli_api_key=args.api_key,
            cli_ffmpeg_dir=args.ffmpeg_dir,
            job_timeout_seconds=job_timeout_seconds,
        )

        log.info("Listening on http://%s:%d" % (args.host, args.port))
        log.info("Worker threads: %d" % args.workers)
        if path_config_manager.scan_paths:
            log.info("Scheduled scans: %d path(s)" % len(path_config_manager.scan_paths))
            for sp in path_config_manager.scan_paths:
                rw = (" -> " + sp["rewrite_to"]) if sp.get("rewrite_to") else ""
                log.info("  %s (every %ds)%s" % (sp["path"], sp.get("interval", 3600), rw))
        else:
            log.info("Scheduled scans: none configured")
        log.info("Endpoints:")
        log.info("  POST /webhook      - Submit conversion job")
        log.info("  GET  /health       - Health check with job stats")
        log.info("  GET  /jobs         - List jobs (?status=pending&limit=50)")
        log.info("  GET  /jobs/<id>    - Get specific job (includes progress when running)")
        log.info("  POST /jobs/<id>/cancel  - Cancel a pending or running job")
        log.info("  GET  /configs      - Show config mappings and status")
        log.info("  GET  /stats        - Job statistics")
        log.info("  POST /cleanup      - Remove old jobs (?days=30)")
        log.info("  GET  /scan         - Check unscanned paths (?path=... for small lists)")
        log.info("  POST /scan/filter  - Check unscanned paths (JSON body for large lists)")
        log.info("  POST /scan/record  - Record paths as scanned")
        log.info("  POST /reload       - Reload daemon.json config without stopping workers")
        log.info("  POST /shutdown     - Graceful shutdown (waits for active conversions)")
        log.info("  POST /restart      - Graceful restart (drains workers, then re-execs)")
        log.info("")
        log.info("Ready to accept connections.")

        _validate_hwaccel(path_config_manager, ffmpeg_dir, log)

        def _shutdown(signum, frame):
            log.info("Received signal %d, shutting down..." % signum)
            # shutdown() is blocking — run in a thread so the signal handler returns
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        def _restart(signum, frame):
            log.info("Received SIGHUP, initiating graceful restart...")
            threading.Thread(target=server.graceful_restart, daemon=True).start()

        signal.signal(signal.SIGHUP, _restart)

        server.serve_forever()

    except Exception as e:
        log.exception("Server error: %s" % e)
        sys.exit(1)


if __name__ == "__main__":
    main()
