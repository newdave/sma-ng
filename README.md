# SMA-NG — Next-Generation Media Automator

![SMA-NG](logo.png)

Automated media conversion, tagging and integration pipeline. SMA-NG converts media files to MP4 or MKV using FFmpeg
with hardware acceleration, tags them with TMDB metadata, and integrates with Sonarr, Radarr, Plex and download
clients. It runs as a command-line tool (`manual.py`) or as an HTTP daemon (`daemon.py`) that queues jobs, runs
workers and serves a dashboard.

SMA-NG is a quasi-fork of [Sickbeard MP4 Automator](https://github.com/mdhiggins/sickbeard_mp4_automator) with a
restructured codebase, a daemon mode, hardware-accelerated encoding and deployment tooling. If you are upgrading from
the original, see the [migration guide](docs/migration.md).

It runs on macOS and Linux, and in Docker on any host.

## Quick Start

Development and everyday tasks run through [mise](https://mise.jdx.dev/) (`curl https://mise.run | sh` installs it).
SMA-NG needs Python 3.12 or newer and FFmpeg; `mise install` provides the pinned Python.

```bash
git clone https://github.com/newdave/sma-ng.git && cd sma-ng
mise install && mise run setup:deps   # pinned Python, venv and dependencies
mise run config:generate              # config/sma-ng.yml with the GPU auto-detected
mise run media:convert -- /path/to/file.mkv
```

## Documentation

The full documentation is in [docs/](docs/), is published as the
[project wiki](https://github.com/newdave/sma-ng/wiki), and is served at `http://localhost:8585/docs` when the daemon is
running.

| Page | Description |
| --- | --- |
| [Getting Started](docs/getting-started.md) | Installation, quick start, CLI usage, supported codecs |
| [Configuration](docs/configuration.md) | `sma-ng.yml` settings reference |
| [Daemon Mode](docs/daemon.md) | HTTP server, API endpoints, dashboard, clustering, persistence |
| [Docker Quick Start](docs/docker-quickstart.md) | The `sma` host command and Docker Compose profiles |
| [Integrations](docs/integrations.md) | Sonarr, Radarr, Plex, NZBGet, SABnzbd, qBittorrent, Deluge |
| [Hardware Acceleration](docs/hardware-acceleration.md) | QSV, VAAPI, NVENC, VideoToolbox, auto-detection |
| [Deployment](docs/deployment.md) | mise tasks, remote deploy, Docker, CI/release |
| [Cluster Operations](docs/cluster-operations.md) | Multi-instance and multi-host patterns, cluster runbooks |
| [Architecture](docs/architecture.md) | Components, conversion pipeline, daemon flow, module reference |
| [Troubleshooting](docs/troubleshooting.md) | Logs, FFmpeg failure classes and fixes, Docker and cluster problems |
| [Library Audit](docs/library-audit.md) | Find damaged, stretched or stale files in a library |
| [Test Coverage](docs/test-coverage.md) | The coverage policy and how to run it |

## Default Settings

The shipped defaults in `setup/sma-ng.yml.sample` produce an MP4 with H.265 video (hardware-accelerated when a GPU is
detected), AC3 audio and `mov_text` subtitles. Change them in `config/sma-ng.yml`; see
[Configuration](docs/configuration.md).

## Docker

Run the tools from any Docker host with the `sma` command (Docker is the only requirement):

```bash
curl -fsSL https://raw.githubusercontent.com/newdave/sma-ng/main/install-smang.sh | sh
sma preview /path/to/file.mkv
```

Each `sma` command runs in a throwaway container that mounts the paths you name; see the
[Docker Quick Start](docs/docker-quickstart.md#the-sma-host-command). To run the daemon (webhooks, job queue, dashboard), use
[Docker Compose](docs/docker-quickstart.md#docker-compose) or:

```bash
docker run --rm -p 8585:8585 \
  -v /your/config:/config \
  -v /your/logs:/logs \
  ghcr.io/newdave/sma-ng:latest
```

Image tags: `latest` and `main` (the main branch after CI passes), `sha-<commit>`, and `X.Y.Z`, `X.Y` and `X` for each release. Images are built for amd64 and arm64.

## Credits

[FFmpeg](http://www.ffmpeg.org/) · [Python](http://www.python.org/) · [tmdbsimple](https://github.com/celiao/tmdbsimple) · [mutagen](https://github.com/quodlibet/mutagen) · [qtfaststart](http://github.com/danielgtaylor/qtfaststart) · [guessit](http://github.com/wackou/guessit) · [subliminal](http://github.com/Diaoul/subliminal) · [Sonarr](http://sonarr.tv/) · [Radarr](http://radarr.video/) · [mise](https://mise.jdx.dev/)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, test commands and commit conventions.
