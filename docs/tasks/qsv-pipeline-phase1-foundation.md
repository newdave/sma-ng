# Task Breakdown — QSV Pipeline Phase 1: Foundation

> **STATUS: COMPLETE — landed 2026-05-16**
> All tasks merged; see commits `73c3bcf..19c6c3a` (T1: 73c3bcf, T2: d24946a, T3: 7ee4b05, T4: 3f7ea6e, T5: 783207b, T6: 1b36698, T7: 307d6fd, T8: 19c6c3a).

Companion to [docs/prps/qsv-pipeline-phase1-foundation.md](../prps/qsv-pipeline-phase1-foundation.md).

Eight commit-sized tasks delivering: failure taxonomy + fallback-policy enum
(carried over from `2026-05-10-qsv-transcoding-refactor.md`), capability
probe + `/health` surfacing (Q-B), declarative `/dev/dri` permissions (D-B),
and lint baseline (P-D).

## Conventions

- All Python commands run from an activated venv (`source venv/bin/activate`).
- One logical commit per task per `CLAUDE.md` commit policy.
- After each commit: `git pull --rebase && git push`.
- No `Co-Authored-By` / AI attribution.
- Conventional commit prefixes: `feat:`, `refactor:`, `chore:`, `docs:`.

## Critical path

```text
T1 (failures.py + fixtures + tests)
    │
    ├── T2 (fallback-policy enum + schema migration)
    │       │
    │       └── T3 (_attempt_ladder helper in MediaProcessor)
    │
    ├── T4 (probe-hw.py + unit tests)
    │       │
    │       └── T5 (daemon /health surfacing + counters wiring)
    │
    ├── T6 (declarative GPU permissions: compose + entrypoint gating)
    │
    └── T7 (ruff/bandit/pre-commit baseline)
                │
                └── T8 (docs sync + sample regen + validation pass)
```

T1 is the dependency root (every later task imports `FfmpegFailureClass`).
T4 is independent of T1–T3 and T6 is independent of all others — those three
can be parallelised by a single developer working on separate branches if
desired, but the sequence above is the recommended commit order to keep PR
review tractable.

---

## T1 — Failure taxonomy module and fixtures

**Files**: `resources/processor/__init__.py` (new), `resources/processor/failures.py` (new), `tests/fixtures/ffmpeg_stderr/*.txt` (new), `tests/test_failures.py` (new)

**Steps**

1. Create `resources/processor/__init__.py` (empty package marker).
2. Create `resources/processor/failures.py` with `FfmpegFailureClass` enum,
   `AttemptRecord` dataclass, `_TAIL = 8192` constant, ordered
   `_PATTERNS` list (DEVICE_OPEN_FAILED first), and
   `parse_ffmpeg_failure(stderr_tail: str) -> FfmpegFailureClass`.
3. Collect ≥ 10 real ffmpeg stderr captures from existing sidecar files
   in production logs (jobs 79, 109, 113, 114 from the prior brainstorm,
   plus any current `_ffmpeg_stderr_sidecar` output in `logs/`). Save
   under `tests/fixtures/ffmpeg_stderr/{device_open_failed,
   decoder_init_failed, encoder_init_failed, filter_init_failed,
   runtime_error}.txt`. Each class needs at least one fixture.
4. Create `tests/test_failures.py` with:
   - Parametrized fixture → class mapping test.
   - Tail-truncation test (full stderr > 8KB returns the same class as
     just the tail).
   - OTHER fallthrough test on unrecognised stderr.
   - Pattern-ordering regression test (DEVICE_OPEN_FAILED beats
     DECODER_INIT_FAILED on `"VA-API ... failed to initialize"`).

**Acceptance**

- **Given** the captured `device_open_failed.txt` fixture,
  **When** `parse_ffmpeg_failure(text)` is invoked,
  **Then** it returns `FfmpegFailureClass.DEVICE_OPEN_FAILED`.
- **Given** the same fixture prepended with 1 MB of progress noise,
  **When** the parser runs,
  **Then** it still returns DEVICE_OPEN_FAILED (tail-only matching).
- **Given** an empty string or completely unknown error text,
  **When** the parser runs,
  **Then** it returns OTHER.

**Validation**

```bash
source venv/bin/activate
python -m pytest tests/test_failures.py -v
ruff check resources/processor tests/test_failures.py
```

**Commit**: `feat(processor): add ffmpeg failure taxonomy and parser`

---

## T2 — Fallback-policy enum and schema migration

**Files**: `resources/config_schema.py`, `resources/readsettings.py`, `setup/sma-ng.yml.sample`, `tests/test_fallback_policy.py` (new)

**Steps**

1. In `resources/config_schema.py`:
   - Add `FallbackPolicy` str-enum (AGGRESSIVE / SW_DECODE_ONLY / HW_ONLY).
   - Replace `software_fallback: bool = False` with
     `fallback_policy: FallbackPolicy = FallbackPolicy.AGGRESSIVE` plus
     `software_fallback: bool | None = None` (deprecated).
   - Add `@model_validator(mode="before")` `_migrate_software_fallback`
     that maps the legacy YAML key.
   - Update the comment block describing fallback behaviour.
2. In `resources/readsettings.py`:
   - Project `fallback_policy` onto `settings.fallback_policy` (new
     attribute). Keep `settings.software_fallback` as a derived bool
     (`policy == AGGRESSIVE`) so legacy `getattr` consumers see the
     expected value during the deprecation window.
   - Emit a one-shot `WARNING` log when the migration validator fires.
3. Regenerate `setup/sma-ng.yml.sample` via `mise run config:sample`.
4. Create `tests/test_fallback_policy.py`:
   - Legacy `software-fallback: false` → policy HW_ONLY + deprecation warning.
   - Legacy `software-fallback: true` → policy AGGRESSIVE + deprecation warning.
   - New `fallback-policy: sw_decode_only` round-trips correctly.
   - Both keys present → new key wins (validator does not overwrite).

**Acceptance**

- **Given** `setup/local.yml` style `base.converter.software-fallback: false`,
  **When** the config loads,
  **Then** `settings.fallback_policy == FallbackPolicy.HW_ONLY` and a
  single deprecation warning is logged.
- **Given** `mise run config:sample` runs against the new schema,
  **When** the generated sample is diffed against the committed copy,
  **Then** the diff is empty (sample is regenerated and committed).

**Validation**

```bash
source venv/bin/activate
python -m pytest tests/test_fallback_policy.py tests/test_config.py -v
mise run config:sample
git diff --exit-code setup/sma-ng.yml.sample
```

**Commit**: `feat(config)!: replace software-fallback bool with fallback-policy enum`

> Note the `!` — this is a deprecation, not a removal. Release-please will
> still cut a minor (the alias keeps current YAML working).

---

## T3 — `_attempt_ladder` helper in MediaProcessor

**Files**: `resources/mediaprocessor.py`, `tests/test_mediaprocessor.py`

**Steps**

1. In `resources/mediaprocessor.py`:
   - Import `FfmpegFailureClass`, `AttemptRecord`, `parse_ffmpeg_failure`
     from `resources.processor.failures`.
   - Add `_attempt_ladder(self, preopts, options, run_fn)` method per the
     PRP pseudocode. The method:
     - Times each tier with `time.monotonic()`.
     - Records `AttemptRecord` per tier including failure class on error.
     - Honours `self.settings.fallback_policy` (HW_ONLY / SW_DECODE_ONLY /
       AGGRESSIVE).
     - Emits one structured log line via `self.log.info(json.dumps({...}))`.
     - Calls `self._increment_fallback_counter(from_tier, failure_class)`
       hook (no-op in CLI mode; daemon overrides it in T5).
   - Replace the inline try/try/except at lines ~3068–3105 with a call to
     `self._attempt_ladder(preopts, options, _run_convert)`.
   - **Preserve** `_strip_hw_decoder_from_preopts`,
     `_strip_qsv_input_pipeline_from_preopts`, `_swap_qsv_codec_to_sw` —
     they are still the per-tier transition functions in Phase 1.
2. Add `self._increment_fallback_counter = lambda *a, **kw: None` as a
   default no-op in `MediaProcessor.__init__`.
3. Update `tests/test_mediaprocessor.py`:
   - Adapt `test_software_fallback_disabled_skips_retries` to use
     `fallback_policy=FallbackPolicy.HW_ONLY`.
   - Add `test_fallback_policy_sw_decode_only_skips_full_software_tier`.
   - Add `test_attempt_ladder_emits_structured_log_line` asserting the
     `event: "ffmpeg.attempts"` JSON line includes per-tier records.

**Acceptance**

- **Given** `fallback_policy: hw_only` and a QSV decode failure,
  **When** conversion runs,
  **Then** no software fallback is attempted and the original error
  surfaces; the structured log line contains exactly one attempt with
  `tier: "hw"`.
- **Given** `fallback_policy: sw_decode_only` and both hw + sw_decode
  failing,
  **When** conversion runs,
  **Then** the full software tier is NOT attempted and the structured
  log contains exactly two attempts.
- **Given** `fallback_policy: aggressive` (default) and hw failing then
  sw_decode succeeding,
  **When** conversion runs,
  **Then** behaviour matches today's three-tier ladder.

**Validation**

```bash
source venv/bin/activate
python -m pytest tests/test_mediaprocessor.py -v
mise run test  # full suite must still pass
```

**Commit**: `refactor(processor): extract _attempt_ladder with failure taxonomy`

---

## T4 — Capability probe script

**Files**: `scripts/probe-hw.py` (new), `tests/test_probe_hw.py` (new)

**Steps**

1. Create `scripts/probe-hw.py` (Python, `chmod +x`, shebang
   `#!/usr/bin/env python3`):
   - No third-party deps.
   - `probe_vainfo()` — runs `vainfo`, parses stderr (driver name +
     version), cross-checks with stdout (entrypoint list).
   - `probe_ffmpeg_hwaccels(ffmpeg)` — `ffmpeg -hide_banner -hwaccels`.
   - `probe_ffmpeg_encoders(ffmpeg)` — `ffmpeg -hide_banner -encoders`,
     extract `*_qsv`, `*_nvenc`, `*_vaapi`, `*_videotoolbox`.
   - `probe_render_nodes()` — enumerate `/dev/dri/renderD*`.
   - `compute_host_signature(render_nodes, image_version)` — sha256.
   - `select_backend(caps)` — qsv | nvenc | vaapi | videotoolbox | software.
   - `_write_snapshot(path, snapshot)` — atomic write via `os.replace`.
   - CLI: `--output`, `--ffmpeg`, `--ffprobe`, `--image-version`.
   - **Fail open**: any subprocess error → record under `errors[]`, set
     `gpu_status` to `degraded` or `unreachable` accordingly; exit 0.
   - All `subprocess.run` calls use `check=False`, `timeout=5`,
     `capture_output=True`, `text=True`. Append `# nosec B603` with
     justification.
2. Create `tests/test_probe_hw.py`:
   - Mock `subprocess.run` for vainfo / ffmpeg / nvidia-smi outputs.
   - Cover ok / degraded / unreachable / unknown branches.
   - Atomic write test (partial write must not corrupt existing file).
   - Backend-selection test (Intel iGPU → qsv; NVIDIA → nvenc; etc.).

**Acceptance**

- **Given** a host with vainfo reporting iHD + h264_qsv encoder,
  **When** `probe-hw.py --output /tmp/caps.json` runs,
  **Then** the JSON has `gpu_status: "ok"`, `selected_backend: "qsv"`,
  and `capabilities.encoders.h264_qsv == true`.
- **Given** vainfo fails with permission denied,
  **When** the script runs,
  **Then** `gpu_status: "unreachable"`, `errors` contains a
  human-readable cause, and the exit code is 0 (fail open).

**Validation**

```bash
source venv/bin/activate
python -m pytest tests/test_probe_hw.py -v
python scripts/probe-hw.py --output /tmp/caps.json --ffmpeg ffmpeg --ffprobe ffprobe
cat /tmp/caps.json | python -m json.tool
ruff check scripts/probe-hw.py
bandit -q scripts/probe-hw.py
```

**Commit**: `feat(scripts): add hardware capability probe`

---

## T5 — Daemon `/health` surfacing and fallback counters

**Files**: `resources/daemon/server.py`, `resources/daemon/handler.py`, `tests/test_server.py`, `tests/test_handler.py`

**Steps**

1. In `resources/daemon/server.py`:
   - Add `self.hw_capabilities = self._probe_hw_capabilities()` at the end
     of `DaemonServer.__init__` (after config load).
   - Add `_probe_hw_capabilities(self) -> dict` per the PRP pseudocode:
     subprocess-invoke `scripts/probe-hw.py`, read the resulting JSON,
     fail open with `{"gpu_status": "unknown", "capabilities": {}, "errors": [...]}`
     on any error. Cache path: `<config_dir>/cache/hw_capabilities.json`.
   - Re-probe when the cache file's mtime predates the server's
     `started_at` (handles host kernel/driver upgrades).
   - Add `self.fallback_counters: dict[tuple[str, str, str], int] = {}`.
     Provide `self.increment_fallback_counter(from_tier, to_tier,
     failure_class)` helper.
2. Wire MediaProcessor → counters: in worker thread setup (where
   MediaProcessor is constructed), set
   `mp._increment_fallback_counter = self.server.increment_fallback_counter`.
3. In `resources/daemon/handler.py` `_get_health`:
   - Merge `self.server.hw_capabilities` under top-level `gpu_status` and
     `capabilities` keys.
   - Build `fallback` summary from `self.server.fallback_counters`
     (`[{"from": ..., "to": ..., "reason": ..., "count": ...}, ...]`).
   - Keep existing keys unchanged.
4. Update tests:
   - `tests/test_server.py`: probe-failure branch (probe-hw.py missing →
     gpu_status `unknown`, no crash); fallback_counters initialised
     empty.
   - `tests/test_handler.py`: `/health` response shape includes the new
     keys; legacy keys still present.

**Acceptance**

- **Given** the daemon starts on a host with QSV runtime present,
  **When** `curl /health` is invoked,
  **Then** the response includes `gpu_status: "ok"`,
  `capabilities.hwaccels` is a non-empty list, and `fallback` is `[]`.
- **Given** a worker records a hw → sw_decode fallback with
  `DEVICE_OPEN_FAILED`,
  **When** the next `/health` request returns,
  **Then** `fallback` contains
  `{"from": "hw", "to": "sw_decode", "reason": "device_open_failed", "count": 1}`.
- **Given** `scripts/probe-hw.py` is deleted,
  **When** the daemon restarts,
  **Then** startup completes, `gpu_status: "unknown"` is logged once
  as a warning, and `/health` returns normally.

**Validation**

```bash
source venv/bin/activate
python -m pytest tests/test_server.py tests/test_handler.py tests/test_daemon.py -v
python daemon.py --smoke-test
# Manual end-to-end:
python daemon.py --host 127.0.0.1 --port 8585 &
DAEMON_PID=$!
sleep 2
curl -s localhost:8585/health | python -m json.tool
kill $DAEMON_PID
```

**Commit**: `feat(daemon): surface gpu_status, capabilities, and fallback counters on /health`

---

## T6 — Declarative GPU permissions and entrypoint gating

**Files**: `docker/docker-compose.yml`, `docker/docker-entrypoint.sh`, `docs/configuration.md`

**Steps**

1. In `docker/docker-compose.yml`, for `sma-intel` and `sma-intel-pg`:
   - Add `group_add: ["video", "render", "992"]` (numeric 992 is the
     image's render GID fallback for hosts where neither `video` nor
     `render` exists).
   - Replace the "No group_add:" comment block with a comment
     explaining that `group_add` is the declarative path and the
     entrypoint fix-up is now opt-in via `SMA_ENTRYPOINT_FIX_GIDS=1`.
2. In `docker/docker-entrypoint.sh`:
   - Wrap the existing `/dev/dri` GID reconciliation block (lines 42–57)
     in `if [ "${SMA_ENTRYPOINT_FIX_GIDS:-0}" = "1" ] && [ "$(id -u)" = "0" ] && [ -d /dev/dri ]; then ... fi`.
   - **Preserve** the `chown /config /logs` reconciliation (still needed
     when running as root via setpriv).
   - Preserve `LIBVA_DRIVER_NAME=iHD` export (independent of the GID logic).
3. Update `docs/configuration.md` (or create `docs/hardware-acceleration.md`):
   - Document the compose migration path.
   - Note the `SMA_ENTRYPOINT_FIX_GIDS=1` opt-in for bare-docker users.
   - Document the migration deadline (entrypoint reconciliation removed
     in one minor release).

**Acceptance**

- **Given** `docker compose -f docker/docker-compose.yml --profile intel config`,
  **When** the resolved config is inspected,
  **Then** `group_add: ["video", "render", "992"]` appears on
  `sma-intel`.
- **Given** the image is run via the compose `intel` profile on an
  Intel iGPU host,
  **When** the container starts,
  **Then** the entrypoint log does NOT include "granted ubuntu access
  to /dev/dri/…", and the container processes run as `ubuntu` from PID
  initialisation onward.
- **Given** the same image is run via `docker run --device /dev/dri … -e SMA_ENTRYPOINT_FIX_GIDS=1`,
  **When** the container starts,
  **Then** the legacy GID reconciliation runs as before.

**Validation**

```bash
docker compose -f docker/docker-compose.yml --profile intel config | grep -A2 group_add
shellcheck docker/docker-entrypoint.sh
```

**Commit**: `feat(docker): declarative /dev/dri permissions via group_add; gate entrypoint GID fix-up`

---

## T7 — Ruff/bandit/pre-commit baseline

**Files**: `pyproject.toml`, `.mise/tasks/test/lint`, `.pre-commit-config.yaml` (new), `docs/development.md`

**Steps**

1. In `pyproject.toml`:
   - Expand `[tool.ruff.lint] select` to
     `["E", "F", "W", "I", "B", "SIM", "RUF", "LOG", "RET", "PTH", "S", "PERF", "PLE", "PLW", "UP"]`.
   - Preserve existing global ignores
     (`E501,E722,F841,E402,F401,F403,F405,F811,F601,E741`).
   - Run `ruff check . --fix` and triage the remaining violations:
     - Auto-fix what's auto-fixable.
     - For legacy modules where new rules fire spuriously, add minimal
       `[tool.ruff.lint.per-file-ignores]` entries with a comment
       documenting each ignore. No global ignore broadening.
   - Add `bandit[toml]>=1.7` and `pre-commit>=3.7` to
     `[project.optional-dependencies] dev`.
   - Add `[tool.bandit]` config: `skips = ["B101"]` (assert in tests),
     `exclude_dirs = ["tests", "venv", "build"]`.
2. In `.mise/tasks/test/lint`:
   - After the existing `"$PY" -m ruff check .` line, append
     `"$PY" -m bandit -q -c pyproject.toml -r resources/daemon triggers daemon.py manual.py rename.py`.
3. Triage any new bandit findings:
   - Add `# nosec <rule_id>` with justification comments on legitimate
     subprocess invocations.
   - Fix any real issues (e.g. `shell=True`, hardcoded creds — unlikely
     but possible).
4. Create `.pre-commit-config.yaml`:
   - `ruff` (lint + format) hook pinned to a recent version.
   - `bandit` hook scoped to `resources/daemon`, `triggers`, entry scripts.
   - `pre-commit-hooks`: `trailing-whitespace`, `end-of-file-fixer`,
     `check-yaml`, `check-merge-conflict`.
5. Update `docs/development.md` (create if missing) with the optional
   `pip install pre-commit && pre-commit install` instructions.

**Acceptance**

- **Given** the expanded ruff config,
  **When** `mise run test:lint` runs against `main`,
  **Then** it exits 0 (either clean or with documented per-file ignores).
- **Given** a developer introduces `subprocess.run(["foo"], shell=True)`
  in `resources/daemon/`,
  **When** the linter runs,
  **Then** bandit fails with rule B602 / B603 and the PR cannot merge.
- **Given** `pre-commit install` is run from a fresh checkout,
  **When** a commit is created,
  **Then** ruff + bandit + whitespace hooks all execute and pass.

**Validation**

```bash
source venv/bin/activate
pip install -e ".[dev]"
mise run test:lint
pre-commit run --all-files  # if installed
```

**Commit**: `chore(lint): expand ruff ruleset, add bandit, add pre-commit baseline`

---

## T8 — Docs sync, sample regen, final validation

**Files**: `docs/hardware-acceleration.md` (new), `docs/daemon.md`, `/tmp/sma-wiki/Hardware-Acceleration.md`, `resources/docs.html`, `setup/sma-ng.yml.sample`

**Steps**

1. Create `docs/hardware-acceleration.md`:
   - Section: capability probe — what gets cached, when re-probe fires.
   - Section: `/health` schema additions (`gpu_status`, `capabilities`,
     `fallback`).
   - Section: `fallback-policy` enum semantics + migration from
     `software-fallback`.
   - Section: declarative GPU permissions (compose `group_add` +
     `SMA_ENTRYPOINT_FIX_GIDS` opt-in).
   - Section: lint baseline (ruff expanded ruleset, bandit, pre-commit).
2. Update `docs/daemon.md` `/health` reference: new top-level keys are
   additive; document the schema.
3. Sync content to `/tmp/sma-wiki/Hardware-Acceleration.md` and to the
   `<details>` blocks in `resources/docs.html` (CLAUDE.md three-place
   rule).
4. Regenerate `setup/sma-ng.yml.sample` if not already done in T2.
5. Run the full validation matrix:

```bash
source venv/bin/activate
mise run config:sample
git diff --exit-code setup/sma-ng.yml.sample
mise run test
mise run test:lint
mise run dev:lint
```

**Acceptance**

- **Given** all prior tasks are merged,
  **When** the validation matrix is run,
  **Then** every command exits 0 and coverage gates (90% global, 70%
  per-module ≥ 100 statements) hold.
- **Given** an operator reads `docs/hardware-acceleration.md`,
  **When** they search for "fallback policy", "/health", or
  "group_add",
  **Then** each topic is documented with a config example and a
  rollback path.
- **Given** the wiki and inline docs are diffed against `docs/hardware-acceleration.md`,
  **When** content equivalence is checked,
  **Then** the three copies match (modulo formatting).

**Validation**

```bash
markdownlint docs/hardware-acceleration.md
mise run test
```

**Commit**: `docs: document Phase 1 — fallback policy, capability probe, declarative GPU perms`

---

## Done-checklist (whole epic)

- [x] T1 merged: `feat(processor): add ffmpeg failure taxonomy and parser`
- [x] T2 merged: `feat(config)!: replace software-fallback bool with fallback-policy enum`
- [x] T3 merged: `refactor(processor): extract _attempt_ladder with failure taxonomy`
- [x] T4 merged: `feat(scripts): add hardware capability probe`
- [x] T5 merged: `feat(daemon): surface gpu_status, capabilities, and fallback counters on /health`
- [x] T6 merged: `feat(docker): declarative /dev/dri permissions via group_add; gate entrypoint GID fix-up`
- [x] T7 merged: `chore(lint): expand ruff ruleset, add bandit, add pre-commit baseline`
- [x] T8 merged: `docs: document Phase 1 — fallback policy, capability probe, declarative GPU perms`
- [x] `curl /health` on `sma-master` returns `gpu_status: ok` and non-empty `capabilities`
- [x] Compose `intel` profile starts without root-mode entrypoint execution
- [x] Coverage ≥ 90% global, ≥ 70% per-module on modules ≥ 100 statements
- [x] No new `# pragma: no cover` or unjustified `# type: ignore`
