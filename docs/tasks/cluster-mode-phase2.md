# Task Breakdown: Cluster Mode — Phase 2 (Config Sync, Node Expiry, Log Archival)

**Source PRP**: [docs/prps/cluster-mode-phase2.md](../prps/cluster-mode-phase2.md)
**Feature Branch Target**: `main`
**Overall Complexity**: Moderate-to-Complex (14 tasks, 3 features, multiple integration points)

---

## PRP Analysis Summary

**Feature**: Phase 2 of sma-ng cluster mode — centralised DB config, TTL-based node expiry,
and filesystem log archival.

**Key Technical Requirements**:

- `cluster_config` table (single-row, upsert on `id=1`) storing a secrets-stripped YAML blob
- `PathConfigManager.load_config()` accepts an optional `job_db` parameter; DB config is merged
  as the base with local `sma-ng.yml` values always winning per-key
- `SECRET_KEYS` constant in `constants.py`; secrets stripped at write time (never at read time)
- Admin UI config editor (`GET /admin/config`, `POST /admin/config`) and push-from-node action
  (`POST /admin/nodes/<node_id>/push-config`) extending the existing node action handler
- `expire_offline_nodes(days)` hard-deletes stale offline nodes plus orphaned `node_commands` rows
- `LogArchiver` class writing gzipped JSONL files atomically before deleting DB rows;
  separate prune step for old archive files on disk
- All new paths gated on `job_db.is_distributed`; SQLite single-node is unaffected

**Validation Requirements** (from PRP success criteria):

- `cluster_config` table created idempotently on startup
- Secrets never stored in DB; stripped at write time for both API paths
- `node_expiry_days: 0` disables expiry; online nodes are never deleted
- `log_archive_after_days: 0` disables archival; `.gz` writes are atomic (tmp + rename)
- DB rows deleted only after `.gz` file confirmed written
- All 2303+ existing tests pass; new tests cover all new DB methods, merge logic,
  expiry, and archival

---

## Task Complexity Assessment

**Overall complexity**: Moderate (individual tasks are well-scoped; the config merge
ordering and HeartbeatThread hot-reload constraint are the primary risks)

**Integration points**:

- `db.py` `_init_db()` — DDL migration; new methods use `_conn()` context manager
- `constants.py` — new `SECRET_KEYS` constant consumed by both `db.py` and `config.py`
- `config.py` `load_config()` — signature change (`job_db=None`) propagates to
  `server.py reload_config()`
- `threads.py` HeartbeatThread — reads new settings from `path_config_manager` on each
  tick (NOT stored as `__init__` args) due to hot-reload constraint
- `handler.py` — two new endpoint methods; `_post_admin_node_action()` extended
- `routes.py` — two new route registrations
- `admin.html` — new Alpine.js data props, methods, and Config section in Cluster tab

**Technical challenges**:

- Config merge ordering: DB base config must be parsed first, then local YAML overlaid
  per-key. The `job_db` reference is not available on the very first `load_config()`
  call during `DaemonServer` construction — only pass `job_db` during `reload_config()`.
- HeartbeatThread hot-reload: all four new settings must be read from
  `path_config_manager` on each tick, not stored in `__init__`, because
  `HeartbeatThread` is not restarted on config reload.
- Atomic `.gz` write: write to `<path>.tmp`, then `os.replace(tmp, final)`. Delete DB
  rows only if all groups succeeded.
- Single-row upsert: `INSERT … ON CONFLICT (id) DO UPDATE` with `CHECK (id = 1)`.
- Alpine.js YAML serialisation: verify whether `js-yaml` is already loaded in
  `admin.html` before adding a dependency; fall back to a JSON editor if not.

---

## Phase Organisation

### Phase 1: Data Layer (Tasks T-201 – T-205)

**Objective**: Establish the `cluster_config` schema migration, all new DB methods,
and the `SECRET_KEYS` constant before any behavioural code changes.

**Deliverables**:

- `cluster_config` table exists after `_init_db()` runs
- `get_cluster_config()`, `set_cluster_config()` with secret stripping
- `expire_offline_nodes()`, `cleanup_orphaned_commands()`
- `get_logs_for_archival()`, `delete_logs_before()`
- `SECRET_KEYS` constant in `constants.py`

**Milestone**: Daemon starts; `cluster_config` table is present; new DB methods are
callable from a Python REPL against a live PostgreSQL instance.

### Phase 2: Behavioural Layer (Tasks T-206 – T-209)

**Objective**: Wire config merge, log archival, node expiry, and admin API endpoints
through the daemon runtime.

**Deliverables**:

- `PathConfigManager.load_config(job_db=None)` merges DB base config; local wins
- `LogArchiver` class (new `log_archiver.py`) writes atomic `.gz` files and prunes
- `HeartbeatThread` calls `expire_offline_nodes()` and `LogArchiver.run()` each tick
- `GET /admin/config`, `POST /admin/config`, push-config action in handler + routes

**Milestone**: Posting a config to `/admin/config` persists it; reloading the daemon
picks up the DB config as base; offline nodes with a stale `last_seen` are removed on
the next heartbeat tick; aged log rows appear as `.gz` files on disk.

### Phase 3: UI, Config, Tests, Docs (Tasks T-210 – T-214)

**Objective**: Surface Phase 2 capabilities through the admin UI, update the sample
config, provide full test coverage, and update documentation.

**Deliverables**:

- Admin UI Config section in the Cluster tab with YAML editor and push-from-node button
- `setup/sma-ng.yml.sample` updated with four new `daemon:` keys
- `tests/test_cluster.py` extended with `TestClusterConfigDB`, `TestNodeExpiryDB`,
  `TestLogArchivalThread`, `TestConfigMerge` test classes
- `docs/daemon.md` updated with three new Cluster Mode sub-sections

**Milestone**: All 2303+ existing tests pass; new test suite passes with and without
`TEST_DB_URL`; Config section functional in the browser admin UI.

---

## Detailed Task Breakdown

---

### T-201 — cluster_config Schema Migration in db.py

**Task ID**: T-201
**Task Name**: Add cluster_config table to PostgreSQL schema via _init_db()
**Priority**: Critical
**Effort**: S
**Source PRP Document**: [docs/prps/cluster-mode-phase2.md](../prps/cluster-mode-phase2.md) — Task 1
**Dependencies**: None (foundational; all other tasks depend on this)

#### Context & Background

**Source PRP Document**: docs/prps/cluster-mode-phase2.md

**Feature Overview**: Phase 2 of sma-ng cluster mode adds a shared DB-resident base
config, TTL-based node expiry, and filesystem log archival. This task lays the DDL
foundation for config storage.

**As a** cluster operator running multiple nodes
**I need** a `cluster_config` table in PostgreSQL
**So that** a single base config can be stored once and fetched by all nodes on startup

#### Dependencies

- **Prerequisite Tasks**: None
- **Parallel Tasks**: T-202 (no shared files), T-203 (no shared files), T-205
- **Integration Points**: All tasks that call `get_cluster_config()` or
  `set_cluster_config()` depend on this DDL having run

#### Technical Requirements

- **REQ-1**: When `_init_db()` runs, it shall create a `cluster_config` table if it
  does not exist, with columns `id INTEGER PRIMARY KEY DEFAULT 1`, `config TEXT NOT NULL`,
  `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`, and `updated_by TEXT`.
- **REQ-2**: The `id` column shall have a `CHECK (id = 1)` constraint to enforce the
  single-row pattern.
- **REQ-3**: The statement shall be idempotent (`CREATE TABLE IF NOT EXISTS`); repeated
  startups must not error.
- **REQ-4**: No new indexes are needed (single-row table, PK lookup only).

**Technical Constraints**:

- All DDL lives in `_init_db()` — no separate migration runner exists.
- No FK constraints — consistent with existing schema.
- Insert the new `CREATE TABLE` statement after the `idx_logs_ts CREATE INDEX` statement
  (the last existing DDL statement in `_init_db()`).

#### Files to Modify

```text
resources/daemon/db.py  - _init_db(): add CREATE TABLE IF NOT EXISTS cluster_config
```

#### Key Implementation Steps

1. Open `resources/daemon/db.py` and locate `_init_db()`.
2. After the `idx_logs_ts` `CREATE INDEX` statement, append the `CREATE TABLE IF NOT
   EXISTS cluster_config` DDL block from the PRP data models section.
3. Verify the existing `ALTER TABLE … ADD COLUMN IF NOT EXISTS` pattern is followed
   for any subsequent column additions (none needed here).

#### Code Patterns to Follow

- Existing `CREATE TABLE IF NOT EXISTS` pattern: `resources/daemon/db.py` `_init_db()`
  (lines 58–196)
- `_conn()` context manager: `resources/daemon/db.py` lines 41–52

#### Acceptance Criteria

```gherkin
Scenario 1: Fresh database
  Given a PostgreSQL database with no sma-ng tables
  When the daemon starts and _init_db() executes
  Then the cluster_config table exists
  And the id column has a CHECK (id = 1) constraint

Scenario 2: Existing database — idempotent
  Given a PostgreSQL database that already has the cluster_config table
  When _init_db() executes again
  Then no error is raised
  And the table is unchanged

Scenario 3: Single-row enforcement
  Given the cluster_config table exists with a row for id=1
  When an INSERT with id=2 is attempted
  Then the CHECK constraint violation is raised
```

**Rule-Based Checklist**:

- [ ] `CREATE TABLE IF NOT EXISTS` — idempotent on repeated runs
- [ ] `CHECK (id = 1)` constraint present on the `id` column
- [ ] `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()` present
- [ ] No new indexes created (single-row table)
- [ ] No FK constraints added

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/db.py --fix
pyright resources/daemon/db.py
TEST_DB_URL=postgresql://user:pass@localhost/testdb \
    python -m pytest tests/test_cluster.py -x -q -k "cluster_config"
```

---

### T-202 — DB Methods for cluster_config in db.py

**Task ID**: T-202
**Task Name**: Implement get_cluster_config() and set_cluster_config() on PostgreSQLJobDatabase
**Priority**: Critical
**Effort**: M
**Source PRP Document**: [docs/prps/cluster-mode-phase2.md](../prps/cluster-mode-phase2.md) — Task 2
**Dependencies**: T-201 (table must exist), T-205 (SECRET_KEYS must be importable)

#### Context & Background

**Source PRP Document**: docs/prps/cluster-mode-phase2.md

**Feature Overview**: `get_cluster_config()` retrieves the stored YAML blob as a parsed
dict; `set_cluster_config()` strips secrets and upserts the blob. These are the two
core DB methods for the config sync feature.

**As a** daemon node starting up
**I need** methods to read and write a cluster-wide base config from PostgreSQL
**So that** all nodes can merge a shared config without manual file synchronisation

#### Dependencies

- **Prerequisite Tasks**: T-201, T-205
- **Parallel Tasks**: T-203, T-204
- **Integration Points**: T-206 (`load_config()` calls `get_cluster_config()`);
  T-209 (handler calls `set_cluster_config()`)

#### Technical Requirements

- **REQ-1**: `get_cluster_config()` shall execute `SELECT config FROM cluster_config
  WHERE id = 1` and return `yaml.safe_load(row["config"])` or `None` if no row exists.
- **REQ-2**: `set_cluster_config(config_dict, updated_by=None)` shall deep-copy
  `config_dict`, strip all keys in `SECRET_KEYS` from the `daemon:` sub-section, and
  upsert using `INSERT … ON CONFLICT (id) DO UPDATE`.
- **REQ-3**: The upsert shall set `updated_at = NOW()` and `updated_by =
  EXCLUDED.updated_by` on conflict.
- **REQ-4**: Both methods shall use the `_conn()` context manager pattern.

**Technical Constraints**:

- `set_cluster_config()` uses stdlib `yaml.safe_dump` for serialisation — NOT
  ruamel.yaml. Comments are not needed in the stored blob.
- Import `SECRET_KEYS` from `resources.daemon.constants` inside the method or at
  module level (not re-defined in `db.py`).
- Deep-copy the caller's dict before mutating to avoid side effects.

#### Files to Modify

```text
resources/daemon/db.py  - add get_cluster_config(), set_cluster_config()
```

#### Key Implementation Steps

1. Add `get_cluster_config(self) -> dict | None` using `_conn()`:
   `SELECT config FROM cluster_config WHERE id = 1`; return
   `yaml.safe_load(row["config"])` or `None`.
2. Add `set_cluster_config(self, config_dict: dict, updated_by: str | None = None)`:
   - `import copy; data = copy.deepcopy(config_dict)`
   - Strip `SECRET_KEYS` from `data.get("daemon", {})`
   - `config_str = yaml.safe_dump(data)` (stdlib yaml)
   - Upsert with the `ON CONFLICT (id) DO UPDATE` pattern from the PRP pseudocode

#### Code Patterns to Follow

- `_conn()` context manager: `resources/daemon/db.py` lines 41–52
- Upsert pattern: existing `heartbeat()` upsert in `resources/daemon/db.py`
- Full pseudocode for `set_cluster_config()`: PRP "Per-Task Pseudocode" section

#### Acceptance Criteria

```gherkin
Scenario 1: Roundtrip store and retrieve
  Given an empty cluster_config table
  When set_cluster_config({"daemon": {"workers": 4}}) is called
  Then get_cluster_config() returns {"daemon": {"workers": 4}}

Scenario 2: Secrets are stripped before storing
  Given a config dict containing {"daemon": {"api_key": "secret", "workers": 2}}
  When set_cluster_config() is called
  Then the stored YAML does not contain api_key
  And get_cluster_config() returns {"daemon": {"workers": 2}}

Scenario 3: Returns None when absent
  Given the cluster_config table has no rows
  When get_cluster_config() is called
  Then None is returned

Scenario 4: Second write overwrites first
  Given set_cluster_config({"daemon": {"workers": 2}}) has been called
  When set_cluster_config({"daemon": {"workers": 8}}) is called
  Then get_cluster_config() returns {"daemon": {"workers": 8}}
  And there is still exactly one row in cluster_config

Scenario 5: Return type is dict, not string
  Given a config blob is stored
  When get_cluster_config() is called
  Then the return value is a Python dict (not a YAML string)
```

**Rule-Based Checklist**:

- [ ] `get_cluster_config()` returns `None` (not `{}`) when the table is empty
- [ ] `set_cluster_config()` strips all five `SECRET_KEYS` from the `daemon:` section
- [ ] `set_cluster_config()` deep-copies the caller's dict before mutation
- [ ] Both methods use `self._conn()` context manager
- [ ] stdlib `yaml.safe_dump` used for serialisation (not ruamel.yaml)
- [ ] `updated_at` is set to `NOW()` on every upsert

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/db.py --fix
pyright resources/daemon/db.py
TEST_DB_URL=postgresql://user:pass@localhost/testdb \
    python -m pytest tests/test_cluster.py -x -q -k "TestClusterConfigDB"
```

---

### T-203 — DB Methods for Node Expiry in db.py

**Task ID**: T-203
**Task Name**: Implement expire_offline_nodes() and cleanup_orphaned_commands() on PostgreSQLJobDatabase
**Priority**: High
**Effort**: M
**Source PRP Document**: [docs/prps/cluster-mode-phase2.md](../prps/cluster-mode-phase2.md) — Task 3
**Dependencies**: T-201 (schema; `cluster_nodes` and `node_commands` tables exist)

#### Context & Background

**Source PRP Document**: docs/prps/cluster-mode-phase2.md

**Feature Overview**: Stale offline nodes accumulate indefinitely after crashes or
decommissions. `expire_offline_nodes()` hard-deletes them (and their orphaned command
rows) to keep the registry clean.

**As a** cluster operator
**I need** offline nodes past their TTL automatically removed from the registry
**So that** the Cluster tab does not accumulate ghost entries from decommissioned nodes

#### Dependencies

- **Prerequisite Tasks**: T-201
- **Parallel Tasks**: T-202, T-204
- **Integration Points**: T-208 (HeartbeatThread calls `expire_offline_nodes()` each tick)

#### Technical Requirements

- **REQ-1**: `expire_offline_nodes(expiry_days: int) -> list[str]` shall select all
  `node_id` values from `cluster_nodes` where `status = 'offline'` AND
  `last_seen < NOW() - make_interval(days => %s)`.
- **REQ-2**: Before deleting from `cluster_nodes`, it shall call
  `self.cleanup_orphaned_commands(expired_node_ids)`.
- **REQ-3**: It shall then `DELETE FROM cluster_nodes WHERE node_id = ANY(%s)` and
  return the list of deleted `node_id` values.
- **REQ-4**: `cleanup_orphaned_commands(node_ids: list[str]) -> int` shall return `0`
  immediately if `node_ids` is empty, otherwise
  `DELETE FROM node_commands WHERE node_id = ANY(%s)` and return `rowcount`.
- **REQ-5**: Both methods shall use the `_conn()` context manager.

**Technical Constraints**:

- Only `status = 'offline'` nodes are eligible — online nodes must never be deleted.
- `cleanup_orphaned_commands()` must be called BEFORE the `cluster_nodes` delete to
  prevent orphan row accumulation (no FK constraint enforces this).
- `expiry_days = 0` is the caller's responsibility to gate; the method itself deletes
  based on whatever interval is passed.

#### Files to Modify

```text
resources/daemon/db.py  - add expire_offline_nodes(), cleanup_orphaned_commands()
```

#### Key Implementation Steps

1. Add `expire_offline_nodes(self, expiry_days: int) -> list[str]`:
   - First `_conn()` block: SELECT expired node_ids (status=offline + stale last_seen)
   - If empty, return `[]`
   - Call `self.cleanup_orphaned_commands(expired)`
   - Second `_conn()` block: DELETE from `cluster_nodes WHERE node_id = ANY(%s)`
   - Return expired list
2. Add `cleanup_orphaned_commands(self, node_ids: list[str]) -> int`:
   - Guard: `if not node_ids: return 0`
   - `DELETE FROM node_commands WHERE node_id = ANY(%s)`; return `cur.rowcount`

Full pseudocode is in the PRP "Per-Task Pseudocode" section.

#### Code Patterns to Follow

- `make_interval(days => %s)` pattern: existing `cleanup_old_jobs()` in
  `resources/daemon/db.py` lines 410–425
- Bulk delete pattern: existing `delete_offline_nodes()` `resources/daemon/db.py`
  lines 789–797

#### Acceptance Criteria

```gherkin
Scenario 1: Expired offline node is deleted
  Given an offline node whose last_seen is 8 days ago
  When expire_offline_nodes(7) is called
  Then the node is removed from cluster_nodes
  And the node_id appears in the returned list

Scenario 2: Online node is never deleted
  Given an online node whose last_seen is 30 days ago
  When expire_offline_nodes(7) is called
  Then the online node remains in cluster_nodes

Scenario 3: Orphaned commands cleaned up first
  Given an expired offline node has 3 pending node_commands rows
  When expire_offline_nodes() runs
  Then all 3 node_commands rows are deleted before the cluster_nodes row

Scenario 4: Returns list of deleted node_ids
  Given two expired offline nodes A and B
  When expire_offline_nodes(1) is called
  Then the returned list contains both node_ids

Scenario 5: Empty result when no expired nodes
  Given no offline nodes are past the TTL
  When expire_offline_nodes(30) is called
  Then an empty list is returned and no rows are deleted
```

**Rule-Based Checklist**:

- [ ] Only `status = 'offline'` nodes are selected for deletion
- [ ] `cleanup_orphaned_commands()` called before `cluster_nodes` delete
- [ ] `cleanup_orphaned_commands([])` returns `0` without hitting the DB
- [ ] Both methods use `self._conn()` context manager
- [ ] No FK constraints added

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/db.py --fix
pyright resources/daemon/db.py
TEST_DB_URL=postgresql://user:pass@localhost/testdb \
    python -m pytest tests/test_cluster.py -x -q -k "TestNodeExpiryDB"
```

---

### T-204 — DB Methods for Log Archival in db.py

**Task ID**: T-204
**Task Name**: Implement get_logs_for_archival() and delete_logs_before() on PostgreSQLJobDatabase
**Priority**: High
**Effort**: S
**Source PRP Document**: [docs/prps/cluster-mode-phase2.md](../prps/cluster-mode-phase2.md) — Task 4
**Dependencies**: T-201 (schema; `logs` table exists from Phase 1)

#### Context & Background

**Source PRP Document**: docs/prps/cluster-mode-phase2.md

**Feature Overview**: `LogArchiver` (T-207) needs two DB methods: one to fetch rows
eligible for archival, one to delete them after the `.gz` files are confirmed written.

**As a** log archiver
**I need** DB methods to fetch and delete aged log rows
**So that** I can write them to disk before removing them from the DB

#### Dependencies

- **Prerequisite Tasks**: T-201
- **Parallel Tasks**: T-202, T-203
- **Integration Points**: T-207 (`LogArchiver._archive_from_db()` calls both methods)

#### Technical Requirements

- **REQ-1**: `get_logs_for_archival(before_days: int) -> list[dict]` shall execute
  `SELECT id, node_id, level, logger, message, timestamp FROM logs WHERE timestamp <
  NOW() - make_interval(days => %s) ORDER BY node_id, timestamp`.
- **REQ-2**: `delete_logs_before(before_days: int) -> int` shall execute
  `DELETE FROM logs WHERE timestamp < NOW() - make_interval(days => %s)` and return
  `cur.rowcount`.
- **REQ-3**: Both methods shall use the `_conn()` context manager.
- **REQ-4**: `get_logs_for_archival()` shall return each row as a plain `dict` (not a
  named tuple or cursor row object) to simplify JSON serialisation.

**Technical Constraints**:

- These methods are additive alongside the existing `cleanup_old_logs()`. Both may be
  active at the same time; `cleanup_old_logs()` is used when archival is disabled.
- `get_logs_for_archival()` ordering by `(node_id, timestamp)` is important — the
  Python grouping in `LogArchiver` relies on this order.

#### Files to Modify

```text
resources/daemon/db.py  - add get_logs_for_archival(), delete_logs_before()
```

#### Key Implementation Steps

1. Add `get_logs_for_archival(self, before_days: int) -> list[dict]`:
   - `SELECT id, node_id, level, logger, message, timestamp FROM logs WHERE timestamp
     < NOW() - make_interval(days => %s) ORDER BY node_id, timestamp`
   - Return `[dict(row) for row in cur.fetchall()]`
2. Add `delete_logs_before(self, before_days: int) -> int`:
   - `DELETE FROM logs WHERE timestamp < NOW() - make_interval(days => %s)`
   - Return `cur.rowcount`

#### Code Patterns to Follow

- `make_interval(days => %s)` and `cleanup_old_logs()`: `resources/daemon/db.py`
  lines 679–687
- `get_logs()` SELECT pattern (Phase 1): `resources/daemon/db.py`

#### Acceptance Criteria

```gherkin
Scenario 1: Returns only aged rows
  Given log rows for today and 10 days ago
  When get_logs_for_archival(7) is called
  Then only the 10-day-old rows are returned

Scenario 2: Rows ordered by node_id then timestamp
  Given logs from two nodes interleaved in insertion order
  When get_logs_for_archival() is called
  Then results are ordered by (node_id ASC, timestamp ASC)

Scenario 3: delete_logs_before returns rowcount
  Given 5 aged log rows in the DB
  When delete_logs_before(1) is called
  Then 5 is returned and all 5 rows are gone

Scenario 4: Returns dicts not cursor row objects
  Given aged log rows exist
  When get_logs_for_archival() is called
  Then each item in the list supports dict-style key access (e.g. row["node_id"])
```

**Rule-Based Checklist**:

- [ ] `get_logs_for_archival()` orders by `node_id, timestamp`
- [ ] Return type is `list[dict]` (not cursor rows)
- [ ] `delete_logs_before()` returns `int` row count
- [ ] Both methods use `self._conn()` context manager
- [ ] Existing `cleanup_old_logs()` is unchanged

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/db.py --fix
pyright resources/daemon/db.py
TEST_DB_URL=postgresql://user:pass@localhost/testdb \
    python -m pytest tests/test_cluster.py -x -q -k "archival or delete_logs"
```

---

### T-205 — SECRET_KEYS Constant in constants.py

**Task ID**: T-205
**Task Name**: Add SECRET_KEYS frozenset to constants.py
**Priority**: Critical
**Effort**: XS
**Source PRP Document**: [docs/prps/cluster-mode-phase2.md](../prps/cluster-mode-phase2.md) — Task 5
**Dependencies**: None

#### Context & Background

**Source PRP Document**: docs/prps/cluster-mode-phase2.md

**Feature Overview**: A single canonical set of secret key names that must never be
stored in `cluster_config`. Defined once in `constants.py` and imported by both
`db.py` and `config.py`.

**As a** security-conscious operator
**I need** a single authoritative list of secret config keys that are never persisted
to the database
**So that** API keys and credentials cannot leak through the cluster config sync mechanism

#### Dependencies

- **Prerequisite Tasks**: None
- **Parallel Tasks**: T-201, T-202, T-203, T-204
- **Integration Points**: T-202 (`set_cluster_config()` imports `SECRET_KEYS`);
  T-206 (`_strip_secrets()` in `config.py` imports `SECRET_KEYS`)

#### Technical Requirements

- **REQ-1**: A module-level `SECRET_KEYS: frozenset[str]` constant shall be added to
  `constants.py` containing `{"api_key", "db_url", "username", "password", "node_id"}`.
- **REQ-2**: The constant shall be a `frozenset` (immutable) not a `set`.

#### Files to Modify

```text
resources/daemon/constants.py  - add SECRET_KEYS frozenset at module level
```

#### Key Implementation Steps

1. Open `resources/daemon/constants.py`.
2. Add `SECRET_KEYS: frozenset[str] = frozenset({"api_key", "db_url", "username",
   "password", "node_id"})` as a module-level constant.

#### Acceptance Criteria

```gherkin
Scenario 1: Constant is importable and complete
  Given resources.daemon.constants is imported
  When SECRET_KEYS is accessed
  Then it is a frozenset containing exactly the five expected key names

Scenario 2: Immutability
  Given SECRET_KEYS is imported
  When an attempt is made to add or remove a value
  Then an AttributeError is raised (frozenset is immutable)
```

**Rule-Based Checklist**:

- [ ] `SECRET_KEYS` is a `frozenset` at module level
- [ ] Contains exactly: `api_key`, `db_url`, `username`, `password`, `node_id`
- [ ] No other existing constants in `constants.py` are modified

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/constants.py --fix
pyright resources/daemon/constants.py
python -c "from resources.daemon.constants import SECRET_KEYS; print(SECRET_KEYS)"
```

---

### T-206 — Config Merge in config.py

**Task ID**: T-206
**Task Name**: Add DB config merge to PathConfigManager.load_config() and new daemon config keys
**Priority**: Critical
**Effort**: L
**Source PRP Document**: [docs/prps/cluster-mode-phase2.md](../prps/cluster-mode-phase2.md) — Task 6
**Dependencies**: T-205 (SECRET_KEYS), T-202 (get_cluster_config() must exist)

#### Context & Background

**Source PRP Document**: docs/prps/cluster-mode-phase2.md

**Feature Overview**: `PathConfigManager.load_config()` is the single place where
daemon config is parsed. This task wires in four new config keys and an optional DB
config fetch-and-merge step so nodes automatically pick up a cluster-wide base config.

**As a** daemon node starting in distributed mode
**I need** my config merged from the DB base config and local sma-ng.yml (local wins)
**So that** I get cluster-wide defaults without manual file synchronisation,
while retaining local overrides

#### Dependencies

- **Prerequisite Tasks**: T-205, T-202
- **Parallel Tasks**: T-207 (no shared files)
- **Integration Points**: T-208 (HeartbeatThread reads new properties from
  `path_config_manager`); T-209 (handler reads `_config_file` for push-config);
  `server.py reload_config()` (must pass `job_db=self.job_db`)

#### Technical Requirements

- **REQ-1**: `_parse_config_data()` shall parse four new keys from the `daemon:` section:
  `node_expiry_days` (int, default 0), `log_archive_dir` (str or None, default None),
  `log_archive_after_days` (int, default 0), `log_delete_after_days` (int, default 0).
- **REQ-2**: `_apply_config_data()` shall assign `self._node_expiry_days`,
  `self._log_archive_dir`, `self._log_archive_after_days`, `self._log_delete_after_days`.
- **REQ-3**: Properties `node_expiry_days`, `log_archive_dir`, `log_archive_after_days`,
  `log_delete_after_days` shall be added to `PathConfigManager`.
- **REQ-4**: A private module-level function `_strip_secrets(data: dict) -> dict` shall
  deep-copy `data` and remove all keys in `SECRET_KEYS` from `data.get("daemon", {})`.
- **REQ-5**: `load_config(config_file, job_db=None)` — after parsing the local YAML,
  if `job_db` is not None and `job_db.is_distributed`, fetch and parse the DB base
  config and merge: `merged = {**db_parsed, **local_parsed}` (local wins per key).
- **REQ-6**: On `Exception` during the DB fetch, log a warning and continue with local
  config only.
- **REQ-7**: `server.py reload_config()` shall be updated to pass `job_db=self.job_db`
  to `load_config()`.

**Technical Constraints**:

- The very first `load_config()` call during `DaemonServer.__init__()` happens before
  `job_db` exists. Pass `job_db=None` on first startup; the DB merge only activates
  during `reload_config()`. This is intentional and documented in the PRP.
- Merge is at the parsed-dict key level, not YAML nesting level. Pass only the
  `daemon:` sub-section of the DB blob through `_parse_config_data()`.
- `int(config.get("key") or 0)` pattern for integer keys with zero default.

#### Files to Modify

```text
resources/daemon/config.py  - _parse_config_data(), _apply_config_data(),
                               load_config() signature, add _strip_secrets(),
                               add four new properties
resources/daemon/server.py  - reload_config(): pass job_db=self.job_db to load_config()
```

#### Key Implementation Steps

1. Add `_strip_secrets(data: dict) -> dict` private function at module level in
   `config.py` (imports `SECRET_KEYS` from `resources.daemon.constants`).
2. Extend `_parse_config_data()` with the four new keys using the existing
   `int(config.get(...) or default)` pattern.
3. Extend `_apply_config_data()` to assign the four new instance attributes.
4. Add the four properties.
5. Modify `load_config(self, config_file, job_db=None)`:
   - Parse local YAML as today → `local_parsed`
   - If `job_db` is not None and `job_db.is_distributed`: fetch DB blob, extract
     `db_raw.get("daemon", {})`, parse via `_parse_config_data()` → `db_parsed`;
     `merged = {**db_parsed, **local_parsed}`; wrap in `try/except Exception`
   - Call `self._apply_config_data(merged)` (or `local_parsed` on DB failure)
6. In `server.py reload_config()`: locate the `load_config()` call and add
   `job_db=self.job_db`.

Full pseudocode: PRP "Per-Task Pseudocode — Task 6" section.

#### Code Patterns to Follow

- `_parse_config_data()` key parsing: `resources/daemon/config.py` lines 286–343
- `_apply_config_data()`: `resources/daemon/config.py` lines 345–366
- `load_config()` flow: `resources/daemon/config.py` lines 234–256
- Atomic YAML write pattern (reference only): `_write_node_id_to_yaml()` lines 17–36

#### Acceptance Criteria

```gherkin
Scenario 1: DB config provides base values
  Given a DB config containing {"daemon": {"workers": 8}}
  And local sma-ng.yml has no workers key
  When load_config(config_file, job_db=job_db) is called
  Then path_config_manager.workers equals 8

Scenario 2: Local config overrides DB config
  Given a DB config containing {"daemon": {"workers": 8}}
  And local sma-ng.yml has workers: 2
  When load_config(config_file, job_db=job_db) is called
  Then path_config_manager.workers equals 2

Scenario 3: DB fetch failure falls back to local config
  Given get_cluster_config() raises an exception
  When load_config(config_file, job_db=job_db) is called
  Then a warning is logged
  And path_config_manager is configured from local YAML only

Scenario 4: No job_db (first startup) uses local config only
  Given job_db=None is passed
  When load_config(config_file, job_db=None) is called
  Then local config is used with no DB fetch attempted

Scenario 5: node_expiry_days defaults to 0
  Given the daemon section has no node_expiry_days key
  When load_config() runs
  Then path_config_manager.node_expiry_days returns 0

Scenario 6: _strip_secrets removes secret keys
  Given {"daemon": {"api_key": "x", "workers": 4}}
  When _strip_secrets() is called
  Then api_key is absent from the returned dict
  And workers is present
```

**Rule-Based Checklist**:

- [ ] Four new properties added (`node_expiry_days`, `log_archive_dir`,
  `log_archive_after_days`, `log_delete_after_days`)
- [ ] `load_config()` signature is `load_config(self, config_file, job_db=None)`
- [ ] Merge order: `{**db_parsed, **local_parsed}` (local wins)
- [ ] DB fetch wrapped in `try/except Exception` with warning log
- [ ] First startup with `job_db=None` does not attempt DB fetch
- [ ] `server.py reload_config()` passes `job_db=self.job_db`
- [ ] `_strip_secrets()` deep-copies before mutating

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/config.py resources/daemon/server.py --fix
pyright resources/daemon/config.py resources/daemon/server.py
python -m pytest tests/test_cluster.py -x -q -k "TestConfigMerge"
```

---

### T-207 — LogArchiver Helper Class (New File)

**Task ID**: T-207
**Task Name**: Create resources/daemon/log_archiver.py with LogArchiver class
**Priority**: High
**Effort**: L
**Source PRP Document**: [docs/prps/cluster-mode-phase2.md](../prps/cluster-mode-phase2.md) — Task 7
**Dependencies**: T-204 (get_logs_for_archival(), delete_logs_before() must exist)

#### Context & Background

**Source PRP Document**: docs/prps/cluster-mode-phase2.md

**Feature Overview**: `LogArchiver` encapsulates all filesystem archival logic:
fetching aged DB rows, grouping by `(node_id, date)`, writing gzipped JSONL files
atomically, deleting the DB rows only after successful write, and pruning old archive
files from disk.

**As a** daemon operator retaining log history
**I need** aged DB log rows written to compressed files on disk before being deleted
**So that** I can inspect historical logs without unbounded PostgreSQL growth

#### Dependencies

- **Prerequisite Tasks**: T-204
- **Parallel Tasks**: T-206 (no shared files)
- **Integration Points**: T-208 (HeartbeatThread instantiates `LogArchiver` and calls
  `run()` each tick)

#### Technical Requirements

- **REQ-1**: `LogArchiver.__init__(archive_dir, archive_after_days, delete_after_days, logger)`
  shall store all four parameters.
- **REQ-2**: `run(job_db)` shall call `_archive_from_db(job_db)` then
  `_prune_old_files()`.
- **REQ-3**: `_archive_from_db(job_db)` shall call `get_logs_for_archival()`, group
  results by `(node_id, timestamp.date())` in Python, call `_write_archive()` for each
  group, and call `delete_logs_before()` ONLY if all groups succeeded.
- **REQ-4**: `_write_archive(node_id, date, records)` shall write to
  `<archive_dir>/<node_id>/<YYYY-MM-DD>.jsonl.gz` atomically: write to `.tmp` file,
  then `os.replace(tmp, final)`. Return `True` on success, `False` on any exception.
- **REQ-5**: On `_write_archive()` failure: log a warning, attempt to clean up the
  `.tmp` file, and skip DB deletion for that tick.
- **REQ-6**: `_prune_old_files()` shall recursively scan `archive_dir`, check
  `os.path.getmtime()` against the `delete_after_days` cutoff, delete old `.gz`
  files, and return the count deleted. If `delete_after_days == 0`, return `0`
  immediately.
- **REQ-7**: stdlib only: `gzip`, `json`, `os`, `os.path`, `datetime`. No new
  dependencies.

**Technical Constraints**:

- Atomic write pattern: `gzip.open(tmp_path, "wt", encoding="utf-8")` → write JSONL →
  `os.replace(tmp_path, final_path)`.
- `timestamp` fields in records may be `datetime` objects; convert with
  `.isoformat()` before `json.dumps()`.
- If any group's `_write_archive()` fails, skip `delete_logs_before()` entirely for
  that tick — better to keep rows than lose them.

#### Files to Create

```text
resources/daemon/log_archiver.py  - NEW: LogArchiver class
```

#### Key Implementation Steps

1. Create `resources/daemon/log_archiver.py` with class `LogArchiver`.
2. Implement `__init__` storing four params; no external calls.
3. Implement `run(job_db)`: call `_archive_from_db()` then `_prune_old_files()`.
4. Implement `_archive_from_db(job_db)`:
   - Fetch rows with `job_db.get_logs_for_archival(self._archive_after_days)`
   - Group with `collections.defaultdict(list)` by `(row["node_id"], ts.date())`
   - Call `_write_archive()` per group; track `all_written`
   - If `all_written`: call `job_db.delete_logs_before(self._archive_after_days)`
5. Implement `_write_archive(node_id, date, records)` with atomic `.tmp` + rename.
6. Implement `_prune_old_files()` using `os.scandir` and `os.path.getmtime`.

Full pseudocode for `_archive_from_db()` and `_write_archive()`: PRP "Per-Task
Pseudocode — Task 7" section.

#### Code Patterns to Follow

- Clean class structure: `resources/daemon/db_log_handler.py`
- Atomic write pattern: `resources/daemon/config.py` `_write_node_id_to_yaml()`
  (tmp + `os.replace`)
- `gzip.open("wt", encoding="utf-8")` + `json.dumps(row) + "\n"`: PRP gotchas section

#### Acceptance Criteria

```gherkin
Scenario 1: Archive file created with correct path
  Given aged log rows for node "abc" on 2025-01-10
  When _write_archive("abc", date(2025, 1, 10), records) is called
  Then <archive_dir>/abc/2025-01-10.jsonl.gz exists
  And each line is valid JSON with the expected fields

Scenario 2: Atomic write — no corrupt files on failure
  Given a write that is interrupted mid-stream
  When _write_archive() raises an exception
  Then no partial final file exists at the target path
  And the .tmp file is cleaned up

Scenario 3: DB rows deleted only after successful write
  Given two groups of aged logs, both written successfully
  When _archive_from_db() completes
  Then delete_logs_before() is called exactly once

Scenario 4: DB deletion skipped when any write fails
  Given two groups of aged logs, one write fails
  When _archive_from_db() completes
  Then delete_logs_before() is NOT called

Scenario 5: Old archive files pruned
  Given a .gz file whose mtime is older than delete_after_days
  When _prune_old_files() is called
  Then the file is deleted

Scenario 6: Recent archive files kept
  Given a .gz file whose mtime is within delete_after_days
  When _prune_old_files() is called
  Then the file is NOT deleted

Scenario 7: delete_after_days=0 disables pruning
  Given delete_after_days=0
  When _prune_old_files() is called
  Then no files are deleted and 0 is returned
```

**Rule-Based Checklist**:

- [ ] Atomic write: `.tmp` written first, then `os.replace(tmp, final)`
- [ ] `delete_logs_before()` called only when all group writes succeed
- [ ] `_write_archive()` returns `bool` (True/False)
- [ ] `timestamp` serialised via `.isoformat()` before `json.dumps()`
- [ ] `delete_after_days == 0` short-circuits `_prune_old_files()` immediately
- [ ] No external dependencies beyond stdlib and existing project imports

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/log_archiver.py --fix
pyright resources/daemon/log_archiver.py
python -m pytest tests/test_cluster.py -x -q -k "TestLogArchivalThread"
```

---

### T-208 — HeartbeatThread Extensions in threads.py

**Task ID**: T-208
**Task Name**: Add node expiry and log archival calls to HeartbeatThread.run()
**Priority**: High
**Effort**: M
**Source PRP Document**: [docs/prps/cluster-mode-phase2.md](../prps/cluster-mode-phase2.md) — Task 8
**Dependencies**: T-203, T-206, T-207

#### Context & Background

**Source PRP Document**: docs/prps/cluster-mode-phase2.md

**Feature Overview**: `HeartbeatThread.run()` already calls `cleanup_old_logs()` on
each tick. Phase 2 appends two more tick-level operations: node expiry and log
archival. Both read their config from `path_config_manager` on each tick (not from
`__init__` args) to respect hot-reload.

**As a** daemon running in distributed mode
**I need** the heartbeat tick to automatically expire stale nodes and archive aged logs
**So that** the cluster registry stays clean and log history is preserved without manual intervention

#### Dependencies

- **Prerequisite Tasks**: T-203, T-206, T-207
- **Parallel Tasks**: T-209 (no shared files)
- **Integration Points**: `server.py` HeartbeatThread construction (no new parameters
  needed — settings read from `path_config_manager`)

#### Technical Requirements

- **REQ-1**: At the end of `run()`'s tick body, after the existing `cleanup_old_logs()`
  block, add a node expiry block:
  `expiry_days = self.server.path_config_manager.node_expiry_days;
  if self.job_db.is_distributed and expiry_days > 0: self.job_db.expire_offline_nodes(expiry_days)`.
- **REQ-2**: After the expiry block, add a log archival block that reads
  `log_archive_dir`, `log_archive_after_days`, and `log_delete_after_days` from
  `path_config_manager` on each tick, constructs a `LogArchiver`, and calls `run()`.
- **REQ-3**: The archival block shall only execute when `is_distributed`, `archive_dir`
  is not None, and `archive_after_days > 0`.
- **REQ-4**: All new settings shall be read from `path_config_manager` on each tick —
  NOT stored as `__init__` attributes — to respect hot-reload.

**Technical Constraints**:

- `HeartbeatThread` is NOT restarted in `reload_config()`. Reading from
  `self.server.path_config_manager` on each tick is the only safe way to pick up
  config changes.
- Import `LogArchiver` inside the conditional block (lazy import) to avoid circular
  imports during startup.

#### Files to Modify

```text
resources/daemon/threads.py  - HeartbeatThread.run(): add expiry + archival blocks
```

#### Key Implementation Steps

1. Locate the end of the tick body in `HeartbeatThread.run()` (after the
   `cleanup_old_logs()` call).
2. Add the node expiry block (reads `node_expiry_days` from `path_config_manager`
   each tick; calls `expire_offline_nodes()` and logs each expired node id).
3. Add the log archival block (reads three archival settings; constructs
   `LogArchiver(archive_dir, archive_after, delete_after, self.log)` and calls
   `archiver.run(self.job_db)`).
4. Both blocks gated on `self.job_db.is_distributed`.

Reference: PRP "Task 8" blueprint section for exact code structure.

#### Code Patterns to Follow

- Existing `cleanup_old_logs()` block in `HeartbeatThread.run()`:
  `resources/daemon/threads.py` lines 45–67
- `self.server.path_config_manager` access pattern (already present in `threads.py`)

#### Acceptance Criteria

```gherkin
Scenario 1: Offline node expired on heartbeat tick
  Given node_expiry_days=7 and an offline node with last_seen 10 days ago
  When the HeartbeatThread tick fires
  Then expire_offline_nodes(7) is called
  And the expired node_id is logged at INFO level

Scenario 2: node_expiry_days=0 disables expiry
  Given node_expiry_days=0
  When the heartbeat tick fires
  Then expire_offline_nodes() is NOT called

Scenario 3: Log archival runs when configured
  Given log_archive_dir="/tmp/archive" and log_archive_after_days=7
  When the heartbeat tick fires
  Then a LogArchiver is constructed and run() is called

Scenario 4: Log archival skipped when archive_dir is None
  Given log_archive_dir=None
  When the heartbeat tick fires
  Then LogArchiver.run() is NOT called

Scenario 5: Settings picked up after hot reload without restart
  Given HeartbeatThread is running with node_expiry_days=0
  When the config is reloaded and node_expiry_days is set to 3
  Then on the next tick expire_offline_nodes(3) is called
```

**Rule-Based Checklist**:

- [ ] Settings read from `path_config_manager` on each tick (not stored in `__init__`)
- [ ] Both blocks gated on `self.job_db.is_distributed`
- [ ] `node_expiry_days == 0` prevents `expire_offline_nodes()` call
- [ ] `archive_dir is None` or `archive_after_days == 0` prevents archival
- [ ] `LogArchiver` import is inside the conditional block (lazy import)

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/threads.py --fix
pyright resources/daemon/threads.py
python -m pytest tests/test_cluster.py tests/test_threads.py -x -q
```

---

### T-209 — Admin API Endpoints in handler.py and routes.py

**Task ID**: T-209
**Task Name**: Add GET/POST /admin/config endpoints and push-config node action to handler
**Priority**: High
**Effort**: M
**Source PRP Document**: [docs/prps/cluster-mode-phase2.md](../prps/cluster-mode-phase2.md) — Tasks 9 & 10
**Dependencies**: T-202 (get_cluster_config(), set_cluster_config()), T-205 (SECRET_KEYS)

#### Context & Background

**Source PRP Document**: docs/prps/cluster-mode-phase2.md

**Feature Overview**: Three new API interactions expose config management to the admin
UI and to operator scripts: read the current DB config, write a new config, and push
the current node's local config to the DB.

**As a** cluster operator or admin UI
**I need** API endpoints to read, write, and push the cluster base config
**So that** I can manage the shared config without direct database access

#### Dependencies

- **Prerequisite Tasks**: T-202, T-205
- **Parallel Tasks**: T-208 (no shared files)
- **Integration Points**: `resources/daemon/routes.py` (route registration);
  `resources/admin.html` (T-210 calls these endpoints)

#### Technical Requirements

- **REQ-1**: `_get_admin_config(path, query)` shall return `503` if
  `not job_db.is_distributed`, else `{"config": raw or {}}` as JSON with status `200`.
- **REQ-2**: `_post_admin_config(path, query)` shall read the request body as JSON,
  validate it contains a `"config"` key, strip secrets via `_strip_secrets()`, call
  `job_db.set_cluster_config()`, and return `200 {"status": "saved"}`.
- **REQ-3**: `_post_admin_node_action()` shall be extended with a `"push-config"`
  branch: read the node's local `sma-ng.yml` via
  `yamlconfig.load(path_config_manager._config_file)`, strip secrets, call
  `job_db.set_cluster_config(data, updated_by=actor)`, return `200 {"status": "pushed"}`.
- **REQ-4**: Both new routes shall be registered in `routes.py` `_get_routes()` and
  `_post_routes()` respectively.
- **REQ-5**: None of the new endpoints shall appear in `PUBLIC_ENDPOINTS` — they require
  authentication.

**Technical Constraints**:

- `push-config` action shares the `/admin/nodes/<node_id>/<action>` URL structure
  already handled by `_post_prefix_routes()`. Only `_post_admin_node_action()` needs
  extending — no new route entry for this path.
- Body parsing follows the `_read_json_paths()` pattern in `handler.py` lines 108–124.
- Return auth errors using the existing `_auth_error()` pattern if needed.

#### Files to Modify

```text
resources/daemon/handler.py  - add _get_admin_config(), _post_admin_config(),
                                extend _post_admin_node_action()
resources/daemon/routes.py   - add GET /admin/config, POST /admin/config to dicts
```

#### API Specification

```yaml
Endpoint 1:
  Method: GET
  Path: /admin/config
  Auth: Required (not in PUBLIC_ENDPOINTS)
  Response 200:
    body: {"config": {<yaml dict or empty dict>}}
  Response 503:
    body: {"error": "Not in distributed mode"}

Endpoint 2:
  Method: POST
  Path: /admin/config
  Auth: Required
  Request Body: {"config": {<yaml dict>}}
  Response 200:
    body: {"status": "saved"}

Endpoint 3 (extends existing node action):
  Method: POST
  Path: /admin/nodes/<node_id>/push-config
  Auth: Required
  Response 200:
    body: {"status": "pushed"}
```

#### Key Implementation Steps

1. Add `_get_admin_config(self, path, query)` to `WebhookHandler` following the
   `_get_cluster_logs()` pattern (lines 411–439).
2. Add `_post_admin_config(self, path, query)` using `_read_json_paths()` for body
   parsing; call `_strip_secrets()` before `set_cluster_config()`.
3. In `_post_admin_node_action()`, add an `elif action == "push-config":` branch
   matching the blueprint in PRP Task 9.
4. In `routes.py` `_get_routes()`, add:
   `"/admin/config": lambda h, p, q: h._get_admin_config(p, q)`
5. In `routes.py` `_post_routes()`, add:
   `"/admin/config": lambda h, p, q: h._post_admin_config(p, q)`

#### Code Patterns to Follow

- `_get_cluster_logs()` auth-gated GET pattern: `resources/daemon/handler.py`
  lines 411–439
- `_post_admin_node_action()` action dispatch: `resources/daemon/handler.py`
  lines 531–591
- `_read_json_paths()` body parsing: `resources/daemon/handler.py` lines 108–124
- Route registration: `resources/daemon/routes.py` `_get_routes()` lines 55–71

#### Acceptance Criteria

```gherkin
Scenario 1: GET /admin/config returns current config
  Given a config blob stored in cluster_config
  When GET /admin/config is requested with valid auth
  Then 200 is returned with the config dict

Scenario 2: GET /admin/config returns empty dict on fresh DB
  Given no row in cluster_config
  When GET /admin/config is requested
  Then 200 is returned with {"config": {}}

Scenario 3: POST /admin/config strips secrets
  Given a payload {"config": {"daemon": {"api_key": "x", "workers": 4}}}
  When POST /admin/config is sent
  Then 200 {"status": "saved"} is returned
  And a subsequent GET returns config without api_key

Scenario 4: push-config strips local secrets
  Given the node's sma-ng.yml contains api_key and db_url
  When POST /admin/nodes/<node_id>/push-config is sent
  Then the stored config has neither api_key nor db_url

Scenario 5: /admin/config requires authentication
  Given the daemon is running with an API key
  When GET /admin/config is requested without Authorization header
  Then 401 is returned

Scenario 6: Non-distributed mode returns 503
  Given job_db.is_distributed is False
  When GET /admin/config is requested
  Then 503 is returned
```

**Rule-Based Checklist**:

- [ ] `_get_admin_config()` and `_post_admin_config()` are NOT in `PUBLIC_ENDPOINTS`
- [ ] `GET /admin/config` registered in `_get_routes()`
- [ ] `POST /admin/config` registered in `_post_routes()`
- [ ] `push-config` handled in `_post_admin_node_action()` — no new route entry needed
- [ ] Secrets stripped before any `set_cluster_config()` call
- [ ] `503` returned when `not job_db.is_distributed`

#### Validation Commands

```bash
source venv/bin/activate
ruff check resources/daemon/handler.py resources/daemon/routes.py --fix
pyright resources/daemon/handler.py resources/daemon/routes.py
python -m pytest tests/test_cluster.py -x -q -k "admin_config or push_config"
```

---

### T-210 — Admin UI Config Section in admin.html

**Task ID**: T-210
**Task Name**: Add cluster Config section with YAML editor and push-from-node button to admin.html
**Priority**: High
**Effort**: M
**Source PRP Document**: [docs/prps/cluster-mode-phase2.md](../prps/cluster-mode-phase2.md) — Task 11
**Dependencies**: T-209 (API endpoints must exist to call from the UI)

#### Context & Background

**Source PRP Document**: docs/prps/cluster-mode-phase2.md

**Feature Overview**: Operators need a web UI to view and edit the cluster base config
without direct database access. The Config section sits in the existing Cluster tab,
below the log viewer.

**As a** cluster operator using the admin web UI
**I need** a config editor section in the Cluster tab
**So that** I can update the shared base config and push my local config to the cluster
without using the API directly

#### Dependencies

- **Prerequisite Tasks**: T-209
- **Parallel Tasks**: T-211 (no shared files)
- **Integration Points**: Alpine.js `adminPage()` component; `authHeaders()` helper;
  Cluster tab HTML structure

#### Technical Requirements

- **REQ-1**: Add to `adminPage()` Alpine.js data: `clusterConfig: ''`,
  `configLoading: false`, `configSaving: false`, `configError: ''`, `configSaved: false`.
- **REQ-2**: Add `loadClusterConfig()` method: `GET /admin/config` with `authHeaders()`;
  convert the returned JSON config object to a YAML string for editor display.
- **REQ-3**: Add `saveClusterConfig()` method: parse the editor text, `POST /admin/config`
  as JSON `{"config": <parsed>}`, show success or error state.
- **REQ-4**: Add `pushNodeConfig(node)` method: `POST /admin/nodes/<node.node_id>/push-config`
  with `authHeaders()`.
- **REQ-5**: Add a Config section to the Cluster tab (after the log viewer) containing:
  a `<textarea>` bound to `clusterConfig`, a Save button, a "Push from this node"
  button, and error/success feedback.
- **REQ-6**: Before implementing YAML serialisation in the browser, check whether
  `js-yaml` is already loaded in `admin.html`. If not, use a plain JSON editor fallback
  (display and accept JSON rather than YAML) rather than adding a new CDN dependency.

**Technical Constraints**:

- Follow the existing Alpine.js `authHeaders()` fetch pattern for all three methods.
- No new CSS frameworks. Style consistently with existing Cluster tab UI.
- The Config section is only meaningful in distributed mode; gate the section's
  visibility on a condition (e.g. the `clusterNodes` array is non-empty, or add a
  `isDistributed` flag from the API).

#### Files to Modify

```text
resources/admin.html  - extend adminPage() data and methods, add Config section HTML
```

#### Key Implementation Steps

1. Open `resources/admin.html` and locate the `adminPage()` Alpine.js component
   (lines 244–261).
2. Add the five new data properties to the `data()` return object.
3. Add `loadClusterConfig()`, `saveClusterConfig()`, and `pushNodeConfig(node)` methods
   following the `loadClusterLogs()` fetch pattern (lines 345–361).
4. Locate the Cluster tab HTML and add the Config section below the log viewer:
   `<textarea x-model="clusterConfig">`, Save button, push button, status feedback.
5. Check for `js-yaml` before using it; if absent use JSON display.

#### Code Patterns to Follow

- `loadClusterLogs()` fetch pattern: `resources/admin.html` lines 345–361
- `authHeaders()` helper: `resources/admin.html` lines 289–293
- Cluster tab structure: `resources/admin.html` Cluster tab section

#### Acceptance Criteria

```gherkin
Scenario 1: Config editor loads on tab open
  Given the Cluster tab is opened and the DB has a config
  When loadClusterConfig() fires
  Then the textarea is populated with the config content

Scenario 2: Save writes config to DB
  Given the operator edits the textarea and clicks Save
  When saveClusterConfig() is called
  Then POST /admin/config is sent with the edited content
  And a success message is shown

Scenario 3: Push from this node updates DB config
  Given the operator clicks "Push from this node"
  When pushNodeConfig() is called
  Then POST /admin/nodes/<node_id>/push-config is sent
  And a success/error state is displayed

Scenario 4: Error state shown on save failure
  Given POST /admin/config returns a non-200 response
  When saveClusterConfig() completes
  Then configError is populated and displayed to the operator

Scenario 5: Loading state prevents double submission
  Given a save is in progress (configSaving=true)
  When the Save button is inspected
  Then it is disabled or shows a loading indicator
```

**Rule-Based Checklist**:

- [ ] Five new Alpine.js data props present in `adminPage()`
- [ ] `loadClusterConfig()`, `saveClusterConfig()`, `pushNodeConfig()` methods added
- [ ] All three methods use `authHeaders()` for authenticated requests
- [ ] `<textarea>` bound to `clusterConfig` with `x-model`
- [ ] Save and push buttons present with loading/disabled states
- [ ] Error and success feedback displayed
- [ ] No new CDN dependencies added without checking existing scripts

#### Validation Commands

```bash
# Serve the daemon locally and verify in browser:
# 1. Open the Cluster tab
# 2. Confirm Config section is visible
# 3. Load, edit, and save a config blob
# 4. Click push-from-this-node and confirm success message
python -m pytest tests/ -x -q
```

---

### T-211 — sma-ng.yml.sample Update

**Task ID**: T-211
**Task Name**: Document four new daemon config keys in setup/sma-ng.yml.sample
**Priority**: Medium
**Effort**: XS
**Source PRP Document**: [docs/prps/cluster-mode-phase2.md](../prps/cluster-mode-phase2.md) — Task 12
**Dependencies**: T-206 (new config fields must be defined in config.py before sample
is updated)

#### Context & Background

**Source PRP Document**: docs/prps/cluster-mode-phase2.md

**Feature Overview**: `setup/sma-ng.yml.sample` is the canonical reference for new
deployments. Four new `daemon:` section keys must be documented with explanatory
comments so operators understand their purpose and defaults.

**As a** new operator setting up cluster mode
**I need** the sample config to document all four Phase 2 daemon keys
**So that** I understand node expiry and log archival options without reading source code

#### Dependencies

- **Prerequisite Tasks**: T-206 (keys must be defined in `config.py` first)
- **Parallel Tasks**: T-210, T-212
- **Integration Points**: `setup/sma-ng.yml.sample`

#### Technical Requirements

- **REQ-1**: Add `node_expiry_days: 0` with a comment explaining that offline nodes
  are hard-deleted after this many days and that 0 disables automatic expiry.
- **REQ-2**: Add `log_archive_dir: null` with a comment explaining gzipped JSONL
  archival and that null disables archival.
- **REQ-3**: Add `log_archive_after_days: 0` with a comment explaining the move-to-disk
  threshold and that 0 disables archival (logs deleted by `log_ttl_days` instead).
- **REQ-4**: Add `log_delete_after_days: 0` with a comment explaining archive file
  pruning and that 0 disables filesystem cleanup.
- **REQ-5**: All four keys shall be added after `log_ttl_days` in the `daemon:` section.

#### Files to Modify

```text
setup/sma-ng.yml.sample  - add four new keys to daemon: section after log_ttl_days
```

#### Key Implementation Steps

1. Open `setup/sma-ng.yml.sample` and locate `log_ttl_days` in the `daemon:` section.
2. Append the four new keys and comments from the PRP Task 12 specification block
   immediately after `log_ttl_days`, preserving existing indentation style.

#### Acceptance Criteria

```gherkin
Scenario 1: All four keys present
  Given setup/sma-ng.yml.sample is opened
  When the daemon: section is inspected
  Then node_expiry_days, log_archive_dir, log_archive_after_days,
       and log_delete_after_days are present

Scenario 2: Default values are correct
  Given the sample is parsed by ruamel.yaml
  When daemon.node_expiry_days is read
  Then it equals 0

Scenario 3: YAML remains valid
  Given the sample has been modified
  When python3 -c "from ruamel.yaml import YAML; YAML().load(...)" is run
  Then no parse error is raised
```

**Rule-Based Checklist**:

- [ ] `node_expiry_days: 0` present with comment about disable-with-zero behaviour
- [ ] `log_archive_dir: null` present with comment about format and disable behaviour
- [ ] `log_archive_after_days: 0` present with comment
- [ ] `log_delete_after_days: 0` present with comment
- [ ] All four appear after `log_ttl_days`
- [ ] No existing keys removed or reindented
- [ ] YAML is valid (parseable by ruamel.yaml)

#### Validation Commands

```bash
python3 -c "from ruamel.yaml import YAML; YAML().load(open('setup/sma-ng.yml.sample'))"
```

---

### T-212 — Tests for All Phase 2 Features

**Task ID**: T-212
**Task Name**: Extend tests/test_cluster.py with TestClusterConfigDB, TestNodeExpiryDB, TestLogArchivalThread, TestConfigMerge
**Priority**: High
**Effort**: L
**Source PRP Document**: [docs/prps/cluster-mode-phase2.md](../prps/cluster-mode-phase2.md) — Task 13
**Dependencies**: T-201 through T-209 (all implementation tasks — tests validate them)

#### Context & Background

**Source PRP Document**: docs/prps/cluster-mode-phase2.md

**Feature Overview**: Full automated test coverage for all Phase 2 features across
four test classes. DB-dependent tests skip gracefully without `TEST_DB_URL`; unit tests
run in CI without any external service.

**As a** developer working on Phase 2
**I need** comprehensive automated tests
**So that** regressions are caught by CI and implementation correctness is verifiable
without a full cluster setup

#### Dependencies

- **Prerequisite Tasks**: T-201, T-202, T-203, T-204, T-205, T-206, T-207, T-208, T-209
- **Parallel Tasks**: T-211, T-213
- **Integration Points**: `tests/conftest.py` (job_db fixture, TEST_DB_URL skip);
  `tests/test_cluster.py` (extend existing file)

#### Technical Requirements

**TestClusterConfigDB** (`@pytest.mark.usefixtures("job_db")`):

- **REQ-1**: `test_set_and_get_cluster_config_roundtrip` — set a config, get it back, assert equality
- **REQ-2**: `test_secrets_are_stripped_before_storing` — payload with `api_key` stored without it
- **REQ-3**: `test_get_cluster_config_returns_none_when_absent` — empty table returns None
- **REQ-4**: `test_set_cluster_config_overwrites_existing_row` — second set replaces first;
  exactly one row
- **REQ-5**: `test_get_cluster_config_returns_dict_not_string` — return type is dict

**TestNodeExpiryDB** (`@pytest.mark.usefixtures("job_db")`):

- **REQ-6**: `test_expire_offline_nodes_deletes_old_offline_rows`
- **REQ-7**: `test_expire_offline_nodes_skips_online_nodes`
- **REQ-8**: `test_expire_offline_nodes_cleans_up_node_commands`
- **REQ-9**: `test_expire_offline_nodes_returns_deleted_node_ids`
- **REQ-10**: `test_zero_expiry_days_is_noop` — call with `days=0`; assert no deletion

**TestLogArchivalThread** (unit, no DB):

- **REQ-11**: `test_write_archive_creates_gz_file` — valid gzip + JSONL written
- **REQ-12**: `test_write_archive_is_atomic` — `.tmp` file replaced by final; no partial
- **REQ-13**: `test_prune_old_files_deletes_expired_gz`
- **REQ-14**: `test_prune_old_files_keeps_recent_gz`
- **REQ-15**: `test_run_calls_archive_then_prune` — mock `job_db`; assert call order

**TestConfigMerge** (unit, no DB):

- **REQ-16**: `test_db_config_provides_base_values`
- **REQ-17**: `test_local_config_overrides_db_config`
- **REQ-18**: `test_strip_secrets_removes_api_key_and_db_url`
- **REQ-19**: `test_strip_secrets_preserves_non_secret_keys`
- **REQ-20**: `test_merge_handles_none_db_config_gracefully`

**Technical Constraints**:

- DB-required tests: decorate with `@pytest.mark.usefixtures("job_db")` or skip via
  the `TEST_DB_URL` pattern from `tests/conftest.py`.
- Unit tests (TestLogArchivalThread, TestConfigMerge): must run without any DB or
  external service.
- Use `tmpdir` or `tmp_path` pytest fixtures for filesystem-based archival tests.
- Each DB test must clean up rows it creates to avoid cross-test contamination.

#### Files to Modify

```text
tests/test_cluster.py  - EXTEND: add four new test classes
```

#### Key Implementation Steps

1. Read `tests/conftest.py` for the `job_db` fixture and `TEST_DB_URL` skip pattern;
   mirror for all `*DB` classes.
2. Read existing `tests/test_cluster.py` for the `_unique_node()` helper and class
   structure patterns.
3. Implement `TestClusterConfigDB` with five tests (REQ-1 through REQ-5).
4. Implement `TestNodeExpiryDB` with five tests (REQ-6 through REQ-10).
5. Implement `TestLogArchivalThread` with five tests (REQ-11 through REQ-15) using
   `tmp_path` for directory creation and `unittest.mock` for `job_db`.
6. Implement `TestConfigMerge` with five tests (REQ-16 through REQ-20) using
   `unittest.mock.MagicMock` for `job_db` and `PathConfigManager`.

#### Code Patterns to Follow

- `job_db` fixture and skip pattern: `tests/conftest.py` lines 393–405
- `_unique_node()` helper: `tests/test_cluster.py`
- Class-based test structure: existing `tests/test_cluster.py`

#### Acceptance Criteria

```gherkin
Scenario 1: All tests pass without PostgreSQL
  Given TEST_DB_URL is not set
  When python -m pytest tests/test_cluster.py -x -q is run
  Then TestLogArchivalThread and TestConfigMerge tests all pass
  And TestClusterConfigDB and TestNodeExpiryDB tests are skipped

Scenario 2: All tests pass with PostgreSQL
  Given TEST_DB_URL is set to a valid connection string
  When python -m pytest tests/test_cluster.py -x -q is run
  Then all 20 new tests pass

Scenario 3: Full suite unaffected
  Given all Phase 2 implementation is complete
  When python -m pytest tests/ -x -q is run
  Then all 2303+ pre-existing tests pass
  And all new tests pass
```

**Rule-Based Checklist**:

- [ ] All 20 test cases (REQ-1 through REQ-20) implemented
- [ ] DB-dependent tests skip gracefully without `TEST_DB_URL`
- [ ] Unit tests run without any external service
- [ ] Filesystem tests use `tmp_path` fixture (not hardcoded paths)
- [ ] Each DB test cleans up its rows (no cross-test contamination)
- [ ] `_unique_node()` helper pattern used for DB tests that insert nodes

#### Validation Commands

```bash
source venv/bin/activate

# Unit tests only (no DB required)
python -m pytest tests/test_cluster.py -x -q -k "not DB"

# Full cluster tests with DB
TEST_DB_URL=postgresql://user:pass@localhost/testdb \
    python -m pytest tests/test_cluster.py -x -q

# Full suite
python -m pytest tests/ -x -q
```

---

### T-213 — Documentation Update for Phase 2

**Task ID**: T-213
**Task Name**: Add Phase 2 sub-sections to docs/daemon.md Cluster Mode section
**Priority**: Medium
**Effort**: M
**Source PRP Document**: [docs/prps/cluster-mode-phase2.md](../prps/cluster-mode-phase2.md) — Task 14
**Dependencies**: T-201 through T-210 (implementation must be complete to document
accurately)

#### Context & Background

**Source PRP Document**: docs/prps/cluster-mode-phase2.md

**Feature Overview**: `docs/daemon.md` is the canonical daemon reference. Three new
sub-sections must be added to the existing Cluster Mode section and mirrored to the
wiki and `resources/docs.html` per `CLAUDE.md` documentation rules.

**As a** cluster operator new to Phase 2 features
**I need** complete documentation for config sync, node expiry, and log archival
**So that** I can configure these features correctly without reading source code

#### Dependencies

- **Prerequisite Tasks**: T-201 through T-210 (all implementation)
- **Parallel Tasks**: T-211, T-212
- **Integration Points**: `docs/daemon.md`; `/tmp/sma-wiki/` GitHub wiki;
  `resources/docs.html`

#### Technical Requirements

- **REQ-1**: Add sub-section "Centralised Base Config" covering: how DB config is
  fetched on startup and merged (local wins), the admin UI config editor and
  push-from-node, and which secrets are never stored in the DB.
- **REQ-2**: Add sub-section "Node Expiry" covering: `node_expiry_days` setting, its
  interaction with stale recovery (Phase 1), and the two-stage flow (online → offline
  → deleted).
- **REQ-3**: Add sub-section "Log Archival" covering: `log_archive_after_days`,
  `log_delete_after_days`, `log_archive_dir`, archive file format
  (`<dir>/<node_id>/<YYYY-MM-DD>.jsonl.gz`), and interaction with `log_ttl_days`.
- **REQ-4**: All three sub-sections must be in the same commit as the implementation
  tasks (per `CLAUDE.md`).
- **REQ-5**: Wiki page and `resources/docs.html` must be updated to match.

**Technical Constraints**:

- Markdown must pass `markdownlint` with no errors (CLAUDE.md rule).
- Lines must not exceed 120 characters.
- All fenced code blocks must declare a language identifier.

#### Files to Modify

```text
docs/daemon.md            - add three sub-sections to Cluster Mode section
/tmp/sma-wiki/<page>.md   - update corresponding wiki page
resources/docs.html       - update inline help section
```

#### Key Implementation Steps

1. Open `docs/daemon.md`, locate the Cluster Mode section added in Phase 1.
2. Append the three sub-sections specified in REQ-1 through REQ-3.
3. Run `markdownlint docs/daemon.md`; fix any violations before committing.
4. Mirror changes to the corresponding wiki page in `/tmp/sma-wiki/` and push.
5. Update `resources/docs.html` inline help with equivalent content.

#### Acceptance Criteria

```gherkin
Scenario 1: All three sub-sections present
  Given docs/daemon.md is opened
  When the Cluster Mode section is inspected
  Then "Centralised Base Config", "Node Expiry", and "Log Archival"
       sub-sections all exist

Scenario 2: Markdownlint passes
  Given docs/daemon.md has been modified
  When markdownlint docs/daemon.md is run
  Then no warnings or errors are reported

Scenario 3: Wiki and docs.html in sync
  Given the daemon.md sections are written
  When /tmp/sma-wiki/ and resources/docs.html are checked
  Then equivalent content is present in both locations
```

**Rule-Based Checklist**:

- [ ] "Centralised Base Config" sub-section covers: merge order, local wins, secrets
  list, admin UI paths
- [ ] "Node Expiry" sub-section covers: `node_expiry_days`, two-stage flow, 0=disabled
- [ ] "Log Archival" sub-section covers: three config keys, file path format,
  interaction with `log_ttl_days`
- [ ] No markdownlint warnings
- [ ] All fenced code blocks have language identifiers
- [ ] Lines do not exceed 120 characters
- [ ] Wiki page updated and pushed
- [ ] `resources/docs.html` updated

#### Validation Commands

```bash
markdownlint docs/daemon.md
cd /tmp/sma-wiki && git add -A && \
    git commit -m "docs: add cluster mode phase 2 sections" && \
    git push origin HEAD:master
```

---

## Implementation Recommendations

### Suggested Team Structure

- **Developer A** (data layer): T-201, T-202, T-203, T-204, T-205 — all changes
  confined to `db.py` and `constants.py`. No UI or threading work. Completes Phase 1.
- **Developer B** (behavioural layer): T-206, T-207, T-208 — config merge, new
  `log_archiver.py`, and HeartbeatThread extensions. Picks up after T-202/T-205 merge.
- **Developer C** (API + UI): T-209, T-210 — handler and routes, then admin UI.
  Can begin T-210 HTML scaffolding immediately; completes API calls after T-209 merges.
- **Developer A or B** (finishing): T-211, T-212, T-213 — sample config, tests, docs.
  T-211 can be done in parallel with T-206; T-212 and T-213 require all implementation
  to be merged first.

### Optimal Task Sequencing

```text
Sprint 1 (Phase 1 data layer — parallel):
  T-205 (SECRET_KEYS, no deps)  ──┐
  T-201 (schema, no deps)       ──┤ all start day 1
  T-203 (node expiry methods)   ──┤ (after T-201)
  T-204 (log archival methods)  ──┘ (after T-201)
  T-202 (config DB methods)     ── after T-201 + T-205

Sprint 2 (Phase 2 behavioural — parallel after Phase 1):
  T-211 (sample config)         ── after T-206 (can be 30-min task)
  T-206 (config merge)          ── after T-202 + T-205
  T-207 (LogArchiver)           ── after T-204
  T-208 (HeartbeatThread)       ── after T-203 + T-206 + T-207
  T-209 (API endpoints)         ── after T-202 + T-205

Sprint 3 (Phase 3 — after Phase 2):
  T-210 (admin UI)              ── after T-209
  T-212 (tests)                 ── after all implementation merged
  T-213 (docs)                  ── after T-210
```

### Parallelisation Opportunities

| Parallel Group | Tasks | Notes |
|---|---|---|
| Day 1 start | T-201, T-205 | Completely independent files |
| After T-201 | T-203, T-204 | Both read same tables, no conflict |
| After T-201 + T-205 | T-202 | Needs both schema and SECRET_KEYS |
| After T-204 | T-207 | No schema dependency, only needs method signatures |
| After T-202 + T-205 | T-206, T-209 | Config and handler can be written in parallel |
| After T-206 | T-211 | 30-minute task, unblocks sample config |
| After T-203 + T-206 + T-207 | T-208 | Three-way fan-in |

### Resource Allocation

- T-206 (config merge): highest conceptual risk — the merge ordering and `job_db=None`
  on first startup are subtle. Assign the developer most familiar with `config.py` and
  have them read the PRP's "Known Gotchas" section before starting.
- T-207 (LogArchiver): the atomic write and group-then-delete ordering are critical
  correctness constraints. Write unit tests (T-212 REQ-11 through REQ-15) before the
  implementation to validate the atomic behaviour.
- T-210 (admin UI): check for `js-yaml` availability in `admin.html` before writing
  any YAML serialisation code; the PRP identifies this as an open question.
- T-212 (tests): can be written incrementally alongside implementation using a
  test-first approach for T-207 and T-206.

---

## Critical Path Analysis

### Tasks on Critical Path

```text
T-201 → T-202 → T-206 → T-208 → T-212 → T-213
T-205 → T-202
T-201 → T-203 → T-208
T-201 → T-204 → T-207 → T-208
T-202 → T-209 → T-210 → T-213
```

The longest path is: **T-201 + T-204 → T-207 → T-208 → T-212 → T-213**

### Potential Bottlenecks

1. **T-206 (Config merge)** — depends on both T-202 and T-205, and T-208 depends on
   it. The `load_config()` signature change propagating to `server.py` is a cross-file
   change; coordinate carefully with any ongoing work in `server.py`.
2. **T-208 (HeartbeatThread)** — three-way dependency on T-203, T-206, T-207. Schedule
   T-207 as an early task to minimise the wait; T-207 has only one dependency (T-204).
3. **T-207 (LogArchiver)** — the atomic write and DB-deletion-only-on-success invariant
   is the highest correctness risk in Phase 2. Write the unit tests first (T-212
   REQ-11–15) and use them to drive the implementation.
4. **T-212 (Tests)** — depends on all implementation tasks. Can be unblocked
   incrementally: write `TestConfigMerge` and `TestLogArchivalThread` as soon as T-206
   and T-207 are complete, before T-208 and T-209 land.

### Schedule Optimisation Suggestions

- Merge T-205 on day 1 (15-minute task); it unblocks T-202 immediately.
- Start T-207 as soon as T-204 merges — it has the fewest dependencies and is on the
  critical path to T-208.
- Write `TestLogArchivalThread` test stubs before implementing T-207 to drive the
  atomic write invariant via TDD.
- T-211 is a 30-minute task; slot it alongside T-206 to avoid a documentation tail
  at sprint end.
- T-213 (docs) is the only task that cannot start until T-210 (admin UI) is functionally
  complete — do not defer it or it will slip to a follow-up commit.
