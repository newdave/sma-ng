# Deployment

SMA-NG uses [mise](https://mise.jdx.dev/) as a task runner for local development and for deploying to remote Docker
hosts. Install it once with the one-liner below; the [mise installation docs](https://mise.jdx.dev/getting-started.html)
cover package managers and Windows.

```bash
curl https://mise.run | sh
```

Run `mise tasks` to print the live task list from your checkout. The wiki also has a generated Mise-Tasks page built
from the same task descriptions.

## Task reference

Every task lives in `.mise/tasks/` and carries its description there; this table mirrors those descriptions. Pass
extra arguments to a task after `--`, for example `mise run test -- tests/test_daemon.py -k worker`.

### Setup

| Task | Description |
| --- | --- |
| `mise run setup:venv` | Create the virtual environment |
| `mise run setup:deps:base` | Install base dependencies |
| `mise run setup:deps:dev` | Install dev dependencies (lint, test) |
| `mise run setup:deps:all` | Install dev dependencies plus the optional postgres and openvino extras from `pyproject.toml` |
| `mise run deps:outdated` | List outdated Python packages in the project venv (dependencies are declared in `pyproject.toml`) |
| `mise run setup:clean` | Remove build artifacts and caches |

### Development and testing

| Task | Description |
| --- | --- |
| `mise run test` | Run tests; extra args go to pytest. Smoke tests skip without the fixture or FFmpeg; daemon tests skip without `TEST_DB_URL` |
| `mise run test:cov` | Run tests with coverage (HTML and terminal summary) and enforce the per-module floor |
| `mise run test:lint` | Run the ruff linter and the bandit security scan, and report issues |
| `mise run test:shell` | Run `shellcheck -x` over tracked shell scripts and `.mise/tasks` shell entrypoints |
| `mise run test:openapi` | Validate `docs/openapi.yaml` |
| `mise run dev:lint` | Run the ruff linter and auto-fix issues |
| `mise run dev:format` | Format Python code with ruff |
| `mise run dev:precommit` | Run pre-commit checks against all files |
| `mise run dev:agents` | Regenerate `.agents/skills` and `.codex/agents/*.toml` from `.claude/skills` and `.claude/agents` (`-- --check` reports drift) |
| `mise run dev:agent` | Start a Claude Code session through omnigent with smart routing (model picked per session, subagents on cheaper models) |
| `mise run dev:team` | Start the omnigent SMA-NG team (manager, architect, two Claude engineers); changes land as PRs |
| `mise run build:dist` | Build wheel and sdist distribution artifacts |

### Media tools and daemon

| Task | Description |
| --- | --- |
| `mise run daemon:start` | Start the daemon; bind address, port and workers come from `daemon:` in `sma-ng.yml` (pass flags after `--` to override, for example `-- --port 9000`) |
| `mise run daemon:smoke` | Run the daemon smoke-test config validation and exit |
| `mise run media:convert -- /path/to/file.mkv` | Convert a file |
| `mise run media:preview -- /path/to/file.mkv` | Preview conversion options without converting |
| `mise run media:codecs` | List supported codecs |
| `mise run media:rename -- /path/to/file-or-dir` | Rename media files using naming templates |
| `mise run media:repair -- [-n] /path/to/file-or-dir` | Repair damaged media by stream copy only: strip data streams, unknown streams and artwork, and fix stretched aspect |

### Configuration

| Task | Description |
| --- | --- |
| `mise run config:gpu` | Detect the GPU type for hardware acceleration |
| `mise run config:generate` | Create `config/sma-ng.yml` from the sample with `base.video.gpu` auto-detected (override with `GPU=qsv\|vaapi\|nvenc\|videotoolbox\|software`) |
| `mise run config:sample` | Regenerate `setup/sma-ng.yml.sample` from the pydantic schema (`-- --check` fails on drift) |
| `mise run config:show` | Render the effective resolved config for a profile (`--profile`, `--diff`, `--section`). With `--input FILE`, print the full FFmpeg command that would run, without transcoding |
| `mise run config:validate` | Validate the effective config: schema, unknown keys, encoder-flag leaks, routing references and service completeness. Exits 0 when clean, 1 for warnings (`--strict`), 2 for errors |

### Local Docker image

| Task | Description |
| --- | --- |
| `mise run build:docker` | Build the Docker image locally for the native platform (`PLATFORM=linux/amd64` to override; `TAG=sma-ng:local` to override the tag) |
| `mise run build:run` | Run the locally built image (defaults to SQLite; `TAG=sma-ng:local` to override) |
| `mise run build:shell` | Open a shell in the locally built image |
| `mise run build:smoke` | Smoke-test the locally built image: verify Python imports and FFmpeg |

### Deploy and cluster

| Task | Description |
| --- | --- |
| `mise run deploy:check` | Verify that `setup/local.yml` exists and `DEPLOY_HOSTS` is set |
| `mise run deploy:setup` | Prepare remote Docker hosts: SSH key, `~/.ssh/config` and `ssh-copy-id`, Docker install, and the install directory seeded with `config/`, `logs/` and `data/` |
| `mise run deploy:config` | Build `sma-ng.yml` locally per host (schema-driven plus `setup/local.yml` overrides) and push it to each host's config directory |
| `mise run deploy:docker` | Push `docker/docker-compose.yml` to each host, pull the latest image, then `docker compose down` and `up -d --force-recreate` |
| `mise run deploy:remote` | Build `sma-ng.yml` locally and redeploy Docker on every selected host: `deploy:config` then `deploy:docker` |
| `mise run cluster -- <verb>` | Cluster operations: `start`, `stop`, `restart`, `status`, `reload`, `drain`, `pause`, `resume` or `upgrade` (`HOST` or `HOSTS` to scope) |

### Docs, wiki and release

| Task | Description |
| --- | --- |
| `mise run docs:check` | Check that relative links resolve, every `mise run` reference is a real task, wiki pages render, and markdownlint passes |
| `mise run wiki:pull` | Clone or reset the GitHub wiki checkout to the remote (`SMA_WIKI_DIR`, default `/tmp/sma-wiki`); local changes there are discarded |
| `mise run wiki:build` | Regenerate the wiki pages from `docs/`, `openapi.yaml` and mise task descriptions into the wiki checkout (`--prune` drops unmanaged pages) |
| `mise run wiki:diff` | Show what `wiki:publish` would change in the wiki checkout (run `wiki:build` first) |
| `mise run wiki:publish` | Regenerate the wiki from committed docs and push it to GitHub (`-- --dry-run`, `--allow-dirty`, `--prune`) |
| `mise run wiki:check` | Verify that every top-level doc maps to a wiki page and every relative link renders for the wiki (no network) |
| `mise run release:status` | Show the manifest version, the latest GitHub release, the open release-please PR and recent CI runs on `main` |
| `mise run release:notes` | Preview the next release: conventional commits since the latest `sma-ng-v*` tag, grouped by type (`-- <ref>`, default `origin/main`) |

The GitHub wiki is generated and never edited by hand. Each page carries a "Generated by `mise run wiki:build`" note
and is rebuilt from the canonical docs: links between docs become wiki page links, and other repository paths become
GitHub URLs. A new top-level `docs/*.md` must be added to `PAGES` in `scripts/wiki_sync.py` and to `DOC_PAGES` in
`resources/daemon/docs_ui.py`; `wiki:check` fails until the first is done. Publish after docs land on `main`:

```bash
mise run wiki:publish -- --dry-run   # review the change set
mise run wiki:publish                # commit and push the wiki
```

## Examples

### Generate a config and force the GPU type

```bash
GPU=nvenc mise run config:generate
```

Use this when auto-detection picks the wrong encoder, for example on a machine with both Intel and NVIDIA GPUs.

### Preview conversion options

```bash
mise run media:preview -- /mnt/unionfs/Media/movies/test-file.mkv
```

This prints the full FFmpeg command and stream map without touching the file. Use it to check encoder selection,
stream copying and audio downmix decisions before a real conversion.

### Smoke-test the Docker image

```bash
mise run build:docker && mise run build:smoke
```

This builds `sma-ng:local` and verifies Python imports and FFmpeg inside it. No container is left running.

### Run tests with a coverage report

```bash
mise run test:cov && open htmlcov/index.html
```

On Linux, use `xdg-open` instead of `open`.

### Debug inside the image

```bash
mise run build:shell
```

This starts `sma-ng:local` with `/config` and `/logs` bind-mounted and opens a shell. From there you can run
`python manual.py -cl` or inspect config files without starting the daemon.

---

## Remote deployment

This page is the task and configuration reference. For step-by-step runbooks (bootstrap a cluster, roll an upgrade,
drain a node, recover a stale node, read cluster logs), see [Cluster Operations](cluster-operations.md).

Hosts run the Docker image only. No source checkout, Python or mise is installed on them: every deploy and cluster
task runs on your workstation and needs only an SSH path to each host.

### Docker compose profile reference

The `docker_profile` key (under `deploy:` or per host in `setup/local.yml`) selects the compose profile that
`deploy:docker` and `cluster start` use. The profiles differ in GPU access and in the database they expect:

| Profile | GPU stack | Database |
| --- | --- | --- |
| `software` | none (CPU only) | SQLite at `/opt/sma/data/sma-ng.db` |
| `software-pg` | none (CPU only) | external PostgreSQL (`daemon.db-url`) |
| `intel` | Intel QSV / VAAPI | SQLite |
| `intel-pg` | Intel QSV / VAAPI | external PostgreSQL (`daemon.db-url`) |
| `nvidia` | NVIDIA NVENC | SQLite |
| `nvidia-pg` | NVIDIA NVENC | external PostgreSQL (`daemon.db-url`) |

Compose never bundles PostgreSQL. The `-pg` profiles run the same container as the others and expect you to point
`daemon.db-url` at an external server. A cluster needs one shared PostgreSQL server; SQLite suits a single node. The
service name is `sma-<profile>` (for example `sma-intel-pg`), and the container is always named `sma-ng`.

### Configuration

Copy the sample and fill in your details:

```bash
cp setup/local.yml.sample setup/local.yml
```

`setup/local.yml` is gitignored. It is the single source of truth for everything that distinguishes your deployment
from the upstream defaults: deploy targets, per-host overrides, credentials, encoder defaults and quality profiles.
`mise run deploy:config` consumes each top-level section as follows:

| Section | Effect on each host's `config/sma-ng.yml` |
| --- | --- |
| `deploy` | Project-wide defaults (hosts, SSH, install directory, image, compose profile, workers) |
| `hosts` | Per-host overrides for any `deploy:` key; `address` and `user` are required for SSH |
| `daemon` | Merged into `daemon:`; `api_key`, `db_url` and `ffmpeg_dir` are stamped, other keys are merged as written |
| `base` | Deep-merged into `base:`; locks encoder defaults such as GPU, codec, CRF profiles and audio |
| `profiles` | Replace the profiles of the same name in `profiles:` (see below) |
| `services` | Stamped into `services.<type>.<instance>` and turned into `daemon.routing` rules |

A minimal example:

```yaml
deploy:
  hosts:
    - sma-master
    - sma-worker-1
  deploy_dir: /opt/sma
  ssh_key: ~/.ssh/id_ed25519_sma
  ffmpeg_dir: /usr/local/bin
  docker_profile: intel

hosts:
  sma-master:
    address: 192.168.1.10
    user: deploy
  sma-worker-1:
    address: 192.168.1.11
    user: deploy
    workers: 2

daemon:
  api_key: your_secret_key
  db_url: postgresql://sma:password@db-host:5432/sma
  routing:                     # plain folders that no Sonarr/Radarr instance manages
    - match: /mnt/unionfs/downloads/metube
      profile: preserve

base:
  video:
    gpu: qsv
    codec: [hevc]
    preset: fast

profiles:
  rq:
    video:
      crf-profiles: '0:22:1M:2M,2000:22:2M:4M,4000:22:3M:8M,8000:22:6M:8M'

services:
  sonarr:
    main:
      url: https://sonarr.example.com
      apikey: <key>
      routes:                  # one routing rule per entry, all pointing at sonarr.main
        - path: /mnt/unionfs/Media/TV/1080P
          profile: rq
        - path: /mnt/unionfs/Media/TV/4K
          profile: hq
    kids:
      url: https://sonarr-kids.example.com
      apikey: <key>
      path: /mnt/unionfs/Media/TV/Kids   # single-path form
      profile: lq
```

Merge rules:

- `base:` is deep-merged. Dictionaries recurse, so setting `base.video.gpu` keeps the other `base.video.*` fields.
  Lists and scalars overwrite, so `base.video.codec: [hevc]` replaces the sample's list. Anything you omit keeps the
  value from `setup/sma-ng.yml.sample`.
- `profiles:` are authoritative. A profile defined in `setup/local.yml` replaces the sample's profile of the same
  name as a whole, and sample profiles that `setup/local.yml` does not define are removed. Define every field you
  want, because a field you omit falls back to `base`, not to the sample's profile.
- `services:` are authoritative per type. An instance that exists in the deployed file but not in `setup/local.yml`
  is removed.

### How `deploy:config` builds a host's config

For each selected host, `deploy:config` builds the file on your workstation in `.deploy-staging/<host>/config/` and
then copies it with rsync to `<deploy_dir>/config/sma-ng.yml`. It never edits the file on the host, so each run
replaces the host's `sma-ng.yml`. The steps are:

1. Start from `setup/sma-ng.yml.sample`.
2. Stamp the `ffmpeg` and `ffprobe` paths from the host's `ffmpeg_dir`.
3. Apply the `base:` and `profiles:` sections from `setup/local.yml`.
4. Merge the `daemon:` section from `setup/local.yml`, except `routing`.
5. Stamp `daemon.api-key`, `daemon.db-url` (when known), `daemon.ffmpeg-dir`, `daemon.node-id` (the host key from
   `setup/local.yml`) and `daemon.workers` (from `workers`, per host or under `deploy:`).
6. Stamp each `services.<type>.<instance>` and rebuild `daemon.routing`.

The database URL comes from `daemon.db_url` in `setup/local.yml`. On hosts whose profile does not end in `-pg`, it
falls back to a URL built from `deploy.db_host`, `db_port`, `db_user`, `db_password` and `db_name`. On `-pg` hosts the
`deploy.db_*` keys are not used, so set `daemon.db_url` explicitly. With no URL at all, the node uses SQLite.

Routing is generated from `services:`. Every service instance that has a `path` and `profile`, or a `routes` list of
such pairs, yields one routing rule, and the rules are sorted by longest match first. A Plex, Jellyfin, Emby or
Autoscan instance without a path is added to every rule's service list. Rules under `daemon.routing` in
`setup/local.yml` are merged in only for prefixes that no service instance generates, so use them for plain folders
such as a downloader drop directory; they do not trigger Plex or Autoscan refreshes. If a rule and a service use the
same `match`, the service rule wins.

`deploy:config` copies only `sma-ng.yml`. Post-process scripts are not copied; bake them into the image or mount them
into the container.

### Deployment workflow

Production images are built in CI. When you push to `main`, CI runs the tests and lint and, if the change affects the
image, builds and pushes `ghcr.io/newdave/sma-ng:latest` (also `main` and `sha-<short>`). See [CI and release](#ci-and-release).
When that run finishes, deploy the new image:

```bash
mise run deploy:remote
```

`deploy:remote` runs `deploy:config` and then `deploy:docker`, one host at a time. It never builds an image locally;
the registry copy is the source of truth. The usual sequence is:

```bash
# Once per host: SSH key, Docker, install directory with config/logs/data
mise run deploy:setup

# Every change: build sma-ng.yml locally, push it, update the container
mise run deploy:remote

# Or run the two halves separately
mise run deploy:config   # build and push sma-ng.yml only
mise run deploy:docker   # push the compose file, then pull and restart or recreate
```

After `deploy:config` alone, apply the new config with `mise run cluster -- reload` for hot-reloadable settings, or
`mise run cluster -- restart` for everything else (see [Reload or restart](#reload-or-restart)).

Select hosts with `HOST` for one node or `HOSTS` for several. Both work for `deploy:config`, `deploy:docker`,
`deploy:remote` and every `cluster` verb; `deploy:setup` accepts only `HOST`.

```bash
HOST=sma-master mise run deploy:remote
HOSTS="sma-master sma-worker-1" mise run deploy:remote
```

#### What `deploy:docker` does

1. Creates the install directories and installs Docker when they are missing.
2. Copies `docker/docker-compose.yml` to `<deploy_dir>/docker/` on the host.
3. Compares the registry's digest for the image tag with the image of the running `sma-ng` container.
   - **Same digest:** only runs `docker compose restart` on the service, so the container re-reads the bind-mounted
     `sma-ng.yml`. This takes a few seconds. Set `FORCE_RECREATE=1` to skip this shortcut.
   - **Different digest, or no digest available:** runs `docker compose pull`, `down`, removes any leftover `sma-ng`
     container, then `up -d --force-recreate`.
4. Waits up to `HEALTH_TIMEOUT` seconds (default `120`) for `/health` to answer, and warns if it does not.
5. Checks that the container runs the image the registry currently advertises. A mismatch is reported but does not
   fail the deploy.

To deploy a specific tag instead of `latest`, set `image_tag` under `deploy:` (or per host) in `setup/local.yml`,
for example `image_tag: 3.2.52`. The environment variable `IMAGE_TAG` also reaches Compose, but the freshness check
reads only `image` and `image_tag` from `setup/local.yml`, so use the file for a pinned release.

The published `ghcr.io/newdave/sma-ng` image is public, so hosts need no registry login. If you deploy a private
fork, log each host in once with a token that has `read:packages`:

```bash
echo "$GH_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
```

### Graceful stop and restart

Every daemon service in `docker/docker-compose.yml` sets `stop_grace_period: ${STOP_GRACE_PERIOD:-2m}`. Docker
therefore waits up to two minutes after SIGTERM for the daemon to drain before it sends SIGKILL, and jobs still
running at that point are requeued. This applies to `cluster stop`, `cluster restart` and `docker compose stop`.

- To change the window, set `STOP_GRACE_PERIOD` (for example `10m`) in a `.env` file next to `docker-compose.yml` on
  the host, or in the shell that runs `docker compose`. It is a Compose substitution, so `config/daemon.env` does
  not apply.
- The effective drain time is the smaller of `daemon.shutdown-grace-seconds` (default `100`) and
  `STOP_GRACE_PERIOD`. Raise both together, and keep `shutdown-grace-seconds` below `STOP_GRACE_PERIOD`.
- To avoid requeues entirely, run `cluster drain` first, or use `cluster upgrade`, which drains for you.

### Reload or restart

Use `mise run cluster -- reload` after `deploy:config` for settings that the daemon can re-read: the API key, routing
rules, scan paths, audit settings, codec lists and profile overlays. Use `mise run cluster -- restart` for anything
else, such as the worker count or the bind address and port.

### Cluster verbs

`mise run cluster -- <verb>` is a single task. A missing or unknown verb prints usage and exits with status 2. Set
`HOST` or `HOSTS` in the environment (`HOST=sma-master mise run cluster -- status`), not after `--`. Without either,
the verb runs on every host in `deploy.hosts`.

| Verb | Action |
| --- | --- |
| `start`, `stop`, `restart` | `docker compose --profile <docker_profile> start`, `stop` or `restart` on each host |
| `status` | `docker compose ps` on each host |
| `reload` | `POST /reload` on each host's daemon, so it re-reads `sma-ng.yml` without a restart |
| `drain` | `POST /admin/nodes/<host>/drain`: the node finishes active jobs and accepts no new ones |
| `pause` | `POST /admin/nodes/<host>/pause`: the node stops picking up new jobs; active jobs continue |
| `resume` | `POST /admin/nodes/<host>/resume`: clear a drain or pause |
| `upgrade` | For each host: drain, wait for no running jobs, then run `deploy:remote` for that host |

The runbooks that combine these verbs are in [Cluster Operations](cluster-operations.md).

---

## Docker image

CI publishes the image to GitHub Container Registry. To run it by hand:

```bash
docker run --rm -p 8585:8585 \
  -v /your/config:/config \
  -v /your/logs:/logs \
  ghcr.io/newdave/sma-ng:latest
```

To build and run it locally, use `mise run build:docker` and `mise run build:run` (SQLite at `./data/sma-ng.db`).
To use PostgreSQL, set `daemon.db_url` in `config/sma-ng.yml`.

Tags:

- `main` tracks the newest `main` commit whose CI passed, as a linux/amd64 image. `sha-<short>` pins one commit.
- Each release adds `X.Y.Z`, `X.Y` and `X` for linux/amd64 and linux/arm64.
- `latest` tracks the newer of the two: the `main` image or the newest release image. Right after a release it is
  the release image, so `sma upgrade` and `deploy:remote` report the release version.

The runtime image includes `vainfo` and the VAAPI userspace drivers for hardware diagnostics. For Intel QSV, use the
`intel` or `intel-pg` profile, which maps `/dev/dri` into the container; this matters on SR-IOV guests where the
Intel virtual function can appear as `card1` while using `renderD128`. Runtime settings live in `/config/sma-ng.yml`;
the daemon does not read `SMA_*` environment variables, except that the image's start script translates `SMA_WORKERS`
into the `--workers` flag.

For hosts that need only the `sma` command or a single daemon, see [Docker Quick Start](docker-quickstart.md). For
installing `sma` on a Docker host without a checkout, run the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/newdave/sma-ng/main/install-smang.sh | sh -s -- --daemon --media /mnt/media
```

The installer creates `/opt/sma/{config,logs,data}` with a sample `sma-ng.yml`, installs `sma`, and with `--daemon`
runs `sma daemon up --media ...` (see [Running the daemon](docker-quickstart.md#running-the-daemon)). Clusters keep
using `deploy:setup` and `deploy:remote`, which manage the same `/opt/sma` layout.

---

## CI and release

| Workflow | Trigger | Description |
| --- | --- | --- |
| `ci.yml` | Pull request and push to `main` | Tests with coverage gates, pre-commit lint, OpenAPI, docs, wiki and config-sample checks, and a runtime `pip-audit`; builds the image when image files change |
| `docker.yml` | Called by CI and the release workflow; manual dispatch | Builds and smoke-tests the image; Trivy scan and push only for `main` and releases |
| `release.yml` | Push to `main`; manual dispatch with a `tag` | release-please PR and version bump; on release, the wheel, sdist and release image |
| `codeql.yml` | Pull request (Python changes) and weekly | CodeQL static analysis, with results in the Security tab |

Every job installs Python through the shared `.github/actions/setup-python` action, which reads `.python-version`, so
workflows never name a Python version. The pre-commit hooks pin Node (`default_language_version.node` in
`.pre-commit-config.yaml`) so that Renovate and runner updates cannot change it unnoticed.

A new push to a pull request cancels that pull request's running checks. Runs on `main` and release runs queue
instead, so every `main` commit gets a result and a release is never interrupted. Pull requests from the
`release-please--` branch skip the test and lint jobs.

Test and lint caches cover pip downloads, the whole dev `venv`, the pre-commit hook environments (keyed on the exact
Python build) and the FFmpeg `.deb` files per runner image.

### When an image is built

CI builds an image only when `scripts/ci-image-changed.sh` finds an image-affecting file in the change: Python source,
the package directories, `setup/`, `docker/`, `scripts/`, `sma-webhook.sh`, `pyproject.toml`, `.dockerignore` or
`docker.yml`. Tests, docs, agent tooling and other workflows never build an image. The operator docs served at `/docs`
therefore reach a host with the next image build.

- **Pull request:** CI compares the merge commit with the base branch, then builds and smoke-tests the image alongside
  the other checks. Nothing is pushed.
- **Push to `main`:** CI compares with the last `main` commit whose CI passed (or builds when that is unknown). The
  image job runs only after the test and lint jobs pass, then smoke-tests and pushes `latest`, `main` and
  `sha-<short>`. Because of this order, `latest` never carries code that failed CI. The push skips `latest` when it
  already points at a newer commit, such as the release image of the release commit that follows.
- **Manual:** dispatch `docker.yml` to rebuild by hand.

Hosts then pick the image up with `mise run deploy:remote`. Trivy findings are report-only and appear in the Security
tab. Each platform builds and is smoke-tested on a native runner (arm64 on `ubuntu-24.04-arm`, not emulation) and is
pushed by digest; a final job publishes one multi-architecture manifest under the tags.

### Build caching

The image build uses two kinds of cache. Docker layer caching (the GitHub Actions cache plus the `buildcache`
registry tag) helps only while nothing above a layer changes. Below that, the Dockerfile keeps its downloads in
BuildKit cache mounts, one set per architecture, so a rebuilt layer does not start from nothing:

- `apt-cache-<arch>` and `apt-lists-{ffmpeg,python,runtime}-<arch>` hold apt `.deb` files and package lists. The
  builder stages disable the base image's apt cleaning. The runtime stage sets it aside only during its install step,
  so the shipped image keeps Ubuntu's cleaning.
- `pip-<arch>` holds pip downloads for the Python venv build.
- `ffmpeg-src-<arch>` holds the FFmpeg source tarball (verified with `xz -t` and downloaded again if corrupt) and the
  nv-codec-headers checkout, both keyed by version.
- `ffmpeg-ccache-<arch>` holds ccache objects. FFmpeg is configured with `--cc="ccache gcc" --cxx="ccache g++"`, and
  the build log ends with `ccache -s` statistics. The FFmpeg compile is the slowest step, and a base-image or apt
  change would otherwise rebuild all of it.

CI runners start with empty mounts, so `docker.yml` persists all of them across runs with one `actions/cache` entry
and the `buildkit-cache-dance` action. The key is `buildkit-mounts-<arch>-<hash>`, where the hash covers
`docker/Dockerfile`, `setup/requirements*.txt` and `pyproject.toml`; a `buildkit-mounts-<arch>-` prefix lets an older
cache seed a changed input. Only `main` and release builds save the cache. Pull request builds restore it read-only
to protect the 10 GB cache budget. A plain `docker build` without these caches still works; it just downloads and
compiles everything.

### Releases

[release-please](https://github.com/googleapis/release-please) drives releases. Do not create `v*` tags by hand,
because that produces duplicate releases. The repository pins release-please to the `always-bump-patch` strategy, so
every release is a point release and patch numbers are not capped (`1.2.12323` is valid). Conventional commit types
still control changelog grouping and breaking-change notes, but `fix:`, `feat:` and `feat!:` all produce a patch
bump unless a one-off `Release-As:` override is used.

Every push to `main` that contains a releasable commit (`fix:` or `feat:`) creates or updates a release PR. The
release workflow classifies the PR by the version in its title:

| Version shape | Class | Action |
| --- | --- | --- |
| `x.y.Z` (Z above 0) | patch | Auto-merged (the default under `always-bump-patch`) |
| `x.Y.0` (Y above 0) | minor | Labelled `type: minor-release`; needs manual approval before merge |
| `X.0.0` | major | No automatic action; merge manually |

Minor and major versions occur only through an explicit `release-as` override, such as a `Release-As: 4.0.0` commit
footer. Auto-merge uses `gh pr merge --auto` when branch protection with required checks is on, and falls back to a
direct squash merge otherwise.

Merging the release PR creates the GitHub release and the `sma-ng-vX.Y.Z` tag. The release workflow then attaches the
wheel and sdist and builds the release image (`X.Y.Z`, `X.Y`, `X` and `latest`; amd64 and arm64) by calling
`docker.yml` directly, because tags created with `GITHUB_TOKEN` start no workflow. When the patch auto-merge merges
the PR directly, the same run creates the release at once. With branch-protection auto-merge, the next push to `main`
creates it. A bot-merged release commit starts no CI run, so the release image is the only image of that commit;
moving `latest` to it is what lets hosts upgrade to the release. A rebuild of an older release leaves `latest` alone. The daemon's `/health` endpoint reports a version
baked into the image from the latest tag, and its `commit` field identifies the exact code that is running.

To rebuild a release's wheel, sdist and image, for example after a failed run, dispatch the workflow with the tag:

```bash
gh workflow run release.yml -f tag=sma-ng-vX.Y.Z
```

release-please opens its PRs with the `RELEASE_PLEASE_TOKEN` repository secret when it is set (a GitHub App or
fine-grained token with contents and pull-requests write), and otherwise falls back to `github.token`. Events created
with `github.token` start no other workflows, so the release PR then gets no CI checks. Set the secret so release
PRs and tags trigger CI normally. Even then, a patch release PR is merged before its checks begin, so CI, Docker and
CodeQL show a failed run with no jobs on the `release-please--` branch. That is expected and needs no action.

---

## Shared storage and paths

SMA-NG matches paths literally: routing rules, `daemon.path-rewrites` and Sonarr, Radarr and download-client callbacks
all compare path strings. In a multi-host or virtualized setup, the main job is to make every node see the same media
tree under the same path. The layout below is the reference environment this project is run on; adapt the idea, not the
hardware.

```text
CephFS (/mnt/local)      NFS media export (/mnt/nfs/Media/...)
        \                      /
         mergerfs on Proxmox nodes: /mnt/local:NC:/mnt/nfs:RW
                     |
        +------------+------------+
        |                         |
  bind mounts into LXC       virtiofsd into KVM VMs
```

- **Local-first writes.** CephFS (`/mnt/local`) is shared across the Proxmox nodes. In the mergerfs branch list it is
  marked `NC` (no create), so new files are directed to the NFS branch (`RW`) while both branches stay visible in the
  merged tree.
- **Bulk media.** A NAS exports the library (`TV`, `Movies`, and so on) over NFS. `cachefilesd` on each Proxmox host
  caches NFS reads locally.
- **One path everywhere.** mergerfs presents a single combined tree. It reaches LXC containers by bind mount and KVM
  guests through virtiofsd, so SMA-NG, Sonarr, Radarr and the download clients all see the same paths.

Practical rules for SMA-NG on such a layout:

1. Use the merged path (for example `/mnt/unionfs/Media/TV`) in routing rules and service paths. Avoid mixing paths
   that exist only on the host with paths that exist only in a guest.
2. When two systems still see different prefixes, normalize them with `daemon.path-rewrites`.
3. Check the branch policy before assuming where a file lands: a path visible in the merged mount is not necessarily
   stored where you expect.
4. When a job fails on one node only, check the storage layer first: a missing bind mount, a broken virtiofsd share,
   an NFS visibility problem, a mergerfs branch-policy surprise or a cold cache.
5. After any change to this layer, confirm that the path exists on the host, in LXCs and in VMs, and that
   `daemon.routing` and `daemon.path-rewrites` still match.
