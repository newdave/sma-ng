# Troubleshooting

## Logs

The daemon writes rotating log files to `logs/daemon.log` and per-config log files in `logs/`. For Docker deployments, container output is available via `docker compose logs`:

```bash
journalctl -u sma-daemon -f
```

The daemon also writes per-config rotating log files in `logs/`:

| Config | Log File |
| --- | --- |
| `config/sma-ng.yml` | `logs/sma-ng.log` |

### How log lines render

Every application log record is exactly one line. Useful invariants when grepping or scripting against the logs:

- Newlines inside an application message are rendered as a visible `⏎` marker (`first ⏎ second`) so a single record stays on a single line.
- JSON-shaped substrings are compacted (no whitespace, no `indent=`). A long `daemon` block in a config dump renders as `{"daemon":{"host":"0.0.0.0",...}}` on one line.
- Records longer than the built-in 1024 character width cap are truncated with a `…+N` tail marker so you can see how much was dropped. The PostgreSQL `logs` table stores the full untruncated record.
- Tracebacks from `log.exception(...)` are emitted on subsequent lines, each prefixed with two spaces + `|`. Filter them with `grep '^  |'` to isolate the trace, or `grep -v '^  |'` to drop them.
- Secrets (`api_key`, `db_url`, `username`, `password`, `node_id`, `apikey`, `token`) are replaced with `***` before the record is written. Add new secret-bearing fields to `resources/daemon/constants.py` so every redaction site picks them up.

The width cap is a per-record format guard, not a database column limit.

You can also view and filter logs through the dashboard log viewer or via the API:

```bash
# List all log files
curl -H "X-API-Key: SECRET" http://localhost:8585/logs

# Last 100 lines of a log
curl -H "X-API-Key: SECRET" "http://localhost:8585/logs/sma-ng?lines=100"

# Filter by job ID
curl -H "X-API-Key: SECRET" "http://localhost:8585/logs/sma-ng?job_id=42"

# Filter by level
curl -H "X-API-Key: SECRET" "http://localhost:8585/logs/sma-ng?level=ERROR"
```

---

## Common Issues

### "Invalid source, no video stream detected"

- File may be corrupt or not a media file
- Check that `ffprobe` path in `[Converter]` is correct
- Run `ffprobe /path/to/file` manually to diagnose

### `hevc_qsv` fails mid-encode with `Invalid FrameType:0` / exit 183

A small fraction of Main10 SDR sources with deep look-ahead and adaptive B
(`bf 8 + adaptive_b 1 + look_ahead_depth 40 + p010le`) trip a transient
bug in the QSV encoder where it emits `Invalid FrameType:0` partway through
the file and exits 183. The decoder side is healthy — only the encoder
chokes.

Set `base.converter.fallback-policy` so the `hw_alt` tier can recover by
swapping `hevc_qsv` for `hevc_vaapi` on the same iGPU while preserving the
working QSV decoder:

```yaml
base:
  converter:
    fallback-policy: hw_alt      # try hw → hw_alt; stop before software
    # fallback-policy: aggressive  # full 4-tier ladder (hw → hw_alt → sw_decode → full_sw)
```

`hw_alt` keeps recovery on the iGPU and avoids the 10–20× wall-clock cost
of falling all the way to libx265 software. See
[`docs/hardware-acceleration.md`](hardware-acceleration.md#fallback-policy-replaces-the-deprecated-boolean-software-fallback)
for the full policy table, and watch for the structured `ffmpeg.attempts`
log line — successful recovery shows `[{tier: hw, failure_class: ...}, {tier: hw_alt, failure_class: null}]` with `result: ok`.

### XviD / MPEG-4 ASP source fails immediately with `decoder_init_failed`

Legacy `.avi` content (XviD / DivX, ffprobe codec `mpeg4`, advanced-simple
profile) and some VC-1 sources cannot be decoded by Intel QSV at all. Both
the `hw` and `hw_alt` tiers reuse the QSV decoder, so they fail back to back
— the `ffmpeg.attempts` line shows two failed attempts ending in
`failure_class: decoder_init_failed`, and the job is surfaced as failed under
`fallback-policy: hw_alt` or `hw_only`.

SMA-NG now auto-recovers this case: a `decoder_init_failed` **or**
`filter_init_failed` under `hw_alt` or `hw_only` triggers one extra
**software-decode + hardware-encode** retry (`tier: sw_decode_hw_encode`). The
source is decoded on the CPU while the QSV encoder is kept, so the expensive
encode stays on the GPU. The recovery log line reads `Decode-side hardware
failure (cause=…); retrying with software decode + hardware encode …
[decode-side-rescue]`, followed by an `ffmpeg.attempts` entry with
`{tier: sw_decode_hw_encode, failure_class: null}` and `result: ok`.

The `filter_init_failed` class covers a hardware filter graph that aborts
before the encoder ever opens — ffmpeg logs `[vf#0:0 …] Task finished with
error code: …` followed by `Could not open encoder before EOF`. Moving decode
to system memory rewrites every GPU filter (`vpp_qsv`, `scale_qsv`,
`scale_vaapi`, the `hwmap` bridge) into a plain `scale`/`format` chain, which
cures graph-level faults the same way it cures decoder faults.

No configuration change is required. If even the software decode fails (a
genuinely corrupt source), switch to `fallback-policy: aggressive` to add the
full software encode tier (`full_sw`) as a last resort.

### 7.1 source fails immediately with `encoder_init_failed` / `audio_channel_layout_mismatch`

A source with a 7.1 (8-channel) audio track re-encoded to `eac3`, `ac3`, or
`dts` used to die at encoder init in tens of milliseconds. The
`ffmpeg.failure_diagnosis` line reports
`cause: audio_channel_layout_mismatch` with the ffmpeg signal
`Specified channel layout '7.1' is not supported by the eac3 encoder`.
The native ffmpeg AC-3/E-AC-3/DTS encoders top out at 5.1 (6 channels), and a
profile with `max-channels: 0` (unlimited — as `rq`/`hq` use) otherwise passes
all 8 source channels straight through.

SMA-NG now auto-recovers this case: before the encode it downmixes the output
to the encoder's ceiling (6 channels) and logs
`Encoder eac3 caps channels at 6; downmixing from 8 [adaptive-audio-channel-cap]`.
No configuration change is required. Copy passthrough and higher-capacity
encoders (`aac`, `libfdk_aac`, `flac`) are unaffected. To keep the full 7.1
layout instead of downmixing, set the profile's `audio.codec` to a 7.1-capable
encoder or let the source stream copy through.

### Source fails immediately with `encoder_init_failed` (profile bit-depth mismatch)

A re-encode used to die at encoder init in ~100 ms when the target profile's
bit-depth class disagreed with the output surface. The `ffmpeg.failure_diagnosis`
line reports `failure_class: encoder_init_failed` with a `profile` and an output
filter that don't agree on bit depth. This happens because the profile fallback
picks the first entry of `video.profile` whenever the source's own profile name
isn't in the list — and for any cross-codec transcode (H.264 source → HEVC) it
never is. Two mismatches result:

- **10-bit profile on 8-bit output** — `profile: main10` with `format=nv12`
  (an 8-bit DVD/x264 source, no 10-bit `pix-fmt` selected). Common trigger:
  `profile: [main10, main]` ordering (main10 first).
- **8-bit profile on 10-bit output** — `profile: main` with a `p010le` surface
  (a 10-bit SDR x265 source, `profile: [main, main10]` ordering).

Either way `hevc_qsv`/`hevc_vaapi` (and the H.264 encoders) reject the surface
at init.

SMA-NG now auto-recovers both cases: before the encode it aligns the profile to
the output depth — downgrading (`main10` → `main`, `high10` → `high`) for 8-bit
output, or upgrading (`main` → `main10`, `high` → `high10`) for 10-bit output —
and logs e.g. `Profile main10 requires 10-bit input but output is 8-bit;
downgrading to main to avoid encoder_init_failed [adaptive-profile-bit-depth]`.
The upgrade direction is only taken when the chosen encoder actually supports
10-bit. It reuses your own spelling from `video.profile` when present. No
configuration change is required. To pin the output bit depth explicitly, set
the profile's `video.pix-fmt` (e.g. `p010le` for 10-bit, `nv12` for 8-bit).

### Output dims differ slightly from `max-width` on QSV jobs

On the QSV full-GPU pipeline an operator downscale (`video.max-width`) is
folded into the single implicit `vpp_qsv` filter — chaining a second QSV
scaler aborts graph init on ffmpeg 8.x/iHD. The configured width is floored
to the nearest even value (it is a cap, never rounded up) and the height is
derived from the source aspect ratio, so the picture is never stretched. The
fold is logged as
`Folding operator downscale into vpp_qsv w=W:h=H … [adaptive-qsv-downscale]`.
Earlier releases floor-aligned both dimensions to a mod-16/mod-32 encoder
boundary, which shipped slightly stretched output (e.g. a derived 1920x1080
target as 1920x1072, or an untouched 1920x1080 source scaled to 1920x1088);
that coercion has been removed — QSV drivers handle surface alignment
internally without rescaling the picture.

### Repairing previously stretched ("hybrid") outputs

Files transcoded before the fix may still carry stretched geometry
(1920x1088, 1920x1072, 1920x1056, 3840x2176, 3840x2144, 1280x736, 1280x704).
Two repair paths exist:

- **Full rebuild (recommended):** `python manual.py --repair-hybrid reencode -i file.mp4`
  re-encodes one file back to its true dimensions through the normal
  pipeline (logged as `[video-dims-override]`); costs an encode generation.
  Re-downloading and re-encoding from the original source is better still.
- **Lossless (display-only):** `python manual.py --repair-hybrid -i /path`
  sweeps a file or directory and remuxes each detected MP4 with a corrective
  display aspect ratio on the primary video stream (`-c copy -aspect:v:0 W:H`).
  It is instant but does not remove the resampled rows: players that ignore
  the sample aspect ratio for HEVC-in-MP4 (some TVs and Plex clients) still
  show a slightly tall picture, and non-square pixels make a Plex transcode
  more likely. This is the `repair.py` repair (below), so cover art, data and
  unknown streams are dropped too. The library audit can do the same
  automatically via `daemon.audit.auto-fix.hybrid-aspect: true` (see
  [Library Audit](library-audit.md)).

Detection is signature-based and skips files already carrying a non-square
pixel aspect ratio, so re-running the repair is safe and idempotent. The one
exception is files damaged by the remux in releases before this fix: it mapped
every stream (`-map 0`) and applied `-aspect` to all video streams, turning
the cover art into a second real `mp4v` video track. Those files are reported
with reason `demoted_poster`, and running the lossless repair again removes
the stray track. If the log shows `Hybrid-aspect file detected: ... demoted_poster`,
that is the fix being applied.

### Repairing damaged media without transcoding

`repair.py` fixes stream-level damage by remuxing with `-c copy`; it never re-encodes. It accepts any mix of files
and directories (`.mp4`, `.m4v`, `.mov`, `.mkv`; directories are walked recursively, hidden files and folders are
skipped) and leaves files that probe as normal alone.

```bash
python repair.py -n /mnt/unionfs/Media/TV          # dry run: report what would change
python repair.py /path/to/file.mp4 /path/to/dir     # repair in place
mise run media:repair -- /path/to/file-or-dir       # same via mise; `sma repair PATH` inside Docker
```

| Flag              | Effect                                                         |
| ----------------- | -------------------------------------------------------------- |
| `-n`, `--dry-run` | Report what would be repaired; change nothing                  |
| `-c`, `--config`  | Alternate `sma-ng.yml` path (disables per-path routing)        |
| `--no-plex`       | Skip the Plex refresh after repairs                            |
| `-v`, `--verbose` | Debug logging                                                  |

What it fixes:

| Damage       | Detected as                                                                                     | Fix                                    |
| ------------ | ----------------------------------------------------------------------------------------------- | -------------------------------------- |
| Data streams | `tmcd` timecode tracks, stray `bin_data`                                                        | Dropped (`-write_tmcd 0` on MP4)       |
| Unknown      | Streams FFprobe cannot classify or identify a codec for (`unknown`/`none`)                      | Dropped                                |
| Artwork      | Attached pictures, cover art demoted to a real `mp4v` video track, image attachments in MKV     | Dropped; font attachments are kept     |
| Aspect ratio | Stretched hybrid dims (see above) with square pixels                                            | `-aspect:v:0` corrective display ratio |

The MP4 chapter text track (`bin_data` tagged `text`, written whenever a file has chapters) is not damage and is
kept. The
aspect fix is display-only, as described above; use `manual.py --repair-hybrid reencode` to remove the
resampled rows.

FFmpeg/FFprobe paths, in-transit output settings and output permissions (`chmod`, `uid`, `gid`) come from
`sma-ng.yml`, exactly as for a conversion: without `-c`, each file is routed to the profile the daemon would
convert it with, so per-service `output-directory` and permissions apply. Each repair writes
`<name>.<temp-extension>` (`<name>.sma` when unset) into `output-directory` (e.g. `/transcode`), or next to the
original when no output directory is set. When
`output-directory-space-ratio` is set and the output directory is short of space, the file fails with
`insufficient free space in output directory` rather than falling back to the source directory. The copy is
re-probed and only moved back over the original (a rename, or a copy via `<name>.smatmp` across filesystems) when
the kept streams (codec, dimensions, channels, language and default/forced flags), duration (within 1 second) and
chapter track match and the copy probes as normal. Otherwise the copy is deleted and the original is untouched.
A leftover from an interrupted run is safe to delete; the library audit reports it as `leftover_tmp` once it is
older than `storage-janitor-max-age-seconds`. MP4 output gets
`+faststart`. After the run, each directory with a repaired file gets one Plex refresh when Plex refresh is
configured (skip with `--no-plex`). The exit code is 0 when nothing failed and 1 when any path is missing, any
repair failed, or the settings could not be loaded.

The library audit reports the same damage as `stream_damage` findings and can repair them automatically with
`daemon.audit.auto-fix.stream-damage: true` (see [Library Audit](library-audit.md)).

### Source rejected: "only audio streams are unknown to FFprobe"

A conversion never copies streams FFprobe cannot classify or identify a codec for; each one is logged as
`Dropping unknown ... stream ... [unknown-stream]`. When every audio stream in the source is unknown, the file is
rejected as an invalid source with `Invalid source, its only audio streams are unknown to FFprobe and cannot be
converted [unknown-stream]` instead of producing video-only output. Check the file with `ffprobe -show_streams`;
usually the FFmpeg build lacks the decoder, or the source is damaged and needs re-downloading.

### Hardware acceleration not working

- Verify `hwdevices` key matches encoder codec name (e.g., `qsv` for `h265qsv`)
- Verify `hwaccel-output-format` uses dict format: `qsv:qsv` not just `qsv`
- Check FFmpeg build supports the hwaccel: `ffmpeg -hwaccels`
- Check the render device exists: `ls /dev/dri/renderD128`
- On Intel SR-IOV guests, verify the guest exposes the Intel VF as a matching `card*` and `renderD*` pair under `/dev/dri`
- If `vainfo` still fails, verify the container has both the host `render` and `video` group IDs
- On Linux, ensure the service user is in the `render` or `video` group: `usermod -aG render <user>`

### Hardware encode failed and the job finished on the CPU

- With `fallback-policy: aggressive`, a hardware encoder failure on QSV, VAAPI, NVENC or VideoToolbox ends
  in the `full_sw` rung: the job is re-run with `libx264`/`libx265`/`libsvtav1` and every hardware filter,
  device and decode flag removed. The warning `falling back to full software pipeline` lists the swaps.
- The job succeeds but runs slower. Fix the hardware issue (see below) to get GPU speed back, or set
  `fallback-policy: hw_only` while you diagnose it.

### Output written as `name.2.mp4`

- A file already existed at the output name, so SMA-NG kept it and numbered the new output
  (`[output-collision]` warning). Remove or rename the old file and re-run if you want the plain name.
- With `output-directory` and `delete-original: false`, a source that has the same name as the output
  (`movie.mp4` to `movie.mp4`) is kept and the new file is restored as `movie.2.mp4`. Set
  `delete-original: true` to replace the source instead.

### `[dolby-vision]` warning on re-encode

- The source carries Dolby Vision metadata, but the video stream is being re-encoded. The encoders can't
  carry the Dolby Vision RPU, so the output plays as its HDR10 (or SDR) base layer. To keep Dolby Vision,
  allow the source codec in the video codec pool so the stream can be copied.

### "QT FastStart failed" warning

- Moving the `moov` atom to the start of the file (`relocate-moov`) failed, usually because of an I/O
  error. The converted file is kept as is (it still plays, but may start more slowly when streamed) and
  the partial `*.QTFS` temp file is removed.

### Job failed with "Job timed out after Ns"

The conversion ran longer than `daemon.job-timeout-seconds` (or `--job-timeout`), so the worker killed the whole
conversion process tree and failed the job with cause `timeout` (category `system`). The log shows
`Job N timed out after Ns; killed conversion process tree: <path>`. Check whether ffmpeg was stalled (NFS or
unionfs reads, a hung GPU) or the encode is genuinely slow (4K software encode); raise the timeout or set it to `0`
to disable it. See [Job timeout](daemon.md#job-timeout).

### "Job N completion discarded: job is no longer running on this node"

The job finished, but its row had already been cancelled, requeued by stale-node recovery, or claimed by another
node, so this node's result was dropped instead of overwriting the newer state. This is expected after a cancel
or a network partition. A cancelled job's output may still exist on disk if ffmpeg finished before the kill landed.

### Conversion produces larger output file

- Lower the `crf` value (lower CRF = higher quality = larger file; raise it to reduce size)
- Add a `max-bitrate` cap in `[Video]`
- Use `bitrate-ratio` to scale based on source codec
- Use `crf-profiles` for tiered quality based on source bitrate

### Subtitles show as "English (MOV_TEXT)" in Plex

- This is Plex reading the raw codec name. SMA-NG sets a title on subtitle streams — this is cosmetic.

### External .srt skipped with "no appropriate codecs found or embed disabled"

- For a text sidecar this usually meant the file is not UTF-8 (CP1252/UTF-16 are
  common); FFmpeg's decode failure made the image/text probe classify it as
  image-based, and with `codec-image-based` empty the file was dropped.
- SMA-NG now auto-detects sidecar encodings and passes `-sub_charenc`
  (log line `[sub-encoding-detect]`), so this skip should only appear for
  genuinely image-based or corrupt files. Re-queue the affected items to pick
  the subtitles up.

### Sonarr/Radarr not rescanning after manual.py

- Verify `path` is set in the `[Sonarr]`/`[Radarr]` section
- Verify `apikey` is correct and `rescan = true`
- Verify the output file path starts with the configured `path` prefix

### Docker: "Read-only file system" errors

- Verify every path FFmpeg writes to (output dir, temp dir, media mounts) is bind-mounted
  read-write into the `sma` service in `docker/docker-compose.yml`
- The bundled compose file mounts `/opt/sma/config`, `/opt/sma/logs`, and `/transcode` by
  default — extend `volumes:` for any additional paths your setup needs

### Daemon doesn't start after restart

- Check `docker compose logs sma --no-color | tail -50` for the error
- Verify `config/sma-ng.yml` is valid YAML.
- Verify `config/sma-ng.yml` exists

### "Janitor swept N files" — what does it mean?

If `logs/daemon.log` shows a recurring single-line JSON event like:

```json
{"event":"storage.janitor","swept_sma":3,"swept_smatmp":1,"swept_empty_mp4":0,"freed_bytes":4823040}
```

the storage janitor removed leftover transcode artefacts from
`base.converter.output-directory`. A small non-zero count is normal —
the janitor exists to reclaim space when ffmpeg or the daemon were
killed mid-job. A sustained high `swept_sma` / `swept_smatmp` rate
indicates the worker is crashing or being OOM-killed mid-transcode.

Counter-mirror: `sma_output_orphan_files_swept_total{node_id,kind}` —
plot `rate(sma_output_orphan_files_swept_total[1h])` per kind. The free-space
gauge `sma_output_dir_free_bytes` and its companion alerts are documented in
[`docs/metrics.md`](metrics.md#storage-instruments).

To disable the janitor (not recommended on hosts with active output
directories), set `daemon.storage-janitor-interval-seconds: 0` in
`sma-ng.yml`.

### Smoke test fails at startup

If `Daemon.smoke_test: true` is set in `sma-ng.yml` or `--smoke-test` is passed:

- Check `logs/daemon.log` for `[FAIL]` lines showing which config raised an exception
- Common cause: a boolean field with a typo (e.g. `force-rename = Truee`) — fix the value in the config
- Run manually to see output: `python daemon.py --smoke-test`
- Missing configs are skipped with `[SKIP]` — create them with `mise run config:generate` or `mise run deploy:config`

---

## Config inspection and validation

If a job isn't behaving as expected and you want to know exactly what
config the daemon would resolve for it:

```bash
mise run config:show -- --profile rq --section video --diff
```

renders the effective `video` section after the `rq` profile overlay,
showing only the fields that differ from `base`. Useful for confirming a
profile carries only the deltas you expect.

For an even more concrete preview — the actual ffmpeg command line that
would run for a specific file — pass `--input`:

```bash
mise run config:show -- --profile rq --input /mnt/unionfs/Media/TV/.../episode.mkv
```

This probes the file, runs the same `generateOptions` path the daemon
uses, and prints the assembled ffmpeg command without transcoding.
Delegates to `manual.py -oo` so the output is byte-identical to what a
real job would invoke.

```bash
mise run config:validate
```

runs schema validation plus operator-facing checks: unknown config keys
(typo detection), encoder-only flag tokens leaking into encoder-agnostic
`codec-parameters` strings, routing references to undefined profiles or
services, missing service credentials, and per-encoder subblock alignment
warnings. Add `--strict` to fail on warnings (CI-friendly) or `--quiet`
for a summary-only line.

---

## Environment Variables

The daemon no longer reads `SMA_*` environment variables for runtime configuration. Use CLI flags for process-level options and `sma-ng.yml` for persistent daemon settings.
