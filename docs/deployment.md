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
| `mise run setup:deps`          | Alias of `setup:deps:base`                                                       |
| `mise run setup:deps:base`     | Install base runtime dependencies from `setup/requirements.txt`                  |
| `mise run setup:deps:dev`      | Install dev dependencies (`pip install -e '.[dev]'`: lint, test, build tools)    |
| `mise run setup:deps:all`      | Install dev, qBittorrent and Deluge extras (`pip install -e '.[dev,qbittorrent,deluge]'`) |
| `mise run setup:clean`         | Remove build artifacts, caches, and compiled bytecode                            |
| `mise run setup:docker:target` | Create Docker bind-mount directories, seed config files, and install the `sma` host command |

### Development

| Task                     | Description                                              |
| ------------------------ | -------------------------------------------------------- |
| `mise run test:lint`     | Run ruff and the bandit security scan, report issues     |
| `mise run dev:lint`      | Run the ruff linter and auto-fix issues                  |
| `mise run dev:format`    | Format Python code with ruff                             |
| `mise run dev:precommit` | Run pre-commit checks against all files                  |
| `mise run test:openapi`  | Validate `docs/openapi.yaml`                             |
| `mise run test`          | Run the test suite (daemon tests require `TEST_DB_URL`)  |
| `mise run test:run`      | Same as `mise run test` (the task behind the alias)      |
| `mise run test:shell`    | Run `shellcheck -x` over tracked shell scripts and `.mise/tasks` entrypoints |
| `mise run test:cov`      | Run tests with coverage (HTML, JSON) and enforce the per-module coverage floor |
| `mise run build:dist`    | Build wheel and sdist artifacts into `dist/`             |
| `mise run test:daemon`   | Run focused daemon/API worker tests                      |
| `mise run test:deploy`   | Run focused deploy/config task tests                     |

### Media Tools

| Task                                            | Description                                          |
| ----------------------------------------------- | ---------------------------------------------------- |
| `mise run daemon:start`                         | Start the daemon; bind, port, workers come from `daemon:` (flags after `--`) |
| `mise run media:convert -- /path/to/file.mkv`   | Convert a media file with auto-tagging               |
| `mise run media:preview -- /path/to/file.mkv`   | Preview FFmpeg conversion options without converting |
| `mise run media:codecs`                         | List all supported video and audio codecs            |
| `mise run media:rename -- /path/to/file-or-dir` | Rename media files using naming templates            |
| `mise run media:repair -- /path/to/file-or-dir` | Repair damaged media by stream copy, no transcoding  |

### GPU and Configuration

| Task                       | Description                                                                        |
| -------------------------- | ---------------------------------------------------------------------------------- |
| `mise run config:gpu`      | Detect available GPU type (`nvenc`, `qsv`, `vaapi`, `videotoolbox`, or `software`) |
| `mise run config:generate` | Generate `config/sma-ng.yml` with GPU auto-detection                               |
| `mise run config:sample`   | Regenerate `setup/sma-ng.yml.sample` from the schema (`-- --check` fails on drift) |
| `mise run config:show`     | Render the effective resolved config for a profile (`-- --profile rq --section video --diff`). With `-- --input <file>`: print the full FFmpeg command for a real source without transcoding. |
| `mise run config:validate` | Validate the effective config — schema, unknown keys, encoder-flag leaks, routing refs |
| `mise run daemon:smoke`    | Run daemon smoke-test config validation and exit                                   |

### Docker Tasks

| Task                    | Description                                                                            |
| ----------------------- | -------------------------------------------------------------------------------------- |
| `mise run build:docker` | Build the Docker image locally for the native platform                                 |
| `mise run build:run`    | Run the locally-built image with SQLite by default                                     |
| `mise run build:shell`  | Open an interactive shell inside the locally-built image                               |
| `mise run build:smoke`  | Smoke-test the image: verify Python imports and FFmpeg binary                          |

### Deploy Tasks

| Task                       | Description                                                                                     |
| -------------------------- | ----------------------------------------------------------------------------------------------- |
| `mise run deploy:check`    | Verify `setup/local.yml` exists and `DEPLOY_HOSTS` is set                                       |
| `mise run deploy:setup`    | First-time host prep: SSH key, Docker install, install dir with config/logs/data, `daemon.env`  |
| `mise run deploy:remote`   | Run `deploy:config` then `deploy:docker` (build config locally, recreate Docker)                |
| `mise run deploy:config`   | Build `config/sma-ng.yml` locally per host and push to each `DEPLOY_HOSTS` entry                |
| `mise run deploy:docker`   | Push `docker-compose.yml`. Compare the registry's current image digest against the running container; if they match, only `docker compose restart $service` to pick up bind-mounted config changes (~3–5s). Otherwise pull, `compose down`, `up -d --force-recreate` (~30s+). Either path ends with a freshness verification against the registry. Override with `FORCE_RECREATE=1` to always take the full path. |

Use `HOST=<host>` for one node or `HOSTS="<host1> <host2>"` for multiple nodes.

### Docs, Wiki and Release

| Task                     | Description                                                                                   |
| ------------------------ | --------------------------------------------------------------------------------------------- |
| `mise run docs:check`    | Relative links resolve, every `mise run` reference is a real task, wiki renders, markdownlint |
| `mise run wiki:pull`     | Clone or fast-forward the GitHub wiki checkout (`SMA_WIKI_DIR`, default `/tmp/sma-wiki`)      |
| `mise run wiki:build`    | Regenerate every wiki page from `docs/`, `openapi.yaml` and task descriptions (`--prune`)     |
| `mise run wiki:diff`     | Show what `wiki:publish` would change                                                         |
| `mise run wiki:publish`  | Rebuild from committed docs, commit and push the wiki (`--dry-run`, `--allow-dirty`)          |
| `mise run wiki:check`    | Verify every doc maps to a wiki page and every relative link renders (no network)             |
| `mise run release:status`| Manifest version, latest GitHub release, open release-please PR, recent CI runs on `main`     |
| `mise run release:notes` | Conventional commits since the latest `sma-ng-v*` tag, grouped by type (`-- <ref>`)           |
| `mise run deps:outdated` | List outdated Python packages in the project venv                                             |

The GitHub wiki is generated, never edited by hand: each page carries a "Generated by `mise run wiki:build`" note
and is rebuilt from the canonical docs. Links between docs become wiki page links and other repository paths become
GitHub URLs. A new top-level `docs/*.md` must be added to `PAGES` in `scripts/wiki_sync.py`; `wiki:check` (run in
CI) fails until it is. Publish after docs land on `main`:

```bash
mise run wiki:publish -- --dry-run   # review the change set
mise run wiki:publish                # commit and push the wiki
```

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

This creates `/opt/sma/config`, `/opt/sma/logs`, `/opt/sma/cache`, `/opt/sma/data`, and `/transcode/sma`, then seeds
`/opt/sma/config/sma-ng.yml` and `/opt/sma/config/daemon.env` if they are missing. It also installs the
`sma` host command to `/usr/local/bin/sma` (override with `SMA_BIN_PATH`); use `sma convert`, `sma preview`,
`sma repair`, `sma rename`, `sma audit`, `sma codecs`, `sma logs`, and `sma shell` from the host (see
[docker-compose-quickstart.md](docker-compose-quickstart.md)). `/opt/sma/sma-ng-docker-aliases.sh` is still written
but is a deprecated shim over `sma` and will be removed in a later release. The `/downloads` compose mount is gone:
add `path-rewrites: [{from: /downloads, to: /mnt/unionfs/downloads}]` if download clients report `/downloads/...`.

Override the default host paths when your compose mounts differ:

```bash
INSTALL_DIR=/srv/sma TRANSCODE_DIR=/srv/transcode mise run setup:docker:target
```

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

| Profile       | GPU stack         | Database                                   | Use on                                        |
| ------------- | ----------------- | ------------------------------------------ | --------------------------------------------- |
| `software`    | none (CPU only)   | SQLite                                     | single-node CPU deployment with SQLite        |
| `software-pg` | none (CPU only)   | external PostgreSQL (`daemon.db-url`)      | single-node deployment, no GPU                |
| `intel`       | Intel QSV / VAAPI | SQLite                                     | single-node Intel GPU deployment with SQLite  |
| `intel-pg`    | Intel QSV / VAAPI | external PostgreSQL (`daemon.db-url`)      | the master in an Intel-GPU cluster            |
| `nvidia`      | NVIDIA NVENC      | SQLite                                     | single-node NVIDIA GPU deployment with SQLite |
| `nvidia-pg`   | NVIDIA NVENC      | external PostgreSQL (`daemon.db-url`)      | the master in an NVIDIA-GPU cluster           |

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
  routing:    # plain folders no service manages; merged with service-generated rules
    - match: /mnt/unionfs/downloads/metube
      profile: preserve

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
      routes:                  # one routing rule per entry, all -> sonarr.main
        - path: /mnt/unionfs/Media/TV/1080P
          profile: rq
        - path: /mnt/unionfs/Media/TV/4K
          profile: hq
    kids:
      url: https://sonarr-kids.example.com
      apikey: <key>
      path: /mnt/unionfs/Media/TV/Kids
      profile: lq              # singular form: routing.match=path, routing.profile=this
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

Production images are built in CI: push to `main`, and the GitHub Actions Docker
workflow builds and pushes `ghcr.io/<deploy.gh_user>/sma-ng:latest`. Once that run
finishes, deploy the freshly published image with:

```bash
mise run deploy:remote
```

`deploy:remote` runs `deploy:config` then `deploy:docker` one host at a time, so each
node regenerates its `sma-ng.yml`, pulls the latest image tag, and recreates only the
SMA container. It never builds an image locally — the registry copy is the source of
truth.

Useful overrides:

```bash
# Deploy one host
HOST=sma-master mise run deploy:remote

# Deploy selected hosts
HOSTS="sma-master sma-worker-1" mise run deploy:remote

# Use an explicit image tag
IMAGE=ghcr.io/newdave/sma-ng:main mise run deploy:remote
```

Images are only built and pushed by CI (see [CI / Release](#ci--release)); there is no
local push task. To deploy a specific published tag, pass `IMAGE=` to `deploy:remote`.

The workflow is `deploy:setup` once per host, then `deploy:remote` for every change:

```bash
# 1. Once per host: SSH key, Docker, install dir with config/logs/data
mise run deploy:setup

# 2. Every change: build sma-ng.yml locally, push it, recreate Docker
mise run deploy:remote

# Or do the two halves separately:
mise run deploy:config   # generate + push sma-ng.yml only
mise run deploy:docker   # push compose yml + pull + restart or recreate

# After deploy:config alone, apply the new config:
mise run cluster:reload    # hot-reloadable changes
mise run cluster:restart   # everything else
```

`deploy:config` pushes only `sma-ng.yml`; it does not copy post-process scripts. Bake them
into the image or mount them into the container.

If the GHCR image is private, `deploy:docker` pulls with the host's own registry
credentials. Authenticate each host once with a GitHub token that has `read:packages`:

```bash
# Run on the host (or over SSH), once:
echo "$GH_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
```

The published `ghcr.io/newdave/sma-ng` image is public, so no host login is needed for
the default deployment.

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
   carrying path+profile pairs — the singular `path`/`profile` keys and/or
   a `routes` list of such pairs, one rule per pair (longest match first).
   Rules listed under `daemon.routing` in `setup/local.yml` are merged in verbatim
   for prefixes no service instance generates — use this for plain folders that no
   Sonarr/Radarr manages, such as a downloader drop directory. A rule whose `match`
   a service already generates is dropped in favour of the service-derived one, and
   plain-folder rules get no Plex/Autoscan fan-out.
5. Stamps `daemon.api-key` / `daemon.db-url` / `daemon.ffmpeg-dir` /
   `daemon.node-id` (kebab-case) into `daemon:`.
6. Deep-merges arbitrary `daemon:` keys from `setup/local.yml` (e.g.
   `path-rewrites`, `strict-routing`, `scan-paths`) into the daemon block.

### Deploy Tasks Reference

| Task              | Description                                                                               |
| ----------------- | ----------------------------------------------------------------------------------------- |
| `deploy:check`    | Verify `setup/local.yml` exists and `DEPLOY_HOSTS` is set                                 |
| `deploy:setup`    | First-time host prep: SSH key, Docker install, install dir with config/logs/data          |
| `deploy:remote`   | Run `deploy:config` then `deploy:docker` (build config locally, recreate Docker)          |
| `deploy:config`   | Build `config/sma-ng.yml` locally per host and push to each `DEPLOY_HOSTS` entry          |
| `config:show`     | Render the effective resolved config for a profile                                        |
| `config:validate` | Validate effective config — schema, unknowns, encoder-flag leaks, routing refs            |
| `deploy:docker`   | Push `docker-compose.yml`, pull image, `docker compose down` + `up -d --force-recreate`   |
| `cluster:start`   | `docker compose start` for selected hosts (`HOST=` / `HOSTS=`)                            |
| `cluster:stop`    | `docker compose stop` for selected hosts (graceful drain, see `STOP_GRACE_PERIOD`)        |
| `cluster:restart` | `docker compose restart` for selected hosts (graceful drain, see `STOP_GRACE_PERIOD`)     |
| `cluster:status`  | `docker compose ps` for selected hosts                                                    |
| `cluster:reload`  | `POST /reload` on selected hosts so daemons re-read `sma-ng.yml` without a restart        |
| `cluster:drain`   | `POST /admin/nodes/<host>/drain`; workers finish active jobs then go idle                 |
| `cluster:pause`   | `POST /admin/nodes/<host>/pause`; workers stop picking up new jobs                        |
| `cluster:resume`  | `POST /admin/nodes/<host>/resume`; clear drain or pause                                   |
| `cluster:upgrade` | Drain each host, wait for `running_jobs=0`, then run `deploy:remote HOST=<host>`          |

See [Cluster Operations](cluster-operations.md) for runbooks combining these.

Hosts run the Docker image only: no code, Python, or `mise` is installed on them. Every
deploy and cluster task runs locally and only needs an SSH path to each host. The
Docker-specific tasks require `docker_profile` to be set per host (or under
`deploy:`) in `setup/local.yml`. Use `HOST=<host>` to target one node, or
`HOSTS="<host1> <host2>"` to target multiple nodes (this applies to every `cluster:*` task,
including `cluster:reload`).

`cluster:start`, `cluster:stop`, `cluster:restart` and `cluster:status` share
`.mise/shared/deploy/cluster_compose.sh`, which runs
`docker compose --profile <docker_profile> start|stop|restart|ps` on each host.

#### Graceful stop and restart

Every daemon service in `docker/docker-compose.yml` sets
`stop_grace_period: ${STOP_GRACE_PERIOD:-2m}`. `cluster:stop` and `cluster:restart` therefore
give the daemon up to two minutes to drain before Docker sends SIGKILL; jobs still running at
the deadline are requeued. To change the window, set `STOP_GRACE_PERIOD` (for example `10m`)
in a `.env` file next to `docker-compose.yml` on the host or in the shell that runs `docker compose` (it is a compose
substitution, so `config/daemon.env` does not apply). To avoid requeues entirely,
run `cluster:drain` first (or use `cluster:upgrade`, which does).

#### Reload or restart

After `deploy:config`, use `mise run cluster:reload` for hot-reloadable changes (API key,
routing rules, scan paths, codec lists, profile overlays). Use `mise run cluster:restart`
for anything else, such as worker count or the API bind address and port.

### Fallback Policy Rollout

`base.converter.fallback-policy` controls how aggressively the ladder in
`_attempt_ladder` retries a failed transcode. The four tiers, in order, are
`hw` → `hw_alt` → `sw_decode` → `full_sw`. See
[`docs/hardware-acceleration.md`](hardware-acceleration.md#fallback-policy-replaces-the-deprecated-boolean-software-fallback)
for the per-policy behaviour table.

Recommended phased rollout when introducing `hw_alt` to a cluster:

1. **Observation window — `hw_alt`** (one to two weeks): set
   `base.converter.fallback-policy: hw_alt` in `setup/local.yml` and run
   `mise run deploy:config && mise run cluster:reload`. The ladder will retry
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
mise run build:docker

# Run locally-built image with SQLite at ./data/sma-ng.db
mise run build:run

# Or point at PostgreSQL explicitly by setting daemon.db_url in config/sma-ng.yml
```

**Tags:** `latest` and `main` (main HEAD, rebuilt after CI passes on every push to `main`), `sha-<commit>`, and
per release `X.Y.Z`, `X.Y`, `X` (linux/amd64 + linux/arm64).

For hardware acceleration diagnostics in containers, the runtime image includes `vainfo` and VAAPI userspace drivers.
For Intel/QSV setups, use the Intel profile (`docker compose --profile intel up`) or the `intel-pg` profile (external PostgreSQL) so `/dev/dri` is mapped into the container. This is important on SR-IOV guests where the Intel VF may appear as `card1` while still using `renderD128`.
The `-pg` profiles run only the `sma-ng` container and use an external PostgreSQL server configured with `daemon.db-url` in `sma-ng.yml`; compose does not bundle or publish PostgreSQL.

Docker runtime settings live in `/config/sma-ng.yml`; the daemon no longer reads `SMA_*` environment variables.

See also:

- [Docker Compose Quick Start](docker-compose-quickstart.md)
- [Environment Architecture](environment-architecture.md)
- [Multi-Instance Deployment](multi-instance-deployment.md)

---

## CI / Release

| Workflow      | Trigger                                  | Description                                                                 |
| ------------- | ---------------------------------------- | --------------------------------------------------------------------------- |
| `ci.yml`      | PR / push to main                        | Tests + coverage gates, pre-commit lint, OpenAPI/docs/config checks, runtime `pip-audit` |
| `docker.yml`  | PR; CI passed on main; called by release | Build, smoke test and Trivy-scan the image; push only for main and releases |
| `release.yml` | Push to main; manual with `tag`          | release-please PR + version bump; on release: wheel/sdist + release image   |
| `codeql.yml`  | PR / push to main / weekly               | CodeQL static analysis (results in the Security tab)                        |

Docker images are built and pushed only by `docker.yml`; there is no local push task. A push to `main` builds the
image only after CI passes on that commit, and every image is smoke-tested before it is pushed, so `latest` never
carries code that failed CI. Hosts then pick the image up with `mise run deploy:remote`. Trivy findings are
report-only (Security tab) for now. Each platform builds and is smoke-tested on a native runner (arm64 on
`ubuntu-24.04-arm`, not QEMU emulation) and is pushed untagged by digest; a final job publishes one multi-arch
manifest under the tags.

Releases are driven by [release-please](https://github.com/googleapis/release-please). **Do not manually create `v*` tags** — this causes duplicate releases.

This repository pins release-please to the `always-bump-patch` versioning strategy, so releases default to point releases and patch numbers are not capped. Versions such as `1.2.12323` are valid.

Conventional commit types still control changelog grouping and breaking-change signaling, but by default they do not change the release from a point release:

- `fix:` → patch bump
- `feat:` → patch bump
- `feat!:` or `BREAKING CHANGE:` → patch bump unless a one-off override is used

### Release PR lifecycle

Every push to `main` containing a releasable commit (`fix:` / `feat:`) creates or updates a Release PR.
The workflow classifies the PR by the version in its title and acts accordingly:

| Version shape | Class | Action                                                                 |
| ------------- | ----- | ---------------------------------------------------------------------- |
| `x.y.Z` (Z>0) | patch | Auto-merged immediately (the default under `always-bump-patch`)        |
| `x.Y.0` (Y>0) | minor | Labelled `type: minor-release`; requires manual approval before merge  |
| `X.0.0`       | major | No automatic action; merge manually                                    |

Minor and major versions only occur via an explicit `release-as` override
(e.g. a commit footer `Release-As: 4.0.0`); day-to-day pushes always produce auto-merged point releases.
Auto-merge uses `gh pr merge --auto` when branch protection with required checks is enabled and falls
back to a direct squash merge otherwise.

Merging the Release PR creates the GitHub Release and `sma-ng-vX.Y.Z` tag. The Release workflow then attaches the
wheel and sdist and builds the release image (`X.Y.Z`, `X.Y`, `X`; linux/amd64 + linux/arm64) by calling
`docker.yml` directly: tags created with `GITHUB_TOKEN` start no workflow on their own. When the patch auto-merge
merges the Release PR directly, the same run creates the release immediately; with branch-protection auto-merge
it is created by the next push to `main`. `latest` keeps tracking `main`, so `deploy:remote` (which pulls
`latest`) deploys main HEAD; deploy a release with an explicit `IMAGE_TAG=X.Y.Z`. The version reported by the
daemon's `/health` endpoint is baked into the image from the latest tag at build time; the `commit` field in
`/health` always identifies the exact code running.

To rebuild a release's wheel, sdist and image (for example after a failed run), dispatch the Release workflow
with the tag: `gh workflow run release.yml -f tag=sma-ng-vX.Y.Z`. Events created with the default `GITHUB_TOKEN`
never start other workflows, so the Release PR gets no CI checks. Adding a `RELEASE_PLEASE_TOKEN` repository
secret (a GitHub App or fine-grained token with contents and pull-requests write) lets release-please's PRs and
tags trigger CI normally.
