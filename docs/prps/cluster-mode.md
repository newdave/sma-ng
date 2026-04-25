# PRP: Cluster Mode — Multi-Node Management (Phase 1)

## Discovery Summary

### Initial Task Analysis

Users running multiple sma-ng daemon instances share a PostgreSQL database for job
queuing but have no coordinated management layer. Three concrete pain points: occasional
job duplication, no cross-node log visibility, and config drift between nodes. A
brainstorming session produced a full architectural design.

### User Clarifications Received

- **Question**: `drain`/`pause`/`resume` command semantics.
- **Answer**: `drain` = stop accepting new jobs, finish active ones, stay online but idle.
  `pause` = workers sleep (no new jobs), `resume` = undo pause.
- **Impact**: `drain` is a new pool mode, not a graceful-shutdown alias. Requires a
  separate `set_drain_mode()` method on `WorkerPool` distinct from the existing `drain()`
  join-based shutdown helper.

- **Question**: `node_commands` table vs. existing `pending_command` column.
- **Answer**: Add `node_commands` table with full status lifecycle for auditability and
  multi-command support.
- **Impact**: `send_node_command()` in `db.py` is rewritten to `INSERT INTO node_commands`
  rather than updating `pending_command` on `cluster_nodes`. The `pending_command` column
  remains in place for backwards compatibility but is no longer written to.

- **Question**: Include `cluster_config` (centralized DB base config) in Phase 1?
- **Answer**: No — defer to Phase 2. Phase 1 is node registry, command channel, and log
  aggregation only.

### Missing Requirements Identified (resolved by preflight)

- `cluster_nodes` table already exists with a richer schema than the brainstorm proposed.
  Phase 1 adds only two new columns (`version`, `hwaccel`) via `ADD COLUMN IF NOT EXISTS`.
- `restart` and `shutdown` are already implemented end-to-end. Phase 1 adds `drain`,
  `pause`, `resume` and migrates all commands to the new `node_commands` table.
- `resolve_node_id()` returns hostname/env-var today. Phase 1 upgrades it to UUID
  generation with `sma-ng.yml` persistence.
- No existing log DB table or handler. Phase 1 adds both.

---

## Goal

Implement Phase 1 of sma-ng cluster mode:

1. Unique UUID-based node identity persisted in `sma-ng.yml`
2. `version` and `hwaccel` columns added to `cluster_nodes`
3. `node_commands` table replacing `pending_command` column for command dispatch
4. `drain`, `pause`, `resume` node commands wired end-to-end
5. `logs` table + PostgreSQL log handler with configurable TTL cleanup
6. Global log viewer in admin web UI (filterable by node/level)
7. Node action buttons for `drain`/`pause`/`resume` in admin web UI

---

## Why

- Eliminates occasional job duplication caused by non-unique node identities in PostgreSQL
  claiming queries.
- Provides operators a single pane of glass: any node's web UI shows all nodes, their
  status, and aggregated logs.
- Enables fleet management actions (drain before maintenance, pause during incidents,
  resume after) without SSH access to individual nodes.
- Lays the foundation for Phase 2 (centralized config, automatic node expiry, log
  archival).

---

## What

### User-Visible Behaviour

- Any node's admin UI shows a Cluster tab with: hostname, hwaccel, version, status, last
  seen, uptime, and action buttons (drain/pause/resume/restart/shutdown) for every node.
- A global log viewer shows log entries from all nodes, filterable by node and level,
  paginated.
- Logs older than `log_ttl_days` are purged automatically (each node runs idempotent
  cleanup on every heartbeat tick).
- Issuing `drain` to a node causes it to finish active jobs then sit idle; the UI reflects
  `draining` status. Issuing `resume` returns it to `active`.
- Issuing `pause` freezes worker job pickup immediately; `resume` restores it.

### Success Criteria

- [ ] All nodes sharing a PostgreSQL instance use unique UUIDs — no duplication from
  identity collisions.
- [ ] `drain`, `pause`, `resume`, `restart`, `shutdown` commands all reachable from admin
  UI and stored in `node_commands` with status lifecycle.
- [ ] Log entries from all cluster nodes appear in the unified log viewer within one
  heartbeat interval.
- [ ] TTL cleanup deletes logs older than `log_ttl_days`; cleanup runs on every heartbeat.
- [ ] Single-node SQLite deployments are completely unaffected (all cluster code paths
  gated on `job_db.is_distributed`).
- [ ] All existing tests pass. New tests cover: UUID generation/persistence, node_commands
  polling, drain/pause/resume flag behaviour, DB log handler, TTL cleanup.

---

## All Needed Context

### Research Phase Summary

- **Codebase patterns found**: All cluster infrastructure extensions have clear existing
  patterns in `db.py`, `threads.py`, `worker.py`, `handler.py`. No new frameworks needed.
- **External research needed**: No. All patterns (psycopg2 connection pool, threading.Event,
  Python logging.Handler, ruamel.yaml round-trip) are already used in the codebase.
- **Knowledge gaps**: None blocking. Gotchas documented below.

### Documentation & References

```yaml
- file: resources/daemon/db.py
  why: >
    Extend _init_db() for new tables/columns. Extend heartbeat() to include version+hwaccel
    in upsert. Add poll_node_command(), ack_node_command(), insert_log(), cleanup_old_logs(),
    get_logs() methods. Rewrite send_node_command() to INSERT into node_commands. Study
    _conn() context manager pattern — ALL new DB methods must use it.

- file: resources/daemon/threads.py
  why: >
    Extend HeartbeatThread.__init__ to accept version, hwaccel, log_ttl_days. Replace
    pending_command string check in run() with node_commands poll. Add drain/pause/resume
    handling. Add log TTL cleanup call.

- file: resources/daemon/worker.py
  why: >
    Add _drain_mode (Event) and _pause_mode (Event) to WorkerPool. Add set_drain_mode(),
    clear_drain_mode(), set_paused(), clear_paused() methods. Wire into ConversionWorker.run()
    inner loop. CRITICAL: preserve existing drain(timeout) method — it is used by shutdown.

- file: resources/daemon/handler.py
  why: >
    Extend _post_admin_node_action() to handle drain/pause/resume. Add GET /cluster/logs
    endpoint. Add Alpine.js log viewer section to admin.html. Pattern for HTML injection:
    see _get_admin() lines 423-425.

- file: resources/daemon/constants.py
  why: >
    Add module-level _node_id_cache and set_node_id_cache(). Modify resolve_node_id() to
    check cache before hostname fallback.

- file: resources/daemon/config.py
  why: >
    PathConfigManager.load_config() must: read node_id from sma-ng.yml daemon section,
    generate UUID if absent, write it back, call set_node_id_cache(). Add log_ttl_days
    parsing (int, default 30). Study _config_file attribute (may be None).

- file: resources/daemon/server.py
  why: >
    _validate_hwaccel() must return a string (e.g. "qsv", "nvenc", "vaapi",
    "videotoolbox", "") instead of None. Pass detected value to HeartbeatThread.

- file: resources/yamlconfig.py
  why: >
    Study write() function. GOTCHA: it converts CommentedMap to plain dict, losing comments.
    For node_id write-back, open file directly with ruamel.yaml round-trip mode to preserve
    comments. Only write when node_id key is absent (first-start case).

- file: setup/sma-ng.yml.sample
  why: Add node_id and log_ttl_days fields to daemon: section with explanatory comments.

- file: resources/admin.html
  why: >
    Study Alpine.js adminPage() component structure, authHeaders() helper, existing node
    table. Add Cluster tab and log viewer extending the same component.

- file: tests/conftest.py
  why: Study job_db fixture (PostgreSQL skip pattern) and daemon_log fixture for new tests.

- file: tests/test_threads.py
  why: Mirror existing HeartbeatThread test patterns for new command-poll tests.

- file: tests/test_worker.py
  why: Mirror WorkerPool/ConversionWorker test helpers for drain/pause/resume tests.
```

### Current Codebase Tree (cluster-relevant)

```text
resources/daemon/
├── __init__.py
├── config.py          # PathConfigManager, ConfigLogManager, ConfigLockManager
├── constants.py       # resolve_node_id(), SCRIPT_DIR, STATUS_* constants
├── context.py         # JobContextFilter
├── db.py              # PostgreSQLJobDatabase — cluster_nodes, job queue
├── docs_ui.py         # Docs HTML serving
├── handler.py         # WebhookHandler, _post_admin_node_action(), HTML helpers
├── routes.py          # Route registration dicts
├── server.py          # DaemonServer, _validate_hwaccel()
├── threads.py         # HeartbeatThread, ScannerThread, RecycleBinCleanerThread
└── worker.py          # WorkerPool, ConversionWorker

resources/
├── admin.html         # Admin web UI (Alpine.js + Tailwind)
├── dashboard.html     # Dashboard UI
├── log.py             # getLogger(), JSONFormatter, JobContextFilter
└── yamlconfig.py      # load(), write() with ruamel.yaml

setup/
└── sma-ng.yml.sample  # Canonical config sample
```

### Desired Codebase Tree (additions)

```text
resources/daemon/
└── db_log_handler.py  # NEW: PostgreSQLLogHandler(logging.Handler)

tests/
└── test_cluster.py    # NEW: UUID, node_commands, logs table, TTL tests
```

### Known Gotchas

```python
# CRITICAL: WorkerPool.drain(timeout) already exists as a join-based shutdown helper.
# The cluster "drain" command is a DIFFERENT concept.
# New pool methods must be named set_drain_mode() / clear_drain_mode() to avoid collision.

# CRITICAL: resolve_node_id() is called BEFORE PathConfigManager loads (in
# PostgreSQLJobDatabase.__init__). Use a module-level cache in constants.py:
#   _node_id_cache: str | None = None
#   set_node_id_cache(value)  <- called by PathConfigManager after UUID generation
#   resolve_node_id() checks cache first, then falls back to hostname.

# CRITICAL: ruamel.yaml write() in yamlconfig.py converts CommentedMap to plain dict,
# stripping all YAML comments. For node_id write-back, open the file directly with
# ruamel.yaml round-trip mode (typ="rt") to preserve existing comments. Accept comment
# loss only if sma-ng.yml does not yet exist (first-start case).

# CRITICAL: pending_command column on cluster_nodes is still present on existing
# deployments. Do NOT drop it. Remove only the RETURNING + clear logic from heartbeat().
# The column stays null going forward.

# CRITICAL: node_commands poll query must use FOR UPDATE SKIP LOCKED to be safe:
#   SELECT * FROM node_commands WHERE node_id = %s AND status = 'pending'
#   ORDER BY issued_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED

# CRITICAL: All cluster code paths must be gated on job_db.is_distributed. SQLite
# single-node setups must pass through unmodified.

# CRITICAL: PostgreSQLLogHandler.emit() must NEVER raise. Swallow all exceptions.
# DB errors in the log handler must not cause recursive logging.

# CRITICAL: _validate_hwaccel() currently returns None. Refactor to return a string
# ("qsv", "nvenc", "vaapi", "videotoolbox", or "") so HeartbeatThread can store it.

# GOTCHA: importlib.metadata.version("sma-ng") returns the installed package version,
# not pyproject.toml directly. Use with try/except ImportError fallback to "unknown".

# GOTCHA: threading.Event.wait() blocks; use it for pause gate in ConversionWorker.run().
# When paused, workers call pause_event.wait() (blocking) instead of spinning.

# GOTCHA: The /cluster/logs endpoint must NOT be added to PUBLIC_ENDPOINTS in handler.py.
# It requires authentication like all other /admin/* routes.
```

---

## Implementation Blueprint

### Data Models

```sql
-- Migration additions to _init_db() in db.py
-- Add to cluster_nodes (idempotent):
ALTER TABLE cluster_nodes ADD COLUMN IF NOT EXISTS version TEXT;
ALTER TABLE cluster_nodes ADD COLUMN IF NOT EXISTS hwaccel TEXT;

-- New table: node_commands
CREATE TABLE IF NOT EXISTS node_commands (
    id          SERIAL PRIMARY KEY,
    node_id     TEXT NOT NULL,
    command     TEXT NOT NULL,
    issued_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status      TEXT NOT NULL DEFAULT 'pending',
    issued_by   TEXT
);
CREATE INDEX IF NOT EXISTS idx_node_commands_node_pending
    ON node_commands (node_id, status)
    WHERE status = 'pending';

-- New table: logs
CREATE TABLE IF NOT EXISTS logs (
    id          BIGSERIAL PRIMARY KEY,
    node_id     TEXT NOT NULL,
    level       TEXT NOT NULL,
    logger      TEXT,
    message     TEXT NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_logs_node_ts ON logs (node_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs (timestamp DESC);
```

```python
# constants.py additions
_node_id_cache: str | None = None

def set_node_id_cache(value: str) -> None:
    global _node_id_cache
    _node_id_cache = value

def resolve_node_id() -> str:
    if _node_id_cache:
        return _node_id_cache
    return os.environ.get("SMA_NODE_NAME", "").strip() or socket.gethostname()
```

```python
# config.py: PathConfigManager additions
@property
def log_ttl_days(self) -> int: ...  # default 30

# In load_config(), after reading YAML:
node_id = daemon_cfg.get("node_id") or None
if not node_id:
    node_id = str(uuid.uuid4())
    _write_node_id_to_yaml(config_file, node_id)
set_node_id_cache(node_id)
self._node_id = node_id
self._log_ttl_days = int(daemon_cfg.get("log_ttl_days", 30))
```

```python
# worker.py: WorkerPool additions
self._drain_mode = threading.Event()   # set = draining (no new jobs accepted)
self._pause_mode = threading.Event()   # set = paused (workers block)

def set_drain_mode(self) -> None:
    self._drain_mode.set()

def clear_drain_mode(self) -> None:
    self._drain_mode.clear()

def set_paused(self) -> None:
    self._pause_mode.set()

def clear_paused(self) -> None:
    self._pause_mode.clear()
    for w in self._workers:
        w.job_event.set()  # wake sleeping workers

# ConversionWorker.run() inner loop — insert BEFORE claim_next_job():
if self.pool._pause_mode.is_set():
    self.pool._pause_mode.wait()  # block until resumed
    continue
if self.pool._drain_mode.is_set():
    break  # exit inner job-claim loop; worker goes idle
```

### List of Tasks

```yaml
Task 1 — Schema migration in db.py:
  MODIFY resources/daemon/db.py:
    - FIND: "_init_db" method
    - ADD after existing ALTER TABLE migrations:
      - ALTER TABLE cluster_nodes ADD COLUMN IF NOT EXISTS version TEXT
      - ALTER TABLE cluster_nodes ADD COLUMN IF NOT EXISTS hwaccel TEXT
      - CREATE TABLE IF NOT EXISTS node_commands (see schema above)
      - CREATE INDEX IF NOT EXISTS idx_node_commands_node_pending ...
      - CREATE TABLE IF NOT EXISTS logs (see schema above)
      - CREATE INDEX IF NOT EXISTS idx_logs_node_ts ...
      - CREATE INDEX IF NOT EXISTS idx_logs_ts ...
    - MODIFY heartbeat() upsert to include version and hwaccel in SET clause and
      add them as parameters
    - REMOVE the RETURNING pending_command clause and the clear-pending_command UPDATE
      from heartbeat(); heartbeat() now returns None
    - REWRITE send_node_command() to INSERT INTO node_commands instead of UPDATE
      cluster_nodes SET pending_command
    - ADD poll_node_command(node_id) -> dict | None: SELECT ... FOR UPDATE SKIP LOCKED,
      returns first pending command row or None
    - ADD ack_node_command(cmd_id, status): UPDATE node_commands SET status=... WHERE id=...
    - ADD insert_logs(records: list[dict]): bulk INSERT INTO logs
    - ADD cleanup_old_logs(days: int) -> int: DELETE FROM logs WHERE timestamp < NOW() -
      make_interval(days => %s)
    - ADD get_logs(node_id=None, level=None, limit=100, offset=0) -> list[dict]: SELECT
      FROM logs with optional WHERE filters, ORDER BY timestamp DESC

Task 2 — Node identity in constants.py:
  MODIFY resources/daemon/constants.py:
    - ADD module-level _node_id_cache: str | None = None
    - ADD set_node_id_cache(value: str) function
    - MODIFY resolve_node_id() to check _node_id_cache first before env/hostname fallback

Task 3 — UUID persistence in config.py:
  MODIFY resources/daemon/config.py:
    - ADD import uuid
    - ADD import ruamel.yaml (already a project dependency via yamlconfig.py)
    - ADD private _write_node_id_to_yaml(config_file: str, node_id: str) function:
      open with ruamel.yaml round-trip mode, set data["daemon"]["node_id"] = node_id,
      write back atomically (write to .tmp, os.replace)
    - MODIFY _parse_config_data() to extract node_id and log_ttl_days (int, default 30)
    - MODIFY _apply_config_data() to store self._node_id and self._log_ttl_days
    - ADD logic to generate UUID if node_id is absent and write it back
    - AFTER storing node_id, call set_node_id_cache(self._node_id)
    - HANDLE _config_file is None case gracefully (fall back to hostname, no write)

Task 4 — hwaccel detection return value in server.py:
  MODIFY resources/daemon/server.py:
    - MODIFY _validate_hwaccel() to return str: the first detected hwaccel keyword
      ("qsv", "nvenc", "vaapi", "videotoolbox") or "" for software-only
    - MODIFY the call site in daemon.py (or server startup) to capture the return value
    - ADD hwaccel as a parameter to HeartbeatThread construction, passing detected value

Task 5 — HeartbeatThread command polling in threads.py:
  MODIFY resources/daemon/threads.py:
    - ADD version, hwaccel, log_ttl_days parameters to HeartbeatThread.__init__
    - MODIFY run() to replace pending_command string check with:
        cmd = self.job_db.poll_node_command(self.node_id)
        if cmd:
            self._execute_command(cmd)
    - ADD _execute_command(cmd: dict) method:
        self.job_db.ack_node_command(cmd["id"], "executing")
        try:
            if cmd["command"] == "drain":
                self.server.worker_pool.set_drain_mode()
                # update node status to "draining" via heartbeat or direct DB call
            elif cmd["command"] == "pause":
                self.server.worker_pool.set_paused()
            elif cmd["command"] == "resume":
                self.server.worker_pool.clear_paused()
                self.server.worker_pool.clear_drain_mode()
            elif cmd["command"] == "restart":
                threading.Thread(target=self.server.graceful_restart, daemon=True).start()
                return  # heartbeat loop exits
            elif cmd["command"] == "shutdown":
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self.job_db.ack_node_command(cmd["id"], "done")
        except Exception:
            self.job_db.ack_node_command(cmd["id"], "failed")
    - ADD log TTL cleanup call after recover_stale_nodes():
        if self.log_ttl_days > 0:
            self.job_db.cleanup_old_logs(self.log_ttl_days)
    - MODIFY heartbeat() call to pass version and hwaccel

Task 6 — WorkerPool drain/pause in worker.py:
  MODIFY resources/daemon/worker.py:
    - ADD _drain_mode = threading.Event() and _pause_mode = threading.Event() to WorkerPool
    - ADD set_drain_mode(), clear_drain_mode(), set_paused(), clear_paused() methods as
      described in Data Models above
    - Give each ConversionWorker a reference to its parent WorkerPool (self.pool)
    - MODIFY ConversionWorker.run() inner while loop:
        BEFORE claim_next_job():
          - if pool._pause_mode.is_set(): pool._pause_mode.wait(); continue
          - if pool._drain_mode.is_set(): break
    - PRESERVE existing drain(timeout) method unchanged

Task 7 — DB log handler in db_log_handler.py:
  CREATE resources/daemon/db_log_handler.py:
    - MIRROR: class PostgreSQLLogHandler(logging.Handler)
    - __init__(self, db, node_id, batch_size=50):
        self._db = db, self._node_id = node_id, self._batch_size = batch_size
        self._batch = [], self._lock = threading.Lock()
    - emit(record: logging.LogRecord):
        Never raise. Append formatted record to self._batch.
        If len >= batch_size, call flush().
    - flush():
        Swap batch under lock. Call self._db.insert_logs(batch).
        Swallow all exceptions.
    - close():
        flush() remaining records, then super().close()

Task 8 — Wire DB log handler in daemon.py:
  MODIFY daemon.py (project root):
    - After job_db and path_config_manager are initialised:
        from resources.daemon.db_log_handler import PostgreSQLLogHandler
        if job_db.is_distributed:
            db_handler = PostgreSQLLogHandler(job_db, path_config_manager.node_id)
            db_handler.setLevel(logging.DEBUG)
            logging.getLogger("DAEMON").addHandler(db_handler)

Task 9 — Admin UI: drain/pause/resume actions + log viewer in handler.py and admin.html:
  MODIFY resources/daemon/handler.py:
    - EXTEND _post_admin_node_action() to handle "drain", "pause", "resume" in the
      action dispatch chain (same pattern as "restart"/"shutdown")
    - ADD _get_cluster_logs(path, query) handler:
        Parse ?node_id=, ?level=, ?limit= (default 100), ?offset= (default 0)
        Call self.job_db.get_logs(...)
        Return JSON response
    - REGISTER _get_cluster_logs at GET /cluster/logs in routes.py _get_routes()
      (NOT in PUBLIC_ENDPOINTS)

  MODIFY resources/admin.html:
    - ADD "Cluster Logs" section to the adminPage() Alpine.js component:
        Data properties: clusterLogs=[], logsNodeFilter="", logsLevelFilter="",
          logsLoading=false, logsError=""
        Method loadClusterLogs(): fetch /cluster/logs with query params, populate
          clusterLogs
    - ADD HTML section after existing node grid:
        Log level filter dropdown (ALL/DEBUG/INFO/WARNING/ERROR)
        Node filter dropdown (populated from clusterNodes list)
        Log table: timestamp, node_id, level, logger, message
        Pagination: offset/limit controls
    - ADD drain/pause/resume buttons to each node row in the node grid following the
      same button pattern as existing restart/shutdown buttons

Task 10 — sma-ng.yml.sample update:
  MODIFY setup/sma-ng.yml.sample:
    - ADD to daemon: section:
        # node_id is auto-generated as a UUID on first start and persisted here.
        # Override only if you need a human-readable cluster identity.
        node_id: null
        # Number of days to retain cluster log entries in PostgreSQL. 0 disables cleanup.
        log_ttl_days: 30

Task 11 — Tests:
  CREATE tests/test_cluster.py:
    - Test UUID generation when node_id absent from config (mock yamlconfig write)
    - Test resolve_node_id() returns cached UUID after set_node_id_cache()
    - Test poll_node_command() returns None when no pending commands
    - Test poll_node_command() returns oldest pending command (requires TEST_DB_URL)
    - Test ack_node_command() updates status correctly
    - Test insert_logs() and get_logs() with node/level filters
    - Test cleanup_old_logs() deletes only records older than TTL
    - Test PostgreSQLLogHandler.emit() buffers and flushes correctly
    - Test PostgreSQLLogHandler.emit() swallows DB exceptions without raising

  EXTEND tests/test_threads.py:
    - Test HeartbeatThread._execute_command("drain") calls worker_pool.set_drain_mode()
    - Test HeartbeatThread._execute_command("pause") calls worker_pool.set_paused()
    - Test HeartbeatThread._execute_command("resume") calls both clear methods
    - Test command ack status "done" on success, "failed" on exception

  EXTEND tests/test_worker.py:
    - Test ConversionWorker does not claim jobs when _drain_mode is set
    - Test ConversionWorker blocks on _pause_mode.wait() when _pause_mode is set
    - Test WorkerPool.set_paused() + clear_paused() wakes workers

Task 12 — Documentation:
  MODIFY docs/daemon.md:
    - ADD "Cluster Mode" section covering:
        - node_id UUID auto-generation and sma-ng.yml persistence
        - Supported node commands and their semantics
        - Cluster tab in admin UI
        - Log aggregation and TTL configuration
        - Graceful degradation for SQLite single-node setups
```

### Per-Task Pseudocode

```python
# Task 1 — heartbeat() method update (db.py)
# PATTERN: follows existing heartbeat upsert (db.py lines 404-448)
# REMOVE: RETURNING pending_command and the subsequent UPDATE to clear it
# ADD: version, hwaccel to the upsert SET clause

def heartbeat(self, node_id, host, workers, started_at, version=None, hwaccel=None):
    with self._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO cluster_nodes (node_id, host, workers, last_seen, started_at,
                    status, approval_status, version, hwaccel)
                VALUES (%s, %s, %s, NOW(), %s, 'online', 'pending', %s, %s)
                ON CONFLICT (node_id) DO UPDATE SET
                    host = EXCLUDED.host,
                    workers = EXCLUDED.workers,
                    last_seen = NOW(),
                    status = CASE
                        WHEN cluster_nodes.status IN ('draining', 'paused') THEN cluster_nodes.status
                        ELSE 'online'
                    END,
                    version = COALESCE(EXCLUDED.version, cluster_nodes.version),
                    hwaccel = COALESCE(EXCLUDED.hwaccel, cluster_nodes.hwaccel)
            """, (node_id, host, workers, started_at, version, hwaccel))
    # NOTE: no longer returns pending_command


# Task 3 — _write_node_id_to_yaml (config.py)
# PATTERN: atomic write using tmp file + os.replace
def _write_node_id_to_yaml(config_file: str, node_id: str) -> None:
    from ruamel.yaml import YAML
    yaml = YAML(typ="rt")
    yaml.width = 120
    with open(config_file) as f:
        data = yaml.load(f)
    if "daemon" not in data:
        data["daemon"] = {}
    data["daemon"]["node_id"] = node_id
    tmp = config_file + ".tmp"
    with open(tmp, "w") as f:
        yaml.dump(data, f)
    os.replace(tmp, config_file)


# Task 5 — _execute_command (threads.py)
# CRITICAL: restart/shutdown must return from run() loop after spawning thread
def _execute_command(self, cmd: dict) -> bool:
    """Returns True if the heartbeat loop should exit (restart/shutdown)."""
    cmd_id = cmd["id"]
    command = cmd["command"]
    self.job_db.ack_node_command(cmd_id, "executing")
    try:
        if command == "drain":
            self.server.worker_pool.set_drain_mode()
        elif command == "pause":
            self.server.worker_pool.set_paused()
        elif command == "resume":
            self.server.worker_pool.clear_paused()
            self.server.worker_pool.clear_drain_mode()
        elif command == "restart":
            threading.Thread(
                target=self.server.graceful_restart, daemon=True
            ).start()
            self.job_db.ack_node_command(cmd_id, "done")
            return True  # caller returns from run()
        elif command == "shutdown":
            threading.Thread(
                target=self.server.shutdown, daemon=True
            ).start()
            self.job_db.ack_node_command(cmd_id, "done")
            return True
        self.job_db.ack_node_command(cmd_id, "done")
    except Exception:
        self.log.exception("Failed to execute command %s", command)
        self.job_db.ack_node_command(cmd_id, "failed")
    return False


# Task 7 — PostgreSQLLogHandler (db_log_handler.py)
class PostgreSQLLogHandler(logging.Handler):
    def __init__(self, db, node_id: str, batch_size: int = 50):
        super().__init__()
        self._db = db
        self._node_id = node_id
        self._batch_size = batch_size
        self._batch: list[dict] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "node_id": self._node_id,
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
            with self._lock:
                self._batch.append(entry)
                if len(self._batch) >= self._batch_size:
                    self._flush_locked()
        except Exception:
            pass  # NEVER raise from emit()

    def flush(self) -> None:
        try:
            with self._lock:
                self._flush_locked()
        except Exception:
            pass

    def _flush_locked(self) -> None:
        if not self._batch:
            return
        batch, self._batch = self._batch, []
        try:
            self._db.insert_logs(batch)
        except Exception:
            pass

    def close(self) -> None:
        self.flush()
        super().close()
```

### Integration Points

```yaml
DATABASE:
  migration: >
    All DDL in PostgreSQLJobDatabase._init_db() using CREATE TABLE IF NOT EXISTS
    and ALTER TABLE ... ADD COLUMN IF NOT EXISTS. No migration runner — changes
    are idempotent and applied on daemon startup.
  indexes:
    - idx_node_commands_node_pending (partial index WHERE status='pending')
    - idx_logs_node_ts (composite on node_id, timestamp DESC)
    - idx_logs_ts (timestamp DESC for cross-node queries)
  existing_column: >
    pending_command column on cluster_nodes remains. Remove only the RETURNING
    + clear logic from heartbeat(). Do not DROP the column.

API/ROUTES:
  add_to: resources/daemon/routes.py _get_routes()
  new_endpoint: GET /cluster/logs
  pattern: see existing route dict entries in routes.py
  auth: must NOT appear in PUBLIC_ENDPOINTS

CONFIG:
  add_to: setup/sma-ng.yml.sample daemon: section
  new_fields: node_id (null), log_ttl_days (30)
  read_in: resources/daemon/config.py _parse_config_data()

WEB_UI:
  modify: resources/admin.html
  pattern: extend existing Alpine.js adminPage() component
  new_data_props: clusterLogs, logsNodeFilter, logsLevelFilter, logsLoading, logsError
  new_methods: loadClusterLogs()
  new_buttons: drain/pause/resume per node row (same style as restart/shutdown)
```

---

## Validation Loop

### Level 1: Syntax & Style

```bash
source venv/bin/activate

# Lint all modified daemon files
ruff check resources/daemon/ resources/admin.html --fix

# Type check (if pyright configured)
pyright resources/daemon/db.py resources/daemon/threads.py \
        resources/daemon/worker.py resources/daemon/constants.py \
        resources/daemon/config.py resources/daemon/db_log_handler.py

# Markdownlint for docs
markdownlint docs/daemon.md
```

### Level 2: Unit Tests

```bash
source venv/bin/activate

# Run without PostgreSQL (all mock-based tests)
python -m pytest tests/test_cluster.py tests/test_threads.py tests/test_worker.py \
    -x -q

# Run with PostgreSQL (integration tests for DB layer)
TEST_DB_URL=postgresql://user:pass@localhost/testdb \
    python -m pytest tests/test_cluster.py -x -q -m "not unit_only"

# Full suite
python -m pytest tests/ -x -q
```

### Final Validation Checklist

- [ ] All 107+ existing tests pass: `python -m pytest tests/ -x -q`
- [ ] New tests cover all tasks above: `python -m pytest tests/test_cluster.py -v`
- [ ] No linting errors: `ruff check resources/daemon/`
- [ ] No type errors: `pyright resources/daemon/`
- [ ] No markdownlint errors: `markdownlint docs/daemon.md`
- [ ] Single-node SQLite daemon starts and processes jobs without error
- [ ] With PostgreSQL: UUID generated on first start, persisted in sma-ng.yml
- [ ] With PostgreSQL: Cluster tab visible in admin UI with all nodes
- [ ] With PostgreSQL: `drain` command issued from UI — node finishes active jobs,
  stops accepting new ones, status shows `draining`
- [ ] With PostgreSQL: `pause` / `resume` round-trip works
- [ ] With PostgreSQL: logs appear in log viewer, filterable by node and level
- [ ] TTL cleanup deletes old logs on next heartbeat tick
- [ ] `docs/daemon.md` updated with Cluster Mode section

---

## Anti-Patterns to Avoid

- ❌ Don't rename or modify `WorkerPool.drain(timeout)` — it is used by graceful shutdown
- ❌ Don't add FK constraints to `node_commands` or `logs` — existing code has none
- ❌ Don't add `/cluster/logs` to `PUBLIC_ENDPOINTS` — it requires auth
- ❌ Don't use `resources.yamlconfig.write()` for node_id write-back — it strips comments
- ❌ Don't raise from `PostgreSQLLogHandler.emit()` — logging handler exceptions cause
  recursive loops
- ❌ Don't gate UUID generation on `is_distributed` — every node needs a unique identity
  even in single-node PostgreSQL deployments
- ❌ Don't embed inline Python in any shell scripts (CLAUDE.md rule)
- ❌ Don't create a new `node_id` if one already exists in `sma-ng.yml`

---

## Task Breakdown Reference

See `docs/tasks/cluster-mode.md` (generated separately) for sprint-ready task cards.

---

## Confidence Score: 9/10

High confidence for one-pass implementation. All extension points are clearly identified
with exact file locations and line-level context. The only uncertainty is the Alpine.js
log viewer pagination UX (no spec beyond "paginated") — implementer should follow the
existing node table UI patterns. DB schema, Python logic, and test patterns are all
fully specified.
