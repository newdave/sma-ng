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
Docker host under `/opt/sma/data/sma-ng.db`. The `-pg` profiles include a bundled PostgreSQL container for
clustered deployments or operators who explicitly want PostgreSQL.

With the current compose layout, use config files for different jobs:

- `/opt/sma/config/sma-ng.yml` → daemon runtime settings
- `/opt/sma/config/daemon.env` → container-only settings (`POSTGRES_*`, `LIBVA_DRIVER_NAME`, `NVIDIA_*`)
- `docker/.env` → Compose interpolation (`IMAGE_TAG`, `PORT`, `PGSQL_BIND_IP`, `PGSQL_PORT`)

For clustered Docker deployments, set `daemon.node_id` in
`/opt/sma/config/sma-ng.yml`.

Bundled PostgreSQL is published on the Docker host by default, so other machines on your network can reach it via the Docker host IP and `PGSQL_PORT` (subject to host firewall rules).

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
/transcodes/sma/
```

The default compose mounts are:

- `/opt/sma/config` → container `/config`
- `/opt/sma/logs` → container `/logs`
- `/opt/sma/data` → container `/data`
- `/mnt` → container `/mnt`
- `/mnt/unionfs/downloads` → container `/downloads`
- `/transcodes` → container `/transcodes`

Adjust the compose file if your host uses different media paths.

The installer accepts environment overrides:

```bash
INSTALL_DIR=/srv/sma TRANSCODE_DIR=/srv/transcodes mise run setup:docker:target
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
PGSQL_BIND_IP=0.0.0.0
PGSQL_PORT=5432
EOF
```

Use `docker/.env` only for values that Docker Compose itself expands from `docker-compose.yml`, such as:

- `IMAGE_TAG`
- `PORT`
- `PGSQL_BIND_IP`
- `PGSQL_PORT`

GPU device permissions (`/dev/dri/*`) are reconciled automatically by the container entrypoint — no `RENDER_GID`/`VIDEO_GID` configuration is required.

Put daemon/container settings in `/opt/sma/config/daemon.env` instead.

## 6. Load CLI Aliases

The installer writes a sourceable Bash helper snippet to `/opt/sma/sma-ng-docker-aliases.sh`.
Load it in your interactive shell:

```bash
source /opt/sma/sma-ng-docker-aliases.sh
```

Useful aliases:

| Alias                                     | Command                                              |
| ----------------------------------------- | ---------------------------------------------------- |
| `sma-manual`                              | Run `python manual.py` inside the `sma-ng` container |
| `sma-convert /mnt/unionfs/Media/file.mkv` | Run `manual.py -i <file> -a`                         |
| `sma-preview /mnt/unionfs/Media/file.mkv` | Run `manual.py -i <file> -oo`                        |
| `sma-codecs`                              | Run `manual.py -cl`                                  |
| `sma-smoke`                               | Run `python daemon.py --smoke-test`                  |
| `sma-rename`                              | Run `python rename.py`                               |
| `sma-logs`                                | Follow `docker logs` for the `sma-ng` container      |
| `sma-shell`                               | Open an interactive shell in the `sma-ng` container  |

Paths passed to these aliases must be container-visible paths, such as `/mnt/...`, `/downloads/...`,
`/transcodes/...`, or another path mounted into the compose service.

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

For bundled PostgreSQL profiles (`software-pg`, `intel-pg`, `nvidia-pg`), set matching `POSTGRES_*` values in `daemon.env` and set the daemon database URL in `sma-ng.yml`:

```bash
POSTGRES_USER=sma
POSTGRES_DB=sma
POSTGRES_PASSWORD=change-me-now
```

```yaml
daemon:
  node_id: media-node-a
  api_key: change-me-too
  db_url: postgresql://sma:change-me-now@sma-pgsql:5432/sma
```

Because the bundled PostgreSQL service publishes `5432` on the Docker host by default, external tools can usually connect with a URL like `postgresql://sma:change-me-now@<docker-host-ip>:5432/sma` while compose-managed SMA containers continue using the internal `sma-pgsql` hostname.

For non-`-pg` profiles that should use external PostgreSQL instead of SQLite, point the daemon at that database:

```yaml
daemon:
  node_id: media-node-a
  api_key: change-me-too
  db_url: postgresql://sma:password@db-host:5432/sma
```

## 8. Start a Profile

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

### Change or restrict the published PostgreSQL address

In `docker/.env`:

```bash
PGSQL_BIND_IP=127.0.0.1
PGSQL_PORT=5433
```

Use `127.0.0.1` if you want PostgreSQL reachable only from the Docker host. Leave the default `0.0.0.0` binding if you want it reachable via the Docker host IP on your network.

### Use an external PostgreSQL instance

Use a non-`-pg` profile and set `daemon.db_url` in `sma-ng.yml`:

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
