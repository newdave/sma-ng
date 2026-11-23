# Hardware Acceleration

SMA-NG can encode video on a GPU through FFmpeg. The `gpu` setting under `base.video` selects the backend.
Everything else is derived from it, so `gpu: qsv` on its own is enough to enable the whole QSV chain.

| Setting | Block | Purpose |
| --- | --- | --- |
| `gpu` | `base.video` | Selects the backend: `qsv`, `vaapi`, `nvenc`, `videotoolbox`, or empty for software |
| `codec`, `codec-parameters`, `preset`, `global-quality` | `base.video` | Encoder choice and tuning |
| `qsv:` and `vaapi:` subblocks | `base.video` and `base.hdr` | Encoder-specific options, used only by the matching encoder |
| `hwaccels`, `hwaccel-decoders`, `hwdevices`, `hwaccel-output-format` | `base.converter` | Hardware decode and device settings |
| `fallback-policy` | `base.converter` | How far a failed hardware encode may fall back; see [below](#fallback-policy-replaces-the-deprecated-boolean-software-fallback) |

When `gpu` is set, SMA-NG fills any of the four `base.converter` hardware settings that you left empty, and it
maps generic codec names to the encoder of the backend. For example, `codec: [h265]` with `gpu: qsv` becomes
`[h265qsv, h265]`: the software encoder stays in the list as a stream-copy and fallback entry. A setting that you
write explicitly always wins over the derived value.

SMA-NG supports Intel QSV, VAAPI, NVIDIA NVENC and Apple VideoToolbox. AMD AMF is not supported; on AMD GPUs use
VAAPI.

The Docker image builds FFmpeg 8.1.2 (`FFMPEG_VERSION` in `docker/Dockerfile`) with VAAPI and NVENC support, and
with Intel QSV (oneVPL) on amd64.

When a hardware encode fails, the default `fallback-policy: aggressive` retries along a ladder that ends in a
software encode, so the job still finishes. See [Full-software fallback](configuration.md#full-software-fallback)
and [`fallback-policy`](#fallback-policy-replaces-the-deprecated-boolean-software-fallback) below.

## GPU Auto-Detection

`mise run config:generate` copies `setup/sma-ng.yml.sample` to `config/sma-ng.yml` and sets `base.video.gpu` from
`scripts/detect-gpu.sh`. It does nothing if `config/sma-ng.yml` already exists. Detection checks, in order:

1. macOS on Apple Silicon: `videotoolbox`
2. a working `nvidia-smi`: `nvenc`
3. `vainfo` succeeds and reports an Intel driver: `qsv`
4. `/dev/dri/renderD128` exists and `vainfo` succeeds: `vaapi`
5. otherwise: software (`gpu` stays empty)

```bash
mise run config:generate              # auto-detect and generate config
GPU=nvenc mise run config:generate    # force NVIDIA
GPU=vaapi mise run config:generate    # force VAAPI
GPU=software mise run config:generate # force software encoding
mise run config:gpu                   # show the detection result without writing config
```

## Supported Backends

| `gpu` value | Hardware | Encoder codec names (`python manual.py -cl`) |
| --- | --- | --- |
| `qsv` | Intel Quick Sync | `h264qsv`, `h265qsv` (or `hevc_qsv`), `av1qsv`, `vp9qsv` |
| `vaapi` | Intel or AMD through VAAPI (Linux) | `h264vaapi`, `h265vaapi` (or `hevc_vaapi`), `av1vaapi` |
| `nvenc` | NVIDIA GPU | `h264_nvenc`, `h265_nvenc` (or `hevc_nvenc`), `av1nvenc` |
| `videotoolbox` | Apple Silicon (macOS) | `h264_videotoolbox`, `h265_videotoolbox` |
| empty | Software (CPU) | `h264`, `h265`, `av1`, `svtav1`, `vp9`, and others |

The first entry of `codec` is the encoder. Later entries are formats that may be stream-copied instead of
re-encoded.

## Quality Control Without a Bitrate Target

All hardware encoders use constant-quality rate control when no bitrate target applies:

| Encoder family | Flag | Default value |
| --- | --- | --- |
| QSV (H.264, HEVC, AV1, VP9) | `-global_quality` (ICQ) | 25 |
| VAAPI H.264 and HEVC | `-qp` (CQP) | 23 |
| VAAPI AV1 | `-rc_mode CQP -global_quality` (0 to 255 scale) | 110 |
| NVENC H.264 and HEVC | `-qp` | 23 |
| NVENC AV1 | `-qp` (0 to 255 scale) | 110 |
| VideoToolbox | `-q:v` (Apple Silicon only) | 65 |

`global-quality` sets the value for the QSV encoders and for VAAPI AV1. For VAAPI AV1, the 1 to 51 value is
converted to the encoder's 0 to 255 scale. The VAAPI H.264 and HEVC encoders and the NVENC encoders always use
the default in the table. A bitrate target (VBR) and constant quality are mutually exclusive: if a bitrate target
is set, `global-quality` is ignored.

When `global-quality` is set, a re-encode that is not driven by bitrate (for example a pixel-format change, a
profile change or `force-reencode`) drops the computed bitrate target and uses constant quality, so a starved
source is not re-encoded at its own starved bitrate. Re-encodes driven by `max-bitrate`, a `crf-profiles` match or an analyzer
bitrate ceiling keep their bitrate target.

When the source bitrate cannot be determined (for example some MKV files without bitrate tags), SMA-NG sets no
bitrate target and lets constant quality drive the encode:

- Software x264 and x265, and VideoToolbox on Apple Silicon, keep a `max-bitrate` ceiling as a capped CRF.
- Other encoders drop the VBV `maxrate` and `bufsize` values, because they reject or ignore VBV without a bitrate.
- Encoders that have no constant-quality mode (VideoToolbox on Intel Macs, V4L2 M2M, OMX) get a conservative
  target by output height: 20000 kbps for 2160p, 10000 for 1440p, 6000 for 1080p, 3000 for 720p and 1500 below
  that, never above `max-bitrate`.

## Bit Depth and Profile Matching

SMA-NG keeps the output bit depth compatible with the encoder:

- If the encoder cannot produce the source bit depth, the pixel format is changed to the nearest one that the
  encoder supports (`Pix-fmt adjusted to ...` in the log).
- The encoder profile is matched to the output bit depth before FFmpeg starts. A `main10` profile on an 8-bit
  output is downgraded to `main` (H.264: `high10` to `high`), and a `main` profile on a 10-bit output is upgraded
  to `main10`. The log line carries the tag `[adaptive-profile-bit-depth]`.
- A profile that does not belong to the codec family, such as an H.264 profile left over on an HEVC encode, is
  replaced by the first listed profile that fits (`[adaptive-profile-family]`).
- If your `profile` list already contains a profile for the output depth, SMA-NG uses that one.

This prevents `encoder_init_failed` errors from the QSV and VAAPI encoders, which refuse a profile whose bit depth
does not match the surface format.

---

## Intel QSV

```yaml
base:
  video:
    gpu: qsv
    codec: [h265qsv, h265]
    look-ahead-depth: 16
    b-frames: 3
    ref-frames: 4
```

Supported QSV encoders: `h264qsv`, `h265qsv`, `av1qsv`, `vp9qsv`.

### Encoder options

Tune the QSV encoder with typed fields in `base.video.qsv` (and `base.hdr.qsv` for HDR sources). A field at its
default value is not sent to FFmpeg, and `setup/sma-ng.yml.sample` leaves all of them at their defaults.

| Field | FFmpeg flag | Unset value |
| --- | --- | --- |
| `low-power` | `-low_power` (shared with VAAPI) | `-1` |
| `async-depth` | `-async_depth` (shared with VAAPI) | `0` |
| `extbrc` | `-extbrc` | `-1` |
| `b-strategy` | `-b_strategy` | `-1` |
| `adaptive-i` | `-adaptive_i` | `-1` |
| `adaptive-b` | `-adaptive_b` | `-1` |
| `p-strategy` | `-p_strategy` | `-1` |
| `rdo` | `-rdo` | `-1` |
| `codec-parameters` | raw flags, QSV only | empty |

The `qsv` block also accepts `preset`, `b-frames`, `ref-frames`, `global-quality`, `look-ahead-depth` and
`extra-hw-frames`, which override the same fields on `base.video` when set.

```yaml
base:
  video:
    gpu: qsv
    qsv:
      low-power: 1
      async-depth: 1
      extbrc: 1
```

Details about the flags:

- `-low_power` and `-async_depth` are valid on both the QSV and the VAAPI encoder. They are shared flags, so a raw
  `-low_power 1` in `base.video.codec-parameters` stays on the command line for `gpu: qsv` and for `gpu: vaapi`.
- The remaining QSV flags (`-extbrc`, `-b_strategy`, `-adaptive_i`, `-adaptive_b`, `-p_strategy`, `-rdo`,
  `-look_ahead`, `-look_ahead_depth`) are QSV only. If you write them in `base.video.codec-parameters`, SMA-NG
  moves them into the `qsv` block when it loads the config, and a VAAPI encoder never receives them.
- SMA-NG adds `-extbrc 1` automatically for bitrate targets, because it is what makes `-look_ahead_depth` work on
  `hevc_qsv`.
- `look-ahead-depth: N` emits `-look_ahead 1 -look_ahead_depth N`. When it is 0, `-look_ahead 0` is sent.
- An encoder drops flags that belong to the other hardware family and logs one warning, instead of failing the
  encode.

### Auto-populated QSV decoders

With `gpu: qsv` and an empty `base.converter.hwaccel-decoders`, SMA-NG uses the safe Intel decoder set
`hevc_qsv`, `h264_qsv`, `vp9_qsv` and `vc1_qsv`, together with `hwaccels: [qsv]`, the device
`/dev/dri/renderD128` and the `qsv` output format.

`av1_qsv` is deliberately omitted. FFmpeg advertises it on every Intel iGPU, but it works only on Arc and Xe2
(DG2 and later). Older parts (Coffee, Comet, Tiger, Alder and Raptor Lake) crash inside oneVPL when it is
selected. If you run Arc or newer hardware, opt in by listing the decoders yourself:

```yaml
base:
  converter:
    hwaccel-decoders: [hevc_qsv, h264_qsv, vp9_qsv, vc1_qsv, av1_qsv]
```

An explicit list always wins over the automatic one.

### Frame pool (`-extra_hw_frames`)

QSV needs spare hardware frames for look-ahead. SMA-NG passes `-extra_hw_frames` at input scope, using
`max(20, look-ahead-depth + 4)`, capped at 100. Set `extra-hw-frames` on `base.video` (or `base.hdr`) to choose a
value yourself.

### Tuning QSV HEVC for ICQ and HDR

Intelligent Constant Quality (ICQ) gives better per-frame quality than VBR that ignores scene content:

```yaml
base:
  video:
    gpu: qsv
    codec: [h265qsv, h265]
    preset: slower            # QSV presets cost little speed; veryslow if you have headroom
    look-ahead-depth: 40      # SMA-NG raises -extra_hw_frames to 44 automatically
    global-quality: 23        # ICQ target; 21 to 25 is typical for 1080p HEVC
    crf-profiles: ''          # no bitrate ladder, so quality drives the encode
    crf-profiles-hd: ''
  hdr:
    pix-fmt: [p010le]         # keep 10-bit for HDR
    primaries: [bt2020]
    transfer: [smpte2084]     # PQ; use [arib-std-b67] for HLG sources
    space: [bt2020nc]
```

- `preset: slower` is passed through to the QSV encoder (`veryfast` to `veryslow`).
- `look-ahead-depth: 40` helps most in ICQ mode.
- `global-quality: 23` is a direct `-global_quality` value. Lower values give higher quality.
- `hdr.pix-fmt: [p010le]` keeps 10-bit HDR; without it the pipeline can fall back to 8-bit.
- For HDR output, SMA-NG sets `-color_primaries`, `-color_trc` and `-colorspace` from the first entry of
  `hdr.primaries`, `hdr.transfer` and `hdr.space`. Use `transfer: [arib-std-b67]` for HLG sources.
- `mov_text` subtitles work only in MP4. If you set `converter.output-format: mkv` and leave `subtitle.codec`
  at its default, SMA-NG substitutes `[srt]` at startup and logs a warning. Set the codec explicitly to avoid the
  warning.

---

## Intel and AMD VAAPI

```yaml
base:
  video:
    gpu: vaapi
    codec: [h265vaapi, h265]
```

Supported VAAPI encoders: `h264vaapi`, `h265vaapi`, `av1vaapi`. With `gpu: vaapi`, SMA-NG uses the decoders
`hevc_vaapi` and `h264_vaapi` and the device `/dev/dri/renderD128`.

Tune the encoder with `base.video.vaapi` (and `base.hdr.vaapi`):

| Field | FFmpeg flag | Unset value |
| --- | --- | --- |
| `low-power` | `-low_power` (shared with QSV) | `-1` |
| `async-depth` | `-async_depth` (shared with QSV) | `0` |
| `rc-mode` | `-rc_mode` (for example `VBR`) | empty |
| `compression-level` | `-compression_level` | `0` |
| `codec-parameters` | raw flags, VAAPI only | empty |

The block also accepts `preset`, `b-frames`, `ref-frames`, `global-quality`, `max-level` and `look-ahead-depth`.
SMA-NG reads the `vaapi` block only when the encoder that runs is a VAAPI encoder. A `vaapi:` block under
`gpu: qsv` therefore has no effect until the `hw_alt` fallback tier swaps the encoder (see below).

---

## NVIDIA NVENC

```yaml
base:
  video:
    gpu: nvenc
    codec: [h265_nvenc, h265]
```

Supported NVENC encoders: `h264_nvenc`, `h265_nvenc`, `av1nvenc`. With `gpu: nvenc`, SMA-NG uses the `cuda`
hwaccel and the `cuvid` decoders (`hevc_cuvid`, `h264_cuvid`, `vp9_cuvid`, `av1_cuvid`, `vc1_cuvid`).
`h264_nvenc_cuda` and `h265_nvenc_cuda` are variants that scale with `scale_cuda` instead of `scale_npp`.
NVENC takes constant quality through `-qp`. It has no `qsv:` or `vaapi:` subblock.

---

## Apple VideoToolbox

```yaml
base:
  video:
    gpu: videotoolbox
    codec: [h265_videotoolbox, h265]
```

Supported encoders: `h264_videotoolbox`, `h265_videotoolbox`. VideoToolbox is built into macOS, so no `hwdevices`
setting is needed. On Apple Silicon, the encoder runs in constant-quality mode (`-q:v 65`) when there is no
bitrate target. On Intel Macs FFmpeg does not support `-q:v`, so SMA-NG always sets a bitrate target there.

---

## Configuration Rules

- The first entry of `codec` is the encoder; later entries allow stream copy without re-encoding.
- Generic names (`h265`, `hevc`, `h264`, `av1`) are mapped to the encoder of the selected backend, and the
  original name stays in the list.
- Hardware decode is used only when the decoder is in `hwaccel-decoders` and supports the source bit depth.

---

## Startup Validation

When the daemon starts, it runs a short test encode for each hardware backend that a loaded config uses (the
H.264 encoder of that backend). A failed test logs a warning, and startup continues:

```text
Hardware encoder 'h264_qsv' validated OK
Hardware encoder 'h264_qsv' (from config sma-ng.yml) does not appear to be available. Conversions may fail. Check driver/SDK installation.
```

The test runs in the background and does not delay accepting connections.

Use `python daemon.py --smoke-test` to verify that every configured `sma-ng.yml` loads and generates options
before you start the server.

## Docker Intel/VAAPI Troubleshooting

If you see errors like these:

```text
[VAAPI] No VA display found for device /dev/dri/renderD128
Failed to set value '/dev/dri/renderD128' for option 'qsv_device'
Error parsing global options: Invalid argument
```

check the following:

1. Start an Intel profile so that `/dev/dri` is mounted:

   ```bash
   docker compose --profile intel up -d
   # or, with an external PostgreSQL (daemon.db-url):
   docker compose --profile intel-pg up -d
   ```

2. Verify VAAPI visibility inside the container:

   ```bash
   docker compose exec sma-intel vainfo
   ```

3. Make sure the container uses Intel's VAAPI driver (`iHD`):

   ```yaml
   environment:
     - LIBVA_DRIVER_NAME=iHD
   ```

4. Confirm that the backend and the codecs match:
   - `base.video.gpu: qsv` needs QSV codecs (`h264qsv`, `h265qsv`, `av1qsv`, `vp9qsv`).
   - `base.video.gpu: vaapi` needs VAAPI codecs (`h264vaapi`, `h265vaapi`, `av1vaapi`).

5. On KVM or Proxmox guests that use Intel SR-IOV, verify the DRI topology that the guest sees. The usable virtual
   function can appear as `card1` with `renderD128`, or as higher-numbered render nodes, depending on the guest.
   Validate with `ls -l /dev/dri` in the guest, then `docker compose exec sma-intel vainfo` in the container.

The Intel Compose profiles mount the whole `/dev/dri` tree, so FFmpeg and VAAPI see the matching `card*` and
`renderD*` nodes together. The container's runtime user joins the host's `video` and `render` groups through
`group_add: [video, render, 992]` in `docker/docker-compose.yml`; the numeric GID 992 is a fallback for hosts where
neither group name resolves. If you run the image with plain `docker run` on a host with a different render GID,
set `SMA_ENTRYPOINT_FIX_GIDS=1`. The entrypoint then reads the group of each mapped `/dev/dri/*` node and adds the
runtime user to it before it drops privileges.

The Docker image includes `vainfo` and the VAAPI userspace drivers for diagnostics.

---

## Phase 1 observability: `/health`, fallback policy, capability probe

The daemon reports hardware status on `GET /health`, so you can check whether QSV or NVENC works on a node with a
single `curl` instead of reading logs.

### Capability probe

At daemon start, `scripts/probe-hw.py` runs `vainfo`, `ffmpeg -hwaccels`, `ffmpeg -encoders`,
`ffmpeg -decoders` and `nvidia-smi -L`, lists the `/dev/dri/renderD*` nodes, and writes a JSON snapshot to
`<config_dir>/cache/hw_capabilities.json`. The probe is limited to 15 seconds. If it fails or times out, the
daemon still starts and `gpu_status` is `unknown`.

### `/health` fields

Authenticated callers get four additional top-level fields: `gpu_status`, `selected_backend`, `capabilities` and
`fallback`. Callers without credentials on an auth-enabled daemon get a minimal liveness view without them.
Clients must ignore unknown keys (see [Daemon Mode](daemon.md)).

```json
{
  "status": "ok",
  "node": "sma-master",
  "gpu_status": "ok",
  "selected_backend": "qsv",
  "capabilities": {
    "hwaccels": ["qsv", "vaapi"],
    "encoders": {"h264_qsv": true, "hevc_qsv": true},
    "decoders": {"h264_qsv": true},
    "render_nodes": ["/dev/dri/renderD128"],
    "vainfo_driver": "iHD",
    "vainfo_version": "23.4.0",
    "ffmpeg_version": "8.1.2",
    "nvidia": false
  },
  "fallback": [
    {"from": "hw", "to": "sw_decode", "reason": "device_open_failed", "count": 2}
  ]
}
```

- `gpu_status` is `ok`, `degraded` (FFmpeg works but `vainfo` or `nvidia-smi` reported a problem),
  `unreachable` (FFmpeg did not respond) or `unknown` (the probe did not run).
- `selected_backend` is `qsv`, `nvenc`, `vaapi`, `videotoolbox` or `software`.
- `fallback` counts tier transitions, keyed by source tier, target tier and failure class. The counters fill as
  worker processes emit the `ffmpeg.attempts` log line (one line of JSON).

The first attempt in `ffmpeg.attempts` is labelled `copy` for a stream-copy video remux, `sw` for a software video
encoder with no hardware decode, and `hw` otherwise. `copy` and `sw` are labels, not fallbacks. Later tiers are
`hw_alt`, `sw_decode_hw_encode`, `sw_decode` and `full_sw`.

A job that uses the output-larger-than-input copy fallback (see the [troubleshooting guide](troubleshooting.md))
emits two `ffmpeg.attempts` lines: one for the re-encode and one for the copy run, which carries
`"copy_fallback": true`. The copy run counts toward the `fallback` counters, but a failed copy run never sets the
job's failure cause, because the job keeps the re-encode and succeeds.

### `fallback-policy` (replaces the deprecated boolean `software-fallback`)

`base.converter.fallback-policy` selects how far a failed hardware encode may fall back. The full ladder, used by
`aggressive`, has four tiers:

1. `hw`: the configured hardware decode and encode.
2. `hw_alt`: for a QSV encode only, swap the encoder to the VAAPI encoder of the same GPU and keep the QSV decoder.
3. `sw_decode`: remove the hardware decoder and keep the hardware encoder.
4. `full_sw`: swap the encoder to software and remove every hardware filter and option.

```yaml
base:
  converter:
    fallback-policy: aggressive        # hw, hw_alt, sw_decode, full_sw (default)
    # fallback-policy: sw_decode_only  # hw, hw_alt, sw_decode; never a software encode
    # fallback-policy: hw_alt          # hw, hw_alt; stop before any software encode
    # fallback-policy: hw_only         # surface hardware failures immediately; no retries
```

| Policy | Ladder | When to use |
| --- | --- | --- |
| `hw_only` | hw | Production nodes that must fail loudly on hardware errors such as a lost `/dev/dri`, a missing oneVPL runtime or a kernel and driver mismatch |
| `hw_alt` | hw, hw_alt | Stay on the iGPU: recover from transient QSV encoder bugs through VAAPI without the 10 to 20 times longer software encode |
| `sw_decode_only` | hw, hw_alt, sw_decode | Allow a software decoder if both hardware encoders fail, but never a software encoder |
| `aggressive` | hw, hw_alt, sw_decode, full_sw | Default. Tries as hard as possible to finish the job |

Some rungs behave in special ways:

- `hw_alt` exists only for QSV encoders (`h264qsv`, `h265qsv`, `av1qsv`). `vp9qsv` has no VAAPI counterpart. The
  QSV decoder is kept, and its frames reach the VAAPI encoder through a zero-copy `hwmap=derive_device=vaapi`
  bridge. This recovers from errors such as `Invalid FrameType:0` on Main10 sources without a software encode.
  For other encoders the rung is skipped.
- A decode-side failure (`decoder_init_failed`) or a hardware filter failure (`filter_init_failed`) is also
  rescued under `hw_only` and `hw_alt`. SMA-NG retries with software decode and the hardware encoder
  (`[decode-side-rescue]`), which keeps the expensive encode on the GPU.
- When no hardware decoder is in use, `sw_decode` would repeat the identical command. The ladder then goes
  straight from the failed hardware tier to `full_sw`, or stops under `sw_decode_only`.

#### What the `hw_alt` swap changes

The VAAPI encoder rejects some QSV options, so the swap rewrites the encoder settings:

- QSV-only flags are removed from `codec-parameters`: `-extbrc`, `-b_strategy`, `-look_ahead`,
  `-look_ahead_depth`, `-adaptive_i`, `-adaptive_b`, `-p_strategy` and `-rdo`.
- The shared flags `-global_quality`, `-low_power` and `-async_depth` are removed too. Their values were tuned for
  QSV, and `hw_alt` has no later VAAPI fallback, so an unsupported value would fail the job.
- Only the `vaapi` block can set them again. To keep `-low_power` or `-async_depth` on the VAAPI encode, set
  `base.video.vaapi.low-power` and `base.video.vaapi.async-depth`. The `preset`, `b-frames`, `ref-frames`,
  `look-ahead-depth`, `global-quality` and `codec-parameters` values of the `vaapi` block are applied as well.
- A QSV `global-quality` becomes `-rc_mode CQP -qp N`. For AV1 it becomes `-rc_mode CQP -global_quality` on the
  0 to 255 scale. This does not happen when a bitrate target is set or `-rc_mode` is already given.

#### When the ladder stops

The ladder stops at once, with no further tier, when another attempt cannot succeed:

- FFmpeg was ended by a signal: `SIGHUP`, `SIGINT`, `SIGKILL` or `SIGTERM`. This includes exit codes 129, 130,
  137 and 143, and the `received signal N` exit of FFmpeg itself. Crash signals such as `SIGSEGV` or `SIGABRT`
  do not stop the ladder, because a software tier can survive a driver crash.
- The output side failed. `No space left on device`, `Disk quota exceeded` and `Read-only file system` stop the
  ladder wherever they appear in the error output. `Permission denied` and `Input/output error` stop it only on a
  line that names the output file or a write, muxing or trailer step; on a hardware-device line they do not,
  because software tiers drop the device.

The log then shows `[attempt-ladder] stop: <reason>; no further tiers`.

The deprecated boolean form still works, with one warning per daemon start. It will be removed in a future
release:

| Legacy YAML | Equivalent policy |
| --- | --- |
| `software-fallback: true` | `fallback-policy: aggressive` |
| `software-fallback: false` | `fallback-policy: hw_only` |

If both keys are present, `fallback-policy` wins.

### Why `hw_only` matters

On production QSV nodes, you usually want hardware failures to surface immediately. A silent CPU fallback hides
real infrastructure problems such as lost `/dev/dri` permissions, a missing oneVPL runtime or a kernel and driver
mismatch. Set `fallback-policy: hw_only` to fail loudly. The `ffmpeg.attempts` log line and the `/health`
counters still record the failure class, so alerting keeps working.

### Failure-class taxonomy

`/health` and the `ffmpeg.attempts` line sort FFmpeg errors into six stable classes. Dashboards can rely on these
values:

| Class | Typical cause |
| --- | --- |
| `device_open_failed` | `/dev/dri` permissions, missing libva or oneVPL runtime |
| `decoder_init_failed` | The hardware decoder rejects this codec or profile (for example AV1 on pre-Arc) |
| `encoder_init_failed` | The hardware encoder rejects the pixel format, profile or level |
| `filter_init_failed` | The filter graph cannot be built (for example `scale_qsv` is unavailable) |
| `runtime_error` | FFmpeg started and then failed mid-encode |
| `other` | Unrecognised error output |

A rise in `other` means that FFmpeg's messages changed. Update the classifier in
`resources/processor/failures.py`.
