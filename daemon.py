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


class JobDatabase:
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
            # Migrate existing databases that lack node_id
            try:
                cursor.execute("ALTER TABLE jobs ADD COLUMN node_id TEXT")
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

    def add_job(self, path, config, args=None):
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
                INSERT INTO jobs (path, config, args, status)
                VALUES (?, ?, ?, ?)
            """,
                (path, config, args_json, STATUS_PENDING),
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
                    ORDER BY created_at ASC
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
                    ORDER BY created_at ASC
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
        """Mark a job as failed."""
        with self._cursor() as cursor:
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


class PostgreSQLJobDatabase:
    """PostgreSQL-backed job queue for distributed multi-node operation.

    Uses SELECT FOR UPDATE SKIP LOCKED to atomically claim jobs, ensuring
    no two nodes ever process the same file. Requires psycopg2-binary.

    Usage:
        db = PostgreSQLJobDatabase("postgresql://user:pass@host/sma")
        python daemon.py --db-url postgresql://user:pass@host/sma
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
                # Migration: add started_at to existing tables
                cur.execute("""
                    ALTER TABLE cluster_nodes
                    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                """)
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

    def add_job(self, path, config, args=None):
        """Add a job to the queue. Returns job ID, or None if a duplicate is already pending/running."""
        args_json = json.dumps(args or [])
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM jobs WHERE path = %s AND status IN (%s, %s) LIMIT 1", (path, STATUS_PENDING, STATUS_RUNNING))
                existing = cur.fetchone()
                if existing:
                    self.log.debug("Duplicate job for path: %s (existing job %d)" % (path, existing["id"]))
                    return None
                cur.execute("INSERT INTO jobs (path, config, args, status) VALUES (%s, %s, %s, %s) RETURNING id", (path, config, args_json, STATUS_PENDING))
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
                        ORDER BY created_at ASC
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
                        ORDER BY created_at ASC
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
        """Mark a job as failed."""
        with self._conn() as conn:
            with conn.cursor() as cur:
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

        Includes uptime_seconds (seconds since daemon start) derived from started_at.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT *,
                           EXTRACT(EPOCH FROM (NOW() - started_at))::INT AS uptime_seconds
                    FROM cluster_nodes
                    ORDER BY last_seen DESC
                """)
                return [dict(r) for r in cur.fetchall()]

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
    Manages per-config locks to ensure only one process runs per config at a time.

    Jobs for the same config will queue and execute sequentially.
    Jobs for different configs can run in parallel.
    """

    def __init__(self, logger=None):
        self.log = logger or log
        self._master_lock = threading.Lock()
        self._config_locks = {}  # config_path -> Lock
        self._active_configs = {}  # config_path -> (job_id, job_path)
        self._waiting_counts = {}  # config_path -> number of waiting jobs

    def _get_lock(self, config_path):
        """Get or create a lock for a config (thread-safe)."""
        with self._master_lock:
            if config_path not in self._config_locks:
                self._config_locks[config_path] = threading.Lock()
                self._waiting_counts[config_path] = 0
            return self._config_locks[config_path]

    def acquire(self, config_path, job_id, job_path):
        """
        Acquire lock for a config. Blocks until the lock is available.
        Returns True when lock is acquired.
        """
        lock = self._get_lock(config_path)

        # Increment waiting count
        with self._master_lock:
            self._waiting_counts[config_path] = self._waiting_counts.get(config_path, 0) + 1
            if config_path in self._active_configs:
                self.log.info("Job %d waiting for config lock: %s (current: job %d)" % (job_id, os.path.basename(config_path), self._active_configs[config_path][0]))

        # Block until lock is available
        lock.acquire()

        # Update state
        with self._master_lock:
            self._waiting_counts[config_path] -= 1
            self._active_configs[config_path] = (job_id, job_path)

        self.log.debug("Job %d acquired lock for config: %s" % (job_id, os.path.basename(config_path)))
        return True

    def release(self, config_path):
        """Release lock for a config."""
        lock = self._get_lock(config_path)

        with self._master_lock:
            if config_path in self._active_configs:
                del self._active_configs[config_path]

        try:
            lock.release()
            self.log.debug("Released lock for config: %s" % os.path.basename(config_path))
        except RuntimeError:
            # Lock was not held
            pass

    def get_status(self):
        """Get current lock status for all configs."""
        with self._master_lock:
            active = {}
            for config, (job_id, job_path) in self._active_configs.items():
                active[config] = {"job_id": job_id, "path": job_path}
            return {"active": active, "waiting": {k: v for k, v in self._waiting_counts.items() if v > 0}}

    def is_locked(self, config_path):
        """Check if a config is currently locked."""
        with self._master_lock:
            return config_path in self._active_configs

    def get_locked_configs(self):
        """Return the set of config paths that are currently locked."""
        with self._master_lock:
            return set(self._active_configs.keys())

    def get_active_job(self, config_path):
        """Get the active job for a config, if any."""
        with self._master_lock:
            return self._active_configs.get(config_path)


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
        self.default_config = DEFAULT_PROCESS_CONFIG
        self.api_key = None  # Can be set from daemon.json
        self.db_url = None  # Can be set from daemon.json
        self.ffmpeg_dir = None  # Can be set from daemon.json
        self.media_extensions = frozenset([".mp4", ".mkv", ".avi", ".mov", ".ts"])
        self.scan_paths = []  # Can be set from daemon.json

        if config_file and os.path.exists(config_file):
            self.load_config(config_file)
        elif os.path.exists(DEFAULT_DAEMON_CONFIG):
            self.load_config(DEFAULT_DAEMON_CONFIG)
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

            # Load media extensions inclusion list for directory scanning
            raw_exts = config.get("media_extensions")
            if raw_exts is not None:
                self.media_extensions = frozenset(("." + e.lower().lstrip(".")) for e in raw_exts if e)

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

                self.path_configs.append({"path": path, "config": config_path})

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

    def get_all_configs(self):
        """Return list of all unique config files."""
        configs = {self.default_config}
        for entry in self.path_configs:
            configs.add(entry["config"])
        return list(configs)


class ConversionWorker(threading.Thread):
    """Background worker thread that processes conversion jobs from the database."""

    def __init__(self, worker_id, job_db, job_event, path_config_manager, config_log_manager, config_lock_manager, logger, ffmpeg_dir=None):
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self.node_id = socket.gethostname()
        self.job_db = job_db
        self.job_event = job_event  # Event to signal new jobs
        self.path_config_manager = path_config_manager
        self.config_log_manager = config_log_manager
        self.config_lock_manager = config_lock_manager
        self.log = logger
        self.script_path = os.path.join(SCRIPT_DIR, "manual.py")
        self.ffmpeg_dir = ffmpeg_dir
        self.running = True

    def stop(self):
        """Signal worker to stop."""
        self.running = False
        self.job_event.set()

    def run(self):
        while self.running:
            # Wait for a job notification or periodic timeout.
            self.job_event.wait(timeout=5.0)

            if not self.running:
                break

            # Drain all available jobs before waiting again.  Without this
            # inner loop, a worker that finishes a job goes back to waiting
            # on the shared event — which may already be clear because the
            # other worker cleared it while this one was busy — and pending
            # jobs are never picked up until an external trigger fires.
            while self.running:
                locked = self.config_lock_manager.get_locked_configs()
                job = self.job_db.claim_next_job(self.worker_id, self.node_id, exclude_configs=locked or None)
                if job:
                    self.process_job(job)
                    # After finishing, signal the other worker in case more
                    # jobs arrived while we were busy.
                    self.job_event.set()
                else:
                    # No claimable job right now — go back to waiting.
                    self.job_event.clear()
                    break

    def process_job(self, job):
        job_id = job["id"]
        path = job["path"]
        args = json.loads(job["args"]) if job["args"] else []
        config_file = job["config"]

        if not os.path.exists(path):
            self.log.error("Job %d: Path does not exist: %s" % (job_id, path))
            self.job_db.fail_job(job_id, "Path does not exist")
            return

        # Job is already marked running by claim_next_job()

        # Acquire lock for this config (blocks if another job is using it)
        self.log.info("Worker %d acquiring lock for job %d: %s" % (self.worker_id, job_id, os.path.basename(config_file)))
        self.config_lock_manager.acquire(config_file, job_id, path)

        try:
            success = self._run_conversion(job_id, path, config_file, args)
            if success:
                self.job_db.complete_job(job_id)
            else:
                self.job_db.fail_job(job_id, "Conversion process failed")
        except Exception as e:
            self.log.exception("Job %d failed: %s" % (job_id, e))
            self.job_db.fail_job(job_id, str(e))
        finally:
            self.config_lock_manager.release(config_file)

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

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )

            for line in process.stdout:
                line = line.strip()
                if line:
                    config_logger.info(line)
                    self.log.info("[%s] %s" % (os.path.basename(config_file), line))

            process.wait()

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


DOCS_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SMA-NG Documentation</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  .copy-btn { position:absolute; top:0.5rem; right:0.5rem; opacity:0; transition:opacity 0.15s; }
  pre:hover .copy-btn { opacity:1; }
  pre { position:relative; }
</style>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen">
<div class="max-w-4xl mx-auto px-6 py-10">
  <div class="mb-6">
    <a href="/" class="text-blue-400 hover:underline text-sm">&larr; Back to Dashboard</a>
  </div>
  <article class="prose-invert">
    %s
  </article>
  <div class="mt-16 mb-8 pt-8 border-t border-gray-700 text-center text-gray-500 text-xs">
    SMA-NG Documentation &mdash; Generated from docs/README.md
  </div>
</div>
<script>
document.querySelectorAll('pre').forEach(pre => {
  const btn = document.createElement('button');
  btn.className = 'copy-btn bg-gray-700 hover:bg-gray-600 text-gray-300 hover:text-white text-xs px-2 py-1 rounded';
  btn.textContent = 'Copy';
  btn.addEventListener('click', () => {
    const code = pre.querySelector('code');
    navigator.clipboard.writeText(code ? code.textContent : pre.textContent).then(() => {
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
    });
  });
  pre.appendChild(btn);
});
</script>
</body>
</html>"""


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SMA-NG Daemon</title>
<script src="https://cdn.tailwindcss.com"></script>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
<style>
[x-cloak]{display:none!important}
/* Tooltip */
.tooltip { position:relative; display:inline-block; }
.tooltip .tip {
  visibility:hidden; opacity:0; transition:opacity 0.15s;
  position:absolute; bottom:calc(100% + 6px); left:50%; transform:translateX(-50%);
  background:#1f2937; border:1px solid #374151; color:#d1d5db;
  font-size:0.7rem; line-height:1.4; white-space:nowrap; max-width:280px;
  white-space:normal; text-align:center;
  padding:0.35rem 0.6rem; border-radius:0.375rem; z-index:50; pointer-events:none;
}
.tooltip:hover .tip { visibility:visible; opacity:1; }
</style>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen">
<div x-data="dashboard()" x-init="init()" x-cloak class="max-w-7xl mx-auto px-4 py-6">

  <!-- Header -->
  <div class="flex items-center justify-between mb-8">
    <div>
      <h1 class="text-2xl font-bold text-white">SMA-NG</h1>
      <p class="text-gray-400 text-sm">Media Conversion Daemon</p>
    </div>
    <div class="flex items-center gap-4">
      <a href="/docs" class="text-xs text-blue-400 hover:underline">Docs</a>
      <span class="tooltip text-xs text-gray-500" x-show="health.node" x-text="health.node">
        <span class="tip">Hostname of this daemon node</span>
      </span>
      <span class="tooltip text-xs text-gray-500" x-show="health.uptime_seconds != null" x-text="'Up ' + fmtUptime(health.uptime_seconds)">
        <span class="tip">Time since daemon started</span>
      </span>
      <span class="text-xs text-gray-500" x-text="'Updated ' + lastUpdate"></span>
      <span class="tooltip inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
            :class="health.status === 'ok' ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'">
        <span class="w-1.5 h-1.5 rounded-full" :class="health.status === 'ok' ? 'bg-green-400' : 'bg-red-400'"></span>
        <span x-text="health.status === 'ok' ? 'Healthy' : 'Offline'"></span>
        <span class="tip">Daemon health status — refreshes every 5 seconds</span>
      </span>
    </div>
  </div>

  <!-- Submit Job Form -->
  <div class="bg-gray-800 rounded-lg border border-gray-700 p-5 mb-8">
    <h2 class="text-sm font-semibold text-green-400 uppercase tracking-wider mb-3">Submit Job</h2>
    <form @submit.prevent="submitJob()" class="flex gap-3">
      <input type="text" x-model="submitPath" placeholder="/path/to/file/or/directory"
             title="Absolute path to the media file or directory to convert"
             class="flex-1 bg-gray-900 border border-gray-600 rounded-lg px-4 py-2 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-blue-500 font-mono">
      <input type="text" x-model="submitArgs" placeholder="extra args e.g. -tmdb 603"
             title="Optional extra arguments passed to manual.py (e.g. -tmdb 603, -tvdb 73871 -s 3 -e 10)"
             class="w-56 bg-gray-900 border border-gray-600 rounded-lg px-4 py-2 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-blue-500 font-mono">
      <button type="submit" :disabled="!submitPath.trim() || submitting"
              title="Queue this path for conversion"
              class="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-sm font-medium text-white transition-colors whitespace-nowrap">
        <span x-show="!submitting">Submit</span>
        <span x-show="submitting">Queuing…</span>
      </button>
    </form>
    <div x-show="submitResult" class="mt-3 text-sm px-3 py-2 rounded-lg"
         :class="submitError ? 'bg-red-900/50 text-red-300' : 'bg-green-900/50 text-green-300'"
         x-text="submitResult"></div>
  </div>

  <!-- Stats + Workers -->
  <div class="grid md:grid-cols-3 gap-6 mb-8">

    <!-- Stats Cards (span 2 cols) -->
    <div class="md:col-span-2">
      <h2 class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Job Statistics</h2>
      <div class="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <template x-for="s in statCards" :key="s.key">
          <div class="tooltip bg-gray-800 rounded-lg p-4 border border-gray-700 cursor-default"
               @click="if(s.key!=='total'){filter=s.key;fetchJobs();document.getElementById('jobs-table').scrollIntoView({behavior:'smooth'})}">
            <div class="text-xs font-medium uppercase tracking-wider" :class="s.color" x-text="s.label"></div>
            <div class="text-2xl font-bold text-white mt-1" x-text="stats[s.key] ?? 0"></div>
            <span class="tip" x-text="s.tip"></span>
          </div>
        </template>
      </div>
    </div>

    <!-- Workers -->
    <div class="bg-gray-800 rounded-lg border border-gray-700 p-5">
      <h2 class="tooltip text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3 cursor-default">
        Workers
        <span class="tip">Background threads that pick up and run conversion jobs. Each config can only run one job at a time.</span>
      </h2>
      <div class="flex items-baseline gap-2 mb-3">
        <span class="text-3xl font-bold text-white" x-text="activeWorkers"></span>
        <span class="text-gray-500 text-sm" x-text="'/ ' + (health.workers || 0) + ' busy'"></span>
      </div>
      <div class="space-y-1.5" x-show="Object.keys(health.active||{}).length">
        <template x-for="(info, config) in (health.active||{})" :key="config">
          <div class="bg-gray-700/50 rounded p-2">
            <div class="flex items-center gap-2 mb-0.5">
              <span class="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse flex-shrink-0"></span>
              <span class="text-xs text-blue-300 font-medium truncate" x-text="config.split('/').pop()"></span>
            </div>
            <div class="tooltip text-xs text-gray-400 truncate pl-3.5 cursor-default" :title="info.path" x-text="info.path.split('/').pop()">
              <span class="tip" x-text="info.path"></span>
            </div>
            <div class="text-xs text-gray-600 pl-3.5">Job #<span x-text="info.job_id"></span></div>
          </div>
        </template>
      </div>
      <p class="text-gray-600 text-xs" x-show="!Object.keys(health.active||{}).length">No active conversions</p>

      <!-- Waiting queues -->
      <div class="mt-3 space-y-1" x-show="Object.keys(health.waiting||{}).length">
        <div class="text-xs text-gray-500 mb-1">Queued</div>
        <template x-for="(count, config) in (health.waiting||{})" :key="config">
          <div class="flex items-center justify-between">
            <span class="text-xs text-gray-400 truncate" x-text="config.split('/').pop()"></span>
            <span class="tooltip text-xs bg-yellow-900/60 text-yellow-300 px-2 py-0.5 rounded-full ml-2 flex-shrink-0" x-text="count">
              <span class="tip">Jobs waiting for this config's lock to be released</span>
            </span>
          </div>
        </template>
      </div>
    </div>
  </div>

  <!-- Config Mappings -->
  <div class="bg-gray-800 rounded-lg border border-gray-700 p-5 mb-8">
    <h2 class="tooltip text-sm font-semibold text-purple-400 uppercase tracking-wider mb-3 cursor-default">
      Config Mappings
      <span class="tip">Path prefixes matched longest-first to determine which autoProcess.ini a job uses</span>
    </h2>
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead><tr class="text-left text-gray-400 border-b border-gray-700 text-xs">
          <th class="pb-2 pr-4 font-medium">Path Prefix</th>
          <th class="pb-2 pr-4 font-medium">Config</th>
          <th class="pb-2 pr-4 font-medium">
            <span class="tooltip cursor-default">Status<span class="tip">Whether this config is currently running a job</span></span>
          </th>
          <th class="pb-2 pr-4 font-medium">
            <span class="tooltip cursor-default">Pending<span class="tip">Jobs waiting in queue for this config</span></span>
          </th>
          <th class="pb-2 font-medium">Log</th>
        </tr></thead>
        <tbody class="divide-y divide-gray-700/50 text-xs">
          <!-- Default config row -->
          <tr x-show="configs.default_config" class="opacity-70 hover:opacity-100 hover:bg-gray-700/20">
            <td class="py-2 pr-4 text-gray-500 font-mono italic">default</td>
            <td class="py-2 pr-4 text-gray-400 font-mono" x-text="(configs.default_config||'').split('/').pop()"></td>
            <td class="py-2 pr-4">
              <span x-show="configs.default_active_job" class="bg-blue-900 text-blue-300 px-2 py-0.5 rounded-full">Running</span>
              <span x-show="!configs.default_active_job" class="bg-gray-700 text-gray-500 px-2 py-0.5 rounded-full">Idle</span>
            </td>
            <td class="py-2 pr-4 text-gray-500" x-text="configs.default_pending_jobs || 0"></td>
            <td class="py-2 text-gray-600 font-mono" x-text="(configs.default_log||'').split('/').pop()"></td>
          </tr>
          <template x-for="c in configs.path_configs || []" :key="c.path">
            <tr class="hover:bg-gray-700/20">
              <td class="py-2 pr-4 text-gray-300 font-mono" :title="c.path" x-text="c.path"></td>
              <td class="py-2 pr-4 text-gray-400 font-mono" x-text="c.config.split('/').pop()"></td>
              <td class="py-2 pr-4">
                <span x-show="c.active_job" class="bg-blue-900 text-blue-300 px-2 py-0.5 rounded-full">Running</span>
                <span x-show="!c.active_job" class="bg-gray-700 text-gray-500 px-2 py-0.5 rounded-full">Idle</span>
              </td>
              <td class="py-2 pr-4 text-gray-400" x-text="c.pending_jobs || 0"></td>
              <td class="py-2 text-gray-600 font-mono" x-text="c.log_file ? c.log_file.split('/').pop() : ''"></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Jobs Table -->
  <div id="jobs-table" class="bg-gray-800 rounded-lg border border-gray-700 p-5">
    <div class="flex items-center justify-between mb-3">
      <div class="flex items-center gap-3">
        <h2 class="text-sm font-semibold text-gray-300 uppercase tracking-wider">Recent Jobs</h2>
        <button @click="requeueAllFailed()" :disabled="requeueingAll || (stats.failed||0) === 0"
                class="text-xs px-2.5 py-1 rounded border border-red-700 text-red-400 hover:border-red-500 hover:text-red-300 disabled:opacity-30 transition-colors"
                x-text="requeueingAll ? 'Requeueing…' : 'Requeue All Failed'">
        </button>
      </div>
      <div class="flex gap-2">
        <template x-for="f in ['all','pending','running','completed','failed']" :key="f">
          <button @click="filter=f; page=0; fetchJobs()"
                  class="text-xs px-2.5 py-1 rounded-full border transition-colors"
                  :class="filter===f ? 'border-blue-500 bg-blue-500/20 text-blue-300' : 'border-gray-600 text-gray-400 hover:border-gray-500'"
                  x-text="f.charAt(0).toUpperCase()+f.slice(1)">
          </button>
        </template>
      </div>
    </div>
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead><tr class="text-left text-gray-400 border-b border-gray-700 text-xs">
          <th class="pb-2 pr-3 w-12 font-medium">ID</th>
          <th class="pb-2 pr-3 font-medium">File</th>
          <th class="pb-2 pr-3 font-medium">Config</th>
          <th class="pb-2 pr-3 font-medium">Status</th>
          <th class="pb-2 pr-3 font-medium">Created</th>
          <th class="pb-2 pr-3 font-medium">Duration</th>
          <th class="pb-2 font-medium"></th>
        </tr></thead>
        <tbody class="divide-y divide-gray-700/50">
          <template x-for="j in jobs" :key="j.id">
            <tr class="hover:bg-gray-700/30 group">
              <td class="py-2 pr-3 text-gray-600 font-mono text-xs" x-text="j.id"></td>
              <td class="py-2 pr-3 text-xs max-w-xs">
                <div class="tooltip cursor-default">
                  <span class="text-gray-300 font-mono" x-text="j.path.split('/').pop()"></span>
                  <span class="tip" x-text="j.path"></span>
                </div>
                <div x-show="j.error" class="text-red-400 mt-0.5 truncate max-w-xs" :title="j.error" x-text="j.error"></div>
              </td>
              <td class="py-2 pr-3 text-gray-500 font-mono text-xs" x-text="j.config ? j.config.split('/').pop() : ''"></td>
              <td class="py-2 pr-3">
                <span class="text-xs px-2 py-0.5 rounded-full"
                      :class="{'pending':'bg-gray-700 text-gray-300','running':'bg-blue-900 text-blue-300','completed':'bg-green-900 text-green-300','failed':'bg-red-900 text-red-300'}[j.status] || 'bg-gray-700 text-gray-400'"
                      x-text="j.status"></span>
              </td>
              <td class="py-2 pr-3 text-gray-500 text-xs" x-text="fmtTime(j.created_at)"></td>
              <td class="py-2 pr-3 text-gray-500 text-xs" x-text="fmtDuration(j)"></td>
              <td class="py-2 text-xs">
                <button x-show="j.status === 'failed'" @click="requeueJob(j.id)"
                        class="opacity-0 group-hover:opacity-100 px-2 py-0.5 rounded border border-red-700 text-red-400 hover:border-red-500 hover:text-red-300 transition-all">
                  Requeue
                </button>
              </td>
            </tr>
          </template>
          <template x-if="jobs.length === 0">
            <tr><td colspan="7" class="py-8 text-center text-gray-500 text-sm">No jobs found</td></tr>
          </template>
        </tbody>
      </table>
    </div>
    <div class="flex justify-between items-center mt-3 text-xs text-gray-500" x-show="jobs.length > 0 || page > 0">
      <span x-text="page === 0 ? 'Showing ' + jobs.length + ' jobs' : 'Page ' + (page+1) + ' · ' + jobs.length + ' jobs'"></span>
      <div class="flex gap-2">
        <button @click="if(page>0){page--;fetchJobs()}" :disabled="page===0"
                class="px-2 py-1 rounded border border-gray-600 disabled:opacity-30" :class="page>0?'hover:border-gray-500':''">Prev</button>
        <button @click="page++;fetchJobs()" :disabled="jobs.length < pageSize"
                class="px-2 py-1 rounded border border-gray-600 disabled:opacity-30" :class="jobs.length>=pageSize?'hover:border-gray-500':''">Next</button>
      </div>
    </div>
  </div>
</div>

<script>
function dashboard() {
  return {
    health: {}, stats: {}, configs: {}, jobs: [],
    filter: 'all', page: 0, pageSize: 50,
    lastUpdate: '', interval: null,
    submitPath: '', submitArgs: '', submitting: false, submitResult: '', submitError: false,
    requeueingAll: false,
    authHeaders(extra) {
      const h = Object.assign({'Content-Type': 'application/json'}, extra || {});
      if (window.SMA_API_KEY) h['X-API-Key'] = window.SMA_API_KEY;
      return h;
    },
    statCards: [
      {key:'total',    label:'Total',     color:'text-gray-400',  tip:'All jobs ever submitted'},
      {key:'pending',  label:'Pending',   color:'text-yellow-400',tip:'Waiting to be picked up by a worker — click to filter'},
      {key:'running',  label:'Running',   color:'text-blue-400',  tip:'Currently being converted — click to filter'},
      {key:'completed',label:'Completed', color:'text-green-400', tip:'Successfully finished — click to filter'},
      {key:'failed',   label:'Failed',    color:'text-red-400',   tip:'Ended with an error — click to filter'},
    ],
    get activeWorkers() {
      return Object.keys(this.health.active || {}).length;
    },
    async init() {
      await this.refresh();
      this.interval = setInterval(() => this.refresh(), 5000);
    },
    async refresh() {
      try {
        const [h, s, c] = await Promise.all([
          fetch('/health', {headers: this.authHeaders()}).then(r=>r.json()),
          fetch('/stats', {headers: this.authHeaders()}).then(r=>r.json()),
          fetch('/configs', {headers: this.authHeaders()}).then(r=>r.json()),
        ]);
        this.health = h; this.stats = s; this.configs = c;
        await this.fetchJobs();
        this.lastUpdate = new Date().toLocaleTimeString();
      } catch(e) { this.health = {status:'error'}; }
    },
    async fetchJobs() {
      const params = new URLSearchParams({limit: this.pageSize, offset: this.page * this.pageSize});
      if (this.filter !== 'all') params.set('status', this.filter);
      try {
        const r = await fetch('/jobs?' + params, {headers: this.authHeaders()}).then(r=>r.json());
        this.jobs = r.jobs || [];
      } catch(e) { this.jobs = []; }
    },
    async submitJob() {
      this.submitting = true; this.submitResult = ''; this.submitError = false;
      try {
        const body = {path: this.submitPath.trim()};
        if (this.submitArgs.trim()) body.args = this.submitArgs.trim().split(/\\s+/);
        const r = await fetch('/webhook', {method:'POST', headers: this.authHeaders(), body: JSON.stringify(body)});
        const d = await r.json();
        if (r.ok) {
          this.submitResult = 'Job #' + d.job_id + ' queued for ' + d.path;
          this.submitPath = ''; this.submitArgs = '';
          setTimeout(() => this.refresh(), 500);
        } else {
          this.submitError = true;
          this.submitResult = d.error || 'Failed to submit job';
        }
      } catch(e) { this.submitError = true; this.submitResult = 'Request failed: ' + e.message; }
      this.submitting = false;
      setTimeout(() => this.submitResult = '', 8000);
    },
    async requeueJob(id) {
      try {
        const r = await fetch('/jobs/' + id + '/requeue', {method: 'POST', headers: this.authHeaders()});
        if (r.ok) {
          const j = this.jobs.find(j => j.id === id);
          if (j) j.status = 'pending';
          setTimeout(() => this.refresh(), 500);
        }
      } catch(e) {}
    },
    async requeueAllFailed() {
      this.requeueingAll = true;
      try {
        const r = await fetch('/jobs/requeue', {method: 'POST', headers: this.authHeaders()});
        if (r.ok) {
          const d = await r.json();
          setTimeout(() => this.refresh(), 300);
        }
      } catch(e) {}
      this.requeueingAll = false;
    },
    fmtTime(t) {
      if (!t) return '-';
      const d = new Date(t.includes('Z') || t.includes('+') ? t : t + 'Z');
      return d.toLocaleString(undefined, {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
    },
    fmtDuration(j) {
      if (!j.started_at) return '-';
      const start = new Date(j.started_at.includes('Z') || j.started_at.includes('+') ? j.started_at : j.started_at + 'Z');
      const end = j.completed_at ? new Date(j.completed_at.includes('Z') || j.completed_at.includes('+') ? j.completed_at : j.completed_at + 'Z') : new Date();
      const s = Math.round((end - start) / 1000);
      if (s < 60) return s + 's';
      if (s < 3600) return Math.floor(s/60) + 'm ' + (s%60) + 's';
      return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
    },
    fmtUptime(s) {
      if (s == null) return '';
      if (s < 60) return s + 's';
      if (s < 3600) return Math.floor(s/60) + 'm';
      if (s < 86400) return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
      return Math.floor(s/86400) + 'd ' + Math.floor((s%86400)/3600) + 'h';
    }
  };
}
</script>
</body>
</html>"""


class WebhookHandler(BaseHTTPRequestHandler):
    """HTTP request handler for webhook endpoints."""

    # Endpoints that don't require authentication
    PUBLIC_ENDPOINTS = ["/", "/health", "/status", "/docs"]

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
        if isinstance(self.server.job_db, PostgreSQLJobDatabase):
            # Run staleness check on every status request so the response
            # reflects current reality rather than waiting for the next
            # heartbeat cycle.
            recovered = self.server.job_db.recover_stale_nodes(self.server.stale_seconds)
            for stale_id, job_count in recovered:
                self.server.logger.warning("Status check: recovered %d jobs from stale node %s" % (job_count, stale_id))
            if any(job_count > 0 for _, job_count in recovered):
                self.server.job_event.set()
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
                    "note": "Cluster status requires PostgreSQL backend (--db-url)",
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
                self.send_json_response(200, job)
            else:
                self.send_json_response(404, {"error": "Job not found"})
        except ValueError:
            self.send_json_response(400, {"error": "Invalid job ID"})

    def _get_configs(self):
        configs_with_status = [
            {
                "path": entry["path"],
                "config": entry["config"],
                "log_file": self.server.config_log_manager.get_log_file(entry["config"]),
                "active_job": self.server.config_lock_manager.get_active_job(entry["config"]),
                "pending_jobs": self.server.job_db.pending_count_for_config(entry["config"]),
            }
            for entry in self.server.path_config_manager.path_configs
        ]
        default_config = self.server.path_config_manager.default_config
        self.send_json_response(
            200,
            {
                "default_config": default_config,
                "default_log": self.server.config_log_manager.get_log_file(default_config),
                "default_active_job": self.server.config_lock_manager.get_active_job(default_config),
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

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        # Check authentication for non-public endpoints
        if not self.is_public_endpoint(parsed.path) and not self.check_auth():
            return

        if parsed.path == "/" and self.wants_html():
            api_key = self.server.api_key or ""
            key_script = "<script>window.SMA_API_KEY=%s;</script>" % json.dumps(api_key)
            self.send_html_response(200, DASHBOARD_HTML.replace("</head>", key_script + "</head>", 1))
        elif parsed.path == "/docs":
            try:
                with open(DOCS_PATH, "r", encoding="utf-8") as f:
                    md_content = f.read()
                self.send_html_response(200, DOCS_TEMPLATE % _render_markdown_to_html(md_content))
            except FileNotFoundError:
                self.send_html_response(404, "<h1>Documentation not found</h1><p>docs/README.md missing</p>")
        elif parsed.path in ["/", "/health"]:
            self._get_health()
        elif parsed.path == "/status":
            self._get_status()
        elif parsed.path == "/jobs":
            self._get_jobs(query)
        elif parsed.path.startswith("/jobs/"):
            self._get_job(parsed.path)
        elif parsed.path == "/configs":
            self._get_configs()
        elif parsed.path == "/stats":
            self.send_json_response(200, self.server.job_db.get_stats())
        elif parsed.path == "/scan":
            self._get_scan(query)
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
            self.server.job_event.set()
        self.send_json_response(200, {"requeued": count})

    def _post_job_requeue(self, path):
        try:
            job_id = int(path.split("/")[-2])
            requeued = self.server.job_db.requeue_job(job_id)
            if requeued:
                self.server.job_event.set()
                self.send_json_response(200, {"requeued": True, "job_id": job_id})
            else:
                job = self.server.job_db.get_job(job_id)
                if job is None:
                    self.send_json_response(404, {"error": "Job not found"})
                else:
                    self.send_json_response(409, {"error": "Job cannot be requeued", "status": job["status"], "note": "Only failed jobs can be requeued"})
        except ValueError:
            self.send_json_response(400, {"error": "Invalid job ID"})

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

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        # All POST endpoints require authentication
        if not self.check_auth():
            return

        if parsed.path in ["/", "/webhook", "/convert"]:
            self._handle_webhook()
        elif parsed.path == "/cleanup":
            self._post_cleanup(query)
        elif parsed.path == "/jobs/requeue":
            self._post_jobs_requeue_bulk(query)
        elif parsed.path.startswith("/jobs/") and parsed.path.endswith("/requeue"):
            self._post_job_requeue(parsed.path)
        elif parsed.path == "/scan/filter":
            self._post_scan_filter()
        elif parsed.path == "/scan/record":
            self._post_scan_record()
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

    def _handle_webhook(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                self.send_json_response(400, {"error": "Empty request body"})
                return

            body = self.rfile.read(content_length).decode("utf-8").strip()

            path = None
            extra_args = []
            config_override = None

            content_type = self.headers.get("Content-Type", "")

            if "application/json" in content_type:
                try:
                    data = json.loads(body)
                    if isinstance(data, dict):
                        path = data.get("path") or data.get("file") or data.get("input")
                        extra_args = data.get("args", [])
                        config_override = data.get("config")
                        if isinstance(extra_args, str):
                            extra_args = extra_args.split()
                    elif isinstance(data, str):
                        path = data
                except json.JSONDecodeError:
                    path = body
            else:
                path = body

            if not path:
                self.send_json_response(400, {"error": "No path provided"})
                return

            path = os.path.abspath(path)

            if not os.path.exists(path):
                self.send_json_response(400, {"error": "Path does not exist", "path": path})
                return

            # --- Directory: expand to individual media files ---
            if os.path.isdir(path):
                files = self._collect_media_files(path)
                if not files:
                    self.send_json_response(200, {"status": "empty", "path": path, "message": "No media files found in directory"})
                    return

                queued = []
                duplicates = []
                for filepath in files:
                    resolved_config = config_override if (config_override and os.path.exists(config_override)) else self.server.path_config_manager.get_config_for_path(filepath)
                    job_id = self.server.job_db.add_job(filepath, resolved_config, extra_args)
                    if job_id is None:
                        existing = self.server.job_db.find_active_job(filepath)
                        duplicates.append({"path": filepath, "job_id": existing["id"] if existing else None})
                    else:
                        queued.append({"job_id": job_id, "path": filepath, "config": resolved_config})

                if queued:
                    self.server.job_event.set()
                    self.server.logger.info("Directory %s: queued %d files, %d duplicates" % (path, len(queued), len(duplicates)))

                self.send_json_response(
                    202,
                    {
                        "status": "queued",
                        "directory": path,
                        "queued": queued,
                        "duplicates": duplicates,
                        "queued_count": len(queued),
                        "duplicate_count": len(duplicates),
                    },
                )
                return

            # --- Single file ---
            # Determine config
            if config_override and os.path.exists(config_override):
                resolved_config = config_override
            else:
                resolved_config = self.server.path_config_manager.get_config_for_path(path)

            # Add job to database (returns None if a duplicate is already pending/running)
            job_id = self.server.job_db.add_job(path, resolved_config, extra_args)

            if job_id is None:
                existing = self.server.job_db.find_active_job(path)
                self.server.logger.info("Duplicate job submission for: %s" % path)
                self.send_json_response(
                    200,
                    {
                        "status": "duplicate",
                        "job_id": existing["id"] if existing else None,
                        "path": path,
                        "config": resolved_config,
                    },
                )
                return

            # Signal workers that a new job is available
            self.server.job_event.set()

            log_file = self.server.config_log_manager.get_log_file(resolved_config)
            config_busy = self.server.config_lock_manager.is_locked(resolved_config)
            pending = self.server.job_db.pending_count_for_config(resolved_config)

            self.server.logger.info("Queued job %d: %s (config: %s)" % (job_id, path, resolved_config))

            self.send_json_response(202, {"status": "queued", "job_id": job_id, "path": path, "config": resolved_config, "log_file": log_file, "config_busy": config_busy, "pending_jobs": pending})

        except Exception as e:
            self.server.logger.exception("Error handling request: %s" % e)
            self.send_json_response(500, {"error": str(e)})


class HeartbeatThread(threading.Thread):
    """Periodically updates this node's heartbeat in the cluster_nodes table
    and recovers jobs from nodes that have gone stale.

    Only active when using PostgreSQLJobDatabase (no-op for SQLite).
    """

    def __init__(self, job_db, node_id, host, worker_count, job_event, interval, stale_seconds, logger, started_at):
        super().__init__(daemon=True)
        self.job_db = job_db
        self.node_id = node_id
        self.host = host
        self.worker_count = worker_count
        self.job_event = job_event
        self.interval = interval
        self.stale_seconds = stale_seconds
        self.log = logger
        self.started_at = started_at
        self.running = True
        self._stop_event = threading.Event()

    def stop(self):
        self.running = False
        self._stop_event.set()

    def run(self):
        if not isinstance(self.job_db, PostgreSQLJobDatabase):
            return  # Heartbeat only meaningful for the shared PG backend
        while self.running:
            try:
                self.job_db.heartbeat(self.node_id, self.host, self.worker_count, self.started_at)
                recovered = self.job_db.recover_stale_nodes(self.stale_seconds)
                for stale_id, job_count in recovered:
                    self.log.warning("Recovered %d jobs from stale node %s" % (job_count, stale_id))
                if any(job_count > 0 for _, job_count in recovered):
                    self.job_event.set()  # Wake workers to pick up requeued jobs
            except Exception:
                self.log.exception("Heartbeat error")
            self._stop_event.wait(timeout=self.interval)


class ScannerThread(threading.Thread):
    """Periodically scans configured directories for new media files and queues them.

    Each entry in scan_paths may specify:
      - path       (required) directory to scan
      - interval   seconds between scans (default: 3600)
      - rewrite_from / rewrite_to   path prefix substitution applied before
                   submitting jobs, e.g. scan /mnt/local/Media but submit
                   paths as /mnt/unionfs/Media so config matching works.
    """

    def __init__(self, scan_paths, job_db, job_event, path_config_manager, logger):
        super().__init__(daemon=True)
        self.scan_paths = scan_paths  # list of dicts from daemon.json
        self.job_db = job_db
        self.job_event = job_event
        self.path_config_manager = path_config_manager
        self.log = logger
        self.running = True
        self._stop_event = threading.Event()
        # Per-entry next-run timestamps so each path has its own schedule.
        self._next_run = {}

    def stop(self):
        self.running = False
        self._stop_event.set()

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
                            self.job_event.set()
                    except Exception:
                        self.log.exception("Scanner error for path: %s" % path)
                    self._next_run[path] = time.monotonic() + interval
                next_wake = min(next_wake, self._next_run[path])
            sleep_for = max(0, next_wake - time.monotonic())
            self._stop_event.wait(timeout=sleep_for)

    def _scan(self, entry):
        scan_dir = entry.get("path", "")
        rewrite_from = entry.get("rewrite_from", "")
        rewrite_to = entry.get("rewrite_to", "")

        if not scan_dir or not os.path.isdir(scan_dir):
            self.log.warning("Scanner: path does not exist or is not a directory: %s" % scan_dir)
            return 0

        allowed = self.path_config_manager.media_extensions
        candidates = []
        for root, dirs, files in os.walk(scan_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if fname.startswith("."):
                    continue
                if os.path.splitext(fname)[1].lower() in allowed:
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
        self.workers = []
        self.job_event = threading.Event()

        # Check for pending jobs from previous run
        pending = job_db.pending_count()
        if pending > 0:
            logger.info("Found %d pending jobs from previous run" % pending)
            self.job_event.set()

        # Start worker threads
        for i in range(worker_count):
            worker = ConversionWorker(
                worker_id=i + 1,
                job_db=job_db,
                job_event=self.job_event,
                path_config_manager=path_config_manager,
                config_log_manager=config_log_manager,
                config_lock_manager=config_lock_manager,
                logger=logger,
                ffmpeg_dir=ffmpeg_dir,
            )
            worker.start()
            self.workers.append(worker)
            logger.debug("Started worker thread %d" % (i + 1))

        # Start heartbeat thread (only does real work with PostgreSQL backend)
        self.heartbeat_thread = HeartbeatThread(
            job_db=job_db,
            node_id=self.node_id,
            host=server_address[0],
            worker_count=worker_count,
            job_event=self.job_event,
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
            job_event=self.job_event,
            path_config_manager=path_config_manager,
            logger=logger,
        )
        self.scanner_thread.start()

    def shutdown(self):
        self.logger.info("Shutting down...")

        # Stop workers
        for worker in self.workers:
            worker.stop()
        self.heartbeat_thread.stop()
        self.scanner_thread.stop()

        # Mark this node offline in the cluster table on clean shutdown
        if isinstance(self.job_db, PostgreSQLJobDatabase):
            try:
                self.job_db.mark_node_offline(self.node_id)
            except Exception:
                pass

        # Wait for workers
        for worker in self.workers:
            worker.join(timeout=5)
        self.heartbeat_thread.join(timeout=5)
        self.scanner_thread.join(timeout=5)

        super().shutdown()


def main():
    parser = argparse.ArgumentParser(description="SMA-NG Daemon - HTTP webhook server for media conversion")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8585, help="Port to listen on (default: 8585)")
    parser.add_argument("--workers", type=int, default=2, help="Number of worker threads (default: 2)")
    parser.add_argument("-d", "--daemon-config", help="Path to daemon.json config file (path mappings)")
    parser.add_argument("--logs-dir", default=LOGS_DIR, help="Directory for per-config log files (default: logs/)")
    parser.add_argument("--db", default=DATABASE_PATH, help="Path to SQLite database (default: config/daemon.db)")
    parser.add_argument(
        "--db-url", help="PostgreSQL connection URL for distributed multi-node operation (e.g. postgresql://user:pass@host/sma). When set, --db is ignored and all nodes share this database."
    )
    parser.add_argument(
        "--ffmpeg-dir", help="Directory containing ffmpeg and ffprobe binaries. Prepended to PATH for each conversion subprocess. If omitted, relies on PATH already containing the binaries."
    )
    parser.add_argument("--heartbeat-interval", type=int, default=30, help="Seconds between cluster heartbeat updates (default: 30). Only used with --db-url.")
    parser.add_argument(
        "--stale-seconds", type=int, default=120, help="Seconds without a heartbeat before a node is declared stale and its running jobs are requeued (default: 120). Only used with --db-url."
    )
    parser.add_argument("--api-key", help="API key for authentication (or set SMA_DAEMON_API_KEY env var)")

    args = parser.parse_args()

    log.info("SMA-NG Daemon starting...")
    log.info("Python %s" % sys.version)

    # Initialize managers
    config_log_manager = ConfigLogManager(args.logs_dir)
    config_lock_manager = ConfigLockManager(logger=log)
    path_config_manager = PathConfigManager(args.daemon_config, logger=log)

    # Determine API key (priority: CLI arg > env var > config file)
    api_key = args.api_key or os.environ.get("SMA_DAEMON_API_KEY") or path_config_manager.api_key

    # Determine database (priority: CLI --db-url > env var > config file > SQLite fallback)
    db_url = args.db_url or os.environ.get("SMA_DAEMON_DB_URL") or path_config_manager.db_url

    # Determine FFmpeg directory (priority: CLI --ffmpeg-dir > env var > config file)
    ffmpeg_dir = args.ffmpeg_dir or os.environ.get("SMA_DAEMON_FFMPEG_DIR") or path_config_manager.ffmpeg_dir
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
        log.info("  GET  /jobs/<id>    - Get specific job")
        log.info("  GET  /configs      - Show config mappings and status")
        log.info("  GET  /stats        - Job statistics")
        log.info("  POST /cleanup      - Remove old jobs (?days=30)")
        log.info("  GET  /scan         - Check unscanned paths (?path=... for small lists)")
        log.info("  POST /scan/filter  - Check unscanned paths (JSON body for large lists)")
        log.info("  POST /scan/record  - Record paths as scanned")
        log.info("")
        log.info("Ready to accept connections.")

        def _shutdown(signum, frame):
            log.info("Received signal %d, shutting down..." % signum)
            # shutdown() is blocking — run in a thread so the signal handler returns
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        server.serve_forever()

    except Exception as e:
        log.exception("Server error: %s" % e)
        sys.exit(1)


if __name__ == "__main__":
    main()
