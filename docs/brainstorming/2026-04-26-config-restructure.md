# Feature Brainstorming Session: Config File Restructure

**Date:** 2026-04-26
**Session Type:** Technical Design

## 1. Context & Problem Statement

### Problem Description

`config/sma-ng.yml` currently has 18 top-level keys at the same nesting level
with no semantic grouping. Converter defaults (`converter`, `video`, `hdr`,
`audio`, `subtitle`, `naming`, `analyzer`, `permissions`, `metadata`), service
integrations (`sonarr`, `radarr`, `plex`, `sabnzbd`, `deluge`, `qbittorrent`,
`utorrent`), `daemon` runtime settings, and `profiles` overlays are all peers.
This makes the file hard to read, hard to operate (it is not obvious which keys
belong to which subsystem), and hard to extend — for example, supporting
multiple Sonarr instances has no clean home in the current shape.

### Target Users

- **Primary Users:** Operators editing `sma-ng.yml` by hand — including the
  project author running multi-library setups (e.g. separate Sonarr instances
  for kids and main TV libraries).
- **Secondary Users:** New users reading the sample config to understand how
  the daemon, converter, profiles, and services relate.

### Success Criteria

- **Operational Clarity:** A reader of `sma-ng.yml` can identify, at a glance,
  which top-level block owns any given setting.
- **Schema Enforcement:** Daemon validates config on load; unknown keys produce
  warnings, type/shape errors prevent startup with a precise message.
- **Sample Drift Prevention:** `setup/sma-ng.yml.sample` is generated from the
  same schema that validates user configs, so docs/sample/code cannot diverge.
- **No Migration Burden:** Existing users edit their config once to match the
  new shape; no migration tool is shipped.

### Constraints & Assumptions

- **Technical Constraints:**
  - Python 3.12+ is the floor; pydantic v2 is acceptable as a new dependency.
  - `autoProcess.ini` is deprecated and being removed entirely as part of this
    work — including INI parsing in `ReadSettings`.
  - Hard cutover is acceptable: no dual-read, no auto-migration, no major
    version bump.
- **Business Constraints:** Single-maintainer project; user base is small
  enough that a one-time manual edit is a non-issue.
- **Assumptions Made:**
  - Profile selection is a daemon-side concern in production but the CLI
    (`manual.py`) needs an explicit override for ad-hoc runs.
  - Service routing decisions are made per-rule, explicitly — there is no
    sensible automatic default for "which Sonarr instance owns this path."

## 2. Brainstormed Ideas & Options

### Option A: Minimal Port (rejected)

- **Description:** Keep current flat layout, just rename keys and add a
  `daemon.routing` block.
- **Pros:** Smallest diff; least churn for the loader.
- **Cons:** Does not address the readability/operational-clarity problem,
  which is the entire point of this work.
- **Effort Estimate:** XS
- **Risk Level:** Low
- **Dependencies:** None

### Option B: Grouped Restructure with Validation (selected)

- **Description:** Introduce four top-level groupings (`daemon`, `base`,
  `profiles`, `services`); profiles mirror the shape of `base` so overrides
  diff cleanly; services support named instances; routing lives under
  `daemon.routing` as longest-prefix-match rules; the entire shape is defined
  by a pydantic schema, and `sma-ng.yml.sample` is generated from that schema.
- **Key Features:**
  - Four-bucket layout: `daemon` / `base` / `profiles` / `services`
  - Named service instances (e.g. `services.sonarr.main`,
    `services.sonarr.kids`)
  - Path-based routing under `daemon.routing` with longest-prefix match,
    bare-`base` fallback, and explicit per-rule service selection
  - Pydantic v2 schema as single source of truth for validation,
    documentation, and sample generation
  - Hard removal of `autoProcess.ini` parsing; stub error directs users to
    `sma-ng.yml`
- **Pros:**
  - Directly addresses both stated motivations (human readability +
    operational clarity).
  - Schema-driven sample eliminates the chronic "sample drifted from code"
    problem.
  - Named service instances unblock multi-library setups without further
    schema work.
- **Cons:**
  - Larger code change in `ReadSettings` and the daemon loader.
  - Adds pydantic v2 as a runtime dependency.
- **Effort Estimate:** L
- **Risk Level:** Medium (touches every config consumer)
- **Dependencies:** pydantic v2

### Option C: Schema + Auto-Migration (rejected)

- **Description:** Same structure as Option B, plus a one-shot migration that
  rewrites the user's existing `sma-ng.yml` to the new shape.
- **Pros:** Zero manual edit for existing users.
- **Cons:** Migration code is throwaway; user base does not justify it; user
  explicitly opted out.
- **Effort Estimate:** XL
- **Risk Level:** Medium

## 3. Decision Outcome

### Chosen Approach

**Selected Solution:** Option B — Grouped Restructure with Pydantic Validation.

### Final Target Shape

```yaml
daemon:
  host: 0.0.0.0
  port: 8585
  workers: 4
  api_key: ...
  db_url: ...
  ffmpeg_dir: ...
  scan_paths: [...]
  path_rewrites: [...]
  routing:
    - match: "/media/tv/kids/**"
      profile: rq
      services: [sonarr.kids, plex.main]
    - match: "/media/tv/**"
      profile: rq
      services: [sonarr.main, plex.main]

base:
  converter: { ... }
  video: { ... }
  hdr: { ... }
  audio: { ... }
  subtitle: { ... }
  metadata: { ... }
  naming: { ... }
  analyzer: { ... }
  permissions: { ... }

profiles:
  rq:
    video: { ... }   # only overridden keys present; shape mirrors `base`
    audio: { ... }
  hq:
    video: { ... }

services:
  sonarr:
    main: { url: https://sonarr.newdave.com,      apikey: ... }
    kids: { url: https://sonarr-kids.newdave.com, apikey: ... }
  radarr:
    main: { ... }
  plex:
    main: { url: ..., token: ... }
  sabnzbd: { ... }
  deluge: { ... }
  qbittorrent: { ... }
  utorrent: { ... }
```

### Routing Semantics

- **Match algorithm:** longest-prefix wins. A file under
  `/media/tv/kids/foo.mkv` matches the `kids` rule even though the `tv` rule
  also matches.
- **No-match fallback:** the job runs against bare `base` settings, no profile
  overlay, no services notified.
- **`services:` omitted in a rule:** no services notified for that path
  (explicit-opt-in).
- **Service references:** `<type>.<instance>` (e.g. `sonarr.kids`). The
  instance name must exist under `services.<type>` or load fails.

### CLI Profile Selection (`manual.py`)

- Path-routing applies by default to whatever input file is passed.
- `--profile <name>` overrides routing when supplied.
- Bare `base` is used when no rule matches and no `--profile` is given.

### Validation Behaviour

- **Schema:** pydantic v2 models in a new `resources/config_schema.py` (or
  similar).
- **Unknown keys:** warn-and-continue (logged at WARNING level, including the
  full dotted path of the unknown key).
- **Type/shape errors:** hard fail at startup with the pydantic error message.
- **Sample generation:** `setup/sma-ng.yml.sample` is generated from the
  schema (likely via a `mise` task) and committed; CI verifies the committed
  sample matches what the schema would produce.

### Rationale

- **Operational clarity** is achieved by the four-bucket grouping plus schema
  errors that point at specific dotted paths.
- **Human readability** is achieved by profiles mirroring `base` shape, so a
  reader can mentally diff a profile against defaults in place.
- **Multi-instance services** fall out naturally from the named-instance
  shape — no special-casing.
- **Schema-as-source-of-truth** prevents the documentation/sample drift that
  has historically plagued the project.

### Trade-offs Accepted

- **Gaining:** clarity, validation, multi-instance support, drift prevention.
- **Sacrificing:** one-time manual edit for existing users; pydantic v2 as a
  new runtime dependency (~7 MB install).
- **Future Considerations:** if profile composition (stacking multiple
  profiles per path) becomes desirable later, the routing-rule shape is
  already a list and could grow a `profiles: [tv, 4k]` form without breaking
  the single-profile-per-rule contract.

## 4. Implementation Plan

### MVP Scope (Phase 1)

**Core Features for Initial Release:**

- [ ] Pydantic v2 schema models for `daemon`, `base`, `profiles`, `services`,
      and `daemon.routing`
- [ ] Loader rewrite: replace flat-key parsing with schema-validated load;
      warn-and-continue on unknown keys, hard-fail on type/shape errors
- [ ] Routing engine: longest-prefix match, bare-`base` fallback, named
      service-instance resolution
- [ ] `manual.py` `--profile` flag wired to the same routing/profile resolver
- [ ] Removal of `autoProcess.ini`: delete `setup/autoProcess.ini.sample`,
      delete `setup/autoProcess.ini.sample-lq`, rip out INI parsing in
      `resources/readsettings.py`, leave a stub that errors with a pointer to
      `sma-ng.yml` if `SMA_CONFIG` resolves to an `.ini` file
- [ ] Schema-driven sample generation: a `mise` task that emits
      `setup/sma-ng.yml.sample` from the schema; CI check that the committed
      sample matches generator output
- [ ] Update `setup/sma-ng.yml.sample` (via the generator) to the new shape

**Acceptance Criteria:**

- As an operator, I can read `sma-ng.yml` top-down and identify which
  subsystem owns any setting by which of the four blocks it lives in.
- As an operator with two Sonarr instances, I can route
  `/media/tv/kids/**` to one and `/media/tv/**` to the other without
  duplicate-key gymnastics.
- As a developer, a typo'd key surfaces in startup logs with its full dotted
  path.
- As a developer, attempting to run with an `.ini` config produces a clear
  error message and a pointer to the YAML migration notes.

**Definition of Done:**

- [ ] Schema implemented and unit-tested
- [ ] Loader and routing engine implemented and unit-tested
- [ ] `manual.py --profile` and path-routing path tested end-to-end
- [ ] INI parsing fully removed; stub error tested
- [ ] Sample generator implemented; committed sample matches generator output
- [ ] CI check enforces sample/schema consistency
- [ ] Documentation updated in all three places per CLAUDE.md:
      `docs/configuration.md`, `docs/daemon.md`, the GitHub wiki
      (`/tmp/sma-wiki/`), and `resources/docs.html`

### Future Enhancements (Phase 2+)

- **Profile stacking** (`profiles: [tv, 4k]` per routing rule) if
  single-profile-per-rule proves limiting in practice.
- **Per-instance feature flags** — e.g. only refresh Plex after movie jobs,
  not TV jobs.
- **Schema-derived JSON Schema** published alongside the sample to enable
  IDE autocomplete (VS Code YAML extension picks it up automatically).

## 5. Action Items & Next Steps

### Immediate Actions

- [ ] **Draft pydantic schema for the four top-level blocks**
  - **Dependencies:** None
  - **Success Criteria:** Schema models compile and round-trip the new sample
    file shape.
- [ ] **Inventory every consumer of `ReadSettings` / `autoProcess.ini`**
  - **Dependencies:** None
  - **Success Criteria:** A list of every call site that reads converter
    defaults, profiles, or service config, so the loader rewrite can target
    them all in one pass.

### Short-term Actions

- [ ] Implement loader rewrite against the new schema
- [ ] Implement routing engine + service-instance resolver
- [ ] Wire `manual.py --profile` flag
- [ ] Build sample generator + CI consistency check
- [ ] Remove INI parsing; add stub error
- [ ] Triple-place documentation update

## 6. Risks & Dependencies

### Technical Risks

- **Risk:** Hidden flat-key consumers outside `ReadSettings`
  - **Impact:** High (silent fallback to defaults if a consumer reads a key
    that has moved)
  - **Probability:** Medium
  - **Mitigation:** Inventory step before code changes; grep for every
    flat-key string literal in the new shape and verify it has a single
    schema-driven entry point.
- **Risk:** Pydantic v2 adds startup latency or packaging weight that hurts
  short-lived `manual.py` invocations
  - **Impact:** Low
  - **Probability:** Low
  - **Mitigation:** Measure cold-start before/after; lazy-import the schema
    in `manual.py` if needed.
- **Risk:** Sample generator and hand-written sample drift during the
  transition
  - **Impact:** Medium
  - **Probability:** Medium
  - **Mitigation:** Land the generator and the CI consistency check in the
    same commit as the schema.

### Operational Risks

- **Risk:** Existing users miss the cutover, daemon refuses to start
  - **Impact:** Medium
  - **Probability:** High at first release
  - **Mitigation:** Release notes call out the breaking change; the loader's
    error message for an old-shape config should name the new top-level
    blocks.

## 7. Resources & References

### Codebase References

- `resources/readsettings.py` — current INI loader and `DEFAULTS` dict; the
  primary site of the rewrite.
- `resources/daemon/config.py` — `PathConfigManager` already loads
  `sma-ng.yml`; routing engine extends this.
- `setup/sma-ng.yml.sample` — current target output of the future generator.
- `setup/autoProcess.ini.sample`, `setup/autoProcess.ini.sample-lq` — to be
  deleted.
- `docs/configuration.md`, `docs/daemon.md` — must be updated in lockstep.
- `resources/docs.html` — inline help served at `/docs`; per CLAUDE.md must
  also be updated.
- `docs/brainstorming/2026-04-24-yaml-config-migration.md` — prior session
  on the INI-to-YAML migration; this work is its natural successor.

### External References

- [pydantic v2 docs](https://docs.pydantic.dev/latest/) — model definitions,
  validators, and JSON-Schema export.

## 8. Session Notes & Insights

### Key Insights Discovered

- The original four-bucket framing (daemon / converter defaults / profiles /
  services) survived contact with reality; path-based routing is the only
  cross-cutting concern, and it lives cleanly under `daemon`.
- Multi-instance services (`sonarr.main` vs `sonarr.kids`) were not in the
  initial framing but emerged from a real operational need; the named-map
  shape absorbs it without further schema work.
- The motivation is clarity, not capability — which justifies a hard cutover
  rather than a migration tool.

### Questions Raised (For Future Investigation)

- Should `services.<type>.<instance>` support a `default: true` flag so a
  bare `sonarr` reference in a routing rule is meaningful? Currently rejected
  in favour of explicit `sonarr.<instance>` references; revisit if it proves
  noisy.
- How does the daemon detect an `.ini` config pointer — by file extension, by
  first-line sniff, or both? Decide during implementation.
- Where should the JSON-Schema export (Phase 2) be served from — the daemon
  itself, the repo, or both?
