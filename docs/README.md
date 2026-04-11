# SMA-NG Documentation

![SMA-NG](../logo.png)

Automated media conversion, tagging, and integration pipeline. Converts media files to MP4/MKV using FFmpeg with hardware acceleration, tags them with TMDB metadata, and integrates with media managers and download clients.

---

## Documentation

- [Getting Started](getting-started.md) — Installation, quick start, CLI usage
- [Configuration Reference](configuration.md) — `autoProcess.ini` settings for every section
- [Daemon Mode](daemon.md) — HTTP webhook server, API endpoints, dashboard, concurrency, persistence, clustering
- [Integrations](integrations.md) — Sonarr, Radarr, Plex, NZBGet, SABnzbd, qBittorrent, Deluge, uTorrent
- [Hardware Acceleration](hardware-acceleration.md) — QSV, VAAPI, NVENC, VideoToolbox, auto-detection
- [Deployment](deployment.md) — mise tasks, remote deploy, systemd service, Docker
- [Troubleshooting](troubleshooting.md) — Logs, common issues, environment variables

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                         Entry Points                            │
├──────────┬──────────┬──────────────────────────────────────────┤
│ manual.py│daemon.py │         triggers/ (bash scripts)         │
│ CLI tool │HTTP server│  sonarr.sh  radarr.sh  sabnzbd.sh  ...  │
└────┬─────┴────┬─────┴──────────────────────┬───────────────────┘
     │          │                             │
     ▼          ▼                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    resources/mediaprocessor.py                   │
│                     MediaProcessor (core)                        │
│  isValidSource → generateOptions → convert → tag → replicate    │
├─────────────────────┬───────────────────┬───────────────────────┤
│ resources/          │ converter/        │ autoprocess/           │
│  readsettings.py    │  ffmpeg.py        │  plex.py              │
│  metadata.py        │  avcodecs.py      │                       │
│  postprocess.py     │  formats.py       │                       │
│  lang.py / log.py   │                   │                       │
│  daemon/            │                   │                       │
└─────────────────────┴───────────────────┴───────────────────────┘
```

### Daemon Package (`resources/daemon/`)

The daemon is structured as a package under `resources/daemon/`:

| Module | Contents |
| --- | --- |
| `constants.py` | `SCRIPT_DIR`, `DEFAULT_*`, `STATUS_*` constants |
| `db.py` | `PostgreSQLJobDatabase` — PostgreSQL-backed job queue |
| `config.py` | `ConfigLockManager`, `ConfigLogManager`, `PathConfigManager` |
| `handler.py` | `WebhookHandler` + HTML helpers |
| `threads.py` | `_StoppableThread`, `HeartbeatThread`, `ScannerThread` |
| `worker.py` | `ConversionWorker`, `WorkerPool` |
| `server.py` | `DaemonServer`, `_validate_hwaccel` |

`daemon.py` (project root) is a thin entry point that imports from this package.

### Processing Pipeline

```text
Input File
  → FFprobe validation (isValidSource)
  → Stream analysis & option generation (generateOptions)
  → FFmpeg conversion with HW accel (convert)
  → TMDB metadata tagging (writeTags)
  → moov atom relocation for streaming (QTFS)
  → File placement: output_dir → restore → copy-to / move-to (replicate)
  → Post-process scripts + Plex/Sonarr/Radarr notifications (post)
  → Output files
```

### Module Reference

| Module | Key Class / Function | Description |
| --- | --- | --- |
| `resources/mediaprocessor.py` | `MediaProcessor` | Core pipeline: validate, convert, tag, replicate |
| `resources/readsettings.py` | `ReadSettings` | Parses `autoProcess.ini` into typed attributes |
| `resources/metadata.py` | `Metadata` | TMDB API client + mutagen MP4 tagger |
| `resources/postprocess.py` | `PostProcessor` | Runs scripts from `post_process/` with env vars |
| `resources/log.py` | `getLogger` | Logging setup (stdout/stderr only) |
| `resources/lang.py` | — | ISO 639 language code conversion |
| `resources/custom.py` | — | Optional hook points loaded from `config/custom.py` |
| `resources/mediamanager.py` | — | Sonarr/Radarr API helpers for trigger scripts |
| `converter/avcodecs.py` | codec classes | Video/audio/subtitle codec definitions + FFmpeg encoder mappings |
| `converter/ffmpeg.py` | `FFMpeg`, `MediaInfo` | FFmpeg/FFprobe wrapper |
| `converter/formats.py` | — | Container format → FFmpeg muxer mappings |
| `autoprocess/plex.py` | — | Plex library refresh via PlexAPI |
