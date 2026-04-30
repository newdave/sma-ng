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
- If `card*` is owned by the host `video` group, set `VIDEO_GID` in `docker/.env` so the container can open the DRM node during `vainfo` and QSV initialization.
- Validate inside the guest first with `ls -l /dev/dri`, then inside the container with `docker compose exec sma-intel vainfo`.

The official Docker image now includes `vainfo` and VAAPI userspace drivers to simplify diagnostics.
