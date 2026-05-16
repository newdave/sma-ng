# QSV Pipeline Phase 1 — Foundation: capability probe, declarative GPU permissions, lint baseline, failure taxonomy

> **STATUS: COMPLETE — landed 2026-05-16**
> All tasks merged; see commits `73c3bcf..19c6c3a` (T1: 73c3bcf, T2: d24946a, T3: 7ee4b05, T4: 3f7ea6e, T5: 783207b, T6: 1b36698, T7: 307d6fd, T8: 19c6c3a). Failure taxonomy, fallback-policy enum, capability probe, /health surfacing, declarative /dev/dri perms, and lint baseline all shipped.

name: "QSV Pipeline Phase 1 — Foundation: capability probe, declarative GPU permissions, lint baseline, failure taxonomy"
description: |
  Phase 1 of the QSV / Python-quality / Docker refactor program. Lands four
  independently-shippable, additive changes that together establish the
  measurement, security, and quality substrate Phase 2 (typed pipeline) will
  build on:

  - **(Q-B) Capability probe + `/health` surfacing** — daemon-startup probe of
    `vainfo` / `ffmpeg -hwaccels` / `ffmpeg -encoders` produces a typed JSON
    snapshot consumed by the existing `_get_health` handler.
  - **(D-B) Declarative `/dev/dri` permissions** — `docker-compose.yml` Intel
    profiles gain `group_add: [video, render]`; the entrypoint's root-mode GID
    reconciliation becomes opt-in via `SMA_ENTRYPOINT_FIX_GIDS=1`.
  - **(P-D) Lint/security baseline** — ruff rules tightened (`B,SIM,RUF,LOG,
    RET,PTH,S,PERF,PLE,PLW,UP`), `bandit` added to `mise run test:lint`, and
    `pre-commit` config wired up.
  - **(Phase-1 carryover from 2026-05-10 brainstorm) Failure taxonomy +
    fallback-policy enum + per-tier metrics** — `FfmpegFailureClass` enum,
    `parse_ffmpeg_failure(stderr_tail)` parser, `fallback-policy:
    aggressive|sw_decode_only|hw_only` replacing the boolean
    `software-fallback`, and `_attempt_ladder` helper that records
    `(tier, FfmpegFailureClass, duration_ms)` per attempt. Counters surfaced
    additively on `/health`.

---

## Discovery Summary

### Initial Task Analysis

User invoked `bp:generate-prp` against Phase 1 of the
`2026-05-16-qsv-python-quality-docker-pipeline.md` brainstorm. The brainstorm
already enumerated the four chosen options and their phasing; this PRP turns
that into an implementation-ready spec. Failure taxonomy is pulled forward
from `2026-05-10-qsv-transcoding-refactor.md` Phase 1 because Q-B's `/health`
metrics consume the same data structures.

### User Clarifications Received

Session ran in autonomous mode. No clarifying questions asked. All scoping
decisions come from the two brainstorm documents.

### Missing Requirements Identified

- **Capability snapshot cache location.** Chosen: `/config/cache/hw_capabilities.json`
  (consistent with the `/config` volume mount). Invalidate when image version
  changes or `/dev/dri` signature changes.
- **Probe failure mode.** Chosen: fail open — if the probe itself errors, mark
  `gpu_status: unknown` and continue startup. Daemon must not block on probe.
- **Migration of `software-fallback: false`.** Chosen: deprecated alias mapping
  `false → hw_only`, `true → aggressive`. Emit a deprecation warning at config
  load. Remove in one minor release.
- **`bandit` scope.** Chosen: `resources/daemon/`, `triggers/`, and top-level
  entry scripts only. Library/converter code is too noisy for an initial pass.
- **pre-commit adoption.** Chosen: config committed but not enforced in CI yet;
  documented in `docs/development.md`. Adoption is opt-in for the maintainer.

## Goal

After this PRP lands, an operator on any SMA-NG host can:

1. `curl localhost:8585/health` and see `gpu_status: ok|degraded|unreachable|unknown`
   plus a `capabilities` object enumerating detected hwaccels, encoders, and
   render-node device paths.
2. `curl localhost:8585/health` and see `fallback` counters broken down by
   from-tier, to-tier, and failure class (`DEVICE_OPEN_FAILED`,
   `DECODER_INIT_FAILED`, `ENCODER_INIT_FAILED`, `FILTER_INIT_FAILED`,
   `RUNTIME_ERROR`, `OTHER`).
3. Configure `base.converter.fallback-policy: hw_only|sw_decode_only|aggressive`
   in `sma-ng.yml` and have it strictly enforced. Legacy `software-fallback:
   false|true` continues to work for one minor release with a deprecation log.
4. Run the official `intel` compose profile without the container ever running
   anything as root, because `group_add: [video, render]` grants render-node
   access declaratively. Operators on hosts with non-standard render GIDs can
   still opt into the legacy fix-up via `SMA_ENTRYPOINT_FIX_GIDS=1`.
5. Run `mise run test:lint` and have ruff (with the expanded rule set) +
   bandit (over `resources/daemon/`, `triggers/`, entry scripts) both run.
   Pre-existing violations either fixed or pinned in `pyproject.toml`.

## Why

- **Q-B unlocks measurement.** Today QSV "works or doesn't"; operators can't
  tell whether a quiet host is running QSV jobs or silently encoding on the
  CPU. Without this, Phase 2's throughput claims can't be validated.
- **D-B reduces container privilege surface from "runs as root briefly" to
  "never runs as root"** on the dominant deployment shape (compose intel
  profile). Easier to security-review. Faster cold-start.
- **P-D catches an entire class of bugs cheaply** — `bandit` would flag
  `subprocess.run(shell=True)` patterns that today only surface as
  exploitable bugs after release. Ruff's `LOG`, `S`, and `PERF` rules apply
  directly to a subprocess-heavy daemon.
- **Failure taxonomy + policy enum unblocks Phase 2.** The typed pipeline's
  `next_fallback()` consumes the same failure classes — building Phase 2
  against a wrong taxonomy is wasted work, so we ship the taxonomy first and
  validate it against production stderr captures.

## What

User-visible behaviour and technical requirements.

### Success Criteria

- [x] `GET /health` returns new top-level keys `gpu_status`, `capabilities`,
  and `fallback` without breaking existing consumers (keys are additive).
- [x] `scripts/probe-hw.py` writes `/config/cache/hw_capabilities.json` on
  daemon startup; takes < 2 seconds on a cold host.
- [x] `ConverterSettings.fallback_policy` enum field replaces the boolean
  `software_fallback` in `resources/config_schema.py`. Existing YAML with
  `software-fallback: false` loads with one deprecation warning and behaves
  identically to `fallback-policy: hw_only`.
- [x] `FfmpegFailureClass` enum + `parse_ffmpeg_failure(stderr_tail)` ship in
  `resources/processor/failures.py` (a new file — first inhabitant of the
  Phase 2 `processor/` package). 10+ stderr fixtures cover at least 4
  distinct failure classes.
- [x] The three-tier ladder in `mediaprocessor.py:3068-3105` is wrapped in an
  `_attempt_ladder` helper that emits one structured log line per job
  containing `attempts: [{tier, failure_class, duration_ms}, ...]`. Behaviour
  is unchanged when `fallback-policy: aggressive` (the default).
- [x] `docker/docker-compose.yml` `sma-intel` and `sma-intel-pg` profiles add
  `group_add: ["video", "render"]`. Entrypoint GID reconciliation runs only
  when `SMA_ENTRYPOINT_FIX_GIDS=1` is set.
- [x] `pyproject.toml [tool.ruff.lint] select` expanded to
  `["E", "F", "W", "I", "B", "SIM", "RUF", "LOG", "RET", "PTH", "S", "PERF",
  "PLE", "PLW", "UP"]`. Resulting violations either fixed or explicitly
  ignored with a rationale comment per ignore.
- [x] `bandit` listed under `[project.optional-dependencies].dev`; invoked
  from `.mise/tasks/test/lint` over `resources/daemon/`, `triggers/`,
  `daemon.py`, `manual.py`, `rename.py`.
- [x] `.pre-commit-config.yaml` committed with ruff + bandit + trailing-
  whitespace hooks; documented in `docs/development.md`.
- [x] `mise run test` passes. Coverage gate (90% global, 70% per-module ≥ 100
  statements) holds.

## All Needed Context

### Research Phase Summary

- **Codebase patterns found**: existing `/health` handler is purely additive
  JSON; existing `detect-gpu.sh` shells out to `vainfo` / `nvidia-smi`;
  existing `ConverterSettings` is pydantic-based with kebab-case YAML aliasing;
  existing ruff config is intentionally permissive for the legacy codec
  modules; existing fallback ladder is a try/except triple in MediaProcessor.
- **External research needed**: No — all libraries (`pydantic`, `bandit`,
  `pre-commit`, `ruff`) are well-established. Bandit ruleset has stable
  defaults appropriate for a subprocess daemon. Pre-commit config is standard.
- **Knowledge gaps**: None blocking. The failure-class taxonomy is grounded in
  the prior brainstorm's captured incidents (job 79 et al.).

### Documentation & References

```yaml
- file: docs/brainstorming/2026-05-16-qsv-python-quality-docker-pipeline.md
  why: Authoritative scope for this PRP. Section 4 Phase 1 lists the chosen
    options and their acceptance criteria.

- file: docs/brainstorming/2026-05-10-qsv-transcoding-refactor.md
  why: Source of the failure taxonomy + policy enum work being pulled forward.
    Section 4 Phase 1 lists the structural pieces.

- file: resources/daemon/handler.py
  lines: 140-175
  why: Pattern for adding fields to /health. Response is a plain dict; new
    top-level keys are additive and won't break consumers per docs/daemon.md.

- file: resources/daemon/server.py
  why: DaemonServer instantiation point — capability snapshot is loaded here
    once at startup and attached to the server object so handler can read it.

- file: resources/config_schema.py
  lines: 42-78
  why: ConverterSettings pattern for adding the fallback_policy enum field
    with backward-compatible alias for software_fallback.

- file: resources/mediaprocessor.py
  lines: 23-130, 3060-3115
  why: Existing _strip_hw_decoder / _strip_qsv_input_pipeline / _swap_qsv_codec
    helpers and three-tier ladder. _attempt_ladder wraps but does not replace
    them in Phase 1 (replacement is Phase 2 work).

- file: scripts/detect-gpu.sh
  why: Existing shell pattern for vainfo / nvidia-smi probing. probe-hw.py
    extends this with structured JSON output and ffmpeg -hwaccels parsing.

- file: docker/Dockerfile
  lines: 258-265, 296-323
  why: ubuntu user (UID 1000) is already a member of render. group_add in
    compose adds the host render/video GIDs at runtime.

- file: docker/docker-entrypoint.sh
  lines: 42-57
  why: Existing GID reconciliation block. Gate the whole block on
    SMA_ENTRYPOINT_FIX_GIDS=1; keep behaviour identical when set.

- file: docker/docker-compose.yml
  lines: 148-213
  why: sma-intel and sma-intel-pg profiles — add group_add stanza.

- file: pyproject.toml
  lines: 81-101
  why: Existing ruff config to extend. F401/F403/F405/F811 ignores must
    remain (legacy converter wildcard re-exports).

- file: .mise/tasks/test/lint
  why: Existing lint runner shim. Append bandit invocation.

- url: https://bandit.readthedocs.io/en/latest/start.html
  why: Bandit config + ignore-rationale pattern. We use `# nosec` with
    a justification comment, not bare disable.

- url: https://pre-commit.com/
  section: "Adding pre-commit plugins to your project"
  why: .pre-commit-config.yaml syntax + the official ruff/bandit hooks.

- url: https://docs.astral.sh/ruff/rules/
  section: "S (flake8-bandit), LOG (flake8-logging), PERF (Perflint)"
  why: Which rules in the expanded selection actually fire on our code.
    Critical: LOG002 (.exception in non-exception handler) is the most
    likely false-positive; allowlist if needed.

- url: https://trac.ffmpeg.org/wiki/Hardware/QuickSync
  section: "Failure modes"
  why: Maps stderr substrings to FfmpegFailureClass values.
```

### Current Codebase tree (abridged)

```bash
sma/
├── converter/                    # ffmpeg subprocess + codec definitions
│   ├── avcodecs.py               # 2.4k lines, HW codec classes
│   ├── ffmpeg.py                 # subprocess wrapper
│   └── formats.py
├── resources/
│   ├── mediaprocessor.py         # 3.4k lines, contains fallback ladder
│   ├── config_schema.py          # pydantic settings
│   ├── readsettings.py           # projection to legacy settings.* attrs
│   └── daemon/
│       ├── handler.py            # /health lives here
│       ├── server.py             # DaemonServer
│       ├── config.py
│       └── ...
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── docker-entrypoint.sh
├── scripts/
│   └── detect-gpu.sh             # pattern for probe-hw.py
├── .mise/tasks/test/lint         # mise run test:lint entrypoint
└── pyproject.toml                # ruff config + dev deps
```

### Desired Codebase tree (files added by this PRP)

```bash
sma/
├── resources/
│   └── processor/                # NEW — first inhabitant of Phase 2 package
│       ├── __init__.py
│       └── failures.py           # FfmpegFailureClass + parse_ffmpeg_failure
├── scripts/
│   └── probe-hw.py               # NEW — capability probe (Python, not shell)
├── tests/
│   ├── fixtures/
│   │   └── ffmpeg_stderr/        # NEW — captured stderr samples
│   │       ├── device_open_failed.txt
│   │       ├── decoder_init_failed.txt
│   │       ├── encoder_init_failed.txt
│   │       ├── filter_init_failed.txt
│   │       └── runtime_error.txt
│   ├── test_failures.py          # NEW — parse_ffmpeg_failure unit tests
│   ├── test_probe_hw.py          # NEW — probe-hw.py with mocked subprocess
│   └── test_fallback_policy.py   # NEW — policy enum migration tests
├── .pre-commit-config.yaml       # NEW
└── docs/
    └── hardware-acceleration.md  # NEW or extended — policy enum docs
```

### Known Gotchas of our codebase & Library Quirks

```python
# CRITICAL: pydantic field rename with alias
#   ConverterSettings uses kebab-case YAML keys. Adding fallback_policy means
#   the YAML key is `fallback-policy`. Keep software_fallback as a field with
#   a model_validator(mode="before") that migrates it.
#
# CRITICAL: handler.py uses 2-space indent (project convention)
#   See pyproject.toml [tool.ruff] indent-width = 2. Do not use 4-space.
#
# CRITICAL: line length is 200, not 88/120
#   pyproject.toml [tool.ruff] line-length = 200. Existing code uses long
#   lines; new code should follow that convention.
#
# CRITICAL: daemon log lines are single-line (CLAUDE.md + 2026-04-27 brainstorm)
#   The per-attempt summary line goes via `log.info(json.dumps({...}))`, not
#   a multi-line log block. Do not use print() in resources/daemon/.
#
# CRITICAL: no inline Python in shell scripts (CLAUDE.md)
#   probe-hw.py must be a real .py file; do not embed Python in
#   detect-gpu.sh or docker-entrypoint.sh.
#
# CRITICAL: subprocess invocations on a subprocess-heavy daemon
#   bandit will flag every subprocess.run() in the codebase. Use targeted
#   `# nosec B603` with a comment explaining input is trusted. Do NOT
#   disable bandit globally.
#
# CRITICAL: existing F-rule ignores must remain
#   pyproject.toml currently ignores F401/F403/F405/F811 for converter
#   wildcard re-exports. Adding rules must preserve these. RUF/B rules
#   may fire on the same modules — pin selectively, don't broaden ignores.
#
# CRITICAL: cluster API contract
#   /health is consumed by the admin UI's per-node version column and by
#   cluster nodes. Adding keys is safe; renaming or removing is not.
#   docs/daemon.md states consumers MUST ignore unknown keys.
#
# CRITICAL: ffmpeg stderr is unbounded
#   parse_ffmpeg_failure must take a tail slice (last ~8KB) not the full
#   stderr — ffmpeg can produce tens of MB of progress noise before the
#   actual error. Match on the tail only.

# GOTCHA: vainfo exit code is unreliable on permission errors
#   vainfo can return 0 while emitting "VA-API libva error: failed to
#   initialize". probe-hw.py must parse stderr, not just check returncode.

# GOTCHA: /dev/dri presence is not a capability signal
#   KVM hosts expose /dev/dri/card0 (bochs virtual) without any usable
#   acceleration. Always cross-check with vainfo output.

# GOTCHA: compose group_add on a host where the `render` group doesn't exist
#   docker compose will fail with "unable to find group render". The image's
#   numeric render GID (992) is the safest fallback; document this in the
#   compose comment block and accept "video" + numeric "992" as the values.
```

## Implementation Blueprint

### Data models and structure

```python
# resources/processor/failures.py

from enum import Enum
from dataclasses import dataclass


class FfmpegFailureClass(str, Enum):
  DEVICE_OPEN_FAILED = "device_open_failed"        # /dev/dri perms, missing libva, missing oneVPL runtime
  DECODER_INIT_FAILED = "decoder_init_failed"      # hw decoder doesn't actually work on this generation
  ENCODER_INIT_FAILED = "encoder_init_failed"      # encoder rejects pix_fmt / profile / level
  FILTER_INIT_FAILED = "filter_init_failed"        # filter graph can't be built (e.g. scale_qsv with bad format)
  RUNTIME_ERROR = "runtime_error"                  # ffmpeg started ok then failed mid-encode
  OTHER = "other"


@dataclass(frozen=True)
class AttemptRecord:
  tier: str                                  # "hw" | "sw_decode" | "full_sw"
  failure_class: FfmpegFailureClass | None   # None if attempt succeeded
  duration_ms: int


def parse_ffmpeg_failure(stderr_tail: str) -> FfmpegFailureClass:
  """Classify an ffmpeg stderr tail into a coarse failure bucket.

  Operates on the LAST ~8KB of stderr — ffmpeg's earlier progress output is
  noise. Returns OTHER for unrecognized patterns (deliberate fallthrough so
  drift in upstream ffmpeg messages is visible as a metric, not a crash).
  """
  ...


# resources/config_schema.py — additions to ConverterSettings

class FallbackPolicy(str, Enum):
  AGGRESSIVE = "aggressive"           # try hw, sw_decode, full_sw — current default
  SW_DECODE_ONLY = "sw_decode_only"   # try hw then sw_decode; never full software
  HW_ONLY = "hw_only"                 # hw only; surface failures immediately


class ConverterSettings(_Base):
  # ... existing fields ...
  fallback_policy: FallbackPolicy = FallbackPolicy.AGGRESSIVE

  # Deprecated — kept for one minor release. model_validator below maps it.
  software_fallback: bool | None = None

  @model_validator(mode="before")
  @classmethod
  def _migrate_software_fallback(cls, values):
    if isinstance(values, dict) and "software-fallback" in values and "fallback-policy" not in values:
      legacy = bool(values.pop("software-fallback"))
      values["fallback-policy"] = "aggressive" if legacy else "hw_only"
      # Deprecation warning emitted by readsettings on load (cannot import log here).
      values["_software_fallback_deprecated"] = True
    return values


# scripts/probe-hw.py output JSON schema

{
  "schema_version": 1,
  "probed_at": "2026-05-16T12:34:56Z",
  "host_signature": "sha256:...",      # hash of /dev/dri device list + image version
  "image_version": "2.4.0",
  "gpu_status": "ok",                  # ok | degraded | unreachable | unknown
  "selected_backend": "qsv",           # qsv | nvenc | vaapi | videotoolbox | software
  "capabilities": {
    "hwaccels": ["qsv", "vaapi"],
    "encoders": {"h264_qsv": true, "hevc_qsv": true, "av1_qsv": false},
    "decoders": {"h264_qsv": true, "hevc_qsv": true, "av1_qsv": false},
    "render_nodes": ["/dev/dri/renderD128"],
    "vainfo_driver": "iHD",
    "vainfo_version": "23.4.0",
    "ffmpeg_version": "8.1"
  },
  "errors": []                          # populated when gpu_status != ok
}
```

### List of tasks to be completed (in order)

```yaml
Task 1 — Failure taxonomy + parser (no behaviour change yet):
CREATE resources/processor/__init__.py:
  - Empty file (package marker).
CREATE resources/processor/failures.py:
  - FfmpegFailureClass Enum, AttemptRecord dataclass, parse_ffmpeg_failure fn.
  - Module is import-safe (no top-level side effects, no circular imports).
CREATE tests/fixtures/ffmpeg_stderr/{device_open_failed,decoder_init_failed,encoder_init_failed,filter_init_failed,runtime_error}.txt:
  - Real stderr captures. Use existing sidecar files in production logs,
    or synthesize from known job 79 / 109 / 113 / 114 incidents documented
    in 2026-05-10 brainstorm.
CREATE tests/test_failures.py:
  - parametrized test: each fixture -> expected FfmpegFailureClass.
  - test for tail-only matching (full stderr is truncated to 8KB).
  - test for OTHER fallthrough on unrecognized stderr.

Task 2 — Fallback-policy enum (schema + backward compat):
MODIFY resources/config_schema.py:
  - ADD FallbackPolicy enum (top of file, near other enums).
  - REPLACE `software_fallback: bool = False` with
    `fallback_policy: FallbackPolicy = FallbackPolicy.AGGRESSIVE` and
    `software_fallback: bool | None = None` (deprecated alias).
  - ADD @model_validator(mode="before") that maps `software-fallback` →
    `fallback-policy`.
  - KEEP the "When True, ..." comment but update it to describe the policy
    semantics.
MODIFY resources/readsettings.py:
  - Project `fallback_policy` onto legacy `settings.software_fallback`
    (True ↔ AGGRESSIVE, False ↔ HW_ONLY, SW_DECODE_ONLY ↔ True for now).
  - Emit a one-shot WARNING log when `_software_fallback_deprecated` is set.
MODIFY setup/sma-ng.yml.sample:
  - REGENERATE via `mise run config:sample` after schema change.
CREATE tests/test_fallback_policy.py:
  - test legacy `software-fallback: false` → HW_ONLY + deprecation warning.
  - test legacy `software-fallback: true` → AGGRESSIVE + deprecation warning.
  - test new `fallback-policy: sw_decode_only` round-trips.

Task 3 — _attempt_ladder helper (instrumentation, no semantic change):
MODIFY resources/mediaprocessor.py:
  - FIND the try/try/except block at lines 3068-3105 (the three-tier ladder).
  - EXTRACT into `_attempt_ladder(self, preopts, options, run_fn) -> list[AttemptRecord]`.
  - Each tier records its own AttemptRecord; on success record (tier, None,
    elapsed); on failure call parse_ffmpeg_failure(e.output[-8192:]) and
    record (tier, failure_class, elapsed).
  - Honour `self.settings.fallback_policy`:
      HW_ONLY: try hw only, re-raise on failure.
      SW_DECODE_ONLY: try hw, then sw_decode; never full_sw.
      AGGRESSIVE: try all three (current behaviour).
  - At end, log a single JSON line via self.log.info:
      `{"event": "ffmpeg.attempts", "attempts": [...], "result": "ok"|"failed"}`
  - PRESERVE the existing _strip_hw_decoder_from_preopts /
    _strip_qsv_input_pipeline_from_preopts / _swap_qsv_codec_to_sw helpers;
    they are still the per-tier transition functions in Phase 1 (typed
    pipeline replacement is Phase 2).
MODIFY tests/test_mediaprocessor.py:
  - UPDATE test_software_fallback_disabled_skips_retries to assert against
    fallback_policy=HW_ONLY (legacy `software-fallback: false`).
  - ADD test_fallback_policy_sw_decode_only_skips_full_software_tier.
  - ADD test_attempt_ladder_emits_structured_log_line.

Task 4 — Capability probe script:
CREATE scripts/probe-hw.py:
  - #!/usr/bin/env python3 — shebang only; no third-party deps.
  - Functions: probe_vainfo() -> dict, probe_ffmpeg_hwaccels(ffmpeg_path: str)
    -> list[str], probe_ffmpeg_encoders(ffmpeg_path: str) -> dict[str, bool],
    probe_render_nodes() -> list[str], compute_host_signature(...) -> str.
  - Top-level: parse args (--output, --ffmpeg, --ffprobe), build snapshot dict,
    write atomically (write to .tmp then os.replace).
  - Fail open: on any subprocess error, populate errors[] and set
    gpu_status accordingly; never raise.
  - subprocess.run with check=False, timeout=5, capture_output=True.
  - Mark `# nosec B603` on subprocess calls (input is hardcoded binary paths).
CREATE tests/test_probe_hw.py:
  - Mock subprocess.run for vainfo/ffmpeg/nvidia-smi outputs.
  - Test ok / degraded / unreachable / unknown branches.
  - Test atomic write (snapshot file is fully formed even if writer is killed).

Task 5 — Wire probe + capability snapshot into daemon:
MODIFY resources/daemon/server.py:
  - At server init (after config load): invoke scripts/probe-hw.py via
    subprocess.run, with output path /config/cache/hw_capabilities.json.
  - Read the resulting JSON, attach to self as self.hw_capabilities (dict).
  - On probe failure: self.hw_capabilities = {"gpu_status": "unknown", ...};
    log a WARNING; do NOT block startup.
  - Re-probe trigger: if /config/cache/hw_capabilities.json is older than the
    container's start time, re-probe. (Detects host kernel/driver changes.)
MODIFY resources/daemon/handler.py:
  - In _get_health (line ~153): merge `self.server.hw_capabilities` into the
    response under top-level keys `gpu_status` and `capabilities`.
  - Add a `fallback` key built from a new self.server.fallback_counters
    instance (see Task 6).
MODIFY resources/daemon/server.py:
  - ADD self.fallback_counters: a simple in-memory dict {(from_tier, to_tier,
    failure_class): count}. Initialised at startup, mutated by MediaProcessor.
  - Optionally persist counters to job_db; defer if scope creep.
MODIFY resources/mediaprocessor.py:
  - When _attempt_ladder records a non-success AttemptRecord, increment the
    matching counter via a callback the daemon passes in. CLI invocations
    (no daemon) pass a no-op callback.

Task 6 — Declarative GPU permissions (Docker):
MODIFY docker/docker-compose.yml:
  - In sma-intel and sma-intel-pg profiles, ADD:
      group_add:
        - "video"
        - "render"
        - "992"           # fallback numeric render GID baked into the image
  - REPLACE the "No group_add:" comment block with a comment documenting that
    `group_add` is the declarative path and the entrypoint fallback is opt-in.
MODIFY docker/docker-entrypoint.sh:
  - WRAP the existing /dev/dri GID reconciliation (lines 42-57) in:
      if [ "${SMA_ENTRYPOINT_FIX_GIDS:-0}" = "1" ] && [ "$(id -u)" = "0" ] && [ -d /dev/dri ]; then ... fi
  - PRESERVE chown reconciliation of /config and /logs (still needed when run
    as root via setpriv).
  - When SMA_ENTRYPOINT_FIX_GIDS is unset, the container still works for the
    compose path because group_add provides access; bare `docker run` users
    can set the env var.
MODIFY docs/configuration.md (or add docs/hardware-acceleration.md):
  - Document the migration: compose uses group_add; legacy bare-docker users
    set SMA_ENTRYPOINT_FIX_GIDS=1.

Task 7 — Ruff + bandit + pre-commit baseline:
MODIFY pyproject.toml [tool.ruff.lint]:
  - EXPAND select to:
      ["E", "F", "W", "I", "B", "SIM", "RUF", "LOG", "RET", "PTH", "S",
       "PERF", "PLE", "PLW", "UP"]
  - PRESERVE the existing F401/F403/F405/F811/F841/E501/E722/E402/E741/F601
    ignores.
  - ADD per-file ignores (top of [tool.ruff.lint.per-file-ignores]) for any
    legacy modules where new rules fire spuriously — pin minimally, document
    each ignore with a comment.
MODIFY pyproject.toml [project.optional-dependencies] dev:
  - ADD: "bandit[toml]>=1.7"
  - ADD: "pre-commit>=3.7"
MODIFY .mise/tasks/test/lint:
  - APPEND a bandit invocation:
      "$PY" -m bandit -q -r resources/daemon triggers daemon.py manual.py rename.py
  - Keep ruff as the first invocation; exit non-zero if either fails.
CREATE .pre-commit-config.yaml:
  - ruff (lint + format) hook
  - bandit hook scoped to daemon/triggers/entry scripts
  - trailing-whitespace, end-of-file-fixer, check-yaml
MODIFY docs/development.md (or create):
  - Document optional `pre-commit install` step.

Task 8 — Validation + docs:
MODIFY docs/hardware-acceleration.md (CREATE if missing):
  - Document fallback_policy enum, GPU /health surfacing, probe behaviour,
    and SMA_ENTRYPOINT_FIX_GIDS opt-in.
MODIFY resources/docs.html, /tmp/sma-wiki/Hardware-Acceleration.md:
  - Same content per CLAUDE.md three-place doc rule.
MODIFY docs/daemon.md:
  - Note new /health top-level keys (gpu_status, capabilities, fallback).
RUN: mise run config:sample (regenerate sma-ng.yml.sample).
RUN: mise run test (full suite; coverage ≥ 90% global, ≥ 70% per-module).
RUN: mise run test:lint (ruff + bandit clean or with explicit allowlist).
```

### Per-task pseudocode (CRITICAL details only)

```python
# Task 1 — parse_ffmpeg_failure

# PATTERN: match on the LAST 8KB only; ffmpeg leaks unbounded progress output
_TAIL = 8192
_PATTERNS: list[tuple[re.Pattern, FfmpegFailureClass]] = [
  (re.compile(r"VA-API .* failed to initialize|cannot open device .*/dev/dri", re.I), FfmpegFailureClass.DEVICE_OPEN_FAILED),
  (re.compile(r"Error parsing global options|Unknown decoder|Decoder .* not found|hwaccel.*not available", re.I), FfmpegFailureClass.DECODER_INIT_FAILED),
  (re.compile(r"Error initializing output stream|encoder .* failed|impossible to convert between", re.I), FfmpegFailureClass.ENCODER_INIT_FAILED),
  (re.compile(r"Error reinitializing filters|No such filter|Failed to configure (input|output) pad", re.I), FfmpegFailureClass.FILTER_INIT_FAILED),
  (re.compile(r"Conversion failed!|Error while decoding stream|Invalid data found", re.I), FfmpegFailureClass.RUNTIME_ERROR),
]
# CRITICAL: ordering matters — DEVICE_OPEN_FAILED is the most specific class
# and must be checked before DECODER_INIT_FAILED (which would otherwise eat
# "VA-API ... failed to initialize" as a generic decoder failure).

# Task 3 — _attempt_ladder (skeleton)
def _attempt_ladder(self, preopts, options, run_fn) -> list[AttemptRecord]:
    policy = self.settings.fallback_policy
    records: list[AttemptRecord] = []

    # Tier 1: hw
    t0 = time.monotonic()
    try:
        run_fn(preopts); records.append(AttemptRecord("hw", None, _ms(t0)))
        self._emit_attempt_log(records, "ok"); return records
    except FFMpegConvertError as e1:
        cls = parse_ffmpeg_failure((e1.output or "")[-_TAIL:])
        records.append(AttemptRecord("hw", cls, _ms(t0)))
        self._increment_fallback_counter("hw", cls)
        if policy == FallbackPolicy.HW_ONLY:
            self._emit_attempt_log(records, "failed"); raise

    # Tier 2: sw_decode (same as today, calls _strip_hw_decoder_from_preopts)
    retry_preopts = _strip_hw_decoder_from_preopts(preopts)
    if retry_preopts is None:
        self._emit_attempt_log(records, "failed")
        raise FFMpegConvertError("no hw decoder to strip", ...)
    t1 = time.monotonic()
    try:
        run_fn(retry_preopts); records.append(AttemptRecord("sw_decode", None, _ms(t1)))
        self._emit_attempt_log(records, "ok"); return records
    except FFMpegConvertError as e2:
        cls = parse_ffmpeg_failure((e2.output or "")[-_TAIL:])
        records.append(AttemptRecord("sw_decode", cls, _ms(t1)))
        self._increment_fallback_counter("sw_decode", cls)
        if policy == FallbackPolicy.SW_DECODE_ONLY:
            self._emit_attempt_log(records, "failed"); raise

    # Tier 3: full_sw (only when AGGRESSIVE)
    sw_preopts = _strip_qsv_input_pipeline_from_preopts(preopts) or []
    original_codec = _swap_qsv_codec_to_sw(options)
    if original_codec is None:
        self._emit_attempt_log(records, "failed")
        raise FFMpegConvertError("no qsv encoder to swap", ...)
    t2 = time.monotonic()
    try:
        run_fn(sw_preopts); records.append(AttemptRecord("full_sw", None, _ms(t2)))
        self._emit_attempt_log(records, "ok"); return records
    except FFMpegConvertError as e3:
        cls = parse_ffmpeg_failure((e3.output or "")[-_TAIL:])
        records.append(AttemptRecord("full_sw", cls, _ms(t2)))
        self._increment_fallback_counter("full_sw", cls)
        self._emit_attempt_log(records, "failed"); raise

# CRITICAL: every tier records exactly one AttemptRecord; the _emit_attempt_log
# call must run before raising so we don't lose telemetry on the final failure.

# Task 4 — probe-hw.py atomic write
def _write_snapshot(path: pathlib.Path, snapshot: dict) -> None:
    tmp = path.with_suffix(".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
    os.replace(tmp, path)  # atomic on POSIX

# Task 5 — daemon startup probe invocation
def _probe_hw_capabilities(self) -> dict:
    cache = pathlib.Path(self.config_dir) / "cache" / "hw_capabilities.json"
    try:
        result = subprocess.run(
            [sys.executable, "/app/scripts/probe-hw.py", "--output", str(cache),
             "--ffmpeg", self.config.base.converter.ffmpeg,
             "--ffprobe", self.config.base.converter.ffprobe],
            timeout=10, capture_output=True, check=False,
        )  # nosec B603 — hardcoded binary, no shell
        if cache.exists():
            return json.loads(cache.read_text())
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as e:
        self.log.warning("hw capability probe failed: %s", e)
    return {"gpu_status": "unknown", "capabilities": {}, "errors": ["probe_failed"]}

# CRITICAL: probe runs at server init, NOT per-request. /health reads from
# the cached snapshot. Re-probe trigger is image-version change or
# /dev/dri signature change.
```

### Integration Points

```yaml
CONFIG:
  - add to: resources/config_schema.py ConverterSettings
  - pattern: pydantic BaseModel with kebab-case YAML aliases
  - migration: model_validator(mode="before") maps software-fallback → fallback-policy

API/ROUTES:
  - add to: resources/daemon/handler.py _get_health (existing handler)
  - pattern: additive top-level keys (gpu_status, capabilities, fallback)
  - contract: existing consumers MUST ignore unknown keys per docs/daemon.md

DOCKER:
  - add to: docker/docker-compose.yml intel + intel-pg profiles
  - pattern: group_add: ["video", "render", "992"]
  - fallback: SMA_ENTRYPOINT_FIX_GIDS=1 retains legacy entrypoint behaviour

LINT:
  - add to: pyproject.toml [tool.ruff.lint] select + dev deps
  - add to: .mise/tasks/test/lint (bandit invocation appended)
  - new file: .pre-commit-config.yaml (optional, documented)

DOCS:
  - update: docs/hardware-acceleration.md (new file)
  - update: docs/daemon.md (/health schema additions)
  - update: /tmp/sma-wiki/Hardware-Acceleration.md (CLAUDE.md three-place rule)
  - update: resources/docs.html (CLAUDE.md three-place rule)
```

## Validation Loop

### Level 1: Syntax & Style

```bash
# Activate venv first (per user memory feedback_venv.md)
source venv/bin/activate

# Ruff with expanded ruleset
ruff check . --fix

# Bandit on daemon + triggers + entry scripts
bandit -q -r resources/daemon triggers daemon.py manual.py rename.py

# Pyright (currently permissive; should still be clean)
mise run dev:lint
```

### Level 2: Unit Tests

```bash
source venv/bin/activate

# New tests for this PRP
python -m pytest tests/test_failures.py tests/test_probe_hw.py tests/test_fallback_policy.py -v

# MediaProcessor tests (must still pass after _attempt_ladder refactor)
python -m pytest tests/test_mediaprocessor.py -v

# Daemon tests (must still pass after /health additions)
python -m pytest tests/test_daemon.py tests/test_handler.py tests/test_server.py -v
```

### Level 3: Integration Sanity

```bash
# Schema regen — fail loudly if the sample drifts
mise run config:sample
git diff --exit-code setup/sma-ng.yml.sample || echo "regenerate sample"

# Full suite + coverage gate
mise run test

# Docker compose validation
docker compose -f docker/docker-compose.yml --profile intel config
# Check group_add appears in the resolved config

# Live daemon smoke
python daemon.py --smoke-test
curl -s localhost:8585/health | jq '.gpu_status, .capabilities, .fallback'
```

## Final validation Checklist

- [x] All tests pass: `mise run test`
- [x] No linting errors: `mise run test:lint` (ruff + bandit clean)
- [x] No type errors: `mise run dev:lint`
- [x] `curl /health` returns `gpu_status` + `capabilities` + `fallback`
- [x] Legacy `software-fallback: false` still loads with deprecation warning
- [x] `docker compose --profile intel up` works on a vanilla Intel iGPU
  host without the entrypoint running anything as root
- [x] `SMA_ENTRYPOINT_FIX_GIDS=1 docker run …` still works for bare-docker
  users
- [x] `setup/sma-ng.yml.sample` regenerated and committed
- [x] `docs/hardware-acceleration.md`, `/tmp/sma-wiki/Hardware-Acceleration.md`,
  `resources/docs.html` synced (CLAUDE.md three-place rule)
- [x] Coverage ≥ 90% global, ≥ 70% per-module on modules ≥ 100 statements
- [x] No new `# pragma: no cover` or `# type: ignore` without rationale
- [x] Commit messages use conventional prefixes (`feat:`, `fix:`, `refactor:`)
- [x] One logical commit per task (per CLAUDE.md commit policy)

---

## Anti-Patterns to Avoid

- ❌ Don't replace the three `_strip_*` helpers in this PRP — that's Phase 2 work.
- ❌ Don't make probe-hw.py block daemon startup; fail open with
  `gpu_status: unknown` and log a warning.
- ❌ Don't broaden the existing F-rule ignores in pyproject.toml; pin new
  rule violations with targeted per-file ignores.
- ❌ Don't disable bandit globally to silence subprocess warnings — use
  `# nosec B603` with a justification comment per call site.
- ❌ Don't remove the entrypoint GID reconciliation — gate it behind
  `SMA_ENTRYPOINT_FIX_GIDS=1` for one release before deletion.
- ❌ Don't add multi-line log records — single-line per CLAUDE.md and the
  2026-04-27 logging-refactor brainstorm.
- ❌ Don't embed Python in `docker-entrypoint.sh` or `scripts/detect-gpu.sh`
  — probe-hw.py is the home for the Python logic.
- ❌ Don't write `print()` in `resources/daemon/` — use the daemon logger.
- ❌ Don't lower coverage thresholds to ship; fix the tests.
- ❌ Don't introduce circular imports between `resources/processor/failures.py`
  and `resources/mediaprocessor.py` — failures.py must be a leaf module.

---

## Related Documents

- Brainstorm (this PRP's scope): `docs/brainstorming/2026-05-16-qsv-python-quality-docker-pipeline.md`
- Brainstorm (failure taxonomy carryover): `docs/brainstorming/2026-05-10-qsv-transcoding-refactor.md`
- Task breakdown: `docs/tasks/qsv-pipeline-phase1-foundation.md`

---

**Confidence score: 8/10** for one-pass implementation success.

Score rationale:
- Strengths: every change is additive or feature-flagged; no schema break;
  validation commands are project-standard and known to work; codebase
  references are line-precise; failure taxonomy substrate is small and
  well-bounded; Docker changes are local to two compose profiles + one
  conditional in the entrypoint.
- Risks against a perfect 10: (1) ruff rule expansion will surface
  legacy violations whose volume is unknown ahead of time — Task 7 may
  need a second pass to pin per-file ignores; (2) real stderr capture
  quality determines parse_ffmpeg_failure accuracy — if the fixtures are
  thin, the OTHER bucket will dominate until production data lands;
  (3) compose `group_add` with a missing `render` group on some hosts
  may need the numeric "992" fallback to be the only entry — minor docs
  iteration likely.
