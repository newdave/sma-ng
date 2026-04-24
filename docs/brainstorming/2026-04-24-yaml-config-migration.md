# Feature Brainstorming Session: YAML Configuration Migration

**Date:** 2026-04-24
**Session Type:** Technical Design / Breaking Change Planning

## 1. Context & Problem Statement

### Problem Description

SMA-NG uses two separate configuration files in two different formats: `autoProcess.ini` (INI format
for conversion settings) and `daemon.json` (JSON for daemon runtime settings). INI's flat structure
with string-only values forces custom type coercion logic throughout the codebase (`SMAConfigParser`),
comma-separated list encoding, and pipe-separated directory lists. Users must maintain two files in
two formats. YAML is more legible, supports native types (lists, dicts, booleans, integers), and
allows the two files to be unified into one.

### Target Users

- **Primary Users:** Existing SMA-NG users who maintain `autoProcess.ini` and/or `daemon.json` and
  want a cleaner, more readable configuration format.
- **Secondary Users:** New users setting up SMA-NG for the first time who benefit from a single,
  self-documenting YAML file instead of two separate formats.

### Success Criteria

- **User Metrics:** Users can configure SMA-NG using a single YAML file; existing users can migrate
  without data loss; the migration is automatic on first startup.
- **Technical Metrics:** All existing INI settings are correctly round-tripped to YAML native types;
  daemon.json settings are available in the `Daemon:` YAML section; comments are preserved on
  round-trip edits.

### Constraints & Assumptions

- **Technical Constraints:** Must preserve backward compatibility with existing `daemon.json` files
  (fallback read); `SMA_CONFIG` / `SMA_DAEMON_*` environment variable overrides must still work.
- **Breaking Change:** Removing INI support is a major breaking change; a migration tool and
  auto-migration at startup are required to soften the impact.
- **Assumptions Made:** `ruamel.yaml` round-trip mode adequately preserves comments and key order;
  `configparser` `vars=os.environ` interpolation is replaceable with `os.path.expandvars()`.

## 2. Brainstormed Ideas & Options

### Option A: Full Replacement (YAML only)

- **Description:** Replace `autoProcess.ini` and `daemon.json` entirely with a single
  `autoProcess.yaml`. Provide auto-migration at startup and a standalone migration tool.
- **Key Features:**
  - Single flat YAML file — all INI sections plus `Daemon:` and `Profiles:` at the top level
  - Auto-migration: detects old-format files, converts, backs up originals as `.bak`
  - Named quality `Profiles:` replace separate `.rq.ini` / `.lq.ini` files
- **Pros:**
  - Eliminates dual-format maintenance burden across the codebase
  - Native types remove all custom type-coercion code (`SMAConfigParser`)
  - Single config file for all settings improves UX
- **Cons:**
  - Breaking change requiring migration path for all existing users
  - Loss of `.rq.ini` / `.lq.ini` separate-file pattern (replaced by `Profiles:`)
- **Effort Estimate:** XL
- **Risk Level:** Medium
- **Dependencies:** `ruamel.yaml` library; update to all downstream consumers

### Option B: Dual-Format Support (INI + YAML)

- **Description:** Support both INI and YAML simultaneously, letting users opt in to YAML at their
  own pace.
- **Pros:** No breaking change; gradual adoption
- **Cons:** Doubles config-parsing code paths permanently; never achieves simplification goal
- **Effort Estimate:** XL
- **Risk Level:** Low (deployment), High (long-term complexity)

### Option C: YAML for New Installs Only

- **Description:** Ship YAML samples and parser; existing installs continue on INI.
- **Pros:** Zero migration risk
- **Cons:** Two permanent code paths; new users get YAML, existing users stay on INI; tech debt grows
- **Effort Estimate:** L

### Additional Ideas Considered

- Consolidating `Daemon:` settings into YAML rather than a separate file was an obvious win and
  included in Option A.
- Named `Profiles:` emerged as a replacement for the separate `.rq.ini` / `.lq.ini` quality preset
  files, reducing file proliferation further.

## 3. Decision Outcome

### Chosen Approach

**Selected Solution:** Option A — Full replacement with auto-migration

### Rationale

- Eliminating `SMAConfigParser` and all comma/pipe-separated string encoding removes significant
  accidental complexity from the codebase.
- Unifying `autoProcess.ini` + `daemon.json` into one file reduces the configuration surface for
  users.
- Auto-migration with `.bak` preservation gives existing users a safe, automatic upgrade path with
  no data loss risk.
- `Profiles:` consolidation eliminates the `.rq.ini` / `.lq.ini` file pattern, making multi-quality
  setups fully self-contained in a single config.

### Trade-offs Accepted

- **What We're Gaining:** Native types, single config file, `Profiles:` support, elimination of
  `SMAConfigParser` complexity.
- **What We're Sacrificing:** Backward INI compatibility (mitigated by auto-migration + `.bak`);
  separate quality-preset files (replaced by inline `Profiles:`).
- **Future Considerations:** `daemon.json` fallback read can be removed in a future major release
  once adoption of YAML is confirmed.

## 4. Implementation Plan

### MVP Scope (Phase 1) — Completed

- [x] Add `ruamel.yaml>=0.18` dependency
- [x] Create `resources/yamlconfig.py` with `load`, `write`, `migrate_ini_to_yaml`, `cfg_get*` helpers
- [x] Remove `SMAConfigParser`; update `DEFAULTS` to native Python types
- [x] Update `ReadSettings.__init__` — YAML path resolution, auto-migration, `profile=` parameter
- [x] Rewrite `writeConfig` and `migrateFromOld` for plain dict
- [x] Update all `_read_*` methods to dict access
- [x] Add `_apply_profile()` static method
- [x] Create `setup/autoProcess.yaml.sample` and `setup/autoProcess.yaml.sample-lq`
- [x] Update `resources/daemon/config.py` — `Daemon:` section, `daemon.json` fallback, profile support
- [x] Update `resources/daemon/constants.py`
- [x] Wire `--profile` through `manual.py` → `worker.py` → `handler.py`
- [x] Update `update.py` — branch on YAML vs INI file presence
- [x] Create `yaml_merge.py` mirroring `ini_merge.py`
- [x] Update `scripts/ini_audit.py` — auto-detect format
- [x] Update `.mise/tasks/config/audit` and `config/roll`
- [x] Update `docker/docker-entrypoint.sh`
- [x] Update tests
- [x] Update docs, `daemon.json.sample`, `docker-compose.yml`

**Acceptance Criteria:**

- As a user, I can run SMA-NG with an existing `autoProcess.ini` and it is automatically converted
  to `autoProcess.yaml` with a log notice, and `autoProcess.ini.bak` is created.
- As a new user, I can copy `setup/autoProcess.yaml.sample` and configure all settings in one file.
- As a daemon operator, I can define `Daemon:` settings in `autoProcess.yaml` without a separate
  `daemon.json`.
- As a multi-quality user, I can define `Profiles: {rq: ..., lq: ...}` and reference them via
  `profile: rq` in `path_configs` without separate config files.

**Definition of Done:**

- [x] Feature implemented and tested
- [x] Documentation updated (docs/, wiki, inline daemon docs)
- [x] Sample config files created
- [x] Docker and mise tasks updated
- [ ] Code reviewed and merged (pending commit + PR)

### Future Enhancements (Phase 2+)

- Remove `daemon.json` fallback read entirely once adoption is confirmed (next major release).
- Remove `ini_merge.py` and INI audit tooling once no users remain on INI.
- Consider schema validation for `autoProcess.yaml` (e.g., with `jsonschema` or `pydantic`).

## 5. Action Items & Next Steps

### Immediate Actions

- [ ] **Commit all changes as logical commits per CLAUDE.md rules**
  - Dependencies: All implementation complete
  - Success Criteria: Each commit covers one logical change area; no mixed commits

- [ ] **Push and open PR for review**
  - Dependencies: Commits created
  - Success Criteria: CI passes; PR description covers migration path and breaking change

### Short-term Actions (Next Sprint)

- [ ] Announce migration guide to users alongside release notes
- [ ] Monitor for edge cases in `migrate_ini_to_yaml` (unusual INI values, custom sections)
- [ ] Consider adding a `mise run config/migrate` task as a user-facing migration command

## 6. Risks & Dependencies

### Technical Risks

- **Risk:** `ruamel.yaml` round-trip mode loses comments or reorders keys on config edits
  - **Impact:** Medium
  - **Probability:** Low
  - **Mitigation:** Use `typ="rt"` consistently; covered by round-trip tests

- **Risk:** `migrate_ini_to_yaml` misclassifies a value's type (e.g., string that looks like a list)
  - **Impact:** Medium
  - **Probability:** Low
  - **Mitigation:** Type driven by `DEFAULTS` dict, not value content; unknown sections copied as strings

- **Risk:** `daemon.json` users (especially Docker deployments) don't notice deprecation
  - **Impact:** Low — fallback read still works
  - **Probability:** Medium
  - **Mitigation:** Warning logged on every startup when `daemon.json` fallback is used

- **Risk:** `SMA_DAEMON_*` env var overrides applied after `PathConfigManager` may not cover all
  new `Daemon:` section keys
  - **Impact:** Medium
  - **Probability:** Low
  - **Mitigation:** Verify env var override logic in `server.py` covers all `Daemon:` fields

## 7. Resources & References

### Codebase References

- `resources/yamlconfig.py` — YAML load/write/migrate helpers
- `resources/readsettings.py` — `ReadSettings` class, `DEFAULTS`, `_read_*` methods
- `resources/daemon/config.py` — `PathConfigManager`, `Daemon:` section parsing
- `yaml_merge.py` — YAML config maintenance tooling (mirrors `ini_merge.py`)
- `setup/autoProcess.yaml.sample` — canonical YAML config reference
- `docs/configuration.md` — full config key reference
- `docs/daemon.md` — daemon config, `Profiles:`, migration from `daemon.json`

### Technical Documentation

- `ruamel.yaml` round-trip docs — comment and key-order preservation
- `/tmp/blueprint.txt` — detailed 18-task implementation blueprint
- `/tmp/todo.txt` — task completion tracking

## 8. Session Notes & Insights

### Key Insights Discovered

- The `Profiles:` concept (replacing `.rq.ini` / `.lq.ini`) emerged naturally once we committed to
  a single YAML file — it's strictly better than the old pattern because profiles are co-located
  with the base config and referenced by name rather than path.
- Auto-migration with `.bak` preservation was the right call: it makes the breaking change
  transparent and reversible without requiring users to take any manual action.
- The flat YAML structure (section names at the top level, matching INI) minimizes the cognitive
  distance for existing users reading their migrated config.

### Questions Raised (For Future Investigation)

- Should `yaml_merge.py` eventually replace `ini_merge.py` entirely, or remain a parallel tool?
- Is there value in a `mise run config/migrate` task for users who want to trigger migration manually
  before upgrading?
- Could `Profiles:` support inheritance (e.g., `rq` extends `base-hdr`) in a future iteration?
