# Docker Compose Quick Start

This is the fastest way to get the SMA-NG daemon running with Docker Compose.

The repository ships a compose file at [`docker/docker-compose.yml`](../docker/docker-compose.yml) with six profiles:

- `software`
- `software-pg`
- `intel`
- `intel-pg`
- `nvidia`
- `nvidia-pg`

The non-`-pg` profiles are single-node profiles and default to SQLite at `/data/sma-ng.db`, persisted on the
Docker host under `/opt/sma/data/sma-ng.db`. The `-pg` profiles run the same container for nodes that use an
external PostgreSQL server, such as cluster members; compose does not bundle PostgreSQL, and the database is
chosen only by `daemon.db_url` in `sma-ng.yml`.

With the current compose layout, use config files for different jobs:

- `/opt/sma/config/sma-ng.yml` → daemon runtime settings
- `/opt/sma/config/daemon.env` → container-only settings (`POSTGRES_*`, `LIBVA_DRIVER_NAME`, `NVIDIA_*`)
- `docker/.env` → Compose interpolation (`IMAGE_TAG`, `PORT`, `STOP_GRACE_PERIOD`)

For clustered Docker deployments, set `daemon.node_id` in
`/opt/sma/config/sma-ng.yml`.

## 1. Prepare Host Directories

From a checkout on the deployment target, run the installer:

```bash
mise run setup:docker:target
```

If `mise` is not installed yet, run the script directly:

```bash
bash setup/install-docker-target.sh
```

By default this creates the persistent directories and seed files the compose file expects:

```bash
/opt/sma/config/sma-ng.yml
/opt/sma/config/daemon.env
/opt/sma/logs/
/opt/sma/cache/
/opt/sma/data/
/transcode/sma/
```

The default compose mounts are:

- `/opt/sma/config` → container `/config`
- `/opt/sma/logs` → container `/logs`
- `/opt/sma/data` → container `/data`
- `/mnt` → container `/mnt`
- `/transcode` → container `/transcode`

Adjust the compose file if your host uses different media paths.

The installer accepts environment overrides:

```bash
INSTALL_DIR=/srv/sma TRANSCODE_DIR=/srv/transcode mise run setup:docker:target
```

## 2. Create Config Files

The installer copies `setup/sma-ng.yml.sample` to `/opt/sma/config/sma-ng.yml` and
`setup/daemon.env.sample` to `/opt/sma/config/daemon.env` only when those files do not already exist.
Existing local config is left untouched.

## 3. Edit `sma-ng.yml`

Set the basics in `/opt/sma/config/sma-ng.yml`:

- `ffmpeg = /usr/local/bin/ffmpeg`
- `ffprobe = /usr/local/bin/ffprobe`
- your desired codec/container settings
- Sonarr/Radarr/Plex sections if needed

For hardware encoders, also set the appropriate `gpu =` and codec values. See [Hardware Acceleration](hardware-acceleration.md).

## 4. Edit `Daemon:` section in `sma-ng.yml`

Minimal example:

```yaml
Daemon:
  default_config: /config/sma-ng.yml
  api_key: change-me
  db_url: sqlite:////data/sma-ng.db
  path_configs:
    - path: /mnt/unionfs/Media/TV
      profile: rq
    - path: /mnt/unionfs/Media/Movies
      profile: rq
```

Notes:

- `/config/...` paths are inside-container paths
- `/mnt/...` paths must match the container view of your mounted media
- for non-`-pg` profiles, use `sqlite:////data/sma-ng.db`
- for `*-pg` profiles, set `db_url` to the PostgreSQL URL
- database connection details are not read from `daemon.env`

## 5. Create a Compose `.env` File

From the repo root:

```bash
cat > docker/.env <<'EOF'
IMAGE_TAG=latest
PORT=8585
EOF
```

Use `docker/.env` only for values that Docker Compose itself expands from `docker-compose.yml`, such as:

- `IMAGE_TAG`
- `PORT`
- `STOP_GRACE_PERIOD`

GPU device permissions (`/dev/dri/*`) are reconciled automatically by the container entrypoint — no `RENDER_GID`/`VIDEO_GID` configuration is required.

Put daemon/container settings in `/opt/sma/config/daemon.env` instead.

## 6. Install the `sma` Host Command

`sma` is one POSIX `sh` script shipped inside the image, so it always matches the image version. Install it on the
Docker host (no sourcing needed; works from `sh`, `bash`, `zsh`, cron, and `ssh host sma ...`):

```bash
# Copies the script out of the image without running it. `latest` is linux/amd64 only; --platform lets an arm64
# host fetch it too (the script is architecture-independent).
id=$(docker create --platform linux/amd64 ghcr.io/newdave/sma-ng:latest)
sudo docker cp "$id":/usr/local/bin/sma-host /usr/local/bin/sma
docker rm "$id"
```

`mise run setup:docker:target` also installs it to `/usr/local/bin/sma` from the repo checkout. Once the container
is running, `sudo docker cp sma-ng:/usr/local/bin/sma-host /usr/local/bin/sma` installs the exact running version.

See the [`sma` CLI Quick Start](sma-cli-quickstart.md) for the command list, path rules, scripting and cron, exit
codes, choosing a container and troubleshooting.

### One path per file

Pass the same path you see on the host. `/mnt`, `/transcode`, and `/recycle` are mounted at identical paths; `/config`,
`/logs`, and `/data` are container-only (use `sma shell` to inspect them). `sma` rejects any host path that is not
under a mount whose host and container paths match, with exit code `2` and a single-line error, before running
anything.

### Migrating from the alias file

- `source /opt/sma/sma-ng-docker-aliases.sh` is no longer needed. The file is now a deprecated shim that forwards the
  old `sma-*` names to `sma` and will be removed in a later release.
- The `/mnt/unionfs/downloads:/downloads` mount was removed from every compose service. Replace `/downloads/...`
  arguments with `/mnt/unionfs/downloads/...`.
- Webhooks from download clients that still report `/downloads/...` keep working with a path rewrite in `sma-ng.yml`:

  ```yaml
  daemon:
    path-rewrites:
      - from: /downloads
        to: /mnt/unionfs/downloads
  ```

- CLI output is now owned by `ubuntu` instead of `root`. Fix old root-owned output once with
  `docker exec -u 0 sma-ng chown -R ubuntu:ubuntu <path>` if needed.

## Stop grace period

Every daemon service sets `stop_grace_period: ${STOP_GRACE_PERIOD:-2m}`. `docker compose stop`, `restart`, and
`mise run cluster:stop` / `cluster:restart` give the daemon that long to drain before Docker sends SIGKILL; jobs still
running at the deadline are requeued. Raise it for long transcodes by setting `STOP_GRACE_PERIOD=10m` in a `.env` file
next to `docker-compose.yml` or in the shell running compose (compose substitution; `daemon.env` does not apply).

## 7. Edit `daemon.env`

For single-node non-`-pg` profiles, set `daemon.db_url` in `sma-ng.yml` to:

```yaml
daemon:
  db_url: sqlite:////data/sma-ng.db
```

The SQLite file is stored on the Docker host at `/opt/sma/data/sma-ng.db`.
Set `daemon.node_id` and `daemon.api_key` in `sma-ng.yml` if you want stable identity and API protection:

```yaml
daemon:
  node_id: media-node-a
  api_key: change-me-too
```

For the `-pg` profiles (`software-pg`, `intel-pg`, `nvidia-pg`), point the daemon at your PostgreSQL server:

```yaml
daemon:
  node_id: media-node-a
  api_key: change-me-too
  db_url: postgresql://sma:password@db-host:5432/sma
```

## 8. Start a Profile

From the repo root:

| Profile       | Hardware          | Database                            |
| ------------- | ----------------- | ----------------------------------- |
| `software`    | CPU only          | SQLite (`/opt/sma/data/sma-ng.db`)  |
| `software-pg` | CPU only          | external PostgreSQL (`db_url`)      |
| `intel`       | Intel QSV / VAAPI | SQLite                              |
| `intel-pg`    | Intel QSV / VAAPI | external PostgreSQL (`db_url`)      |
| `nvidia`      | NVIDIA NVENC      | SQLite                              |
| `nvidia-pg`   | NVIDIA NVENC      | external PostgreSQL (`db_url`)      |

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml --profile intel up -d
```

## 9. Verify

Check the containers:

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml ps
```

Check daemon health:

```bash
curl http://localhost:8585/health
```

Open the dashboard:

```text
http://localhost:8585/dashboard
```

## Hardware-Specific Notes

### Intel QSV

Requirements:

- host Intel GPU
- `/dev/dri` available
- correct render/video group IDs

If the Intel GPU is exposed through SR-IOV, confirm which render node belongs to the VF inside the guest. The working device is often `/dev/dri/renderD129` or higher rather than `/dev/dri/renderD128`.
The Intel compose profiles now mount the whole `/dev/dri` tree so the container can see the matching `card*` and `renderD*` nodes together, which matters on KVM guests where the VF may show up as `card1` with `renderD128`.

Quick check:

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml exec sma-intel vainfo
```

Use `sma-intel-pg` as the service name with the `intel-pg` profile.

### NVIDIA

Requirements:

- NVIDIA driver installed on host
- `nvidia-container-toolkit` installed

Quick check:

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml exec sma-nvidia ffmpeg -hide_banner -encoders
```

Or:

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml exec sma-nvidia-pg ffmpeg -hide_banner -encoders
```

## Submitting a Test Job

Plain text webhook:

```bash
curl -X POST http://localhost:8585/webhook/generic \
  -H "X-API-Key: change-me-too" \
  -d "/mnt/unionfs/Media/Movies/Test Movie (2024)/movie.mkv"
```

JSON webhook:

```bash
curl -X POST http://localhost:8585/webhook/generic \
  -H "X-API-Key: change-me-too" \
  -H "Content-Type: application/json" \
  -d '{"path":"/mnt/unionfs/Media/Movies/Test Movie (2024)/movie.mkv"}'
```

## Common Customizations

### Change the published daemon port

In `docker/.env`:

```bash
PORT=8686
```

### Use an external PostgreSQL instance

Use the matching `-pg` profile and set `daemon.db_url` in `sma-ng.yml`:

```yaml
daemon:
  db_url: postgresql://sma:password@db-host:5432/sma
```

Database URLs in `/opt/sma/config/daemon.env` are ignored.

### Route different media roots to different profiles

```yaml
Daemon:
  default_config: /config/sma-ng.yml
  path_configs:
    - path: /mnt/unionfs/Media/TV
      profile: rq
    - path: /mnt/unionfs/Media/Movies
      profile: lq
```

## Updating

Pull new code and recreate the selected profile:

```bash
git pull --rebase
docker compose --env-file docker/.env -f docker/docker-compose.yml --profile software-pg up -d --build
```

Replace `software-pg` with whichever profile you use.

## Troubleshooting

### Daemon starts but jobs fail immediately

- check `/opt/sma/config/sma-ng.yml`
- verify media paths inside the container
- verify `ffmpeg` and `ffprobe` paths

### Jobs are accepted but never run

- check PostgreSQL connectivity for `*-pg` or external DB mode
- check `/health` and `/status`
- check `/opt/sma/logs`

### Intel profile cannot see `/dev/dri`

- verify host device exists (`ls -l /dev/dri` on the host)
- for SR-IOV guests, verify the guest itself exposes a matching `card*` and `renderD*` pair under `/dev/dri`
- check container startup logs for `granted ubuntu access to /dev/dri/...` lines confirming the entrypoint added the runtime user to the host render/video groups
- verify `vainfo` works inside the container

### NVIDIA profile starts without GPU

- verify `nvidia-container-toolkit`
- verify host driver installation
- check `docker info` and container runtime configuration
