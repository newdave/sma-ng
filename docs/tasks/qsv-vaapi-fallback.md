# Task Breakdown: QSV → VAAPI Fallback Tier

**Source PRP**: [docs/prps/qsv-vaapi-fallback.md](../prps/qsv-vaapi-fallback.md)
**Feature**: Insert `hw_alt` tier (hevc_vaapi encoder + preserved QSV decoder) between `hw` and `sw_decode` in `_attempt_ladder`, with nested `video.vaapi:` / `hdr.vaapi:` overlay config.
**Validation**: see PRP "Validation Loop" — `mise run dev:lint`, `mise run test:lint`, `python -m pytest tests/ -q`.

---

## Task 1: Schema and tests

### T1.1 Add `VAAPISettings` nested model

- **Title**: Define `VAAPISettings` pydantic model with sentinel-default fields.
- **Files**: `resources/config_schema.py`
- **Effort**: S
- **Dependencies**: —
- **Given-When-Then**:
  - Given a fresh `SmaConfig`,
  - When `cfg.base.video.vaapi` is read with no YAML overrides,
  - Then all fields return their sentinels (`""`, `0`, `-1`, `0.0`) and `model_dump()` is stable.
- **Mirror**: `HDRSettings` structural style (`resources/config_schema.py:203-225`).

### T1.2 Attach `vaapi` field to `VideoSettings` and `HDRSettings`

- **Title**: Wire nested `vaapi` field onto both parent blocks with `Field(default_factory=VAAPISettings)`.
- **Files**: `resources/config_schema.py`
- **Effort**: S
- **Dependencies**: T1.1
- **Given-When-Then**:
  - Given YAML containing `base.video.vaapi.codec-parameters: '-rc_mode VBR'`,
  - When the config validates,
  - Then `cfg.base.video.vaapi.codec_parameters == '-rc_mode VBR'` and `cfg.base.hdr.vaapi` is independent.

### T1.3 Add `FallbackPolicy.HW_ALT` enum value

- **Title**: Insert `HW_ALT = "hw_alt"` between `HW_ONLY` and `SW_DECODE_ONLY`; update `_migrate_software_fallback` comment.
- **Files**: `resources/config_schema.py`
- **Effort**: S
- **Dependencies**: —
- **Given-When-Then**:
  - Given YAML `fallback-policy: hw_alt`,
  - When the schema parses,
  - Then `cfg.base.converter.fallback_policy is FallbackPolicy.HW_ALT` and the legacy `software-fallback: true` still maps to `AGGRESSIVE`.

### T1.4 Export `VAAPISettings`

- **Title**: Add `VAAPISettings` to `__all__` (around line 662).
- **Files**: `resources/config_schema.py`
- **Effort**: S
- **Dependencies**: T1.1
- **Given-When-Then**:
  - Given `from resources.config_schema import VAAPISettings`,
  - When the import runs,
  - Then it succeeds without `ImportError`.

### T1.5 Add fallback-policy enum tests

- **Title**: Cover `HW_ALT` round-trip and legacy `software-fallback: true` regression.
- **Files**: `tests/test_fallback_policy.py`
- **Effort**: S
- **Dependencies**: T1.3
- **Given-When-Then**:
  - Given a YAML string with `fallback-policy: hw_alt`,
  - When parsed and re-dumped,
  - Then the round-trip equals the original and a separate test confirms `software-fallback: true` → `AGGRESSIVE`.

### T1.6 Create `tests/test_vaapi_overlay.py`

- **Title**: Cover sentinel defaults, partial overlay merge, and `hdr.vaapi` independence from `video.vaapi`.
- **Files**: `tests/test_vaapi_overlay.py` (new)
- **Effort**: M
- **Dependencies**: T1.1, T1.2
- **Given-When-Then**:
  - Given `base.video.vaapi.codec-parameters: '-rc_mode VBR'` set and nothing else,
  - When the runtime merges overlay onto parent video,
  - Then non-overlay parent fields (`preset`, `b_frames`, etc.) survive and only `codec_parameters` is appended.

---

## Task 2: Readsettings projection

### T2.1 Project `base.video.vaapi` onto `self.vaapi`

- **Title**: Add `_project_vaapi` helper and `self.vaapi` flat dict.
- **Files**: `resources/readsettings.py`
- **Effort**: S
- **Dependencies**: T1.2
- **Given-When-Then**:
  - Given a populated `base.video.vaapi`,
  - When `ReadSettings.__init__` finishes,
  - Then `self.vaapi` is a `dict` with snake_case keys matching `VAAPISettings.model_dump(by_alias=False)`.
- **Mirror**: existing hdr projection (`resources/readsettings.py:462-480`).

### T2.2 Project `base.hdr.vaapi` onto `self.hdr["vaapi"]`

- **Title**: Add nested vaapi dict inside the existing hdr projection.
- **Files**: `resources/readsettings.py`
- **Effort**: S
- **Dependencies**: T2.1
- **Given-When-Then**:
  - Given a populated `base.hdr.vaapi`,
  - When the hdr projection runs,
  - Then `self.hdr["vaapi"]` exists and is independent of `self.vaapi`.

### T2.3 Readsettings projection test

- **Title**: Cover both projections via `tests/test_readsettings.py` (or extend `test_vaapi_overlay.py`).
- **Files**: `tests/test_vaapi_overlay.py` or `tests/test_readsettings.py`
- **Effort**: S
- **Dependencies**: T2.1, T2.2
- **Given-When-Then**:
  - Given a yaml file with both `video.vaapi` and `hdr.vaapi` set differently,
  - When `ReadSettings` loads it,
  - Then both flat dicts retain their distinct values.

---

## Task 3: Encoder + preopts swap helpers

### T3.1 Add `_QSV_TO_VAAPI_CODEC_MAP` and `_QSV_ONLY_CODEC_FLAGS`

- **Title**: Define module-level constants near `_QSV_CODEC_TO_SW`.
- **Files**: `resources/mediaprocessor.py` (~line 88)
- **Effort**: S
- **Dependencies**: —
- **Given-When-Then**:
  - Given `_QSV_TO_VAAPI_CODEC_MAP["hevc_qsv"]`,
  - When read,
  - Then it returns `"hevc_vaapi"`; all four QSV codec aliases (`h264qsv`, `hevc_qsv`, `h265qsv`, `av1qsv`, `hevcqsvpatched`) map correctly.

### T3.2 Implement `_strip_qsv_only_flags`

- **Title**: Tokenise codec-parameters string and drop any flag in `_QSV_ONLY_CODEC_FLAGS` plus its value.
- **Files**: `resources/mediaprocessor.py`
- **Effort**: M
- **Dependencies**: T3.1
- **Given-When-Then**:
  - Given `"-low_power 0 -global_quality 23 -preset slow"`,
  - When stripped,
  - Then result is `"-preset slow"` and any non-QSV flags (`-preset`, `-rc_mode`, `-qp`, `-b:v`) are preserved.

### T3.3 Implement `_swap_qsv_codec_to_vaapi`

- **Title**: Swap codec, strip QSV pix_fmt, apply overlay (sentinel-fallback), inject `-rc_mode CQP -qp N` when needed.
- **Files**: `resources/mediaprocessor.py`
- **Effort**: L
- **Dependencies**: T3.1, T3.2
- **Given-When-Then**:
  - Given `options['video'] = {'codec': 'hevc_qsv', 'global_quality': 23, 'params': '-low_power 0'}` and an empty overlay,
  - When `_swap_qsv_codec_to_vaapi(options, {})` runs,
  - Then `options['video']['codec'] == 'hevc_vaapi'`, `qsv_pix_fmt` is popped, `params` contains `-rc_mode CQP -qp 23` and no QSV-only flags, return value is `"hevc_qsv"`.
- **Mirror**: `_swap_qsv_codec_to_sw` (`resources/mediaprocessor.py:97-128`).

### T3.4 Implement `_rewrite_qsv_preopts_for_vaapi_encode`

- **Title**: Preserve QSV decode preopts; append `-init_hw_device vaapi=vaapi0:<device>` and `-filter_hw_device vaapi0`; idempotent.
- **Files**: `resources/mediaprocessor.py`
- **Effort**: M
- **Dependencies**: T3.1
- **Given-When-Then**:
  - Given preopts containing `-qsv_device /dev/dri/renderD128`,
  - When rewritten,
  - Then output keeps every QSV decode flag and appends the VAAPI device init pair referencing the same render node; calling twice yields the same result.
- **Mirror**: `_strip_qsv_input_pipeline_from_preopts` (`resources/mediaprocessor.py:54-85`).

### T3.5 Implement `_inject_hwmap_to_video_filter`

- **Title**: Prepend `hwmap=derive_device=vaapi,` to `options['video']['filter']`, creating the chain if absent; idempotent.
- **Files**: `resources/mediaprocessor.py`
- **Effort**: S
- **Dependencies**: —
- **Given-When-Then**:
  - Given `options['video']['filter'] = 'scale_qsv=w=1920:h=1080'`,
  - When injected,
  - Then filter becomes `hwmap=derive_device=vaapi,scale_qsv=w=1920:h=1080`; calling twice does not double the bridge.

### T3.6 Micro-tests for swap/preopts/filter helpers

- **Title**: Add `TestSwapQsvCodecToVaapi`, `TestRewriteQsvPreoptsForVaapi`, `TestInjectHwmap` test classes.
- **Files**: `tests/test_mediaprocessor.py`
- **Effort**: M
- **Dependencies**: T3.2, T3.3, T3.4, T3.5
- **Given-When-Then**:
  - Given each helper in isolation with crafted fixtures,
  - When invoked,
  - Then list-of-codecs head replacement, QSV-only flag stripping, idempotent device append, and hwmap idempotency all assert green.

---

## Task 4: Insert hw_alt tier in `_attempt_ladder`

### T4.1 Refactor `run_fn` signature to accept `(preopts, options)`

- **Title**: Thread `options` explicitly through every tier's `run_fn` call; update the `convert()` lambda.
- **Files**: `resources/mediaprocessor.py` (~`_attempt_ladder` and `convert()` around line 3100 / 3152-3265)
- **Effort**: M
- **Dependencies**: —
- **Given-When-Then**:
  - Given a converted job under any policy,
  - When the ladder calls `run_fn`,
  - Then both `preopts` and `options` are passed positionally; tier 1 (hw) success path produces a byte-identical ffmpeg command to the pre-refactor version.

### T4.2 Implement `_resolve_vaapi_overlay`

- **Title**: Instance method on `MediaProcessor` returning the correct overlay dict (HDR vs SDR) from `self.settings`.
- **Files**: `resources/mediaprocessor.py`
- **Effort**: S
- **Dependencies**: T2.1, T2.2
- **Given-When-Then**:
  - Given a job whose source is HDR10,
  - When `_resolve_vaapi_overlay(options)` is called,
  - Then it returns `self.settings.hdr["vaapi"]`; for SDR sources it returns `self.settings.vaapi`.

### T4.3 Insert the hw_alt tier between hw and sw_decode

- **Title**: Add the new tier block after tier 1 (~line 3206); deep-copy options; skip when source isn't QSV-encoded; respect `HW_ALT` policy stop point.
- **Files**: `resources/mediaprocessor.py`
- **Effort**: L
- **Dependencies**: T3.3, T3.4, T3.5, T4.1, T4.2
- **Given-When-Then**:
  - Given tier 1 (`hw`) raised `FFMpegConvertError` and `policy == AGGRESSIVE`,
  - When the ladder reaches tier 2,
  - Then `_rewrite_qsv_preopts_for_vaapi_encode`, deep-copy + `_swap_qsv_codec_to_vaapi`, and `_inject_hwmap_to_video_filter` all fire, `run_fn` is retried, and on success an `AttemptRecord(tier="hw_alt", failure_class=None)` is appended.
- **Critical**: tier 1 success path must remain byte-identical; no mutation of original `options`.

### T4.4 Renumber sw_decode / full_sw and update local error vars

- **Title**: Rename `first_err` / `second_err` / `third_err` to readable chain names; ensure HW_ONLY still raises immediately and HW_ALT stops after the new tier.
- **Files**: `resources/mediaprocessor.py`
- **Effort**: S
- **Dependencies**: T4.3
- **Given-When-Then**:
  - Given `policy == HW_ALT` and both hw and hw_alt failed,
  - When the ladder ends,
  - Then it raises the hw_alt error with no sw_decode attempt and the structured log shows exactly two attempt records.

### T4.5 Add `AttemptRecord.tier = "hw_alt"` to recorded set

- **Title**: Document/whitelist the new tier string in `resources/processor/failures.py` if a literal set is enforced.
- **Files**: `resources/processor/failures.py`
- **Effort**: S
- **Dependencies**: —
- **Given-When-Then**:
  - Given an `AttemptRecord(tier="hw_alt")`,
  - When emitted via `_emit_attempt_log`,
  - Then no `ValueError` / lint warning fires and the JSON line contains `"tier": "hw_alt"`.

### T4.6 Ladder tests for the new tier

- **Title**: Add `TestAttemptLadderTier2HwAlt` and `TestHwAltOnlyPolicy` covering success-on-hw_alt, skip-when-not-QSV, HW_ALT stop, AGGRESSIVE continue-to-sw_decode.
- **Files**: `tests/test_attempt_ladder.py`
- **Effort**: L
- **Dependencies**: T4.3, T4.4, T4.5
- **Given-When-Then**:
  - Given `_make_mp(policy=AGGRESSIVE)` and a run_fn that fails on hw and succeeds on hw_alt,
  - When `_attempt_ladder` runs,
  - Then the result is success, no sw_decode call is observed, and `ffmpeg.attempts` shows `[hw, hw_alt]`.

### T4.7 Smoke tests in `test_mediaprocessor.py`

- **Title**: Mirror HW_ONLY / SW_DECODE_ONLY smoke tests (lines 4102-4144) for HW_ALT.
- **Files**: `tests/test_mediaprocessor.py`
- **Effort**: M
- **Dependencies**: T4.3
- **Given-When-Then**:
  - Given a MediaProcessor configured for HW_ALT,
  - When a fake ffmpeg run fails at hw and succeeds at hw_alt,
  - Then `convert()` returns success and writes the output path.

---

## Task 5: Runtime overlay reader

### T5.1 Build vaapi_overlay in `generateOptions`

- **Title**: Compute `vaapi_overlay` dict (HDR vs SDR) and attach it to the options carrier without mutating tier-1 values.
- **Files**: `resources/mediaprocessor.py` (`generateOptions`, ~line 1577-1612)
- **Effort**: M
- **Dependencies**: T2.1, T2.2
- **Given-When-Then**:
  - Given an SDR Main10 source and `self.settings.vaapi['codec_parameters'] == '-compression_level 4'`,
  - When `generateOptions` returns,
  - Then `options['_vaapi_overlay']['codec_parameters'] == '-compression_level 4'` and `options['video']` is unchanged from the pre-overlay tier 1 path.

### T5.2 Wire `_resolve_vaapi_overlay` to read the carrier

- **Title**: Have `_resolve_vaapi_overlay` consume the carrier set by T5.1.
- **Files**: `resources/mediaprocessor.py`
- **Effort**: S
- **Dependencies**: T4.2, T5.1

### T5.3 Test: overlay does not pollute tier 1 path

- **Title**: Assert tier-1 (`hw`) ffmpeg invocation is byte-identical with and without `video.vaapi` set.
- **Files**: `tests/test_vaapi_overlay.py`
- **Effort**: M
- **Dependencies**: T5.1, T5.2
- **Given-When-Then**:
  - Given two identical jobs, one with `video.vaapi.codec-parameters` set and one without,
  - When tier 1 runs to success,
  - Then the recorded ffmpeg command line is identical for both.

---

## Task 6: Sample regen + docs

### T6.1 Regenerate `setup/sma-ng.yml.sample`

- **Title**: Run `mise run config:sample` and commit the result.
- **Files**: `setup/sma-ng.yml.sample`
- **Effort**: S
- **Dependencies**: T1.1, T1.2, T1.3
- **Given-When-Then**:
  - Given the schema changes,
  - When `mise run config:sample` runs,
  - Then `setup/sma-ng.yml.sample` contains a `vaapi:` block under both `video:` and `hdr:` with sentinel defaults and an inline comment about hw_alt.

### T6.2 Document `base.video.vaapi` and `base.hdr.vaapi`

- **Title**: Add field tables and an example profile snippet to `docs/configuration.md`.
- **Files**: `docs/configuration.md`
- **Effort**: M
- **Dependencies**: T6.1
- **Given-When-Then**:
  - Given a reader scans the configuration doc,
  - When they reach the base.video section,
  - Then a `## base.video.vaapi` subsection enumerates every field, its sentinel, inherit semantics, and a YAML example with `rc_mode: VBR`.

### T6.3 Update FallbackPolicy table

- **Title**: Include `HW_ALT` row and describe the 4-tier `aggressive` ladder (hw → hw_alt → sw_decode → full_sw).
- **Files**: `docs/configuration.md` (and any other doc holding the policy table)
- **Effort**: S
- **Dependencies**: T1.3
- **Given-When-Then**:
  - Given the FallbackPolicy table,
  - When the reader inspects it,
  - Then `hw_alt` is listed between `hw_only` and `sw_decode_only` with a one-line description.

### T6.4 Add troubleshooting entry

- **Title**: Document "Invalid FrameType:0 from hevc_qsv → set fallback-policy: hw_alt".
- **Files**: `docs/troubleshooting.md`
- **Effort**: S
- **Dependencies**: T1.3
- **Given-When-Then**:
  - Given an operator searches for `Invalid FrameType:0`,
  - When they find the entry,
  - Then it tells them which fallback-policy unlocks the VAAPI tier.

---

## Task 7: Operator config rollout

### T7.1 Update `setup/local.yml` on sma-master

- **Title**: Set `base.converter.fallback-policy: hw_alt` for the one-week observation window (operator-side, gitignored).
- **Files**: `setup/local.yml` (on host; not committed)
- **Effort**: S
- **Dependencies**: T1.3, T6.1
- **Given-When-Then**:
  - Given the daemon restarts after this change,
  - When `/status` is queried,
  - Then `fallback_policy == "hw_alt"`.

### T7.2 Document the rollout step in `docs/deployment.md`

- **Title**: Note the recommended phased rollout (hw_alt → aggressive after observation).
- **Files**: `docs/deployment.md`
- **Effort**: S
- **Dependencies**: T6.3
- **Given-When-Then**:
  - Given a fresh operator follows deployment.md,
  - When they reach the fallback-policy guidance,
  - Then they see the recommended phased strategy and the validation log line to look for (`ffmpeg.attempts` with `hw_alt`).

### T7.3 Manual end-to-end on sma-master

- **Title**: Re-run job 3695's input file under `fallback-policy: hw_alt` and verify tier-2 recovery + 10-bit preserved output.
- **Files**: — (operational)
- **Effort**: M
- **Dependencies**: T7.1, T4.3, T5.2
- **Given-When-Then**:
  - Given job 3695's input file is fed to the daemon,
  - When transcode completes,
  - Then ffprobe shows `profile=main10`, `pix_fmt=yuv420p10le`, and the log emits `ffmpeg.attempts` with `[hw failure_class=runtime_error, hw_alt failure_class=null]` `result=ok`.

---

## Critical Path

```text
                T1.1 ──► T1.2 ──┐
                                ├─► T2.1 ──► T2.2 ──► T4.2 ──┐
                T1.3 ──► T1.5   │                            │
                                │                            │
T1.1 ─► T1.4    T3.1 ─► T3.2 ─► T3.3 ──────────────────┐    │
                T3.1 ─► T3.4                            │    │
                T3.5                                    │    │
                                                        ▼    ▼
                                              T4.1 ─► T4.3 ─► T4.4 ─► T4.5
                                                        │
                                                        ├─► T4.6
                                                        ├─► T4.7
                                                        │
                                              T5.1 ─► T5.2 ─► T5.3
                                                        │
                                              T6.1 ─► T6.2
                                              T6.3, T6.4
                                                        │
                                              T7.1 ─► T7.2 ─► T7.3 (E2E)
```

Blocking chain (longest path to operational green):

```text
T1.1 → T1.2 → T2.1 → T2.2 → T4.2 → T4.3 → T4.4 → T7.3
                                  ▲
T3.1 → T3.2 → T3.3 ───────────────┤
T3.1 → T3.4 ──────────────────────┤
T3.5 ─────────────────────────────┤
T4.1 ─────────────────────────────┘
```

**Parallelisable lanes**:

- Schema lane (T1.1 → T1.2 → T1.4, T1.3, T1.5, T1.6) runs alongside helper lane (T3.1 → T3.2/T3.4, T3.5).
- Docs lane (T6.x) can start as soon as T1.3 and T6.1 land; it does not block T4.x.
- T7.x is operator-side and runs last, gated on T4.3 + T5.2 being merged and deployed.
