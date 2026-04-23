# Docker Compose Quick Start

This is the fastest way to get the SMA-NG daemon running with Docker Compose.

The repository ships a compose file at [`docker/docker-compose.yml`](../docker/docker-compose.yml) with six profiles:

- `software`
- `software-pg`
- `intel`
- `intel-pg`
- `nvidia`
- `nvidia-pg`

The `-pg` profiles include a bundled PostgreSQL container. The non-`-pg` profiles are for external PostgreSQL or single-node/no-cluster setups where you will supply `SMA_DAEMON_DB_URL` yourself.

With the current compose layout, use the two env files for different jobs:

- `/opt/sma/config/daemon.env` → container/runtime settings (`SMA_NODE_NAME`, `SMA_DAEMON_*`, `POSTGRES_*`, `LIBVA_DRIVER_NAME`, `NVIDIA_*`)
- `docker/.env` → Compose interpolation (`SMA_IMAGE_TAG`, `SMA_PORT`, `PGSQL_BIND_IP`, `PGSQL_PORT`, `RENDER_GID`, `VIDEO_GID`)

For clustered Docker deployments, set `SMA_NODE_NAME` in
`/opt/sma/config/daemon.env`. The daemon uses that value directly as its
cluster node ID, so container hostnames no longer need to carry identity.

Bundled PostgreSQL is published on the Docker host by default, so other machines on your network can reach it via the Docker host IP and `PGSQL_PORT` (subject to host firewall rules).

## 1. Prepare Host Directories

Create the persistent directories the compose file expects:

```bash
sudo mkdir -p /opt/sma/config /opt/sma/logs /transcodes
sudo chown -R "$USER":"$USER" /opt/sma /transcodes
```

The default compose mounts are:

- `/opt/sma/config` → container `/config`
- `/opt/sma/logs` → container `/logs`
- `/mnt` → container `/mnt`
- `/mnt/unionfs/downloads` → container `/downloads`
- `/transcodes` → container `/transcodes`

Adjust the compose file if your host uses different media paths.

## 2. Create Config Files

At minimum, create:

```bash
cp setup/autoProcess.ini.sample /opt/sma/config/autoProcess.ini
cp setup/daemon.json.sample /opt/sma/config/daemon.json
```

If you want runtime environment overrides, also create:

```bash
cp setup/daemon.env.sample /opt/sma/config/daemon.env 2>/dev/null || true
```

If `setup/daemon.env.sample` is not present in your checkout, just create `/opt/sma/config/daemon.env` manually.

## 3. Edit `autoProcess.ini`

Set the basics in `/opt/sma/config/autoProcess.ini`:

- `ffmpeg = /usr/local/bin/ffmpeg`
- `ffprobe = /usr/local/bin/ffprobe`
- your desired codec/container settings
- Sonarr/Radarr/Plex sections if needed

For hardware encoders, also set the appropriate `gpu =` and codec values. See [Hardware Acceleration](hardware-acceleration.md).

## 4. Edit `daemon.json`

Minimal example:

```json
{
  "default_config": "/config/autoProcess.ini",
  "api_key": "change-me",
  "db_url": null,
  "path_configs": [
    {"path": "/mnt/media/TV", "config": "/config/autoProcess.ini"},
    {"path": "/mnt/media/Movies", "config": "/config/autoProcess.ini"}
  ]
}
```

Notes:

- `/config/...` paths are inside-container paths
- `/mnt/...` paths must match the container view of your mounted media
- for `*-pg` profiles, `db_url` can stay `null` because the compose environment provides it
- for non-`-pg` profiles, set `db_url` or `SMA_DAEMON_DB_URL` yourself if you want PostgreSQL-backed clustering

## 5. Create a Compose `.env` File

From the repo root:

```bash
cat > docker/.env <<'EOF'
SMA_IMAGE_TAG=latest
SMA_PORT=8585
PGSQL_BIND_IP=0.0.0.0
PGSQL_PORT=5432
# RENDER_GID=109
# VIDEO_GID=44
EOF
```

Use `docker/.env` only for values that Docker Compose itself expands from `docker-compose.yml`, such as:

- `SMA_IMAGE_TAG`
- `SMA_PORT`
- `PGSQL_BIND_IP`
- `PGSQL_PORT`
- `RENDER_GID`
- `VIDEO_GID`

Put daemon/container settings in `/opt/sma/config/daemon.env` instead.

## 6. Edit `daemon.env`

For bundled PostgreSQL profiles (`software-pg`, `intel-pg`, `nvidia-pg`), set matching `POSTGRES_*` values plus the daemon database URL:

```bash
POSTGRES_USER=sma
POSTGRES_DB=sma
POSTGRES_PASSWORD=change-me-now
SMA_NODE_NAME=media-node-a
SMA_DAEMON_DB_URL=postgresql://sma:change-me-now@sma-pgsql:5432/sma
SMA_DAEMON_API_KEY=change-me-too
```

Because the bundled PostgreSQL service publishes `5432` on the Docker host by default, external tools can usually connect with a URL like `postgresql://sma:change-me-now@<docker-host-ip>:5432/sma` while compose-managed SMA containers continue using the internal `sma-pgsql` hostname.

For non-`-pg` profiles, point the daemon at your external database instead:

```bash
SMA_NODE_NAME=media-node-a
SMA_DAEMON_DB_URL=postgresql://sma:password@db-host:5432/sma
SMA_DAEMON_API_KEY=change-me-too
```

## 7. Start a Profile

From the repo root:

### Software encode with bundled PostgreSQL

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml --profile software-pg up -d
```

### Software encode without bundled PostgreSQL

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml --profile software up -d
```

### Intel QSV with bundled PostgreSQL

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml --profile intel-pg up -d
```

### Intel QSV without bundled PostgreSQL

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml --profile intel up -d
```

### NVIDIA with bundled PostgreSQL

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml --profile nvidia-pg up -d
```

### NVIDIA without bundled PostgreSQL

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml --profile nvidia up -d
```

## 8. Verify

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

Or for the bundled-PostgreSQL profile:

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml exec sma-intel-pg vainfo
```

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
  -d "/mnt/media/Movies/Test Movie (2024)/movie.mkv"
```

JSON webhook:

```bash
curl -X POST http://localhost:8585/webhook/generic \
  -H "X-API-Key: change-me-too" \
  -H "Content-Type: application/json" \
  -d '{"path":"/mnt/media/Movies/Test Movie (2024)/movie.mkv"}'
```

## Common Customizations

### Change the published daemon port

In `docker/.env`:

```bash
SMA_PORT=8686
```

### Change or restrict the published PostgreSQL address

In `docker/.env`:

```bash
PGSQL_BIND_IP=127.0.0.1
PGSQL_PORT=5433
```

Use `127.0.0.1` if you want PostgreSQL reachable only from the Docker host. Leave the default `0.0.0.0` binding if you want it reachable via the Docker host IP on your network.

### Use an external PostgreSQL instance

Use a non-`-pg` profile and set:

```bash
SMA_DAEMON_DB_URL=postgresql://sma:password@db-host:5432/sma
```

Place it in `/opt/sma/config/daemon.env` or `daemon.json`.

### Route different media roots to different configs

```json
{
  "default_config": "/config/autoProcess.ini",
  "path_configs": [
    {"path": "/mnt/media/TV", "config": "/config/autoProcess.tv.ini"},
    {"path": "/mnt/media/Movies", "config": "/config/autoProcess.movies.ini"}
  ]
}
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

- check `/opt/sma/config/autoProcess.ini`
- verify media paths inside the container
- verify `ffmpeg` and `ffprobe` paths

### Jobs are accepted but never run

- check PostgreSQL connectivity for `*-pg` or external DB mode
- check `/health` and `/status`
- check `/opt/sma/logs`

### Intel profile cannot see `/dev/dri`

- verify host device exists
- verify `RENDER_GID`
- verify `VIDEO_GID`
- for SR-IOV guests, verify the guest itself exposes a matching `card*` and `renderD*` pair under `/dev/dri`
- verify `vainfo` works inside the container

### NVIDIA profile starts without GPU

- verify `nvidia-container-toolkit`
- verify host driver installation
- check `docker info` and container runtime configuration
