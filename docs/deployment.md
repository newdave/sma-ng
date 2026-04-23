# Deployment

SMA-NG uses [mise](https://mise.jdx.dev/) as a task runner for local development and remote deployments.
Install it once with the one-liner below — see the
[mise installation docs](https://mise.jdx.dev/getting-started.html) for package manager and Windows options.

```bash
curl https://mise.run | sh
```

## Task Reference

### Setup

| Task | Description |
| --- | --- |
| `mise run venv` | Create the Python virtual environment |
| `mise run install` | Install base runtime dependencies from `setup/requirements.txt` |
| `mise run install-dev` | Install dev dependencies (lint, test tools) |
| `mise run install-all` | Install all optional dependencies including qBittorrent and Deluge integrations |
| `mise run clean` | Remove build artifacts, caches, and compiled bytecode |

### Development

| Task | Description |
| --- | --- |
| `mise run lint` | Run the ruff linter and report issues |
| `mise run lint-fix` | Run the ruff linter and auto-fix issues |
| `mise run test` | Run the test suite (daemon tests require `TEST_DB_URL`) |
| `mise run test-cov` | Run tests with coverage report (HTML + terminal summary) |

### Media Tools

| Task | Description |
| --- | --- |
| `mise run daemon` | Start the daemon HTTP server on `0.0.0.0:8585` |
| `mise run convert -- /path/to/file.mkv` | Convert a media file with auto-tagging |
| `mise run preview -- /path/to/file.mkv` | Preview FFmpeg conversion options without converting |
| `mise run codecs` | List all supported video and audio codecs |
| `mise run rename -- /path/to/file-or-dir` | Rename media files using naming templates |

### GPU and Configuration

| Task | Description |
| --- | --- |
| `mise run detect-gpu` | Detect available GPU type (`nvenc`, `qsv`, `vaapi`, `videotoolbox`, or `software`) |
| `mise run config` | Generate `config/` ini files with GPU auto-detection |

### Docker Tasks

| Task | Description |
| --- | --- |
| `mise run docker:build` | Build the Docker image locally for the native platform |
| `mise run docker:push` | Build and push a multi-arch image (`linux/amd64` + `linux/arm64`) — requires `IMAGE=` |
| `mise run docker:run` | Run the locally-built image — requires `SMA_DAEMON_DB_URL` |
| `mise run docker:shell` | Open an interactive shell inside the locally-built image |
| `mise run docker:smoke` | Smoke-test the image: verify Python imports and FFmpeg binary |

### Deploy Tasks

| Task | Description |
| --- | --- |
| `mise run deploy:check` | Verify `setup/.local.ini` exists and `DEPLOY_HOSTS` is set |
| `mise run deploy:setup` | First-time host prep: SSH key, apt deps, deploy dir, systemd install |
| `mise run deploy:run` | Sync code, install dependencies, and reload systemd on all hosts |
| `mise run deploy:config` | Roll configs to remote hosts: create missing files, merge new keys, stamp credentials |
| `mise run deploy:restart` | Force-restart the `sma-daemon` systemd service on all hosts |
| `mise run deploy:docker-upgrade` | Rsync code to Docker hosts, pull latest image, and recreate the SMA container |
| `mise run deploy:docker-pg-restart` | Restart bundled PostgreSQL on hosts using `*-pg` Docker profiles |
| `mise run deploy:docker-pg-recreate` | Remove and recreate bundled PostgreSQL (destructive — removes `sma-pgdata` volume) |
| `mise run deploy:remote-make` | Run an arbitrary make target on all hosts (`REMOTE_MAKE=test mise run deploy:remote-make`) |
| `mise run deploy:ghcr-login` | Log in to `ghcr.io` on all `DEPLOY_HOSTS` using a GitHub token |
| `mise run systemd-install` | Install the systemd service file (run as root on the target host) |

Run `mise tasks` to print a live list directly from the repo.

## Examples

### Generate config, overriding GPU type

```bash
GPU=nvenc mise run config
```

Useful on machines where auto-detection picks the wrong encoder (for example, when both Intel and NVIDIA GPUs are
present and you want to force one).

### Preview conversion options before committing

```bash
mise run preview -- /mnt/media/movies/test-file.mkv
```

Prints the full FFmpeg command and stream map without touching the file.
Use this to verify encoder selection, stream copying, and audio downmix decisions before running a real conversion.

### Run tests with coverage and open the report

```bash
mise run test-cov && open htmlcov/index.html
```

Generates an HTML report in `htmlcov/` so you can browse coverage by file and line.
On Linux, replace `open` with `xdg-open`.

### Smoke-test the Docker image before deploying

```bash
mise run docker:build && mise run docker:smoke
```

Builds a local `sma-ng:local` image and immediately verifies Python imports and FFmpeg availability inside it.
No containers are left running afterwards.

### Push a multi-architecture image to a registry

```bash
IMAGE=ghcr.io/myorg/sma-ng:2.0.0 mise run docker:push
```

Builds for both `linux/amd64` and `linux/arm64` via `docker buildx` and pushes both manifests.
Requires `docker buildx` and registry credentials.

### Run the test suite on all remote hosts

```bash
REMOTE_MAKE=test mise run deploy:remote-make
```

SSHes into every host in `DEPLOY_HOSTS` and runs `make test` in `DEPLOY_DIR`.
Useful for verifying a code deployment before switching over.

### Open a shell in the Docker image for debugging

```bash
mise run docker:shell
```

Starts the locally-built `sma-ng:local` image with `/config` and `/logs` bind-mounted and drops you into `/bin/sh`.
You can run `python manual.py -cl` or inspect config files without starting the full daemon.

---

## Remote Deployment

### Configuration

Copy the sample and fill in your details:

```bash
cp setup/.local.ini.sample setup/.local.ini
```

`setup/.local.ini` is gitignored. It controls deploy targets, credentials, and per-host overrides:

```ini
[deploy]
DEPLOY_HOSTS = user@server1.example.com user@server2.example.com
DEPLOY_DIR   = ~/sma
SSH_KEY      = ~/.ssh/id_ed25519_sma
FFMPEG_DIR   = /usr/local/bin

[daemon]
api_key = your_secret_key
db_url  =                    # required for multi-node: postgresql://user:pass@host/db

[Sonarr]
host        = sonarr.example.com
port        = 443
ssl         = true
apikey      = abc123
media_path  = /mnt/media/TV
config_file = config/autoProcess.sonarr.ini

[Radarr]
host        = radarr.example.com
apikey      = def456
media_path  = /mnt/media/Movies
config_file = config/autoProcess.radarr.ini

# Per-host override
[user@server1.example.com]
DEPLOY_DIR = /opt/sma
SSH_PORT   = 2222
FFMPEG_DIR = /opt/ffmpeg/bin
```

### Deployment Workflow

```bash
# 1. First-time: SSH key, apt deps, deploy dir, systemd install
mise run deploy:setup

# 2. Sync code, install deps, reload systemd
mise run deploy:run

# 3. Push configs (create missing, merge new keys, stamp credentials)
mise run deploy:config

# 4. Restart daemon on all hosts
mise run deploy:restart

# Optional: sync code to Docker hosts, pull the latest image, and recreate
# only the SMA service for each configured profile
mise run deploy:docker-upgrade

# Optional: restart or recreate bundled PostgreSQL on hosts using *-pg profiles
mise run deploy:docker-pg-restart
mise run deploy:docker-pg-recreate
```

### What `deploy:config` Does

For each remote host:

1. Detects GPU type remotely and sets `gpu =` in the generated config
2. Creates missing config files from samples (`autoProcess.ini`, `daemon.json`, `daemon.env`)
3. Merges new keys from updated samples into existing configs (non-destructive — existing values preserved)
4. Stamps service credentials from `setup/.local.ini` into all `*.ini` files
5. Sets `ffmpeg`/`ffprobe` paths from `FFMPEG_DIR` in every `.ini`
6. Stamps daemon credentials (`api_key`, `db_url`, `ffmpeg_dir`) into `daemon.json` and `daemon.env`
7. Deploys post-process scripts with correct interpreter shebang and credentials

### Deploy Tasks Reference

| Task | Description |
| --- | --- |
| `deploy:check` | Verify `setup/.local.ini` exists and `DEPLOY_HOSTS` is set |
| `deploy:setup` | First-time host prep: SSH key, apt deps, deploy dir, systemd install |
| `deploy:run` | Sync code + install deps + reload systemd on all hosts |
| `deploy:config` | Roll configs: create missing, merge new keys, stamp credentials |
| `deploy:restart` | Restart `sma-daemon` on all hosts |
| `deploy:docker-upgrade` | Rsync the local codebase to each Docker host, pull the latest image for that host's `DOCKER_PROFILE`, and recreate only the SMA container |
| `deploy:docker-pg-restart` | Restart bundled PostgreSQL on hosts whose `DOCKER_PROFILE` ends in `-pg` |
| `deploy:docker-pg-recreate` | Stop bundled PostgreSQL, remove its Docker volume, and recreate it on hosts whose `DOCKER_PROFILE` ends in `-pg` |
| `deploy:remote-make` | Run an arbitrary make target on all hosts (`REMOTE_MAKE=test mise run deploy:remote-make`) |

The Docker-specific deploy tasks require `DOCKER_PROFILE` to be set per host (or in `[deploy]`) in `setup/.local.ini`. The PostgreSQL lifecycle tasks skip hosts that are not using one of the bundled `*-pg` profiles.
Use `deploy:docker-pg-recreate` only when you intentionally want a fresh bundled PostgreSQL data directory on the remote host; it removes the compose-managed `sma-pgdata` volume before bringing the service back.

---

## Systemd Service

The daemon can be installed as a systemd service:

```bash
# Install and enable locally
make systemd-install

# Or via mise (deploys to all remote hosts)
mise run deploy:run
```

Service unit: `setup/sma-daemon.service`

Key settings:

- Loads `config/daemon.env` for environment overrides
- `TimeoutStopSec=10` — sends SIGKILL 10 seconds after SIGTERM if the process has not exited
- `KillMode=mixed` — SIGTERM triggers graceful drain
- Default `ReadWritePaths`: `/opt/sma/config /opt/sma/logs /transcodes /mnt` — add any additional paths your setup needs

Override the service user:

```bash
SERVICE_USER=myuser make systemd-install
```

---

## Docker

A Docker image is published to GHCR on every release.

```bash
# Pull and run
docker run --rm -p 8585:8585 \
  -v /your/config:/config \
  -v /your/logs:/logs \
  ghcr.io/newdave/sma-ng:latest

# Build locally
make docker-build

# Run locally-built image
SMA_DAEMON_DB_URL=postgresql://user:pass@host/db make docker-run
```

**Tags:** `latest`, `1`, `1.2`, `1.2.3` (semver), `main` (rolling build from main branch).

For hardware acceleration diagnostics in containers, the runtime image includes `vainfo` and VAAPI userspace drivers.
For Intel/QSV setups, use either the Intel profile (`docker compose --profile intel up`) or the bundled-PostgreSQL Intel profile (`docker compose --profile intel-pg up`) so `/dev/dri` is mapped into the container. This is important on SR-IOV guests where the Intel VF may appear as `card1` while still using `renderD128`.
The bundled PostgreSQL compose service publishes `5432` on the Docker host by default using `PGSQL_BIND_IP`/`PGSQL_PORT` from `docker/.env` (defaults: `0.0.0.0` and `5432`). That makes the database reachable via the Docker host IP unless you intentionally restrict it to `127.0.0.1` or a more specific interface.

**Environment variables for Docker:**

| Variable | Default | Description |
| --- | --- | --- |
| `SMA_DAEMON_HOST` | `0.0.0.0` | Bind host |
| `SMA_DAEMON_PORT` | `8585` | Port |
| `SMA_DAEMON_WORKERS` | `2` | Worker count |
| `SMA_DAEMON_API_KEY` | | API key |
| `SMA_DAEMON_DB_URL` | | PostgreSQL connection URL (required) |
| `SMA_DAEMON_FFMPEG_DIR` | | Directory containing `ffmpeg`/`ffprobe` |
| `SMA_CONFIG` | | Override `autoProcess.ini` path |

See also:

- [Docker Compose Quick Start](docker-compose-quickstart.md)
- [Environment Architecture](environment-architecture.md)
- [Multi-Instance Deployment](multi-instance-deployment.md)

---

## CI / Release

| Workflow | Trigger | Description |
| --- | --- | --- |
| `ci.yml` | PR / push to main | Runs test suite |
| `docker.yml` | PR / push to main or `v*` tag | PR: build-only + smoke test; main/tag: build + push to GHCR |
| `release.yml` | Push to main | release-please manages release PR + version bump; on release: wheel/sdist + Docker semver tags |

Releases are driven by [release-please](https://github.com/googleapis/release-please). **Do not manually create `v*` tags** — this causes duplicate releases.

This repository pins release-please to the `always-bump-patch` versioning strategy, so releases default to point releases and patch numbers are not capped. Versions such as `1.2.12323` are valid.

Conventional commit types still control changelog grouping and breaking-change signaling, but by default they do not change the release from a point release:

- `fix:` → patch bump
- `feat:` → patch bump
- `feat!:` or `BREAKING CHANGE:` → patch bump unless a one-off override is used
