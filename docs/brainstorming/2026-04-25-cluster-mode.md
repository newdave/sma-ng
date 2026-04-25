# Feature Brainstorming Session: Cluster Mode — Multi-Node Management

**Date:** 2026-04-25
**Session Type:** Technical Design / Feature Planning

## 1. Context & Problem Statement

### Problem Description

Users running multiple sma-ng daemon instances (e.g. nodes with different GPU hardware) have no
coordinated management layer. Three concrete pain points were identified:

1. **Job duplication** — multiple nodes occasionally process the same job. Root cause identified as
   non-unique node identities causing PostgreSQL job-claim logic to break down.
2. **Log visibility** — per-node logs are not accessible from other nodes' web UIs, making
   multi-node monitoring difficult.
3. **Config drift** — each node maintains its own `sma-ng.yml`, making it easy for codec
   preferences, API keys, and metadata settings to diverge across the fleet.

### Target Users

- **Primary Users:** Power users and self-hosters running two or more sma-ng daemons (typically
  on different machines with different GPU capabilities).
- **Secondary Users:** Homelab operators wanting a single pane of glass for media conversion
  activity across their infrastructure.

### Success Criteria

- **Technical Metrics:**
  - Zero job duplication across nodes sharing a PostgreSQL instance.
  - Any node's web UI reflects global cluster state within one heartbeat interval.
  - Log entries queryable by node/level from any node's UI.
  - Config changes propagate to all nodes within one heartbeat interval.

### Constraints & Assumptions

- **Technical Constraints:**
  - Cluster features require PostgreSQL; SQLite single-node deployments must be unaffected
    (graceful degradation).
  - No inter-node HTTP — all coordination goes through the shared PostgreSQL database.
  - Node restart/shutdown commands must be safe to issue without authentication (DB access
    is the implicit authorization boundary).
- **Assumptions Made:**
  - All nodes in a cluster share one PostgreSQL instance.
  - Hardware-specific settings (hwaccel, FFmpeg path, GPU encoder) remain node-local overrides.
  - Log volume is manageable with a configurable TTL; full log archival is out of scope.

## 2. Brainstormed Ideas & Options

### Option A: Peer-to-Peer via PostgreSQL (Selected)

- **Description:** All nodes are equal peers. PostgreSQL is the sole coordination layer —
  no master node, no inter-node HTTP. Each node reads global state from the DB and the
  web UI renders that global state directly.
- **Key Features:**
  - Auto-generated UUID per node, persisted in `sma-ng.yml`
  - Node registry table updated on each heartbeat
  - Poll-based command channel via `node_commands` table
  - Aggregated log table with per-node TTL cleanup
  - Layered config: DB base → `sma-ng.yml` local overrides
- **Pros:**
  - No new network topology — works behind NAT, firewalls, VPNs
  - Single point of truth already used for job queue
  - Symmetric — any node can show full cluster state
  - Naturally resilient: no master failure scenario
- **Cons:**
  - PostgreSQL becomes a harder dependency for cluster features
  - Command latency bound to heartbeat interval (typically 30–60 s)
- **Effort Estimate:** L
- **Risk Level:** Medium
- **Dependencies:** PostgreSQL already configured; `HeartbeatThread` already exists

### Option B: Dedicated Control Plane Node

- **Description:** One node acts as a master, exposing an extended API. Other nodes register
  with the master and receive commands via HTTP.
- **Pros:**
  - Lower command latency (push vs. poll)
- **Cons:**
  - Single point of failure
  - Requires node addressing and discovery
  - Significant new complexity vs. Option A
- **Effort Estimate:** XL
- **Risk Level:** High

### Additional Ideas Considered

- **Command authentication:** Rejected for now — DB access is considered sufficient
  authorization boundary for this use case.
- **Log streaming (WebSocket):** Deferred to Phase 2; polling the DB on page load is
  sufficient for MVP.
- **Automatic node deregistration:** Nodes that miss N heartbeats could be marked `offline`
  automatically — good candidate for Phase 2.

## 3. Decision Outcome

### Chosen Approach

**Selected Solution:** Option A — Peer-to-Peer via PostgreSQL

### Rationale

- **Simplicity:** PostgreSQL is already the shared layer for job queuing; extending it for
  coordination avoids introducing new infrastructure.
- **Symmetry:** No master/follower topology means no single point of failure beyond the DB
  itself, which operators already treat as critical infrastructure.
- **User request alignment:** The user explicitly asked for every node's web UI to show a
  global view — this is naturally achieved when all state lives in the DB.

### Trade-offs Accepted

- **What We're Gaining:** Simple deployment, no node discovery, works across any network
  topology, single coordination layer.
- **What We're Sacrificing:** Command latency (heartbeat-bound), real-time log streaming.
- **Future Considerations:** WebSocket log tailing, sub-heartbeat command delivery if
  latency becomes a user complaint.

## 4. Implementation Plan

### Database Schema

Four new tables added to the PostgreSQL schema:

```sql
-- Node registry
CREATE TABLE nodes (
    node_id     TEXT PRIMARY KEY,
    hostname    TEXT NOT NULL,
    hwaccel     TEXT,
    status      TEXT NOT NULL DEFAULT 'active',  -- active, idle, draining, offline
    version     TEXT,
    last_heartbeat TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Control channel (poll-based)
CREATE TABLE node_commands (
    id          SERIAL PRIMARY KEY,
    node_id     TEXT NOT NULL REFERENCES nodes(node_id),
    command     TEXT NOT NULL,  -- drain, pause, resume, restart, shutdown
    issued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    status      TEXT NOT NULL DEFAULT 'pending'  -- pending, executing, done, failed
);

-- Centralized config (base + per-node overrides)
CREATE TABLE cluster_config (
    node_id     TEXT NOT NULL,  -- 'base' for the shared base row
    config_yaml TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (node_id)
);

-- Aggregated logs
CREATE TABLE logs (
    id          BIGSERIAL PRIMARY KEY,
    node_id     TEXT NOT NULL,
    level       TEXT NOT NULL,
    logger      TEXT,
    message     TEXT NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX logs_node_ts ON logs (node_id, timestamp DESC);
```

### Config Hierarchy

Effective config merge order (lowest → highest priority):

1. sma-ng built-in defaults
2. `cluster_config` base row (DB)
3. `sma-ng.yml` local file (always wins — acts as the node's local override layer)

Hardware-specific keys (`hwaccel`, `ffmpeg_dir`, GPU encoder settings) are expected to live
in `sma-ng.yml` and naturally override the DB base.

### MVP Scope (Phase 1)

**Core Features:**

- [ ] Auto-generate `node_id` UUID on first start; persist in `sma-ng.yml`
- [ ] `nodes` table: register on startup, update on each heartbeat tick
- [ ] `node_commands` table: heartbeat thread polls and executes `drain`, `pause`, `resume`,
  `restart`, `shutdown`
- [ ] `cluster_config` table: load base config from DB, merge with `sma-ng.yml` at startup
  and on heartbeat (detect `updated_at` change)
- [ ] `logs` table: route Python log handler output to DB with `node_id`; configurable TTL
  with periodic cleanup (run by each node, idempotent)
- [ ] Web UI — Cluster tab:
  - Node grid: hostname, hwaccel, status, last seen, version
  - Per-node action buttons: drain, pause, resume, restart, shutdown
  - Global log viewer: paginated, filterable by node and level

**Acceptance Criteria:**

- As an operator, I can see all active nodes and their status from any node's web UI.
- As an operator, I can issue a restart or shutdown to any node from the UI without SSH
  access to that node.
- No job is processed more than once across nodes sharing a PostgreSQL instance.
- Log entries from all nodes appear in the unified log viewer within one heartbeat interval.
- A node starting with no DB base config row continues to work using its `sma-ng.yml` alone.

**Definition of Done:**

- [ ] Feature implemented and tested (unit + integration)
- [ ] Code reviewed and merged
- [ ] `docs/daemon.md` updated with cluster mode section
- [ ] `setup/sma-ng.yml.sample` updated with `node_id` and log TTL fields
- [ ] Graceful degradation confirmed: SQLite single-node setups unaffected
- [ ] DB migration script included

### Future Enhancements (Phase 2+)

- **Automatic node expiry:** Mark nodes `offline` after N missed heartbeats; surface in UI.
- **Real-time log streaming:** WebSocket tail of the `logs` table.
- **Job reassignment UI:** Surface stuck/claimed jobs and allow manual reassignment to another
  node.
- **Per-node config editor:** UI to edit `cluster_config` override rows directly.
- **Log archival:** Export logs to S3/object storage before TTL deletion.

## 5. Action Items & Next Steps

### Immediate Actions

- [ ] **Generate PRP from this brainstorming document**
  - **Dependencies:** This document finalized
  - **Success Criteria:** PRP covers schema, config merge, heartbeat extensions, and UI changes

- [ ] **Audit existing `PostgreSQLJobDatabase` job-claiming logic**
  - **Dependencies:** None
  - **Success Criteria:** Confirm exactly where node identity is used in claim/lock queries;
    identify the duplication race condition

### Short-term Actions (Next Sprint)

- [ ] Implement DB schema migration (new tables)
- [ ] Implement node UUID generation and `sma-ng.yml` persistence
- [ ] Extend `HeartbeatThread` with registry update, command polling, config sync
- [ ] Implement DB log handler with TTL cleanup
- [ ] Implement Cluster tab in web UI

## 6. Risks & Dependencies

### Technical Risks

- **Risk:** Log table growth before TTL cleanup runs
  - **Impact:** Medium
  - **Probability:** Medium
  - **Mitigation:** Run cleanup on every node heartbeat (idempotent `DELETE WHERE timestamp <
    now() - ttl`); make TTL configurable in `sma-ng.yml`.

- **Risk:** Config merge edge cases (conflicting key types between DB YAML and local YAML)
  - **Impact:** Medium
  - **Probability:** Low
  - **Mitigation:** Deep-merge with `sma-ng.yml` always winning; log a warning when a local
    key overrides a DB key so operators are aware.

- **Risk:** Heartbeat thread blocking on DB commands during a slow shutdown
  - **Impact:** Low
  - **Probability:** Low
  - **Mitigation:** Execute commands in a thread; heartbeat tick remains non-blocking.

## 7. Resources & References

### Codebase References

- `resources/daemon/db.py` — `PostgreSQLJobDatabase`; job-claiming logic to audit
- `resources/daemon/threads.py` — `HeartbeatThread`; primary extension point
- `resources/daemon/handler.py` — Web UI route handlers; Cluster tab goes here
- `resources/daemon/config.py` — Config loading; extend for DB base config merge
- `setup/sma-ng.yml.sample` — Add `node_id`, `log_ttl_days` fields

## 8. Session Notes & Insights

### Key Insights Discovered

- The job duplication bug is almost certainly caused by non-unique node names in the
  existing PostgreSQL job-claim queries, not a fundamental architecture flaw. Enforcing
  UUID-based identity likely fixes it with minimal code change.
- `sma-ng.yml` acting as the local override layer (rather than a separate per-node DB row)
  is the cleanest approach — it preserves existing single-node behavior and requires no
  migration for existing users.
- `daemon.json` is deprecated; all new config fields go in `sma-ng.yml`.

### Questions Raised (For Future Investigation)

- What is the exact SQL in `PostgreSQLJobDatabase` that claims jobs, and does it use
  `SELECT FOR UPDATE SKIP LOCKED`? This determines whether the duplication fix is a
  one-liner or a schema change.
- Should log TTL be per-level (e.g. keep `ERROR` longer than `DEBUG`)? Deferred — flat TTL
  is sufficient for MVP.
- Should the `cluster_config` base row be editable from the web UI in Phase 1, or is
  direct DB manipulation acceptable for MVP?
