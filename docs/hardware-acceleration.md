# Hardware Acceleration

SMA-NG supports hardware-accelerated video encoding via FFmpeg. The `gpu` setting in `[Video]` selects the encoder backend.

## GPU Auto-Detection

`make config` (or `mise run config`) auto-detects the GPU and writes the correct value to `autoProcess.ini`:

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
[Converter]
hwaccels = qsv
hwaccel-decoders = hevc_qsv, h264_qsv, vp9_qsv, av1_qsv
hwdevices = qsv:/dev/dri/renderD128
hwaccel-output-format = qsv:qsv

[Video]
gpu = qsv
codec = h265qsv, h265
```

Supported QSV codecs: `h264qsv`, `h265qsv`, `av1qsv`, `vp9qsv`

QSV-specific tuning options:

```ini
[Video]
look-ahead = 20       # look-ahead frames (la_depth)
b-frames = 4          # B-frames
ref-frames = 4        # reference frames
```

---

## Intel / AMD VAAPI

```ini
[Converter]
hwaccels = vaapi
hwaccel-decoders = hevc_vaapi, h264_vaapi
hwdevices = vaapi:/dev/dri/renderD128
hwaccel-output-format = vaapi:vaapi

[Video]
gpu = vaapi
codec = h265vaapi, h265
```

Supported VAAPI codecs: `h264vaapi`, `h265vaapi`, `av1vaapi`

---

## NVIDIA NVENC

```ini
[Converter]
hwaccels = cuda
hwaccel-decoders = hevc_cuvid, h264_cuvid

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

- `hwdevices` format: `type:device_path` where `type` must be a substring of the encoder codec name
- `hwaccel-output-format` format: `hwaccel_name:output_format` (dict, not a bare value)
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
