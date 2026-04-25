# Task Breakdown: Cluster Mode — Multi-Node Management (Phase 1)

**Source PRP**: [docs/prps/cluster-mode.md](../prps/cluster-mode.md)
**Feature Branch Target**: `main`
**Overall Complexity**: Complex (12 tasks, 3 phases, multiple integration points)

---

## PRP Analysis Summary

**Feature**: Phase 1 of sma-ng cluster mode — multi-node management via shared PostgreSQL.

**Key Technical Requirements**:

- UUID-based node identity persisted in `sma-ng.yml` via ruamel.yaml round-trip writes
- Two new PostgreSQL tables (`node_commands`, `logs`) and two new columns on `cluster_nodes`
- `node_commands` table replaces the `pending_command` column for all command dispatch
- `drain`, `pause`, `resume` commands wired end-to-end through DB → HeartbeatThread → WorkerPool
- PostgreSQL log handler with batched writes and configurable TTL cleanup
- Admin UI additions: drain/pause/resume buttons per node, cluster log viewer with filters

**Validation Requirements** (from PRP success criteria):

- No identity collisions across nodes sharing PostgreSQL
- All five node commands reachable from admin UI, stored with full status lifecycle
- Logs from all nodes appear in viewer within one heartbeat interval
- TTL cleanup executes on every heartbeat tick
- SQLite single-node deployments are 100% unaffected (all paths gated on `is_distributed`)
- Full existing test suite passes; new tests cover all new code paths

---

## Task Complexity Assessment

**Overall complexity**: Complex

**Integration points**:

- PostgreSQL schema (`db.py` `_init_db`) — DDL changes affect all cluster-aware callers
- Module-level cache in `constants.py` — initialised by `config.py`, consumed by `db.py`
  before `config.py` has run, so ordering and cache-coherency are critical
- `WorkerPool` — two new threading.Event fields; existing `drain(timeout)` method must
  not be touched
- `HeartbeatThread` — replaces existing `pending_command` string result with
  `poll_node_command()` poll; signature change propagates to `server.py`
- `daemon.py` entry point — wires DB log handler after job_db initialisation
- Admin UI (Alpine.js) — two new UI features in the same component

**Technical challenges**:

- Cache ordering: `resolve_node_id()` is called inside `PostgreSQLJobDatabase.__init__`
  which runs before `PathConfigManager.load_config()`. The module-level cache in
  `constants.py` must be set by `config.py` so subsequent DB calls use the UUID.
- `drain` naming collision: `WorkerPool.drain(timeout)` already exists as a join-based
  shutdown helper; new cluster drain must be `set_drain_mode()` / `clear_drain_mode()`.
- ruamel.yaml round-trip: `yamlconfig.write()` strips comments. Node-id write-back
  must open the file directly with `YAML(typ="rt")`.
- Log handler safety: `PostgreSQLLogHandler.emit()` must never raise under any
  circumstances to avoid recursive logging.

---

## Phase Organisation

### Phase 1: Data Layer (Tasks 1–4)

**Objective**: Establish the PostgreSQL schema, node identity plumbing, and hwaccel
detection before any behavioural code changes.

**Deliverables**:

- `_init_db()` creates `node_commands` and `logs` tables; `cluster_nodes` gains
  `version` and `hwaccel` columns
- `resolve_node_id()` is cache-aware
- UUID generated and persisted in `sma-ng.yml` on first start
- `_validate_hwaccel()` returns a string, not None

**Milestone**: Daemon starts against a fresh or existing PostgreSQL DB with new schema;
UUID appears in `sma-ng.yml`; `cluster_nodes` heartbeat row includes `version` and
`hwaccel`.

### Phase 2: Behavioural Layer (Tasks 5–8)

**Objective**: Wire commands and logging through the daemon runtime: HeartbeatThread
polling, WorkerPool drain/pause/resume, DB log handler.

**Deliverables**:

- HeartbeatThread polls `node_commands` instead of reading `pending_command`
- `drain`, `pause`, `resume` commands alter WorkerPool state
- `PostgreSQLLogHandler` buffers and flushes log records to the `logs` table
- Handler wired into the DAEMON logger on startup

**Milestone**: Issuing a `drain` / `pause` / `resume` command via direct DB insert
changes WorkerPool behaviour; DAEMON log lines appear in the `logs` table.

### Phase 3: UI, Config, Tests, Docs (Tasks 9–12)

**Objective**: Surface Phase 2 capabilities through the admin UI, finalise config
sample, provide full test coverage, and update documentation.

**Deliverables**:

- Admin UI: drain/pause/resume buttons per node row; cluster log viewer with filters
- `GET /cluster/logs` API endpoint
- `setup/sma-ng.yml.sample` updated with `node_id` and `log_ttl_days`
- `tests/test_cluster.py` created; `test_threads.py` and `test_worker.py` extended
- `docs/daemon.md` Cluster Mode section

**Milestone**: All 107+ existing tests pass; new test suite passes with and without
`TEST_DB_URL`; cluster log viewer functional in browser.

---

## Detailed Task Breakdown

---

### T-001 — Schema Migration in db.py

**Task ID**: T-001
**Task Name**: Extend PostgreSQL schema with node_commands and logs tables
**Priority**: Critical
**Effort**: M
**Source PRP Document**: [docs/prps/cluster-mode.md](../prps/cluster-mode.md) — Task 1
**Dependencies**: None (foundational; all other tasks depend on this)

#### Context & Background

`PostgreSQLJobDatabase._init_db()` is the single location for all DDL in the project.
New tables and columns are added here as idempotent statements applied on every daemon
startup — no separate migration runner exists.

**As a** daemon operator running multiple nodes
**I need** the PostgreSQL schema extended with `node_commands` and `logs` tables and
`version`/`hwaccel` columns on `cluster_nodes`
**So that** node commands have a full audit lifecycle and log aggregation has a backing
store

#### Dependencies

- **Prerequisite Tasks**: None
- **Parallel Tasks**: T-002 (no shared files)
- **Integration Points**: All other tasks that call new DB methods depend on T-001
  having run `_init_db()` at least once

#### Technical Requirements

- **REQ-1**: When the daemon starts against a PostgreSQL DB, `_init_db()` shall create
  `node_commands` and `logs` tables if they do not exist.
- **REQ-2**: When the daemon starts against an existing DB, the `ALTER TABLE … ADD
  COLUMN IF NOT EXISTS` statements shall be idempotent and not error.
- **REQ-3**: The `pending_command` column on `cluster_nodes` shall remain; it must not
  be dropped.
- **REQ-4**: `heartbeat()` shall include `version` and `hwaccel` in its upsert `SET`
  clause and shall no longer return a `pending_command` value (return type `None`).
- **REQ-5**: `send_node_command()` shall `INSERT INTO node_commands` rather than
  `UPDATE cluster_nodes SET pending_command`.
- **REQ-6**: New methods `poll_node_command()`, `ack_node_command()`, `insert_logs()`,
  `cleanup_old_logs()`, `get_logs()` shall be added to `PostgreSQLJobDatabase`.

**Technical Constraints**:

- All new DB methods must use the `_conn()` context manager pattern (see
  `resources/daemon/db.py` lines 41–52).
- No FK constraints — consistent with existing schema.
- `poll_node_command()` must use `FOR UPDATE SKIP LOCKED`.

#### Files to Modify

```text
resources/daemon/db.py  - _init_db(), heartbeat(), send_node_command(),
                          + poll_node_command(), ack_node_command(),
                          + insert_logs(), cleanup_old_logs(), get_logs()
```

#### Key Implementation Steps

1. **Add DDL to `_init_db()`** — append after the existing `ADD COLUMN IF NOT EXISTS`
   block for `cluster_nodes`: two `ALTER TABLE` statements for `version` and `hwaccel`,
   `CREATE TABLE IF NOT EXISTS node_commands` with its partial index, `CREATE TABLE IF
   NOT EXISTS logs` with its two indexes. See PRP schema block.
2. **Rewrite `heartbeat()`** — add `version=None, hwaccel=None` parameters; add them
   to the upsert `SET` clause using `COALESCE(EXCLUDED.version, cluster_nodes.version)`;
   remove the `RETURNING pending_command` clause and the follow-up `UPDATE` that cleared
   it; change return type to `None`.
3. **Rewrite `send_node_command()`** — replace `UPDATE cluster_nodes SET
   pending_command` with `INSERT INTO node_commands (node_id, command, issued_by)
   VALUES (%s, %s, %s)`.
4. **Add `poll_node_command(node_id)`** — `SELECT … FOR UPDATE SKIP LOCKED LIMIT 1`
   filtered to `status = 'pending'` and ordered by `issued_at ASC`; return dict or None.
5. **Add `ack_node_command(cmd_id, status)`** — `UPDATE node_commands SET status = %s
   WHERE id = %s`.
6. **Add `insert_logs(records)`** — bulk `INSERT INTO logs` using
   `psycopg2.extras.execute_values` or equivalent.
7. **Add `cleanup_old_logs(days)`** — `DELETE FROM logs WHERE timestamp < NOW() -
   make_interval(days => %s)`; return rowcount.
8. **Add `get_logs(node_id, level, limit, offset)`** — `SELECT … ORDER BY timestamp
   DESC` with optional `WHERE` filters; return list of dicts.

#### Code Patterns to Follow

- `_conn()` context manager: `resources/daemon/db.py` lines 41–52
- Existing `ALTER TABLE … ADD COLUMN IF NOT EXISTS` block: `resources/daemon/db.py`
  lines 100–152
- Existing `heartbeat()` upsert: `resources/daemon/db.py` lines approximately 400–448

#### Acceptance Criteria

```gherkin
Scenario 1: Fresh PostgreSQL database
  Given a fresh PostgreSQL database with no sma-ng tables
  When the daemon starts and _init_db() executes
  Then node_commands and logs tables exist
  And cluster_nodes has version and hwaccel columns
  And idx_node_commands_node_pending, idx_logs_node_ts, idx_logs_ts indexes exist

Scenario 2: Existing database migration
  Given a PostgreSQL database with an existing cluster_nodes table lacking version/hwaccel
  When _init_db() executes
  Then the ALTER TABLE statements complete without error
  And existing cluster_nodes rows are unchanged

Scenario 3: heartbeat() no longer returns pending_command
  Given a running node
  When heartbeat() is called
  Then the return value is None
  And the upsert sets version and hwaccel via COALESCE

Scenario 4: send_node_command() writes to node_commands
  Given a cluster with at least one registered node
  When send_node_command(node_id, "drain") is called
  Then a row appears in node_commands with status="pending"
  And cluster_nodes.pending_command remains NULL

Scenario 5: poll_node_command() uses SKIP LOCKED
  Given two concurrent callers polling for the same node's pending command
  When both call poll_node_command() simultaneously
  Then exactly one caller receives the row; the other receives None
```

**Rule-Based Checklist**:

- [ ] `_init_db()` is idempotent on repeated runs
- [ ] `pending_command` column is not dropped
- [ ] `heartbeat()` signature accepts `version` and `hwaccel` kwargs
- [ ] `heartbeat()` returns `None`
- [ ] `poll_node_command()` uses `FOR UPDATE SKIP LOCKED`
- [ ] All new methods use `self._conn()` context manager
- [ ] No FK constraints added

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/db.py --fix
pyright resources/daemon/db.py
TEST_DB_URL=postgresql://user:pass@localhost/testdb \
    python -m pytest tests/test_cluster.py -x -q -k "db or schema"
```

---

### T-002 — Node Identity Cache in constants.py

**Task ID**: T-002
**Task Name**: Add module-level node_id cache to constants.py
**Priority**: Critical
**Effort**: S
**Source PRP Document**: [docs/prps/cluster-mode.md](../prps/cluster-mode.md) — Task 2
**Dependencies**: None

#### Context & Background

`resolve_node_id()` is called inside `PostgreSQLJobDatabase.__init__()` which runs
before `PathConfigManager.load_config()` generates the UUID. A module-level cache
allows `config.py` to push the UUID into `constants.py` after it is generated so
all subsequent calls return the UUID rather than the hostname fallback.

**As a** daemon starting against PostgreSQL
**I need** `resolve_node_id()` to return the UUID after `PathConfigManager` has set it
**So that** all cluster operations (heartbeat, job claims, log entries) use the same
stable identity

#### Dependencies

- **Prerequisite Tasks**: None
- **Parallel Tasks**: T-001, T-003
- **Integration Points**: T-003 calls `set_node_id_cache()` after generating the UUID;
  T-001's `PostgreSQLJobDatabase.__init__` calls `resolve_node_id()` during construction

#### Technical Requirements

- **REQ-1**: A module-level `_node_id_cache: str | None = None` variable shall exist in
  `constants.py`.
- **REQ-2**: `set_node_id_cache(value: str)` shall set `_node_id_cache`.
- **REQ-3**: `resolve_node_id()` shall return `_node_id_cache` when it is set, and fall
  back to the existing `SMA_NODE_NAME` env var / `socket.gethostname()` chain otherwise.

#### Files to Modify

```text
resources/daemon/constants.py  - add _node_id_cache, set_node_id_cache(), modify
                                  resolve_node_id()
```

#### Key Implementation Steps

1. Add `_node_id_cache: str | None = None` at module level.
2. Add `set_node_id_cache(value: str) -> None` with `global _node_id_cache`.
3. Modify `resolve_node_id()` to check `_node_id_cache` first (see PRP pseudocode).

#### Code Patterns to Follow

- Existing `resolve_node_id()`: `resources/daemon/constants.py` lines 16–18

#### Acceptance Criteria

```gherkin
Scenario 1: Cache miss — falls back to hostname
  Given _node_id_cache is None
  When resolve_node_id() is called
  Then the result equals socket.gethostname() (or SMA_NODE_NAME if set)

Scenario 2: Cache hit — returns UUID
  Given set_node_id_cache("550e8400-e29b-41d4-a716-446655440000") has been called
  When resolve_node_id() is called
  Then "550e8400-e29b-41d4-a716-446655440000" is returned

Scenario 3: Cache survives repeated calls
  Given set_node_id_cache() has been called once
  When resolve_node_id() is called multiple times
  Then the same UUID is returned every time
```

**Rule-Based Checklist**:

- [ ] `_node_id_cache` is a module-level variable (not class-level)
- [ ] `set_node_id_cache()` uses `global _node_id_cache`
- [ ] `resolve_node_id()` cache check comes before env-var/hostname fallback
- [ ] Existing `SMA_NODE_NAME` / `socket.gethostname()` fallback is unchanged

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/constants.py --fix
pyright resources/daemon/constants.py
python -m pytest tests/test_cluster.py -x -q -k "resolve_node_id or cache"
```

---

### T-003 — UUID Persistence in config.py

**Task ID**: T-003
**Task Name**: Generate and persist node UUID in sma-ng.yml via PathConfigManager
**Priority**: Critical
**Effort**: M
**Source PRP Document**: [docs/prps/cluster-mode.md](../prps/cluster-mode.md) — Task 3
**Dependencies**: T-002 (must exist before `set_node_id_cache()` can be called)

#### Context & Background

Every daemon node must have a stable unique identity to prevent job duplication in
`SELECT FOR UPDATE SKIP LOCKED` claims. `PathConfigManager.load_config()` is the
correct place to generate and persist the UUID because it runs after the config file
is located but before any DB interaction uses `resolve_node_id()`.

**As a** new daemon node starting for the first time
**I need** a UUID automatically generated and written into `sma-ng.yml`
**So that** the node has a stable identity across restarts and cluster identity is
traceable in `sma-ng.yml`

#### Dependencies

- **Prerequisite Tasks**: T-002 (`set_node_id_cache()` must exist)
- **Parallel Tasks**: T-001, T-004
- **Integration Points**: T-005 (HeartbeatThread receives `version` and `hwaccel` from
  config); T-008 (daemon.py wires log handler using `node_id`)

#### Technical Requirements

- **REQ-1**: When `node_id` is absent or null in `sma-ng.yml`, `load_config()` shall
  generate a UUID4 and write it back to the file atomically.
- **REQ-2**: When `node_id` is already present, it shall be read but NOT overwritten.
- **REQ-3**: The write-back shall use `ruamel.yaml` round-trip mode (`typ="rt"`) to
  preserve existing comments, not `yamlconfig.write()`.
- **REQ-4**: The write-back shall be atomic: write to a `.tmp` file then `os.replace()`.
- **REQ-5**: When `_config_file` is `None`, UUID generation shall proceed but no file
  write occurs; `resolve_node_id()` hostname fallback remains in effect.
- **REQ-6**: `log_ttl_days` (int, default 30) shall be parsed from the `daemon:` section.
- **REQ-7**: After storing `node_id`, `set_node_id_cache(node_id)` shall be called.

**Technical Constraints**:

- `ruamel.yaml` is already a dependency via `resources/yamlconfig.py`; do not add new
  deps.
- Never call `yamlconfig.write()` for node-id write-back — it converts `CommentedMap`
  to plain dict, stripping YAML comments.

#### Files to Modify

```text
resources/daemon/config.py  - add uuid import, add _write_node_id_to_yaml(),
                               extend _parse_config_data() and _apply_config_data(),
                               add node_id + log_ttl_days properties
```

#### Key Implementation Steps

1. Add `import uuid` and `import os` (if not already present) at top of `config.py`.
2. Add `_write_node_id_to_yaml(config_file: str, node_id: str) -> None` private
   function using `YAML(typ="rt")` as shown in PRP pseudocode (atomic tmp + replace).
3. In `_parse_config_data()` (or equivalent config-reading path): extract
   `daemon_cfg.get("node_id")` and `int(daemon_cfg.get("log_ttl_days", 30))`.
4. In `_apply_config_data()`: if `node_id` is absent/null, generate `str(uuid.uuid4())`
   and call `_write_node_id_to_yaml(self._config_file, node_id)` if `_config_file` is
   not None. Store as `self._node_id` and `self._log_ttl_days`.
5. Call `set_node_id_cache(self._node_id)` immediately after storing.
6. Expose `node_id` and `log_ttl_days` as properties.

#### Code Patterns to Follow

- `resources/yamlconfig.py` — study `load()` for ruamel.yaml `YAML(typ="rt")` usage
- Atomic write pattern: write to `.tmp`, then `os.replace(tmp, config_file)`
- Existing `PathConfigManager._config_file` None-guard patterns in `config.py`

#### Acceptance Criteria

```gherkin
Scenario 1: First start — no node_id in file
  Given sma-ng.yml has no node_id key in the daemon section
  When PathConfigManager.load_config() runs
  Then a UUID4 is generated
  And it is written to sma-ng.yml under daemon.node_id
  And set_node_id_cache() is called with that UUID
  And resolve_node_id() returns the UUID

Scenario 2: Subsequent start — node_id already persisted
  Given sma-ng.yml already contains a node_id value
  When PathConfigManager.load_config() runs
  Then the existing UUID is read without modification
  And the file is not rewritten

Scenario 3: No config file (_config_file is None)
  Given _config_file is None (e.g. SMA_CONFIG unset in minimal test setup)
  When load_config() runs
  Then UUID generation proceeds (in-memory)
  And no file write is attempted
  And resolve_node_id() still returns the generated UUID

Scenario 4: Comment preservation
  Given sma-ng.yml contains inline comments above the daemon section
  When node_id is written for the first time
  Then existing YAML comments are preserved in the file

Scenario 5: log_ttl_days default
  Given daemon section does not contain log_ttl_days
  When load_config() runs
  Then path_config_manager.log_ttl_days returns 30
```

**Rule-Based Checklist**:

- [ ] `yamlconfig.write()` is NOT used for node-id write-back
- [ ] Write-back is atomic (`os.replace`)
- [ ] Existing UUID is never overwritten
- [ ] `set_node_id_cache()` called after UUID is resolved
- [ ] `_config_file is None` case handled without exception
- [ ] `log_ttl_days` property exists with default 30

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/config.py --fix
pyright resources/daemon/config.py
python -m pytest tests/test_cluster.py -x -q -k "uuid or node_id or config"
```

---

### T-004 — hwaccel Detection Return Value in server.py

**Task ID**: T-004
**Task Name**: Refactor _validate_hwaccel() to return a string and thread it to HeartbeatThread
**Priority**: High
**Effort**: S
**Source PRP Document**: [docs/prps/cluster-mode.md](../prps/cluster-mode.md) — Task 4
**Dependencies**: T-003 (HeartbeatThread signature changes; must be coordinated)

#### Context & Background

`_validate_hwaccel()` currently returns `None` for all outcomes. HeartbeatThread needs
the detected hardware acceleration method as a string to populate the `hwaccel` column
on `cluster_nodes`.

**As a** cluster operator
**I need** each node's hardware acceleration type recorded in `cluster_nodes`
**So that** the admin UI can show per-node hwaccel capability at a glance

#### Dependencies

- **Prerequisite Tasks**: T-001 (heartbeat upsert accepts `hwaccel`), T-003 (wiring
  context — HeartbeatThread construction happens in server.py)
- **Parallel Tasks**: T-002
- **Integration Points**: T-005 (HeartbeatThread `__init__` gains `hwaccel` parameter)

#### Technical Requirements

- **REQ-1**: `_validate_hwaccel()` shall return the first detected hwaccel keyword
  string: one of `"qsv"`, `"nvenc"`, `"vaapi"`, `"videotoolbox"`, or `""` for
  software-only / detection failure.
- **REQ-2**: The `HeartbeatThread` construction in `DaemonServer` shall pass the
  `hwaccel` return value.

**Technical Constraints**:

- The return-value change must not break any existing caller that discards the result.

#### Files to Modify

```text
resources/daemon/server.py  - _validate_hwaccel() return type str, pass to
                               HeartbeatThread construction
```

#### Key Implementation Steps

1. Modify `_validate_hwaccel()`: replace any `return None` / `return` with
   `return ""` as the default; return the detected keyword string on success.
2. Capture the return value at the call site in `DaemonServer` startup.
3. Pass `hwaccel=detected_hwaccel` when constructing `HeartbeatThread` (T-005 adds the
   parameter to `__init__`).

#### Acceptance Criteria

```gherkin
Scenario 1: hwaccel detected
  Given the host has QSV hardware
  When _validate_hwaccel() is called
  Then it returns "qsv" (not None)

Scenario 2: No hardware acceleration
  Given the host has no supported GPU encoder
  When _validate_hwaccel() is called
  Then it returns "" (empty string, not None)

Scenario 3: HeartbeatThread receives hwaccel
  Given _validate_hwaccel() returns "nvenc"
  When DaemonServer starts HeartbeatThread
  Then HeartbeatThread is constructed with hwaccel="nvenc"
```

**Rule-Based Checklist**:

- [ ] `_validate_hwaccel()` never returns `None`
- [ ] Return value is captured and forwarded to `HeartbeatThread`
- [ ] Software fallback returns `""` not `"software"`

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/server.py --fix
pyright resources/daemon/server.py
python -m pytest tests/ -x -q -k "hwaccel or heartbeat"
```

---

### T-005 — HeartbeatThread Command Polling in threads.py

**Task ID**: T-005
**Task Name**: Replace pending_command string poll with node_commands table poll in HeartbeatThread
**Priority**: Critical
**Effort**: M
**Source PRP Document**: [docs/prps/cluster-mode.md](../prps/cluster-mode.md) — Task 5
**Dependencies**: T-001 (DB methods), T-004 (hwaccel parameter), T-006 (WorkerPool
methods called by `_execute_command`)

#### Context & Background

`HeartbeatThread.run()` currently checks the string return value of `heartbeat()` for
`"restart"` or `"shutdown"`. Phase 1 replaces this with `poll_node_command()` and adds
`drain`, `pause`, `resume` to the command dispatch.

**As a** daemon operator
**I need** HeartbeatThread to poll the `node_commands` table and execute drain/pause/resume
**So that** fleet management commands take effect within one heartbeat interval

#### Dependencies

- **Prerequisite Tasks**: T-001, T-004, T-006
- **Parallel Tasks**: T-007 (different file)
- **Integration Points**: `DaemonServer` constructs `HeartbeatThread`; WorkerPool
  methods called in `_execute_command`

#### Technical Requirements

- **REQ-1**: `HeartbeatThread.__init__` shall accept `version`, `hwaccel`, and
  `log_ttl_days` parameters.
- **REQ-2**: `run()` shall call `poll_node_command(self.node_id)` each tick instead of
  reading the `heartbeat()` return value.
- **REQ-3**: `_execute_command(cmd)` shall ack the command as `"executing"` before
  dispatching, then ack as `"done"` on success or `"failed"` on exception.
- **REQ-4**: `restart` and `shutdown` commands shall spawn a daemon thread and return
  `True` from `_execute_command()` to break the heartbeat loop.
- **REQ-5**: `drain` shall call `worker_pool.set_drain_mode()`.
- **REQ-6**: `pause` shall call `worker_pool.set_paused()`.
- **REQ-7**: `resume` shall call `worker_pool.clear_paused()` AND
  `worker_pool.clear_drain_mode()`.
- **REQ-8**: Log TTL cleanup shall run after `recover_stale_nodes()` when
  `log_ttl_days > 0`.

**Technical Constraints**:

- `_execute_command()` must be gated on `job_db.is_distributed` (via the existing
  `if not self.job_db.is_distributed: return` guard at the top of `run()`).

#### Files to Modify

```text
resources/daemon/threads.py  - HeartbeatThread.__init__(), run(), add
                                _execute_command()
```

#### Key Implementation Steps

1. Add `version`, `hwaccel`, `log_ttl_days` to `HeartbeatThread.__init__`.
2. In `run()`: replace the `command = self.job_db.heartbeat(...)` + string-check block
   with a call to `self.job_db.heartbeat(..., version=self.version,
   hwaccel=self.hwaccel)` (no return value consumed), then `cmd =
   self.job_db.poll_node_command(self.node_id)` + `if cmd: should_exit =
   self._execute_command(cmd); if should_exit: return`.
3. After `recover_stale_nodes()`, add TTL cleanup:
   `if self.log_ttl_days > 0: self.job_db.cleanup_old_logs(self.log_ttl_days)`.
4. Implement `_execute_command(cmd: dict) -> bool` per PRP pseudocode; swallow
   unexpected exceptions with `self.log.exception(...)`.

#### Code Patterns to Follow

- Existing `HeartbeatThread.run()` command dispatch: `resources/daemon/threads.py`
  lines 42–63
- `threading.Thread(target=..., daemon=True).start()` pattern in existing restart/
  shutdown handling

#### Acceptance Criteria

```gherkin
Scenario 1: drain command received
  Given a running HeartbeatThread with a WorkerPool
  When poll_node_command() returns {"id": 1, "command": "drain"}
  Then worker_pool.set_drain_mode() is called
  And ack_node_command(1, "done") is called

Scenario 2: pause command received
  Given a running HeartbeatThread
  When poll_node_command() returns {"id": 2, "command": "pause"}
  Then worker_pool.set_paused() is called
  And ack_node_command(2, "done") is called

Scenario 3: resume clears both modes
  Given the pool is in drain mode and paused
  When poll_node_command() returns {"id": 3, "command": "resume"}
  Then worker_pool.clear_paused() is called
  And worker_pool.clear_drain_mode() is called
  And ack_node_command(3, "done") is called

Scenario 4: failed command acked correctly
  Given worker_pool.set_drain_mode() raises an exception
  When the drain command is dispatched
  Then ack_node_command(cmd_id, "failed") is called

Scenario 5: restart command exits heartbeat loop
  Given poll_node_command() returns a restart command
  When _execute_command() runs
  Then it returns True
  And HeartbeatThread.run() exits
  And server.graceful_restart() is invoked in a daemon thread

Scenario 6: TTL cleanup runs each tick
  Given log_ttl_days = 7
  When the heartbeat tick completes
  Then cleanup_old_logs(7) is called
```

**Rule-Based Checklist**:

- [ ] `heartbeat()` return value is no longer consumed for command dispatch
- [ ] `poll_node_command()` called each tick when `is_distributed`
- [ ] `_execute_command()` acks "executing" before dispatch
- [ ] `restart`/`shutdown` return `True` to break run loop
- [ ] TTL cleanup gated on `log_ttl_days > 0`
- [ ] Exception in command execution logs but does not crash heartbeat loop

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/threads.py --fix
pyright resources/daemon/threads.py
python -m pytest tests/test_threads.py tests/test_cluster.py -x -q
```

---

### T-006 — WorkerPool Drain/Pause Modes in worker.py

**Task ID**: T-006
**Task Name**: Add drain_mode and pause_mode threading.Events to WorkerPool
**Priority**: Critical
**Effort**: M
**Source PRP Document**: [docs/prps/cluster-mode.md](../prps/cluster-mode.md) — Task 6
**Dependencies**: None (can be developed in parallel with T-001–T-004)

#### Context & Background

`WorkerPool` manages a set of `ConversionWorker` threads. Phase 1 adds two
`threading.Event` flags to implement cluster-controlled drain (stop accepting new jobs)
and pause (freeze all job pickup) without touching the existing `drain(timeout)`
shutdown helper.

**As a** cluster operator
**I need** to drain or pause a node's workers via a DB command
**So that** I can do node maintenance without losing in-flight jobs or SSH access

#### Dependencies

- **Prerequisite Tasks**: None
- **Parallel Tasks**: T-001, T-002, T-003, T-004
- **Integration Points**: T-005 calls `set_drain_mode()`, `clear_drain_mode()`,
  `set_paused()`, `clear_paused()` on the pool

#### Technical Requirements

- **REQ-1**: `WorkerPool` shall have `_drain_mode = threading.Event()` and
  `_pause_mode = threading.Event()` instance attributes.
- **REQ-2**: Methods `set_drain_mode()`, `clear_drain_mode()`, `set_paused()`,
  `clear_paused()` shall exist on `WorkerPool`.
- **REQ-3**: `clear_paused()` shall also call `w.job_event.set()` for each worker to
  wake threads blocked in `pause_event.wait()`.
- **REQ-4**: `ConversionWorker` shall hold a reference to its parent `WorkerPool`
  as `self.pool`.
- **REQ-5**: At the top of `ConversionWorker.run()`'s inner job-claim loop, BEFORE
  `claim_next_job()`: if `pool._pause_mode.is_set()` then call
  `pool._pause_mode.wait()` (blocks); if `pool._drain_mode.is_set()` then `break` from
  the inner loop.
- **REQ-6**: The existing `WorkerPool.drain(timeout)` join-based shutdown method shall
  not be modified.

**Technical Constraints**:

- Use `threading.Event` not `threading.Lock` — `Event.wait()` blocks without spinning.
- The pause gate is checked once per iteration before claiming a job.

#### Files to Modify

```text
resources/daemon/worker.py  - WorkerPool.__init__(), set_drain_mode(),
                               clear_drain_mode(), set_paused(), clear_paused();
                               ConversionWorker.__init__() + run()
```

#### Key Implementation Steps

1. In `WorkerPool.__init__()`: add `self._drain_mode = threading.Event()` and
   `self._pause_mode = threading.Event()`.
2. Add the four control methods to `WorkerPool` as specified.
3. When constructing each `ConversionWorker` inside `WorkerPool`, pass `pool=self`;
   store as `self.pool` in `ConversionWorker.__init__()`.
4. In `ConversionWorker.run()` inner loop: insert pause/drain checks immediately before
   the `claim_next_job()` call (see PRP pseudocode).

#### Code Patterns to Follow

- Existing `ConversionWorker.job_event` usage: `resources/daemon/worker.py` lines
  56–57 and `stop()` method
- `threading.Event.wait()` blocking pattern (see `_StoppableThread._stop_event.wait()`
  in `threads.py`)

#### Acceptance Criteria

```gherkin
Scenario 1: Workers stop claiming jobs when drain mode set
  Given a WorkerPool with workers waiting for jobs
  When set_drain_mode() is called
  Then each worker that reaches the check breaks from the inner loop
  And workers do not call claim_next_job()

Scenario 2: Workers block when paused
  Given a WorkerPool in normal operation
  When set_paused() is called
  Then workers reaching the pause check block on _pause_mode.wait()
  And they do not claim new jobs while paused

Scenario 3: Resume wakes all paused workers
  Given all workers are blocked in _pause_mode.wait()
  When clear_paused() is called
  Then all workers unblock and resume normal operation
  And job_event.set() is called for each worker

Scenario 4: Existing drain(timeout) unchanged
  Given a shutdown is initiated via WorkerPool.drain(timeout)
  When drain(timeout) is called
  Then it behaves identically to pre-Phase-1 behaviour
  And set_drain_mode() is not implicitly called
```

**Rule-Based Checklist**:

- [ ] `_drain_mode` and `_pause_mode` are `threading.Event` instances
- [ ] `clear_paused()` calls `w.job_event.set()` for each worker
- [ ] Pause check uses `_pause_mode.wait()` (blocking), not polling
- [ ] Drain check uses `break` to exit the inner loop
- [ ] Existing `drain(timeout)` method is unchanged

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/worker.py --fix
pyright resources/daemon/worker.py
python -m pytest tests/test_worker.py -x -q -k "drain or pause or resume"
```

---

### T-007 — PostgreSQL Log Handler in db_log_handler.py

**Task ID**: T-007
**Task Name**: Create PostgreSQLLogHandler for batched log writes to the logs table
**Priority**: High
**Effort**: M
**Source PRP Document**: [docs/prps/cluster-mode.md](../prps/cluster-mode.md) — Task 7
**Dependencies**: T-001 (`insert_logs()` method must exist on `PostgreSQLJobDatabase`)

#### Context & Background

There is currently no mechanism for persisting daemon log records to PostgreSQL. The
new `PostgreSQLLogHandler` buffers `logging.LogRecord` objects and flushes them in
batches to the `logs` table, enabling the cross-node log viewer in the admin UI.

**As a** cluster operator
**I need** log records from all nodes persisted to PostgreSQL
**So that** I can view and filter unified logs from any node's admin UI

#### Dependencies

- **Prerequisite Tasks**: T-001 (`insert_logs()` must be available)
- **Parallel Tasks**: T-005, T-006
- **Integration Points**: T-008 (daemon.py attaches this handler to the DAEMON logger)

#### Technical Requirements

- **REQ-1**: `PostgreSQLLogHandler(db, node_id, batch_size=50)` shall extend
  `logging.Handler`.
- **REQ-2**: `emit()` shall NEVER raise under any circumstances.
- **REQ-3**: Records shall be buffered in `self._batch`; when `len(self._batch) >=
  batch_size`, `flush()` is called automatically inside `emit()`.
- **REQ-4**: `flush()` shall swap the batch under lock before calling
  `self._db.insert_logs()`; exceptions during the DB write shall be swallowed.
- **REQ-5**: `close()` shall call `flush()` to drain remaining buffered records, then
  `super().close()`.
- **REQ-6**: Thread safety: all batch access shall be protected by `self._lock`.

**Technical Constraints**:

- Do not import anything outside the Python stdlib and existing project deps.
- `emit()` must swallow ALL exceptions — including `KeyboardInterrupt` is not required,
  but any `Exception` subclass must be caught.

#### Files to Create

```text
resources/daemon/db_log_handler.py  - NEW: PostgreSQLLogHandler class
```

#### Key Implementation Steps

1. Create `resources/daemon/db_log_handler.py`.
2. Implement `PostgreSQLLogHandler` per the PRP pseudocode (full implementation
   provided in the PRP's "Per-Task Pseudocode" section).
3. Ensure `_flush_locked()` swaps `self._batch` to empty list before the DB call so
   new records accumulate while the write is in progress.

#### Code Patterns to Follow

- Full implementation in PRP pseudocode section under "Task 7"
- Python stdlib `logging.Handler` subclass pattern

#### Acceptance Criteria

```gherkin
Scenario 1: Batching below threshold
  Given batch_size=50 and only 10 records emitted
  When emit() is called 10 times
  Then insert_logs() is not called
  And the 10 records are held in _batch

Scenario 2: Flush on batch_size threshold
  Given batch_size=3
  When emit() is called 3 times
  Then insert_logs() is called with the 3 records
  And _batch is empty afterward

Scenario 3: DB exception during flush
  Given insert_logs() raises psycopg2.OperationalError
  When flush() is called
  Then no exception propagates out of flush()

Scenario 4: emit() never raises
  Given the DB is unavailable
  When emit() is called
  Then no exception propagates to the caller

Scenario 5: close() drains remaining records
  Given 5 records are buffered (below batch_size)
  When close() is called
  Then insert_logs() is called once with those 5 records
```

**Rule-Based Checklist**:

- [ ] `emit()` has a bare `except Exception: pass` guard
- [ ] `flush()` has its own exception guard
- [ ] Batch swap happens inside the lock before DB write
- [ ] `close()` calls `flush()` then `super().close()`
- [ ] Thread safety verified with concurrent emit calls

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/db_log_handler.py --fix
pyright resources/daemon/db_log_handler.py
python -m pytest tests/test_cluster.py -x -q -k "log_handler or emit or flush"
```

---

### T-008 — Wire DB Log Handler in daemon.py

**Task ID**: T-008
**Task Name**: Attach PostgreSQLLogHandler to DAEMON logger on startup
**Priority**: High
**Effort**: S
**Source PRP Document**: [docs/prps/cluster-mode.md](../prps/cluster-mode.md) — Task 8
**Dependencies**: T-003 (`node_id` property on `PathConfigManager`), T-007
(`PostgreSQLLogHandler` class)

#### Context & Background

`daemon.py` is the thin project-root entry point that initialises `job_db` and
`path_config_manager`. Attaching the log handler here ensures it is wired before
the daemon spawns any threads.

**As a** daemon starting in distributed mode
**I need** the DAEMON logger wired to PostgreSQLLogHandler automatically
**So that** all DAEMON log records are captured in the `logs` table without manual
configuration

#### Dependencies

- **Prerequisite Tasks**: T-001, T-003, T-007
- **Parallel Tasks**: T-004, T-005, T-006
- **Integration Points**: `daemon.py` main flow; `job_db.is_distributed` guard

#### Technical Requirements

- **REQ-1**: After `job_db` and `path_config_manager` are both initialised, if
  `job_db.is_distributed`, a `PostgreSQLLogHandler` shall be added to
  `logging.getLogger("DAEMON")`.
- **REQ-2**: The handler shall be set to `logging.DEBUG` level.
- **REQ-3**: SQLite single-node deployments (`is_distributed = False`) shall not
  receive the handler.

#### Files to Modify

```text
daemon.py  - add PostgreSQLLogHandler wiring after job_db + path_config_manager init
```

#### Key Implementation Steps

1. Locate the section in `daemon.py` where `job_db` and `path_config_manager` are both
   available.
2. Add the conditional block per PRP Task 8 blueprint.

#### Acceptance Criteria

```gherkin
Scenario 1: Distributed mode
  Given job_db.is_distributed is True
  When the daemon starts
  Then logging.getLogger("DAEMON").handlers includes a PostgreSQLLogHandler
  And the handler's level is DEBUG

Scenario 2: Single-node SQLite mode
  Given job_db.is_distributed is False
  When the daemon starts
  Then no PostgreSQLLogHandler is attached to the DAEMON logger
```

**Rule-Based Checklist**:

- [ ] Import is inside the `if job_db.is_distributed:` block (not at module top)
- [ ] Handler level set to `logging.DEBUG`
- [ ] No changes to SQLite code paths

#### Validation Commands

```bash
source venv/bin/activate
ruff check daemon.py --fix
python -m pytest tests/ -x -q -k "daemon or log_handler"
```

---

### T-009 — Admin UI: Node Action Buttons and Cluster Log Viewer

**Task ID**: T-009
**Task Name**: Add drain/pause/resume node buttons and cluster log viewer to admin UI
**Priority**: High
**Effort**: L
**Source PRP Document**: [docs/prps/cluster-mode.md](../prps/cluster-mode.md) — Task 9
**Dependencies**: T-001 (`get_logs()` DB method), T-005 (drain/pause/resume routed to
WorkerPool), T-006 (WorkerPool accepts commands)

#### Context & Background

The admin UI (`resources/admin.html`) already has a node grid with `restart` and
`shutdown` buttons rendered via Alpine.js. Phase 1 adds `drain`, `pause`, `resume`
buttons to that grid and adds a new cluster log viewer section backed by a new
`GET /cluster/logs` API endpoint.

**As a** cluster operator using the admin web UI
**I need** drain/pause/resume action buttons per node and a filterable log viewer
**So that** I can manage node states and inspect aggregated logs without SSH access

#### Dependencies

- **Prerequisite Tasks**: T-001, T-005, T-006
- **Parallel Tasks**: T-010
- **Integration Points**: `handler.py` `_post_admin_node_action()`; `routes.py`
  `_get_routes()`; Alpine.js `adminPage()` component in `admin.html`

#### Technical Requirements

- **REQ-1**: `_post_admin_node_action()` in `handler.py` shall handle `"drain"`,
  `"pause"`, and `"resume"` with the same pattern as `"restart"`/`"shutdown"`.
- **REQ-2**: `GET /cluster/logs` endpoint shall accept query params `node_id`, `level`,
  `limit` (default 100), `offset` (default 0) and return JSON from `get_logs()`.
- **REQ-3**: `/cluster/logs` must NOT appear in `PUBLIC_ENDPOINTS`.
- **REQ-4**: The admin UI node row shall include drain/pause/resume buttons styled
  identically to the existing restart/shutdown buttons.
- **REQ-5**: A cluster log viewer section shall be added to `adminPage()` with:
  `clusterLogs`, `logsNodeFilter`, `logsLevelFilter`, `logsLoading`, `logsError` data
  properties and a `loadClusterLogs()` method.
- **REQ-6**: The log table shall show: timestamp, node_id, level, logger, message.
- **REQ-7**: The log viewer shall include a level dropdown (ALL/DEBUG/INFO/WARNING/ERROR)
  and node dropdown (populated from `clusterNodes`), plus pagination controls.

**Technical Constraints**:

- Follow the existing Alpine.js `authHeaders()` helper pattern in `admin.html` for
  all fetch calls.
- Register the route in `routes.py`, not inline in `handler.py`.
- Log viewer pagination should follow the existing UI pattern (no new CSS frameworks).

#### Files to Modify

```text
resources/daemon/handler.py  - extend _post_admin_node_action(), add
                                _get_cluster_logs()
resources/daemon/routes.py   - register GET /cluster/logs
resources/admin.html         - drain/pause/resume buttons, log viewer section
```

#### API Specification

```yaml
Method: GET
Path: /cluster/logs
Headers:
  Authorization: Bearer <api_key>  (required when API key configured)
Query Parameters:
  node_id: string (optional filter)
  level:   string (optional; DEBUG/INFO/WARNING/ERROR)
  limit:   integer (default 100, max 500)
  offset:  integer (default 0)
Response:
  status: 200
  body:
    logs:
      - id: integer
        node_id: string
        level: string
        logger: string
        message: string
        timestamp: string (ISO 8601)
    total: integer (optional, for pagination)
```

#### Acceptance Criteria

```gherkin
Scenario 1: drain button issues command
  Given the cluster tab is open and node X is online
  When the operator clicks "Drain" for node X
  Then POST /admin/node/{node_id}/action with body {"action":"drain"} is sent
  And the node row reflects "draining" status after refresh

Scenario 2: GET /cluster/logs with filters
  Given logs exist in the DB for nodes A and B
  When GET /cluster/logs?node_id=A&level=ERROR is requested
  Then only ERROR-level logs from node A are returned

Scenario 3: /cluster/logs requires auth
  Given the daemon is running with an API key
  When GET /cluster/logs is requested without the Authorization header
  Then 401 Unauthorized is returned

Scenario 4: Log viewer populates
  Given the cluster log viewer section is visible in the admin UI
  When loadClusterLogs() fires
  Then the log table is populated with rows from /cluster/logs
  And the node filter dropdown is populated from clusterNodes

Scenario 5: Pagination controls work
  Given 250 log entries exist
  When the operator advances to page 3 (offset=200, limit=100)
  Then the correct 50 remaining entries are shown
```

**Rule-Based Checklist**:

- [ ] `drain`, `pause`, `resume` handled in `_post_admin_node_action()`
- [ ] `/cluster/logs` not in `PUBLIC_ENDPOINTS`
- [ ] Route registered in `routes.py` `_get_routes()`
- [ ] Alpine.js data props and `loadClusterLogs()` method added
- [ ] Log table columns: timestamp, node_id, level, logger, message
- [ ] Level and node filter dropdowns present
- [ ] Pagination offset/limit controls present

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/handler.py resources/daemon/routes.py --fix
python -m pytest tests/ -x -q -k "cluster_logs or node_action"
# Manual: open admin UI, verify log viewer renders and drain/pause/resume buttons appear
```

---

### T-010 — sma-ng.yml.sample Update

**Task ID**: T-010
**Task Name**: Document node_id and log_ttl_days in sma-ng.yml.sample
**Priority**: Medium
**Effort**: S
**Source PRP Document**: [docs/prps/cluster-mode.md](../prps/cluster-mode.md) — Task 10
**Dependencies**: T-003 (new config fields must be defined before sample is updated)

#### Context & Background

`setup/sma-ng.yml.sample` is the canonical sample config. Operators copy it to start
a new deployment. New `node_id` and `log_ttl_days` fields must be documented there with
explanatory comments.

**As a** new operator setting up a cluster node
**I need** the sample config to document node_id and log_ttl_days
**So that** I understand the purpose of each field and its default behaviour

#### Dependencies

- **Prerequisite Tasks**: T-003 (fields must be defined in config.py first)
- **Parallel Tasks**: T-009, T-012
- **Integration Points**: `setup/sma-ng.yml.sample`

#### Technical Requirements

- **REQ-1**: The `daemon:` section of `sma-ng.yml.sample` shall include `node_id: null`
  with an explanatory comment.
- **REQ-2**: The `daemon:` section shall include `log_ttl_days: 30` with an explanatory
  comment.

#### Files to Modify

```text
setup/sma-ng.yml.sample  - add node_id and log_ttl_days to daemon: section
```

#### Key Implementation Steps

1. Open `setup/sma-ng.yml.sample` and locate the `daemon:` section.
2. Add fields per PRP Task 10 specification, preserving all existing entries and
   indentation style.

#### Acceptance Criteria

```gherkin
Scenario 1: node_id documented
  Given setup/sma-ng.yml.sample is opened
  When the daemon: section is inspected
  Then node_id: null is present with a comment explaining UUID auto-generation

Scenario 2: log_ttl_days documented
  Given setup/sma-ng.yml.sample is opened
  When the daemon: section is inspected
  Then log_ttl_days: 30 is present with a comment explaining TTL behaviour
  And 0 is noted as the disable-cleanup value
```

**Rule-Based Checklist**:

- [ ] `node_id: null` present with multi-line comment
- [ ] `log_ttl_days: 30` present with comment noting `0 disables cleanup`
- [ ] No existing keys removed or reindented
- [ ] YAML is valid (parseable by ruamel.yaml)

#### Validation Commands

```bash
python3 -c "from ruamel.yaml import YAML; YAML().load(open('setup/sma-ng.yml.sample'))"
```

---

### T-011 — Tests

**Task ID**: T-011
**Task Name**: Create test_cluster.py and extend test_threads.py / test_worker.py
**Priority**: High
**Effort**: L
**Source PRP Document**: [docs/prps/cluster-mode.md](../prps/cluster-mode.md) — Task 11
**Dependencies**: T-001 through T-008 (all implementation tasks — tests validate them)

#### Context & Background

The PRP requires full test coverage for the new cluster mode code paths. Tests fall
into three groups: DB/schema tests in a new `test_cluster.py`, command dispatch tests
appended to `test_threads.py`, and WorkerPool drain/pause/resume tests appended to
`test_worker.py`.

**As a** developer
**I need** comprehensive automated tests for cluster mode
**So that** regressions are caught by CI and the implementation can be validated
without a full cluster setup

#### Dependencies

- **Prerequisite Tasks**: T-001, T-002, T-003, T-004, T-005, T-006, T-007, T-008
- **Parallel Tasks**: T-010, T-012
- **Integration Points**: `tests/conftest.py` (job_db fixture, TEST_DB_URL skip);
  `tests/test_threads.py` (extend); `tests/test_worker.py` (extend)

#### Technical Requirements

New file `tests/test_cluster.py`:

- **REQ-1**: UUID generation when `node_id` absent (mock file write)
- **REQ-2**: `resolve_node_id()` returns cached UUID after `set_node_id_cache()`
- **REQ-3**: `poll_node_command()` returns `None` when no pending commands (requires
  `TEST_DB_URL`)
- **REQ-4**: `poll_node_command()` returns oldest pending command (requires
  `TEST_DB_URL`)
- **REQ-5**: `ack_node_command()` updates status
- **REQ-6**: `insert_logs()` and `get_logs()` with node/level filters
- **REQ-7**: `cleanup_old_logs()` deletes only records older than TTL
- **REQ-8**: `PostgreSQLLogHandler.emit()` buffers and flushes
- **REQ-9**: `PostgreSQLLogHandler.emit()` swallows DB exceptions

Extensions to `tests/test_threads.py`:

- **REQ-10**: `_execute_command("drain")` calls `set_drain_mode()`
- **REQ-11**: `_execute_command("pause")` calls `set_paused()`
- **REQ-12**: `_execute_command("resume")` calls both clear methods
- **REQ-13**: Command ack status `"done"` on success, `"failed"` on exception

Extensions to `tests/test_worker.py`:

- **REQ-14**: Worker does not claim jobs when `_drain_mode` is set
- **REQ-15**: Worker blocks on `_pause_mode.wait()` when `_pause_mode` is set
- **REQ-16**: `set_paused()` + `clear_paused()` wakes workers

**Technical Constraints**:

- Tests requiring a real PostgreSQL DB must be skipped when `TEST_DB_URL` is not set
  (mirror pattern from `tests/conftest.py`).
- All mock-based tests (UUID, cache, handler, worker event) must run without any DB.

#### Files to Create/Modify

```text
tests/test_cluster.py    - NEW: cluster-specific tests
tests/test_threads.py    - EXTEND: command dispatch tests
tests/test_worker.py     - EXTEND: drain/pause/resume tests
```

#### Key Implementation Steps

1. Study `tests/conftest.py` for the `TEST_DB_URL` skip pattern and the `job_db`
   fixture; mirror for PostgreSQL-dependent tests.
2. Study `tests/test_threads.py` and `tests/test_worker.py` for existing mock
   patterns and helper fixtures.
3. Write all mock-based tests first (UUID, cache, log handler); they run in CI
   without any external service.
4. Write DB-dependent tests with the `pytest.mark.skipif(not TEST_DB_URL, ...)` guard.

#### Code Patterns to Follow

- `TEST_DB_URL` skip pattern: `tests/conftest.py`
- Existing HeartbeatThread mock pattern: `tests/test_threads.py`
- Existing WorkerPool test helpers: `tests/test_worker.py`

#### Acceptance Criteria

```gherkin
Scenario 1: Full test suite still passes
  Given all Phase 1 implementation tasks are complete
  When python -m pytest tests/ -x -q is run
  Then all 107+ pre-existing tests pass
  And all new cluster tests pass

Scenario 2: Mock-based tests run without PostgreSQL
  Given TEST_DB_URL is not set
  When python -m pytest tests/test_cluster.py -x -q is run
  Then UUID, cache, log handler, worker event tests all pass
  And PostgreSQL-dependent tests are skipped

Scenario 3: Integration tests run with PostgreSQL
  Given TEST_DB_URL is set
  When python -m pytest tests/test_cluster.py -x -q is run
  Then poll_node_command, ack_node_command, insert_logs, cleanup_old_logs tests pass
```

**Rule-Based Checklist**:

- [ ] All 9 test_cluster.py requirements covered
- [ ] All 4 test_threads.py extensions covered
- [ ] All 3 test_worker.py extensions covered
- [ ] DB-dependent tests skip gracefully without TEST_DB_URL
- [ ] No test relies on global state (each test cleans up its DB rows)

#### Validation Commands

```bash
source venv/bin/activate

# Mock-based only (no DB required)
python -m pytest tests/test_cluster.py tests/test_threads.py tests/test_worker.py \
    -x -q

# Full integration suite
TEST_DB_URL=postgresql://user:pass@localhost/testdb \
    python -m pytest tests/test_cluster.py -x -q

# Full suite
python -m pytest tests/ -x -q
```

---

### T-012 — Documentation

**Task ID**: T-012
**Task Name**: Add Cluster Mode section to docs/daemon.md
**Priority**: Medium
**Effort**: S
**Source PRP Document**: [docs/prps/cluster-mode.md](../prps/cluster-mode.md) — Task 12
**Dependencies**: T-001 through T-009 (implementation must be complete to document
accurately)

#### Context & Background

`docs/daemon.md` is the canonical reference for the sma-ng daemon. Per `CLAUDE.md`,
documentation changes must be applied in three places: `docs/`, the GitHub wiki
(`/tmp/sma-wiki/`), and the inline help in `resources/docs.html`.

**As a** cluster operator new to sma-ng
**I need** complete documentation for cluster mode in docs/daemon.md
**So that** I can configure UUID identity, understand node commands, and use the log
viewer without reading source code

#### Dependencies

- **Prerequisite Tasks**: T-001 through T-009 (all implementation)
- **Parallel Tasks**: T-010, T-011
- **Integration Points**: `docs/daemon.md`; GitHub wiki `Daemon.md` or equivalent;
  `resources/docs.html`

#### Technical Requirements

- **REQ-1**: A "Cluster Mode" section shall be added to `docs/daemon.md` covering:
  - `node_id` UUID auto-generation and `sma-ng.yml` persistence
  - Supported node commands and their semantics (`drain`, `pause`, `resume`,
    `restart`, `shutdown`)
  - Cluster tab description in admin UI
  - Log aggregation and `log_ttl_days` configuration
  - Graceful degradation note for SQLite single-node setups
- **REQ-2**: The section shall be added in the same commit as the implementation tasks
  per `CLAUDE.md` documentation rules.
- **REQ-3**: The wiki page (`/tmp/sma-wiki/`) must be updated.
- **REQ-4**: `resources/docs.html` inline help must reflect the new section.

**Technical Constraints**:

- Markdown must pass `markdownlint` with no errors (CLAUDE.md rule).
- Lines must not exceed 120 characters.
- All fenced code blocks must declare a language.

#### Files to Modify

```text
docs/daemon.md            - add Cluster Mode section
/tmp/sma-wiki/<page>.md   - update corresponding wiki page
resources/docs.html       - update inline help section
```

#### Key Implementation Steps

1. Open `docs/daemon.md` and identify the correct insertion point for the new section.
2. Write the Cluster Mode section covering all five topics in REQ-1.
3. Run `markdownlint docs/daemon.md`; fix any violations.
4. Mirror changes to the wiki page and `resources/docs.html`.

#### Acceptance Criteria

```gherkin
Scenario 1: Section exists and is complete
  Given docs/daemon.md is opened
  When the Cluster Mode section is found
  Then it covers node_id UUID, all five commands, admin UI cluster tab,
       log_ttl_days, and SQLite graceful degradation

Scenario 2: Markdownlint passes
  Given docs/daemon.md has been modified
  When markdownlint docs/daemon.md is run
  Then no warnings or errors are reported

Scenario 3: Wiki and docs.html in sync
  Given the daemon.md section is written
  When /tmp/sma-wiki/ and resources/docs.html are checked
  Then equivalent content is present in both
```

**Rule-Based Checklist**:

- [ ] All five topics from REQ-1 covered
- [ ] No markdownlint warnings
- [ ] All fenced code blocks have language identifiers
- [ ] Wiki page updated and pushed
- [ ] `resources/docs.html` updated

#### Validation Commands

```bash
markdownlint docs/daemon.md
# Push wiki:
cd /tmp/sma-wiki && git add -A && git commit -m "docs: add cluster mode section" \
    && git push origin HEAD:master
```

---

## Implementation Recommendations

### Suggested Team Structure

- **Developer A** (Phase 1 data layer): T-001, T-002, T-003, T-004 — all changes are
  confined to `db.py`, `constants.py`, `config.py`, `server.py` with no UI work.
- **Developer B** (Phase 2 behavioural layer): T-005, T-006, T-007, T-008 — all
  threading and logging changes. Picks up after T-001 is merged.
- **Developer C** (Phase 3 UI and QA): T-009 (UI), T-010 (sample config), T-011
  (tests), T-012 (docs) — can begin T-010 and T-012 scaffolding early, completes T-009
  and T-011 after Phase 2 merges.

### Optimal Task Sequencing

```text
Sprint 1 (Phase 1 data layer — parallel):
  T-002 (cache, no deps)       ──┐
  T-001 (schema)               ──┤ all can start day 1
  T-004 (hwaccel return value) ──┘

Sprint 1 continues (requires T-002):
  T-003 (UUID persistence)     ── after T-002

Sprint 2 (Phase 2 behavioural — parallel after Phase 1):
  T-006 (WorkerPool events)    ──┐ start as soon as T-001 merged
  T-007 (log handler)          ──┤ start as soon as T-001 merged
  T-005 (HeartbeatThread)      ── after T-001 + T-004 + T-006
  T-008 (wire handler)         ── after T-003 + T-007

Sprint 3 (Phase 3 — after Phase 2):
  T-010 (sample config)        ── after T-003 (can be 1-hour task)
  T-009 (admin UI)             ── after T-001 + T-005 + T-006
  T-011 (tests)                ── after all implementation tasks
  T-012 (docs)                 ── after T-009
```

### Parallelisation Opportunities

| Parallel Group | Tasks | Notes |
|---|---|---|
| Day 1 start | T-001, T-002, T-004 | Independent files |
| After T-002 | T-003 alongside T-001, T-004 | Only needs cache |
| After Phase 1 | T-006, T-007 | Independent of each other |
| After T-003+T-007 | T-008 | Small wiring task |
| After T-001+T-005+T-006 | T-009 | UI can start |
| Throughout | T-010 | 1-hour task, no blockers after T-003 |

### Resource Allocation

- T-001, T-005: most complex tasks — assign most experienced developer on `db.py` and
  `threads.py`.
- T-009: requires Alpine.js familiarity — review `admin.html` before starting.
- T-003: ruamel.yaml round-trip gotcha is the primary risk — review `yamlconfig.py`
  first and write a quick proof-of-concept before full implementation.
- T-011: can be written incrementally as each implementation task completes.

---

## Critical Path Analysis

### Tasks on Critical Path

```text
T-002 → T-003 → T-008 → T-011 → T-012
T-001 → T-005 → T-009 → T-012
T-006 → T-005
T-007 → T-008
T-004 → T-005
```

The longest path is: **T-001 + T-006 → T-005 → T-009 → T-011 → T-012**

### Potential Bottlenecks

1. **T-001 (Schema)** — every behavioural task depends on the new DB methods. Must be
   merged first. Keep scope tight: DDL + new methods only, no behavioural changes.
2. **T-003 (UUID persistence)** — the ruamel.yaml round-trip write-back is the highest
   risk subtask. Proof-of-concept before full implementation recommended.
3. **T-005 (HeartbeatThread)** — depends on T-001, T-004, and T-006 all being merged.
   Schedule T-006 as an early parallel task to avoid a wait here.
4. **T-009 (Admin UI)** — largest frontend task; depends on T-005 and T-006. Start UI
   scaffolding (HTML structure, Alpine.js data props) before all backend tasks merge to
   reduce the critical path duration.

### Schedule Optimisation Suggestions

- Merge T-002 (30-minute task) on day 1 to unblock T-003.
- Merge T-004 on day 1 (small change); T-005 then only waits on T-001 and T-006.
- Start T-011 test stubs alongside implementation: write failing tests first, let the
  implementation make them pass (test-driven where practical).
- T-010 and T-012 documentation tasks are low-risk; parallelise with T-011 to avoid
  a documentation-only tail at the end of the sprint.
