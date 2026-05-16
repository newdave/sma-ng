# Brainstorm — Tighten the `rq` profile to a MeGusta-grade 1080p HEVC target

**Date:** 2026-05-16
**Facilitator:** Claude (bp:brainstorm)
**Scope:** review `profiles.rq` and the base settings it inherits; identify
issues/contradictions/oddities; propose a tightened replacement profile aligned
with a "MeGusta-quality 1080p library" target (compact files, single AAC
stereo, modest bitrate, fast iGPU encode).

## 1. Problem statement

The `rq` profile in `setup/sma-ng.yml.sample` is loose enough that real-world
output is far larger than the operator's stated mental model ("MeGusta-like
compact 1080p HEVC"). Several recent investments (QSV Phase 1 capability probe,
ICQ/global-quality knob, look-ahead pool sizing) are also **inert** on the `rq`
path because `rq` does not opt into `gpu: qsv` or set `global-quality`.

The aim of this session is to:

1. Enumerate the concrete issues / contradictions / oddities in the current
   profile and the base defaults it inherits.
2. Propose a tightened `rq` overlay that engages the QSV pipeline and lands
   typical 1080p episodes in the ~400–700 MB / 45-min neighborhood.
3. Surface the decision points (ICQ target, HDR policy, AC3 retention, GPU
   mandate) the operator needs to confirm before a PRP is written.

## 2. Current state — `rq` and inherited base

### `rq` overlay (`setup/sma-ng.yml.sample` lines 283–292)

```yaml
rq:
  video:
    codec: [h265]
    max-bitrate: 8000
  audio:
    codec: [ac3, aac]
```

### Inherited from `base` that materially shapes `rq` output

| Key | Value | Effect on `rq` |
| --- | --- | --- |
| `base.video.gpu` | `''` | No hardware acceleration. QSV pipeline is **inert**. |
| `base.video.preset` | `''` | Encoder default (≈ `medium`). Leaves 15–25% file-size on the table. |
| `base.video.global-quality` | `0` | ICQ not engaged. Encoder uses default VBR rate control. |
| `base.video.crf-profiles` / `crf-profiles-hd` | `''` | No tiered CRF ladder. |
| `base.video.bitrate-ratio` | `{}` | H.264 → HEVC re-encode does not bias smaller. |
| `base.video.look-ahead-depth` | `0` | LA disabled on QSV. |
| `base.video.max-width` | `0` | 4K sources encode at 4K @ 8 Mbps — catastrophic PSNR. |
| `base.video.pix-fmt` / `prioritize-source-pix-fmt: true` | empty / true | 10-bit sources stay 10-bit even when targeting HEVC main. |
| `base.hdr.max-bitrate` | `-1` | HDR inherits SDR 8 Mbps cap → measurably worse HDR than SDR. |
| `base.audio.channel-bitrate` | `128` | 5.1 → 6 × 128 = 768 kbps if max-channels left unlimited. |
| `base.audio.max-channels` | `0` | Surround preserved. Inflates output size. |
| `base.metadata.relocate-moov` | `true` | ✅ Correct (streaming). |

### Sample-file oddity unrelated to `rq`

- `base.audio.universal.first-stream-only: false` — would duplicate the AAC
  track for every audio stream if universal-audio is ever enabled. Should be
  `true`. Not biting `rq` today but worth fixing in the same sweep.

## 3. Findings — issues / contradictions / oddities (ranked)

### Severity: HIGH

1. **GPU not engaged anywhere on the `rq` path.** All Phase 1 QSV work (probe,
   ICQ, look-ahead, extra_hw_frames pool sizing) is dormant. Software x265 at
   `medium` is what actually runs.
2. **`max-bitrate: 8000` is far too loose** for MeGusta-style compact output.
   Real MeGusta 1080p WEB-DLs land at 1.5–2.5 Mbps avg; HEVC-equivalent
   compact target is ~4–5 Mbps cap with ICQ ≈ 25 as the primary dial.
3. **Audio `codec: [ac3, aac]` plus inherited `max-channels: 0`** means AC3
   5.1 streams copy through at 384–640 kbps and any re-encode runs at 768
   kbps. MeGusta target is single AAC stereo @ ~128 kbps.
4. **`max-width: 0`** lets 4K sources encode at 4K @ 8 Mbps cap — visibly
   broken for a library that is supposed to be uniformly 1080p.

### Severity: MEDIUM

5. **`global-quality: 0`** — ICQ not set; the rate-control mode the recent
   QSV ICQ PRP was designed to use is never engaged for `rq`.
6. **`bitrate-ratio: {}`** — H.264-to-HEVC conversions don't bias smaller;
   should be `{h264: 0.6, hevc: 1.0}`.
7. **`preset: ''`** — falling back to encoder default. QSV docs in this repo
   explicitly recommend `slower`.
8. **`hdr.max-bitrate: -1`** — HDR inherits the SDR cap. Either HDR needs a
   bumped cap (12–15 Mbps) or HDR should be set to `[copy]` so it's skipped.

### Severity: LOW

9. **`look-ahead-depth: 0`** — once ICQ is on, LA at 40 is a free quality win.
10. **`b-frames: -1`, `ref-frames: -1`** — encoder defaults are fine; tightening
    to `b-frames: 8`, `ref-frames: 4` extracts a few more % at no perceptual
    cost.
11. **`pix-fmt: []` whitelist empty** — 10-bit sources stay 10-bit even when
    encoding HEVC main. Force `[yuv420p, nv12]` to keep output 8-bit and
    broadly compatible.

## 4. Proposed tightened `rq`

```yaml
profiles:
  rq:
    video:
      gpu: qsv                                  # actually use QSV pipeline
      codec: [h265]
      max-width: 1920                           # downscale 4K → 1080p
      preset: slower                            # repo-recommended QSV default
      profile: [main]                           # force 8-bit HEVC
      pix-fmt: [yuv420p, nv12]                  # restrict pix_fmt
      global-quality: 25                        # ICQ — primary quality knob
      max-bitrate: 4500                         # ceiling, not target
      bitrate-ratio: {h264: 0.6, hevc: 1.0}     # bias HEVC re-encode smaller
      look-ahead-depth: 40                      # quality win at no real cost
      b-frames: 8
      ref-frames: 4
    hdr:
      codec: [copy]                             # skip HDR re-encode (alt: bump max-bitrate)
    audio:
      codec: [aac]                              # single AAC track
      channel-bitrate: 128
      max-channels: 2                           # 5.1 → stereo downmix
      max-bitrate: 256
```

**Expected output:** ~400–700 MB for a 45-min 1080p episode at ICQ 25
(MeGusta-equivalent for HEVC).

## 5. Decision points the operator must confirm

| Decision | Options | Default proposed |
| --- | --- | --- |
| **ICQ target** | 23 (better, larger) / 25 (MeGusta-equiv) / 27 (smaller, visibly softer) | 25 |
| **HDR policy** | `copy` (preserve, no compression) / `encode @ 12 Mbps` (compact, accept hit) / `route HDR to separate profile` | `copy` |
| **AC3 5.1 retention** | drop entirely (true MeGusta) / keep as second track via `universal-audio` | drop |
| **GPU mandate** | `gpu: qsv` + `fallback-policy: hw_only` (fail loud) / current `aggressive` (silent SW fallback) | `hw_only` |
| **Atmos/TrueHD** | `atmos-force-copy: true` (preserve when present) / `false` (re-encode to AAC stereo) | `false` |

## 6. Risks / things to verify before shipping

- The 8 Mbps → 4.5 Mbps drop will visibly differ on grainy/dark scenes.
  Recommend a side-by-side A/B on 2–3 reference sources (anime, live-action
  drama, fast-action) before adjusting the global default.
- `gpu: qsv` + `hw_only` means hosts without working QSV will fail jobs
  loudly rather than silently degrade. Coordinate with the capability-probe
  output: any host showing `gpu_status: ok` in `/health` is safe to flip.
- `max-width: 1920` triggers `scale_qsv` on the QSV pipeline. Validate the
  filter graph doesn't conflict with `dynamic-parameters` or the existing
  HDR detection.
- `profile: [main]` + 10-bit source → forces re-encode every time. Worth it
  for library uniformity; document the implication.

## 7. Next steps

1. Operator confirms the five decision points in §5.
2. Generate a PRP (`bp:generate-prp`) targeting:
   - Update `profiles.rq` in `setup/sma-ng.yml.sample` to the §4 shape.
   - Fix the `universal.first-stream-only: false` oddity in `base`.
   - Add a brief "When to use `rq`" note in `docs/configuration.md` documenting
     the MeGusta target and the ICQ/`max-bitrate` interaction.
   - Add a small A/B harness or document the manual A/B procedure on three
     reference sources.
3. Roll out by re-routing one test path to the new `rq`, validate output
   size + quality, then flip the rest.

## 8. Related work

- PRP: `docs/prps/qsv-hevc-icq-encoding.md` (ICQ + HDR color tagging — the
  encoder-level prep work this profile change consumes).
- PRP: `docs/prps/qsv-pipeline-phase1-foundation.md` (capability probe,
  `fallback-policy` enum — required for the `gpu: qsv` + `hw_only` decision).
- Brainstorm: `docs/brainstorming/2026-05-10-qsv-transcoding-refactor.md`
  (the broader QSV pipeline rework this fits into).
