# Configuration Reference

Configuration lives in `config/sma-ng.yml` (YAML). Copy from
`setup/sma-ng.yml.sample` or generate it with `make config` /
`mise run config:generate`. Use command-specific `-c/--config` flags where
available to load a different file.

INI-format configs and flat-shape YAML are not supported. Pointing
SMA-NG at a `.ini` file or a flat-shape YAML (top-level `converter:`,
`video:`, etc.) fails fast at startup with a pointer to this document.

## Four-bucket layout

`sma-ng.yml` has exactly four top-level keys:

```yaml
daemon:    # daemon-only runtime settings (host, port, db_url, routing, …)
base:      # default media-conversion settings (converter, video, audio, …)
profiles:  # named per-section overlays applied on top of base
services:  # Sonarr / Radarr / Plex instances, keyed by name
```

`manual.py` reads only `base`, `profiles`, and `services` (it ignores
the `daemon` block). The daemon reads everything.

Routing rules under `daemon.routing` map a file's path to a profile name
and to one or more service instances; full semantics are in the
[Daemon Mode reference](daemon.md#path-routing). Quick example:

```yaml
daemon:
  routing:
    - match: /mnt/unionfs/Media/TV
      profile: rq
      services: [sonarr.main]
    - match: /mnt/unionfs/Media/TV/Kids
      profile: lq
      services: [sonarr.kids]
```

The longest matching prefix wins; an unmatched path falls through to
`base` with no profile and no service notification.

The schema is the single source of truth for defaults and types; every
YAML key is validated by pydantic at load. Unknown keys are accepted
but logged as `WARNING Unknown config key: <dotted.path>` so typos
surface immediately. Sample regeneration (`mise run config:sample`)
and the `config-sample-consistency` CI job keep the committed sample
locked to the schema.

The downloader sections (`SABNZBD`, `Deluge`, `qBittorrent`, `uTorrent`)
that used to live here are removed — those integrations are now
shell-trigger-only and configured in `triggers/`. See
[Integrations](integrations.md).

---

## base.converter

| Option                    | Type    | Default           | Description                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------------- | ------- | ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ffmpeg`                  | path    | `ffmpeg`          | Path to FFmpeg binary                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `ffprobe`                 | path    | `ffprobe`         | Path to FFprobe binary                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `threads`                 | int     | `0`               | FFmpeg threads (0 = auto)                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `output-directory`        | path    |                   | Temporary output location (files moved back after)                                                                                                                                                                                                                                                                                                                                                                                                  |
| `output-format`           | string  | `mp4`             | Container format: `mp4`, `mkv`, `mov`                                                                                                                                                                                                                                                                                                                                                                                                               |
| `output-extension`        | string  | `mp4`             | Output file extension                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `temp-extension`          | string  |                   | Temporary file extension during conversion                                                                                                                                                                                                                                                                                                                                                                                                          |
| `temp-output`             | bool    | `true`            | Use temporary output file during conversion                                                                                                                                                                                                                                                                                                                                                                                                         |
| `minimum-size`            | int     | `0`               | Minimum source file size in MB (0 = disabled)                                                                                                                                                                                                                                                                                                                                                                                                       |
| `ignored-extensions`      | list    | `[nfo, ds_store]` | Extensions to skip                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `copy-to`                 | path(s) |                   | Copy output to additional directories                                                                                                                                                                                                                                                                                                                                                                                                               |
| `move-to`                 | path    |                   | Move output to final destination                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `delete-original`         | bool    | `true`            | Delete source file after successful conversion                                                                                                                                                                                                                                                                                                                                                                                                      |
| `recycle-bin`             | path    |                   | Copy original here before deleting (only when `delete-original = True`)                                                                                                                                                                                                                                                                                                                                                                             |
| `process-same-extensions` | bool    | `false`           | Reprocess files already in output format                                                                                                                                                                                                                                                                                                                                                                                                            |
| `bypass-if-copying-all`   | bool    | `false`           | Skip conversion if all streams can be copied                                                                                                                                                                                                                                                                                                                                                                                                        |
| `force-convert`           | bool    | `false`           | Force conversion even if codec matches                                                                                                                                                                                                                                                                                                                                                                                                              |
| `post-process`            | bool    | `false`           | Run post-process scripts                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `wait-post-process`       | bool    | `false`           | Wait for post-process scripts to finish                                                                                                                                                                                                                                                                                                                                                                                                             |
| `software-fallback`       | bool    | `false`           | When `true`, retry hardware-accelerated failures with software decode and, if needed, a full software pipeline. Defaults to `false` so the original FFmpeg error surfaces immediately — the retry chain historically masked real hardware issues (e.g. `/dev/dri` permissions, missing QSV runtime) by silently completing jobs on the CPU. Set to `true` per-profile or globally to restore the legacy "always finish, even in software" behavior. |
| `preopts`                 | list    |                   | Extra FFmpeg options before input                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `postopts`                | list    |                   | Extra FFmpeg options after codec options                                                                                                                                                                                                                                                                                                                                                                                                            |

---

## base.video

| Option                      | Type   | Default | Description                                                                                                                                                                                                                                                                                               |
| --------------------------- | ------ | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `codec`                     | list   | `h265`  | Video codecs in priority order. First is used for encoding, rest are copy-eligible                                                                                                                                                                                                                        |
| `gpu`                       | string |         | Hardware acceleration backend: `qsv`, `vaapi`, `nvenc`, `videotoolbox`, or empty for software                                                                                                                                                                                                             |
| `max-bitrate`               | int    | `0`     | Maximum video bitrate in kbps (0 = unlimited)                                                                                                                                                                                                                                                             |
| `bitrate-ratio`             | dict   |         | Scale source bitrate per codec: `hevc:1.0, h264:0.65`                                                                                                                                                                                                                                                     |
| `crf-profiles`              | string |         | Tiered bitrate targets by source bitrate. Format: `source_kbps:quality:target:maxrate` (comma-separated). Example: `0:22:3M:6M,8000:22:5M:10M`. Leave blank to use `bitrate-ratio` + `max-bitrate` instead.                                                                                               |
| `crf-profiles-hd`           | string |         | Same format as `crf-profiles`, applied to sources above 1080p height. Falls back to `crf-profiles` when empty.                                                                                                                                                                                            |
| `preset`                    | string |         | Encoder preset: `ultrafast` to `veryslow`                                                                                                                                                                                                                                                                 |
| `profile`                   | list   |         | Video profile: `main`, `high`, `main10`                                                                                                                                                                                                                                                                   |
| `max-level`                 | float  |         | Maximum H.264/H.265 level (e.g., `5.2`)                                                                                                                                                                                                                                                                   |
| `max-width`                 | int    | `0`     | Maximum output width (0 = no limit)                                                                                                                                                                                                                                                                       |
| `pix-fmt`                   | list   |         | Pixel format whitelist                                                                                                                                                                                                                                                                                    |
| `dynamic-parameters`        | bool   | `false` | Pass HDR/color metadata to encoder                                                                                                                                                                                                                                                                        |
| `prioritize-source-pix-fmt` | bool   | `true`  | Keep source pix_fmt if in whitelist                                                                                                                                                                                                                                                                       |
| `filter`                    | string |         | Custom FFmpeg video filter                                                                                                                                                                                                                                                                                |
| `force-filter`              | bool   | `false` | Force re-encode when filter is set                                                                                                                                                                                                                                                                        |
| `codec-parameters`          | string\|list |   | Encoder-agnostic FFmpeg flags (e.g. `-x265-params <opts>` for libx265, free-form fallback flags). Accepts a YAML list of flag fragments for readability; the schema joins entries with spaces. Encoder-specific flags belong under `base.video.qsv.codec-parameters` or `base.video.vaapi.codec-parameters` — anything QSV- or VAAPI-specific written here is automatically lifted into the right subblock by the migration shim. |
| `look-ahead-depth`          | int    | `0`     | Look-ahead frames for rate control (QSV: `la_depth`). `0` = encoder default. SMA-NG auto-sizes `-extra_hw_frames` to `look-ahead-depth + 4` (floor `20`, cap `100`) to keep the device frame pool from running dry.                                                                                       |
| `extra-hw-frames`           | int    | `0`     | QSV `-extra_hw_frames` pool size (input/device scope). `0` = auto: derived from `look-ahead-depth`. Any positive value overrides the auto-derived pool, clamped to ffmpeg's QSV ceiling of `100`. Profiles can override per path (`profiles.<name>.video.extra-hw-frames`). Only emitted when `gpu: qsv`. |
| `global-quality`            | int    | `0`     | ICQ quality target for QSV encodes (lower = better, typical `21–25` for 1080p HEVC). `0` lets the codec use its default. Ignored when a bitrate target is set via `crf-profiles` / `bitrate-ratio` / `max-bitrate`; ICQ and VBR are mutually exclusive.                                                   |
| `b-frames`                  | int    | `-1`    | Number of B-frames. `-1` = encoder default.                                                                                                                                                                                                                                                               |
| `ref-frames`                | int    | `-1`    | Number of reference frames. `-1` = encoder default.                                                                                                                                                                                                                                                       |

> **Encoder-flag safety:** When the active encoder is QSV, only `base.video.qsv.*` (plus the encoder-agnostic `codec-parameters`) is emitted to ffmpeg. When the active encoder is VAAPI (typically the `hw_alt` fallback tier), only `base.video.vaapi.*` is emitted. The other subblock is silently ignored. This means a QSV-only flag misplaced under `vaapi:` can never leak onto a `hevc_vaapi` command line — run `mise run config:validate` to catch the leak before it ships.
>
> **QSV preset:** `h264_qsv`, `hevc_qsv`, `vp9_qsv`, and `av1_qsv` accept the standard FFmpeg QSV preset names (`veryfast`, `faster`, `fast`, `medium`, `slow`, `slower`, `veryslow`). Earlier releases silently dropped any preset on these encoders; SMA-NG now passes them through. `slower` is the recommended QSV default — it costs little speed on Intel iGPUs and meaningfully improves quality.

---

## base.video.qsv

Per-encoder typed overlay for Intel QSV encoders (`hevc_qsv`, `h264_qsv`,
`av1_qsv`). The runtime reads from this block only when the active encoder
is QSV; the parallel `base.video.vaapi` block is ignored on the QSV path,
and vice versa. This is the "never pass wrong options to ffmpeg" guarantee:
a QSV-only flag in this block can never leak onto a `hevc_vaapi` command
line.

Use this for QSV-specific knobs that have no VAAPI equivalent or that you
want to tune independently of VAAPI. Operators can also write per-encoder
overrides for the shared fields (`preset`, `b-frames`, etc.) here; unset
values inherit from `base.video.*` at runtime.

| Option             | Type   | Default | Description                                                                                                                                  |
| ------------------ | ------ | ------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `preset`           | string | `''`    | Override `base.video.preset` for the QSV path. `''` inherits.                                                                                |
| `codec-parameters` | string | `''`    | Free-form QSV-only flag string appended to the assembled command line. Use for flags not exposed as typed fields below.                      |
| `low-power`        | int    | `-1`    | `-low_power` flag value (`0` = VDENC quality path, `1` = fixed-function low-power). `-1` = don't emit (encoder default fires).               |
| `async-depth`      | int    | `0`     | `-async_depth` pipeline depth. `0` = don't emit. VDENC sweet spot is `4`; higher values can starve look-ahead.                               |
| `extbrc`           | int    | `-1`    | `-extbrc` extended BRC toggle (`0`/`1`). `-1` = don't emit.                                                                                  |
| `b-strategy`       | int    | `-1`    | `-b_strategy` adaptive B-frame strategy (`0`/`1`). `-1` = don't emit.                                                                        |
| `adaptive-i`       | int    | `-1`    | `-adaptive_i` (`0`/`1`). `-1` = don't emit.                                                                                                  |
| `adaptive-b`       | int    | `-1`    | `-adaptive_b` (`0`/`1`). `-1` = don't emit.                                                                                                  |
| `p-strategy`       | int    | `-1`    | `-p_strategy` (`0`/`1`). `-1` = don't emit.                                                                                                  |
| `rdo`              | int    | `-1`    | `-rdo` rate-distortion optimization (`0`/`1`). `-1` = don't emit. Quality win on slow presets; small perf cost.                              |
| `look-ahead-depth` | int    | `0`     | Override `base.video.look-ahead-depth` for QSV. `0` = inherit.                                                                               |
| `extra-hw-frames`  | int    | `0`     | Override `base.video.extra-hw-frames` (QSV surface-pool size) for the QSV path. `0` = inherit.                                               |
| `global-quality`   | int    | `0`     | Override `base.video.global-quality` (QSV ICQ target). `0` = inherit.                                                                        |
| `b-frames`         | int    | `-1`    | Override `base.video.b-frames` for QSV. `-1` = inherit.                                                                                       |
| `ref-frames`       | int    | `-1`    | Override `base.video.ref-frames` for QSV. `-1` = inherit.                                                                                     |

> **Migration:** if you have a legacy `base.video.codec-parameters` string
> with QSV-only flags in it, the schema's migration validator automatically
> lifts those tokens into `base.video.qsv.codec-parameters` on load. The
> shape is value-preserving; operators are encouraged to write the typed
> shape directly going forward.

---

## base.video.vaapi

Per-encoder typed overlay for VAAPI encoders (`hevc_vaapi`, `h264_vaapi`,
`av1_vaapi`) used by the `hw_alt` fallback tier (see
`base.converter.fallback-policy`). When QSV encoding fails and the policy
permits `hw_alt`, SMA-NG swaps `hevc_qsv`/`h264_qsv`/`av1_qsv` for the same-vendor
VAAPI encoder while preserving the (working) QSV decoder via a zero-copy
`hwmap=derive_device=vaapi` bridge.

Same operator promise as the QSV block: a VAAPI-only flag in this block
can never leak onto a non-VAAPI command line. Unset fields inherit from
`base.video.*` at runtime.

| Option              | Type   | Default | Description                                                                                                                                            |
| ------------------- | ------ | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `preset`            | string | `''`    | Encoder preset override for the VAAPI tier. `''` inherits `base.video.preset`.                                                                         |
| `codec-parameters`  | string | `''`    | Free-form VAAPI-only flag string appended to the assembled command line.                                                                               |
| `rc-mode`           | string | `''`    | VAAPI rate-control mode (`VBR`, `CBR`, `CQP`). `''` = inherit from `codec-parameters` if `-rc_mode` is set there, otherwise the encoder default fires. |
| `compression-level` | int    | `0`     | `-compression_level` (VAAPI-specific quality/speed tuning). `0` = don't emit.                                                                          |
| `low-power`         | int    | `-1`    | `-low_power` for VAAPI (semantics differ from QSV's flag of the same name). `-1` = don't emit.                                                         |
| `look-ahead-depth`  | int    | `0`     | Override `base.video.look-ahead-depth` for VAAPI. `0` = inherit. Mapped to VAAPI's own look-ahead control where supported.                             |
| `global-quality`    | int    | `0`     | Quality target for VAAPI. `0` = inherit. Translated to `-rc_mode CQP -qp <N>` because VAAPI has no direct equivalent of QSV's ICQ `-global_quality`.   |
| `b-frames`          | int    | `-1`    | B-frame override. `-1` = inherit from `base.video.b-frames`.                                                                                            |
| `ref-frames`        | int    | `-1`    | Reference frame override. `-1` = inherit from `base.video.ref-frames`.                                                                                  |
| `max-level`         | float  | `0.0`   | Profile level cap override. `0.0` = inherit from `base.video.max-level`.                                                                                |

> **VAAPI vs QSV flag names diverge.** `-global_quality` is QSV-only; on
> VAAPI use `-rc_mode CQP -qp <N>` for quality-targeted, or `-rc_mode VBR
> -b:v <rate> -maxrate <max>` for capped-VBR. Either write the encoder
> tunings as typed fields here and let the runtime translate, or stuff
> them into `codec-parameters` verbatim.

---

## base.hdr

Override video settings for HDR content (detected automatically).

| Option             | Type   | Description                                                                                                                                                                                   |
| ------------------ | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `codec`            | list   | Video codec for HDR content                                                                                                                                                                   |
| `pix-fmt`          | list   | Pixel format for HDR (e.g., `p010le`)                                                                                                                                                         |
| `space`            | list   | Color space: `bt2020nc`                                                                                                                                                                       |
| `transfer`         | list   | Transfer function: `smpte2084`                                                                                                                                                                |
| `primaries`        | list   | Color primaries: `bt2020`                                                                                                                                                                     |
| `preset`           | string | Encoder preset override for HDR                                                                                                                                                               |
| `profile`          | string | Profile override for HDR                                                                                                                                                                      |
| `codec-parameters` | string | Extra params for HDR encoding                                                                                                                                                                 |
| `filter`           | string | Video filter for HDR content                                                                                                                                                                  |
| `force-filter`     | bool   | Force re-encode for HDR filter                                                                                                                                                                |
| `look-ahead-depth` | int    | Look-ahead depth override for HDR encoding (default: `0`)                                                                                                                                     |
| `extra-hw-frames`  | int    | QSV pool override for HDR encoding (default: `0` = inherit from `base.video.extra-hw-frames`). The larger of the two values wins so neither pipeline starves.                                 |
| `global-quality`   | int    | ICQ quality target for HDR encodes (default: `0` = inherit from `base.video.global-quality`)                                                                                                  |
| `b-frames`         | int    | B-frames override for HDR encoding (default: `-1` = encoder default)                                                                                                                          |
| `ref-frames`       | int    | Reference frames override for HDR encoding (default: `-1` = encoder default)                                                                                                                  |
| `max-bitrate`      | int    | HDR-only override of `base.video.max-bitrate` in kbps. `-1` (default) inherits the SDR cap; `0` disables the cap entirely so HDR remuxes copy through; positive values cap HDR independently. |

When the output is HDR (10-bit pix_fmt and `space`/`transfer`/`primaries` set), SMA-NG emits the configured first values as FFmpeg output flags so HDR-aware players (Plex, Apple TV, etc.) tag the stream correctly:

```text
-color_primaries <hdr.primaries[0]>   # e.g. bt2020
-color_trc       <hdr.transfer[0]>    # e.g. smpte2084 for HDR10, arib-std-b67 for HLG
-colorspace      <hdr.space[0]>       # e.g. bt2020nc
```

For HLG sources, set `transfer: [arib-std-b67]`. The flags are emitted regardless of encoder (qsv / vaapi / nvenc / software) and only on HDR output — HDR→SDR transcodes do not carry the tags forward.

---

## base.hdr.qsv

HDR-specific QSV overlay. Same field shape and semantics as
[`base.video.qsv`](#basevideoqsv) but unset values inherit from
`base.hdr.*` (not `base.video.*`). Use this to tune the QSV encoder
differently for HDR content (e.g. higher `look-ahead-depth`, different
`rdo` setting) without affecting SDR encodes.

---

## base.hdr.vaapi

HDR-specific overlay for the `hw_alt` fallback tier. Same field shape and
semantics as [`base.video.vaapi`](#basevideovaapi) but unset values inherit
from `base.hdr.*` (not `base.video.*`).

| Option              | Type   | Default | Description                                                                                          |
| ------------------- | ------ | ------- | ---------------------------------------------------------------------------------------------------- |
| `preset`            | string | `''`    | Encoder preset override for HDR VAAPI encodes. `''` inherits `base.hdr.preset`.                      |
| `codec-parameters`  | string | `''`    | Free-form VAAPI-only flag string appended to the assembled command line (HDR side).                  |
| `rc-mode`           | string | `''`    | VAAPI rate-control mode for HDR encodes (`VBR`, `CBR`, `CQP`).                                       |
| `compression-level` | int    | `0`     | `-compression_level` for HDR. `0` = don't emit.                                                      |
| `low-power`         | int    | `-1`    | `-low_power` for HDR VAAPI. `-1` = don't emit.                                                       |
| `look-ahead-depth`  | int    | `0`     | HDR look-ahead override. `0` = inherit from `base.hdr.look-ahead-depth`.                             |
| `global-quality`    | int    | `0`     | HDR quality target. `0` = inherit. Mapped to `-rc_mode CQP -qp <N>`.                                 |
| `b-frames`          | int    | `-1`    | HDR B-frame override. `-1` = inherit.                                                                |
| `ref-frames`        | int    | `-1`    | HDR reference frame override. `-1` = inherit.                                                        |
| `max-level`         | float  | `0.0`   | HDR profile level cap override. `0.0` = inherit.                                                     |

---

## base.analyzer

Optional per-job planning layer for analyzer-assisted transcoding decisions.

It does **not** replace FFmpeg encoding or hardware acceleration. Instead, it provides the config, planner hooks, and preview surfaces that an analyzer backend can use before FFmpeg runs.

Current implementation scope:

- backend selection for analyzer runtime
- OpenVINO device selection including `CPU`, `GPU`, `NPU`, and composite selectors such as `AUTO:NPU,CPU`
- bounded recommendation plumbing in the planner for codec ordering, bitrate ceilings, presets, filters, and force-reencode decisions
- preview output integration via `manual.py -oo`

Current backend status:

- the OpenVINO backend currently validates runtime availability and requested device selection
- the current OpenVINO backend returns placeholder observations, so analyzer-driven recommendation payloads will usually be empty until richer model-backed inference is added
- `model-dir`, `cache-dir`, `max-frames`, and `target-width` are forward-compatible analyzer settings; they are reserved for richer model-backed inference as the backend grows
- if the backend is unavailable or a requested device such as `NPU` is missing, SMA-NG logs a warning and falls back to normal planning

| Option                      | Type   | Default    | Description                                                                                                   |
| --------------------------- | ------ | ---------- | ------------------------------------------------------------------------------------------------------------- |
| `enabled`                   | bool   | `false`    | Enable analyzer-assisted planning                                                                             |
| `backend`                   | string | `openvino` | Analyzer backend runtime. Current supported value: `openvino`                                                 |
| `device`                    | string | `AUTO`     | OpenVINO target device selector. Valid examples: `AUTO`, `CPU`, `GPU`, `NPU`, `AUTO:NPU,CPU`, `MULTI:NPU,GPU` |
| `model-dir`                 | path   |            | Optional directory containing analyzer models (reserved for richer future inference)                          |
| `cache-dir`                 | path   |            | Optional cache directory for compiled analyzer artifacts                                                      |
| `max-frames`                | int    | `12`       | Reserved sampling limit for future model-backed inference                                                     |
| `target-width`              | int    | `960`      | Reserved downscale width for future model-backed inference                                                    |
| `allow-codec-reorder`       | bool   | `true`     | Allow analyzer to reorder the configured video codec pool                                                     |
| `allow-bitrate-adjustments` | bool   | `true`     | Allow analyzer to change bitrate multipliers / ceilings                                                       |
| `allow-preset-adjustments`  | bool   | `true`     | Allow analyzer to override encoder preset                                                                     |
| `allow-filter-adjustments`  | bool   | `true`     | Allow analyzer to add bounded FFmpeg filters such as deinterlace/crop/denoise                                 |
| `allow-force-reencode`      | bool   | `true`     | Allow analyzer to force re-encode when copy would otherwise be selected                                       |

Example:

```yaml
base:
  analyzer:
    enabled: true
    backend: openvino
    device: AUTO:NPU,CPU
    model-dir: ""
    cache-dir: /var/cache/sma-openvino
    max-frames: 12
    target-width: 960
    allow-codec-reorder: true
    allow-bitrate-adjustments: true
    allow-preset-adjustments: true
    allow-filter-adjustments: true
    allow-force-reencode: true
```

### OpenVINO notes

- Install the optional runtime with `pip install -r setup/requirements-openvino.txt` or `pip install .[openvino]`
- If `device = NPU` (or `AUTO:NPU,...`) is configured and the runtime reports no NPU, SMA-NG logs a warning and falls back to normal planning instead of failing the entire job
- Analyzer recommendations are intentionally bounded and local to the current job; they do not mutate your saved `sma-ng.yml`

---

## base.audio

| Option                      | Type   | Default | Description                                                                             |
| --------------------------- | ------ | ------- | --------------------------------------------------------------------------------------- |
| `codec`                     | list   | `aac`   | Audio codecs in priority order. Matching streams are copied; others re-encoded to first |
| `languages`                 | list   |         | Language whitelist (ISO 639-3, e.g., `eng`). Empty = all                                |
| `default-language`          | string | `eng`   | Default language for unlabeled streams                                                  |
| `first-stream-of-language`  | bool   | `false` | Keep only first stream per language                                                     |
| `allow-language-relax`      | bool   | `true`  | If no whitelisted language found, keep all audio                                        |
| `include-original-language` | bool   | `false` | Include original media language even if not in whitelist                                |
| `channel-bitrate`           | int    | `128`   | Bitrate per channel in kbps (0 = auto)                                                  |
| `variable-bitrate`          | int    | `0`     | VBR quality level (0 = disabled/CBR)                                                    |
| `max-bitrate`               | int    | `0`     | Maximum audio bitrate in kbps                                                           |
| `max-channels`              | int    | `0`     | Maximum audio channels (0 = unlimited, 6 = 5.1)                                         |
| `copy-original`             | bool   | `false` | Copy original audio stream in addition to transcoded                                    |
| `aac-adtstoasc`             | bool   | `true`  | Apply AAC ADTS to ASC bitstream filter                                                  |
| `ignored-dispositions`      | list   |         | Skip streams with these dispositions: `comment`, `hearing_impaired`                     |
| `unique-dispositions`       | bool   | `false` | One stream per disposition per language                                                 |
| `stream-codec-combinations` | list   |         | Identify duplicate streams by codec combo                                               |
| `ignore-trudhd`             | bool   | `true`  | Ignore TrueHD streams                                                                   |
| `atmos-force-copy`          | bool   | `false` | Always copy Atmos tracks                                                                |
| `force-default`             | bool   | `false` | Override source default stream                                                          |
| `relax-to-default`          | bool   | `false` | If preferred language absent, default to any                                            |

### base.audio.sorting

| Option            | Type | Description                                        |
| ----------------- | ---- | -------------------------------------------------- |
| `sorting`         | list | Sort order: `language, channels.d, map, d.comment` |
| `default-sorting` | list | Sort order for default stream selection            |
| `codecs`          | list | Codec priority for sorting                         |

### base.audio.universal

Generates an additional stereo AAC stream for device compatibility.

| Option              | Type | Default | Description                              |
| ------------------- | ---- | ------- | ---------------------------------------- |
| `codec`             | list |         | UA codec (e.g., `aac`). Empty = disabled |
| `channel-bitrate`   | int  | `128`   | Bitrate per channel                      |
| `first-stream-only` | bool | `true`  | Only add UA for first audio stream       |

---

## base.subtitle

| Option                      | Type   | Default    | Description                                                                                                                                                                                                                                                         |
| --------------------------- | ------ | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `codec`                     | list   | `mov_text` | Subtitle codec for text-based subs. `mov_text` is MP4-only; if `converter.output-format` is `mkv` / `webm` and `codec` is left at the default, it is auto-substituted with `[srt]` at startup with a WARNING. Set the field explicitly to silence the substitution. |
| `codec-image-based`         | list   |            | Codec for image-based subs (PGS, VobSub)                                                                                                                                                                                                                            |
| `languages`                 | list   |            | Language whitelist (ISO 639-3)                                                                                                                                                                                                                                      |
| `default-language`          | string | `eng`      | Default for unlabeled subs                                                                                                                                                                                                                                          |
| `first-stream-of-language`  | bool   | `false`    | One subtitle per language                                                                                                                                                                                                                                           |
| `burn-subtitles`            | bool   | `false`    | Burn subtitles into video                                                                                                                                                                                                                                           |
| `burn-dispositions`         | list   | `forced`   | Only burn subs with these dispositions                                                                                                                                                                                                                              |
| `embed-subs`                | bool   | `true`     | Embed subtitle streams in output                                                                                                                                                                                                                                    |
| `embed-image-subs`          | bool   | `false`    | Embed image-based subs                                                                                                                                                                                                                                              |
| `embed-only-internal-subs`  | bool   | `false`    | Only embed subs from source (no external files)                                                                                                                                                                                                                     |
| `ignored-dispositions`      | list   |            | Skip subs with these dispositions                                                                                                                                                                                                                                   |
| `remove-bitstream-subs`     | list   | `true`     | Remove bitstream subtitle formats                                                                                                                                                                                                                                   |
| `include-original-language` | bool   | `false`    | Include original language subs                                                                                                                                                                                                                                      |

### base.subtitle.cleanit

| Option        | Type | Description                           |
| ------------- | ---- | ------------------------------------- |
| `enabled`     | bool | Enable subtitle cleaning via cleanit  |
| `config-path` | path | Custom cleanit config                 |
| `tags`        | list | Cleanit tag sets: `default, no-style` |

### base.subtitle.ffsubsync

| Option    | Type | Description                        |
| --------- | ---- | ---------------------------------- |
| `enabled` | bool | Enable subtitle sync via ffsubsync |

### base.subtitle.subliminal

| Option                           | Type | Description                         |
| -------------------------------- | ---- | ----------------------------------- |
| `download-subs`                  | bool | Download missing subtitles          |
| `providers`                      | list | Subtitle providers: `opensubtitles` |
| `download-forced-subs`           | bool | Download forced subtitle variants   |
| `download-hearing-impaired-subs` | bool | Include HI subs in downloads        |

---

## base.metadata

| Option             | Type   | Default | Description                                           |
| ------------------ | ------ | ------- | ----------------------------------------------------- |
| `relocate-moov`    | bool   | `true`  | Move moov atom to file start (streaming optimization) |
| `full-path-guess`  | bool   | `true`  | Use full file path for guessit metadata matching      |
| `tag`              | bool   | `true`  | Enable TMDB metadata tagging                          |
| `tag-language`     | string | `eng`   | Language for TMDB metadata                            |
| `download-artwork` | bool   | `false` | Embed cover art from TMDB                             |
| `strip-metadata`   | bool   | `true`  | Remove existing metadata before tagging               |
| `keep-titles`      | bool   | `false` | Preserve original stream titles                       |

---

## base.permissions

| Option  | Type  | Default | Description                 |
| ------- | ----- | ------- | --------------------------- |
| `chmod` | octal | `0664`  | File permissions for output |
| `uid`   | int   | `-1`    | Owner UID (-1 = no change)  |
| `gid`   | int   | `-1`    | Group GID (-1 = no change)  |

---

## profiles

Named overlays applied per-section on top of `base`. Sections the
profile does not mention pass through unchanged. Profiles are applied
either via `manual.py --profile <name>` or auto-resolved from a
`daemon.routing[].profile` field for a given input path.

```yaml
profiles:
  rq:
    video:
      codec: [h265]
      max-bitrate: 8000
    audio:
      codec: [ac3, aac]
  lq:
    video:
      codec: [h264]
      max-bitrate: 3000
      preset: fast
    audio:
      codec: [aac]
      max-channels: 2
```

Overlay semantics are shallow per top-level section: the overlay's
`video:` block replaces only the keys it sets (others come from
`base.video`); the overlay's `audio:` block similarly replaces only its
own keys. Sections the profile omits inherit from `base` untouched.

### profiles.\<name\>.max-concurrent

Optional cluster-wide cap on the number of jobs with this profile that
may run simultaneously. Enforced at claim time by the daemon's
`claim_next_job` against the live `jobs` table — when the cap is reached
a pending job with this profile is skipped until a running peer finishes.

| Key              | Type | Default     | Notes                                                                  |
| ---------------- | ---- | ----------- | ---------------------------------------------------------------------- |
| `max-concurrent` | int  | unlimited   | `null` or `<=0` disables the cap. Counted across every daemon node.    |

Typical use is the 4K HDR profile (`hq`), where running multiple
transcodes in parallel saturates the GPU encoder and chews through the
output filesystem faster than the storage janitor can sweep it:

```yaml
profiles:
  hq:
    max-concurrent: 1
    video:
      max-bitrate: 18000
```

### profiles.\<name\>.concurrency-cost + daemon.concurrency-budget

`max-concurrent` enforces a hard per-profile slot count. Pair it (or
replace it) with the **weighted-budget scheduler** when you want
profiles to share a single per-node encoder-capacity ceiling:

| Key                          | Where        | Type | Default          | Notes                                                                                                                          |
| ---------------------------- | ------------ | ---- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `concurrency-cost`           | per profile  | int  | `1`              | Weight this profile carries against the budget. `<=0` is treated as `1`.                                                       |
| `daemon.concurrency-budget`  | per node     | int  | `daemon.workers` | Per-node ceiling. `null` / `<=0` resolves to `workers` so a zero-config install behaves identically to a no-budget deployment. |

Semantics: every running job's `concurrency-cost` is summed. A new
claim is refused when `sum + this_job.cost > budget`. The advisory
lock that serialises per-profile cap counting (see
`docs/daemon.md` → "Profile concurrency caps") also serialises the
cost-sum, so two concurrent claims can't both pass the budget check
on the same available slot.

Both gates compose — the tighter wins. Use `max-concurrent` for caps
that are *not* encoder-driven (e.g. "never two hq because the
output-disk janitor can't keep up"); use `concurrency-cost` +
`concurrency-budget` for the encoder-bandwidth share. They live side by
side on the same profile.

Example for a 3-worker Meteor Lake node where one 4K transcode equals
the encoder load of three 1080p HEVC slower-preset transcodes or six
1080p VDENC speed-first transcodes:

```yaml
daemon:
  workers: 3
  concurrency-budget: 6        # per-node encoder-capacity ceiling

profiles:
  hq:
    max-concurrent: 1          # hard secondary ceiling
    concurrency-cost: 6        # 1 hq saturates the budget
  rq:
    concurrency-cost: 2        # 3 rq fills the budget
  lq:
    concurrency-cost: 1        # 6 lq fills the budget (capped at workers=3)
```

Misconfiguration is caught at startup: if any profile's
`concurrency-cost` exceeds the effective budget, the daemon refuses
to start with a structured error naming the offending profile and
both values (`profiles.<name>.concurrency-cost=<N> exceeds the
effective daemon concurrency budget (<M>)`).

---

## services.sonarr / services.radarr

Each service type is a map keyed by instance name. The instance name is
referenced from `daemon.routing[].services` as `<type>.<instance>` —
e.g. `sonarr.kids`, `radarr.4k`. The path-prefix derivation that drove
multi-instance matching in the old INI shape is now expressed
explicitly in routing rules.

| Option              | Type   | Default  | Description                                                                                                                                                                                                  |
| ------------------- | ------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `url`               | string | required | Full base URL including scheme and port (e.g. `http://sonarr.local:8989`)                                                                                                                                    |
| `apikey`            | string |          | API key                                                                                                                                                                                                      |
| `force-rename`      | bool   | `false`  | After import, trigger Sonarr/Radarr's own RenameFiles command. When enabled, SMA's naming templates are skipped and the arr instance applies its configured naming format instead. Requires `rescan = true`. |
| `rescan`            | bool   | `true`   | Trigger library rescan after processing                                                                                                                                                                      |
| `block-reprocess`   | bool   | `false`  | Prevent reprocessing same-extension files                                                                                                                                                                    |
| `in-progress-check` | bool   | `true`   | Wait for in-progress scans before rescanning                                                                                                                                                                 |

```yaml
services:
  sonarr:
    main:
      url: http://sonarr.example.com:8989
      apikey: abc123
    kids:
      url: http://sonarr-kids.example.com:8989
      apikey: def456
  radarr:
    main:
      url: http://radarr.example.com:7878
      apikey: ghi789
    4k:
      url: http://radarr-4k.example.com:7878
      apikey: jkl012
```

Hook each instance into routing:

```yaml
daemon:
  routing:
    - match: /mnt/unionfs/Media/TV
      services: [sonarr.main]
    - match: /mnt/unionfs/Media/TV-Kids
      services: [sonarr.kids]
    - match: /mnt/unionfs/Media/Movies
      services: [radarr.main]
    - match: /mnt/unionfs/Media/Movies/4K
      services: [radarr.4k]
```

A service instance not referenced from any routing rule is still loaded
and addressable from CLI tools, but no automatic notify happens for it
(routing is the trigger).

Validation: every `<type>.<instance>` reference in routing must resolve
to an existing entry in `services.<type>.<instance>`. A typo
(`sonarr.kid` vs `sonarr.kids`) fails fast at startup.

---

## services.plex

Plex instances follow the same map-by-name shape. The first instance
named `main` (or otherwise the first defined) is what the conversion
pipeline notifies.

| Option         | Type   | Description                                                              |
| -------------- | ------ | ------------------------------------------------------------------------ |
| `url`          | string | Full base URL including scheme and port (e.g. `http://plex.local:32400`) |
| `token`        | string | Plex authentication token                                                |
| `refresh`      | bool   | Trigger library refresh after processing                                 |
| `ignore-certs` | bool   | Skip SSL certificate verification                                        |
| `path-mapping` | string | Map SMA-NG paths to Plex library paths (`local=remote`, comma-separated) |
| `plexmatch`    | bool   | Write `.plexmatch` files for matched media                               |

```yaml
services:
  plex:
    main:
      url: http://plex.example.com:32400
      token: xxxxxxxxxxxxxxxxxxxx
      refresh: true
```

---

## Downloader integrations

Downloader integration (SABnzbd / Deluge / qBittorrent / uTorrent) is
shell-trigger-only and configured under `triggers/`. There is no
corresponding Python config block in `sma-ng.yml`. See
[Integrations](integrations.md) for the trigger setup.

---

## Processing Pipeline

### Video Decision Tree

1. Source codec in allowed list → **copy** (unless overridden by bitrate/width/level/profile/filter)
2. Bitrate exceeds `max-bitrate` (after `bitrate-ratio` scaling) → **re-encode**
3. Width exceeds `max-width` → **re-encode + downscale**
4. Level exceeds `max-level` → **re-encode**
5. Profile not in whitelist → **re-encode**
6. Burn subtitles enabled → **re-encode**
7. Otherwise → **copy**

### Bitrate Calculation

1. `estimateVideoBitrate()` computes source video bitrate from container total minus audio
2. `bitrate-ratio` scales the estimate per source codec (e.g., H.264 at 0.65x for HEVC target)
3. `crf-profiles` selects CRF/maxrate/bufsize tier based on scaled bitrate
4. `max-bitrate` caps the final result

When `[Analyzer]` is enabled, the analyzer may additionally:

1. reorder the configured codec pool before copy/transcode selection
2. scale the planned bitrate with `bitrate_ratio_multiplier`
3. apply a stricter bitrate ceiling than `[Video].max-bitrate`
4. append bounded filters and force re-encode when those filters require it

### Preview Output (`manual.py -oo`)

The JSON preview now includes an `analyzer` object. It may be empty when the analyzer is disabled or produces no bounded recommendations. When populated, it lets you inspect proposed codec ordering, filters, presets, and force-reencode decisions before any conversion runs.

### Audio Decision Tree

1. Filter by `languages` whitelist + `include-original-language`
2. Filter by `ignored-dispositions`
3. Source codec in allowed list → **copy**; otherwise → **re-encode** to first codec
4. Apply channel limits (`max-channels`), bitrate limits (`max-bitrate`)
5. Sort by `[Audio.Sorting]` rules
6. Select default stream
7. Optionally generate Universal Audio stream (stereo compatibility)
