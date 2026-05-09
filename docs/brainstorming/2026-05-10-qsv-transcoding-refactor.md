# Feature Brainstorming Session: QSV Transcoding Refactor

**Date:** 2026-05-10
**Session Type:** Technical Design / Refactor Planning

## 1. Context & Problem Statement

### Problem Description

The QSV (Intel Quick Sync) transcoding pipeline accumulated structural debt as
it was extended one fix at a time. The current implementation is correct often
enough to ship, but the *shape* of the code makes every new bug class easy to
introduce and hard to detect:

- The FFmpeg command line is built as a flat `list[str]` of preopts and then
  *surgically rewritten* on each fallback tier via three string-mutation
  helpers (`_strip_hw_decoder_from_preopts`,
  `_strip_qsv_input_pipeline_from_preopts`, `_swap_qsv_codec_to_sw`). Every
  new option (`-fix_sub_duration`, `-qsv_device`, `-extra_hw_frames`, …)
  becomes another grep target for these helpers, and missing one produces
  silent failures.
- Recent regressions trace directly to that shape:
  - Job 79 et al. — `Error parsing global options: Invalid argument` because
    `-qsv_device /dev/dri/renderD128` was retained on the third-tier "full
    software" fallback. Patched in 5a6145c, but the pattern will recur on the
    next added flag.
  - Duplicate `-fix_sub_duration -fix_sub_duration` because both
    `converter/__init__.py:134` (auto-add for any input with subs) and a
    user preopt entry independently appended it. Surfaced only via log
    inspection, not a test.
  - AV1 hardware decode footgun (Coffee/Comet/Tiger Lake) carried as a
    comment in `setup/local.yml` instead of a typed safety constraint.
- Operators have very little visibility into *which* tier ran, *why* it
  fell back, or *how often* QSV is actually being used vs. silently
  encoding on the CPU. The new `software_fallback: false` toggle is binary;
  there is no per-stage observability and no metric distinguishing
  "ffmpeg's QSV init failed" from "ffmpeg returned non-zero mid-encode."
- HW config knobs (`gpu`, `hwaccels`, `hwaccel-decoders`, `hwdevices`,
  `hwaccel-output-format`, `codec-parameters`) project onto flat `settings.*`
  attributes via `_apply_hwaccel_profile` and
  `_map_codecs_with_fallback`. Cross-cutting changes (e.g. "disable AV1
  decode on this GPU generation") require touching multiple sites and
  trusting the operator to clamp values via YAML.

This refactor targets both **correctness/maintainability** (a) and
**observability + configurability** (c). Performance is in scope only as a
side effect of removing redundant work the current shape causes (e.g. always
running tier-1 retries even when the operator knows they cannot succeed).

### Target Users

- **Primary Users:** SMA-NG operators running Intel iGPU / Arc deployments.
  They write `gpu: qsv`, expect QSV to work, and want clear errors when it
  does not.
- **Secondary Users:** Maintainers (code owners) who add/modify HW backends
  and need a place to express invariants once.
- **Operational Users:** Cluster operators who want metrics on QSV
  utilization and fallback frequency to capacity-plan and detect drift
  (e.g. a host quietly running every job in software).

### Success Criteria

- **Business Metrics:**
  - QSV initialization failures surface as actionable errors at the *first*
    failure, with a single named cause class (permission, missing runtime,
    unsupported codec/profile, hardware decoder rejection, encoder rejection).
  - Operators can answer "what fraction of jobs ran on QSV vs. fell back?"
    from the daemon API without grepping logs.
- **User Metrics:**
  - Zero regressions of the "duplicate flag" or "leftover hardware option
    on software path" classes — guaranteed structurally, not by
    grep-and-patch.
  - Configuration surface for HW acceleration is documented in a single
    section of `docs/hardware-acceleration.md` and stays in lockstep with
    a typed model.
- **Technical Metrics:**
  - One typed object describes a transcode pipeline; mode transitions
    (`hw → sw_decode → full_sw`) are functions on that object, not list
    surgery.
  - 100% test coverage on the mode-transition functions (not just the
    string helpers).
  - Per-tier fallback counters exposed via `/health` or a new `/metrics`
    endpoint.

### Constraints & Assumptions

- **Technical Constraints:**
  - Must remain backward-compatible with existing `sma-ng.yml` schemas
    (`base.video.gpu`, `base.converter.hwaccel-decoders`, etc.). No big-bang
    schema migration.
  - Must continue to support the four HW backends already in tree: `qsv`,
    `vaapi`, `nvenc`, `videotoolbox`, plus pure software.
  - FFmpeg is invoked via subprocess; we never link against libav directly.
    The pipeline object renders to argv.
  - Daemon log lines are single-line per
    `docs/brainstorming/2026-04-27-logging-refactor.md`. Any new
    structured-event records must respect that.
- **Business Constraints:**
  - No new release blockers. The refactor lands as a sequence of small
    PRs, each shippable on its own (release-please patch bumps).
  - Cannot break the daemon's existing job/log API contract — clusters
    are deployed today.
- **Regulatory/Compliance:** None.
- **Assumptions:**
  - Operators care more about predictable behavior than about saving
    every last frame to QSV — a refactor that occasionally leaves a job in
    software when QSV would have worked is acceptable as long as the
    reason is logged.
  - The recent `software-fallback: false` flag is a stopgap, not the
    long-term API. The refactor can either keep it or supersede it with a
    richer policy.

---

## 2. Brainstormed Ideas & Options

### Option A: Typed Pipeline Object with Render-and-Mutate Modes

- **Description:**
  Introduce a `TranscodePipeline` value object that owns the full ffmpeg
  argv shape (input device, hwaccel chain, decoder, encoder, filter graph,
  output options). Mode transitions are explicit methods that return a new
  `TranscodePipeline` (`.with_software_decode()`,
  `.with_full_software_pipeline()`). The pipeline knows how to render to
  argv exactly once, at the end. The convert loop calls
  `pipeline.run()` and on failure asks `pipeline.next_fallback()` for the
  next mode (or `None` to stop).

- **Key Features:**
  - Single source of truth for argv composition; no string-level surgery.
  - Each mode is a typed object; operations like "strip the QSV input
    pipeline" become "construct a software-decode variant from the same
    inputs," which is trivially correct.
  - Fallback chain lives on the pipeline (`pipeline.fallback_chain`), so
    the convert loop is dumb (`for mode in chain: try mode.run()`).
  - HW backend specifics (qsv vs. vaapi vs. nvenc) live in subclasses or
    strategies; common transitions live on the base.

- **Pros:**
  - Eliminates the duplicate-flag and leftover-flag bug classes by
    construction.
  - Each mode is independently testable (`render_argv()` is a pure
    function of pipeline state).
  - Clear extension point for new backends (AMF, V4L2, future Arc-only
    paths) — implement a new strategy, not a new branch in
    `_run_ffmpeg`.

- **Cons:**
  - Largest blast radius. Touches `MediaProcessor`, `converter/`,
    `ReadSettings._apply_hwaccel_profile`, and most QSV-related tests.
  - Requires a clear migration story to keep tests green during the
    refactor (a single PR that flips the world is risky).

- **Effort Estimate:** L
- **Risk Level:** Medium (mitigable with strangler pattern)
- **Dependencies:** Decision on whether to keep the existing
  `_strip_*` helpers as a transitional shim during migration.

### Option B: Declarative HW Capability Matrix + Thin Builder

- **Description:**
  Move the implicit knowledge currently spread across the codebase (which
  decoders are unsafe on which GPUs, which encoders need
  `-qsv_device`, which hwaccels imply which output formats) into a
  declarative `HW_CAPABILITIES` table. A thin builder consumes the active
  config + capability matrix and emits the argv. Fallback is a function
  that takes a failed-pipeline descriptor and returns a less-aggressive
  one *from the same matrix*.

- **Key Features:**
  - The `local.yml` "AV1 unsafe on pre-Arc Intel iGPU" comment becomes a
    capability-matrix entry: `{"qsv": {"av1_decode": {"min_gen":
    "arc"}}}`.
  - Operators get warnings (not silent overrides) when their YAML
    requests a capability the matrix doesn't permit.
  - Builder is stateless, easy to unit-test against a mocked matrix.

- **Pros:**
  - Captures operational lore that today only lives in code comments.
  - Enables `vainfo --json` / `intel_gpu_top`-driven autodetection later
    (the matrix is the contract; detection can populate it).
  - Smaller code change than Option A — the convert loop stays mostly
    the same.

- **Cons:**
  - Doesn't fully fix the list-mutation problem; still need *some*
    builder to render argv. Without Option A's pipeline object, the
    builder grows back into ad-hoc list manipulation.
  - Capability matrix becomes a new thing to maintain in sync with
    upstream FFmpeg/oneVPL releases.

- **Effort Estimate:** M
- **Risk Level:** Low (additive, mostly new code)
- **Dependencies:** Source of truth for the matrix (hand-curated vs.
  derived from `ffmpeg -encoders`/`-decoders` + `vainfo`).

### Option C: Observability + Policy Layer Only

- **Description:**
  Leave the pipeline construction alone for now. Wrap the existing
  three-tier logic in an `FfmpegRunResult` that records which tier
  ran, why each prior tier failed (parsed from stderr into a small
  enum: `DEVICE_OPEN_FAILED`, `DECODER_INIT_FAILED`,
  `ENCODER_INIT_FAILED`, `RUNTIME_ERROR`, `OTHER`), and how long each
  tier took. Replace the binary `software_fallback` flag with a
  policy enum: `aggressive | sw_decode_only | hw_only | adaptive`.
  Expose per-failure-class counters through `/health`.

- **Key Features:**
  - Adds the visibility the user mentioned in (c) without touching the
    fragile pipeline code.
  - `adaptive` policy disables tiers that have been failing identically
    on the same node for N jobs (per failure class), so a host with
    broken `/dev/dri` perms doesn't pay the tier-1 retry cost on every
    job.
  - Log-line stays single-line; structured fields go in a single
    `extra=` dict.

- **Pros:**
  - Cheapest, lowest risk, ships quickly.
  - Validates the failure taxonomy before encoding it into a typed
    pipeline.
  - Operationally useful immediately ("show me the fallback rate per
    node").

- **Cons:**
  - Doesn't address (a) at all. The string-surgery bug class stays
    open.
  - Risks calcifying the current shape if it makes the pain
    "manageable enough."

- **Effort Estimate:** S–M
- **Risk Level:** Low
- **Dependencies:** None.

### Additional Ideas Considered

- **Drop FFmpeg subprocess invocation, use python-ffmpeg or
  PyAV:** rejected. Significantly larger blast radius, no concrete win
  for the bugs in scope, locks us out of FFmpeg flag changes upstream.
- **Probe-then-build:** run a 1-second `ffprobe`/`ffmpeg -loglevel
  error -t 0` dry run to validate the QSV pipeline before committing
  to a full job. Worth keeping as a Phase 2 enhancement on top of A
  or B — see §4.
- **Per-profile fallback policy:** `profiles.hq.converter.fallback:
  aggressive` while `profiles.lq.converter.fallback: hw_only`. Falls
  out of Option C for free.
- **Health probe at daemon startup:** call `vainfo` /
  `intel_gpu_top -L` once at startup, fail closed (or warn loudly)
  if the requested `gpu` backend is unreachable. Reduces the noise
  Option C is built to surface.

---

## 3. Decision Outcome

### Chosen Approach

**Selected Solution:** **A + C combined, sequenced.** Option C lands first
(weeks 1–2) to get failure taxonomy and per-tier visibility into
production. Option A follows (weeks 3–6) and replaces the convert loop
with a typed pipeline that consumes the failure taxonomy C just defined.
Option B's capability matrix is folded into Option A as the data source
for pipeline construction (instead of a separate layer).

### Rationale

**Primary Factors in Decision:**

- **C-first de-risks A.** Building the typed pipeline against the wrong
  failure taxonomy would be a wasted refactor. Shipping C first surfaces
  what failures *actually* happen in production (not just what we
  remember from past bugs).
- **A solves the structural cause.** The user explicitly named
  correctness/maintainability as a primary driver. Observability without
  structural fix means the same bug class keeps appearing in metrics.
- **B alone is insufficient.** A capability matrix without a typed
  pipeline still routes through the same list-mutation helpers. Folding
  B into A (the matrix becomes the pipeline's input data) gets the
  benefit without a separate API surface.

### Trade-offs Accepted

- **What We're Gaining:**
  - Structural elimination of the string-surgery bug class.
  - Per-tier and per-failure-class metrics, queryable via the daemon
    API.
  - Clear extension point for future HW backends.
  - A typed home for the AV1-unsafe-on-pre-Arc class of operational
    lore.
- **What We're Sacrificing:**
  - Schedule: this is two refactors back-to-back, not one.
  - Some short-term churn in test fixtures (existing tests that mock
    `_strip_*` helpers will need to be rewritten against the pipeline
    object).
- **Future Considerations:**
  - Probe-then-build dry runs (Phase 3) become trivial once the
    pipeline object exists — render argv with `-t 0` and inspect
    return code.
  - Auto-population of the capability matrix from `vainfo --json` /
    `ffmpeg -hwaccels` is a Phase 4 nice-to-have; the matrix is
    hand-curated until then.

---

## 4. Implementation Plan

### MVP Scope (Phase 1 — Observability, weeks 1–2)

**Core Features for Initial Release:**

- [ ] `FfmpegFailureClass` enum
  (`DEVICE_OPEN_FAILED`, `DECODER_INIT_FAILED`, `ENCODER_INIT_FAILED`,
  `FILTER_INIT_FAILED`, `RUNTIME_ERROR`, `OTHER`).
- [ ] `parse_ffmpeg_failure(stderr_tail) -> FfmpegFailureClass`,
  unit-tested against a fixture set of real stderr captures
  (collect 10–20 from current production logs).
- [ ] Replace the inline `try/except FFMpegConvertError` ladder in
  `MediaProcessor.convert()` with a small `_attempt_ladder` helper
  that records `(tier, FfmpegFailureClass, duration_ms)` for every
  attempt and emits a single structured log line at the end.
- [ ] Per-config and per-node counters (`qsv_attempts_total`,
  `qsv_fallback_total{from,to,reason}`) surfaced through `/health`
  (existing endpoint, additive fields) — no new endpoint needed.
- [ ] Per-profile policy: replace the boolean
  `base.converter.software-fallback` with
  `base.converter.fallback-policy: aggressive | sw_decode_only |
  hw_only`, defaulting to `aggressive` for backward compat.
  Maintain `software-fallback: false` as a deprecated alias mapping
  to `hw_only` for one minor release.

**Acceptance Criteria:**

- As an operator, I can curl `/health` and see for each node how many
  jobs in the last hour fell back to software, broken down by reason.
- As a maintainer, I can write `assert
  parse_ffmpeg_failure(captured_stderr) == DEVICE_OPEN_FAILED` against
  the exact stderr that motivated commit 5a6145c.
- The existing
  `test_software_fallback_disabled_skips_retries` passes against the
  new policy enum.

**Definition of Done:**

- [ ] Feature implemented and tested
- [ ] Code reviewed and merged
- [ ] `docs/hardware-acceleration.md` updated with policy enum docs
- [ ] Wiki + `resources/docs.html` synced (per CLAUDE.md three-place rule)
- [ ] One full job cycle on `sma-master` shows the new metrics in
  `/health`
- [ ] Coverage for `parse_ffmpeg_failure` ≥ 95% line coverage

### Future Enhancements (Phase 2 — Typed Pipeline, weeks 3–6)

**Features for Later Iterations:**

- [ ] `TranscodePipeline` value object in `converter/pipeline.py`
  with `render_argv()`, `next_fallback()`, and immutable mode-transition
  methods.
- [ ] HW-backend strategies (`QsvStrategy`, `VaapiStrategy`,
  `NvencStrategy`, `VideoToolboxStrategy`, `SoftwareStrategy`)
  selecting the right preopts/postopts/codec params.
- [ ] Capability matrix
  (`converter/hw_capabilities.py`) consumed by strategies; AV1-unsafe-
  on-pre-Arc moves from local.yml comment to typed entry.
- [ ] Strangler-pattern migration: ship the new
  pipeline behind a feature flag (`base.converter.experimental-pipeline:
  true`), run it side-by-side via `--dry-run` for one release, then
  promote to default and delete `_strip_hw_decoder_from_preopts` /
  `_strip_qsv_input_pipeline_from_preopts` / `_swap_qsv_codec_to_sw`.

**Nice-to-Have Improvements (Phase 3+):**

- [ ] `pipeline.dry_run()` — render argv with `-t 0` to validate
  before committing the full job. Catches QSV init failures in <1s.
- [ ] Daemon startup probe: `vainfo` / `nvidia-smi` once at startup,
  surface result in `/health` as `gpu_status: ok | degraded |
  unreachable`.
- [ ] Auto-populate capability matrix from `ffmpeg -hwaccels`,
  `ffmpeg -encoders`, `vainfo --json`, falling back to the
  hand-curated entries.
- [ ] Per-job pipeline introspection: `GET /jobs/<id>/pipeline`
  returns the rendered argv and the chosen mode for support
  diagnostics.

---

## 5. Action Items & Next Steps

### Immediate Actions (This Week)

- [ ] **Collect 10–20 real ffmpeg stderr captures** from the
  `sma-master` log (job 79, 109, 113, 114 et al.) into
  `tests/fixtures/ffmpeg_stderr/*.txt`.
  - **Dependencies:** Production access to log files (already
    available via the daemon `/logs` API).
  - **Success Criteria:** Captures cover at least 4 distinct failure
    classes and live as test fixtures.

- [ ] **Draft `FfmpegFailureClass` enum + parser stub.**
  - **Dependencies:** Stderr captures from the previous task.
  - **Success Criteria:** Stub passes a smoke test against one
    captured stderr per failure class; PR opened with `feat(qsv):` prefix.

- [ ] **Confirm `/health` schema additivity** with cluster operators.
  - **Dependencies:** None — `/health` already returns a JSON object;
    new top-level keys are non-breaking.
  - **Success Criteria:** Documented in
    `docs/daemon.md` that consumers MUST ignore unknown keys.

### Short-term Actions (Next Sprint)

- [ ] Land Phase 1 (`fallback-policy` enum + per-tier metrics +
  `parse_ffmpeg_failure`).
- [ ] Open RFC issue for Phase 2 pipeline shape; collect feedback
  before implementing.
- [ ] Spike: prototype `TranscodePipeline.render_argv()` against the
  current QSV happy path. Validate that the rendered argv is byte-for-byte
  equivalent to today's command on three representative jobs (mkv→mp4
  HDR, mp4 force-convert, h264 1080p remux).

---

## 6. Risks & Dependencies

### Technical Risks

- **Risk:** `parse_ffmpeg_failure` undercounts a failure class because
  ffmpeg's stderr changes between versions.
  - **Impact:** Medium (metrics drift, but no correctness regression).
  - **Probability:** Medium.
  - **Mitigation Strategy:** Pin a default `OTHER` bucket; add a
    "saw an unclassified failure" warning log so operators notice
    drift. Capture stderr in the failure record so a future parser
    update can reclassify historic data.

- **Risk:** Phase 2 strangler migration breaks an in-flight cluster
  deployment because the experimental pipeline produces a slightly
  different argv.
  - **Impact:** High (jobs fail on production node).
  - **Probability:** Low–Medium.
  - **Mitigation Strategy:** Mandatory side-by-side dry-run period.
    Pipeline self-test at startup compares its rendered argv against
    the legacy builder for a synthetic input; any divergence aborts
    daemon startup with a clear error.

- **Risk:** Capability matrix becomes a maintenance tax.
  - **Impact:** Low.
  - **Probability:** Medium.
  - **Mitigation Strategy:** Keep it small and additive. Default
    behavior is permissive; matrix entries only *clamp* what the
    operator's YAML allows. Auto-population is a Phase 4 deferred
    item.

- **Risk:** Removing `software-fallback` boolean breaks
  the user's already-deployed `setup/local.yml`.
  - **Impact:** Medium.
  - **Probability:** Certain (if not handled).
  - **Mitigation Strategy:** Keep the boolean as a deprecated alias
    that maps to `hw_only` (for `false`) or `aggressive` (for `true`)
    for one minor release. Emit a deprecation warning at config-load
    time.

### Dependencies

- Production stderr captures (own — pull from `sma-master` daemon
  logs).
- Code-owner review for `MediaProcessor.convert()` changes.
- No external/blocking dependencies.

---

## 7. Resources & References

### Technical Documentation

- [Intel oneVPL programming guide](https://intel.github.io/libvpl/)
  — context for which encoder/decoder pairs are safe per GPU
  generation. Feeds the capability matrix.
- [FFmpeg HWAccel Intro](https://trac.ffmpeg.org/wiki/HWAccelIntro)
  — canonical mapping between `-hwaccel` flags and runtime APIs.
- [FFmpeg QSV docs](https://trac.ffmpeg.org/wiki/Hardware/QuickSync)
  — pre-opt order requirements; confirms that `-qsv_device` is a
  global option that must precede `-i`.

### Codebase References

- `resources/mediaprocessor.py:23-130` — current
  `_strip_hw_decoder_from_preopts`,
  `_strip_qsv_input_pipeline_from_preopts`, `_swap_qsv_codec_to_sw`.
  These functions disappear in Phase 2.
- `resources/mediaprocessor.py:3020-3056` — current three-tier
  try/except ladder. Replaced in Phase 1 by `_attempt_ladder` helper,
  removed entirely in Phase 2 in favor of pipeline object's
  `next_fallback()`.
- `resources/readsettings.py:194-242` — `_apply_hwaccel_profile` and
  `_apply_hwaccel_codec_map`. Phase 2 migrates these into the
  `HwStrategy` selection step.
- `setup/local.yml:127-136` — operational lore comment about
  AV1-unsafe-on-pre-Arc that Phase 2 encodes as a typed capability
  entry.
- `tests/test_mediaprocessor.py::test_software_fallback_disabled_skips_retries`
  — current contract test. Must remain green through both phases.
- `docs/brainstorming/2026-04-27-logging-refactor.md` — single-line
  log invariant; new structured fields go in `extra=`, not multi-line
  output.

### External Research

- [Jellyfin's Hardware Acceleration design notes](https://jellyfin.org/docs/general/administration/hardware-acceleration/)
  — useful reference for how a downstream project models QSV/VAAPI/
  NVENC backends as strategies.
- [tdarr's transcoding pipeline](https://docs.tdarr.io/) — similar
  fallback-tier pattern; their public stance on observability.

---

## 8. Session Notes & Insights

### Key Insights Discovered

- The deployed-image-lag we hit while debugging job 79
  ("`-qsv_device` still on the SW retry") is a *symptom* of the same
  root cause this refactor targets: every fallback tier is built by
  mutating the same flat list, so any new flag automatically inherits
  the bug class until someone remembers to teach the strippers about
  it. Phase 2 makes that bug class structurally impossible.
- The `software-fallback: false` flag we shipped two commits ago is
  exactly Option C's policy enum at N=1. Phase 1 generalizes it
  rather than rewriting it.
- Operator-side lore (AV1 unsafe on pre-Arc; PGS subs need
  `-fix_sub_duration`; iHD must be exported before vainfo runs) lives
  in three places today — `local.yml` comments, `docker-entrypoint.sh`
  exports, and codepath comments. The capability matrix is the
  single canonical home.

### Questions Raised (For Future Investigation)

- Should the capability matrix be auto-populated from runtime probes
  (`vainfo --json`, `ffmpeg -hwaccels`), hand-curated, or both? Phase
  4 question; Phase 2 hard-codes the matrix.
- Do we want a per-job `priority` knob that influences fallback
  policy ("HQ profile, never fall back to software; LQ profile, fine
  to fall back")? Falls out of Phase 1's per-profile policy support.
- Is there any case where we'd want a pipeline to fall back *to* a
  different HW backend rather than to software? (e.g. QSV → VAAPI on
  the same Intel iGPU, in case oneVPL is broken but mesa-va is fine.)
  Probably yes, but defer to Phase 3.

### Team Feedback

- Single-developer project; no team feedback yet. The two-phase
  sequencing is partly to give the maintainer a natural
  break-point to revisit Phase 2's design with a week of Phase 1
  metrics in hand.
