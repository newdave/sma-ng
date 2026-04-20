# Deployment

SMA-NG uses [mise](https://mise.jdx.dev/) as a task runner for local development and remote deployments.

## Local Development Tasks

```bash
mise run install          # Create venv and install dependencies
mise run install-dev      # Install dev + test dependencies
mise run test             # Run test suite
mise run lint             # Run ruff linter
mise run lint-fix         # Auto-fix lint issues
mise run detect-gpu       # Detect available GPU acceleration
mise run config           # Generate config with auto-detected GPU
mise run daemon           # Start daemon on 0.0.0.0:8585
mise run convert -- /path/to/file.mkv   # Convert a file
mise run preview -- /path/to/file.mkv   # Preview options only
mise run codecs           # List supported codecs
```

### Docker Tasks

```bash
mise run docker:build     # Build image locally
mise run docker:run       # Run locally-built image
mise run docker:shell     # Open shell in locally-built image
mise run docker:smoke     # Smoke-test imports and ffmpeg
```

---

## Remote Deployment

### Prerequisites

```bash
# Install mise
make install-mise
```

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
| `deploy:remote-make` | Run an arbitrary make target on all hosts (`REMOTE_MAKE=test mise run deploy:remote-make`) |

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
make docker-run
```

**Tags:** `latest`, `1`, `1.2`, `1.2.3` (semver), `main` (rolling build from main branch).

For hardware acceleration diagnostics in containers, the runtime image includes `vainfo` and VAAPI userspace drivers.
For Intel/QSV setups, use either the Intel profile (`docker compose --profile intel up`) or the bundled-PostgreSQL Intel profile (`docker compose --profile intel-pg up`) so `/dev/dri` is mapped.

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

Conventional commit types:

- `fix:` → patch bump
- `feat:` → minor bump
- `feat!:` or `BREAKING CHANGE:` → major bump
