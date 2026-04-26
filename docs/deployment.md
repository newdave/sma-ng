# Deployment

SMA-NG uses [mise](https://mise.jdx.dev/) as a task runner for local development and remote deployments.
Install it once with the one-liner below — see the
[mise installation docs](https://mise.jdx.dev/getting-started.html) for package manager and Windows options.

```bash
curl https://mise.run | sh
```

## Task Reference

### Setup

| Task                      | Description                                                                     |
| ------------------------- | ------------------------------------------------------------------------------- |
| `mise run setup:venv`     | Create the Python virtual environment                                           |
| `mise run setup:deps`     | Install base runtime dependencies from `setup/requirements.txt`                 |
| `mise run setup:deps:dev` | Install dev dependencies (lint, test tools)                                     |
| `mise run setup:deps:all` | Install all optional dependencies including qBittorrent and Deluge integrations |
| `mise run setup:clean`    | Remove build artifacts, caches, and compiled bytecode                           |

### Development

| Task                     | Description                                              |
| ------------------------ | -------------------------------------------------------- |
| `mise run test:lint`     | Run the ruff linter and report issues                    |
| `mise run dev:lint`      | Run the ruff linter and auto-fix issues                  |
| `mise run dev:format`    | Format Python code with ruff                             |
| `mise run dev:precommit` | Run pre-commit checks against all files                  |
| `mise run test:openapi`  | Validate `docs/openapi.yaml`                             |
| `mise run test`          | Run the test suite (daemon tests require `TEST_DB_URL`)  |
| `mise run test:cov`      | Run tests with coverage report (HTML + terminal summary) |
| `mise run test:daemon`   | Run focused daemon/API worker tests                      |
| `mise run test:deploy`   | Run focused deploy/config task tests                     |
| `mise run dev:check`     | Run the local CI-equivalent check set                    |

### Media Tools

| Task                                            | Description                                          |
| ----------------------------------------------- | ---------------------------------------------------- |
| `mise run daemon:start`                         | Start the daemon HTTP server on `0.0.0.0:8585`       |
| `mise run media:convert -- /path/to/file.mkv`   | Convert a media file with auto-tagging               |
| `mise run media:preview -- /path/to/file.mkv`   | Preview FFmpeg conversion options without converting |
| `mise run media:codecs`                         | List all supported video and audio codecs            |
| `mise run media:rename -- /path/to/file-or-dir` | Rename media files using naming templates            |

### GPU and Configuration

| Task                       | Description                                                                        |
| -------------------------- | ---------------------------------------------------------------------------------- |
| `mise run config:gpu`      | Detect available GPU type (`nvenc`, `qsv`, `vaapi`, `videotoolbox`, or `software`) |
| `mise run config:generate` | Generate `config/sma-ng.yml` with GPU auto-detection                         |
| `mise run config:audit`    | Audit local config files against the YAML sample                                   |
| `mise run daemon:smoke`    | Run daemon smoke-test config validation and exit                                   |

### Docker Tasks

| Task                    | Description                                                                           |
| ----------------------- | ------------------------------------------------------------------------------------- |
| `mise run build:docker` | Build the Docker image locally for the native platform                                |
| `mise run build:push`   | Build and push a multi-arch image (`linux/amd64` + `linux/arm64`) — requires `IMAGE=` |
| `mise run docker:run`   | Run the locally-built image — requires `SMA_DAEMON_DB_URL`                            |
| `mise run build:shell`  | Open an interactive shell inside the locally-built image                              |
| `mise run build:smoke`  | Smoke-test the image: verify Python imports and FFmpeg binary                         |

### Deploy Tasks

| Task                         | Description                                                                                 |
| ---------------------------- | ------------------------------------------------------------------------------------------- |
| `mise run deploy:check`      | Verify `setup/local.yml` exists and `DEPLOY_HOSTS` is set                                  |
| `mise run deploy:setup`      | First-time host prep: SSH key, apt deps, deploy dir, Docker install                        |
| `mise run deploy:mise`       | Sync the local `.mise/` deploy control plane to all hosts                                   |
| `mise run deploy:sync`       | Sync code and install dependencies on all hosts                            |
| `mise run config:roll`       | Roll configs to remote hosts: create missing files, merge new keys, stamp credentials       |
| `mise run deploy:restart`    | Gracefully shut down `sma-daemon` on all hosts, then restart its Docker container                  |
| `mise run config:audit`      | Audit local configs                                                                         |
| `mise run deploy:docker`     | Rsync code to Docker hosts, pull latest image, and recreate the SMA container               |
| `mise run pg:restart`        | Restart bundled PostgreSQL on hosts using `*-pg` Docker profiles                            |
| `mise run pg:recreate`       | Remove and recreate bundled PostgreSQL (destructive — removes `sma-pgdata` volume)          |
| `mise run deploy:login`      | Log in to `ghcr.io` on all `DEPLOY_HOSTS` using a GitHub token                              |

Also available: `mise run deploy:dockerstop` to stop Docker services on selected nodes.
Use `HOST=<host>` for one node or `HOSTS="<host1> <host2>"` for multiple nodes.

Run `mise tasks` to print a live list directly from the repo.

## Examples

### Generate config, overriding GPU type

```bash
GPU=nvenc mise run config:generate
```

Useful on machines where auto-detection picks the wrong encoder (for example, when both Intel and NVIDIA GPUs are
present and you want to force one).

### Preview conversion options before committing

```bash
mise run media:preview -- /mnt/media/movies/test-file.mkv
```

Prints the full FFmpeg command and stream map without touching the file.
Use this to verify encoder selection, stream copying, and audio downmix decisions before running a real conversion.

### Run tests with coverage and open the report

```bash
mise run test:cov && open htmlcov/index.html
```

Generates an HTML report in `htmlcov/` so you can browse coverage by file and line.
On Linux, replace `open` with `xdg-open`.

### Smoke-test the Docker image before deploying

```bash
mise run build:docker && mise run build:smoke
```

Builds a local `sma-ng:local` image and immediately verifies Python imports and FFmpeg availability inside it.
No containers are left running afterwards.

### Push a multi-architecture image to a registry

```bash
IMAGE=ghcr.io/myorg/sma-ng:2.0.0 mise run build:push
```

Builds for both `linux/amd64` and `linux/arm64` via `docker buildx` and pushes both manifests.
Requires `docker buildx` and registry credentials.

### Open a shell in the Docker image for debugging

```bash
mise run build:shell
```

Starts the locally-built `sma-ng:local` image with `/config` and `/logs` bind-mounted and drops you into `/bin/sh`.
You can run `python manual.py -cl` or inspect config files without starting the full daemon.

---

## Remote Deployment

### Configuration

Copy the sample and fill in your details:

```bash
cp setup/local.yml.sample setup/local.yml
```

`setup/local.yml` is gitignored. It is the single source of truth for everything
that distinguishes this deployment from the upstream defaults — deploy targets,
per-host overrides, credentials, encoder defaults, and quality profiles. Each
top-level section is consumed by `mise run config:roll` as follows:

| Section    | Effect on each host's `config/sma-ng.yml`                                                  |
| ---------- | ------------------------------------------------------------------------------------------ |
| `deploy`   | Project-wide defaults read by `scripts/local-config.py` for any host-context lookup        |
| `hosts`    | Per-host overrides for any `deploy:` key (`address` and `user` are required for SSH)       |
| `daemon`   | Stamped into `daemon:` (kebab-cased) and `config/daemon.env` (`SMA_DAEMON_*` env vars)     |
| `base`     | Deep-merged into `base:` — locks encoder defaults (gpu, codec, crf-profiles, audio…)       |
| `profiles` | Deep-merged into `profiles:` — overlay rules selected by routing                           |
| `services` | Stamped into `services.<type>.<instance>` and auto-converted into `daemon.routing` rules   |

Minimal example with the four overlay sections:

```yaml
deploy:
  hosts:
    - sma-master
    - sma-worker-1
  deploy_dir: /opt/sma
  ssh_key: ~/.ssh/id_ed25519_sma
  ffmpeg_dir: /usr/local/bin
  docker_profile: intel        # or intel-pg, nvenc, etc.

hosts:
  sma-master:
    address: 192.168.1.10
    user: deploy
    docker_profile: intel-pg   # bundled postgres on this host
  sma-worker-1:
    address: 192.168.1.11
    user: deploy

daemon:
  api_key: your_secret_key
  # db_url:   # required for non-pg multi-node: postgresql://user:pass@host/db

base:                          # locks encoder defaults across every host
  video:
    gpu: qsv
    codec: [hevc]
    preset: fast

profiles:                      # overlays applied per routing rule
  rq:
    video:
      crf-profiles: '0:22:1M:2M,2000:22:2M:4M,4000:22:3M:8M,8000:22:6M:8M'
  lq:
    video:
      crf-profiles: '0:22:3M:6M,8000:22:5M:10M'

services:                      # nested <type>.<instance>; matches sma-ng.yml schema
  sonarr:
    main:
      url: https://sonarr.example.com
      apikey: <key>
      path: /mnt/media/TV/1080P
      profile: rq              # routing.match=path, routing.profile=this
    kids:
      url: https://sonarr-kids.example.com
      apikey: <key>
      path: /mnt/media/TV/Kids
      profile: lq
```

Deep-merge semantics for `base:` and `profiles:`:

- Dicts recurse — adding `base.video.gpu: qsv` does **not** wipe other `base.video.*` fields.
- Lists and scalars overwrite — setting `base.video.codec: [hevc]` replaces whatever the
  sample's list contained.
- Anything you omit inherits from `setup/sma-ng.yml.sample`.

Note that `sma-ng.yml.sample` ships with its own `profiles.rq` / `profiles.lq` defaults
(e.g. `codec: [h265]`, `max-bitrate: 8000`). Profiles are a per-section *shallow* overlay
on top of `base`, so any field that the sample's profile sets will win over your `base:`
unless you also set it under `profiles:`.

### Deployment Workflow

```bash
# 1. First-time: SSH key, apt deps, install Docker, deploy dir
mise run deploy:setup

# 2. Optional: sync only the remote .mise task/control-plane code
mise run deploy:mise

# 3. Sync code and install deps
mise run deploy:sync

# 4. Push configs (create missing, merge new keys, stamp credentials)
mise run config:roll

# 5. Restart daemon on all hosts
mise run deploy:restart

# Optional: sync code to Docker hosts, pull the latest image, and recreate
# only the SMA service for each configured profile
mise run deploy:docker

# Optional: stop Docker services on one host
HOST=user@server1.example.com mise run deploy:dockerstop

# Optional: stop Docker services on multiple hosts
HOSTS="user@server1.example.com user@server2.example.com" mise run deploy:dockerstop

# Optional: restart or recreate bundled PostgreSQL on hosts using *-pg profiles
mise run pg:restart
mise run pg:recreate
```

### What `config:roll` Does

`config:roll` depends on `deploy:mise`, so the remote host gets the current local
`.mise/` helper and task code before any config mutation runs.

For managed deployments, `config:roll` also stamps the host's `SMA_NODE_NAME`
from `setup/local.yml` into `config/daemon.env`, and the daemon uses that
value as its cluster node ID.

For each remote host:

1. Detects the host's GPU type and writes it into a freshly-created `sma-ng.yml`
2. Creates `config/sma-ng.yml` and `config/daemon.env` from the bundled samples if missing
3. Backs up `config/` to `.backup/<timestamp>/` before any mutation
4. Merges new keys from `setup/sma-ng.yml.sample` into the existing `config/sma-ng.yml`
   via `yaml_merge.py` (non-destructive — existing values preserved)
5. Stamps `ffmpeg` / `ffprobe` paths from the host's resolved `ffmpeg_dir` into
   `base.converter.{ffmpeg,ffprobe}`
6. Deep-merges `base:` and `profiles:` overlays from `setup/local.yml` into the
   matching blocks of `sma-ng.yml` (see overlay semantics above)
7. Stamps each `services.<type>.<instance>` from `setup/local.yml` into the
   `services:` block, and rebuilds `daemon.routing` from every instance
   carrying both `path` and `profile` (longest match first)
8. Stamps `daemon.api-key` / `daemon.db-url` / `daemon.ffmpeg-dir` (kebab-case)
   into `daemon:` and writes the corresponding `SMA_DAEMON_*` env vars plus
   `SMA_NODE_NAME` into `config/daemon.env`
9. Deploys post-process scripts with the correct interpreter shebang and
   Plex/Jellyfin/Emby credentials

### Deploy Tasks Reference

| Task             | Description                                                                                                                               |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `deploy:check`   | Verify `setup/local.yml` exists and `DEPLOY_HOSTS` is set                                                                                |
| `deploy:setup`   | First-time host prep: SSH key, apt deps, deploy dir, Docker install                                                                      |
| `deploy:mise`    | Sync the local `.mise/` deploy control plane to each remote `DEPLOY_DIR`                                                                  |
| `deploy:sync`    | Sync code and install deps on all hosts                                                                                                   |
| `config:roll`    | Roll configs: create missing, merge new keys, stamp credentials                                                                           |
| `deploy:restart` | Gracefully shut down `sma-daemon` on all hosts, then restart its Docker container                                                                |
| `config:audit`   | Audit local configs                                                                                                                       |
| `deploy:docker`  | Rsync the local codebase to each Docker host, stamp `SMA_NODE_NAME` into `daemon.env`, pull the latest image for that host's `DOCKER_PROFILE`, and recreate only the SMA container |
| `pg:restart`     | Restart bundled PostgreSQL on hosts whose `DOCKER_PROFILE` ends in `-pg`                                                                  |
| `pg:recreate`    | Stop bundled PostgreSQL, remove its Docker volume, and recreate it on hosts whose `DOCKER_PROFILE` ends in `-pg`                          |

Additional Docker lifecycle helper: `deploy:dockerstop` (alias: `deploy:docker:stop`) stops services on selected hosts.

All remote-facing deploy/config tasks depend on `deploy:mise`, so the remote `.mise/`
control plane is refreshed before those wrappers run. The Docker-specific deploy tasks
require `DOCKER_PROFILE` to be set per host (or under `deploy:`) in `setup/local.yml`.
Use `HOST=<host>` to target one node, or `HOSTS="<host1> <host2>"` to target multiple nodes.
The PostgreSQL lifecycle tasks skip hosts that are not using one of the bundled `*-pg`
profiles.
Use `pg:recreate` only when you intentionally want a fresh bundled PostgreSQL data directory on the remote host; it removes the compose-managed `sma-pgdata` volume before bringing the service back.

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

| Variable                | Default   | Description                             |
| ----------------------- | --------- | --------------------------------------- |
| `SMA_DAEMON_HOST`       | `0.0.0.0` | Bind host                               |
| `SMA_DAEMON_PORT`       | `8585`    | Port                                    |
| `SMA_DAEMON_WORKERS`    | `2`       | Worker count                            |
| `SMA_DAEMON_API_KEY`    |           | API key                                 |
| `SMA_DAEMON_DB_URL`     |           | PostgreSQL connection URL (required)    |
| `SMA_DAEMON_FFMPEG_DIR` |           | Directory containing `ffmpeg`/`ffprobe` |
| `SMA_CONFIG`            |           | Override `sma-ng.yml` path         |

See also:

- [Docker Compose Quick Start](docker-compose-quickstart.md)
- [Environment Architecture](environment-architecture.md)
- [Multi-Instance Deployment](multi-instance-deployment.md)

---

## CI / Release

| Workflow      | Trigger                       | Description                                                                                    |
| ------------- | ----------------------------- | ---------------------------------------------------------------------------------------------- |
| `ci.yml`      | PR / push to main             | Runs test suite                                                                                |
| `docker.yml`  | PR / push to main or `v*` tag | PR: build-only + smoke test; main/tag: build + push to GHCR                                    |
| `release.yml` | Push to main                  | release-please manages release PR + version bump; on release: wheel/sdist + Docker semver tags |

Releases are driven by [release-please](https://github.com/googleapis/release-please). **Do not manually create `v*` tags** — this causes duplicate releases.

This repository pins release-please to the `always-bump-patch` versioning strategy, so releases default to point releases and patch numbers are not capped. Versions such as `1.2.12323` are valid.

Conventional commit types still control changelog grouping and breaking-change signaling, but by default they do not change the release from a point release:

- `fix:` → patch bump
- `feat:` → patch bump
- `feat!:` or `BREAKING CHANGE:` → patch bump unless a one-off override is used
