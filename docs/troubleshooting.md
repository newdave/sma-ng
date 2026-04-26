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
- Records longer than `SMA_LOG_MAX_WIDTH` (default 1024) are truncated with a `…+N` tail marker so you can see how much was dropped. The PostgreSQL `logs` table stores the full untruncated record.
- Tracebacks from `log.exception(...)` are emitted on subsequent lines, each prefixed with two spaces + `|`. Filter them with `grep '^  |'` to isolate the trace, or `grep -v '^  |'` to drop them.
- Secrets (`api_key`, `db_url`, `username`, `password`, `node_id`, `apikey`, `token`) are replaced with `***` before the record is written. Add new secret-bearing fields to `resources/daemon/constants.py` so every redaction site picks them up.

The width cap is a per-record format guard, not a database column limit. Override with `SMA_LOG_MAX_WIDTH=4096 python daemon.py …` to relax it during deep debugging.

You can also view and filter logs through the dashboard log viewer or via the API:

```bash
# List all log files
curl -H "X-API-Key: SECRET" http://localhost:8585/logs

# Last 100 lines of a log
curl -H "X-API-Key: SECRET" "http://localhost:8585/logs/autoProcess?lines=100"

# Filter by job ID
curl -H "X-API-Key: SECRET" "http://localhost:8585/logs/autoProcess?job_id=42"

# Filter by level
curl -H "X-API-Key: SECRET" "http://localhost:8585/logs/autoProcess?level=ERROR"
```

---

## Common Issues

### "Invalid source, no video stream detected"

- File may be corrupt or not a media file
- Check that `ffprobe` path in `[Converter]` is correct
- Run `ffprobe /path/to/file` manually to diagnose

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
- The bundled compose file mounts `/opt/sma/config`, `/opt/sma/logs`, and `/transcodes` by
  default — extend `volumes:` for any additional paths your setup needs

### Daemon doesn't start after restart

- Check `docker compose logs sma --no-color | tail -50` for the error
- Verify `config/sma-ng.yml` is valid YAML.
- Verify `config/sma-ng.yml` exists

### Smoke test fails at startup

If `Daemon.smoke_test: true` is set in `sma-ng.yml` or `--smoke-test` is passed:

- Check `logs/daemon.log` for `[FAIL]` lines showing which config raised an exception
- Common cause: a boolean field with a typo (e.g. `force-rename = Truee`) — fix the value in the config
- Run manually to see output: `python daemon.py --smoke-test`
- Missing configs are skipped with `[SKIP]` — create them with `mise run config:generate` or `mise run config:roll`

---

## Environment Variables

| Variable | Description |
| --- | --- |
| `SMA_CONFIG` | Override path to `sma-ng.yml` |
| `SMA_DAEMON_API_KEY` | Daemon API key |
| `SMA_DAEMON_DB_URL` | PostgreSQL connection URL for distributed mode |
| `SMA_DAEMON_FFMPEG_DIR` | Directory containing `ffmpeg`/`ffprobe` (prepended to PATH) |
| `SMA_DAEMON_HOST` | Daemon bind host (Docker default: `0.0.0.0`) |
| `SMA_DAEMON_PORT` | Daemon port (Docker default: `8585`) |
| `SMA_DAEMON_WORKERS` | Number of concurrent workers (Docker default: `2`) |
| `SMA_DAEMON_CONFIG` | Path to daemon config, normally `config/sma-ng.yml` |
| `SMA_DAEMON_LOGS_DIR` | Directory for per-config log files |
