# Task Breakdown: sma-ng.yml Config Restructure

**Source PRP**: [docs/prps/config-restructure.md](../prps/config-restructure.md)
**Brainstorming**: [docs/brainstorming/2026-04-26-config-restructure.md](../brainstorming/2026-04-26-config-restructure.md)
**Owner**: @dhill (single-maintainer)
**Date created**: 2026-04-26

## Summary

Hard-cutover restructure of `config/sma-ng.yml` into four semantic top-level
buckets (`daemon` / `base` / `profiles` / `services`), backed by a pydantic v2
schema, with full removal of `autoProcess.ini` parsing and CI-enforced sample
generation. The PRP enumerates 28 ordered tasks; this document groups them
into six work packages, defines acceptance criteria in Given-When-Then form,
and surfaces the critical path.

## Complexity Assessment

- **Overall complexity**: L (large, ~3-5 working days for single maintainer)
- **Risk level**: Medium — touches every config consumer, but precedent
  patterns (longest-prefix routing, shallow profile merge, secrets redaction,
  ruamel round-trip) all already exist in the codebase
- **Integration points**: `manual.py`, `daemon.py`, `resources/daemon/*`,
  `resources/mediaprocessor.py`, `autoprocess/plex.py`, `scripts/plexmatch.py`,
  CI workflow, GitHub wiki
- **Breaking change**: Yes (operator-facing). Requires `BREAKING CHANGE:`
  trailer in the loader-rewrite commit so release-please picks it up.

## Resolved Decisions

All open decisions from the PRP and brainstorm have been settled:

1. **`migrateFromOld` YAML-applicable migrations** (Task 3) — **RIP**.
   No warn-and-rename cycle. Document in release notes that any user with
   `sort-streams`, `prefer-more-channels`, `default-more-channels`,
   `final-sort`, `copy-original-before`, `move-after`, or top-level `gpu`
   keys in YAML must edit them by hand to the new shape. `migrateFromOld`
   (readsettings.py:1037-1112) is deleted entirely.
2. **Per-path `default_args`** (Task 5) — **DROP**. Only
   `daemon.default_args` (global) survives. The current
   `path_configs[].default_args` per-path override is removed; routing
   rules carry only `match`, `profile`, `services`. Document in release
   notes.
3. **`.ini` detection** (Task 2) — **EXTENSION ONLY**.
   `path.lower().endswith(".ini")` is sufficient; no first-line sniff.
4. **Service `default: true` flag** — out of scope (Phase 2).

---

## Work Package Overview

| WP   | Name                          | Tasks            | Size  | Hours    | Risk   | Critical path | Depends on |
| ---- | ----------------------------- | ---------------- | ----- | -------- | ------ | ------------- | ---------- |
| WP-1 | Schema foundation             | 1, 10            | M     | 6-10 h   | Low    | Yes           | -          |
| WP-2 | Loader rewrite                | 2, 3, 4          | L     | 10-16 h  | Medium | Yes           | WP-1       |
| WP-3 | Sample generator + CI         | 11, 12, 13, 22   | S     | 4-6 h    | Low    | No (parallel) | WP-1       |
| WP-4 | Consumer migration            | 5, 6, 7, 8, 9    | L     | 8-14 h   | Medium | Yes           | WP-2       |
| WP-5 | Tests                         | 14-20            | M     | 8-12 h   | Low    | Partial       | WP-2, WP-4 |
| WP-6 | Documentation, validation, cutover | 21, 23-28   | M     | 6-10 h   | Low    | Tail          | WP-3, WP-5 |

T-shirt sizing convention: XS = <2h, S = 2-6h, M = 6-12h, L = 12-20h,
XL = 20h+.

Total estimate: **42-68 hours** of focused work, plus rebase/review overhead.

---

## WP-1 — Schema Foundation

**Objective**: Land the pydantic v2 schema and the runtime dependency. No
behaviour change yet; schema imports cleanly and round-trips a hand-built
sample dict.

**Deliverables**:

- `resources/config_schema.py` with full model tree
- `pydantic>=2,<3` in both `setup/requirements.txt` and `pyproject.toml`

**Milestones**:

- `python -c "from resources.config_schema import SmaConfig; SmaConfig()"` works
- `mise run dev:lint` passes on the new file
- Defaults in the schema match `readsettings.DEFAULTS` byte-for-byte where
  user-facing

**Risk**: Low — purely additive. Wrong defaults would be regressions, so
the cross-check vs `DEFAULTS` is the main quality gate.

**Critical path**: Yes — every other WP depends on the schema existing.

### Task 1 — CREATE `resources/config_schema.py`

- **Priority**: Critical
- **Dependencies**: Task 10 (pydantic install must land in same commit or
  prior)
- **Files**: `resources/config_schema.py` (new)

**Acceptance Criteria (Given-When-Then)**:

```gherkin
Scenario: Schema round-trips defaults
  Given the new module resources/config_schema.py
  When I instantiate SmaConfig() with no arguments
  Then every field has a default that matches resources/readsettings.py
       DEFAULTS (lines 24-296) for the corresponding key
  And no ValidationError is raised

Scenario: Unknown keys are accepted but tracked
  Given a SmaConfig model with extra="allow"
  When I pass {"base": {"converter": {"notakey": 1}}}
  Then validation succeeds
  And model.base.converter.__pydantic_extra__ contains "notakey"

Scenario: Routing references are validated
  Given a config with daemon.routing[0].services = ["sonarr.kids"]
  And services.sonarr does not contain "kids"
  When I call SmaConfig.model_validate(data)
  Then a ValidationError is raised
  And the error message includes "daemon.routing[0].services" and "kids"

Scenario: Routing profile reference validated
  Given daemon.routing[0].profile = "rq"
  And profiles does not contain "rq"
  When validating
  Then ValidationError mentions the unknown profile name

Scenario: Service ref shape validated
  Given daemon.routing[0].services = ["sonarr"]  # missing instance
  When validating
  Then ValidationError says "must be of form '<type>.<instance>'"
```

**Checklist**:

- [ ] Every key in `DEFAULTS` (24-296) present with identical default value
- [ ] `model_config = ConfigDict(extra="allow")` on the `_Base` class
- [ ] `@model_validator(mode="after")` cross-references routing → services
      and routing → profiles
- [ ] Downloader configs (SAB/deluge/qBittorrent/uTorrent) **not** in schema
- [ ] `PathRewrite.from_` uses `Field(alias="from")`
- [ ] Pyright sees no errors on the new file

---

### Task 10 — Install pydantic

- **Priority**: Critical
- **Dependencies**: None
- **Files**: `setup/requirements.txt`, `pyproject.toml`

**Acceptance Criteria**:

```gherkin
Scenario: Dependency resolves
  Given a fresh venv
  When I run `pip install -r setup/requirements.txt`
  Then pydantic>=2,<3 is installed
  And `python -c "import pydantic; assert pydantic.VERSION.startswith('2.')"` passes

Scenario: pyproject parity
  Given pyproject.toml [project.dependencies]
  When I diff against setup/requirements.txt
  Then both pin the same major/minor floor for pydantic
```

**Checklist**:

- [ ] `pydantic>=2,<3` in `setup/requirements.txt`
- [ ] Same constraint in `pyproject.toml` `[project.dependencies]`
- [ ] CI install step passes (verified in WP-6 final validation)

---

## WP-2 — Loader Rewrite

**Objective**: Replace the INI-aware, flat-key `ReadSettings` loader with a
schema-validated, four-bucket loader. Preserve every public API surface
listed in PRP research §1.1. Delete INI migration code.

**Deliverables**:

- `resources/config_loader.py` — `ConfigLoader` class
- Rewritten `resources/readsettings.py` — thin adapter
- `resources/yamlconfig.py` — INI migrator removed

**Milestones**:

- A hand-written four-bucket YAML loads through `ReadSettings` without error
- Old-shape YAML produces the documented `Old flat-shape` error
- `.ini` paths via `SMA_CONFIG` produce the documented error

**Risk**: Medium — every test that uses `tmp_ini` will break until WP-5;
that's expected. Run `pytest tests/test_config_schema.py` only at this
stage (created in WP-5; build it first if needed).

**Critical path**: Yes.

### Task 2 — CREATE `resources/config_loader.py`

- **Priority**: Critical
- **Dependencies**: Task 1
- **Files**: `resources/config_loader.py` (new)

**Acceptance Criteria**:

```gherkin
Scenario: Load valid four-bucket YAML
  Given a YAML with top-level keys daemon/base/profiles/services
  When ConfigLoader().load(path) runs
  Then it returns a SmaConfig instance
  And no error is logged

Scenario: Reject .ini path
  Given path = "/tmp/foo.ini"
  When ConfigLoader().load(path) runs
  Then ConfigError is raised with message containing
       "autoProcess.ini is no longer supported"

Scenario: Reject old flat shape
  Given a YAML with top-level converter: but no base:
  When loading
  Then ConfigError is raised mentioning "Old flat-shape config detected"
       and pointing at docs/configuration.md

Scenario: Warn on unknown keys
  Given a YAML with base.converter.notakey: 1
  When loading
  Then SmaConfig is returned
  And a single WARNING log line "Unknown config key: base.converter.notakey"
       is emitted

Scenario: Longest-prefix routing wins
  Given routing rules with match "/media/tv/" and "/media/tv/kids/"
  When resolve_routing(cfg, "/media/tv/kids/foo.mkv") runs
  Then the resolution.profile and resolution.services come from the
       "/media/tv/kids/" rule

Scenario: No-match fallback to bare base
  Given routing rules that don't match the input file
  When resolve_routing runs
  Then resolution.profile is None
  And resolution.services is []
  And resolution.base_config is cfg.base unmodified

Scenario: Empty services list means no notify
  Given a routing rule whose services: is omitted
  When resolve_routing matches that rule
  Then resolution.services == []

Scenario: Profile shallow-merge preserved
  Given base.video = {"codec": "h264", "bitrate": 5000}
  And profiles.rq.video = {"codec": "hevc"}
  When apply_profile(cfg, "rq") runs
  Then result.video.codec == "hevc"
  And result.video.bitrate == 5000
```

**Checklist**:

- [ ] Public API: `load()`, `apply_profile()`, `resolve_routing()`,
      `RoutingResolution` dataclass/NamedTuple
- [ ] Longest-prefix sort mirrors `resources/daemon/config.py:373`
- [ ] Shallow-merge mirrors `resources/readsettings.py:497-504` (NOT deep)
- [ ] Catches `pydantic.ValidationError`, re-raises as `ConfigError`
- [ ] Recursive `_warn_unknown_keys` walks nested models and dict-of-models
      (services maps)
- [ ] Uses `ruamel.yaml` (via existing `yamlconfig.load`); does **not**
      import `yaml`/PyYAML

---

### Task 3 — REWRITE `resources/readsettings.py`

- **Priority**: Critical
- **Dependencies**: Task 2
- **Files**: `resources/readsettings.py`

**Acceptance Criteria**:

```gherkin
Scenario: Public API preserved
  Given any existing call site of ReadSettings
  When code passes ReadSettings(configFile=..., logger=..., profile=...)
  Then the constructor signature is unchanged
  And every attribute listed in PRP research §1.1 still exists on the
      instance after construction

Scenario: Multi-instance Sonarr exposed
  Given services.sonarr.main and services.sonarr.kids in YAML
  When ReadSettings loads
  Then settings.sonarr_instances is a dict (or list) keyed by instance name
  And manual.py:349-356 iteration still works

Scenario: HWACCEL tables retained
  Given the new readsettings.py
  When inspected
  Then HWACCEL_PROFILES, CODEC_ALIASES, HWACCEL_CODEC_MAP constants
       are still present (lines 405-445 equivalent)

Scenario: migrateFromOld removed
  Given the rewritten file
  When grepped for "migrateFromOld" or "migrate_ini_to_yaml"
  Then no occurrences remain

Scenario: _validate_binaries preserved
  Given existing tests that patch ReadSettings._validate_binaries
  When tests run after rewrite
  Then patching still works (method exists, same signature)
```

**Checklist**:

- [ ] DEFAULTS dict deleted (replaced by schema defaults)
- [ ] `_read_*` methods deleted
- [ ] `_apply_profile` deleted (semantic moved to ConfigLoader)
- [ ] `_read_sonarr_radarr` name-prefix scanning (909-943) deleted
- [ ] INI auto-migration code path (350-362) deleted
- [ ] **Open decision 1 resolved**: `migrateFromOld` YAML key-renames —
      either deleted entirely or kept as one-cycle warn (decide in commit
      message; default = delete)
- [ ] Commit body includes `BREAKING CHANGE:` trailer

---

### Task 4 — DELETE `migrate_ini_to_yaml` from `resources/yamlconfig.py`

- **Priority**: High
- **Dependencies**: Task 3 (so callers are gone first)
- **Files**: `resources/yamlconfig.py`

**Acceptance Criteria**:

```gherkin
Scenario: Function gone, loader intact
  Given resources/yamlconfig.py
  When inspected
  Then migrate_ini_to_yaml (lines 26-78) is deleted
  And yamlconfig.load remains and is unchanged

Scenario: No stale imports
  Given the repository
  When grepped for "migrate_ini_to_yaml"
  Then zero matches
```

**Checklist**:

- [ ] Function removed
- [ ] `yamlconfig.load` untouched
- [ ] Imports of `migrate_ini_to_yaml` removed from all sites

---

## WP-3 — Sample Generator + CI

**Objective**: Schema-driven sample generation with a CI guard. Can run in
parallel with WP-2 once Task 1 is in.

**Deliverables**:

- `scripts/generate_sma_ng_sample.py`
- `.mise/tasks/config/generate-sample`
- Regenerated `setup/sma-ng.yml.sample`
- `config-sample-consistency` CI job

**Milestones**:

- `mise run config:generate-sample` writes the sample file
- `mise run config:generate-sample --check` exits non-zero on drift
- CI red-light demo: hand-edit the sample, push, CI fails

**Risk**: Low — output is bytes-comparable; ruamel.yaml round-trip is
already used elsewhere.

**Critical path**: Parallel — does not block consumer migration but must
land before WP-6 cutover.

### Task 11 — CREATE `scripts/generate_sma_ng_sample.py`

- **Priority**: High
- **Dependencies**: Task 1
- **Files**: `scripts/generate_sma_ng_sample.py` (new)

**Acceptance Criteria**:

```gherkin
Scenario: Default run writes sample
  Given a clean working tree
  When `python scripts/generate_sma_ng_sample.py` runs
  Then setup/sma-ng.yml.sample is rewritten
  And the file parses back through SmaConfig.model_validate without error

Scenario: --check detects drift
  Given a hand-edited setup/sma-ng.yml.sample
  When `python scripts/generate_sma_ng_sample.py --check` runs
  Then exit code is non-zero
  And a unified diff is printed to stdout

Scenario: --check on clean tree
  Given the committed sample matches generator output
  When --check runs
  Then exit code is 0 and no diff is printed
```

**Checklist**:

- [ ] Uses `ruamel.yaml` `YAML(typ="rt")` with `indent(mapping=2,
      sequence=4, offset=2)`
- [ ] Includes illustrative entries for `profiles.rq`,
      `services.sonarr.{main,kids}`, `daemon.routing` rules
- [ ] Section comment headers preserved (operator-facing readability)
- [ ] Does **not** import PyYAML

---

### Task 12 — CREATE `.mise/tasks/config/generate-sample`

- **Priority**: High
- **Dependencies**: Task 11
- **Files**: `.mise/tasks/config/generate-sample` (new, executable)

**Acceptance Criteria**:

```gherkin
Scenario: Mise wrapper works
  Given the new task script with #MISE description header
  When `mise run config:generate-sample` runs
  Then it invokes scripts/generate_sma_ng_sample.py with passed args

Scenario: ShellCheck clean
  Given the script
  When `shellcheck` runs
  Then no warnings or errors
```

**Checklist**:

- [ ] `#MISE description="Regenerate setup/sma-ng.yml.sample from pydantic
      schema"` header
- [ ] Executable bit set
- [ ] No inline Python (CLAUDE.md shell rule)
- [ ] Wiki `Mise-Tasks.md` updated to document the task

---

### Task 13 — REGENERATE `setup/sma-ng.yml.sample`

- **Priority**: High
- **Dependencies**: Tasks 11, 12
- **Files**: `setup/sma-ng.yml.sample`

**Acceptance Criteria**:

```gherkin
Scenario: Sample reflects four-bucket layout
  Given the regenerated sample
  When read top-down
  Then top-level keys are exactly: daemon, base, profiles, services
  And no downloader configs (SAB/deluge/qbittorrent/utorrent) appear

Scenario: Sample is reload-safe
  Given setup/sma-ng.yml.sample
  When ConfigLoader().load(path) runs against it
  Then a SmaConfig is returned with no warnings
```

---

### Task 22 — UPDATE `.github/workflows/ci.yml` — sample consistency job

- **Priority**: High
- **Dependencies**: Task 11
- **Files**: `.github/workflows/ci.yml`

**Acceptance Criteria**:

```gherkin
Scenario: CI catches drift
  Given a PR that hand-edits setup/sma-ng.yml.sample
  When CI runs the config-sample-consistency job
  Then the job fails with the diff printed in logs

Scenario: Clean PR passes
  Given a PR that regenerates the sample correctly
  When CI runs
  Then the config-sample-consistency job is green
```

**Checklist**:

- [ ] New job name: `config-sample-consistency`
- [ ] Step runs `mise run config:generate-sample --check`
- [ ] Job runs in parallel with existing test jobs (no needs:)
- [ ] Pip cache reused if existing CI uses one

---

## WP-4 — Consumer Migration

**Objective**: Update every Python module that touches the loader output to
use the new shape and routing engine. Surface secrets correctly through
admin endpoint redaction.

**Deliverables**:

- `resources/daemon/config.py` updated (PathConfigManager replaced by
  routing wrapper)
- `resources/daemon/constants.py` SECRET_KEYS extended
- `resources/daemon/handler.py` webhook flow updated
- `manual.py` lines 349-356 rewired through `resolve_routing`
- Downloader configs removed from `ReadSettings`

**Milestones**:

- Daemon starts and accepts a webhook against a four-bucket config
- `manual.py --profile rq -i file.mkv` overrides routing
- `/configs` admin endpoint redacts service-instance secrets

**Risk**: Medium — handler webhook flow has many call sites
(830-836, 857-861, 879-880, 902-903) and is the daemon's hot path.

**Critical path**: Yes.

### Task 5 — UPDATE `resources/daemon/config.py`

- **Priority**: Critical
- **Dependencies**: Task 2
- **Files**: `resources/daemon/config.py`

**Acceptance Criteria**:

```gherkin
Scenario: PathConfigManager uses ConfigLoader
  Given the new config.py
  When loading a four-bucket YAML
  Then daemon options come from cfg.daemon (not _parse_config_data)

Scenario: get_recycle_bin INI fallback gone
  Given resources/daemon/config.py
  When grepped for INI references in get_recycle_bin
  Then none remain
  And recycle_bin is sourced from base.converter.recycle_bin only

Scenario: Routing wrapper surfaces routing info
  Given a configured routing rule for "/media/tv/kids/**"
  When get_config_for_path("/media/tv/kids/foo.mkv") runs
  Then it returns the matching routing entry
  And get_profile_for_path returns the rule's profile

Scenario: path_rewrites preserved
  Given the new config.py
  When rewrite_path is called with an existing rewrite rule
  Then behaviour matches the pre-restructure implementation

Scenario: Service secrets redacted
  Given a config with services.sonarr.kids.apikey = "secret123"
  When the /configs admin endpoint serialises via _strip_secrets
  Then "secret123" does not appear in the response
```

**Checklist**:

- [ ] `_parse_config_data` (336-397) replaced
- [ ] `get_recycle_bin` INI fallback (485-499) deleted
- [ ] `_strip_secrets` (40-47) walks `data["services"]`
- [ ] **Open decision 2 resolved**: per-routing `default_args` migrated or
      dropped (default: drop, keep `daemon.default_args` global only)
- [ ] `rewrite_path` (468-475) preserved as-is

---

### Task 6 — UPDATE `resources/daemon/constants.py` — SECRET_KEYS

- **Priority**: High
- **Dependencies**: Task 5 (touches the same redaction path)
- **Files**: `resources/daemon/constants.py`

**Acceptance Criteria**:

```gherkin
Scenario: Per-instance secrets registered
  Given the new constants.py
  When inspected
  Then SECRET_KEYS (or a sibling SERVICE_SECRET_FIELDS) lists at minimum
       apikey, token, password
  And _strip_secrets uses these to redact services.<type>.<instance> fields

Scenario: Top-level daemon secrets still redacted
  Given the new constants.py
  When _strip_secrets runs on a config with daemon.api_key
  Then daemon.api_key is removed from the output
```

---

### Task 7 — UPDATE `resources/daemon/handler.py` — webhook + admin

- **Priority**: Critical
- **Dependencies**: Tasks 5, 6
- **Files**: `resources/daemon/handler.py`

**Acceptance Criteria**:

```gherkin
Scenario: Webhook submission resolves routing
  Given an inbound webhook with file path "/media/tv/kids/show.s01e01.mkv"
  When the webhook handler enqueues the job
  Then the resolved profile and services come from
       ConfigLoader.resolve_routing
  And lines previously at 830-836, 857-861, 879-880, 902-903 use the
      new API

Scenario: /configs surfaces routing
  Given a configured daemon.routing list
  When GET /configs runs (with auth)
  Then the response includes the routing rules
  And service instance secrets are redacted
```

---

### Task 8 — UPDATE `manual.py`

- **Priority**: High
- **Dependencies**: Task 2
- **Files**: `manual.py`

**Acceptance Criteria**:

```gherkin
Scenario: Routing-driven service notification
  Given services.sonarr.kids configured with path "/media/tv/kids/**"
  When `python manual.py -i /media/tv/kids/foo.mkv -oo` runs
  Then the resolved service list includes ("sonarr", "kids")

Scenario: --profile overrides routing
  Given a path that would route to profile "hq"
  When `python manual.py --profile rq -i /media/tv/kids/foo.mkv -oo` runs
  Then the effective profile is "rq", not "hq"

Scenario: Pre-existing CLI overrides survive
  Given apply_cli_overrides at lines 756-812
  When the run resolves profile then applies CLI overrides
  Then CLI overrides win (post-profile)
```

**Checklist**:

- [ ] Lines 349-356 rewired to `ConfigLoader.resolve_routing`
- [ ] `-p/--profile` flag (line 825) untouched in declaration
- [ ] Help text updated to mention "overrides path-routing"
- [ ] `apply_cli_overrides` runs after profile resolution

---

### Task 9 — DELETE downloader configs

- **Priority**: Medium
- **Dependencies**: Task 3
- **Files**: `resources/readsettings.py`, `resources/config_schema.py`,
  `scripts/generate_sma_ng_sample.py`

**Acceptance Criteria**:

```gherkin
Scenario: Attributes gone
  Given a fresh ReadSettings instance
  When inspected
  Then settings.SAB / settings.deluge / settings.qBittorrent /
       settings.uTorrent are absent

Scenario: No Python consumer breaks
  Given the repository
  When grepped for those attribute names
  Then no remaining Python module references them
  (shell triggers under triggers/* use env vars and are unaffected)

Scenario: Schema omits downloaders
  Given resources/config_schema.py
  When inspected
  Then Services has no SABInstance, DelugeInstance, etc.

Scenario: Release notes called out
  Given the commit that removes downloader configs
  When release-please assembles the changelog
  Then a "downloader Python configs removed (shell triggers unaffected)"
       entry appears
```

---

## WP-5 — Tests

**Objective**: Replace `tmp_ini` with `tmp_yaml`, add new schema/routing/
sample tests, delete obsolete INI tests. Get the suite back to green.

**Deliverables**:

- Updated `tests/conftest.py`
- New `tests/test_config_schema.py`,
  `tests/test_config_routing.py`,
  `tests/test_config_sample.py`
- Updated `tests/test_config.py`, `tests/test_smoke.py`
- Deleted `tests/test_ini_*.py`

**Risk**: Low.

**Critical path**: Partial — must finish before WP-6 cutover but can begin
once WP-2 is on a branch.

### Task 14 — UPDATE `tests/conftest.py` — tmp_ini → tmp_yaml

- **Priority**: Critical
- **Dependencies**: Task 2

```gherkin
Scenario: tmp_yaml produces valid four-bucket file
  Given the new fixture
  When a test calls tmp_yaml(overrides={"base": {"video": {"codec":"h264"}}})
  Then a YAML file is written with daemon/base/profiles/services keys
  And ConfigLoader().load(path) succeeds against it

Scenario: Helper fixtures preserved
  Given conftest.py after the change
  When inspected
  Then make_stream, make_format, make_media_info, daemon_log, job_db
       are all still present
```

---

### Task 15 — UPDATE `tests/test_config.py`

- **Priority**: High
- **Dependencies**: Task 14

```gherkin
Scenario: Test classes preserved
  Given the rewritten file
  When pytest collects
  Then existing class names match (so selectors keep working)

Scenario: Multi-instance tests use new shape
  Given tests previously using sonarr_kids prefix scan
  When inspected
  Then they now write services.sonarr.{name} maps directly
```

---

### Task 16 — CREATE `tests/test_config_schema.py`

- **Priority**: High
- **Dependencies**: Task 1

```gherkin
Scenario: Extra keys warned, accepted
  Given a payload with base.converter.notakey=1
  When SmaConfig.model_validate runs
  Then no error is raised
  And the unknown key is captured in __pydantic_extra__

Scenario: Type errors hard-fail
  Given a payload with daemon.port = "not-an-int"
  When validating
  Then ValidationError is raised
  And the error path includes "daemon.port"

Scenario: routing→service cross-ref
  Given routing references sonarr.ghost where no sonarr.ghost exists
  When validating
  Then ValidationError mentions sonarr.ghost

Scenario: SECRET_KEYS coverage
  Given a config with all known secret fields populated
  When _strip_secrets is applied
  Then every secret in SECRET_KEYS / SERVICE_SECRET_FIELDS is redacted
```

---

### Task 17 — CREATE `tests/test_config_routing.py`

- **Priority**: High
- **Dependencies**: Task 2

```gherkin
Scenario: Exact match wins over shorter prefix
  Given rules ["/media/tv/", "/media/tv/kids/"]
  When matching "/media/tv/kids/foo.mkv"
  Then "/media/tv/kids/" wins

Scenario: No match → bare base
  Given rules that don't cover "/media/movies/foo.mkv"
  When resolving
  Then services == [] and profile is None

Scenario: services-omitted means no notify
  Given a rule with services: omitted
  When matched
  Then resolution.services == []

Scenario: Mirrors TestPathConfigManager structure
  Given tests/test_daemon.py:48-170
  When test_config_routing.py is read
  Then it uses the same fixture style and assertion patterns
```

---

### Task 18 — CREATE `tests/test_config_sample.py`

- **Priority**: High
- **Dependencies**: Tasks 11, 13

```gherkin
Scenario: Round-trip is bytes-identical
  Given the committed setup/sma-ng.yml.sample
  When loaded into SmaConfig and re-emitted via the generator
  Then the output is byte-for-byte identical to the committed file
```

---

### Task 19 — DELETE INI test modules

- **Priority**: Medium
- **Dependencies**: Task 3

```gherkin
Scenario: Files removed
  Given the tests/ directory
  When listed
  Then test_ini_audit.py, test_ini_merge.py, test_ini_merge_import.py
       are absent

Scenario: scripts/ini_audit.py also removed
  Given the repo (post-Task 21)
  When grepped
  Then scripts/ini_audit.py does not exist
```

---

### Task 20 — UPDATE `tests/test_smoke.py`

- **Priority**: Medium
- **Dependencies**: Task 14

```gherkin
Scenario: Smoke test uses tmp_yaml
  Given test_smoke.py
  When grepped for tmp_ini
  Then zero matches; tmp_yaml is used instead
```

---

## WP-6 — Documentation, Validation, Cutover

**Objective**: Update prose docs in two places (collapsed-three per
discovery), delete the orphaned INI audit script, run the full validation
loop, and tag the release with a `BREAKING CHANGE:` trailer.

**Deliverables**:

- Rewritten `docs/configuration.md`
- Rewritten `docs/daemon.md` Path-Based Configuration block
- Updated GitHub wiki (`/tmp/sma-wiki/`)
- `scripts/ini_audit.py` deleted
- All Level 1-4 validation gates green

**Risk**: Low. Mostly mechanical.

**Critical path**: Tail — must come last so docs don't lie about partial
state.

### Task 21 — DELETE `scripts/ini_audit.py`

- **Priority**: Medium
- **Dependencies**: Task 19

```gherkin
Scenario: Script and tests gone together
  Given the repo
  When grepped for "ini_audit"
  Then no Python file references it
```

---

### Task 23 — Sanity-check requirements pinning

- **Priority**: Medium
- **Dependencies**: Task 10

```gherkin
Scenario: CI install resolves
  Given CI runs `pip install -r setup/requirements.txt`
  When the install step finishes
  Then exit code is 0 and pydantic 2.x is selected
```

---

### Task 24 — REWRITE `docs/configuration.md`

- **Priority**: High
- **Dependencies**: Tasks 3, 13

```gherkin
Scenario: Section structure matches new shape
  Given the rewritten doc
  When inspected
  Then top-level sections include ## daemon, ## base.converter,
       ## base.video, ## base.hdr, ## base.audio, ## base.subtitle,
       ## base.metadata, ## base.naming, ## base.analyzer,
       ## base.permissions, ## profiles, ## services.sonarr,
       ## services.radarr, ## services.plex, ## daemon.routing

Scenario: Downloader sections removed
  Given lines 326-330 region
  When inspected
  Then [SABNZBD]/[Deluge]/[qBittorrent]/[uTorrent] sections are gone
  And replaced with a note that downloader integration is shell-trigger-only

Scenario: Markdownlint clean
  Given the new doc
  When `markdownlint docs/configuration.md` runs
  Then no errors or warnings
```

---

### Task 25 — REWRITE `docs/daemon.md` — routing block

- **Priority**: High
- **Dependencies**: Task 2

```gherkin
Scenario: Path Routing section
  Given the rewritten doc
  When inspected
  Then the Path-Based Configuration block (152-209) is replaced by a
       ## Path Routing section documenting daemon.routing rules,
       longest-prefix semantics, bare-base fallback, services omission

Scenario: Top-Level Keys table updated
  Given the table at 184-197
  When inspected
  Then it lists keys from the new daemon section, not the old flat shape

Scenario: Cluster reference matches
  Given Cluster Configuration Reference (775+)
  When inspected
  Then it reflects the new daemon-section keys
```

---

### Task 26 — UPDATE GitHub wiki

- **Priority**: High
- **Dependencies**: Tasks 24, 25
- **Workflow**: `cd /tmp/sma-wiki && git add -A && git commit -m
  "docs: restructure config to four-bucket layout" && git push origin
  HEAD:master`

```gherkin
Scenario: Wiki mirrors docs/
  Given /tmp/sma-wiki/Configuration.md and Daemon.md
  When diffed against docs/configuration.md and docs/daemon.md
  Then content is structurally equivalent (modulo wiki link syntax)

Scenario: Wiki pushed
  Given the local wiki working tree
  When git push origin HEAD:master runs
  Then it succeeds; new content is live on github.com wiki
```

---

### Task 27 — CHANGELOG / release-please trailer

- **Priority**: Critical
- **Dependencies**: Task 3 (loader-rewrite commit body)

```gherkin
Scenario: Breaking-change trailer present
  Given the loader-rewrite commit
  When `git log` is inspected
  Then the body contains a "BREAKING CHANGE: ..." trailer
  And release-please picks it up on the next run

Scenario: Release notes mention cutover
  Given the auto-generated release PR
  When inspected
  Then it calls out:
    - four-bucket sma-ng.yml shape required
    - autoProcess.ini fully removed
    - downloader Python configs removed
    - new daemon.routing replaces name-prefix scanning
```

**Checklist**:

- [ ] No manual `v*` tag (release-please owns version bumps)
- [ ] Commit message follows conventional commits (`feat!:` or `refactor!:`)
- [ ] No AI attribution / no Co-Authored-By line (CLAUDE.md commit rule)

---

### Task 28 — Final validation

- **Priority**: Critical
- **Dependencies**: All preceding tasks

```gherkin
Scenario: Lint clean
  Given the full repo
  When `mise run dev:lint` runs
  Then exit 0

Scenario: Format clean
  When `mise run dev:format` runs (check mode)
  Then exit 0

Scenario: Tests green
  When `mise run test` runs
  Then all tests pass

Scenario: Pre-commit clean
  When `mise run dev:precommit` runs
  Then exit 0

Scenario: Manual smokes pass
  Given an old-shape config
  When `SMA_CONFIG=/tmp/old.yml python daemon.py` runs
  Then stderr contains "Old flat-shape"

  Given an .ini path
  When `SMA_CONFIG=/tmp/x.ini python daemon.py` runs
  Then stderr contains "no longer supported"

  Given a config with base.converter.notakey
  When daemon starts
  Then logs include "Unknown config key: base.converter.notakey"

  Given a four-bucket config with services.sonarr.kids
  When `python manual.py -i /media/tv/kids/foo.mkv -oo` runs
  Then sonarr.kids is in the resolved service list

  Given the same config and input
  When `python manual.py --profile rq -i /media/tv/kids/foo.mkv -oo` runs
  Then the effective profile is "rq" regardless of routing
```

---

## Dependencies Graph

```text
Task 10 ─┐
         ├─→ Task 1 ─┬─→ Task 2 ─┬─→ Task 3 ──→ Task 4 ──┐
                     │           │                       │
                     │           ├─→ Task 5 ──→ Task 6 ──┼─→ Task 7
                     │           │                       │
                     │           └─→ Task 8               └─→ Task 9
                     │
                     ├─→ Task 11 ─→ Task 12 ─→ Task 13 ─→ Task 18
                     │       │
                     │       └────→ Task 22
                     │
                     └─→ Task 16

Task 2  ─→ Task 14 ─┬─→ Task 15
                    ├─→ Task 17
                    └─→ Task 20

Task 3  ─→ Task 19 ─→ Task 21

(All) ─→ Task 23 ─→ Task 24 / 25 ─→ Task 26 ─→ Task 27 ─→ Task 28
```

---

## Critical Path

The critical path (longest serial chain that determines minimum elapsed
time) is:

```text
Task 10 → Task 1 → Task 2 → Task 3 → Task 5 → Task 7 → Task 15 → Task 28
```

Tasks 11/12/13/22 (sample generator + CI) and 16/17/18 (new tests) sit on
parallel branches off Task 1 and Task 2; they should be worked
opportunistically while Task 3's larger rewrite is in flight to compress
the schedule.

**Key bottlenecks**:

- **Task 3** (readsettings rewrite) — single largest delta; everything in
  WP-4 and WP-5 waits on it.
- **Task 7** (handler webhook flow) — many touch points, easy to miss one.
  Mitigate with a grep-pass on the line-number references in the PRP.
- **Task 26** (wiki push) — easy to forget; folded into the WP-6 doc commit
  per CLAUDE.md.

---

## Implementation Recommendations

**Sequencing within WPs**: Inside WP-2, do Task 2 (new loader) on a branch
**before** rewriting `readsettings.py`. Add a few smoke tests against the
loader directly (Task 16 stubs) so Task 3 has a regression net before the
rewrite lands.

**Parallelisation opportunities** (single-maintainer, but useful for
context-switching during long compilation/test runs):

- WP-3 (sample generator + CI) can be drafted as soon as Task 1 lands
- New test files (Tasks 16, 17, 18) can be drafted against the schema and
  loader before WP-4 finishes consumer migration

**Commit boundaries** (per CLAUDE.md "small logical commits" rule):

1. `feat: add pydantic v2 dependency` (Task 10)
2. `feat: add config_schema with four-bucket pydantic models` (Task 1)
3. `feat: add config_loader with routing engine` (Task 2 + Task 16/17 stubs)
4. `feat!: rewrite ReadSettings on top of config_loader` (Tasks 3, 4, 9
   together — they form one logical change). **Include `BREAKING CHANGE:`
   trailer.**
5. `refactor(daemon): route webhooks via config_loader` (Tasks 5, 6, 7)
6. `refactor(manual): resolve profile/services via routing engine` (Task 8)
7. `feat: schema-driven sma-ng.yml.sample generator` (Tasks 11, 12, 13)
8. `ci: add config-sample-consistency check` (Task 22)
9. `test: migrate fixtures and add schema/routing/sample tests`
   (Tasks 14, 15, 17, 18, 20)
10. `chore: delete obsolete INI tooling and tests` (Tasks 19, 21)
11. `docs: restructure config docs for four-bucket layout`
    (Tasks 24, 25, 26)
12. (no commit — Task 28 is validation only)

After each commit: `git pull --rebase && git push`.

**Resource allocation**: Single-maintainer; no parallel owners. Block ~5
focused working days; expect a half-day for unforeseen consumer breakage
(grep-pass for attribute access on `ReadSettings` should catch most).
