name: "Tune QSV HEVC encoding for ICQ + HDR + correct color tagging"
description: |
  Six related changes to the SMA-NG video encoder pipeline so that
  Intel QSV HEVC encodes default to ICQ (intelligent constant
  quality) with `slower` preset, deeper look-ahead, 10-bit pix_fmt,
  explicit BT.2020/PQ color tags on HDR output, and a
  container-aware subtitle codec default. The work is split between
  engine changes (avcodecs/mediaprocessor/schema/config-validation)
  and deployment-side config tuning (operator-driven, not in this
  PRP's scope).

---

## Discovery Summary

### Initial Task Analysis

User-supplied tuning notes:

- `-global_quality 23` (ICQ mode) instead of VBR — hevc_qsv's sweet
  spot. Lower = better quality, 21–25 typical for 1080p HEVC.
- `-preset slower` — QSV presets cost very little speed but
  meaningfully improve quality.
- `look_ahead_depth 40` instead of 16.
- `-pix_fmt p010le` for 10-bit HDR passthrough.
- Explicit BT.2020/PQ color flags on HDR output. Use
  `arib-std-b67` for trc when source is HLG.
- `mov_text` only works for `.mp4` output — when producing MKV,
  switch to `srt`.

### Codebase mapping (where each change lands)

```text
1. ICQ default 23 vs VBR
   converter/avcodecs.py:1553      H265QSVCodec.hw_quality_default = 25  → 23
   converter/avcodecs.py:1166-1178 _hw_quality_opts() — emits
                                   -global_quality only when bitrate
                                   absent. Already correct: when
                                   crf-profiles match returns no
                                   bitrate, ICQ kicks in.
   resources/mediaprocessor.py:1318-1327
                                   crf-profiles / max-bitrate logic
                                   sets vbitrate/vmaxrate/vbufsize.
                                   ICQ is reached by leaving these
                                   unset. Operator removes
                                   crf-profiles to opt in.

2. preset slower
   converter/avcodecs.py:1554      H265QSVCodec.hw_presets = ()
                                   ← empty tuple wipes any preset.
                                   Needs the QSV preset whitelist.

3. look_ahead_depth 40
   resources/config_schema.py:106  VideoSettings.look_ahead_depth: int = 0
   resources/config_schema.py:122  HDRSettings.look_ahead_depth: int = 0
                                   No cap in schema. The "16 cap" is
                                   the operator's local.yml choice.
   converter/avcodecs.py:1600-1604 hevc_qsv emits look_ahead /
                                   look_ahead_depth. Does NOT bump
                                   -extra_hw_frames the way AV1 QSV
                                   does (line 2138). Deep look-ahead
                                   risks frame-pool exhaustion;
                                   mirror AV1's pattern for safety.

4. pix_fmt p010le
   resources/config_schema.py:113  HDRSettings.pix_fmt: list[str] = []
   resources/mediaprocessor.py:1361-1376
                                   pix_fmt selection for HDR.
   converter/avcodecs.py:1573      _hw_parse_pix_fmt() routes
                                   pix_fmt → qsv_pix_fmt → format=
                                   in scale_qsv chain.
                                   Wiring works; just needs
                                   hdr.pix-fmt: [p010le] set.

5. Explicit BT.2020/PQ color flags
   converter/avcodecs.py:1620-1665 H265QSVCodecPatched (the only
                                   QSV variant that emits color
                                   metadata) writes them as
                                   x265-params strings — NOT FFmpeg
                                   global -color_primaries / -color_trc /
                                   -colorspace flags. Plain
                                   H265QSVCodec emits nothing.
                                   Fix: emit FFmpeg global flags
                                   on every QSV HEVC encode when
                                   hdrOutput is true.
   resources/mediaprocessor.py:1419
                                   hdrOutput is computed but no
                                   color_primaries/transfer/space
                                   are placed into video_settings
                                   from the HDR config.

6. mov_text vs srt by container
   resources/config_schema.py:241  SubtitleSettings.codec default
                                   = ["mov_text"]
   resources/config_schema.py:52-53 ConverterSettings.output_format
                                   = "mp4", output_extension = "mp4"
                                   No validation that subtitle codec
                                   is compatible with output
                                   container.
```

### Operator state (per `setup/local.yml`)

Current QSV HEVC settings:

```yaml
base.video.gpu: qsv
base.video.codec: [hevc]
base.video.preset: fast              # silently dropped today (whitelist=())
base.video.look-ahead-depth: 16      # operator chose this cap
base.video.crf-profiles: '0:22:1M:2M,...'   # forces VBR; blocks ICQ
base.hdr.pix-fmt: []                 # not set; defaults to source
base.hdr.transfer: [smpte2084]       # PQ; correct for HDR10
base.hdr.primaries: [bt2020]
base.hdr.space: [bt2020nc]
```

The `preset` choice is silently nullified by the empty
`hw_presets` whitelist. The `crf-profiles` matches drive VBR via
`vbitrate`, blocking the ICQ default from taking effect.

### Scope decision (auto-mode assumption)

This PRP covers the **engine** changes. The companion operator
config update (clearing `crf-profiles`, setting `preset: slower`,
`look-ahead-depth: 40`, `hdr.pix-fmt: [p010le]`) is documented but
left to the operator because `setup/local.yml` is gitignored.

Two engine alternatives surfaced for the ICQ default:

- **Alt A (recommended)**: change `H265QSVCodec.hw_quality_default`
  from 25 to 23. Minimal diff. Affects every operator who falls
  back to defaults — expected user impact: better quality at the
  same bitrate budget; opt-out by setting `base.video.global_quality`.
- **Alt B**: leave `hw_quality_default = 25` and add a new schema
  knob `base.video.global_quality: int = 0` (0 → codec default).
  Operator opts in. Lower blast radius, more configuration.

Alt B is the safer default for shared code. We do **both**: keep
the existing `hw_quality_default = 25` as the codec floor, but
expose `base.video.global_quality` so the operator can pick 23 (or
21–25 per scene) without editing avcodecs.py. This avoids surprising
existing deployments.

### User Clarifications Received

None. Auto-mode. The HLG case (`arib-std-b67` transfer) is a
config-only setting (`base.hdr.transfer: [arib-std-b67]`) so no
new code surface is needed for it — the engine just passes the
configured transfer through.

## Goal

Make QSV HEVC encodes ICQ-capable, deep-look-ahead-safe, color-
tagged correctly for HDR output, and resilient to operator-set
output-container changes. Keep existing VBR/CRF behaviour
available for operators who explicitly request it.

## Why

- ICQ produces consistently better quality than scene-blind VBR at
  comparable bitrates, with less encoder configuration churn.
- The current pipeline silently strips the QSV `preset` setting,
  which makes preset tuning a no-op and is a hidden footgun.
- HDR output without explicit color metadata renders as washed-out
  SDR on most clients (Plex, Apple TV, etc.) — visible quality bug.
- `mov_text` in an MKV file produces an unplayable subtitle stream;
  silent today, no validation.

## What

Engine-side: six focused diffs to `converter/avcodecs.py`,
`resources/mediaprocessor.py`, `resources/config_schema.py`, and
`setup/sma-ng.yml.sample`. Plus operator-facing docs.

### Success Criteria

- [ ] `base.video.preset: slower` produces `-preset slower` in the
      ffmpeg command line for hevc_qsv (today: silently stripped).
- [ ] `base.video.look-ahead-depth: 40` produces both
      `-look_ahead_depth 40` and `-extra_hw_frames 44` for hevc_qsv
      (the +4 mirrors the AV1 QSV pattern at line 2138).
- [ ] `base.video.global_quality: 23` produces `-global_quality 23`
      and suppresses `-b:v` / `-maxrate` / `-bufsize` for the
      hevc_qsv stream when no bitrate would otherwise be set.
      Default (`global_quality: 0`) preserves the codec's existing
      default of 25.
- [ ] When `hdrOutput` is true, the ffmpeg command includes
      `-color_primaries <base.hdr.primaries[0]>`,
      `-color_trc <base.hdr.transfer[0]>`,
      `-colorspace <base.hdr.space[0]>` against the encoded
      video stream.
- [ ] `base.hdr.pix-fmt: [p010le]` causes the scale_qsv chain to
      emit `:format=p010le` (already works — covered by an
      assertion test, no code change needed).
- [ ] When `output_format == "mkv"` (or `output_extension` ends in
      `mkv`) and `subtitle.codec` is `["mov_text"]`, config
      validation logs a WARNING at startup and substitutes
      `["srt"]` for the loaded settings. (Operator can opt out by
      explicitly listing `mov_text` together with at least one
      MKV-compatible codec, in which case mov_text is dropped
      silently with a debug log.)
- [ ] All existing tests pass.
- [ ] New tests cover: preset whitelist passthrough,
      extra_hw_frames sizing, global_quality plumbing, color flag
      emission on HDR output, mov_text/mkv warning.

## All Needed Context

### Research Phase Summary

- **Codebase patterns found**: AV1 QSV at `converter/avcodecs.py:2136-2140`
  already implements the `-extra_hw_frames` pattern we need for
  hevc_qsv. `_hw_parse_preset` at line 1131 implements a clean
  whitelist mechanism we just need to populate.
- **External research needed**: No — the user provided the FFmpeg
  flag names and values verbatim. Cross-checked against FFmpeg
  hevc_qsv documentation locally (`ffmpeg -h encoder=hevc_qsv`)
  during implementation will be sufficient.
- **Knowledge gaps**: None. The QSV preset list is FFmpeg-defined
  and stable: `veryfast, faster, fast, medium, slow, slower, veryslow`.

### Documentation & References

```yaml
- file: converter/avcodecs.py
  why: |
    All encoder-class changes land here. Read H265QSVCodec
    (line 1541), H265QSVCodecPatched (line 1620), AV1QSVCodec
    (line ~2100) for the extra_hw_frames pattern, and HWAccelVideoCodec
    (line 1112) for the mixin contract.

- file: resources/mediaprocessor.py
  why: |
    _select_video_codec() at line ~1234 owns video_settings dict
    construction. HDR detection (isHDRInput / isHDROutput) lives
    here; color tags need to be added when hdrOutput is true.

- file: resources/config_schema.py
  why: |
    Add `global_quality: int = 0` to VideoSettings (and HDRSettings).
    SubtitleSettings codec default is ["mov_text"] — leave as-is
    but add a post-load validator on SmaConfig (or in
    config_loader.py) for the mov_text/mkv combination.

- file: resources/config_loader.py
  why: |
    Existing site for cross-cutting validation (the .ini rejection
    and flat-shape rejection both live here). The mov_text/mkv
    warning belongs alongside.

- file: resources/readsettings.py
  why: |
    Bridges schema → flat settings.* attrs. New global_quality
    field needs a projection here for legacy consumers.

- file: setup/sma-ng.yml.sample
  why: |
    Regenerate via `mise run config:sample` after schema changes
    so the sample is in lockstep.

- file: docs/configuration.md
  why: |
    Document the new global_quality knob and the mov_text/mkv
    auto-substitution.

- file: tests/test_avcodecs.py (if exists; otherwise tests/test_codecs.py)
  why: |
    Mirror existing encoder-options tests for the new assertions.

- url: https://ffmpeg.org/ffmpeg-codecs.html#hevc_qsv
  why: |
    Authoritative list of accepted preset / global_quality /
    look_ahead_depth values for hevc_qsv.

- url: https://patchwork.ffmpeg.org/project/ffmpeg/patch/20201202131826.10558-1-omondifredrick@gmail.com/
  why: |
    Original H265QSVCodecPatched HDR-metadata patch.
    Reference only — we are NOT extending the patched variant;
    we're emitting global FFmpeg color flags that work with
    stock FFmpeg builds.
```

### Known Gotchas

```python
# CRITICAL: hevc_qsv's `-look_ahead_depth` consumes hardware frames.
#   Setting it to 40 without bumping -extra_hw_frames will trigger
#   "frame pool exhausted" errors on Intel iGPUs at 1080p+. Mirror
#   the AV1 pattern: extra_hw_frames = look_ahead_depth + 4. The
#   AV1 codec at avcodecs.py:2138 already proves this is the right
#   shape; we just port it to hevc_qsv.

# CRITICAL: Color tags must be emitted as FFmpeg OUTPUT-stream
#   flags, not as encoder-private params. The names are:
#     -color_primaries, -color_trc, -colorspace, -color_range
#   They take FFmpeg-style identifiers (bt2020, smpte2084,
#   bt2020nc, arib-std-b67, etc.), not the integers used by the
#   patched HEVC variants. Apply per-stream with `:v:0` only when
#   the encoder is HEVC; otherwise apply globally to v:0 is fine
#   since SMA-NG produces single-video-stream outputs.

# CRITICAL: ICQ mode disables the encoder's bitrate target. If the
#   operator has crf-profiles configured, _match_bitrate_profile
#   returns a target → vbitrate is set → _hw_quality_opts skips
#   the global_quality flag (the existing logic at line 1167-1170).
#   This is correct: an explicit bitrate beats ICQ. Document this
#   in the operator-facing docs.

# CRITICAL: When global_quality is set, also avoid emitting -maxrate
#   and -bufsize, because hevc_qsv ignores them in ICQ mode and
#   FFmpeg logs a warning. _hw_quality_opts already gates this on
#   `self.hw_quality_flag != "-global_quality"` — verify the new
#   plumbing keeps that check intact.

# CRITICAL: subtitle.codec auto-substitution must NOT silently
#   override an explicit operator setting. Trigger only when the
#   loaded value is exactly the schema default (["mov_text"]). Any
#   non-default value is operator intent and should be left alone
#   (with a separate validator that logs a hard error if every
#   listed codec is incompatible with the output container).

# CRITICAL: The HLG case mentioned in the task ("If your source is
#   HLG, use arib-std-b67 for trc") is a config-only setting:
#     base.hdr.transfer: [arib-std-b67]
#   No new code surface needed; the engine plumbs whatever is in
#   base.hdr.transfer[0] verbatim. Document it.
```

## Implementation Blueprint

### Data models

```python
# resources/config_schema.py — VideoSettings and HDRSettings
class VideoSettings(_Base):
    ...
    global_quality: int = 0     # NEW: 0 = use codec default
    ...

class HDRSettings(_Base):
    ...
    global_quality: int = 0     # NEW: HDR-specific override
    ...
```

### Tasks (in order)

```yaml
Task 1 — Populate the QSV preset whitelist for hevc_qsv (and h264_qsv):
MODIFY converter/avcodecs.py
  - H264QSVCodec.hw_presets (line 1345): replace () with
    ("veryfast","faster","fast","medium","slow","slower","veryslow")
  - H265QSVCodec.hw_presets (line 1554): same.
  - Mirror for AV1QSVCodec (line ~2113) only if `ffmpeg -h
    encoder=av1_qsv` confirms the same preset set; otherwise skip.

Task 2 — extra_hw_frames mirroring for hevc_qsv:
MODIFY converter/avcodecs.py:1600-1604 (H265QSVCodec
       _codec_specific_produce_ffmpeg_list look_ahead block):
  - When look_ahead_depth > 0, also emit
    ["-extra_hw_frames", str(look_ahead_depth + 4)]
    matching the AV1 QSV pattern at line 2138.

Task 3 — global_quality config knob:
MODIFY resources/config_schema.py:
  - Add `global_quality: int = 0` to VideoSettings and HDRSettings.
MODIFY resources/readsettings.py:
  - Project the new field onto self.global_quality (and
    self.hdr["global_quality"] inside the hdr dict).
MODIFY resources/mediaprocessor.py _select_video_codec:
  - Read vglobal_quality from hdr or video settings (HDR-aware,
    same pattern as look_ahead_depth at line 1356).
  - If vglobal_quality > 0 AND vbitrate is None (i.e., not in VBR
    mode from crf-profiles), pass `global_quality=vglobal_quality`
    into video_settings.
MODIFY converter/avcodecs.py:
  - Add `"global_quality": int` to encoder_options for the QSV
    classes. _hw_parse_quality should populate `safe["gq"] =
    safe["global_quality"]` when present.
MODIFY setup/sma-ng.yml.sample:
  - Regenerate via `mise run config:sample`.

Task 4 — Emit FFmpeg color-tag flags on HDR output:
MODIFY resources/mediaprocessor.py _select_video_codec:
  - When hdrOutput is true, populate video_settings with:
      "color_primaries": self.settings.hdr["primaries"][0] if any,
      "color_transfer":  self.settings.hdr["transfer"][0]  if any,
      "color_space":     self.settings.hdr["space"][0]     if any.
  - When hdrInput is true but hdrOutput is false (HDR→SDR
    transcode), do NOT carry forward HDR color tags. (Existing
    behaviour stays.)
MODIFY converter/avcodecs.py HWAccelVideoCodec or the H265Codec
       base output stage:
  - If safe contains color_primaries / color_transfer / color_space,
    emit ["-color_primaries", v, "-color_trc", v, "-colorspace", v]
    after the existing -tag:v hvc1 block (line 1529).
  - Verify the same flags get emitted by H264QSVCodec when an
    operator forces HDR-out via H264 (rare; defensive).

Task 5 — Container-aware subtitle codec validation:
MODIFY resources/config_loader.py SmaConfig post-validation
       (or add a new function called from ConfigLoader.load just
       before returning the validated config):
  - Compute target_container from converter.output_format /
    output_extension (mkv, mov, mp4, webm, ...).
  - If target_container in {"mkv", "matroska", "webm"} AND
    subtitle.codec == ["mov_text"], log a WARNING via the
    daemon logger and substitute subtitle.codec = ["srt"] in the
    loaded config.
  - If target_container is "mp4" / "m4v" AND any of subtitle.codec
    is "srt"/"subrip" (which mp4 doesn't natively accept), log a
    WARNING and drop those entries; do NOT substitute mov_text
    automatically (operator may have a reason).

Task 6 — hw_quality_default review (Alt A check):
DO NOT change hw_quality_default values for hevc_qsv (Alt B path).
The new global_quality knob is the operator-facing way to pick 23.

Task 7 — Tests:
ADD tests in tests/test_avcodecs.py (or appropriate test module):
  - test_h265qsv_preset_slower_emitted
  - test_h265qsv_look_ahead_emits_extra_hw_frames
  - test_h265qsv_global_quality_emits_flag_and_skips_bitrate
  - test_h265qsv_hdr_output_emits_color_flags
  - test_h265qsv_pix_fmt_p010le_in_scale_chain (smoke)
ADD tests in tests/test_config_loader.py:
  - test_movtext_with_mkv_output_warns_and_substitutes_srt
  - test_movtext_with_mp4_output_unchanged
  - test_explicit_subtitle_codec_with_mkv_left_alone

Task 8 — Documentation (three-place rule per CLAUDE.md):
MODIFY docs/configuration.md:
  - Document base.video.global_quality (explain ICQ vs VBR,
    typical 21–25 range, how it interacts with crf-profiles).
  - Document the QSV preset whitelist (now respected) and
    recommend `slower` for QSV deployments.
  - Document the auto-substitution rule for mov_text/mkv.
MODIFY docs/hardware-acceleration.md:
  - Add a "Tuning QSV HEVC for ICQ" section with the recommended
    operator config snippet (clears crf-profiles, sets preset:
    slower, look-ahead-depth: 40, hdr.pix-fmt: [p010le], etc.).
MIRROR same content into /tmp/sma-wiki/Configuration.md and
       /tmp/sma-wiki/Home.md (or wherever the analogous wiki
       sections live), and into resources/docs.html if applicable.

Task 9 — Operator handoff note (NOT a code change):
The operator must update their gitignored setup/local.yml to take
advantage of the new behaviour. Suggested values (documented in
docs/hardware-acceleration.md):

  base.video.global_quality: 23
  base.video.preset: slower
  base.video.look-ahead-depth: 40
  base.hdr.pix-fmt: [p010le]
  base.video.crf-profiles: ''       # clear to enable ICQ
  base.video.crf-profiles-hd: ''    # clear to enable ICQ for HDR
  base.subtitle.codec: [mov_text]   # keep for mp4
  # base.converter.output-format: mkv  # would auto-flip to srt
```

### Per-task pseudocode

```python
# Task 1 — preset whitelist
class H265QSVCodec(HWAccelVideoCodec, H265Codec):
    ...
    hw_presets = ("veryfast", "faster", "fast", "medium",
                  "slow", "slower", "veryslow")

# Task 2 — extra_hw_frames mirroring
def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
    ...
    look_ahead_depth = safe.get("look_ahead_depth", 0) or 0
    if look_ahead_depth > 0:
        optlist.extend([
            "-look_ahead", "1",
            "-look_ahead_depth", str(look_ahead_depth),
            "-extra_hw_frames", str(look_ahead_depth + 4),
        ])
    else:
        optlist.extend(["-look_ahead", "0"])
    ...

# Task 3 — global_quality knob
# encoder_options addition
encoder_options.update({
    ...
    "global_quality": int,
})
# _hw_parse_quality (mixin)
def _hw_parse_quality(self, safe):
    if "global_quality" in safe and safe["global_quality"] > 0:
        safe[self.hw_quality_key] = safe["global_quality"]
        del safe["global_quality"]
    elif self.hw_quality_key not in safe and "bitrate" not in safe \
            and self.hw_quality_default is not None:
        safe[self.hw_quality_key] = self.hw_quality_default

# Task 4 — color tag emission
# In H265Codec._codec_specific_produce_ffmpeg_list, after -tag:v:
for k, flag in (("color_primaries", "-color_primaries"),
                ("color_transfer",  "-color_trc"),
                ("color_space",     "-colorspace")):
    if k in safe and safe[k]:
        optlist.extend([flag, safe[k]])

# Task 5 — config_loader post-validation
def _normalize_subtitle_codec_for_container(self, cfg, logger):
    out_fmt = (cfg.base.converter.output_format or "").lower()
    out_ext = (cfg.base.converter.output_extension or "").lower()
    target = out_fmt or out_ext
    if target in ("mkv", "matroska", "webm"):
        if cfg.base.subtitle.codec == ["mov_text"]:
            logger.warning(
                "subtitle.codec is mov_text but output container is %s; "
                "substituting [srt]. Set base.subtitle.codec explicitly "
                "to silence this warning." % target
            )
            cfg.base.subtitle.codec = ["srt"]
```

### Integration Points

```yaml
SCHEMA:
  - resources/config_schema.py adds VideoSettings.global_quality and
    HDRSettings.global_quality (both int, default 0).
CONFIG VALIDATION:
  - resources/config_loader.py post-validates subtitle.codec against
    the resolved output container.
ENCODERS:
  - converter/avcodecs.py: H265QSVCodec gains preset whitelist,
    extra_hw_frames emission, global_quality plumbing, color-tag
    emission. H264QSVCodec gets the preset whitelist too (small
    win, same fix).
PROCESSOR:
  - resources/mediaprocessor.py: _select_video_codec adds
    color_primaries/transfer/space and global_quality to
    video_settings.
SAMPLE:
  - setup/sma-ng.yml.sample regenerated.
DOCS:
  - docs/configuration.md, docs/hardware-acceleration.md.
  - /tmp/sma-wiki/Configuration.md mirror.
  - resources/docs.html mirror (grep first; not all docs.html
    sections mirror configuration.md).
```

## Validation Loop

### Level 1: Syntax & Style

```bash
source venv/bin/activate
ruff check converter/ resources/ tests/
ruff format --check converter/ resources/ tests/
python scripts/lint-logging.py
```

### Level 2: Unit & Integration tests

```bash
source venv/bin/activate
pytest tests/test_avcodecs.py tests/test_config_loader.py \
       tests/test_mediaprocessor.py -v
pytest        # full suite
```

### Level 3: Empirical ffmpeg command verification

```bash
source venv/bin/activate
# Use manual.py -oo to dump ffmpeg options without converting
python manual.py -i /path/to/sample-hdr.mkv -oo --profile rq | \
    grep -E '(-preset|-look_ahead|-extra_hw_frames|-global_quality|-color_)'

# Expected lines (with the recommended operator config):
#   -preset slower
#   -look_ahead 1
#   -look_ahead_depth 40
#   -extra_hw_frames 44
#   -global_quality 23
#   -color_primaries bt2020
#   -color_trc smpte2084
#   -colorspace bt2020nc
#   (and -pix_fmt p010le inside the scale_qsv chain)
```

### Level 4: Live encode smoke

```bash
# Optional, slow (~15-30s for a short clip on the operator hardware):
ffmpeg -i sample-hdr.mkv -t 5 -c:v hevc_qsv -preset slower \
       -look_ahead 1 -look_ahead_depth 40 -extra_hw_frames 44 \
       -global_quality 23 -pix_fmt p010le \
       -color_primaries bt2020 -color_trc smpte2084 -colorspace bt2020nc \
       /tmp/test-icq.mp4

# Verify the output stream has the color tags via:
ffprobe -v error -select_streams v:0 \
        -show_entries stream=color_primaries,color_transfer,color_space,pix_fmt \
        /tmp/test-icq.mp4
# Expected:
#   color_primaries=bt2020
#   color_transfer=smpte2084
#   color_space=bt2020nc
#   pix_fmt=p010le
```

## Final validation Checklist

- [ ] `pytest` passes
- [ ] `ruff check` / `ruff format --check` clean
- [ ] `python scripts/lint-logging.py` clean
- [ ] `markdownlint docs/ AGENTS.md /tmp/sma-wiki/` clean
- [ ] `python manual.py -i SAMPLE -oo` shows the expected flag set
- [ ] Live ffmpeg encode produces a file whose ffprobe output
      matches the expected color/pix_fmt
- [ ] Three-place doc rule honored
- [ ] One commit per logical area: schema, encoder, processor,
      validation, sample regen, tests, docs
- [ ] No AI attribution / Co-Authored-By

## Task Breakdown

A companion task breakdown lives at
[docs/tasks/qsv-hevc-icq-encoding.md](../tasks/qsv-hevc-icq-encoding.md).

---

## Anti-Patterns to Avoid

- ❌ Don't change `H265QSVCodec.hw_quality_default` from 25 to 23.
  The new operator-facing knob is the right path; tweaking the
  codec default has too broad a blast radius.
- ❌ Don't emit color flags when hdrInput is true but hdrOutput is
  false (HDR→SDR transcode). The colorspace conversion handles
  the metadata; carrying HDR tags onto SDR output mis-tags the file.
- ❌ Don't auto-substitute subtitle codec when the operator has set
  it explicitly to anything other than the schema default.
- ❌ Don't emit `-maxrate` / `-bufsize` when `-global_quality` is
  active — the existing gate at avcodecs.py:1171 must remain.
- ❌ Don't bundle the engine changes with operator-side config
  edits to `setup/local.yml`. local.yml is gitignored and lives on
  the operator's machine.
- ❌ Don't add "AI-generated" or `Co-Authored-By` lines to commits.

## Confidence Score

**7 / 10** for one-pass implementation success.

Why not higher: the change spans schema, processor, encoder, and
validation; the test surface is wide and there are several edge
cases (HDR↔SDR transcodes, profile-overridden HDR settings,
operator with explicit codec list). Why not lower: the user's
six items each have a clean, mechanical landing site in the
codebase, and the AV1 QSV class already proves the
extra_hw_frames pattern is correct.
