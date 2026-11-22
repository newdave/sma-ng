# SMA-NG — Next-Generation Media Automator

![SMA-NG](../logo.png)

Automated media conversion, tagging, and integration pipeline. SMA-NG converts media files to MP4/MKV using FFmpeg
with hardware acceleration, tags them with TMDB metadata, and integrates with Sonarr, Radarr, Plex, and download
clients. It runs as a command-line tool (`manual.py`) or as an HTTP daemon (`daemon.py`) that queues jobs, runs
workers, and serves a dashboard.

This page is the index of the documentation. Each topic has one canonical page; start with
[Getting Started](getting-started.md).

## User Guide

- [Getting Started](getting-started.md): requirements, first conversion, CLI usage, all `manual.py` options, repair,
  post-process scripts and supported codecs.
- [Docker Quick Start](docker-quickstart.md): run SMA-NG in Docker with Compose, and drive it from the Docker host
  with the `sma` command.
- [Configuration](configuration.md): `sma-ng.yml` reference, profiles, services, and the stream decision rules.
- [Integrations](integrations.md): Sonarr, Radarr, Plex, Emby, Jellyfin and download-client triggers.
- [Library Audit](library-audit.md): find damaged, stretched or stale files across a library.
- [Migrating from sickbeard_mp4_automator](migration.md)

## Running the Daemon

- [Daemon Mode](daemon.md): HTTP API, webhooks, dashboard, authentication, path-based routing, scheduling, job
  persistence and cluster internals.
- [Cluster Operations](cluster-operations.md): deployment models, shared PostgreSQL, capacity planning, and runbooks
  for bootstrap, config rolls, zero-loss upgrades and node recovery.
- [Cluster Metrics](metrics.md): Prometheus metrics exported by the daemon.
- [Hardware Acceleration](hardware-acceleration.md): QSV, VAAPI, NVENC and VideoToolbox setup.
- [Deployment](deployment.md): `mise` task reference, remote deployment with `deploy:*`, CI and releases, and the
  reference storage and host environment.

## Reference

- [Troubleshooting](troubleshooting.md): logs, FFmpeg failure classes and fixes, Docker and cluster problems.
- [Architecture](architecture.md): component diagram, data flow and module reference.
- [Test Coverage](test-coverage.md): coverage policy and how to measure it.
- [`openapi.yaml`](openapi.yaml): the daemon HTTP API as an OpenAPI spec.

## License

See [license.md](../license.md).
