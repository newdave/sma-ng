# Configuration Reference

Configuration lives in `config/sma-ng.yml` (YAML). Copy from
`setup/sma-ng.yml.sample` or generate it with `make config` /
`mise run config:generate`. Override the path via the `SMA_CONFIG`
environment variable.

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
    - match: /mnt/media/TV
      profile: rq
      services: [sonarr.main]
    - match: /mnt/media/TV/Kids
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

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `ffmpeg` | path | `ffmpeg` | Path to FFmpeg binary |
| `ffprobe` | path | `ffprobe` | Path to FFprobe binary |
| `threads` | int | `0` | FFmpeg threads (0 = auto) |
| `output-directory` | path | | Temporary output location (files moved back after) |
| `output-format` | string | `mp4` | Container format: `mp4`, `mkv`, `mov` |
| `output-extension` | string | `mp4` | Output file extension |
| `temp-extension` | string | | Temporary file extension during conversion |
| `temp-output` | bool | `true` | Use temporary output file during conversion |
| `minimum-size` | int | `0` | Minimum source file size in MB (0 = disabled) |
| `ignored-extensions` | list | `[nfo, ds_store]` | Extensions to skip |
| `copy-to` | path(s) | | Copy output to additional directories |
| `move-to` | path | | Move output to final destination |
| `delete-original` | bool | `true` | Delete source file after successful conversion |
| `recycle-bin` | path | | Copy original here before deleting (only when `delete-original = True`) |
| `process-same-extensions` | bool | `false` | Reprocess files already in output format |
| `bypass-if-copying-all` | bool | `false` | Skip conversion if all streams can be copied |
| `force-convert` | bool | `false` | Force conversion even if codec matches |
| `post-process` | bool | `false` | Run post-process scripts |
| `wait-post-process` | bool | `false` | Wait for post-process scripts to finish |
| `preopts` | list | | Extra FFmpeg options before input |
| `postopts` | list | | Extra FFmpeg options after codec options |

---

## base.video

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `codec` | list | `h265` | Video codecs in priority order. First is used for encoding, rest are copy-eligible |
| `gpu` | string | | Hardware acceleration backend: `qsv`, `vaapi`, `nvenc`, `videotoolbox`, or empty for software |
| `max-bitrate` | int | `0` | Maximum video bitrate in kbps (0 = unlimited) |
| `bitrate-ratio` | dict | | Scale source bitrate per codec: `hevc:1.0, h264:0.65` |
| `crf-profiles` | string | | Tiered bitrate targets by source bitrate. Format: `source_kbps:quality:target:maxrate` (comma-separated). Example: `0:22:3M:6M,8000:22:5M:10M`. Leave blank to use `bitrate-ratio` + `max-bitrate` instead. |
| `crf-profiles-hd` | string | | Same format as `crf-profiles`, applied to sources above 1080p height. Falls back to `crf-profiles` when empty. |
| `preset` | string | | Encoder preset: `ultrafast` to `veryslow` |
| `profile` | list | | Video profile: `main`, `high`, `main10` |
| `max-level` | float | | Maximum H.264/H.265 level (e.g., `5.2`) |
| `max-width` | int | `0` | Maximum output width (0 = no limit) |
| `pix-fmt` | list | | Pixel format whitelist |
| `dynamic-parameters` | bool | `false` | Pass HDR/color metadata to encoder |
| `prioritize-source-pix-fmt` | bool | `true` | Keep source pix_fmt if in whitelist |
| `filter` | string | | Custom FFmpeg video filter |
| `force-filter` | bool | `false` | Force re-encode when filter is set |
| `codec-parameters` | string | | Extra codec params (e.g., `x265-params`) |
| `look-ahead-depth` | int | `0` | Look-ahead frames for rate control (QSV: `la_depth`). `0` = encoder default. |
| `b-frames` | int | `-1` | Number of B-frames. `-1` = encoder default. |
| `ref-frames` | int | `-1` | Number of reference frames. `-1` = encoder default. |

> **Note:** `codec-parameters` values are automatically cleared at runtime when `gpu` is not `qsv`. QSV-specific flags (e.g. `-low_power 1 -extbrc 1`) in the sample are silently ignored by other backends.

---

## base.hdr

Override video settings for HDR content (detected automatically).

| Option | Type | Description |
| --- | --- | --- |
| `codec` | list | Video codec for HDR content |
| `pix-fmt` | list | Pixel format for HDR (e.g., `p010le`) |
| `space` | list | Color space: `bt2020nc` |
| `transfer` | list | Transfer function: `smpte2084` |
| `primaries` | list | Color primaries: `bt2020` |
| `preset` | string | Encoder preset override for HDR |
| `profile` | string | Profile override for HDR |
| `codec-parameters` | string | Extra params for HDR encoding |
| `filter` | string | Video filter for HDR content |
| `force-filter` | bool | Force re-encode for HDR filter |
| `look-ahead-depth` | int | Look-ahead depth override for HDR encoding (default: `0`) |
| `b-frames` | int | B-frames override for HDR encoding (default: `-1` = encoder default) |
| `ref-frames` | int | Reference frames override for HDR encoding (default: `-1` = encoder default) |

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

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `enabled` | bool | `false` | Enable analyzer-assisted planning |
| `backend` | string | `openvino` | Analyzer backend runtime. Current supported value: `openvino` |
| `device` | string | `AUTO` | OpenVINO target device selector. Valid examples: `AUTO`, `CPU`, `GPU`, `NPU`, `AUTO:NPU,CPU`, `MULTI:NPU,GPU` |
| `model-dir` | path | | Optional directory containing analyzer models (reserved for richer future inference) |
| `cache-dir` | path | | Optional cache directory for compiled analyzer artifacts |
| `max-frames` | int | `12` | Reserved sampling limit for future model-backed inference |
| `target-width` | int | `960` | Reserved downscale width for future model-backed inference |
| `allow-codec-reorder` | bool | `true` | Allow analyzer to reorder the configured video codec pool |
| `allow-bitrate-adjustments` | bool | `true` | Allow analyzer to change bitrate multipliers / ceilings |
| `allow-preset-adjustments` | bool | `true` | Allow analyzer to override encoder preset |
| `allow-filter-adjustments` | bool | `true` | Allow analyzer to add bounded FFmpeg filters such as deinterlace/crop/denoise |
| `allow-force-reencode` | bool | `true` | Allow analyzer to force re-encode when copy would otherwise be selected |

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

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `codec` | list | `aac` | Audio codecs in priority order. Matching streams are copied; others re-encoded to first |
| `languages` | list | | Language whitelist (ISO 639-3, e.g., `eng`). Empty = all |
| `default-language` | string | `eng` | Default language for unlabeled streams |
| `first-stream-of-language` | bool | `false` | Keep only first stream per language |
| `allow-language-relax` | bool | `true` | If no whitelisted language found, keep all audio |
| `include-original-language` | bool | `false` | Include original media language even if not in whitelist |
| `channel-bitrate` | int | `128` | Bitrate per channel in kbps (0 = auto) |
| `variable-bitrate` | int | `0` | VBR quality level (0 = disabled/CBR) |
| `max-bitrate` | int | `0` | Maximum audio bitrate in kbps |
| `max-channels` | int | `0` | Maximum audio channels (0 = unlimited, 6 = 5.1) |
| `copy-original` | bool | `false` | Copy original audio stream in addition to transcoded |
| `aac-adtstoasc` | bool | `true` | Apply AAC ADTS to ASC bitstream filter |
| `ignored-dispositions` | list | | Skip streams with these dispositions: `comment`, `hearing_impaired` |
| `unique-dispositions` | bool | `false` | One stream per disposition per language |
| `stream-codec-combinations` | list | | Identify duplicate streams by codec combo |
| `ignore-trudhd` | bool | `true` | Ignore TrueHD streams |
| `atmos-force-copy` | bool | `false` | Always copy Atmos tracks |
| `force-default` | bool | `false` | Override source default stream |
| `relax-to-default` | bool | `false` | If preferred language absent, default to any |

### base.audio.sorting

| Option | Type | Description |
| --- | --- | --- |
| `sorting` | list | Sort order: `language, channels.d, map, d.comment` |
| `default-sorting` | list | Sort order for default stream selection |
| `codecs` | list | Codec priority for sorting |

### base.audio.universal

Generates an additional stereo AAC stream for device compatibility.

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `codec` | list | | UA codec (e.g., `aac`). Empty = disabled |
| `channel-bitrate` | int | `128` | Bitrate per channel |
| `first-stream-only` | bool | `true` | Only add UA for first audio stream |

---

## base.subtitle

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `codec` | list | `mov_text` | Subtitle codec for text-based subs |
| `codec-image-based` | list | | Codec for image-based subs (PGS, VobSub) |
| `languages` | list | | Language whitelist (ISO 639-3) |
| `default-language` | string | `eng` | Default for unlabeled subs |
| `first-stream-of-language` | bool | `false` | One subtitle per language |
| `burn-subtitles` | bool | `false` | Burn subtitles into video |
| `burn-dispositions` | list | `forced` | Only burn subs with these dispositions |
| `embed-subs` | bool | `true` | Embed subtitle streams in output |
| `embed-image-subs` | bool | `false` | Embed image-based subs |
| `embed-only-internal-subs` | bool | `false` | Only embed subs from source (no external files) |
| `ignored-dispositions` | list | | Skip subs with these dispositions |
| `remove-bitstream-subs` | list | `true` | Remove bitstream subtitle formats |
| `include-original-language` | bool | `false` | Include original language subs |

### base.subtitle.cleanit

| Option | Type | Description |
| --- | --- | --- |
| `enabled` | bool | Enable subtitle cleaning via cleanit |
| `config-path` | path | Custom cleanit config |
| `tags` | list | Cleanit tag sets: `default, no-style` |

### base.subtitle.ffsubsync

| Option | Type | Description |
| --- | --- | --- |
| `enabled` | bool | Enable subtitle sync via ffsubsync |

### base.subtitle.subliminal

| Option | Type | Description |
| --- | --- | --- |
| `download-subs` | bool | Download missing subtitles |
| `providers` | list | Subtitle providers: `opensubtitles` |
| `download-forced-subs` | bool | Download forced subtitle variants |
| `download-hearing-impaired-subs` | bool | Include HI subs in downloads |

---

## base.metadata

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `relocate-moov` | bool | `true` | Move moov atom to file start (streaming optimization) |
| `full-path-guess` | bool | `true` | Use full file path for guessit metadata matching |
| `tag` | bool | `true` | Enable TMDB metadata tagging |
| `tag-language` | string | `eng` | Language for TMDB metadata |
| `download-artwork` | bool | `false` | Embed cover art from TMDB |
| `strip-metadata` | bool | `true` | Remove existing metadata before tagging |
| `keep-titles` | bool | `false` | Preserve original stream titles |

---

## base.permissions

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `chmod` | octal | `0664` | File permissions for output |
| `uid` | int | `-1` | Owner UID (-1 = no change) |
| `gid` | int | `-1` | Group GID (-1 = no change) |

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

---

## services.sonarr / services.radarr

Each service type is a map keyed by instance name. The instance name is
referenced from `daemon.routing[].services` as `<type>.<instance>` —
e.g. `sonarr.kids`, `radarr.4k`. The path-prefix derivation that drove
multi-instance matching in the old INI shape is now expressed
explicitly in routing rules.

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `url` | string | required | Full base URL including scheme and port (e.g. `http://sonarr.local:8989`) |
| `apikey` | string | | API key |
| `force-rename` | bool | `false` | After import, trigger Sonarr/Radarr's own RenameFiles command. When enabled, SMA's naming templates are skipped and the arr instance applies its configured naming format instead. Requires `rescan = true`. |
| `rescan` | bool | `true` | Trigger library rescan after processing |
| `block-reprocess` | bool | `false` | Prevent reprocessing same-extension files |
| `in-progress-check` | bool | `true` | Wait for in-progress scans before rescanning |

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
    - match: /mnt/media/TV
      services: [sonarr.main]
    - match: /mnt/media/TV-Kids
      services: [sonarr.kids]
    - match: /mnt/media/Movies
      services: [radarr.main]
    - match: /mnt/media/Movies/4K
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

| Option | Type | Description |
| --- | --- | --- |
| `url` | string | Full base URL including scheme and port (e.g. `http://plex.local:32400`) |
| `token` | string | Plex authentication token |
| `refresh` | bool | Trigger library refresh after processing |
| `ignore-certs` | bool | Skip SSL certificate verification |
| `path-mapping` | string | Map SMA-NG paths to Plex library paths (`local=remote`, comma-separated) |
| `plexmatch` | bool | Write `.plexmatch` files for matched media |

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
