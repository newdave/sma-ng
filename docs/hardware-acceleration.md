# Hardware Acceleration

SMA-NG supports hardware-accelerated video encoding via FFmpeg. The `gpu` setting in `[Video]` selects the encoder backend.

## GPU Auto-Detection

`make config` and `mise run config` call the same generator, auto-detect the GPU the same way, and write the correct value to the generated `autoProcess*.ini` profiles:

- macOS → `videotoolbox`
- NVIDIA GPU (detected via `nvidia-smi`) → `nvenc`
- Intel iGPU (detected via `/sys/module/i915` or `vainfo`) → `qsv`
- Generic VA-API device (`/dev/dri/renderD128`) → `vaapi`
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

```ini
[Video]
gpu = qsv
codec = h265qsv, h265
codec-parameters = -low_power 1 -async_depth 1 -extbrc 1
look-ahead-depth = 16
b-frames = 3
ref-frames = 4
```

Supported QSV codecs: `h264qsv`, `h265qsv`, `av1qsv`, `vp9qsv`

`codec-parameters` accepts raw FFmpeg encoder flags. The defaults in `setup/autoProcess.ini.sample` enable QSV low-power mode and extended rate control (`-low_power 1 -async_depth 1 -extbrc 1`). These are automatically cleared at runtime when `gpu` is not `qsv`.

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

Use `python daemon.py --smoke-test` to verify that all configured `autoProcess.ini` files load cleanly before starting the server.

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

2. Verify VAAPI visibility inside the container:

```bash
docker compose exec sma-intel vainfo
```

3. Ensure the container uses Intel's VAAPI driver (`iHD`):

```yaml
environment:
	- LIBVA_DRIVER_NAME=iHD
```

4. Confirm your config/backend alignment:

- `gpu = qsv` should use QSV codecs (`h264qsv`, `h265qsv`, `av1qsv`, `vp9qsv`)
- `gpu = vaapi` should use VAAPI codecs (`h264vaapi`, `h265vaapi`, `av1vaapi`)

The official Docker image now includes `vainfo` and VAAPI userspace drivers to simplify diagnostics.
