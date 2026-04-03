SMA-NG — Next-Generation Media Automator
==============

![SMA-NG](logo.png)

Automated media conversion, tagging, and integration pipeline. Converts media files to MP4/MKV using FFmpeg with hardware acceleration, tags them with TMDB metadata, and integrates with media managers and download clients.

A quasi-fork of [Sickbeard MP4 Automator](https://github.com/mdhiggins/sickbeard_mp4_automator) with a restructured codebase, daemon mode, hardware-accelerated encoding, and deployment tooling.

Works on Windows, macOS, and Linux.

## Quick Start

```bash
git clone https://github.com/newdave/sma-ng.git && cd sma-ng
make config    # auto-detect GPU, generate config
make install   # create venv, install dependencies
python manual.py -i "/path/to/file.mkv" -a
```

Or with mise:

```bash
mise install && mise run install && mise run config
mise run convert -- /path/to/file.mkv
```

## Documentation

Full documentation is in [docs/](docs/) and served at `http://localhost:8585/docs` when the daemon is running.

| Page | Description |
| --- | --- |
| [Getting Started](docs/getting-started.md) | Installation, quick start, CLI usage, supported codecs |
| [Configuration](docs/configuration.md) | `autoProcess.ini` settings reference |
| [Daemon Mode](docs/daemon.md) | HTTP server, API endpoints, dashboard, clustering, persistence |
| [Integrations](docs/integrations.md) | Sonarr, Radarr, Plex, NZBGet, SABnzbd, qBittorrent, Deluge |
| [Hardware Acceleration](docs/hardware-acceleration.md) | QSV, VAAPI, NVENC, VideoToolbox, auto-detection |
| [Deployment](docs/deployment.md) | mise tasks, remote deploy, systemd service, Docker, CI/release |
| [Troubleshooting](docs/troubleshooting.md) | Logs, common issues, environment variables |

## Default Settings

- Container: MP4
- Video: H265 (hardware-accelerated if detected)
- Audio: AAC 2.0 + AC3 passthrough when source has >2 channels
- Subtitles: mov_text

## Docker

```bash
docker run --rm -p 8585:8585 \
  -v /your/config:/config \
  -v /your/logs:/logs \
  ghcr.io/newdave/sma-ng:latest
```

Tags: `latest`, `1`, `1.2`, `1.2.3` (semver), `main` (rolling).

## Credits

[FFmpeg](http://www.ffmpeg.org/) · [Python](http://www.python.org/) · [tmdbsimple](https://github.com/celiao/tmdbsimple) · [mutagen](https://github.com/quodlibet/mutagen) · [qtfaststart](http://github.com/danielgtaylor/qtfaststart) · [guessit](http://github.com/wackou/guessit) · [subliminal](http://github.com/Diaoul/subliminal) · [Sonarr](http://sonarr.tv/) · [Radarr](http://radarr.video/) · [mise](https://mise.jdx.dev/)
