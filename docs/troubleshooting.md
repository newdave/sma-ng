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

SMA-NG now auto-recovers this case: a `decoder_init_failed` under `hw_alt` or
`hw_only` triggers one extra **software-decode + hardware-encode** retry
(`tier: sw_decode_hw_encode`). The source is decoded on the CPU while the QSV
encoder is kept, so the expensive encode stays on the GPU. The recovery log
line reads `Decode-side hardware failure (cause=decoder_init_failed); retrying
with software decode + hardware encode … [decode-side-rescue]`, followed by an
`ffmpeg.attempts` entry with `{tier: sw_decode_hw_encode, failure_class: null}`
and `result: ok`.

No configuration change is required. If even the software decode fails (a
genuinely corrupt source), switch to `fallback-policy: aggressive` to add the
full software encode tier (`full_sw`) as a last resort.

### Hardware acceleration not working

- Verify `hwdevices` key matches encoder codec name (e.g., `qsv` for `h265qsv`)
- Verify `hwaccel-output-format` uses dict format: `qsv:qsv` not just `qsv`
- Check FFmpeg build supports the hwaccel: `ffmpeg -hwaccels`
- Check the render device exists: `ls /dev/dri/renderD128`
- On Intel SR-IOV guests, verify the guest exposes the Intel VF as a matching `card*` and `renderD*` pair under `/dev/dri`
- If `vainfo` still fails, verify the container has both the host `render` and `video` group IDs
- On Linux, ensure the service user is in the `render` or `video` group: `usermod -aG render <user>`

### Conversion produces larger output file

- Lower the `crf` value (lower CRF = higher quality = larger file; raise it to reduce size)
- Add a `max-bitrate` cap in `[Video]`
- Use `bitrate-ratio` to scale based on source codec
- Use `crf-profiles` for tiered quality based on source bitrate

### Subtitles show as "English (MOV_TEXT)" in Plex

- This is Plex reading the raw codec name. SMA-NG sets a title on subtitle streams — this is cosmetic.

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
