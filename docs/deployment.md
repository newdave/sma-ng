# Deployment

SMA-NG uses [mise](https://mise.jdx.dev/) as a task runner for local development and remote deployments.
Install it once with the one-liner below — see the
[mise installation docs](https://mise.jdx.dev/getting-started.html) for package manager and Windows options.

```bash
curl https://mise.run | sh
```

## Task Reference

### Setup

| Task                           | Description                                                                      |
| ------------------------------ | -------------------------------------------------------------------------------- |
| `mise run setup:venv`          | Create the Python virtual environment                                            |
| `mise run setup:deps`          | Install base runtime dependencies from `setup/requirements.txt`                  |
| `mise run setup:deps:dev`      | Install dev dependencies (lint, test tools)                                      |
| `mise run setup:deps:all`      | Install all optional dependencies including qBittorrent and Deluge integrations  |
| `mise run setup:clean`         | Remove build artifacts, caches, and compiled bytecode                            |
| `mise run setup:docker:target` | Create Docker bind-mount directories, seed config files, and install CLI aliases |

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
| `mise run config:generate` | Generate `config/sma-ng.yml` with GPU auto-detection                               |
| `mise run config:audit`    | Audit local config files against the YAML sample                                   |
| `mise run config:show`     | Render the effective resolved config for a profile (`-- --profile rq --section video --diff`). With `-- --input <file>`: print the full FFmpeg command for a real source without transcoding. |
| `mise run config:validate` | Validate the effective config — schema, unknown keys, encoder-flag leaks, routing refs |
| `mise run daemon:smoke`    | Run daemon smoke-test config validation and exit                                   |

### Docker Tasks

| Task                    | Description                                                                            |
| ----------------------- | -------------------------------------------------------------------------------------- |
| `mise run build:docker` | Build the Docker image locally for the native platform                                 |
| `mise run build:push`   | Build and push a Docker image — requires `IMAGE=`, override platforms with `PLATFORM=` |
| `mise run docker:run`   | Run the locally-built image with SQLite by default                                     |
| `mise run build:shell`  | Open an interactive shell inside the locally-built image                               |
| `mise run build:smoke`  | Smoke-test the image: verify Python imports and FFmpeg binary                          |

### Deploy Tasks

| Task                       | Description                                                                                     |
| -------------------------- | ----------------------------------------------------------------------------------------------- |
| `mise run deploy:check`    | Verify `setup/local.yml` exists and `DEPLOY_HOSTS` is set                                       |
| `mise run deploy:setup`    | First-time host prep: SSH key, apt deps, deploy dir, Docker install                             |
| `mise run deploy:mise`     | Sync the local `.mise/` deploy control plane to all hosts                                       |
| `mise run deploy:redeploy` | Build/push the current code image, then run `deploy:remote` against production hosts            |
| `mise run deploy:remote`   | Run `deploy:config` then `deploy:docker` (build config locally, recreate Docker)                |
| `mise run deploy:config`   | Build `config/sma-ng.yml` locally per host and push to each `DEPLOY_HOSTS` entry                |
| `mise run deploy:sync`     | Sync code and install dependencies on all hosts                                                 |
| `mise run deploy:reload`   | Hot-reload `config/sma-ng.yml` on every host with `POST /reload`                                |
| `mise run deploy:restart`  | Gracefully shut down `sma-daemon` on all hosts, then restart its Docker container               |
| `mise run config:audit`    | Audit local configs                                                                             |
| `mise run deploy:docker`   | Push `docker-compose.yml`, pull image, `docker compose down` + `up -d --force-recreate`         |
| `mise run deploy:login`    | Log in to `ghcr.io` on all `DEPLOY_HOSTS` using a GitHub token                                  |

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
mise run media:preview -- /mnt/unionfs/Media/movies/test-file.mkv
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

### Prepare a Docker target host

```bash
mise run setup:docker:target
```

This creates `/opt/sma/config`, `/opt/sma/logs`, `/opt/sma/cache`, `/opt/sma/data`, and `/transcodes/sma`, then seeds
`/opt/sma/config/sma-ng.yml` and `/opt/sma/config/daemon.env` if they are missing. It also installs
`/opt/sma/sma-ng-docker-aliases.sh`; source that file in Bash to get aliases such as `sma-manual`,
`sma-convert`, `sma-preview`, `sma-codecs`, and `sma-logs`.

Override the default host paths when your compose mounts differ:

```bash
INSTALL_DIR=/srv/sma TRANSCODE_DIR=/srv/transcodes mise run setup:docker:target
```

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

> **Looking for *how* to do something?** This page is the task and config
> reference. For step-by-step runbooks (bootstrap a 3-node cluster, roll an
> upgrade with no queued-job loss, drain a node for maintenance, recover a
> stale node, read cluster logs), see
> [Cluster Operations](cluster-operations.md).

### Docker compose profile reference

`docker_profile` (in `setup/local.yml` `deploy:` or per-host) selects which
compose profile is brought up by `cluster:start` / `deploy:docker`. Only one
node should run a `*-pg` profile — that's the host that carries the bundled
PostgreSQL the rest of the cluster connects to. Non-`*-pg` profiles default
to a local SQLite database at `/opt/sma/data/sma-ng.db` for single-node use.

| Profile       | GPU stack         | Bundled Postgres?         | Use on                                        |
| ------------- | ----------------- | ------------------------- | --------------------------------------------- |
| `software`    | none (CPU only)   | no                        | single-node CPU deployment with SQLite        |
| `software-pg` | none (CPU only)   | yes (`sma-pgsql` service) | single-node deployment, no GPU                |
| `intel`       | Intel QSV / VAAPI | no                        | single-node Intel GPU deployment with SQLite  |
| `intel-pg`    | Intel QSV / VAAPI | yes                       | the master in an Intel-GPU cluster            |
| `nvidia`      | NVIDIA NVENC      | no                        | single-node NVIDIA GPU deployment with SQLite |
| `nvidia-pg`   | NVIDIA NVENC      | yes                       | the master in an NVIDIA-GPU cluster           |

### Configuration

Copy the sample and fill in your details:

```bash
cp setup/local.yml.sample setup/local.yml
```

`setup/local.yml` is gitignored. It is the single source of truth for everything
that distinguishes this deployment from the upstream defaults — deploy targets,
per-host overrides, credentials, encoder defaults, and quality profiles. Each
top-level section is consumed by `mise run deploy:config` as follows:

| Section    | Effect on each host's `config/sma-ng.yml`                                                |
| ---------- | ---------------------------------------------------------------------------------------- |
| `deploy`   | Project-wide defaults read by `scripts/local-config.py` for any host-context lookup      |
| `hosts`    | Per-host overrides for any `deploy:` key (`address` and `user` are required for SSH)     |
| `daemon`   | Stamped into `daemon:` (kebab-cased)                                                     |
| `base`     | Deep-merged into `base:` — locks encoder defaults (gpu, codec, crf-profiles, audio…)     |
| `profiles` | Deep-merged into `profiles:` — overlay rules selected by routing                         |
| `services` | Stamped into `services.<type>.<instance>` and auto-converted into `daemon.routing` rules |

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
      path: /mnt/unionfs/Media/TV/1080P
      profile: rq              # routing.match=path, routing.profile=this
    kids:
      url: https://sonarr-kids.example.com
      apikey: <key>
      path: /mnt/unionfs/Media/TV/Kids
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

For normal production code changes, use the single redeploy command:

```bash
mise run deploy:redeploy
```

`deploy:redeploy` builds and pushes the current checkout as a Docker image, then calls
`deploy:docker` one host at a time so each node pulls the exact image tag and recreates
only the SMA container.
By default it builds `linux/amd64` and deploys `ghcr.io/<deploy.gh_user>/sma-ng:latest`.

Useful overrides:

```bash
# Redeploy one host
HOST=sma-master mise run deploy:redeploy

# Redeploy selected hosts
HOSTS="sma-master sma-worker-1" mise run deploy:redeploy

# Use an explicit image tag
IMAGE=ghcr.io/newdave/sma-ng:main mise run deploy:redeploy

# Include config/sample/service changes in the same run
ROLL_CONFIG=true mise run deploy:redeploy

# Pull and recreate an already-pushed image without rebuilding
BUILD_IMAGE=false IMAGE=ghcr.io/newdave/sma-ng:main mise run deploy:redeploy
```

Lower-level tasks are still available when you need one specific phase:

```bash
# 1. First-time: SSH key, apt deps, install Docker, deploy dir
mise run deploy:setup

# 2. Optional: sync only the remote .mise task/control-plane code
mise run deploy:mise

# 3. Sync code and install deps
mise run deploy:sync

# 4. Push configs (create missing, merge new keys, stamp credentials)
mise run deploy:config

# 5. Restart daemon on all hosts
mise run deploy:restart

# Build the next sma-ng.yml locally and recreate Docker on every host
mise run deploy:remote

# Or do the two halves separately:
mise run deploy:config   # generate + push sma-ng.yml only
mise run deploy:docker   # push compose yml + docker compose down/up
```

### What `deploy:config` Does

For each host in `DEPLOY_HOSTS`, `deploy:config` runs entirely **locally** —
the existing stamp helpers (`stamp_daemon.py`, `stamp_ffmpeg.py`) execute
against a per-host staging dir under `.deploy-staging/<host>/config/`, then
the finished `sma-ng.yml` is rsync'd to `<host>:<deploy_dir>/config/sma-ng.yml`.

For each remote host the staging build:

1. Starts from `setup/sma-ng.yml.sample` (regenerated from the pydantic schema).
2. Stamps `ffmpeg` / `ffprobe` paths from the host's resolved `ffmpeg_dir`.
3. Deep-merges `base:` and `profiles:` overlays from `setup/local.yml`.
4. Stamps each `services.<type>.<instance>` from `setup/local.yml` into the
   `services:` block, and rebuilds `daemon.routing` from every instance
   carrying both `path` and `profile` (longest match first).
5. Stamps `daemon.api-key` / `daemon.db-url` / `daemon.ffmpeg-dir` /
   `daemon.node-id` (kebab-case) into `daemon:`.
6. Deep-merges arbitrary `daemon:` keys from `setup/local.yml` (e.g.
   `path-rewrites`, `strict-routing`, `scan-paths`) into the daemon block.

### Deploy Tasks Reference

| Task              | Description                                                                               |
| ----------------- | ----------------------------------------------------------------------------------------- |
| `deploy:check`    | Verify `setup/local.yml` exists and `DEPLOY_HOSTS` is set                                 |
| `deploy:setup`    | First-time host prep: SSH key, apt deps, deploy dir, Docker install                       |
| `deploy:mise`     | Sync the local `.mise/` deploy control plane to each remote `DEPLOY_DIR`                  |
| `deploy:redeploy` | Build/push the current code image, then run `deploy:remote` per host                      |
| `deploy:remote`   | Run `deploy:config` then `deploy:docker` (build config locally, recreate Docker)          |
| `deploy:config`   | Build `config/sma-ng.yml` locally per host and push to each `DEPLOY_HOSTS` entry          |
| `deploy:sync`     | Sync code and install deps on all hosts                                                   |
| `deploy:reload`   | Hot-reload `config/sma-ng.yml` on every host with `POST /reload`                          |
| `deploy:restart`  | Gracefully shut down `sma-daemon` on all hosts, then restart its Docker container         |
| `config:audit`    | Audit local configs                                                                       |
| `config:show`     | Render the effective resolved config for a profile                                        |
| `config:validate` | Validate effective config — schema, unknowns, encoder-flag leaks, routing refs            |
| `deploy:docker`   | Push `docker-compose.yml`, pull image, `docker compose down` + `up -d --force-recreate`   |
| `cluster:start`   | `docker compose start` for selected hosts (`HOST=` / `HOSTS=`)                            |
| `cluster:stop`    | `docker compose stop` for selected hosts                                                  |
| `cluster:restart` | `docker compose restart` for selected hosts                                               |
| `cluster:status`  | `docker compose ps` for selected hosts                                                    |
| `cluster:drain`   | `POST /admin/nodes/<host>/drain`; workers finish active jobs then go idle                 |
| `cluster:pause`   | `POST /admin/nodes/<host>/pause`; workers stop picking up new jobs                        |
| `cluster:resume`  | `POST /admin/nodes/<host>/resume`; clear drain or pause                                   |
| `cluster:upgrade` | Drain each host, wait for `running_jobs=0`, then run `deploy:remote HOST=<host>`          |

See [Cluster Operations](cluster-operations.md) for runbooks combining these.

`deploy:config` and `deploy:docker` run locally — they only need an SSH path to
each host. Tasks that invoke remote helpers or remote `mise` commands
(`deploy:sync`, `deploy:restart`, `deploy:login`, `cluster:*`) depend on
`deploy:mise` so the remote `.mise/` control plane is refreshed first. The
Docker-specific tasks require `docker_profile` to be set per host (or under
`deploy:`) in `setup/local.yml`. Use `HOST=<host>` to target one node, or
`HOSTS="<host1> <host2>"` to target multiple nodes.

### Fallback Policy Rollout

`base.converter.fallback-policy` controls how aggressively the ladder in
`_attempt_ladder` retries a failed transcode. The four tiers, in order, are
`hw` → `hw_alt` → `sw_decode` → `full_sw`. See
[`docs/hardware-acceleration.md`](hardware-acceleration.md#fallback-policy-replaces-the-deprecated-boolean-software-fallback)
for the per-policy behaviour table.

Recommended phased rollout when introducing `hw_alt` to a cluster:

1. **Observation window — `hw_alt`** (one to two weeks): set
   `base.converter.fallback-policy: hw_alt` in `setup/local.yml` and run
   `mise run deploy:config && mise run deploy:reload`. The ladder will retry
   tier-1 QSV failures on the same-vendor VAAPI encoder (10-bit preserved) and
   stop there. No software fallback runs, so any unrecovered job still surfaces
   as a failure — making the new tier easy to evaluate.
2. **Promote — `aggressive`**: once `hw_alt` recovers cleanly across the
   workload, promote to `fallback-policy: aggressive` to enable the full
   `hw → hw_alt → sw_decode → full_sw` ladder.

To confirm the new tier is exercising correctly, grep the daemon log for the
single-line `ffmpeg.attempts` record emitted at job completion. A successful
hw_alt recovery looks like:

```text
ffmpeg.attempts ... attempts=[hw failure_class=runtime_error, hw_alt failure_class=null] result=ok
```

A `result=ok` with no `hw_alt` entry means tier 1 succeeded — that is the
expected steady state for most jobs and should not generate log noise.

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

# Run locally-built image with SQLite at ./data/sma-ng.db
make docker-run

# Or point at PostgreSQL explicitly by setting daemon.db_url in config/sma-ng.yml
```

**Tags:** `latest`, `1`, `1.2`, `1.2.3` (semver), `main` (rolling build from main branch).

For hardware acceleration diagnostics in containers, the runtime image includes `vainfo` and VAAPI userspace drivers.
For Intel/QSV setups, use either the Intel profile (`docker compose --profile intel up`) or the bundled-PostgreSQL Intel profile (`docker compose --profile intel-pg up`) so `/dev/dri` is mapped into the container. This is important on SR-IOV guests where the Intel VF may appear as `card1` while still using `renderD128`.
The bundled PostgreSQL compose service publishes `5432` on the Docker host by default using `PGSQL_BIND_IP`/`PGSQL_PORT` from `docker/.env` (defaults: `0.0.0.0` and `5432`). That makes the database reachable via the Docker host IP unless you intentionally restrict it to `127.0.0.1` or a more specific interface.

Docker runtime settings live in `/config/sma-ng.yml`; the daemon no longer reads `SMA_*` environment variables.

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
