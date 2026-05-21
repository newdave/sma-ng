# Hardware Acceleration

SMA-NG supports hardware-accelerated video encoding via FFmpeg. The `gpu` setting under `base.converter` selects the encoder backend.

## GPU Auto-Detection

`make config` and `mise run config:generate` call the same generator, auto-detect the GPU the same way, and write the correct value to the generated `sma-ng.yml`:

- macOS → `videotoolbox`
- NVIDIA GPU (detected via `nvidia-smi`) → `nvenc`
- Intel iGPU (detected via `/sys/module/i915` or `vainfo`) → `qsv`
- Generic VA-API device (typically `/dev/dri/renderD128`) → `vaapi`
- Fallback → software

```bash
make config           # auto-detect and generate config
make config GPU=nvenc  # force NVIDIA
make config GPU=vaapi  # force VA-API
make config GPU=       # force software encoding
make detect-gpu        # show detection result without writing config
```

## Supported Backends

| Value | Hardware | Supported Codecs |
| --- | --- | --- |
| `qsv` | Intel Quick Sync | h264, h265, av1, vp9 |
| `vaapi` | Intel / AMD VA-API (Linux) | h264, h265, av1 |
| `nvenc` | NVIDIA GPU | h264, h265, av1 |
| `videotoolbox` | Apple Silicon / macOS | h264, h265 |
| *(empty)* | Software (CPU) | all codecs |

---

## Intel QSV

```yaml
base:
  video:
    gpu: qsv
    codec: [h265qsv, h265]
    codec-parameters: '-low_power 1 -async_depth 1 -extbrc 1'
    look-ahead-depth: 16
    b-frames: 3
    ref-frames: 4
```

Supported QSV codecs: `h264qsv`, `h265qsv`, `av1qsv`, `vp9qsv`

`codec-parameters` accepts raw FFmpeg encoder flags. The defaults in `setup/sma-ng.yml.sample` enable QSV low-power mode and extended rate control (`-low_power 1 -async_depth 1 -extbrc 1`). These are automatically cleared at runtime when `gpu` is not `qsv`.

### Auto-populated QSV decoders

Setting `gpu: qsv` (and leaving `base.converter.hwaccel-decoders` empty) auto-populates the hardware decoder list with the safe Intel QSV set: `hevc_qsv`, `h264_qsv`, `vp9_qsv`, `vc1_qsv`. `av1_qsv` is **deliberately omitted** — FFmpeg advertises it on every Intel iGPU, but it only actually works on Arc / Xe2 (DG2 and later); pre-Arc parts (Coffee/Comet/Tiger/Alder/Raptor Lake) crash inside oneVPL when it's selected. Operators on Arc-or-newer hardware can opt in by listing the decoders explicitly:

```yaml
base:
  converter:
    hwaccel-decoders: [hevc_qsv, h264_qsv, vp9_qsv, vc1_qsv, av1_qsv]
```

The auto-fill only runs when `hwaccel-decoders` is empty — an explicit list always wins.

### Tuning QSV HEVC for ICQ + HDR

For best per-frame quality on Intel QSV, prefer ICQ (Intelligent
Constant Quality) over scene-blind VBR. Recommended tuning:

```yaml
base:
  video:
    gpu: qsv
    codec: [h265qsv, h265]
    preset: slower            # QSV presets cost little speed; veryslow if headroom
    look-ahead-depth: 40      # SMA-NG bumps -extra_hw_frames automatically
    global-quality: 23        # ICQ target; 21–25 typical for 1080p HEVC
    crf-profiles: ''          # clear bitrate matching to enable ICQ
    crf-profiles-hd: ''       # same for HD
  hdr:
    pix-fmt: [p010le]         # required for 10-bit HDR passthrough
    primaries: [bt2020]
    transfer: [smpte2084]     # PQ; use [arib-std-b67] for HLG sources
    space: [bt2020nc]
```

- **`preset: slower`** — earlier releases silently dropped QSV presets; SMA-NG now passes them through. `slower` improves quality for a small speed cost on Intel iGPUs.
- **`look-ahead-depth: 40`** — deeper look-ahead helps in ICQ mode. SMA-NG automatically emits `-extra_hw_frames 44` so the device frame pool doesn't run dry.
- **`global-quality: 23`** — direct knob for `-global_quality`. Lower values raise quality; the typical 1080p HEVC sweet spot is 21–25. Ignored when `crf-profiles` or `max-bitrate` set a bitrate target (ICQ and VBR are mutually exclusive).
- **`hdr.pix-fmt: [p010le]`** — required to keep 10-bit HDR passthrough; without it the pipeline truncates to 8-bit.
- **HDR color tags** — when the output is HDR, SMA-NG emits `-color_primaries`, `-color_trc`, and `-colorspace` from `hdr.primaries[0]`, `hdr.transfer[0]`, `hdr.space[0]`. Use `transfer: [arib-std-b67]` for HLG sources so players don't render PQ as washed-out.
- **MKV vs MP4 subtitles** — `mov_text` is MP4-only. If you switch `converter.output-format` to `mkv`, leave `subtitle.codec` at the default and SMA-NG auto-substitutes `[srt]` at startup with a WARNING; set it explicitly to take control.

---

## Intel / AMD VAAPI

```ini
[Video]
gpu = vaapi
codec = h265vaapi, h265
```

Supported VAAPI codecs: `h264vaapi`, `h265vaapi`, `av1vaapi`

---

## NVIDIA NVENC

```ini
[Video]
gpu = nvenc
codec = h265_nvenc, h265
```

Supported NVENC codecs: `h264_nvenc`, `h265_nvenc`, `av1_nvenc`

---

## Apple VideoToolbox

```ini
[Video]
gpu = videotoolbox
codec = h265_videotoolbox, h265
```

No `hwaccels` or `hwdevices` needed — VideoToolbox is built into macOS.

---

## Configuration Rules

- The codec list's first entry is used for encoding; subsequent entries allow stream copying without re-encoding
- CRF is mapped to `-global_quality` for QSV and `-qp` for VAAPI automatically

---

## Startup Validation

At daemon startup, SMA-NG probes each unique config's hardware encoder to verify it's available. If a probe fails, a warning is logged but startup continues:

```text
[DAEMON] Hardware encoder 'hevc_nvenc' validated OK
[DAEMON] WARNING: Hardware encoder 'hevc_qsv' does not appear to be available
```

This runs in the background and does not delay accepting connections.

Use `python daemon.py --smoke-test` to verify that all configured `sma-ng.yml` files load cleanly before starting the server.

## Docker Intel/VAAPI Troubleshooting

If you see errors like:

```text
[VAAPI] No VA display found for device /dev/dri/renderD128
Failed to set value '/dev/dri/renderD128' for option 'qsv_device'
Error parsing global options: Invalid argument
```

check the following:

1. Start an Intel profile so `/dev/dri` is mounted:

```bash
docker compose --profile intel up -d
# or, with bundled PostgreSQL:
docker compose --profile intel-pg up -d
```

1. Verify VAAPI visibility inside the container:

```bash
docker compose exec sma-intel vainfo
```

1. Ensure the container uses Intel's VAAPI driver (`iHD`):

```yaml
environment:
  - LIBVA_DRIVER_NAME=iHD
```

1. Confirm your config/backend alignment:

- `gpu = qsv` should use QSV codecs (`h264qsv`, `h265qsv`, `av1qsv`, `vp9qsv`)
- `gpu = vaapi` should use VAAPI codecs (`h264vaapi`, `h265vaapi`, `av1vaapi`)

1. On KVM or Proxmox guests using Intel SR-IOV, verify the guest-visible DRI topology.

- The usable Intel VF may appear as `card1` with `renderD128`, or as higher-numbered render nodes, depending on the guest.
- The Intel Docker Compose profiles mount the whole `/dev/dri` tree so FFmpeg and VAAPI can see the matching `card*` and `renderD*` nodes together.
- The container's `ubuntu` runtime user joins the host's `video` and `render` groups declaratively via `docker-compose.yml`'s `group_add: [video, render, 992]` stanza. The image's baked-in numeric 992 render GID serves as a fallback for hosts where neither group name resolves.
- Bare-`docker run` users on hosts with non-standard render GIDs can opt back in to the legacy entrypoint behaviour by setting `SMA_ENTRYPOINT_FIX_GIDS=1`. The entrypoint will then stat the mapped `/dev/dri/*` device nodes and add the runtime user to whatever groups own them before dropping privileges via `setpriv`.
- Validate inside the guest first with `ls -l /dev/dri`, then inside the container with `docker compose exec sma-intel vainfo`.

The official Docker image now includes `vainfo` and VAAPI userspace drivers to simplify diagnostics.

---

## Phase 1 observability: `/health`, fallback policy, capability probe

SMA-NG 2.4+ surfaces hardware status as structured fields on the daemon's
`/health` endpoint so operators can answer "is QSV/NVENC actually working
on this node?" from a single `curl`, without grepping daemon logs.

### Capability probe

At daemon startup, `scripts/probe-hw.py` shells out to `vainfo`,
`ffmpeg -hwaccels`, `ffmpeg -encoders`, `ffmpeg -decoders`,
`nvidia-smi -L`, and the `/dev/dri/renderD*` node list, then writes a
typed JSON snapshot to `<config_dir>/cache/hw_capabilities.json`. The
probe is **fail-open** — any subprocess error yields
`gpu_status: unknown` so daemon startup never blocks on it.

The snapshot is regenerated when the cache file predates the current
daemon start time (e.g. after a host kernel or driver upgrade).

### `/health` schema additions

`GET /health` returns three additive top-level fields on top of the
existing payload (consumers MUST ignore unknown keys per
`docs/daemon.md`):

```json
{
  "status": "ok",
  "node": "sma-master",
  "gpu_status": "ok",                       // ok | degraded | unreachable | unknown
  "selected_backend": "qsv",                // qsv | nvenc | vaapi | videotoolbox | software
  "capabilities": {
    "hwaccels": ["qsv", "vaapi"],
    "encoders": {"h264_qsv": true, "hevc_qsv": true},
    "decoders": {"h264_qsv": true},
    "render_nodes": ["/dev/dri/renderD128"],
    "vainfo_driver": "iHD",
    "vainfo_version": "23.4.0",
    "ffmpeg_version": "8.1.1",
    "nvidia": false
  },
  "fallback": [
    {"from": "hw", "to": "sw_decode", "reason": "device_open_failed", "count": 2}
  ]
}
```

`fallback` accumulates per-tier transition counters keyed by
`(from_tier, to_tier, failure_class)`. Counters populate as worker
subprocesses emit the `ffmpeg.attempts` structured log line (single-line
JSON, picked up by the worker stdout parser).

### `fallback-policy` (replaces the deprecated boolean `software-fallback`)

`base.converter.fallback-policy` selects how far the hardware → software
fallback ladder is allowed to descend on failure:

```yaml
base:
  converter:
    fallback-policy: aggressive   # try hw → sw_decode → full_sw (default)
    # fallback-policy: sw_decode_only   # try hw → sw_decode; never swap encoder
    # fallback-policy: hw_only          # surface hw failures immediately; no retries
```

The deprecated boolean form continues to work for one minor release:

| Legacy YAML                 | Equivalent new policy |
| --------------------------- | --------------------- |
| `software-fallback: true`   | `fallback-policy: aggressive` |
| `software-fallback: false`  | `fallback-policy: hw_only` |

Loading a config with the deprecated key emits one `WARNING` log per
daemon startup pointing operators at the new field.

### Why `hw_only` matters

Operators running production QSV nodes typically want hardware failures
to surface **immediately** — a silent CPU fallback masks real
infrastructure problems (lost `/dev/dri` permissions, missing oneVPL
runtime, kernel-driver mismatch). Set `fallback-policy: hw_only` to
fail loudly; the structured log line and `/health` counter still record
the failure class so the alert path is observable.

### Failure-class taxonomy

`/health` and the structured log line classify ffmpeg stderr tails into
six stable buckets (the values are pinned by a regression test; external
dashboards can rely on them):

| Class                | Typical cause                                      |
| -------------------- | -------------------------------------------------- |
| `device_open_failed` | `/dev/dri` permissions, missing libva/oneVPL runtime |
| `decoder_init_failed`| HW decoder rejects this codec/profile (e.g. AV1 on pre-Arc) |
| `encoder_init_failed`| HW encoder rejects pix_fmt / profile / level         |
| `filter_init_failed` | Filter graph build (e.g. `scale_qsv` unavailable)    |
| `runtime_error`      | ffmpeg started OK then failed mid-encode             |
| `other`              | Unrecognised stderr (drift in upstream ffmpeg messages) |

A spike in `other` is the signal to update the classifier in
`resources/processor/failures.py`.
