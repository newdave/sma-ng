# Feature Brainstorming Session: QSV Optimization + Python Quality + Docker FFmpeg Pipeline

**Date:** 2026-05-16
**Session Type:** Technical Design / Cross-Cutting Refactor Planning

> Builds on [`2026-05-10-qsv-transcoding-refactor.md`](2026-05-10-qsv-transcoding-refactor.md).
> That doc covers the QSV pipeline-shape refactor in depth (Options A/B/C, typed
> pipeline, failure taxonomy, capability matrix). This session widens the lens to
> the surrounding concerns the user named: **(1)** squeezing more out of Intel
> QSV than today's "make it not crash" baseline, **(2)** raising the Python code
> baseline of the transcoder modules, and **(3)** aligning the Docker image and
> runtime with current best practice for GPU-enabled FFmpeg pipelines.

## 1. Context & Problem Statement

### Problem Description

Three tangled problems share the same blast radius (`resources/mediaprocessor.py`,
`converter/`, `docker/`). Solving any one in isolation leaves value on the table:

- **QSV is functional but not optimised.** Today the pipeline picks QSV when
  configured, falls back when it crashes, and stops there. We do not:
  - keep frames on the GPU end-to-end (`hwupload=extra_hw_frames=…`, fully-GPU
    filter chains via `scale_qsv` / `vpp_qsv` / `overlay_qsv`);
  - tune encoder rate-control per source (`-look_ahead`, `-extbrc`,
    `-low_power`, `-async_depth`, `-bf`, `-preset veryslow` analogues);
  - reuse a single `qsv_device` across decode + filter + encode (we sometimes
    open multiple devices per job);
  - probe capability per host once (we relitigate every job).
- **Python quality has drifted under feature pressure.** `mediaprocessor.py` is
  3.4k lines, `avcodecs.py` 2.4k, `ffmpeg.py` ~0.9k. There is no strict typing
  gate, no module-level cohesion contract, and command construction is
  intermingled with policy decisions, I/O, and logging. Pyright runs but is
  permissive. Tests exist but rely on string-shape assertions against argv.
- **Docker layout works but is single-purpose.** Image is multi-arch and
  multi-stage (good), but it bundles VAAPI + QSV + NVENC + software runtimes
  into one tag — operators on NVENC-only hosts pay for Intel userspace, and
  vice versa. There is no separate "thin runtime" variant. The entrypoint
  does GPU GID reconciliation imperatively rather than relying on declarative
  device permissions.

These are coupled: better QSV utilisation requires a structured pipeline
(handled by the Phase-2 typed pipeline from the prior doc) which only pays off
once the Python modules around it are cohesive enough to land changes safely,
and the Docker image has to actually surface the runtime capabilities the
pipeline depends on (`vainfo`, `libvpl`, `iHD`, render-group access).

### Target Users

- **Primary Users:** SMA-NG operators on Intel iGPU / Arc deployments who want
  *measurable* throughput gains, not just a green log line.
- **Secondary Users:** Maintainers extending the transcoder — clearer module
  boundaries, faster test feedback, stronger type signal.
- **Operational Users:** Cluster operators who want a leaner image footprint
  and predictable cold-start behaviour on heterogeneous fleets.

### Success Criteria

- **Business Metrics:**
  - Median QSV job throughput (fps decoded × encoded) improves ≥ 25% on the
    reference `sma-master` workload (1080p H.264 → H.265 QSV) without quality
    regression at the same target bitrate.
  - Image size for the dominant `qsv` variant drops or stays flat while
    surfacing a separate `nvenc` variant that is ≥ 30% smaller.
- **User Metrics:**
  - Zero "duplicate flag" / "leftover hardware option" classes (carried over
    from prior brainstorm — structural guarantee, not patch).
  - `mise run test:lint` passes under strict pyright on
    `converter/` + `resources/mediaprocessor.py` + `resources/daemon/`.
- **Technical Metrics:**
  - Per-module coverage stays ≥ 70% on production modules ≥ 100 statements
    (CLAUDE.md gate). No new `# pragma: no cover`.
  - One typed pipeline object owns argv construction; HW backend choice is a
    strategy, not a branch (carries Option A from prior brainstorm forward).
  - End-to-end GPU pipeline: `hwaccel=qsv -hwaccel_output_format=qsv`,
    fully-QSV filter graph where possible, single shared `-qsv_device`.

### Constraints & Assumptions

- **Technical Constraints:**
  - Must remain backward-compatible with existing `sma-ng.yml` schemas.
  - Cannot block on a single mega-PR; everything ships as a sequence of
    independently-revertible PRs (release-please patch bumps).
  - FFmpeg invocation stays subprocess-based; no `python-ffmpeg`/PyAV.
  - Single-line daemon logs (see prior `2026-04-27-logging-refactor.md`).
  - Linux containers only for GPU paths; macOS dev uses VideoToolbox or
    software (already supported, kept untouched here).
- **Business Constraints:** Single maintainer. Schedule is "value per PR,"
  not "ship the whole thing in one sprint."
- **Regulatory/Compliance:** None.
- **Assumptions:**
  - The prior brainstorm's Phase 1 (failure taxonomy + policy enum) is the
    foundation that lets this work measure itself; Phase 2 (typed pipeline)
    overlaps with QSV-tuning Option A here.
  - Most QSV gains come from keeping frames on the GPU; CPU↔GPU copies are
    the dominant tax today.

---

## 2. Brainstormed Ideas & Options

### Track 1 — QSV Optimisation

#### Option Q-A: Full-GPU pipeline (`hwaccel_output_format=qsv` end-to-end)

- **Description:** Promote the current "decode on GPU, filter on CPU,
  re-upload to encode" pattern to "decode → filter → encode all in QSV
  surfaces." Use `-hwaccel qsv -hwaccel_output_format qsv` and require
  the filter graph to use `_qsv` variants (`scale_qsv`, `vpp_qsv`,
  `overlay_qsv`, `deinterlace_qsv`). Fall back to `hwdownload,…,hwupload`
  only when a needed filter has no QSV variant.
- **Pros:**
  - Removes per-frame `hwdownload`/`hwupload` copies — typically the
    biggest CPU/PCIe drag in a QSV pipeline.
  - Enables `-extra_hw_frames` to size surface pools correctly, which
    unblocks `-async_depth > 1`.
  - Aligns with FFmpeg 8.x best practice (the path libvpl is built around).
- **Cons:**
  - Some filters in our chain (e.g. subtitle burn-in via `subtitles=`)
    are CPU-only; we must detect and insert minimum-cost
    `hwdownload→…→hwupload` brackets around them.
  - More state to model (surface formats, frame contexts).
- **Effort:** M · **Risk:** Medium · **Depends on:** Typed pipeline (Phase 2 of prior brainstorm).

#### Option Q-B: Per-host capability probe (cache `vainfo` / `ffmpeg -hwaccels`)

- **Description:** At daemon startup probe `vainfo --json`,
  `ffmpeg -hwaccels`, `ffmpeg -encoders`, write a typed capability snapshot
  to `/config/cache/hw_capabilities.json`, and have the pipeline consult
  the snapshot before constructing argv. Re-probe when image version
  changes or `/dev/dri` signature changes.
- **Pros:**
  - Replaces best-effort guess-and-fallback with "I know this host can do AV1
    decode, I won't even try it on this generation."
  - Surfaces a single `gpu_status` field on `/health`.
  - Folds the AV1-unsafe-on-pre-Arc lore from `local.yml` into machine-checked data.
- **Cons:** Cache invalidation. New disk-state to manage.
- **Effort:** S–M · **Risk:** Low · **Depends on:** Failure taxonomy (Phase 1 of prior brainstorm).

#### Option Q-C: Encoder rate-control tuning matrix

- **Description:** Introduce per-profile QSV encoder presets that go beyond
  bitrate/CRF: `-look_ahead 1`, `-extbrc 1`, `-bf`, `-async_depth`,
  `-low_power` (VDENC), `-preset`, `-global_quality` for ICQ. Default
  matrix tuned for archival (`hq`), realtime (`rt`), and battery/iGPU
  (`lp`). Expose as `profiles.<name>.qsv-tuning: hq|rt|lp|custom`.
- **Pros:** Concrete operator-visible quality/throughput knob. Matches
  what tdarr/Jellyfin expose.
- **Cons:** Easy to mis-tune on older silicon; need validation matrix.
- **Effort:** S · **Risk:** Low · **Depends on:** Q-B for safety clamps.

#### Option Q-D: Share a single `qsv_device` across decode/filter/encode

- **Description:** Today multiple sub-components can each open
  `/dev/dri/renderD128`. Standardise on one `-init_hw_device qsv=qsv:<dev>`
  + `-filter_hw_device qsv` for the whole job; never let a sub-codec open
  its own device.
- **Pros:** Eliminates a known class of QSV init failures
  ("device already in use"). Required substrate for Q-A.
- **Cons:** Touches encoder + filter wiring in `converter/avcodecs.py`.
- **Effort:** S · **Risk:** Low.

### Track 2 — Python Quality

#### Option P-A: Split `mediaprocessor.py` into cohesive modules

- **Description:** 3.4k lines mixing probe, plan, build-argv, run, retry,
  rename, tag, postprocess. Carve into:
  - `resources/processor/probe.py` (ffprobe + analysis)
  - `resources/processor/plan.py` (stream selection + tagging plan)
  - `resources/processor/run.py` (subprocess execution + attempt ladder)
  - `resources/processor/postprocess.py` (rename, tag, qtfaststart, plex)
  - `resources/processor/__init__.py` re-exports `MediaProcessor` for
    backward compatibility.
- **Pros:** Each module ≤ ~700 lines, unit-testable in isolation. Pyright
  strictness becomes affordable per-module.
- **Cons:** One large mechanical PR (or several smaller strangler PRs).
  Risk of churn in test imports.
- **Effort:** M · **Risk:** Medium (mostly mechanical).

#### Option P-B: Strict typing gate on hot modules

- **Description:** Add `[tool.pyright] strict = ["converter", "resources/processor", "resources/daemon"]`
  and fix the resulting errors over time. Make new code in those paths
  fail CI if it regresses. Outside paths stay at current `basic`.
- **Pros:** Cheap to enable, immediate signal on the modules that matter.
  Catches a class of bugs (None coercion, dict-vs-attr access) that today
  only surface at runtime in QSV failures.
- **Cons:** Initial fix-up backlog. Risk of `# type: ignore` proliferation
  if not policed.
- **Effort:** S to enable, M to clear backlog · **Risk:** Low.

#### Option P-C: Replace argv-string tests with structured-pipeline tests

- **Description:** Today many tests assert on substrings of the rendered
  argv. They are brittle to flag order and miss semantic regressions.
  Switch to asserting on the typed `TranscodePipeline` (from prior
  brainstorm Phase 2) and only render argv at the boundary.
- **Pros:** Decouples test stability from FFmpeg syntactic churn.
  Catches "we forgot to set `-qsv_device` on the filter chain" by
  inspecting the object, not by hoping the string survived.
- **Cons:** One-time conversion cost; bridge the old-shape tests until
  the pipeline lands.
- **Effort:** M · **Risk:** Low · **Depends on:** Q-A / Phase 2 pipeline.

#### Option P-D: Lint/format/security baseline

- **Description:** Tighten ruff (`E,F,W,UP,B,SIM,RUF,LOG,RET,PTH,S,
  ASYNC,PERF,PLE,PLW`), add `bandit` for the daemon HTTP surface, run
  `vulture` periodically to surface dead code. Add `pre-commit` config.
- **Pros:** Catches log-secret leaks, subprocess shell injection,
  resource leaks (`PERF`), all relevant to a subprocess-heavy daemon.
- **Cons:** Initial fix-up. Some rules will be opinionated.
- **Effort:** S · **Risk:** Low.

### Track 3 — Docker / Runtime Pipeline

#### Option D-A: Image variants per accelerator (`qsv`, `nvenc`, `vaapi`, `cpu`)

- **Description:** Build four runtime tags from the same multi-stage
  Dockerfile, each shipping only the GPU userspace it actually uses. The
  `qsv` tag still ships `libvpl2`/`libmfx-gen1*`/iHD, but `nvenc` drops
  them entirely. A `cpu` tag drops VAAPI too.
- **Pros:** Operators pull what they need; ~30% size win on `nvenc`,
  bigger on `cpu`. Reduces blast radius of CVEs in vendor userspace.
- **Cons:** Four tags to publish per release. Need CI matrix change.
- **Effort:** M · **Risk:** Low · **Depends on:** Buildx matrix already in CI.

#### Option D-B: Declarative `/dev/dri` permissions (drop entrypoint GID reconciliation)

- **Description:** Replace the entrypoint's "stat /dev/dri, add ubuntu to
  whichever group owns it" logic with explicit
  `group_add: ["video", "render"]` (and `device_cgroup_rules`) in
  `docker-compose.yml`, plus documentation of `--group-add` for plain
  `docker run`. Keep the entrypoint as a fallback for compose-less
  deployments, gated by an env var.
- **Pros:** Container starts as the unprivileged user from PID 1.
  Removes the only reason the image runs anything as root. Faster
  cold-start. Easier security review.
- **Cons:** Documentation/UX cost — operators have to add one line to
  compose. We keep the fallback for one release to cushion the change.
- **Effort:** S · **Risk:** Low.

#### Option D-C: Healthcheck that actually validates GPU

- **Description:** Today's HEALTHCHECK only curls `/health`. Promote it
  to optionally exercise the configured GPU once per minute (cached) via
  a daemon `/health?probe=gpu` query that calls `vainfo` / runs a
  zero-byte `-t 0` ffmpeg job. Reuses the capability snapshot from Q-B.
- **Pros:** Catches "container is up, but iHD driver disappeared after
  host kernel upgrade" without an operator looking at logs.
- **Cons:** Probe cost. Must rate-limit. Must not flap the healthcheck
  on transient GPU contention.
- **Effort:** S · **Risk:** Low–Medium.

#### Option D-D: Pin and cache base + ffmpeg sources by digest

- **Description:** Pin `ubuntu:24.04` and the Intel PPA snapshot by
  digest; fetch FFmpeg by signed tarball checksum (verify against
  upstream's `MD5SUMS`/`SHA256SUMS`). Cache `make`/`pip` layers via
  buildx cache mounts. Add `--sbom=true --provenance=true` for SLSA
  level 1 attestation.
- **Pros:** Reproducible builds. Supply-chain trail. Faster CI on
  cache hit (FFmpeg compile is the long pole).
- **Cons:** Pins need periodic bumps. SBOM publishing is new infra.
- **Effort:** S–M · **Risk:** Low.

### Additional Ideas Considered

- **Replace subprocess with `ffmpeg-python` / PyAV** — rejected (see prior
  brainstorm). Cost vastly exceeds benefit for our use case.
- **Move daemon HTTP layer to FastAPI/Starlette** — out of scope. The
  current stdlib `http.server` is fine for the load this hits.
- **Single "fat" image with runtime gpu selection** — exists today; the
  argument here is to keep the fat one as `latest` and add slim variants.
- **GPU scheduling across cluster nodes** — defer; cluster mode already
  routes by tag, and per-job GPU pinning needs a separate design.

---

## 3. Decision Outcome

### Chosen Approach

**A staged plan that lands across three tracks in lockstep with the prior
brainstorm's phasing:**

- **Phase 1 (foundation, weeks 1–2)** — already chosen by the prior
  brainstorm: failure taxonomy + policy enum + `/health` metrics. Add
  here: **Q-B** (capability probe) and **D-B** (declarative device
  permissions) and **P-D** (ruff/bandit baseline). All small, additive,
  unlock measurement.

- **Phase 2 (structural, weeks 3–6)** — prior brainstorm's typed
  pipeline (Option A). On top of it land **Q-A** (full-GPU pipeline)
  and **Q-D** (shared device), because the typed pipeline is the right
  home for surface-format and device-handle state. Concurrently land
  **P-A** (module split) and **P-B** (strict typing on the new modules
  while they are small) — splitting before strict typing is much
  cheaper than after.

- **Phase 3 (productisation, weeks 7–9)** — **Q-C** (encoder tuning
  matrix), **P-C** (structured-pipeline tests), **D-A** (image
  variants), **D-C** (GPU healthcheck), **D-D** (digest pins + SBOM).
  These all consume Phase 2 outputs.

### Rationale

- **Q-A is the biggest single performance lever**, but it is only safe
  on top of the typed pipeline — the surface-format bookkeeping has no
  good home in today's flat preopts list. Sequencing it after Phase 2
  is what makes it cheap.
- **P-A before P-B.** Strict-typing a 3.4k-line file is a multi-week
  slog; strict-typing five 700-line modules right after they are
  carved out is a couple of days each.
- **D-B and D-D are independent of pipeline work** and pay back in
  security and cold-start time on day one — they live in Phase 1.
- **D-A waits for Phase 3** because the variant matrix is most valuable
  once the typed pipeline drives the runtime requirements (no point
  publishing a `cpu` tag while the pipeline still imports QSV codepaths).

### Trade-offs Accepted

- **What We're Gaining:** measurable QSV throughput, leaner images per
  use case, strict-typed hot path, structural elimination of the
  prior brainstorm's bug class.
- **What We're Sacrificing:** ~9 weeks of focused refactor work in a
  single-maintainer project; some test churn; one mechanical
  module-split PR with wide diff.
- **Future Considerations:** auto-population of the capability matrix
  from runtime probes (Phase 4 of prior brainstorm) becomes trivial
  once Q-B exists. Cross-backend fallback (QSV → VAAPI) is unlocked
  by Q-D + Phase 2.

---

## 4. Implementation Plan

### Phase 1 — Foundation (weeks 1–2)

**Core Features:**

- [ ] Failure taxonomy + `/health` metrics (carried from prior brainstorm).
- [ ] **Q-B**: `scripts/probe-hw.py` produces a typed JSON snapshot;
  `resources/daemon/server.py` reads it at startup and exposes
  `gpu_status` + capability summary on `/health`.
- [ ] **D-B**: `docker/docker-compose.yml` gains
  `group_add: ["video", "render"]`; entrypoint GID reconciliation
  becomes opt-in via `SMA_ENTRYPOINT_FIX_GIDS=1`.
- [ ] **P-D**: ruff config tightened; bandit added to
  `mise run test:lint`; pre-commit config in `.pre-commit-config.yaml`.

**Acceptance Criteria:**

- `curl localhost:8585/health` returns `gpu_status: ok|degraded|unreachable`
  and a non-empty `capabilities` object on a Q-A or QSV host.
- `docker compose up` on a vanilla Intel iGPU host transcodes a QSV job
  without the entrypoint running any root-level usermod commands.
- `mise run test:lint` rejects a PR that adds `subprocess.run(shell=True)`
  to `resources/daemon/`.

### Phase 2 — Structural (weeks 3–6)

**Core Features:**

- [ ] Typed `TranscodePipeline` (prior brainstorm Phase 2) merged.
- [ ] **Q-D**: single `-init_hw_device qsv` + `-filter_hw_device qsv`
  in all QSV pipelines; `converter/avcodecs.py` QSV codecs stop opening
  their own devices.
- [ ] **Q-A**: filter graph uses `_qsv` variants where available;
  CPU-only filters wrapped in minimum-cost `hwdownload`/`hwupload`
  brackets; `-extra_hw_frames` sized from capability snapshot.
- [ ] **P-A**: `resources/mediaprocessor.py` carved into
  `resources/processor/{probe,plan,run,postprocess}.py`; the top-level
  module re-exports `MediaProcessor` for compatibility.
- [ ] **P-B**: pyright `strict` enabled for `converter/`,
  `resources/processor/`, `resources/daemon/`; existing errors fixed
  or annotated.

**Acceptance Criteria:**

- On the reference 1080p H.264 → H.265 QSV workload, end-to-end frames
  spend < 5% of wall-time in `hwdownload`/`hwupload` (measured via
  `-stats_period` parsing in Q-B's metrics).
- `pyright --strict converter resources/processor resources/daemon`
  is clean.
- All existing tests pass without changes to argv-string assertions
  (shim layer in place).

### Phase 3 — Productisation (weeks 7–9)

**Core Features:**

- [ ] **Q-C**: `profiles.<name>.qsv-tuning: hq|rt|lp` schema field;
  defaults documented; `hq` enables `extbrc + look_ahead`; clamped by
  capability matrix.
- [ ] **P-C**: argv-string assertions in
  `tests/test_mediaprocessor.py`/`test_ffmpeg.py` rewritten against the
  typed pipeline.
- [ ] **D-A**: CI builds `qsv` / `nvenc` / `vaapi` / `cpu` variants from
  one Dockerfile via `--target runtime-<flavour>` ARGs.
- [ ] **D-C**: `HEALTHCHECK` optionally exercises the configured GPU once
  per minute (rate-limited cache).
- [ ] **D-D**: ubuntu base + Intel PPA pinned by digest; FFmpeg
  tarball checksum verified; buildx cache mounts and SBOM/provenance
  enabled.

**Acceptance Criteria:**

- `nvenc` image is ≥ 30% smaller than `qsv` image.
- `docker scout` / `trivy` against pinned digests yields a reproducible
  CVE report between two CI runs of the same SHA.
- `HEALTHCHECK` returns unhealthy within 90s of `iHD` driver being
  uninstalled on the host (manual test).

### Future Enhancements (Phase 4+)

- Auto-populate capability matrix from runtime probes (was Phase 4 of
  prior brainstorm).
- Cross-backend fallback (`qsv → vaapi`) on the same Intel host.
- Per-job GPU pinning in cluster mode.
- Replace `http.server` with a typed ASGI app when load justifies it
  (not now).

---

## 5. Action Items & Next Steps

### Immediate Actions (This Week)

- [ ] **Capture baseline QSV metrics on `sma-master`.** Run the reference
  workload (1080p H.264 → H.265 QSV, 30 jobs) and record wall-time,
  encoded fps, and `intel_gpu_top` utilisation.
  - **Dependencies:** None.
  - **Success Criteria:** Numbers committed to
    `docs/benchmarks/2026-05-baseline.md`; serves as the bar Phase 2
    must beat by ≥ 25%.

- [ ] **Land `scripts/probe-hw.py` + `/health` capability surfacing (Q-B).**
  - **Dependencies:** Capability JSON schema agreed (lives in
    `resources/processor/hw_capabilities.py` — co-located with the
    pipeline strategies that consume it in Phase 2).
  - **Success Criteria:** `curl /health` shows `capabilities` on QSV,
    NVENC, VAAPI, and software-only hosts.

- [ ] **Tighten ruff/bandit (P-D) and add `pre-commit`.**
  - **Dependencies:** None.
  - **Success Criteria:** `mise run test:lint` runs both; CI fails on
    new violations; existing violations triaged in a tracking issue.

### Short-term Actions (Next Sprint)

- [ ] Land D-B (declarative device permissions) with documented
  migration path.
- [ ] Open RFC issue for `TranscodePipeline` API surface (one combined
  RFC with the prior brainstorm's Phase 2; reviewers see the QSV
  optimisation hooks in the same doc).
- [ ] Spike: rewrite the convert path for the reference workload only,
  behind `base.converter.experimental-pipeline: true`. Compare argv
  byte-for-byte against today's pipeline plus new `_qsv` filter
  variants for the cases Q-A enables.

---

## 6. Risks & Dependencies

### Technical Risks

- **Risk:** Full-GPU pipeline (Q-A) breaks on filters that require CPU
  surfaces (e.g. burned-in `subtitles=`) when the operator forgets to
  install fonts.
  - **Impact:** Medium (job fails noisily).
  - **Probability:** Medium.
  - **Mitigation:** Detect CPU-only filter need at plan time; emit a
    structured warning; auto-bracket with minimum `hwdownload→hwupload`;
    log the cost so the operator can optimise.

- **Risk:** Module split (P-A) lands as one mechanical PR and conflicts
  with in-flight feature branches.
  - **Impact:** Medium.
  - **Probability:** Medium (single maintainer, but feature branches
    exist).
  - **Mitigation:** Land split in a quiet week; freeze
    `resources/mediaprocessor.py` for 48h before merge; keep the
    re-export shim for ≥ one minor release so external scripts
    importing `MediaProcessor` from the old path keep working.

- **Risk:** Image variants (D-A) drift in CI matrix.
  - **Impact:** Low (operators on the right variant unaffected).
  - **Probability:** Low–Medium.
  - **Mitigation:** Single Dockerfile with `ARG ACCEL=qsv|nvenc|vaapi|cpu`
    gating apt installs; one CI job per variant; daily nightly build
    to catch upstream drift.

- **Risk:** Strict typing (P-B) tempts `# type: ignore` proliferation.
  - **Impact:** Low.
  - **Probability:** Medium.
  - **Mitigation:** ruff rule `PGH003` bans bare ignores; CODEOWNERS
    review required for any `# type: ignore[...]` in the strict paths.

- **Risk:** Q-C tuning matrix is wrong on older GPUs (e.g. Coffee Lake
  rejects `extbrc`).
  - **Impact:** Medium (jobs fail).
  - **Probability:** Medium.
  - **Mitigation:** Clamp via capability matrix from Q-B; expose a
    `qsv-tuning: lp` safe default that disables advanced flags on
    pre-Tiger-Lake generations.

### Dependencies

- Prior brainstorm Phase 1 (failure taxonomy) is the substrate this
  doc's Phase 1 builds on.
- FFmpeg 8.x in image (already present).
- libvpl/oneVPL availability on amd64 (already handled in Dockerfile).
- No external/blocking dependencies.

---

## 7. Resources & References

### Technical Documentation

- [Intel oneVPL programming guide](https://intel.github.io/libvpl/) —
  surface format, async depth, lookahead semantics.
- [FFmpeg QSV HW acceleration](https://trac.ffmpeg.org/wiki/Hardware/QuickSync)
  — `hwaccel_output_format`, `init_hw_device`, filter graph rules.
- [FFmpeg HW Acceleration Intro](https://trac.ffmpeg.org/wiki/HWAccelIntro)
  — backend coexistence model that underpins Q-D and Q-A.
- [Docker BuildKit cache mounts](https://docs.docker.com/build/cache/backends/)
  — D-D's compile cache strategy.
- [SLSA Level 1 requirements](https://slsa.dev/spec/v1.0/requirements)
  — supply-chain attestation target for D-D.

### Codebase References

- `resources/mediaprocessor.py` — module to split per P-A; current host
  of the three `_strip_*` helpers retired by the typed pipeline.
- `converter/avcodecs.py` — QSV/NVENC/VAAPI codec classes; Q-D requires
  these to stop opening their own devices.
- `converter/ffmpeg.py` — subprocess wrapper; receives argv from the
  pipeline.
- `resources/readsettings.py:194-242` — `_apply_hwaccel_profile`;
  migrates to capability matrix in Phase 2.
- `docker/Dockerfile` — multi-stage build, target for D-A variants and
  D-D pinning.
- `docker/docker-entrypoint.sh` — D-B retires the imperative GID logic.
- `docs/brainstorming/2026-05-10-qsv-transcoding-refactor.md` — the
  doc this one extends.
- `docs/brainstorming/2026-04-27-logging-refactor.md` — single-line
  log invariant constraints on new metrics.

### External Research

- [Jellyfin Hardware Acceleration design notes](https://jellyfin.org/docs/general/administration/hardware-acceleration/)
  — model for full-GPU filter chains and tuning presets.
- [Tdarr workers and HW backends](https://docs.tdarr.io/) — operational
  reference for image-variant strategy.
- [FFmpeg Docker best practices (jrottenberg/ffmpeg)](https://github.com/jrottenberg/ffmpeg)
  — multi-variant Dockerfile pattern that D-A mirrors.

---

## 8. Session Notes & Insights

### Key Insights Discovered

- The three tracks (QSV optimisation, Python quality, Docker pipeline)
  look independent but share a single critical path: the typed
  pipeline. Without it, Q-A has nowhere to put surface-format state,
  P-C has no object to assert on, and Q-C has no central place to
  clamp tuning by capability. Sequencing all three through Phase 2
  saves duplicated work.
- D-B (declarative permissions) is the only piece that meaningfully
  reduces the image's privilege surface today; everything else is
  performance or maintainability. Worth pulling forward into Phase 1
  on its own merits.
- The current QSV pipeline's biggest *measurable* tax is per-frame
  CPU↔GPU copies, not encoder tuning. Q-A pays back faster than Q-C
  in throughput, even though Q-C is the more visible operator-facing
  knob.

### Questions Raised (For Future Investigation)

- Should the image-variant matrix include an `arc` flavour optimised
  for Intel Arc dGPUs (newer kernel, newer iHD)? Defer until enough
  operators run Arc to justify the CI cost.
- Is there value in a `--dry-run` mode for the typed pipeline that
  renders argv + capability decisions to stdout for support
  diagnostics? Cheap addition once Phase 2 lands; track as Phase 3
  nice-to-have.
- Do we want to publish a structured event stream (NDJSON over an SSE
  endpoint) of per-job pipeline decisions for external dashboards?
  Out of scope; revisit if Grafana integration becomes a real ask.

### Team Feedback

- Single-maintainer project. The phasing is set so that **any one
  phase is shippable and useful** even if subsequent phases are
  deferred — Phase 1 alone gives measurement + leaner permissions
  + linting; Phase 2 alone gives the refactor; Phase 3 alone gives
  the productisation polish.
