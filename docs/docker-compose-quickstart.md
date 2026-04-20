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

If you want environment-variable overrides, also create:

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

## 5. Create a `.env` File

From the repo root:

```bash
cat > docker/.env <<'EOF'
POSTGRES_PASSWORD=change-me-now
SMA_DAEMON_API_KEY=change-me-too
SMA_PORT=8585
EOF
```

You can also set:

- `POSTGRES_USER`
- `POSTGRES_DB`
- `PGSQL_PORT`
- `RENDER_GID`
- `VIDEO_GID`
- `LIBVA_DRIVER_NAME`

## 6. Start a Profile

From the repo root:

### Software encode with bundled PostgreSQL

```bash
docker compose -f docker/docker-compose.yml --profile software-pg up -d
```

### Software encode without bundled PostgreSQL

```bash
docker compose -f docker/docker-compose.yml --profile software up -d
```

### Intel QSV with bundled PostgreSQL

```bash
docker compose -f docker/docker-compose.yml --profile intel-pg up -d
```

### Intel QSV without bundled PostgreSQL

```bash
docker compose -f docker/docker-compose.yml --profile intel up -d
```

### NVIDIA with bundled PostgreSQL

```bash
docker compose -f docker/docker-compose.yml --profile nvidia-pg up -d
```

### NVIDIA without bundled PostgreSQL

```bash
docker compose -f docker/docker-compose.yml --profile nvidia up -d
```

## 7. Verify

Check the containers:

```bash
docker compose -f docker/docker-compose.yml ps
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

Quick check:

```bash
docker compose -f docker/docker-compose.yml exec sma-intel vainfo
```

Or for the bundled-PostgreSQL profile:

```bash
docker compose -f docker/docker-compose.yml exec sma-intel-pg vainfo
```

### NVIDIA

Requirements:

- NVIDIA driver installed on host
- `nvidia-container-toolkit` installed

Quick check:

```bash
docker compose -f docker/docker-compose.yml exec sma-nvidia ffmpeg -hide_banner -encoders
```

Or:

```bash
docker compose -f docker/docker-compose.yml exec sma-nvidia-pg ffmpeg -hide_banner -encoders
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

### Use an external PostgreSQL instance

Use a non-`-pg` profile and set:

```bash
SMA_DAEMON_DB_URL=postgresql://sma:password@db-host:5432/sma
```

Place it in:

- `docker/.env`, or
- `/opt/sma/config/daemon.env`, or
- `daemon.json`

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
docker compose -f docker/docker-compose.yml --profile software-pg up -d --build
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
- verify `RENDER_GID` and `VIDEO_GID`
- verify `vainfo` works inside the container

### NVIDIA profile starts without GPU

- verify `nvidia-container-toolkit`
- verify host driver installation
- check `docker info` and container runtime configuration
