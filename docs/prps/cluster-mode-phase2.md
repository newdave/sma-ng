# PRP: Cluster Mode — Phase 2 (Config Sync, Node Expiry, Log Archival)

## Discovery Summary

### Initial Task Analysis

Phase 1 of cluster mode is fully implemented: UUID node identity, `node_commands`
dispatch table, `drain`/`pause`/`resume` worker commands, `logs` table with
`PostgreSQLLogHandler`, and a Cluster tab in the admin UI. Phase 2 extends the
cluster management layer with three capabilities: a shared DB-resident base
config that nodes overlay with local overrides, TTL-based hard expiry of
long-offline nodes, and filesystem-based archival of aged cluster logs before
they are deleted.

### User Clarifications Received

- **Question**: Authority model when local config and DB config differ.
- **Answer**: B — local `sma-ng.yml` always wins; DB config is a cluster-wide
  default that nodes can override locally.
- **Impact**: Merge order is DB-base first, local-override second. `PathConfigManager`
  reads DB on startup (and reload) then overlays its own parsed config on top.

- **Question**: How does config get into the DB?
- **Answer**: A + B — admin UI form/editor for interactive editing; push-from-node
  API for bulk-loading the current node's local config.
- **Impact**: Two new endpoints (`GET /admin/config`, `POST /admin/config`,
  `POST /admin/nodes/<node_id>/push-config`); one new admin UI section.

- **Question**: Scope — what settings are centralised?
- **Answer**: A + B — both `autoProcess` media settings (video/audio/subtitle/etc.)
  AND daemon settings (`path_configs`, `scan_paths`, `path_rewrites`). Secrets
  (`api_key`, `db_url`, `username`, `password`) and `node_id` are excluded.
- **Impact**: The YAML blob stored in `cluster_config` is the full `sma-ng.yml`
  content minus the secret keys. Secrets are stripped before upload and ignored
  on download.

- **Question**: Log archive destination.
- **Answer**: B — local filesystem, gzipped JSONL, one file per node per day.
- **Impact**: No new dependencies. Archive directory is configurable
  (`log_archive_dir`). No UI needed for the archive itself (files are on disk).

- **Question**: Archival thresholds.
- **Answer**: B — two separate settings: `log_archive_after_days` (move from DB
  to filesystem) and `log_delete_after_days` (delete archive files from
  filesystem).
- **Impact**: Two new config keys. Archival step writes `.gz` files then deletes
  the DB rows. Filesystem cleanup step deletes old `.gz` files. Both run on
  every HeartbeatThread tick.

- **Question**: Which nodes are eligible for expiry?
- **Answer**: A — only `offline` nodes. Stale recovery (`online` → `offline`) is
  already handled by Phase 1; expiry is the second stage (`offline` → deleted).
- **Impact**: Expiry SQL: `DELETE FROM cluster_nodes WHERE status = 'offline'
  AND last_seen < NOW() - make_interval(days => %s)`. Orphaned `node_commands`
  rows are also cleaned up.

### Missing Requirements Identified

None — all ambiguities resolved by the clarification round above.

---

## Goal

Implement Phase 2 of sma-ng cluster mode:

1. `cluster_config` table storing a YAML blob; `PathConfigManager` merges it as
   the base config with local `sma-ng.yml` as the authoritative overlay.
2. Admin UI config editor and push-from-node API for loading config into the DB.
3. `node_expiry_days` TTL that hard-deletes long-offline nodes (and their
   orphaned `node_commands` rows) from the cluster registry.
4. Log archival: aged DB log rows are written to gzipped JSONL files on the
   local filesystem before being deleted; old archive files are pruned by a
   separate TTL.

---

## Why

- Operators running 5–10 nodes today must manually keep every node's config in
  sync via configuration management tools or manual file copies. A single DB
  base config eliminates that burden.
- Stale offline node rows accumulate indefinitely after crashes or node
  decommissions, cluttering the cluster tab. Automatic expiry keeps the
  registry clean.
- Phase 1 TTL cleanup destroys log history permanently. Archival to local
  filesystem lets operators retain months of history without unbounded DB
  growth.

---

## What

### User-Visible Behaviour

- The admin UI Cluster tab gains a **Config** section showing the current DB
  base config as a YAML editor. Operators can edit and save it, or click
  "Push from this node" to load the current node's local config into the DB
  (secrets stripped).
- On startup and config reload, each node fetches the DB base config, merges
  it with local `sma-ng.yml` (local wins), and uses the merged result.
- Offline nodes that have not sent a heartbeat for longer than `node_expiry_days`
  are automatically removed from the cluster registry on the next heartbeat tick.
  Running jobs on those nodes were already requeued by stale recovery.
- DB log rows older than `log_archive_after_days` are written to
  `<log_archive_dir>/<node_id>/<YYYY-MM-DD>.jsonl.gz` then deleted from the DB.
- Archive `.gz` files older than `log_delete_after_days` are deleted from the
  filesystem on each heartbeat tick.

### Success Criteria

- [ ] `cluster_config` table is created idempotently on daemon startup.
- [ ] `GET /admin/config` returns the current DB config blob (or `{}` if absent).
- [ ] `POST /admin/config` stores a YAML blob; secrets (`api_key`, `db_url`,
  `username`, `password`, `node_id`) are stripped before saving.
- [ ] `POST /admin/nodes/<node_id>/push-config` reads the target node's local
  `sma-ng.yml`, strips secrets, and upserts into `cluster_config`.
- [ ] On startup, `PathConfigManager` fetches and merges DB config; local
  values override DB values for every key.
- [ ] `node_expiry_days: 0` (default) disables expiry entirely. When set,
  offline nodes past the TTL are deleted along with their `node_commands` rows.
- [ ] `log_archive_after_days: 0` disables archival. When set, DB rows are
  written to `.jsonl.gz` then deleted.
- [ ] `log_delete_after_days: 0` disables filesystem pruning. When set, old
  `.gz` files are deleted.
- [ ] All new cluster paths are gated on `job_db.is_distributed`; SQLite
  single-node deployments are unaffected.
- [ ] All existing tests pass. New tests cover all new DB methods, config merge
  logic, expiry logic, and archival logic.

---

## All Needed Context

### Research Phase Summary

- **Codebase patterns found**: Every required extension point has a clear
  existing pattern. DB migrations use `CREATE TABLE IF NOT EXISTS` +
  `ALTER TABLE ADD COLUMN IF NOT EXISTS`. All DB methods use the `_conn()`
  context manager. Config parsing follows the `_parse_config_data()` /
  `_apply_config_data()` split. HeartbeatThread already calls cleanup helpers
  on each tick. The admin UI uses Alpine.js `adminPage()` with `authHeaders()`.
- **External research needed**: No. ruamel.yaml, psycopg2, gzip, and json are
  all already in the codebase or stdlib.
- **Knowledge gaps**: None blocking. All gotchas documented below.

### Documentation & References

```yaml
- file: resources/daemon/db.py
  why: >
    _init_db() migration pattern (lines 58–196). _conn() context manager
    (lines 41–52). cleanup_old_logs() delete pattern (lines 679–687).
    recover_stale_nodes() offline-transition pattern (lines 551–576).
    delete_offline_nodes() bulk-delete pattern (lines 789–797).
    All new DB methods (get_cluster_config, set_cluster_config,
    expire_offline_nodes, cleanup_orphaned_commands, archive_old_logs)
    must follow the exact _conn() pattern.

- file: resources/daemon/config.py
  why: >
    PathConfigManager._parse_config_data() (lines 286–343) — add new keys
    (node_expiry_days, log_archive_dir, log_archive_after_days,
    log_delete_after_days) following the int(config.get("key") or default)
    pattern. _apply_config_data() (lines 345–366) — add corresponding
    self._* assignments. load_config() (lines 234–256) — add DB config
    fetch + merge step after YAML parse. _write_node_id_to_yaml() (lines
    17–36) — atomic YAML write pattern to mirror for config editing.
    SECRET_KEYS constant listing api_key, db_url, username, password,
    node_id — strip these before DB upload.

- file: resources/daemon/threads.py
  why: >
    HeartbeatThread.__init__ signature (line 30). run() loop structure
    (lines 45–67) — Phase 2 appends node-expiry and log-archival calls
    before _stop_event.wait(). _execute_command() helper pattern (lines
    69–98) — new _archive_logs() and _expire_nodes() helpers follow the
    same self-contained-method style.

- file: resources/daemon/server.py
  why: >
    HeartbeatThread construction (lines 138–155) — new parameters must be
    added here sourced from path_config_manager. reload_config() (lines
    201–253) — HeartbeatThread is NOT restarted on reload; new HeartbeatThread
    parameters that can change at runtime must be read from path_config_manager
    on each tick rather than stored in __init__ args.

- file: resources/daemon/handler.py
  why: >
    _post_admin_node_action() (lines 531–591) — extend to handle
    "push-config" action (same path structure). _get_cluster_logs() (lines
    411–439) — pattern for auth-gated GET endpoint returning JSON.
    PUBLIC_ENDPOINTS list (line 36) — new config endpoints must NOT be added.
    _read_json_paths() (lines 108–124) — JSON body parsing pattern.

- file: resources/daemon/routes.py
  why: >
    _get_routes() dict (lines 55–71) — add GET /admin/config.
    _post_routes() dict (lines 82–99) — add POST /admin/config.
    _post_prefix_routes() (lines 102–106) — push-config action already
    handled by existing /admin/nodes/ prefix route.

- file: resources/admin.html
  why: >
    adminPage() Alpine.js component structure (lines 244–261). authHeaders()
    helper (lines 289–293). loadClusterLogs() fetch pattern (lines 345–361).
    Cluster tab structure to extend for Config section.

- file: resources/yamlconfig.py
  why: >
    load() function — returns {} on OSError, never raises. DB config fetch
    returning None must be treated identically (fall back to {}).

- file: setup/sma-ng.yml.sample
  why: >
    daemon: section (lines 437–475) — add node_expiry_days, log_archive_dir,
    log_archive_after_days, log_delete_after_days with comments. Mirror the
    node_id / log_ttl_days comment style added in Phase 1.

- file: tests/conftest.py
  why: >
    job_db fixture (lines 393–405) — PostgreSQL skip pattern. daemon_log
    fixture (lines 365–390) — log injection pattern.

- file: tests/test_cluster.py
  why: >
    All class-based test patterns. @pytest.mark.usefixtures("job_db") for
    DB-required classes. _unique_node() helper pattern. Mirror for new
    TestClusterConfigDB, TestNodeExpiryDB, TestLogArchivalThread classes.

- file: docs/prps/cluster-mode.md
  why: Phase 1 PRP — structural and gotcha reference.
```

### Current Codebase Tree (cluster-relevant)

```text
resources/daemon/
├── config.py          # PathConfigManager — add DB config merge here
├── constants.py       # resolve_node_id(), SECRET_KEYS (new)
├── db.py              # PostgreSQLJobDatabase — add cluster_config table + methods
├── db_log_handler.py  # PostgreSQLLogHandler (Phase 1, unchanged)
├── handler.py         # WebhookHandler — add config GET/POST endpoints
├── routes.py          # Route registration — add /admin/config routes
├── server.py          # DaemonServer — wire new HeartbeatThread params
└── threads.py         # HeartbeatThread — add expiry + archival calls

setup/
└── sma-ng.yml.sample  # Add 4 new daemon: keys

tests/
└── test_cluster.py    # Extend with new test classes
```

### Desired Codebase Tree (additions only)

```text
resources/daemon/
└── log_archiver.py    # NEW: LogArchiver helper class (archive + prune)

tests/
└── test_cluster.py    # EXTENDED: TestClusterConfigDB, TestNodeExpiryDB,
                       #           TestLogArchivalThread
```

### Known Gotchas

```python
# CRITICAL: HeartbeatThread is NOT restarted in reload_config(). Any
# parameters that need to respond to a hot reload must be read from
# path_config_manager on each tick, not stored as __init__ args.
# Safest approach: store a reference to path_config_manager and call
# path_config_manager.log_archive_after_days on each tick.

# CRITICAL: Secrets must be stripped before storing config in cluster_config.
# Strip at write time (both push-from-node and admin UI save).
# Define SECRET_KEYS = {"api_key", "db_url", "username", "password", "node_id"}
# in constants.py. Strip from the daemon: subsection of the YAML dict.

# CRITICAL: DB config merge must happen AFTER local YAML is parsed, not
# before. Order: (1) fetch DB blob, (2) parse DB blob via _parse_config_data(),
# (3) parse local YAML via _parse_config_data(), (4) merged = {**db_parsed,
# **local_parsed}. But step (4) is at the key level — if a local key is None
# or the YAML default, the DB value should win. See merge strategy below.

# CRITICAL: cluster_config table uses a single-row upsert. Use a CHECK
# constraint or upsert on a fixed id=1 to ensure only one row exists.
# Pattern: INSERT INTO cluster_config (id, config, updated_by) VALUES (1, %s, %s)
#           ON CONFLICT (id) DO UPDATE SET config = EXCLUDED.config, ...

# CRITICAL: log archival must write files atomically (.tmp then rename) to
# avoid corrupted .gz files if the process is killed mid-write. Follow the
# _write_node_id_to_yaml() atomic-write pattern.

# CRITICAL: archive_old_logs() must DELETE the DB rows for a given
# (node_id, date) window ONLY after the .gz file is confirmed written.
# If the write fails, skip deletion — better to keep rows than lose them.

# CRITICAL: _parse_config_data() is called with a dict (the daemon: section).
# When merging DB config, pass only the daemon: subsection of the DB blob
# through _parse_config_data(), not the entire YAML. The DB blob stores the
# full sma-ng.yml structure; extract data.get("daemon", {}) before parsing.

# CRITICAL: expire_offline_nodes() must delete node_commands for expired nodes
# BEFORE deleting from cluster_nodes to avoid FK-style orphan accumulation.
# No FK constraint exists, but cleanup_orphaned_commands() must be called first.

# GOTCHA: gzip + json in stdlib — no new dependencies needed:
#   import gzip, json
#   with gzip.open(path, "wt", encoding="utf-8") as f:
#       for record in records: f.write(json.dumps(record) + "\n")

# GOTCHA: Archive files group DB rows by (node_id, date). The SQL must
# extract the date portion from the timestamp:
#   SELECT ... FROM logs
#   WHERE timestamp < NOW() - make_interval(days => %s)
#   ORDER BY node_id, timestamp
# Then group in Python by (node_id, timestamp.date()) before writing.

# GOTCHA: yamlconfig.load() returns {} on file-not-found (never raises).
# get_cluster_config() returning None must be treated as {} before calling
# _parse_config_data() to avoid KeyError on .get() calls.

# GOTCHA: push-config action shares the /admin/nodes/<node_id>/<action>
# URL structure already handled by the _post_prefix_routes() prefix match.
# Only _post_admin_node_action() needs to be extended — no new route entry.
```

---

## Implementation Blueprint

### Data Models

```sql
-- New table: cluster_config (single-row, upsert on id=1)
CREATE TABLE IF NOT EXISTS cluster_config (
    id         INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    config     TEXT NOT NULL,           -- YAML blob (secrets stripped)
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT
);

-- No new index needed — single-row table, PK lookup only.
```

```python
# constants.py additions
SECRET_KEYS: frozenset[str] = frozenset({"api_key", "db_url", "username", "password", "node_id"})

# config.py additions to PathConfigManager.__init__ defaults
self._node_expiry_days: int = 0
self._log_archive_dir: str | None = None
self._log_archive_after_days: int = 0
self._log_delete_after_days: int = 0

# New properties
@property
def node_expiry_days(self) -> int: ...

@property
def log_archive_dir(self) -> str | None: ...

@property
def log_archive_after_days(self) -> int: ...

@property
def log_delete_after_days(self) -> int: ...
```

```python
# db.py new method signatures
def get_cluster_config(self) -> dict | None: ...
# Returns parsed YAML dict or None if no row exists.

def set_cluster_config(self, config_dict: dict, updated_by: str | None = None) -> None: ...
# Strips SECRET_KEYS from daemon: section before storing.
# Upserts the YAML-serialised dict into cluster_config.

def expire_offline_nodes(self, expiry_days: int) -> list[str]: ...
# Deletes offline nodes whose last_seen < NOW() - expiry_days.
# Cleans up node_commands for those nodes first.
# Returns list of deleted node_ids.

def cleanup_orphaned_commands(self, node_ids: list[str]) -> int: ...
# DELETE FROM node_commands WHERE node_id = ANY(%s)

def get_logs_for_archival(self, before_days: int) -> list[dict]: ...
# SELECT * FROM logs WHERE timestamp < NOW() - make_interval(days => %s)
# ORDER BY node_id, timestamp

def delete_logs_before(self, before_days: int) -> int: ...
# DELETE FROM logs WHERE timestamp < NOW() - make_interval(days => %s)
# Returns count deleted.
```

```python
# log_archiver.py — new file
class LogArchiver:
    def __init__(self, archive_dir: str, archive_after_days: int, delete_after_days: int, logger): ...

    def run(self, job_db) -> None:
        """Fetch aged DB rows, write .gz files, delete DB rows, prune old files."""

    def _write_archive(self, node_id: str, date: datetime.date, records: list[dict]) -> bool:
        """Write records to <archive_dir>/<node_id>/<YYYY-MM-DD>.jsonl.gz atomically.
        Returns True on success."""

    def _prune_old_files(self) -> int:
        """Delete .gz files older than delete_after_days. Returns count deleted."""
```

### List of Tasks

```yaml
Task 1 — cluster_config schema migration in db.py:
  MODIFY resources/daemon/db.py:
    - FIND: "_init_db" method, after the idx_logs_ts CREATE INDEX statement
    - ADD: CREATE TABLE IF NOT EXISTS cluster_config with single-row CHECK
      constraint (id = 1)
    - No index needed (single row, PK lookup only)

Task 2 — DB methods for cluster_config in db.py:
  MODIFY resources/daemon/db.py:
    - ADD get_cluster_config() -> dict | None:
        SELECT config FROM cluster_config WHERE id = 1
        Return yaml.safe_load(row["config"]) or None
    - ADD set_cluster_config(config_dict, updated_by=None):
        Strip SECRET_KEYS from config_dict["daemon"] if present
        yaml_str = yaml.safe_dump(config_dict) — use stdlib yaml (no comments needed)
        INSERT INTO cluster_config (id, config, updated_at, updated_by)
        VALUES (1, %s, NOW(), %s)
        ON CONFLICT (id) DO UPDATE SET config = EXCLUDED.config,
            updated_at = NOW(), updated_by = EXCLUDED.updated_by

Task 3 — DB methods for node expiry in db.py:
  MODIFY resources/daemon/db.py:
    - ADD expire_offline_nodes(expiry_days) -> list[str]:
        Find expired nodes: SELECT node_id FROM cluster_nodes
          WHERE status = 'offline'
          AND last_seen < NOW() - make_interval(days => %s)
        Call cleanup_orphaned_commands(expired_node_ids)
        DELETE FROM cluster_nodes WHERE node_id = ANY(%s)
        Return expired node_ids list
    - ADD cleanup_orphaned_commands(node_ids: list[str]) -> int:
        If not node_ids: return 0
        DELETE FROM node_commands WHERE node_id = ANY(%s)
        Return rowcount

Task 4 — DB methods for log archival in db.py:
  MODIFY resources/daemon/db.py:
    - ADD get_logs_for_archival(before_days) -> list[dict]:
        SELECT id, node_id, level, logger, message, timestamp
        FROM logs WHERE timestamp < NOW() - make_interval(days => %s)
        ORDER BY node_id, timestamp
    - ADD delete_logs_before(before_days) -> int:
        DELETE FROM logs WHERE timestamp < NOW() - make_interval(days => %s)
        Return rowcount

Task 5 — SECRET_KEYS constant in constants.py:
  MODIFY resources/daemon/constants.py:
    - ADD module-level SECRET_KEYS = frozenset({
        "api_key", "db_url", "username", "password", "node_id"
      })

Task 6 — Config merge in config.py:
  MODIFY resources/daemon/config.py:
    - ADD _strip_secrets(data: dict) -> dict private function:
        Copy data; strip SECRET_KEYS from data.get("daemon", {})
        Return sanitised copy
    - MODIFY _parse_config_data() to add new keys:
        "node_expiry_days": int(config.get("node_expiry_days") or 0)
        "log_archive_dir": config.get("log_archive_dir") or None
        "log_archive_after_days": int(config.get("log_archive_after_days") or 0)
        "log_delete_after_days": int(config.get("log_delete_after_days") or 0)
    - MODIFY _apply_config_data() to assign:
        self._node_expiry_days = parsed["node_expiry_days"]
        self._log_archive_dir = parsed["log_archive_dir"]
        self._log_archive_after_days = parsed["log_archive_after_days"]
        self._log_delete_after_days = parsed["log_delete_after_days"]
    - ADD properties: node_expiry_days, log_archive_dir,
        log_archive_after_days, log_delete_after_days
    - MODIFY load_config() — after local YAML parse, before _ensure_node_id():
        If job_db is available AND job_db.is_distributed:
          db_raw = job_db.get_cluster_config() or {}
          db_daemon = db_raw.get("daemon", {})
          db_parsed = self._parse_config_data(db_daemon)
          local_parsed = <current parsed dict>
          merged = {**db_parsed, **local_parsed}  # local wins
          self._apply_config_data(merged)
        NOTE: load_config() currently has no job_db reference. Add optional
        job_db parameter to load_config(config_file, job_db=None).
    - MODIFY all callers of load_config() in server.py reload_config() to
        pass job_db=self.job_db

Task 7 — LogArchiver helper class (new file):
  CREATE resources/daemon/log_archiver.py:
    - MIRROR pattern from: resources/daemon/db_log_handler.py (clean class)
    - class LogArchiver:
        __init__(self, archive_dir, archive_after_days, delete_after_days, logger)
        run(self, job_db) -> None  — main entry point; calls both sub-steps
        _archive_from_db(self, job_db) -> int  — fetch, group, write .gz, delete
        _write_archive(self, node_id, date, records) -> bool  — atomic .gz write
        _prune_old_files(self) -> int  — walk dir, delete expired .gz
    - Grouping: iterate get_logs_for_archival() result in Python, group by
      (node_id, timestamp.date()), write one .gz per group
    - Path: <archive_dir>/<node_id>/<YYYY-MM-DD>.jsonl.gz
    - Atomic write: write to <path>.tmp, then os.replace(tmp, path)
    - Delete DB rows ONLY after .gz confirmed written (no exception from _write_archive)
    - Prune: os.scandir(archive_dir) recursively; os.path.getmtime() vs cutoff

Task 8 — HeartbeatThread extensions in threads.py:
  MODIFY resources/daemon/threads.py:
    - MODIFY __init__ to read new settings from path_config_manager reference
      rather than discrete parameters (so hot-reload is picked up):
        Store self._path_config_manager = path_config_manager reference
        (path_config_manager is already available via self.server.path_config_manager)
    - MODIFY run() — after cleanup_old_logs() block, add:
        # Node expiry
        expiry_days = self.server.path_config_manager.node_expiry_days
        if self.job_db.is_distributed and expiry_days > 0:
            expired = self.job_db.expire_offline_nodes(expiry_days)
            for nid in expired:
                self.log.info("Expired offline node: %s" % nid)
        # Log archival
        archive_dir = self.server.path_config_manager.log_archive_dir
        archive_after = self.server.path_config_manager.log_archive_after_days
        delete_after = self.server.path_config_manager.log_delete_after_days
        if self.job_db.is_distributed and archive_dir and archive_after > 0:
            from resources.daemon.log_archiver import LogArchiver
            archiver = LogArchiver(archive_dir, archive_after, delete_after, self.log)
            archiver.run(self.job_db)

Task 9 — Admin API endpoints in handler.py:
  MODIFY resources/daemon/handler.py:
    - ADD _get_admin_config(path, query) method:
        If not job_db.is_distributed: return 503
        raw = self.server.job_db.get_cluster_config()
        Return JSON {"config": raw or {}}
    - ADD _post_admin_config(path, query) method:
        If not job_db.is_distributed: return 503
        Read Content-Length, json.loads body expecting {"config": {...}} or raw YAML str
        Strip secrets via _strip_secrets()
        Call job_db.set_cluster_config(config_dict, updated_by=actor)
        Return 200 {"status": "saved"}
    - EXTEND _post_admin_node_action() with "push-config" action:
        Read node's local sma-ng.yml via yamlconfig.load(path_config_manager._config_file)
        Strip secrets
        Call job_db.set_cluster_config(data, updated_by=actor)
        Return 200 {"status": "pushed"}

Task 10 — Route registration in routes.py:
  MODIFY resources/daemon/routes.py:
    - ADD to _get_routes() dict:
        "/admin/config": lambda handler, path, query: handler._get_admin_config(path, query)
    - ADD to _post_routes() dict:
        "/admin/config": lambda handler, path, query: handler._post_admin_config(path, query)
    - NOTE: push-config is already handled by existing
        ("/admin/nodes/", ...) prefix route — no new entry needed

Task 11 — Admin UI in admin.html:
  MODIFY resources/admin.html:
    - ADD to adminPage() Alpine.js data:
        clusterConfig: '',     // raw YAML string for editor
        configLoading: false,
        configSaving: false,
        configError: '',
        configSaved: false,
    - ADD loadClusterConfig() method:
        GET /admin/config with authHeaders()
        Convert JSON config object to YAML string for editor display
        Use js-yaml (already loaded or inline yaml stringify)
    - ADD saveClusterConfig() method:
        Parse editor text as YAML, POST to /admin/config as JSON
        Show success/error state
    - ADD pushNodeConfig(node) method:
        POST /admin/nodes/<node.node_id>/push-config with authHeaders()
    - ADD Config section to Cluster tab (after log viewer):
        YAML <textarea> bound to clusterConfig
        Save button → saveClusterConfig()
        "Push from this node" button → pushNodeConfig(current node)
        Error/success feedback

Task 12 — sma-ng.yml.sample update:
  MODIFY setup/sma-ng.yml.sample:
    - ADD to daemon: section (after log_ttl_days):
        # Days after which offline nodes are hard-deleted from the registry.
        # Set to 0 to disable automatic node expiry.
        node_expiry_days: 0
        # Directory for archived cluster log files (gzipped JSONL, one per node per day).
        # Set to null to disable log archival.
        log_archive_dir: null
        # Move cluster logs older than this many days from DB to log_archive_dir.
        # Set to 0 to disable archival (logs are deleted per log_ttl_days instead).
        log_archive_after_days: 0
        # Delete archived log files older than this many days from log_archive_dir.
        # Set to 0 to disable archive file pruning.
        log_delete_after_days: 0

Task 13 — Tests:
  EXTEND tests/test_cluster.py:
    - TestClusterConfigDB (@pytest.mark.usefixtures("job_db")):
        test_set_and_get_cluster_config_roundtrip
        test_secrets_are_stripped_before_storing
        test_get_cluster_config_returns_none_when_absent
        test_set_cluster_config_overwrites_existing_row
        test_get_cluster_config_returns_dict_not_string
    - TestNodeExpiryDB (@pytest.mark.usefixtures("job_db")):
        test_expire_offline_nodes_deletes_old_offline_rows
        test_expire_offline_nodes_skips_online_nodes
        test_expire_offline_nodes_cleans_up_node_commands
        test_expire_offline_nodes_returns_deleted_node_ids
        test_zero_expiry_days_is_noop (call with days=0, assert no deletion)
    - TestLogArchivalThread (unit, no DB needed):
        test_write_archive_creates_gz_file
        test_write_archive_is_atomic (check .tmp file is replaced)
        test_prune_old_files_deletes_expired_gz
        test_prune_old_files_keeps_recent_gz
        test_run_calls_archive_then_prune (mock job_db)
    - TestConfigMerge (unit, no DB needed):
        test_db_config_provides_base_values
        test_local_config_overrides_db_config
        test_strip_secrets_removes_api_key_and_db_url
        test_strip_secrets_preserves_non_secret_keys
        test_merge_handles_none_db_config_gracefully

Task 14 — Documentation:
  MODIFY docs/daemon.md:
    - ADD to "Cluster Mode" section:
        Sub-section: "Centralised Base Config"
          - How DB config is fetched on startup and merged
          - Local sma-ng.yml always wins
          - Admin UI config editor and push-from-node
          - Secrets that are never stored in DB
        Sub-section: "Node Expiry"
          - node_expiry_days setting and its interaction with stale recovery
        Sub-section: "Log Archival"
          - log_archive_after_days, log_delete_after_days, log_archive_dir
          - Archive file format and location
          - Interaction with log_ttl_days
```

### Per-Task Pseudocode

```python
# Task 6 — Config merge in load_config() (config.py)
# PATTERN: DB config is base; local YAML overrides (local wins per key)

def load_config(self, config_file, job_db=None):
    data = _yaml_load(config_file) or {}
    daemon_data = data.get(DAEMON_SECTION) or {}

    # Step 1: Parse local config
    local_parsed = self._parse_config_data(daemon_data)
    if not daemon_data.get("default_config"):
        local_parsed["default_config"] = config_file

    # Step 2: Fetch and parse DB base config (if distributed)
    merged = local_parsed
    if job_db is not None and getattr(job_db, "is_distributed", False):
        try:
            db_raw = job_db.get_cluster_config() or {}
            db_daemon = db_raw.get("daemon", {}) if db_raw else {}
            if db_daemon:
                db_parsed = self._parse_config_data(db_daemon)
                # Local wins: start with DB values, overlay local
                merged = {**db_parsed, **local_parsed}
        except Exception:
            self.log.warning("Failed to fetch cluster config from DB; using local only")

    self._apply_config_data(merged)
    self._ensure_node_id(config_file)
    return merged


# Task 7 — LogArchiver._archive_from_db (log_archiver.py)
# CRITICAL: Write .gz BEFORE deleting DB rows. Skip deletion on write failure.

def _archive_from_db(self, job_db) -> int:
    import gzip, json, os
    from collections import defaultdict

    records = job_db.get_logs_for_archival(self._archive_after_days)
    if not records:
        return 0

    # Group by (node_id, date)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        ts = r["timestamp"]
        date = ts.date() if hasattr(ts, "date") else ts
        groups[(r["node_id"], date)].append(r)

    all_written = True
    for (node_id, date), recs in groups.items():
        success = self._write_archive(node_id, date, recs)
        if not success:
            all_written = False
            self._log.warning("Log archive write failed for %s/%s" % (node_id, date))

    # Only delete rows that were successfully archived
    # SIMPLIFICATION: if any group failed, skip deletion entirely this tick
    if all_written:
        deleted = job_db.delete_logs_before(self._archive_after_days)
        return deleted
    return 0


def _write_archive(self, node_id: str, date, records: list[dict]) -> bool:
    import gzip, json, os
    node_dir = os.path.join(self._archive_dir, node_id)
    os.makedirs(node_dir, exist_ok=True)
    filename = "%s.jsonl.gz" % date.isoformat()
    final_path = os.path.join(node_dir, filename)
    tmp_path = final_path + ".tmp"
    try:
        with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
            for r in records:
                row = dict(r)
                ts = row.get("timestamp")
                if ts is not None and hasattr(ts, "isoformat"):
                    row["timestamp"] = ts.isoformat()
                f.write(json.dumps(row) + "\n")
        os.replace(tmp_path, final_path)
        return True
    except Exception as e:
        self._log.warning("Failed to write log archive %s: %s" % (final_path, e))
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False


# Task 3 — expire_offline_nodes (db.py)
# PATTERN: follows cleanup_old_jobs (lines 410-425), make_interval(days => %s)

def expire_offline_nodes(self, expiry_days: int) -> list[str]:
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
            cur.execute(
                "DELETE FROM cluster_nodes WHERE node_id = ANY(%s)",
                (expired,),
            )
    for nid in expired:
        self.log.info("Expired offline node: %s" % nid)
    return expired


# Task 2 — set_cluster_config (db.py)
# Strip secrets at write time, never at read time

def set_cluster_config(self, config_dict: dict, updated_by=None) -> None:
    import yaml as _yaml
    from resources.daemon.constants import SECRET_KEYS
    # Deep-copy to avoid mutating caller's dict
    import copy
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
```

### Integration Points

```yaml
DATABASE:
  migration: >
    cluster_config table added in _init_db() after idx_logs_ts CREATE INDEX.
    Single-row enforcement via CHECK (id = 1) on PRIMARY KEY column.
    No new indexes needed.
  existing_tables: >
    cluster_nodes — expire_offline_nodes() deletes rows with status='offline'
    and stale last_seen. No schema change needed.
    node_commands — cleanup_orphaned_commands() deletes by node_id list.
    logs — get_logs_for_archival() and delete_logs_before() added; existing
    cleanup_old_logs() remains for use when archival is disabled.

API/ROUTES:
  new_endpoints:
    - GET  /admin/config         → handler._get_admin_config()
    - POST /admin/config         → handler._post_admin_config()
    - POST /admin/nodes/<id>/push-config → _post_admin_node_action() extended
  add_to: resources/daemon/routes.py _get_routes() and _post_routes()
  auth: all three require authentication (not in PUBLIC_ENDPOINTS)

CONFIG:
  new_keys_in_sma-ng.yml:
    - daemon.node_expiry_days (int, default 0 = disabled)
    - daemon.log_archive_dir (str | null)
    - daemon.log_archive_after_days (int, default 0 = disabled)
    - daemon.log_delete_after_days (int, default 0 = disabled)
  parsed_in: resources/daemon/config.py _parse_config_data()
  applied_in: resources/daemon/config.py _apply_config_data()
  sample_update: setup/sma-ng.yml.sample daemon: section

WEB_UI:
  modify: resources/admin.html
  new_data_props: clusterConfig, configLoading, configSaving, configError, configSaved
  new_methods: loadClusterConfig(), saveClusterConfig(), pushNodeConfig(node)
  new_section: Config editor below log viewer in Cluster tab
```

---

## Validation Loop

### Level 1: Syntax & Style

```bash
source venv/bin/activate

# Lint all modified daemon files
ruff check resources/daemon/ resources/daemon/log_archiver.py --fix

# Type check
pyright resources/daemon/db.py resources/daemon/config.py \
        resources/daemon/threads.py resources/daemon/log_archiver.py

# Markdownlint for updated docs
markdownlint docs/daemon.md
```

### Level 2: Unit Tests

```bash
source venv/bin/activate

# Run without PostgreSQL (mock-based unit tests only)
python -m pytest tests/test_cluster.py -x -q -k "not DB"

# Run with PostgreSQL (full integration)
TEST_DB_URL=postgresql://user:pass@localhost/testdb \
    python -m pytest tests/test_cluster.py -x -q

# Full suite
python -m pytest tests/ -x -q
```

### Final Validation Checklist

- [ ] All 2303+ existing tests pass: `python -m pytest tests/ -x -q`
- [ ] New tests cover all three features: `python -m pytest tests/test_cluster.py -v`
- [ ] No linting errors: `ruff check resources/daemon/`
- [ ] No type errors: `pyright resources/daemon/`
- [ ] No markdownlint errors: `markdownlint docs/daemon.md`
- [ ] `GET /admin/config` returns `{}` on a fresh DB (no 500)
- [ ] `POST /admin/config` with a payload containing `api_key` does NOT persist the key
- [ ] `POST /admin/nodes/<id>/push-config` strips secrets from local config
- [ ] `node_expiry_days: 0` (default) — no nodes deleted on heartbeat tick
- [ ] `node_expiry_days: 1` — offline node with stale `last_seen` is removed on next tick
- [ ] Log archival disabled when `log_archive_dir` is null
- [ ] Archive `.gz` files written atomically (no corrupted files on kill)
- [ ] DB rows deleted only after `.gz` write confirmed
- [ ] Single-node SQLite daemon starts and processes jobs without touching any cluster path
- [ ] `docs/daemon.md` updated with all three new sub-sections

---

## Anti-Patterns to Avoid

- ❌ Do not store `api_key`, `db_url`, `username`, `password`, or `node_id` in
  `cluster_config` — strip at write time, never at read time
- ❌ Do not delete DB log rows before confirming the `.gz` file was written
  successfully
- ❌ Do not add config or expiry endpoints to `PUBLIC_ENDPOINTS` — they require auth
- ❌ Do not use `resources.yamlconfig.write()` for any new write-back — it strips
  YAML comments. Use ruamel.yaml round-trip mode for `sma-ng.yml` modifications,
  stdlib `yaml.safe_dump` for the DB config blob (comments not needed there)
- ❌ Do not restart `HeartbeatThread` in `reload_config()` — read new settings
  from `path_config_manager` on each tick instead
- ❌ Do not use `yaml.dump` (ruamel) for the DB blob — use stdlib `yaml.safe_dump`
  to keep the stored string clean and comment-free
- ❌ Do not add FK constraints to `cluster_config` or rely on cascade deletes —
  existing code has none, keep it consistent
- ❌ Do not allow archival to silently skip expired DB rows when the `.gz` write
  fails — log a warning and leave the DB rows intact for the next tick

---

## Task Breakdown Reference

See `docs/tasks/cluster-mode-phase2.md` (generated separately) for sprint-ready
task cards.

---

## Confidence Score: 8/10

High confidence for one-pass implementation. All extension points are identified
with exact file paths and line numbers. The config merge strategy is well-defined
(DB base, local overlay). The two uncertainties are:
1. Alpine.js YAML serialisation in the browser (js-yaml must be available or
   a simple JSON editor used as fallback — implementer should check if js-yaml
   is already loaded in `admin.html` before adding a dependency).
2. The `load_config(job_db=None)` signature change requires auditing all callers
   to ensure backward compatibility — in particular the initial `DaemonServer`
   construction path where `PathConfigManager` is created before `job_db` exists.
   The implementer should pass `job_db` only during `reload_config()`, not on
   first startup, and let the node bootstrap without DB config on first start.
