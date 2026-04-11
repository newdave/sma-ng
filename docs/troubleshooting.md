# Troubleshooting

## Logs

The daemon writes rotating log files to `logs/daemon.log` and per-config log files in `logs/`. When running as a systemd service, recent output is also available via journald:

```bash
journalctl -u sma-daemon -f
```

The daemon also writes per-config rotating log files in `logs/`:

| Config | Log File |
| --- | --- |
| `config/autoProcess.ini` | `logs/autoProcess.log` |
| `config/autoProcess.tv.ini` | `logs/autoProcess.tv.log` |

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
- Check device exists: `ls /dev/dri/renderD128`
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

### systemd: "Read-only file system" errors

- Check `ReadWritePaths` in the systemd unit includes all paths FFmpeg writes to (temp dir, output dir, media mounts)
- Default unit includes `/opt/sma/config /opt/sma/logs /transcodes /mnt` — add any additional paths

### Daemon doesn't start after restart

- Check `journalctl -u sma-daemon --no-pager -n 50` for the error
- Verify `config/daemon.json` is valid JSON: `python -m json.tool config/daemon.json`
- Verify `config/autoProcess.ini` exists

### Smoke test fails at startup

If `smoke_test: true` is set in `daemon.json` or `--smoke-test` is passed:

- Check `logs/daemon.log` for `[FAIL]` lines showing which config raised an exception
- Common cause: a boolean field with a typo (e.g. `force-rename = Truee`) — fix the value in the config
- Run manually to see output: `python daemon.py --smoke-test`
- Missing configs are skipped with `[SKIP]` — create them with `mise run config` or `mise run deploy:config`

---

## Environment Variables

| Variable | Description |
| --- | --- |
| `SMA_CONFIG` | Override path to `autoProcess.ini` |
| `SMA_DAEMON_API_KEY` | Daemon API key |
| `SMA_DAEMON_DB_URL` | PostgreSQL connection URL for distributed mode |
| `SMA_DAEMON_FFMPEG_DIR` | Directory containing `ffmpeg`/`ffprobe` (prepended to PATH) |
| `SMA_DAEMON_HOST` | Daemon bind host (Docker default: `0.0.0.0`) |
| `SMA_DAEMON_PORT` | Daemon port (Docker default: `8585`) |
| `SMA_DAEMON_WORKERS` | Number of concurrent workers (Docker default: `2`) |
| `SMA_DAEMON_CONFIG` | Path to `daemon.json` config file |
| `SMA_DAEMON_LOGS_DIR` | Directory for per-config log files |
