# Docker Compose Quick Start

This guide runs the SMA-NG daemon with Docker Compose. Compose is optional: on a single host,
`sma daemon up --media /path/to/media` starts the same daemon without it (see
[Running the daemon](sma-cli-quickstart.md#11-running-the-daemon)). Use Compose when you want its profiles, override
files, or the cluster deployment tooling.

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

Install the `sma` command first; it creates `/opt/sma/config`, `/opt/sma/logs` and `/opt/sma/data` and a sample
`/opt/sma/config/sma-ng.yml`, and needs no source checkout:

```bash
curl -fsSL https://raw.githubusercontent.com/newdave/sma-ng/main/install-smang.sh | sh
```

Then fetch the compose file and the container settings file it requires:

```bash
sudo mkdir -p /opt/sma/docker /transcode /recycle
sudo curl -fsSL -o /opt/sma/docker/docker-compose.yml \
  https://raw.githubusercontent.com/newdave/sma-ng/main/docker/docker-compose.yml
docker run --rm --entrypoint cat ghcr.io/newdave/sma-ng:latest /app/setup/daemon.env.sample \
  | sudo tee /opt/sma/config/daemon.env >/dev/null
```

The default compose mounts are:

- `/opt/sma/config` → container `/config`
- `/opt/sma/logs` → container `/logs`
- `/opt/sma/data` → container `/data`
- `/mnt` → container `/mnt`
- `/transcode` → container `/transcode`
- `/recycle` → container `/recycle`

Adjust the compose file if your host uses different media paths; keep host and container paths identical so
webhook paths resolve inside the daemon.

## 2. Config Files

The installer writes `/opt/sma/config/sma-ng.yml` only when it does not exist, and `daemon.env` above comes from
the image's sample. Existing local config is never overwritten. Fresh samples are written to
`/opt/sma/config/defaults/` each time the container starts, so you can diff your config against them.

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

`sma` runs SMA-NG's tools (convert, preview, repair, audit, rename) from the Docker host. Install it with the one-line
installer, which needs only Docker:

```bash
curl -fsSL https://raw.githubusercontent.com/newdave/sma-ng/main/install-smang.sh | sh
```

Each `sma` command runs in its own throwaway container that mounts exactly the paths you name and reads the same
`/opt/sma/config/sma-ng.yml` as the daemon, so it works alongside a running daemon. Paths no longer have to sit
under the daemon's `/mnt`, `/transcode` and `/recycle` mounts. See the [`sma` CLI Quick Start](sma-cli-quickstart.md)
for every command, what gets mounted, exit codes and upgrading.

The daemon itself still uses the compose mounts above: webhook paths must be visible at the same path inside the
daemon container, so keep media under `/mnt` (or add mounts) for Sonarr, Radarr and other integrations.

### Migrating from the alias file

- The old `sma-*` alias file was removed; delete the `source` line for it from your shell profile and use `sma`.
- The `/mnt/unionfs/downloads:/downloads` mount was removed from every compose service. Replace `/downloads/...`
  arguments with `/mnt/unionfs/downloads/...`.
- Webhooks from download clients that still report `/downloads/...` keep working with a path rewrite in `sma-ng.yml`:

  ```yaml
  daemon:
    path-rewrites:
      - from: /downloads
        to: /mnt/unionfs/downloads
  ```

- `sma` runs commands as your own user, so new files are owned by you. Fix old root-owned output once with
  `sudo chown -R "$USER" <path>` if needed.

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
