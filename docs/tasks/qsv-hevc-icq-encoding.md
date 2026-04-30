# Task Breakdown — Tune QSV HEVC encoding for ICQ + HDR

Companion to [docs/prps/qsv-hevc-icq-encoding.md](../prps/qsv-hevc-icq-encoding.md).

Six engine changes split into eight commit-sized tasks. Operator-side
`setup/local.yml` updates are documented separately and not part of
the commit set.

## Conventions

- All Python commands run from an activated venv.
- One logical commit per task per `CLAUDE.md` rules.
- After each commit: `git pull --rebase && git push`.
- No `Co-Authored-By` / AI attribution.

## Critical path

```text
T1 (preset whitelist) ─┐
T2 (extra_hw_frames) ──┤
T3 (global_quality)  ──┼── T7 (tests) ── T8 (docs) ── T9 (commits)
T4 (color flags)     ──┤
T5 (mov_text/mkv)    ──┤
T6 (sample regen)    ──┘
```

T1–T6 are independent edits but T3 and T6 touch overlapping files
(schema → sample regeneration); do T6 last among the engine tasks.

---

## T1 — QSV preset whitelist

**File**: `converter/avcodecs.py`

**Steps**

1. In `H265QSVCodec` (line ~1554), set
   `hw_presets = ("veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")`.
2. In `H264QSVCodec` (line ~1345), apply the same whitelist.
3. (Optional) `ffmpeg -h encoder=av1_qsv` to confirm the same
   preset set; if confirmed, mirror to `AV1QSVCodec`. Otherwise
   leave AV1 alone.

**Acceptance**

- **Given** `base.video.preset: slower` and `base.video.gpu: qsv`
  with HEVC codec selected,
  **When** ffmpeg options are produced,
  **Then** `-preset slower` appears in the optlist.
- **Given** `base.video.preset: nonsense_value`,
  **When** options are produced,
  **Then** the preset is dropped silently (existing behavior).

**Suggested commit**: `feat(qsv): allow QSV preset values to reach ffmpeg`

---

## T2 — `-extra_hw_frames` for hevc_qsv look-ahead

**File**: `converter/avcodecs.py:1600-1604` (H265QSVCodec)

**Steps**

1. When `look_ahead_depth > 0`, append
   `["-extra_hw_frames", str(look_ahead_depth + 4)]` after the
   existing look_ahead/look_ahead_depth pair.
2. Mirror the AV1 QSV pattern at line 2138 verbatim.

**Acceptance**

- **Given** `base.video.look-ahead-depth: 40`,
  **When** options are produced,
  **Then** `-extra_hw_frames 44` is in the optlist alongside
  `-look_ahead_depth 40`.
- **Given** `base.video.look-ahead-depth: 0`,
  **When** options are produced,
  **Then** no `-extra_hw_frames` flag is emitted and
  `-look_ahead 0` is.

**Suggested commit**: `fix(qsv): bump extra_hw_frames with look_ahead_depth`

---

## T3 — `global_quality` schema knob

**Files**

- `resources/config_schema.py`
- `resources/readsettings.py`
- `resources/mediaprocessor.py`
- `converter/avcodecs.py`

**Steps**

1. Add `global_quality: int = 0` to `VideoSettings` and
   `HDRSettings` in `config_schema.py`.
2. In `readsettings.py`, project the new field onto
   `self.global_quality` and into the `self.hdr` dict.
3. In `mediaprocessor.py:_select_video_codec`, read
   `vglobal_quality` (HDR-aware, mirroring the look_ahead_depth
   pattern at line 1356) and add it to `video_settings` as
   `"global_quality": vglobal_quality` only when
   `vglobal_quality > 0` AND `vbitrate is None`.
4. In `converter/avcodecs.py`:
   - Add `"global_quality": int` to `encoder_options` for
     `H265QSVCodec` and `H264QSVCodec` (and `AV1QSVCodec` if AV1
     was included in T1).
   - Update `_hw_parse_quality` to honor `global_quality` when
     present (set `safe[hw_quality_key] = safe["global_quality"]`,
     then `del safe["global_quality"]`).

**Acceptance**

- **Given** `base.video.global_quality: 23` and no `crf-profiles`
  match,
  **When** options are produced,
  **Then** `-global_quality 23` is emitted and `-b:v` /
  `-maxrate` / `-bufsize` are not.
- **Given** `base.video.global_quality: 0` (default),
  **When** options are produced,
  **Then** the existing codec default (25 for hevc_qsv) is used.
- **Given** `base.video.global_quality: 23` AND a matching
  `crf-profiles` entry that resolves to a target bitrate,
  **When** options are produced,
  **Then** the bitrate path wins (VBR), `-global_quality` is not
  emitted, and a debug log notes that VBR is overriding ICQ.

**Suggested commit**: `feat(qsv): expose base.video.global_quality (ICQ)`

---

## T4 — Emit FFmpeg color-tag flags on HDR output

**Files**

- `resources/mediaprocessor.py`
- `converter/avcodecs.py`

**Steps**

1. In `_select_video_codec`, after `hdrOutput` is computed
   (line ~1419), populate `video_settings` when `hdrOutput` is
   `True`:

   ```python
   if hdrOutput:
       primaries = self.settings.hdr.get("primaries") or []
       transfer  = self.settings.hdr.get("transfer")  or []
       space     = self.settings.hdr.get("space")     or []
       if primaries:
           video_settings["color_primaries"] = primaries[0]
       if transfer:
           video_settings["color_transfer"] = transfer[0]
       if space:
           video_settings["color_space"] = space[0]
   ```

2. In `converter/avcodecs.py` `H265Codec._codec_specific_produce_ffmpeg_list`
   (line 1496), after the existing `-tag:v hvc1` emission
   (line 1529), append:

   ```python
   for k, flag in (("color_primaries", "-color_primaries"),
                   ("color_transfer",  "-color_trc"),
                   ("color_space",     "-colorspace")):
       if k in safe and safe[k]:
           optlist.extend([flag, str(safe[k])])
   ```

3. Add the same emission to `H264Codec` for parity (rare path
   but defensive).

**Acceptance**

- **Given** an HDR input, `base.hdr.primaries: [bt2020]`,
  `base.hdr.transfer: [smpte2084]`, `base.hdr.space: [bt2020nc]`,
  and an HDR-capable output (10-bit pix_fmt),
  **When** ffmpeg options are produced,
  **Then** `-color_primaries bt2020`, `-color_trc smpte2084`,
  `-colorspace bt2020nc` are present in the optlist.
- **Given** an HDR input transcoded to SDR (8-bit pix_fmt),
  **When** options are produced,
  **Then** none of the color flags are emitted.
- **Given** `base.hdr.transfer: [arib-std-b67]` (HLG),
  **When** options are produced for HLG output,
  **Then** `-color_trc arib-std-b67` is emitted verbatim.

**Suggested commit**: `feat(hdr): emit BT.2020/PQ color tags on HDR output`

---

## T5 — Container-aware subtitle codec validation

**File**: `resources/config_loader.py`

**Steps**

1. After the pydantic validation passes (just before the loader
   returns the validated config), call a new
   `_normalize_subtitle_codec_for_container(cfg, logger)`.
2. Implementation:

   ```python
   def _normalize_subtitle_codec_for_container(self, cfg, logger):
       conv = cfg.base.converter
       target = (conv.output_format or "").lower() or \
                (conv.output_extension or "").lower()
       sub = cfg.base.subtitle
       if target in ("mkv", "matroska", "webm"):
           if sub.codec == ["mov_text"]:
               logger.warning(
                   "subtitle.codec is mov_text but output container is %s; "
                   "substituting [srt]. Set base.subtitle.codec explicitly "
                   "to silence this warning." % target
               )
               sub.codec = ["srt"]
       elif target in ("mp4", "m4v"):
           bad = [c for c in sub.codec if c in ("srt", "subrip")]
           if bad:
               logger.warning(
                   "subtitle.codec entries %r are not supported in mp4; "
                   "they will be ignored at encode time." % bad
               )
   ```

3. Use the daemon logger when available; fall back to
   `logging.getLogger("sma.config")`.

**Acceptance**

- **Given** `output_format: mkv` and `subtitle.codec: [mov_text]`
  (the schema default),
  **When** the config loads,
  **Then** the loaded `subtitle.codec` is `["srt"]` and a
  WARNING-level log line names the substitution.
- **Given** `output_format: mp4` and `subtitle.codec: [mov_text]`,
  **When** the config loads,
  **Then** no warning is logged and `subtitle.codec` is unchanged.
- **Given** `output_format: mkv` and `subtitle.codec:
  [mov_text, srt]` (operator explicit),
  **When** the config loads,
  **Then** no substitution happens (mov_text will be skipped at
  encode time naturally).

**Suggested commit**: `feat(config): warn + normalize mov_text/mkv mismatch`

---

## T6 — Regenerate `setup/sma-ng.yml.sample`

**Steps**

```bash
source venv/bin/activate
mise run config:sample
git diff setup/sma-ng.yml.sample
```

Verify the new `global_quality: 0` keys appear under `base.video`
and `base.hdr` and that nothing else changed.

**Acceptance**

- The diff is limited to the new fields.
- `pytest tests/test_config_schema.py` (or the closest equivalent)
  still passes.

**Suggested commit**: bundled with T3 (same logical change).

---

## T7 — Tests

**Files**

- `tests/test_avcodecs.py` (or appropriate codec-options test
  module — search the repo for existing QSV codec tests first)
- `tests/test_config_loader.py`
- `tests/test_mediaprocessor.py` (color-tag emission)

**Steps**

1. Add (with descriptive names):
   - `test_h265qsv_preset_slower_emitted`
   - `test_h265qsv_look_ahead_emits_extra_hw_frames`
   - `test_h265qsv_global_quality_emits_flag_and_skips_bitrate`
   - `test_h265qsv_global_quality_zero_uses_codec_default`
   - `test_h265qsv_hdr_output_emits_color_flags`
   - `test_h265qsv_pix_fmt_p010le_in_scale_chain`
   - `test_h265qsv_hdr_to_sdr_omits_color_flags`
   - `test_movtext_with_mkv_output_warns_and_substitutes_srt`
   - `test_movtext_with_mp4_output_unchanged`
   - `test_explicit_subtitle_codec_with_mkv_left_alone`
2. Run `pytest -v` after each new test to confirm it both fails
   without the production change and passes with it (TDD).

**Acceptance**

- All new tests pass.
- The full suite still passes.
- Skipped count unchanged.

**Suggested commit**: bundle each test with the production change
it covers (T1 → preset test, T2 → extra_hw_frames test, etc.).

---

## T8 — Documentation (three-place rule)

**Files**

- `docs/configuration.md`
- `docs/hardware-acceleration.md`
- `/tmp/sma-wiki/Configuration.md`
- `/tmp/sma-wiki/Home.md` (the GPU section already mentions QSV)
- `resources/docs.html` (grep for matching sections; not all
  pages mirror)

**Steps**

1. In `docs/configuration.md`, document
   `base.video.global_quality` and the QSV preset whitelist.
2. Add a "Tuning QSV HEVC for ICQ" section to
   `docs/hardware-acceleration.md` with the recommended operator
   snippet (clear `crf-profiles`, set `preset: slower`,
   `look-ahead-depth: 40`, `hdr.pix-fmt: [p010le]`).
3. Mirror to wiki and `docs.html` per `CLAUDE.md`.
4. `markdownlint docs/ /tmp/sma-wiki/` clean.

**Acceptance**

- The new operator-facing knob is discoverable from
  `docs/configuration.md`.
- The "Tuning QSV HEVC" section exists with a working snippet.
- Wiki and inline help stay in lockstep.

**Suggested commit**: `docs: tune QSV HEVC ICQ + HDR color tags`

---

## T9 — Commit + push

**Steps**

Group commits per CLAUDE.md "logical commit" rules. Suggested
ordering (each its own commit):

1. `feat(qsv): allow QSV preset values to reach ffmpeg`
   (T1 + its test)
2. `fix(qsv): bump extra_hw_frames with look_ahead_depth`
   (T2 + its test)
3. `feat(qsv): expose base.video.global_quality (ICQ)`
   (T3 + T6 sample regen + tests)
4. `feat(hdr): emit BT.2020/PQ color tags on HDR output`
   (T4 + tests)
5. `feat(config): warn + normalize mov_text/mkv mismatch`
   (T5 + tests)
6. `docs: tune QSV HEVC ICQ + HDR color tags`
   (T8)

After each: `git pull --rebase && git push`. Push wiki
separately to `master`.

**Acceptance**

- `git log --oneline` shows ~6 small commits, not one umbrella.
- No `Co-Authored-By`.
- Remote is up-to-date and CI is green.

---

## Operator handoff (NOT part of the commit set)

The operator's gitignored `setup/local.yml` should be updated to
take advantage of the new behavior. Recommended values:

```yaml
base:
  video:
    preset: slower
    look-ahead-depth: 40
    global_quality: 23
    crf-profiles: ''        # clear → enable ICQ
    crf-profiles-hd: ''     # clear → enable ICQ for HDR
  hdr:
    pix-fmt: [p010le]
    transfer: [smpte2084]   # or [arib-std-b67] for HLG sources
```

`mise run config:roll` will deploy these to remote hosts.

## Out of scope

- `setup/local.yml` edits (gitignored, operator's machine).
- Changes to `H265QSVCodecPatched` or any of the patched-FFmpeg
  variants. Those exist for users running custom FFmpeg builds
  and shouldn't be touched here.
- Any AV1-QSV changes beyond what `ffmpeg -h encoder=av1_qsv`
  validates as safe (T1 step 3).
- Removing or changing `H265QSVCodec.hw_quality_default`. The new
  knob is the operator interface; the codec default stays.

## Escalation triggers

Stop and ask the user if:

1. `ffmpeg -h encoder=hevc_qsv` does not list `slower` as a
   preset on the target FFmpeg build — implies the operator's
   FFmpeg is too old; document the version requirement instead
   of shipping the change.
2. T4 lands but ffprobe on a real encode shows the color tags
   missing — implies the flags are being applied to the wrong
   stream specifier; revisit per-stream vs global wiring.
3. T5 produces a test failure for an existing config in the wild
   — operator has an unanticipated subtitle config shape;
   adjust the substitution rule.
