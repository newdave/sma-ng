# QSV → VAAPI Fallback Tier with Nested Per-Encoder Config Overrides

> **STATUS: IN-FLIGHT — generated 2026-05-21**
> Task breakdown: `docs/tasks/qsv-vaapi-fallback.md`

name: "QSV → VAAPI fallback tier with nested per-encoder config overrides"
description: |

  Add a hardware-alternate fallback tier between the existing `hw` and
  `sw_decode` tiers in `_attempt_ladder` so transient hevc_qsv encoder
  failures (e.g. mid-encode `Invalid FrameType:0` on Main10 with
  `bf=8 + adaptive_b`) route through `hevc_vaapi` on the same Intel
  iGPU instead of falling all the way to libx265 software encode. The
  default hybrid pipeline keeps the working QSV decoder and only swaps
  the encoder. Operators can tune VAAPI-specific encoder options via a
  new nested `video.vaapi:` (and `hdr.vaapi:`) config block that
  overlays the parent video/hdr block on the sentinel-fallback pattern
  established for the `hdr` overlay.

## Discovery Summary

### Initial Task Analysis

Operator-driven. Real failure observed in production: job 3695 (1080p
Main10 SDR x265 source) failed mid-encode with `Invalid FrameType:0` /
exit 183 from `hevc_qsv` after running successfully for 23 seconds /
1860 frames. The job's parameters were `profile main10`, `bf 8`,
`refs 3`, `look_ahead_depth 40`, `adaptive_i 1`, `adaptive_b 1`,
`p_strategy 1`, `b_strategy 1`, surface `p010le`. With the current
`fallback-policy: hw_only` on sma-master, the job is lost. With the
existing `aggressive` policy the recovery path is libx265 software
encode — a 10–20× wall-clock regression on this Coffee/Comet Lake
class iGPU.

### User Clarifications Received (in conversation, prior to /bp:generate-prp)

- **Q**: Why VAAPI specifically, not Vulkan Video?
  **A**: Mesa Vulkan Video *encode* is experimental for HEVC on iHD in
  this FFmpeg 8.0.x build (`Lavc62.28.100`). Quality and stability
  aren't production-ready. VAAPI is the only mature non-software HW
  encoder on Intel today. Vulkan can become a `hw_alt` candidate later.
- **Q**: Insert VAAPI tier where in the ladder?
  **A**: Between tier 1 (`hw`) and tier 2 (`sw_decode`). The QSV
  encoder bug class is exactly what VAAPI sidesteps; trying SW decode
  with the same broken QSV encoder afterwards is wasted effort.
- **Q**: Full QSV → VAAPI swap (both ends), or hybrid QSV decode +
  VAAPI encode?
  **A**: Default to hybrid. The decoder was demonstrably working
  before the encoder choked. Full swap only as a degenerate fallback
  if the hwmap bridge fails.
- **Q**: Should operators be able to tune VAAPI separately from QSV?
  **A**: Yes — VAAPI's encoder option names diverge from QSV's
  (`-rc_mode`, `-compression_level`, `-qp` etc. vs QSV's
  `-global_quality`, `-look_ahead_depth`). A nested `vaapi:` block
  under `video:` lets operators add per-encoder tuning without
  duplicating the entire encoder section.

### Missing Requirements Identified

None blocking. The hwmap bridge is the only known unknown — we'll
attempt `hwmap=derive_device=vaapi` first, fall back to
`hwdownload,format=p010le,hwupload` if hwmap rejects the surface
context. Both are documented FFmpeg idioms.

## Goal

`hevc_qsv` mid-encode failures stop being job-losing events. They get
re-attempted on `hevc_vaapi` with the original QSV decoder preserved
(hybrid pipeline). Operators can tune VAAPI separately from QSV via
a nested config block. The existing `sw_decode` and `full_sw` tiers
remain available as final safety nets when both QSV and VAAPI fail.

## Why

- **Operator-visible bug**: production failures observed at ~4% rate on
  the live host with the current `hw_only` policy. Failed jobs are
  silently dropped by Sonarr/Radarr's "downloaded but unprocessable"
  state, requiring manual intervention.
- **Quality preservation**: aligns with the project's "actively try to
  not fail to transcode" rule (memory: feedback_golden_rule_transcode_success).
- **GPU throughput**: avoids the 10–20× wall-clock regression of
  falling all the way to libx265 software for transient QSV bugs.
- **Foundation for future HW alternates**: the new tier slot and the
  `vaapi:` nested-overlay pattern are reusable for `hevc_vulkan` when
  Mesa's Vulkan Video encode stabilises.

## What

### User-visible behaviour

1. New `FallbackPolicy` enum value `hw_alt` (hw_qsv → hw_alt_vaapi,
   stop). Plus `aggressive` policy gains the new tier in its ladder.
2. New `base.video.vaapi:` schema block. Same fields as `base.video.*`
   that are relevant to the encoder (codec-parameters, preset, look-ahead-depth,
   global-quality, b-frames, ref-frames, max-level). All optional;
   sentinel-fallback to the parent `base.video.*` when unset.
3. New `base.hdr.vaapi:` schema block. Same semantics for HDR encodes.
4. When `hw_alt`/`aggressive` and tier 1 fails, the runtime:
   - keeps the QSV input-side preopts (`-hwaccel qsv -hwaccel_output_format qsv -vcodec hevc_qsv -qsv_device …`)
   - swaps the encoder: `hevc_qsv → hevc_vaapi` (also `h264_qsv → h264_vaapi`, `av1_qsv → av1_vaapi`)
   - inserts a surface bridge filter: `hwmap=derive_device=vaapi` (zero-copy on Intel via dmabuf)
   - replaces the QSV scale/vpp filter chain with the VAAPI equivalent (`scale_vaapi` / `vpp_vaapi` via `_hw_vaapi_scale_opts`)
   - applies the operator's `vaapi:` overlay on top of the resolved video block
5. New `AttemptRecord.tier` value `"hw_alt"` in the structured
   `ffmpeg.attempts` JSON log so operators can see when the new tier
   fired.

### Success Criteria

- [ ] Job 3695's exact input file transcodes successfully under
      `fallback-policy: aggressive` on sma-master (tier-1 QSV fails,
      tier-2 hw_alt VAAPI succeeds, sw tiers never invoked).
- [ ] `_attempt_ladder` emits a single `ffmpeg.attempts` JSON line with
      `attempts: [{hw, failure_class=..., ...}, {hw_alt, failure_class=null, ...}]`
      and `result: "ok"` when tier 2 (hw_alt) recovers.
- [ ] `fallback-policy: hw_only` still surfaces the original tier-1 error
      (no behavioural change for operators who explicitly opted out of
      fallback).
- [ ] `fallback-policy: hw_alt` runs hw → hw_alt and stops (no SW tiers).
- [ ] `fallback-policy: sw_decode_only` runs hw → hw_alt → sw_decode and
      stops at sw_decode failure (no full_sw).
- [ ] `fallback-policy: aggressive` runs the full 4-tier ladder
      (hw → hw_alt → sw_decode → full_sw).
- [ ] Schema parses `base.video.vaapi.codec-parameters: '-rc_mode VBR …'`
      without warnings and the VAAPI tier's ffmpeg command line carries
      those params.
- [ ] When `base.video.vaapi:` is omitted, the VAAPI tier uses the
      parent `base.video.codec-parameters` with QSV-only flags stripped
      (the QSV-only flag set `_QSV_ONLY_CODEC_FLAGS` is documented and
      tested).
- [ ] Pre-existing `fallback-policy: software-fallback` deprecation
      shim still works (no regression in `_migrate_software_fallback`).
- [ ] Unit tests for the new `_swap_qsv_codec_to_vaapi`,
      `_rewrite_qsv_preopts_for_vaapi_encode`, and the merged hw_alt
      tier in `_attempt_ladder`.
- [ ] Coverage: ≥90% global line coverage maintained; per-module ≥70%
      for any production module ≥100 statements touched (per CLAUDE.md
      Validation Matrix).

## All Needed Context

### Research Phase Summary

Codebase research returned a tight evidence pack (bp:codebase-research,
this session). External research was deemed unnecessary — every
mechanism we need is already present in the codebase; only the
*combination* (swap encoder + bridge surfaces + apply nested overlay)
is new. FFmpeg's `hwmap=derive_device=vaapi` is documented at
<https://ffmpeg.org/ffmpeg-filters.html#hwmap>; the QSV↔VAAPI dmabuf
zero-copy property on Intel iGPUs is documented at
<https://trac.ffmpeg.org/wiki/Hardware/QuickSync#TranscodingbetweenVAAPIandQSV>.

- **Codebase patterns found**: tier ladder
  (`resources/mediaprocessor.py:3152-3265`), QSV→SW swap
  (`resources/mediaprocessor.py:97-128`), VAAPI codec classes
  (`converter/avcodecs.py:1315-1349, 1727-1755, 2108-2139`), hdr
  overlay refactor (`resources/mediaprocessor.py:1577-1612` after the
  refactor we just shipped), schema enum + migration shim
  (`resources/config_schema.py:26-131`).
- **External research needed**: No. All FFmpeg idioms are standard.
- **Knowledge gaps identified**: None blocking; one runtime unknown
  (hwmap success rate on this iHD driver) addressed by the
  hwdownload+hwupload fallback inside the hw_alt tier itself.

### Documentation & References

```yaml
- file: resources/mediaprocessor.py
  why: |
    `_attempt_ladder` (lines 3152-3265) is where the new tier
    inserts. `_swap_qsv_codec_to_sw` (97-128) is the template for
    `_swap_qsv_codec_to_vaapi`. `_strip_qsv_input_pipeline_from_preopts`
    (54-85) is the template for `_rewrite_qsv_preopts_for_vaapi_encode`.

- file: converter/avcodecs.py
  why: |
    `H265VAAPICodec` (1727-1755), `H264VAAPICodec` (1352-1380),
    `AV1VAAPICodec` (2108-2139), and the `VAAPIVideoCodec` mixin
    (1315-1349) already implement the VAAPI encoder option mapping
    we'll route to. `H265VAAPICodec.hw_profiles` is unset → `main10`
    is accepted (verified by codebase research).

- file: resources/config_schema.py
  why: |
    `FallbackPolicy` enum (26-41) plus the `_migrate_software_fallback`
    pre-validator (106-131). Add `HW_ALT = "hw_alt"`; the
    `aggressive` policy is the union of all tiers, so it needs no
    enum change but its docstring updates. VideoSettings (174-200)
    is the parent block; add `VAAPISettings` nested under it.

- file: resources/processor/failures.py
  why: |
    `AttemptRecord` (42-48) and `FfmpegFailureClass` enum (26-39).
    Add tier `"hw_alt"` to the recorded set. No new failure class
    needed; existing `RUNTIME_ERROR` / `ENCODER_INIT_FAILED` cover
    the VAAPI-tier outcomes.

- file: tests/test_attempt_ladder.py
  why: |
    Primary mirror target for new ladder tests. Patterns to copy:
    `_make_mp(policy)` constructor at lines 22-30, `_err()` helper
    at 33-34, and the `TestAttemptLadderTier*` class layout.

- file: tests/test_mediaprocessor.py
  why: |
    Lines 4102-4144 already have HW_ONLY/SW_DECODE_ONLY ladder
    smoke tests. Mirror the same shape for HW_ALT.

- file: docs/configuration.md
  why: |
    The `base.hdr` section table (lines 119-150) is the template
    for documenting the new `base.video.vaapi` nested block.

- url: https://ffmpeg.org/ffmpeg-filters.html#hwmap
  why: |
    `hwmap=derive_device=vaapi` lets a QSV surface be consumed by a
    VAAPI encoder zero-copy on Intel. Fallback path is documented
    at the same URL: `hwdownload` to CPU then `hwupload` back to
    VAAPI.

- url: https://trac.ffmpeg.org/wiki/Hardware/QuickSync
  why: |
    Confirms QSV and VAAPI share dmabuf-backed surfaces on Intel
    so the bridge is real-zero-copy, not "copy via VRAM."

- docfile: .claude/projects/-Users-dhill-Projects-sma/memory/feedback_golden_rule_transcode_success.md
  why: |
    Project memory: "actively try to not fail to transcode" — this
    PRP directly serves that rule.
```

### Current Codebase tree (relevant slice)

```text
sma/
├── converter/
│   └── avcodecs.py                       # H{264,265,AV1}VAAPICodec live here
├── resources/
│   ├── config_loader.py                  # apply_profile (field-level overlay)
│   ├── config_schema.py                  # FallbackPolicy, VideoSettings, HDRSettings
│   ├── mediaprocessor.py                 # _attempt_ladder, swap helpers
│   ├── readsettings.py                   # settings projection
│   └── processor/
│       └── failures.py                   # AttemptRecord, FfmpegFailureClass
├── tests/
│   ├── test_attempt_ladder.py            # ladder tier tests
│   ├── test_fallback_policy.py           # schema enum tests
│   └── test_mediaprocessor.py            # broad mp tests
├── setup/
│   ├── local.yml                         # gitignored runtime overrides
│   └── sma-ng.yml.sample                 # canonical sample (regenerated)
└── docs/
    ├── configuration.md                  # base.video / base.hdr tables
    └── prps/qsv-vaapi-fallback.md        # this document
```

### Desired Codebase tree

```text
sma/
├── resources/
│   ├── config_schema.py                  # + VAAPISettings, + FallbackPolicy.HW_ALT
│   ├── mediaprocessor.py                 # + _swap_qsv_codec_to_vaapi,
│   │                                     # + _rewrite_qsv_preopts_for_vaapi_encode,
│   │                                     # + hw_alt tier in _attempt_ladder,
│   │                                     # + _resolve_vaapi_overlay
│   ├── readsettings.py                   # + self.vaapi dict projection
│   └── (test fixtures unchanged)
├── tests/
│   ├── test_attempt_ladder.py            # + TestAttemptLadderTier2HwAlt,
│   │                                     # + TestHwAltOnlyPolicy,
│   │                                     # + TestSwapQsvCodecToVaapi
│   ├── test_fallback_policy.py           # + HW_ALT enum test, + migration test
│   ├── test_mediaprocessor.py            # + _swap_qsv_codec_to_vaapi micro-tests
│   └── test_vaapi_overlay.py             # NEW — overlay resolution tests
├── setup/
│   └── sma-ng.yml.sample                 # regenerated to include video.vaapi
└── docs/
    └── configuration.md                  # + base.video.vaapi table + example
```

### Known Gotchas of our codebase & Library Quirks

```python
# CRITICAL: `_QSV_INPUT_PIPELINE_FLAGS` (mediaprocessor.py:43-51) defines
# the QSV input-side flag set. For VAAPI we keep most of these as-is but
# the *values* change:
#   -hwaccel qsv -> -hwaccel qsv (UNCHANGED — keep QSV decode for hybrid)
#   -hwaccel_output_format qsv -> -hwaccel_output_format qsv (UNCHANGED)
#   -qsv_device <dev> -> -qsv_device <dev> (UNCHANGED)
# Then add VAAPI init AFTER -i for the encoder filter chain:
#   -init_hw_device vaapi=vaapi0:/dev/dri/renderD128 -filter_hw_device vaapi0
# Plus prepend `hwmap=derive_device=vaapi,` to whatever vf chain exists.

# CRITICAL: hevc_vaapi does NOT accept the QSV-specific encoder flags. The
# codec-parameters string from base.video may contain:
#   -low_power -async_depth -extbrc -b_strategy -look_ahead -look_ahead_depth
#   -adaptive_i -adaptive_b -p_strategy -global_quality (use -qp/-rc_mode)
# All must be stripped before passing to hevc_vaapi. Define
# `_QSV_ONLY_CODEC_FLAGS` constant; strip them inside
# `_swap_qsv_codec_to_vaapi` BEFORE applying the operator's vaapi overlay.

# CRITICAL: VAAPI quality control is mode-dependent. `-rc_mode VBR` +
# `-b:v <rate>` (matches existing maxrate/bitrate), or `-rc_mode CQP` +
# `-qp <value>` (matches ICQ). DO NOT pass `-global_quality` — that's QSV.

# CRITICAL: `H265VAAPICodec.hw_profiles` is NOT defined, so the encoder
# accepts `main10` without the profile-whitelist check at avcodecs.py:1162
# firing. Verified by codebase-research. Don't add a whitelist there or
# Main10 fallback breaks.

# CRITICAL: The hdr overlay refactor that just shipped (mediaprocessor.py
# :1577-1612) uses sentinel-fallback. Mirror exactly the same pattern for
# the new VAAPI overlay so behaviour is consistent.

# GOTCHA: `_swap_qsv_codec_to_sw` accepts a list-of-codecs (head only).
# `_swap_qsv_codec_to_vaapi` must do the same to support the
# acceptable-as-source pattern (codec: [hevc_qsv, hevc, av1]).

# GOTCHA: schema field name MUST be `vaapi` (kebab-case in YAML is also
# `vaapi`, no conversion needed). Pydantic model_dump(by_alias=True) is
# used by stamp_daemon and config_loader; keep alias = field name.

# GOTCHA: `setup/sma-ng.yml.sample` is regenerated by
# `mise run config:sample`, NOT hand-edited. After schema changes run
# this and commit the result.
```

## Implementation Blueprint

### Data models and structure

```python
# resources/config_schema.py — add a NESTED settings class.

class VAAPISettings(_Base):
  """VAAPI encoder option overrides for the hw_alt fallback tier.

  All fields are optional; unset (sentinel) values inherit from the
  parent VideoSettings or HDRSettings block. Same overlay semantics
  as the hdr→video overlay (mediaprocessor.py:1577-1612).

  IMPORTANT: VAAPI's encoder accepts different flags from QSV.
  `-global_quality` (QSV ICQ) has no VAAPI equivalent — use
  `-rc_mode CQP -qp <N>` for quality-targeted, or `-rc_mode VBR
  -b:v <rate> -maxrate <max>` for capped-VBR.
  """
  preset: str = ""
  codec_parameters: str = ""    # appended onto stripped parent codec_params
  look_ahead_depth: int = 0     # 0 = inherit
  global_quality: int = 0       # 0 = inherit; mapped to -qp via -rc_mode CQP
  b_frames: int = -1            # -1 = inherit
  ref_frames: int = -1
  max_level: float = 0.0        # 0.0 = inherit
  rc_mode: str = ""             # "" = inherit; one of {VBR, CBR, CQP, ICQ-not-supported}


class VideoSettings(_Base):
  # ... existing fields ...
  vaapi: VAAPISettings = Field(default_factory=VAAPISettings)


class HDRSettings(_Base):
  # ... existing fields ...
  vaapi: VAAPISettings = Field(default_factory=VAAPISettings)


class FallbackPolicy(str, Enum):
  HW_ONLY = "hw_only"
  HW_ALT = "hw_alt"               # NEW — hw → hw_alt only
  SW_DECODE_ONLY = "sw_decode_only"
  AGGRESSIVE = "aggressive"        # hw → hw_alt → sw_decode → full_sw
```

### List of tasks (in implementation order)

```yaml
Task 1 — schema and tests
MODIFY resources/config_schema.py:
   - ADD VAAPISettings class (mirror HDRSettings structural style)
   - ADD `vaapi: VAAPISettings = Field(default_factory=...)` to VideoSettings
     after the existing `extra_hw_frames` field (around line 200)
   - ADD the same `vaapi` field to HDRSettings after existing
     `max_bitrate` (around line 225)
   - ADD `HW_ALT = "hw_alt"` to FallbackPolicy enum
   - UPDATE the `_migrate_software_fallback` pre-validator comment to note
     legacy `software-fallback: true` still maps to AGGRESSIVE (which now
     includes the hw_alt tier transparently)
   - EXPORT VAAPISettings via `__all__` (line 662 area)
MIRROR pattern from HDRSettings (lines 203-225)
PRESERVE _migrate_software_fallback behaviour exactly

MODIFY tests/test_fallback_policy.py:
   - ADD test that HW_ALT enum value parses, serialises, and round-trips
   - ADD test that legacy software-fallback:true still resolves to
     AGGRESSIVE (regression)

CREATE tests/test_vaapi_overlay.py:
   - test that VAAPISettings defaults are all sentinels
   - test that a partial vaapi block (just codec-parameters) merges
     with parent video without wiping other parent fields
   - test that base.hdr.vaapi resolves separately from base.video.vaapi

Task 2 — readsettings projection
MODIFY resources/readsettings.py:
   - ADD `self.vaapi = self._project_vaapi(base.video.vaapi)` after the
     existing video projection block (around line 460)
   - ADD `self.hdr["vaapi"] = self._project_vaapi(hdr_cfg.vaapi)` inside
     the hdr dict (around line 478)
   - The projection helper just .model_dump(by_alias=False)s the
     VAAPISettings into a flat dict that the runtime overlay can read

MIRROR pattern from existing hdr projection (readsettings.py:462-480)

Task 3 — encoder + preopts swap helpers
MODIFY resources/mediaprocessor.py:
   - ADD `_QSV_TO_VAAPI_CODEC_MAP` constant near line 88 next to
     `_QSV_CODEC_TO_SW`:
       {"h264qsv":"h264_vaapi", "hevc_qsv":"hevc_vaapi",
        "h265qsv":"hevc_vaapi", "av1qsv":"av1_vaapi",
        "hevcqsvpatched":"hevc_vaapi"}
   - ADD `_QSV_ONLY_CODEC_FLAGS` constant — set of flag strings the
     hevc_vaapi encoder will reject:
       {"-low_power", "-async_depth", "-extbrc", "-b_strategy",
        "-look_ahead", "-look_ahead_depth", "-adaptive_i",
        "-adaptive_b", "-p_strategy", "-global_quality"}
   - ADD `_strip_qsv_only_flags(params_str)` helper that splits the
     codec-parameters string and removes any flag in _QSV_ONLY_CODEC_FLAGS
     plus the immediately-following value. Returns the cleaned string.
   - ADD `_swap_qsv_codec_to_vaapi(options, vaapi_overlay)` mirroring
     `_swap_qsv_codec_to_sw`. Returns the original codec name on
     success. Mutates options in place:
       - video.codec → mapped VAAPI codec (head only for list)
       - video.params → _strip_qsv_only_flags(parent params) + vaapi_overlay.codec_parameters
       - video.preset → vaapi_overlay.preset if set, else strip
       - video.global_quality → mapped to -qp via params (move out of safe dict)
       - video.b_frames / ref_frames / look_ahead_depth → overlay-applied
       - video.pix_fmt → keep (compatible: nv12, p010le)
       - pop qsv_pix_fmt
   - ADD `_rewrite_qsv_preopts_for_vaapi_encode(preopts)`:
       - KEEP -hwaccel qsv, -hwaccel_output_format qsv, -qsv_device, -vcodec
         (all input-side QSV decode flags) — this is the *hybrid* default
       - APPEND -init_hw_device vaapi=vaapi0:<device-from-qsv_device>
         -filter_hw_device vaapi0
       - The new pair is added *after* the existing QSV pipeline preopts
         so QSV decode and VAAPI encode both have their device contexts.
   - ADD `_inject_hwmap_to_video_filter(options)` that prepends
     `hwmap=derive_device=vaapi,` to options['video']['filter']
     (creating the chain if it doesn't exist).
   - The new tier in `_attempt_ladder` calls these three together.

MIRROR pattern from _swap_qsv_codec_to_sw (lines 97-128) and
_strip_qsv_input_pipeline_from_preopts (lines 54-85)

Task 4 — insert hw_alt tier in _attempt_ladder
MODIFY resources/mediaprocessor.py:_attempt_ladder (lines 3152-3265):
   - AFTER the tier-1 (hw) block (3190-3206), BEFORE the tier-2 (sw_decode)
     block (3208), INSERT a new tier:
       - Skip if policy == HW_ONLY (already raises above)
       - retry_preopts = _rewrite_qsv_preopts_for_vaapi_encode(preopts)
       - retry_options = deep-copy options
       - original_codec = _swap_qsv_codec_to_vaapi(retry_options, vaapi_overlay)
       - if original_codec is None: skip this tier (input wasn't QSV
         encode in the first place — go straight to sw_decode)
       - _inject_hwmap_to_video_filter(retry_options)
       - log "Conversion failed with hw QSV; retrying with hw_alt (VAAPI encoder, QSV decode preserved)."
       - try run_fn(retry_preopts, retry_options): on success, record + return.
         on failure, classify, record, check policy:
           if policy == HW_ALT: raise (final tier for that policy)
           else: continue to sw_decode tier
   - UPDATE the existing tier-2 (sw_decode) to be tier-3, tier-3 to tier-4.
     Rename `first_err`/`second_err`/`third_err` to make the chain readable.
PRESERVE the AttemptRecord shape; just add a new tier value `"hw_alt"`.

CRITICAL: the `run_fn` signature currently takes only `preopts`. The
`options` dict is captured by closure in the calling code. To pass a
deep-copied options through to the new tier we need to either:
   (a) thread options into run_fn as a second arg (preferred — explicit)
   (b) ALWAYS deep-copy at every tier transition and mutate the global
       options dict (hidden coupling — avoid)
Choose (a). This is a small refactor of the `convert()` caller in
mediaprocessor.py to pass options into the run_fn lambda.

MODIFY tests/test_attempt_ladder.py:
   - ADD TestAttemptLadderTier2HwAlt class:
     - success on hw_alt after hw failure
     - skip hw_alt when source isn't QSV encode
     - HW_ALT policy stops after hw_alt failure
     - hw_alt failure under AGGRESSIVE continues to sw_decode
   - ADD TestSwapQsvCodecToVaapi class (mirrors TestSwapQsvCodecToSw)

Task 5 — runtime overlay reader
MODIFY resources/mediaprocessor.py:generateOptions (around line 1577-1612):
   - The video_settings dict that goes into options['video'] is built
     from the HDR-overlaid resolution we just shipped. ADD a new step
     that builds the vaapi_overlay dict (read from self.settings.vaapi
     for SDR / self.settings.hdr['vaapi'] for HDR inputs).
   - DON'T apply the overlay to video_settings yet — pass it through
     as `video_settings['_vaapi_overlay']` (or store on the
     MediaProcessor instance) so `_swap_qsv_codec_to_vaapi` can read it
     at swap time.
   - The overlay applies ONLY when the hw_alt tier fires, not on the
     hw tier — keeps the tier 1 path identical to today.

CRITICAL: the overlay must NOT mutate the original options or
video_settings dict on the success path (tier 1 hw). Only the hw_alt
retry copy gets the overlay applied.

Task 6 — sample regen + docs
RUN: mise run config:sample
   - This regenerates setup/sma-ng.yml.sample. The new video.vaapi block
     should appear with all sentinel-default fields and an inline
     comment "VAAPI overrides for the hw_alt fallback tier."

MODIFY docs/configuration.md:
   - ADD a new ## base.video.vaapi section after base.video (around the
     base.hdr section at line 119)
   - ADD a new ## base.hdr.vaapi section after base.hdr
   - DOCUMENT every VAAPISettings field with its sentinel and inherit
     semantics
   - ADD an example showing a profile that sets vaapi: { rc_mode: VBR,
     codec_parameters: '-compression_level 4' }
   - UPDATE the FallbackPolicy table (search for "fallback-policy" in
     docs/) to include HW_ALT and describe the new 4-tier aggressive
     ladder.

MODIFY docs/troubleshooting.md (if present):
   - ADD an entry: "Job failed with 'Invalid FrameType:0' from hevc_qsv"
     → "Set fallback-policy: aggressive (or hw_alt) so the hw_alt VAAPI
     tier recovers without going to libx265."

Task 7 — config knob in setup/local.yml deployment
MODIFY setup/local.yml (gitignored, sma-master's actual config):
   - SET base.converter.fallback-policy: hw_alt (or aggressive — operator
     choice; PRP recommends starting with hw_alt for one-week observation
     window before opening to aggressive)
NOTE: this is the operator-side change. Document it in deployment.md.
```

### Per-task pseudocode

```python
# Task 3: _swap_qsv_codec_to_vaapi (mediaprocessor.py)

def _swap_qsv_codec_to_vaapi(options, vaapi_overlay):
  # PATTERN: mirror _swap_qsv_codec_to_sw (mediaprocessor.py:97-128)
  if not options or not isinstance(options.get("video"), dict):
    return None
  video = options["video"]
  codec = video.get("codec")
  if not codec:
    return None
  head = codec[0] if isinstance(codec, list) else codec
  vaapi_codec = _QSV_TO_VAAPI_CODEC_MAP.get(head)
  if not vaapi_codec:
    return None
  # codec swap
  if isinstance(codec, list):
    video["codec"] = [vaapi_codec] + list(codec[1:])
  else:
    video["codec"] = vaapi_codec
  # GOTCHA: qsv_pix_fmt is QSV-only
  video.pop("qsv_pix_fmt", None)
  # CRITICAL: strip QSV-only flags from codec-parameters
  parent_params = video.get("params") or ""
  cleaned = _strip_qsv_only_flags(parent_params)
  # apply overlay (parent + overlay codec-parameters appended)
  overlay_params = (vaapi_overlay or {}).get("codec_parameters", "")
  if overlay_params:
    cleaned = (cleaned.rstrip() + " " + overlay_params.lstrip()).strip()
  video["params"] = cleaned or None
  # apply scalar overlay (sentinel fallback)
  for field, sentinel in [("preset", ""), ("b_frames", -1),
                          ("ref_frames", -1), ("look_ahead_depth", 0)]:
    ov = (vaapi_overlay or {}).get(field, sentinel)
    if ov != sentinel:
      video[field] = ov
  # global_quality → -qp via -rc_mode CQP (QSV ICQ has no direct VAAPI map)
  ov_gq = (vaapi_overlay or {}).get("global_quality", 0)
  src_gq = video.pop("global_quality", 0) or 0
  effective_q = ov_gq if ov_gq > 0 else src_gq
  if effective_q > 0 and "bitrate" not in video:
    # Inject -rc_mode CQP -qp N into params if no explicit rc_mode override
    if "-rc_mode" not in video.get("params") or "":
      video["params"] = (video.get("params") or "").rstrip() + f" -rc_mode CQP -qp {effective_q}"
  return head


def _rewrite_qsv_preopts_for_vaapi_encode(preopts):
  # PATTERN: NOT a strip — preserve QSV decode preopts, APPEND vaapi device.
  if not preopts:
    return None
  out = list(preopts)
  # find -qsv_device to know the render node path
  device = "/dev/dri/renderD128"
  for i, tok in enumerate(out):
    if tok == "-qsv_device" and i + 1 < len(out):
      device = out[i + 1]
      break
  # idempotent — don't append if already there (shouldn't happen but
  # defensive)
  if "-init_hw_device" in out:
    for i, tok in enumerate(out):
      if tok == "-init_hw_device" and "vaapi" in out[i+1]:
        return out
  out.extend([
    "-init_hw_device", f"vaapi=vaapi0:{device}",
    "-filter_hw_device", "vaapi0",
  ])
  return out


def _inject_hwmap_to_video_filter(options):
  # PATTERN: video filter is a string at options['video']['filter']
  video = options.get("video") or {}
  existing = video.get("filter") or ""
  bridge = "hwmap=derive_device=vaapi"
  if bridge in existing:
    return  # idempotent
  if existing:
    video["filter"] = bridge + "," + existing
  else:
    video["filter"] = bridge
```

```python
# Task 4: new tier in _attempt_ladder

def _attempt_ladder(self, preopts, options, outputfile, run_fn):
    policy = getattr(self.settings, "fallback_policy", FallbackPolicy.AGGRESSIVE)
    records: list[AttemptRecord] = []
    # ... existing tier 1 (hw) block ...

    # NEW: Tier 2 (hw_alt) — VAAPI encoder, QSV decoder preserved
    if policy != FallbackPolicy.HW_ONLY:  # HW_ONLY already raised
        vaapi_overlay = self._resolve_vaapi_overlay(options)  # reads settings
        retry_preopts = _rewrite_qsv_preopts_for_vaapi_encode(preopts)
        retry_options = copy.deepcopy(options)
        original_codec = _swap_qsv_codec_to_vaapi(retry_options, vaapi_overlay)
        if original_codec is None:
            # source wasn't QSV encode — skip hw_alt, fall through to sw_decode
            pass
        else:
            _inject_hwmap_to_video_filter(retry_options)
            self.log.warning(
              "Conversion failed with hw QSV (cause=%s); retrying with hw_alt "
              "(swap encoder %s -> %s, preserve QSV decoder via hwmap bridge). "
              "Original error: %s" % (cls0.value, original_codec,
              retry_options["video"]["codec"], str(first_err)[:300])
            )
            if outputfile is not None and os.path.isfile(outputfile):
                self.removeFile(outputfile)
            t_alt = time.monotonic()
            try:
                run_fn(retry_preopts, retry_options)  # NOTE: 2-arg run_fn
                records.append(AttemptRecord(tier="hw_alt", failure_class=None, duration_ms=_ms(t_alt)))
                self._emit_attempt_log(records, "ok")
                return
            except FFMpegConvertError as err:
                cls = _classify(err)
                records.append(AttemptRecord(tier="hw_alt", failure_class=cls, duration_ms=_ms(t_alt)))
                if policy == FallbackPolicy.HW_ALT:
                    self.log.warning(
                      "hw_alt also failed and fallback-policy=hw_alt; surfacing error (cause=%s)." % cls.value)
                    self._emit_attempt_log(records, "failed")
                    raise
                hw_alt_err = err

    # Tier 3 (sw_decode) — UNCHANGED logic, just shifted down the chain
    # Tier 4 (full_sw) — UNCHANGED
```

### Integration Points

```yaml
CONFIG:
  - file: resources/config_schema.py
    additions:
      - class VAAPISettings (new, nested)
      - VideoSettings.vaapi field
      - HDRSettings.vaapi field
      - FallbackPolicy.HW_ALT value
  - file: setup/sma-ng.yml.sample (regenerated)
  - file: setup/local.yml (operator-side, on sma-master):
      base:
        converter:
          fallback-policy: hw_alt   # opt-in for monitoring window
        video:
          vaapi:                    # (optional) operator overrides
            codec-parameters: '-rc_mode VBR -compression_level 4'

RUNTIME:
  - file: resources/mediaprocessor.py
    additions:
      - _QSV_TO_VAAPI_CODEC_MAP, _QSV_ONLY_CODEC_FLAGS constants
      - _swap_qsv_codec_to_vaapi(options, vaapi_overlay)
      - _rewrite_qsv_preopts_for_vaapi_encode(preopts)
      - _inject_hwmap_to_video_filter(options)
      - _strip_qsv_only_flags(params_str)
      - _resolve_vaapi_overlay (instance method on MediaProcessor)
      - New hw_alt tier inside _attempt_ladder
      - run_fn signature change: now takes (preopts, options) — CHECK ALL CALLERS

CALLERS OF run_fn:
  - resources/mediaprocessor.py:convert() — the lambda capturing options
    needs to forward both args. Search for `run_fn=` near line 3100 area.

LOGS:
  - structured event ffmpeg.attempts now emits one extra tier slot
    `{tier: "hw_alt", failure_class: ..., duration_ms: ...}` for any
    job that traversed hw → hw_alt.

DOCS:
  - docs/configuration.md: + base.video.vaapi, + base.hdr.vaapi, +
    updated FallbackPolicy table
  - docs/troubleshooting.md (if exists): + Invalid FrameType:0 entry
  - docs/deployment.md (if affected): note fallback-policy choice
```

## Validation Loop

### Level 1: Syntax & Style

```bash
# Per CLAUDE.md Validation Matrix.
source venv/bin/activate

mise run dev:lint
mise run test:lint

python -c "import resources.config_schema; print('schema ok')"
python -c "
import yaml
from resources.config_schema import SmaConfig
with open('setup/sma-ng.yml.sample') as f:
    raw = yaml.safe_load(f)
cfg = SmaConfig.model_validate(raw)
print('sample parses; video.vaapi =', cfg.base.video.vaapi)
print('hdr.vaapi =', cfg.base.hdr.vaapi)
print('fallback policies =', [p.value for p in __import__('resources.config_schema', fromlist=['FallbackPolicy']).FallbackPolicy])
"
```

### Level 2: Unit tests

```bash
source venv/bin/activate
python -m pytest tests/test_fallback_policy.py tests/test_attempt_ladder.py tests/test_vaapi_overlay.py -q
python -m pytest tests/test_mediaprocessor.py -q
python -m pytest tests/ -q   # broad pass
```

### Level 3: Manual end-to-end on sma-master

```bash
# After deploy:redeploy with new image:
# 1. Identify a known-bad source (job 3695's input file or equivalent):
#    Main10 SDR HEVC source at moderate bitrate
# 2. Trigger via webhook or scanner
# 3. Watch /opt/sma/logs/sma-ng.log for the structured attempts line:
#    "ffmpeg.attempts" should have records [hw failure_class=runtime_error,
#                                            hw_alt failure_class=null]
#    result=ok
# 4. ffprobe the output: profile=main10 pix_fmt=yuv420p10le (verifies
#    10-bit preserved through the VAAPI tier)
# 5. Verify no regression: a known-good HEVC SDR source still completes
#    in tier 1 (hw) — should NOT see "Conversion failed" in the log for
#    any source that previously worked.
```

## Final validation Checklist

- [ ] `mise run test` passes (all 3257+ tests)
- [ ] `mise run test:lint` clean
- [ ] Coverage stays ≥90% global, ≥70% per touched module ≥100 stmts
- [ ] `setup/sma-ng.yml.sample` regenerated and committed
- [ ] `docs/configuration.md` updated with both new vaapi sections + table refresh
- [ ] Job 3695's input file (or equivalent Main10 SDR source) recovers via hw_alt
- [ ] `ffmpeg.attempts` log entry shows correct tier sequence
- [ ] No regression on tier-1-success jobs (no `hw_alt` log noise for them)
- [ ] `fallback-policy: hw_only` still surfaces tier-1 errors immediately
- [ ] `fallback-policy: hw_alt` stops at the new tier (no SW fallback)
- [ ] Operators with the deprecated `software-fallback: true` config still get AGGRESSIVE behaviour (including new hw_alt tier)

---

## Anti-Patterns to Avoid

- ❌ Don't add a new `FallbackPolicy` value just for "VAAPI then SW" —
   `aggressive` is the union policy; only add HW_ALT for the
   "stop at VAAPI" stopping point.
- ❌ Don't mutate `options` in place at the hw_alt tier — deep-copy
   first. Tier 1 success path must be byte-for-byte identical to today.
- ❌ Don't try to pass QSV's `-global_quality` to hevc_vaapi — it's
   silently ignored (worse: in some FFmpeg builds it surfaces as a
   warning that breaks regex-based stderr parsers).
- ❌ Don't strip `-hwaccel qsv` from preopts in the hw_alt tier — that's
   what we WANT to keep for hybrid decode.
- ❌ Don't put VAAPI device init BEFORE the QSV input pipeline — order
   matters; QSV pipeline needs to consume its `-qsv_device` first, then
   the VAAPI device context comes online for the encoder.
- ❌ Don't author a runtime VAAPI overlay that wholesale-replaces the
   parent video block — sentinel-fallback overlay per the recently
   landed hdr pattern (`refactor(media): hdr settings overlay video`,
   commit 36ad87c). Consistency matters.
- ❌ Don't bypass the `_QSV_ONLY_CODEC_FLAGS` strip on the codec-params
   string — passing `-low_power 0` to hevc_vaapi is undefined behaviour
   on iHD.
- ❌ Don't widen the hwmap branch with try/except fallback to
   hwdownload/hwupload in v1 — keep it simple. If hwmap fails, that's
   a different failure class and the existing sw_decode/full_sw tiers
   take over. Optimise later if hwmap rejection rate is non-trivial.
- ❌ Don't add the `vaapi:` nested block to profile overrides for v1 —
   profiles use the same per-section overlay semantics as base, so
   `profiles.rq.video.vaapi.codec_parameters` Just Works through the
   existing apply_profile machinery. Test it but don't add per-profile
   docs/examples until v2.

---

## Implementation Confidence

Score: **8/10** (one-pass implementation viability).

Rationale:

- (+) Every code pattern needed already exists in the codebase (swap
      helper, preopts rewriter, schema overlay, tier ladder).
- (+) Recent hdr-overlay refactor provides a fresh, well-tested
      template for the nested-overlay shape.
- (+) Failure mode is reproducible (job 3695's input is on disk).
- (+) VAAPI codec classes already wired up — no new codec
      registration required.
- (−) `run_fn` signature change ripples through the tier-2 and tier-3
      call sites and the `convert()` caller. Easy mechanical change,
      but mechanical changes are where regression risk lives.
- (−) hwmap=derive_device=vaapi success rate on this exact iHD driver
      build is the one real runtime unknown. Mitigated by the existing
      sw_decode/full_sw tiers still being present as final safety
      nets, but the PRP intentionally does NOT add an in-tier hwmap
      retry — that's deferred to v2.

## Task Breakdown

See `docs/tasks/qsv-vaapi-fallback.md` (to be generated by
`bp:team-lead-task-breakdown`).
