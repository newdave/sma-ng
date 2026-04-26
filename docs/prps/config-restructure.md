name: "PRP — sma-ng.yml Config Restructure"
description: |

## Purpose

Restructure `config/sma-ng.yml` from a 18-flat-key layout into four semantic
top-level blocks (`daemon` / `base` / `profiles` / `services`), introduce
named service instances, replace prefix-name multi-instance scanning with an
explicit map, formalise path-routing under `daemon.routing` (longest-prefix
match), validate the entire file with a pydantic v2 schema (warn on unknown
keys, fail on type errors), generate `setup/sma-ng.yml.sample` from the
schema, and remove the deprecated `autoProcess.ini` parsing path entirely.

Hard cutover. No migration tool. No major version bump.

---

## Discovery Summary

### Initial Task Analysis

Brainstorming session
([docs/brainstorming/2026-04-26-config-restructure.md](../brainstorming/2026-04-26-config-restructure.md))
established the four-bucket target shape, longest-prefix routing semantics,
named service instances (`services.sonarr.kids` for the kids library, `.main`
for the main library), and the validation strategy. Codebase research
revealed the implementation is more incremental than the brainstorm assumed:
profiles already mirror flat sections in the current YAML, longest-prefix
routing already exists for `path_configs`, and INI sample files are already
deleted.

### User Clarifications Received

- **Q:** What's driving the restructure?
  **A:** Human readability and operational clarity. Not enabling new
  functionality.
  **Impact:** Schema-driven validation and grouping become first-class
  goals; migration tooling is unnecessary.
- **Q:** Migration strategy?
  **A:** Hard cutover. No migration tool. No major version bump.
  **Impact:** Loader rewrites can be aggressive; old keys produce errors,
  not deprecation warnings.
- **Q:** Routing — one profile per path or stackable?
  **A:** One profile per path; longest-prefix wins; bare-`base` fallback
  on no match.
  **Impact:** Routing engine is a single-pass list walk, no merge logic.
- **Q:** Multiple service instances?
  **A:** Yes — e.g. `sonarr.kids` for `/media/tv/kids/**` vs `sonarr.main`
  for `/media/tv/**`.
  **Impact:** `services.<type>` becomes a map of named instances; routing
  rules reference instances as `<type>.<instance>`.
- **Q:** Routing rule with `services:` omitted?
  **A:** No services notified (explicit-opt-in).
  **Impact:** Empty/missing `services` is a valid rule, not an error.
- **Q:** Schema library?
  **A:** Pydantic v2.
  **Impact:** New runtime dep; sample generator uses `model_dump()` +
  `ruamel.yaml`.
- **Q:** Unknown keys?
  **A:** Warn-and-continue.
  **Impact:** Pydantic models use `extra="allow"` with a post-validate
  walker that logs unknown paths at WARNING.
- **Q:** Sample drift prevention?
  **A:** Generate `sma-ng.yml.sample` from the schema; CI checks committed
  sample matches.
  **Impact:** New `mise` task plus a CI workflow check.

### Missing Requirements Identified

Discovered during research:

- `setup/autoProcess.ini.sample` and `*.sample-lq` **do not exist** (already
  removed in prior cleanup); only `config/autoProcess*.ini.bak` user-side
  leftovers remain. Loader must still error on `SMA_CONFIG` pointing to an
  `.ini`, but no sample-file deletion is needed.
- `manual.py` `--profile` flag **already exists** (line 825). No new flag is
  required; only its semantics change.
- `daemon.path_configs` (`resources/daemon/config.py:373`) already implements
  the exact longest-prefix routing the new `daemon.routing` requires.
  Restructure is a rename + addition of `services:` selector + a strict
  schema, not a greenfield routing engine.
- Downloader configs (`SAB`, `deluge`, `qBittorrent`, `uTorrent`) are read
  into `ReadSettings` but **unused by any Python module** (shell triggers
  use env vars). Decision required: drop them entirely from the new schema,
  or retain as schema-only stubs for forward compatibility. **PRP decision:
  drop.** Document the change in release notes; shell triggers are
  unaffected.
- `resources/docs.html` is a thin auto-rendering shell; the inline `/docs`
  endpoint serves `docs/*.md` directly. CLAUDE.md's "three places" rule
  collapses to two for config docs (`docs/` + GitHub wiki).
- `SECRET_KEYS` in `resources/daemon/constants.py` controls redaction in the
  cluster-config admin endpoint. New per-instance secrets
  (`services.sonarr.<name>.apikey`, etc.) must be added there to preserve
  redaction.
- `pyrightconfig.json` exists but Pyright is not run in CI. New pydantic
  models must still be readable by Pyright in case CI gates are added later.

## Goal

Ship a single, breaking restructure of `config/sma-ng.yml` such that:

1. The file has exactly four top-level keys: `daemon`, `base`, `profiles`,
   `services`.
2. `base` contains all converter defaults; `profiles.<name>` mirrors the
   shape of `base` and overlays it via shallow-per-section merge.
3. `services.<type>` is a map of named instances. Routing rules reference
   instances as `<type>.<instance>`.
4. `daemon.routing` is a longest-prefix-match list of `{match, profile,
   services}` rules. No-match falls through to bare `base`. `services`
   omitted = no notifications for that path.
5. The full file is validated by pydantic v2 on load. Unknown keys → WARN.
   Type/shape errors → hard fail with the dotted path.
6. `setup/sma-ng.yml.sample` is generated from the schema; CI fails if the
   committed sample drifts.
7. `autoProcess.ini` parsing is fully removed; loader emits a clear error if
   `SMA_CONFIG` resolves to an `.ini` file.
8. `manual.py --profile <name>` overrides path-routing.
9. All call sites of `ReadSettings` continue to work (signature preserved).

## Why

- **Operational clarity:** four-bucket layout means a reader can identify
  the owning subsystem of any setting at a glance.
- **Multi-library support:** the project author runs separate Sonarr
  instances per library; the current name-prefix-scanning hack
  (`sonarr_kids`, `sonarr-main` matched in `_read_sonarr_radarr` 909-940)
  is fragile and undocumented. Named instances are explicit.
- **Sample drift prevention:** `setup/sma-ng.yml.sample` and
  `docs/configuration.md` chronically drift from `DEFAULTS` in
  `readsettings.py`. Schema-as-source-of-truth eliminates this.
- **Typo detection:** today a misspelled key silently uses the default. New
  schema warns with a dotted path so operators see the problem in the log.

## What

User-visible behaviour:

- Editing `sma-ng.yml` requires the new four-bucket layout. Daemon refuses
  to start with a clear error if the old shape is detected (no `base:`
  block, but `converter:` at top level).
- Operators with multiple libraries declare each Sonarr/Radarr/Plex
  instance under `services.<type>.<name>`, then reference them in
  `daemon.routing`.
- `manual.py --profile <name>` continues to work; no flag change.
- Pointing `SMA_CONFIG` at an `.ini` file produces:
  `ERROR: autoProcess.ini is no longer supported. Convert to sma-ng.yml — see docs/configuration.md.`

### Success Criteria

- [ ] `setup/sma-ng.yml.sample` reflects the four-bucket layout
- [ ] Loading any old-shape `sma-ng.yml` (top-level `converter:` etc.)
      produces a hard error pointing at the missing `base:` block
- [ ] Loading an `.ini` file via `SMA_CONFIG` produces a hard error
- [ ] Unknown keys produce WARN-level log lines with full dotted path
- [ ] Type errors produce hard fail with pydantic's error message including
      the dotted path
- [ ] Routing: `/media/tv/kids/foo.mkv` → `services.sonarr.kids` notified;
      `/media/tv/4k/bar.mkv` → `services.sonarr.main` notified;
      `/media/movies/baz.mkv` (no matching rule) → bare `base`, no services
      notified
- [ ] `manual.py --profile rq -i file.mkv` overrides path-routing
- [ ] CI workflow `config-sample-consistency` fails on a hand-edited
      `sma-ng.yml.sample` that diverges from generator output
- [ ] All existing tests pass (after fixture updates for `tmp_ini` →
      `tmp_yaml`)
- [ ] `mise run dev:lint`, `mise run test`, `mise run dev:precommit` all
      green
- [ ] `docs/configuration.md`, `docs/daemon.md`, GitHub wiki updated

## All Needed Context

### Research Phase Summary

- **Codebase patterns found:**
  - Existing longest-prefix routing in `path_configs`/`path_rewrites`
    (`resources/daemon/config.py:354,373`) — direct precedent for
    `daemon.routing`.
  - Existing `_apply_profile` shallow-merge semantics
    (`resources/readsettings.py:497-504`) — preserve under new schema.
  - Existing secrets-redaction pattern in `_strip_secrets`
    (`resources/daemon/config.py:40-47`) — extend `SECRET_KEYS` for new
    per-instance fields.
  - Existing ruamel.yaml round-trip in `_write_node_id_to_yaml`
    (`resources/daemon/config.py:18-37`) — pattern for sample generator.
  - Existing test pattern for path-config (`tests/test_daemon.py:48-170`,
    `TestPathConfigManager`) — template for routing tests.
- **External research needed:** No — pydantic v2 is sufficiently documented
  in the official site; ruamel.yaml is already in use.
- **Knowledge gaps:** None — codebase has all required patterns.

### Documentation & References

```yaml
- file: docs/brainstorming/2026-04-26-config-restructure.md
  why: Authoritative target shape and decisions; reflects user intent

- file: resources/readsettings.py
  why: Current loader (1112 lines). The DEFAULTS dict (24-296) is the
        complete enumeration of converter settings. _apply_profile
        (497-504) is the merge semantic to preserve.
  critical: |
    Lines 350-362 contain the INI auto-migration code path that must be
    deleted. _read_sonarr_radarr (909-943) does name-prefix scanning that
    becomes obsolete under services.<type>.<instance>.

- file: resources/daemon/config.py
  why: PathConfigManager loader and routing precursors. _parse_config_data
        (336-397) enumerates current daemon.* keys. Lines 373, 430-475
        show longest-prefix routing already implemented.
  critical: |
    get_recycle_bin (485-499) has an INI fallback to delete. _strip_secrets
    (40-47) needs SECRET_KEYS extension for service.<type>.<instance>.apikey.

- file: resources/yamlconfig.py
  why: yamlconfig.load is the universal entry point; migrate_ini_to_yaml
        (26-78) is the function to delete

- file: resources/daemon/constants.py
  why: SECRET_KEYS, DAEMON_SECTION, DEFAULT_PROCESS_CONFIG live here

- file: setup/sma-ng.yml.sample
  why: Current sample (486 lines); shape to emit from new generator

- file: setup/requirements.txt
  why: Add pydantic>=2,<3

- file: pyproject.toml
  why: Add pydantic to [project.dependencies] (lines 35-49) to keep parity
        with requirements.txt; ruff config 78-99; pytest config 101-109

- file: tests/conftest.py
  why: tmp_ini fixture must be replaced with tmp_yaml; many tests depend
        on it

- file: tests/test_config.py
  why: 787 lines of ReadSettings coverage; tests must be updated for new
        shape but signature stays

- file: tests/test_daemon.py
  why: TestPathConfigManager (45-170) is the template for new routing
        engine tests

- file: docs/configuration.md
  why: Section-by-section config reference (370 lines); rewrite headings
        to reflect new buckets

- file: docs/daemon.md
  why: Path-Based Configuration block 152-209 must become routing docs;
        Top-Level Keys table 184-197

- file: .pre-commit-config.yaml
  why: hooks that run on Python files; must pass after rewrite

- url: https://docs.pydantic.dev/latest/concepts/models/
  why: BaseModel, model_config(extra="allow"), model_dump
  critical: Pydantic v2 model_config replaces v1 Config class

- url: https://docs.pydantic.dev/latest/concepts/serialization/
  why: model_dump for sample generation
  critical: |
    model_dump(mode="python") returns native types; pass to ruamel.yaml's
    YAML().dump() to produce comment-preservable output. Do NOT use
    model_dump_json or PyYAML.

- url: https://yaml.readthedocs.io/en/latest/example.html
  why: ruamel.yaml round-trip mode (typ="rt") for sample generation with
        comments
```

### Current Codebase tree (relevant subset)

```text
sma/
├── config/
│   ├── sma-ng.yml                # user config (out of git)
│   ├── autoProcess.ini.bak       # legacy, user-side, ignore
│   └── autoProcess.{lq,rq}.ini.bak
├── converter/                    # codec/format definitions, FFmpeg wrapper
├── docs/
│   ├── configuration.md          # 370 lines; sections to rewrite
│   ├── daemon.md                 # 807 lines; routing block 152-209
│   ├── brainstorming/2026-04-26-config-restructure.md
│   └── prps/config-restructure.md  # this file
├── manual.py                     # CLI; --profile already at line 825
├── daemon.py                     # thin entry point
├── pyproject.toml                # ruff, pytest, [project.dependencies]
├── pyrightconfig.json
├── resources/
│   ├── readsettings.py           # 1112-line loader to rewrite
│   ├── yamlconfig.py             # YAML load + INI migrator (delete migrator)
│   ├── mediaprocessor.py         # consumer of settings.Plex
│   ├── rename_util.py            # consumer of settings.Plex
│   ├── postprocess.py
│   ├── metadata.py
│   ├── daemon/
│   │   ├── config.py             # PathConfigManager, routing precursor
│   │   ├── constants.py          # SECRET_KEYS, DAEMON_SECTION
│   │   ├── handler.py            # webhook + admin endpoints
│   │   ├── server.py
│   │   ├── worker.py
│   │   ├── threads.py
│   │   └── db.py
│   └── docs.html                 # auto-render shell; no inline help map
├── autoprocess/
│   └── plex.py                   # consumer of settings.Plex
├── setup/
│   ├── sma-ng.yml.sample         # to be regenerated
│   ├── requirements.txt          # add pydantic
│   ├── daemon.json.sample        # already a deprecation stub (precedent)
│   └── ...
├── tests/
│   ├── conftest.py               # tmp_ini fixture to replace
│   ├── test_config.py            # 787 lines; ReadSettings coverage
│   ├── test_daemon.py            # PathConfigManager tests
│   ├── test_manual.py            # patches manual.ReadSettings
│   ├── test_smoke.py             # uses tmp_ini
│   └── test_ini_*.py             # ini-merge tests; delete or rescope
├── triggers/
│   └── lib/, media_managers/, torrents/, usenet/  # shell scripts; no Python config touch
├── scripts/
│   ├── ini_audit.py              # closest precedent for sample audit
│   └── plexmatch.py              # consumer of ReadSettings
└── .mise/
    ├── tasks/
    │   ├── config/{audit,generate,gpu,roll}
    │   ├── dev/{format,lint,precommit}
    │   └── test/{run,cov,lint,...}
    └── shared/
```

### Desired Codebase tree (additions)

```text
sma/
├── resources/
│   ├── config_schema.py          # NEW — pydantic v2 models for the entire shape
│   ├── config_loader.py          # NEW — load+validate+routing engine
│   └── readsettings.py           # REWRITTEN — thin adapter around config_loader
├── scripts/
│   └── generate_sma_ng_sample.py # NEW — emits setup/sma-ng.yml.sample from schema
├── .mise/tasks/config/
│   └── generate-sample           # NEW — wraps the generator script
├── .github/workflows/
│   └── ci.yml                    # MODIFY — add config-sample-consistency job
└── tests/
    ├── conftest.py               # MODIFY — replace tmp_ini with tmp_yaml
    ├── test_config_schema.py     # NEW — pydantic model tests
    ├── test_config_routing.py    # NEW — longest-prefix routing tests
    ├── test_config_sample.py     # NEW — generator round-trip test
    └── test_ini_merge*.py        # DELETE
```

### Known Gotchas of our codebase & Library Quirks

```python
# CRITICAL: pydantic v2 — model_config replaces v1's Config class.
#   class MyModel(BaseModel):
#       model_config = ConfigDict(extra="allow")
# `extra="allow"` is required for warn-and-continue on unknown keys; we
# capture extras via __pydantic_extra__ and emit one WARNING per dotted path.

# CRITICAL: ruamel.yaml round-trip mode preserves comments. Use:
#   from ruamel.yaml import YAML
#   yaml = YAML(typ="rt")
#   yaml.indent(mapping=2, sequence=4, offset=2)
# Plain `yaml.dump(data)` (typ="safe") strips comments — do NOT use that
# for the sample generator.

# CRITICAL: Project standardised on ruamel.yaml. Do NOT introduce PyYAML.
# pydantic.model_dump(mode="python") + ruamel.yaml dump is the path.

# CRITICAL: Ruff config in pyproject.toml has line-length=200 and
# indent-width=2. New files MUST conform — pre-commit will reject otherwise.

# CRITICAL: Every existing test that uses `tmp_ini` patches
# ReadSettings._validate_binaries. New tests must do the same OR the loader
# rewrite must be split so that the schema layer is testable without
# touching FFmpeg binary discovery.

# CRITICAL: SECRET_KEYS in resources/daemon/constants.py must be extended
# to redact `services.<type>.<instance>.apikey` and `.token` and `.password`
# in the admin /configs endpoint. Today it only redacts top-level
# `daemon.api_key` etc.

# CRITICAL: _strip_secrets walks `data["daemon"]` only. Restructure must
# extend it to also redact under `data["services"]`.

# CRITICAL: `_apply_profile` (readsettings.py:497-504) does shallow-merge
# per section: `data.setdefault(section, {}).update(overrides)`. The new
# loader must preserve this exact semantic — deep merge is wrong (would
# change behaviour for users with partial overlays).

# CRITICAL: manual.py at line 349-356 iterates `settings.sonarr_instances`
# and matches paths by `instance["path"]` prefix. After restructure, this
# logic moves into the routing engine; manual.py asks the engine "which
# services for this input file?" and gets back a resolved list of
# (type, instance) tuples.

# CRITICAL: daemon.py:98-110 instantiates ReadSettings inside
# run_smoke_test for every config_path returned by
# path_config_manager.get_all_configs(). Multi-config support must survive.

# CRITICAL: pydantic v2 raises pydantic.ValidationError, NOT ValueError.
# The startup error path must catch ValidationError specifically and emit
# the formatted error (not just str(e)) so users see field paths.
```

## Implementation Blueprint

### Data models and structure

```python
# resources/config_schema.py — new file
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Annotated, Literal

class _Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

class ConverterSettings(_Base):
    ffmpeg: str | None = None
    ffprobe: str | None = None
    threads: int = 0
    output_directory: str | None = None
    output_format: str = "mp4"
    # ... full enumeration mirrors readsettings.DEFAULTS["converter"]

class VideoSettings(_Base): ...
class HDRSettings(_Base): ...
class AudioSettings(_Base): ...
class SubtitleSettings(_Base): ...
class MetadataSettings(_Base): ...
class NamingSettings(_Base): ...
class AnalyzerSettings(_Base): ...
class PermissionSettings(_Base): ...

class BaseConfig(_Base):
    converter: ConverterSettings = Field(default_factory=ConverterSettings)
    video: VideoSettings = Field(default_factory=VideoSettings)
    hdr: HDRSettings = Field(default_factory=HDRSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    subtitle: SubtitleSettings = Field(default_factory=SubtitleSettings)
    metadata: MetadataSettings = Field(default_factory=MetadataSettings)
    naming: NamingSettings = Field(default_factory=NamingSettings)
    analyzer: AnalyzerSettings = Field(default_factory=AnalyzerSettings)
    permissions: PermissionSettings = Field(default_factory=PermissionSettings)

class ProfileOverlay(_Base):
    """Mirrors BaseConfig but every field is optional — partial overlay."""
    converter: ConverterSettings | None = None
    video: VideoSettings | None = None
    hdr: HDRSettings | None = None
    audio: AudioSettings | None = None
    subtitle: SubtitleSettings | None = None
    metadata: MetadataSettings | None = None
    naming: NamingSettings | None = None
    analyzer: AnalyzerSettings | None = None
    permissions: PermissionSettings | None = None

# Service instance schemas
class SonarrInstance(_Base):
    url: str
    apikey: str
    rename: bool = True
    rescan: bool = True
    in_progress_check: bool = True
    block_reprocess: bool = False
    # path NOT included — routing rules carry path; instances are name-keyed

class RadarrInstance(SonarrInstance): ...

class PlexInstance(_Base):
    url: str
    token: str | None = None
    refresh: bool = False

class Services(_Base):
    sonarr: dict[str, SonarrInstance] = Field(default_factory=dict)
    radarr: dict[str, RadarrInstance] = Field(default_factory=dict)
    plex: dict[str, PlexInstance] = Field(default_factory=dict)
    # downloaders dropped per discovery

# Routing
class RoutingRule(_Base):
    match: str
    profile: str | None = None  # None → use base
    services: list[str] = Field(default_factory=list)  # ["sonarr.kids", "plex.main"]

class ScanPath(_Base):
    path: str
    interval: int = 3600
    enabled: bool = True

class PathRewrite(_Base):
    from_: str = Field(alias="from")
    to: str

class DaemonConfig(_Base):
    host: str = "0.0.0.0"
    port: int = 8585
    workers: int = 4
    api_key: str | None = None
    db_url: str | None = None
    ffmpeg_dir: str | None = None
    node_id: str | None = None
    job_timeout_seconds: int = 7200
    progress_log_interval: int = 30
    smoke_test: bool = True
    recycle_bin_max_age_days: int = 30
    recycle_bin_min_free_gb: int = 50
    log_ttl_days: int = 30
    node_expiry_days: int = 7
    log_archive_dir: str | None = None
    log_archive_after_days: int = 7
    log_delete_after_days: int = 30
    default_args: dict[str, str] = Field(default_factory=dict)
    scan_paths: list[ScanPath] = Field(default_factory=list)
    path_rewrites: list[PathRewrite] = Field(default_factory=list)
    routing: list[RoutingRule] = Field(default_factory=list)
    media_extensions: list[str] = Field(default_factory=lambda: [".mkv", ".mp4", ".avi", ...])

class SmaConfig(_Base):
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    base: BaseConfig = Field(default_factory=BaseConfig)
    profiles: dict[str, ProfileOverlay] = Field(default_factory=dict)
    services: Services = Field(default_factory=Services)

    @model_validator(mode="after")
    def _validate_routing_references(self) -> "SmaConfig":
        # Each routing rule's `services` entries must reference an existing instance
        for i, rule in enumerate(self.daemon.routing):
            for ref in rule.services:
                if "." not in ref:
                    raise ValueError(f"daemon.routing[{i}].services: '{ref}' must be of form '<type>.<instance>'")
                stype, sname = ref.split(".", 1)
                instances = getattr(self.services, stype, None)
                if instances is None:
                    raise ValueError(f"daemon.routing[{i}].services: unknown service type '{stype}'")
                if sname not in instances:
                    raise ValueError(f"daemon.routing[{i}].services: '{ref}' has no matching services.{stype}.{sname}")
            # Validate profile reference too
            if rule.profile is not None and rule.profile not in self.profiles:
                raise ValueError(f"daemon.routing[{i}].profile: '{rule.profile}' not defined in profiles")
        return self
```

### list of tasks to be completed in order

```yaml
Task 1 — CREATE resources/config_schema.py:
   - DEFINE pydantic v2 models per blueprint above
   - MIRROR every key in resources/readsettings.py DEFAULTS (24-296)
   - PRESERVE exact default values from DEFAULTS (these are user-facing
     defaults; changing them silently is a regression)
   - INCLUDE @model_validator that cross-references routing→services and
     routing→profiles
   - SET model_config = ConfigDict(extra="allow") on _Base

Task 2 — CREATE resources/config_loader.py:
   - DEFINE class ConfigLoader with public API:
     - load(path) -> SmaConfig
     - apply_profile(cfg: SmaConfig, profile_name: str) -> BaseConfig
     - resolve_routing(cfg: SmaConfig, file_path: str) ->
         RoutingResolution(profile, services, base_config)
   - IMPLEMENT INI rejection: if path.lower().endswith(".ini"), raise
     ConfigError("autoProcess.ini is no longer supported. Convert to
     sma-ng.yml — see docs/configuration.md")
   - LOAD with ruamel.yaml safe load; pass dict to SmaConfig
   - IMPLEMENT unknown-key warner: walk model.__pydantic_extra__ recursively,
     log one WARNING per dotted path
   - IMPLEMENT longest-prefix match for routing — MIRROR
     resources/daemon/config.py:373 sort + 430-444 walk
   - IMPLEMENT shallow-merge profile application — MIRROR
     resources/readsettings.py:497-504 semantic exactly

Task 3 — REWRITE resources/readsettings.py:
   - PRESERVE public API: ReadSettings(configFile=None, logger=None,
     profile=None) and all attribute names listed in research §1.1
   - INTERNAL: replace DEFAULTS dict + _read_* methods + _apply_profile +
     migrateFromOld + INI loader with a thin adapter that:
       1. Calls ConfigLoader.load(configFile)
       2. Applies profile via ConfigLoader.apply_profile if profile is set
       3. Flattens the resolved BaseConfig back onto self.* attributes
          using the exact attribute names listed in research §1.1
   - DELETE _read_sonarr_radarr name-prefix scanning (lines 909-943).
     Replace with: read services.sonarr/radarr maps and expose them as
     `sonarr_instances` / `radarr_instances` for backward compatibility
     with manual.py:349-356
   - PRESERVE _validate_binaries (1014) — every test patches it
   - DELETE migrate_ini_to_yaml call (350-362)
   - DELETE migrateFromOld entirely (1037-1112) — no warn-and-rename;
     historic key renames (`sort-streams`, `prefer-more-channels`,
     `default-more-channels`, `final-sort`, `copy-original-before`,
     `move-after`, top-level `gpu`) become hard schema errors. Users edit
     by hand. Document removed keys in release notes.
   - KEEP HWACCEL_PROFILES, CODEC_ALIASES, HWACCEL_CODEC_MAP tables
     (405-445) — these remain part of the loader

Task 4 — DELETE resources/yamlconfig.py:migrate_ini_to_yaml (26-78):
   - REMOVE function
   - PRESERVE yamlconfig.load (the ruamel-based loader still needed)
   - SCAN every import site of migrate_ini_to_yaml; remove imports

Task 5 — UPDATE resources/daemon/config.py:
   - REPLACE _parse_config_data (336-397) with a call to ConfigLoader.load
     and read fields off the resulting SmaConfig.daemon
   - DELETE get_recycle_bin INI fallback (485-499); recycle_bin now lives
     under base.converter.recycle_bin only
   - UPDATE _strip_secrets (40-47) to also redact services.<type>.<instance>
     secret fields; extend SECRET_KEYS in constants.py
   - REPLACE path_configs with a routing-engine wrapper:
     - get_config_for_path → returns config path of the routing match
     - get_profile_for_path → returns matched profile or None
   - DROP get_args_for_path entirely. Per-path default_args is removed;
     only global `daemon.default_args` survives. Document in release notes.
   - PRESERVE rewrite_path (468-475); path_rewrites stays under daemon

Task 6 — UPDATE resources/daemon/constants.py:
   - EXTEND SECRET_KEYS to include service instance secrets. New entries:
     services.sonarr.*.apikey, services.radarr.*.apikey,
     services.plex.*.token. Use a glob-aware redactor or extend
     _strip_secrets to handle the wildcard.

Task 7 — UPDATE resources/daemon/handler.py:
   - UPDATE webhook submission flow (830-836, 857-861, 879-880, 902-903)
     to use the new ConfigLoader.resolve_routing() API
   - UPDATE /configs admin endpoint to surface daemon.routing rules in
     addition to existing path mapping output

Task 8 — UPDATE manual.py:
   - REWIRE lines 349-356 (sonarr/radarr instance iteration) to use
     ConfigLoader.resolve_routing(input_file_path) for service notification
   - PRESERVE -p/--profile flag at line 825 — only docs/help text need
     mentioning the new override semantic
   - PRESERVE apply_cli_overrides (756-812) — runs after profile resolution

Task 9 — DELETE downloader configs:
   - REMOVE settings.SAB / settings.deluge / settings.qBittorrent /
     settings.uTorrent attributes from ReadSettings
   - REMOVE corresponding DEFAULTS entries from schema
   - VERIFY no Python module reads these (research confirmed clean)
   - UPDATE setup/sma-ng.yml.sample generator to omit them
   - DOCUMENT the removal in release notes

Task 10 — INSTALL pydantic:
   - ADD pydantic>=2,<3 to setup/requirements.txt
   - ADD pydantic>=2,<3 to pyproject.toml [project.dependencies]
   - PIN the same minor version in both files

Task 11 — CREATE scripts/generate_sma_ng_sample.py:
   - IMPORT SmaConfig, build a "fully populated" instance with all defaults
     plus illustrative example entries for profiles + services + routing
     (mirror current sample's example values where possible)
   - EMIT via ruamel.yaml YAML(typ="rt") with comment headers per section
   - WRITE to setup/sma-ng.yml.sample
   - PRINT a diff if --check is passed (CI mode)

Task 12 — CREATE .mise/tasks/config/generate-sample:
   - Use existing .mise/tasks/config/generate as a template
   - Header: #MISE description="Regenerate setup/sma-ng.yml.sample from
     pydantic schema"
   - Body: "$PY" scripts/generate_sma_ng_sample.py "$@"

Task 13 — REGENERATE setup/sma-ng.yml.sample:
   - mise run config:generate-sample
   - Manually review output; commit with the schema change

Task 14 — UPDATE tests/conftest.py:
   - REPLACE tmp_ini fixture with tmp_yaml that writes the new four-bucket
     YAML shape
   - PRESERVE the existing helpers: make_stream, make_format,
     make_media_info, daemon_log, job_db

Task 15 — UPDATE tests/test_config.py:
   - ADAPT every test to use tmp_yaml + the new shape
   - PRESERVE test class names so pytest selectors keep working
   - Multi-instance tests now write services.sonarr.{name} maps directly

Task 16 — CREATE tests/test_config_schema.py:
   - Test pydantic validation: extra keys, type errors, missing required
     fields, routing→services cross-reference, routing→profile reference
   - Test that all SECRET_KEYS paths are redacted by _strip_secrets

Task 17 — CREATE tests/test_config_routing.py:
   - MIRROR tests/test_daemon.py:48-170 TestPathConfigManager structure
   - exact-match, longest-prefix-wins, no-match-falls-through-to-base,
     services-omitted-means-no-notify, services-references-validated

Task 18 — CREATE tests/test_config_sample.py:
   - Round-trip: load setup/sma-ng.yml.sample → SmaConfig → dump → compare
     bytes-identical to committed sample (CI consistency check primitive)

Task 19 — DELETE tests/test_ini_audit.py, tests/test_ini_merge.py,
              tests/test_ini_merge_import.py:
   - These cover INI tooling that no longer exists
   - VERIFY scripts/ini_audit.py is also deleted (Task 21)

Task 20 — UPDATE tests/test_smoke.py:
   - REPLACE tmp_ini with tmp_yaml

Task 21 — DELETE scripts/ini_audit.py:
   - Tooling for the old INI-vs-sample audit; superseded by sample
     generator + CI check

Task 22 — UPDATE .github/workflows/ci.yml:
   - ADD job 'config-sample-consistency' that runs:
       mise run config:generate-sample --check
     This must fail if the regenerator would change the committed sample.

Task 23 — UPDATE setup/requirements.txt and pyproject.toml:
   - Already covered in Task 10; sanity-check that pip install in CI
     resolves cleanly

Task 24 — REWRITE docs/configuration.md:
   - Rewrite section headings: ## daemon, ## base.converter, ## base.video,
     ## base.hdr, ## base.audio, ## base.subtitle, ## base.metadata,
     ## base.naming, ## base.analyzer, ## base.permissions, ## profiles,
     ## services.sonarr, ## services.radarr, ## services.plex
   - REMOVE [SABNZBD]/[Deluge]/[qBittorrent]/[uTorrent] sections (326-330)
     — replace with a note that downloader integration is shell-trigger-only
   - ADD new section ## daemon.routing — document longest-prefix match,
     bare-base fallback, service references, examples

Task 25 — REWRITE docs/daemon.md:
   - REPLACE Path-Based Configuration block (152-209) with a new
     "## Path Routing" section documenting daemon.routing
   - UPDATE Top-Level Keys table (184-197)
   - UPDATE env vars docs at 525 to reflect any changes (none expected)
   - VERIFY Cluster Configuration Reference (775+) reflects the new
     daemon-section keys

Task 26 — UPDATE GitHub wiki:
   - CD to /tmp/sma-wiki/
   - Mirror the configuration.md and daemon.md changes onto the
     corresponding wiki pages
   - git add -A && git commit -m "docs: restructure config to four-bucket
     layout" && git push origin HEAD:master

Task 27 — UPDATE CHANGELOG / release notes (release-please will pick up
        from commits, but the breaking-change marker must be in the
        commit body): use BREAKING CHANGE: in the trailer of the main
        loader commit so release-please recognises it

Task 28 — RUN full validation:
   - mise run dev:lint
   - mise run dev:format
   - mise run test
   - mise run dev:precommit
```

### Per task pseudocode (critical bits only)

```python
# Task 2 — ConfigLoader.load
def load(self, path: str) -> SmaConfig:
    if path.lower().endswith(".ini"):
        raise ConfigError(
            "autoProcess.ini is no longer supported. "
            "Convert to sma-ng.yml — see docs/configuration.md"
        )
    raw = yamlconfig.load(path)  # ruamel safe-load existing helper
    # Detect old shape: top-level converter/video/etc. without `base:`
    # CRITICAL: this is the single most user-visible failure mode
    if "base" not in raw and any(k in raw for k in ("converter", "video", "audio")):
        raise ConfigError(
            "Old flat-shape config detected. "
            "Wrap converter/video/audio/etc. under a top-level `base:` block. "
            "See docs/configuration.md#migration."
        )
    try:
        cfg = SmaConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"Config validation failed:\n{e}") from e
    self._warn_unknown_keys(cfg, path_prefix="")
    return cfg

def _warn_unknown_keys(self, model, path_prefix: str):
    extras = getattr(model, "__pydantic_extra__", None) or {}
    for k, v in extras.items():
        dotted = f"{path_prefix}.{k}" if path_prefix else k
        self.logger.warning("Unknown config key: %s", dotted)
    # Recurse into known fields that are themselves models
    for name, field in model.__class__.model_fields.items():
        val = getattr(model, name)
        if isinstance(val, BaseModel):
            self._warn_unknown_keys(val, f"{path_prefix}.{name}" if path_prefix else name)
        elif isinstance(val, dict):
            for k, v in val.items():
                if isinstance(v, BaseModel):
                    self._warn_unknown_keys(v, f"{path_prefix}.{name}.{k}")

# Task 2 — resolve_routing (longest-prefix match)
def resolve_routing(self, cfg: SmaConfig, file_path: str) -> RoutingResolution:
    # MIRROR resources/daemon/config.py:373 (sort) + 430-444 (walk)
    rules = sorted(cfg.daemon.routing, key=lambda r: len(r.match), reverse=True)
    normalised = self._normalise_path(file_path, cfg.daemon.path_rewrites)
    for rule in rules:
        if normalised.startswith(rule.match.rstrip("/*")):
            return RoutingResolution(
                profile=rule.profile,
                services=[(ref.split(".", 1)) for ref in rule.services],
                base_config=self.apply_profile(cfg, rule.profile),
            )
    # No match — bare base, no services
    return RoutingResolution(profile=None, services=[], base_config=cfg.base)

# Task 5 — _strip_secrets extension (config.py:40-47)
def _strip_secrets(data: dict) -> dict:
    sanitised = copy.deepcopy(data)
    daemon = sanitised.get("daemon", {})
    for key in SECRET_KEYS:
        daemon.pop(key, None)
    # NEW — walk services.<type>.<instance>.<secret_field>
    services = sanitised.get("services", {})
    for stype, instances in services.items():
        if not isinstance(instances, dict):
            continue
        for iname, ival in instances.items():
            if not isinstance(ival, dict):
                continue
            for secret in SERVICE_SECRET_FIELDS:  # ["apikey", "token", "password"]
                ival.pop(secret, None)
    return sanitised
```

### Integration Points

```yaml
DATABASE:
  - none — schema lives in YAML, not in Postgres
  - cluster-shared daemon config (db_url, scan_paths, etc.) already syncs
    via existing sma_cluster_config table; new fields ride along

CONFIG:
  - replace: setup/sma-ng.yml.sample (regenerated)
  - update: setup/requirements.txt (+pydantic)
  - update: pyproject.toml (+pydantic)

PYTHON:
  - new module: resources/config_schema.py
  - new module: resources/config_loader.py
  - rewritten: resources/readsettings.py
  - touched: resources/daemon/config.py, constants.py, handler.py
  - touched: manual.py (lines 349-356)

CI:
  - add job: config-sample-consistency (calls
    `mise run config:generate-sample --check`)

DOCS (per CLAUDE.md three-place rule, collapsed to two for config):
  - docs/configuration.md (full rewrite)
  - docs/daemon.md (Path-Based Configuration block)
  - /tmp/sma-wiki/Configuration.md (mirror)
  - /tmp/sma-wiki/Daemon.md (mirror)
  - resources/docs.html — auto-renders docs/*.md, no separate update

SECRETS:
  - update: resources/daemon/constants.py SECRET_KEYS
  - update: _strip_secrets to walk services.<type>.<instance>
```

## Validation Loop

### Level 1: Syntax & Style

```bash
source venv/bin/activate

# Lint and format (ruff is the project standard)
mise run dev:lint
mise run dev:format

# pre-commit (yaml/toml/ruff/whitespace gates)
mise run dev:precommit
```

### Level 2: Schema & Sample Consistency

```bash
source venv/bin/activate

# Schema unit tests
python -m pytest tests/test_config_schema.py -v

# Routing engine
python -m pytest tests/test_config_routing.py -v

# Sample round-trip (must produce byte-identical output)
python -m pytest tests/test_config_sample.py -v

# Sample regenerator dry-run (CI mode)
mise run config:generate-sample --check
```

### Level 3: End-to-End

```bash
source venv/bin/activate

# Full test suite
mise run test

# Coverage variant
mise run test:cov

# Daemon smoke (verifies the loader path is exercised)
mise run dev:test:daemon  # if exists, else: mise run test:daemon
```

### Level 4: Manual smoke

```bash
source venv/bin/activate

# Old-shape rejection
echo "converter: {}\nvideo: {}" > /tmp/old.yml
SMA_CONFIG=/tmp/old.yml python daemon.py 2>&1 | grep "Old flat-shape"

# INI rejection
touch /tmp/x.ini
SMA_CONFIG=/tmp/x.ini python daemon.py 2>&1 | grep "no longer supported"

# Unknown key warning
echo "base:\n  converter:\n    notakey: 1\nprofiles: {}\nservices: {}\ndaemon: {}" > /tmp/typo.yml
SMA_CONFIG=/tmp/typo.yml python daemon.py 2>&1 | grep "Unknown config key: base.converter.notakey"

# Manual conversion routing path
python manual.py -i "/media/tv/kids/foo.mkv" -oo  # expect sonarr.kids in resolved services
python manual.py --profile rq -i "/media/tv/kids/foo.mkv" -oo  # explicit profile override
```

## Final validation Checklist

- [ ] All tests pass: `source venv/bin/activate && mise run test`
- [ ] No lint errors: `source venv/bin/activate && mise run dev:lint`
- [ ] Pre-commit passes: `source venv/bin/activate && mise run dev:precommit`
- [ ] Sample is up to date: `mise run config:generate-sample --check`
- [ ] Old-shape config produces a clear error
- [ ] `.ini` config produces a clear error
- [ ] Unknown keys produce one WARN per dotted path
- [ ] Routing resolves longest-prefix correctly with bare-base fallback
- [ ] `--profile` flag overrides routing
- [ ] All `ReadSettings` consumers (manual.py, rename.py, daemon.py,
      update.py, plexmatch.py, mediaprocessor.py, plex.py, rename_util.py,
      docker_smoke_imports.py) work without changes
- [ ] Sonarr multi-instance routing example runs end-to-end against a
      mock URL
- [ ] Service secrets redacted in /configs admin endpoint
- [ ] docs/configuration.md, docs/daemon.md, GitHub wiki updated

---

## Anti-Patterns to Avoid

- ❌ Don't introduce PyYAML; the project uses ruamel.yaml
- ❌ Don't change the public `ReadSettings(configFile, logger, profile)`
  signature — too many call sites
- ❌ Don't use deep-merge for profile overlays; existing semantic is
  shallow-per-section (see `_apply_profile` 497-504)
- ❌ Don't write a migration tool — user explicitly opted out
- ❌ Don't keep INI parsing as a "just in case" fallback — full removal is
  the entire point of this work
- ❌ Don't hand-edit `setup/sma-ng.yml.sample` post-restructure; the
  generator is the source of truth
- ❌ Don't skip the routing→services cross-reference validator — it
  catches the most common typo (`sonarr.kid` vs `sonarr.kids`)
- ❌ Don't catch generic `Exception` in the loader — catch
  `pydantic.ValidationError` and `ConfigError` explicitly
- ❌ Don't forget to extend `SECRET_KEYS` for the new per-instance fields;
  the admin endpoint will leak secrets otherwise
- ❌ Don't bundle this into a single mega-commit; per CLAUDE.md commit
  rules, split by logical change (schema, loader, INI removal, sample
  generator + CI, tests, docs)

---

## Confidence Score

**9/10** — High confidence in one-pass implementation. The codebase already
contains every required pattern (longest-prefix routing in
`path_configs`, profile shallow-merge in `_apply_profile`,
secrets-redaction in `_strip_secrets`, ruamel.yaml round-trip in
`_write_node_id_to_yaml`). All open decisions are now settled (see
"Resolved Decisions" in the task breakdown):

- `migrateFromOld` is fully ripped (no warn-and-rename cycle)
- Per-path `default_args` dropped; only global `daemon.default_args` survives
- `.ini` detection by extension only

The remaining uncertainty is whether any consumer reads a
converter-default attribute that isn't in the explicit research §1.1
list — mitigated by a grep pass listed as part of Task 3 before merging
the loader rewrite.

The task list is conservative and ordered to allow incremental
validation: schema → loader → readsettings adapter → consumers → tests →
docs. Each layer has a validation gate before moving on.

## Task Breakdown

Detailed task breakdown with work packages, Given-When-Then acceptance
criteria, and critical-path analysis is at
[`docs/tasks/config-restructure.md`](../tasks/config-restructure.md).
