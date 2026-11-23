# Docker Quick Start

SMA-NG ships as a Docker image. There are two ways to run it, and they combine:

- [The `sma` host command](#the-sma-host-command): runs convert, preview, repair, audit and rename on a Docker host
  with only Docker installed, and can start the daemon itself (`sma daemon up`).
- [Docker Compose](#docker-compose): the shipped profiles, override files and cluster deployment tooling.

## The `sma` host command

`sma` runs SMA-NG's tools (convert, preview, repair, audit, rename) on a Docker host without a source checkout,
Compose, or Python. When an SMA-NG daemon answers, on this host or a remote one you configure, `sma` sends the work
to it as a job and shows its output. Otherwise it starts a throwaway container from the SMA-NG image, mounts exactly
the paths you name, runs the tool as your own user, and removes the container when it finishes.

`sma` itself is a small POSIX `sh` launcher. The logic lives in the image, so `sma` and the image are always upgraded
together.

### Install

Docker is the only requirement:

```bash
curl -fsSL https://raw.githubusercontent.com/newdave/sma-ng/main/install-smang.sh | sh
```

The installer:

- pulls `ghcr.io/newdave/sma-ng:latest`; on arm64 hosts it picks the latest release instead, because `latest` is
  published for linux/amd64 only;
- installs `sma` to `/usr/local/bin` (using `sudo` if needed), or `~/.local/bin` when it cannot;
- records the image in `~/.config/sma/image`;
- creates the data directory `SMA_HOME` with `config/`, `logs/` and `data/`, owned by you, plus a sample
  `config/sma-ng.yml` if none exists. `SMA_HOME` is `/opt/sma` on Linux and `~/.local/share/sma` on macOS
  (`$XDG_DATA_HOME/sma` when that is set); nothing is created under `/opt` on a Mac.

The rest of this guide writes `/opt/sma` for the data directory; on macOS read `~/.local/share/sma`.

Re-running it is safe; existing config is never overwritten.

| Option | Environment | Default | Meaning |
| ------ | ----------- | ------- | ------- |
| `--tag TAG` | `SMA_IMAGE_TAG` | `latest` (release on arm64) | Image tag to install |
| `--bin-dir DIR` | `SMA_BIN_DIR` | `/usr/local/bin` | Where `sma` goes |
| | `SMA_HOME` | `/opt/sma` (macOS: `~/.local/share/sma`) | Directory holding `config/`, `logs/`, `data/` |

Pass options through the pipe with `sh -s --`:

```bash
curl -fsSL https://raw.githubusercontent.com/newdave/sma-ng/main/install-smang.sh | sh -s -- --tag 3.2.23
```

Edit `/opt/sma/config/sma-ng.yml` before converting (see [Configuration](configuration.md)). Every `sma` command
reads it.

### Check it works

```bash
sma version
```

```text
sma host command: 6807857c...
sma image: 3.2.23 (commit 6807857c...)
```

### Run something

```bash
sma preview /mnt/media/Movies/Film.mkv        # print the conversion plan, change nothing
sma convert /mnt/media/Movies/Film.mkv        # convert, tag and file it
sma convert ~/Downloads/a.mkv ~/Downloads/b.mkv
sma repair -n /mnt/media/TV                   # dry-run repair report for a whole directory
sma audit /mnt/media/Movies                   # library audit report
```

Paths can be anywhere on the host, absolute or relative to the current directory. Arguments after the path are passed
to the tool, for example `sma convert Film.mkv -tmdb 603`.

### Commands

| Command | Runs |
| ------- | ---- |
| `sma convert PATH... [ARGS]` | `manual.py -a -i PATH`, one container per path; stops at the first failure |
| `sma preview PATH [ARGS]` | `manual.py -a -i PATH -oo` (plan only) |
| `sma repair PATH... [-n] [ARGS]` | `repair.py PATH...`: stream-copy repair, never transcodes; `-n` is a dry run |
| `sma audit PATH [ARGS]` | `manual.py --audit -i PATH` |
| `sma rename PATH [ARGS]` | `rename.py PATH` (`--dry-run` to preview) |
| `sma codecs` | List supported codecs (always a throwaway container) |
| `sma shell [ARGS]` | `bash` in a throwaway container with the current directory mounted |
| `sma jobs [list\|show ID\|logs ID [-f]\|cancel ID]` | Daemon jobs (see [Daemon targets](#daemon-targets)) |
| `sma daemon up\|down\|restart\|status\|logs\|upgrade` | The long-running daemon (see [Running the daemon](#running-the-daemon)) |
| `sma target add\|list\|use\|rm ...` | Daemons `sma` can send work to |
| `sma map add\|list\|rm ...` | Translate local paths to a daemon's paths |
| `sma upgrade [TAG]` | Pull the image (or switch to `TAG`) and replace `sma` from it |
| `sma status` | Same as `sma daemon status`: container, health, daemon version and commit, workers, GPU, job counts |
| `sma version` | Host command, image and daemon versions; checks the latest GitHub release and says what is out of date |
| `sma help` | Command list |

Options placed before the command:

| Option | Meaning |
| ------ | ------- |
| `--target NAME` | Send to this daemon target; fail with exit `3` if it does not answer |
| `--local` | Run in a throwaway container even when a daemon answers |
| `--no-wait` | Print the job ids instead of following the jobs |
| `--json` | Machine-readable results on stdout (one JSON object per job) |
| `--mount PATH` | Also mount `PATH` in throwaway runs (repeatable), for example a post-process scratch directory |
| `-q` | Fewer messages from `sma` itself |

`sma` sets the tool's config, input and job id itself, so `-c`, `-i` and `--job-id` are rejected as tool arguments.

### What gets mounted

Each run mounts, at the same path inside the container:

- **every path you name**: a directory itself, or a file through its parent directory. A path that does not exist
  stops `sma` with exit code `2` before anything runs.
- **directories from `sma-ng.yml`**, but only those that exist on the host: `output-directory`, `move-to`,
  `copy-to`, `recycle-bin` and the analyzer model and cache directories, from the base config and every profile;
- **`/opt/sma/config`**, where the tools find `sma-ng.yml` (they also write their `logging.ini` there);
- **`/opt/sma/logs`** for the tools' logs, if you can write to it;
- **`extra-mounts`** from `~/.config/sma/config.yml` and any `--mount PATH`.

```yaml
# ~/.config/sma/config.yml
extra-mounts:
  - /srv/scratch
```

Paths containing `:` cannot be mounted, and `/` is never mounted.

### Users and hardware

The container runs as your user and group, so new files are owned by you. Under rootless Docker, `sma` leaves the
user to Docker's own mapping. On SELinux hosts the mounts get the `:z` label.

GPU access is detected on the host for every run:

- `/dev/dri` (Intel, AMD): `--device /dev/dri` plus the device nodes' groups, so VA-API and QSV work as your user;
- a working `nvidia-smi`: `--gpus all` (needs the NVIDIA container toolkit).

### Scripts and cron

`sma` asks Docker for a terminal only when you are at one, so it works from cron, scripts and `ssh host sma ...`.

| Code | Meaning |
| ---- | ------- |
| `0` | Success |
| `1` | A daemon job failed or was cancelled |
| other | A throwaway run's own exit code (for example `1` failed, `75` not enough output space) |
| `2` | Usage error, a path that does not exist, or a path the daemon target does not serve |
| `3` | Docker, or the chosen daemon target, is not reachable |
| `4` | The daemon rejected the API key |
| `5` | `sma` and the image do not match; run `sma upgrade` |
| `130` | Ctrl-C while following a daemon job; the job keeps running |

```cron
# Nightly dry-run repair report of the TV library
0 3 * * * sma repair -n /mnt/media/TV >> /var/log/sma-repair.log 2>&1
```

### Upgrading

```bash
sma upgrade            # pull the current tag again (latest moves; a version tag does not)
sma upgrade 3.2.23     # switch to a specific release
```

`sma upgrade` replaces the `sma` script from the new image and updates `~/.config/sma/image`. If `sma` is installed
somewhere you cannot write, it tells you to run `sudo sma upgrade`.

`sma version` shows when an upgrade is due. It asks GitHub for the latest release (`newdave/sma-ng`, or
`SMA_RELEASE_REPO`) and marks the image, and the daemon if one answers, `out of date` when they are older, with the
command to run (`sma upgrade` or `sma daemon upgrade`). The check gives up after 3 seconds and never fails the
command: offline it prints `latest: unknown (...)`. Set `SMA_NO_UPDATE_CHECK=1` to skip it. `--json` prints the
same as a JSON object.

### Settings

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `SMA_TARGET` | `default-target` | Daemon target for this command |
| `SMA_IMAGE` | `~/.config/sma/image` | Image to run for this command |
| `SMA_HOME` | `/opt/sma` (macOS: `~/.local/share/sma`) | Directory holding `config/`, `logs/`, `data/` |
| `SMA_CONFIG_HOME` | `~/.config/sma` | Client settings |
| `DOCKER_BIN` | `docker` | Docker executable |

### Daemon targets

#### Where a command runs

For `convert`, `preview`, `repair`, `audit` and `rename`, `sma` picks the daemon first:

1. `--target NAME`, else the `SMA_TARGET` variable, else `default-target` from `~/.config/sma/config.yml`;
2. with none of those, a daemon on this host at `http://127.0.0.1:8585`, if one answers.

If that daemon answers `/health` within a second, the command becomes a job on it. A chosen target that does not
answer is an error (exit `3`); with no target chosen, `sma` falls back to a throwaway container. `--local` always
uses a throwaway container, with a warning when a daemon is running, because both would compete for the GPU and the
same files.

The local daemon reads the same `sma-ng.yml`, so `sma` sends its `daemon.api-key` to the `local` target without a
`sma target add` step. That key always wins for `local`, so a key saved with `sma target add local URL
--api-key-stdin` is only used when `sma-ng.yml` sets no key or the invoking user cannot read it.

```bash
sma repair -n /mnt/media/TV        # job on the local daemon, output streamed until it finishes
sma --no-wait convert ~/a.mkv      # print the job id and return
sma jobs                           # recent jobs: id, kind, status, path
sma jobs logs 42 -f                # follow a job's output
sma jobs show 42                   # status, timings, error
sma jobs cancel 42
```

While a job runs, `sma` prints its output and exits with `0` when it completes, `1` when it fails or is cancelled.
Ctrl-C stops following (exit `130`) and prints `sma jobs show <id>`; the job keeps running. Daemon jobs share the
daemon's queue, so a job may wait for others first. See [Job Kinds](daemon.md#job-kinds) for what each kind runs.

#### Remote daemons

```bash
sma target add nas http://nas.lan:8585 --api-key-stdin <<< "$SMA_API_KEY"
sma target use nas                 # make it the default
sma target list                    # * marks the default; keys are never shown
sma --target nas audit /mnt/media/Movies
```

The API key is read from standard input only, never from the command line, so it does not end up in your shell
history. `~/.config/sma/config.yml` is written with mode `0600`, and `sma` refuses to use a config holding API keys
that other users can read.

Paths are sent as typed (made absolute on this host). When a daemon sees your media under a different path, add a
mapping. The longest matching prefix wins, and paths that match no mapping are sent unchanged:

```bash
sma map add nas /Volumes/Media /mnt/unionfs/Media
sma --target nas preview /Volumes/Media/Movies/Film.mkv   # sent as /mnt/unionfs/Media/Movies/Film.mkv
```

Before submitting, `sma` checks the path against the roots the daemon serves (`GET /paths`) and stops with exit
`2` if it is outside them.

```yaml
# ~/.config/sma/config.yml
default-target: nas
targets:
  nas:
    url: http://nas.lan:8585
    api-key: "..."
    mappings:
      - {local: /Volumes/Media, remote: /mnt/unionfs/Media}
extra-mounts: [/srv/scratch]
```

#### Scripting

`--json` prints one JSON object per job on stdout. With `--no-wait` that is the queued job; otherwise it is the final
state, and the job's output is not streamed:

```bash
sma --json repair -n /mnt/media/TV
```

```json
{"job_id": 42, "kind": "repair", "path": "/mnt/media/TV", "status": "completed", "error": null}
```

Coming from the old `docker exec` version of `sma` or the alias file: `sma manual`, `sma smoke`, `sma logs`,
`sma status` and the `SMA_CONTAINER` / `SMA_COMPOSE_*` variables are gone. Use `docker logs -f sma-ng` for daemon logs.

### Running the daemon

The daemon receives webhooks from Sonarr and Radarr, keeps the job queue and serves the dashboard. `sma` can run it
for you, no Compose needed:

```bash
sma daemon up --media /mnt/media             # start sma-ng (or replace it with the current image)
sma status                                   # container state, /health, version, workers, jobs (= sma daemon status)
sma daemon logs                              # follow its log (also: sma logs)
sma daemon restart
sma daemon upgrade                           # pull the pinned image and recreate the container
sma daemon down                              # stop and remove it
```

`sma daemon up` creates a container named `sma-ng` with `--restart unless-stopped`, publishes port 8585 (change
it with `--port N`), and mounts `/opt/sma/config`, `/opt/sma/logs` and `/opt/sma/data` plus each `--media`
directory at the same path, and `/transcode` and `/recycle` when they exist. GPUs are passed through as for
throwaway runs. It then waits up to 60 seconds for the daemon to answer. `--media` and `--port` are remembered in
`~/.config/sma/config.yml`, so later `up` and `upgrade` need no options; `up` without any media root stops with
exit code `2`. Webhook paths must be under one of the media roots.

If the `sma-ng` container was started by Docker Compose, `sma daemon` drives that compose project instead:
`up` is `docker compose up -d`, `down` stops the service (the rest of the project is left alone), `upgrade` pulls
and recreates it with the compose file's image tag. Mounts and ports then stay in the compose file. Set
`SMA_DAEMON_NAME` when the container has another name. Clusters keep their own deployment tooling (see
[Cluster Operations](cluster-operations.md)); to `sma` each node is a target.

### `sma` troubleshooting

| Symptom | Fix |
| ------- | --- |
| `sma: cannot talk to Docker` (exit `3`) | Start Docker, or add yourself to the `docker` group |
| `target ... is not reachable` (exit `3`) | Check the URL with `sma target list`; is the daemon running? |
| `rejected the API key` (exit `4`) | `sma target add NAME URL --api-key-stdin` with the daemon's `daemon.api-key`; for `local`, check `$SMA_HOME/config/sma-ng.yml` is readable by you |
| `... is not under any path target X serves` (exit `2`) | Add a mapping: `sma map add X LOCAL REMOTE` |
| `does not support repair jobs; upgrade all nodes` | A cluster node runs an older release: upgrade every node |
| `sma: X: no such file or directory` (exit `2`) | Check the path; it is resolved on the host before anything runs |
| `plan version ... unsupported; run sma upgrade` (exit `5`) | `sma` and the image are from different releases: `sma upgrade` |
| A tool cannot find or write a directory | It is not one `sma` mounts; add it with `--mount PATH` or `extra-mounts` |
| Output files owned by root | Older `sma` versions ran as root: `sudo chown -R "$USER" <path>` once |
| `no matching manifest for linux/arm64` | Install or upgrade with a release tag: `sma upgrade 3.2.23` |

## Docker Compose

Run the SMA-NG daemon with Docker Compose. Compose is optional: on a single host,
`sma daemon up --media /path/to/media` starts the same daemon without it (see
[Running the daemon](#running-the-daemon)). Use Compose when you want its profiles, override
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

### Prepare Host Directories

Install the `sma` command first ([Install](#install)); it creates `/opt/sma/config`, `/opt/sma/logs` and
`/opt/sma/data` and a sample `/opt/sma/config/sma-ng.yml`, and needs no source checkout.

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

### Config Files

The installer writes `/opt/sma/config/sma-ng.yml` only when it does not exist, and `daemon.env` above comes from
the image's sample. Existing local config is never overwritten. Fresh samples are written to
`/opt/sma/config/defaults/` each time the container starts, so you can diff your config against them.

### Edit `sma-ng.yml`

Set the basics in `/opt/sma/config/sma-ng.yml`:

- `ffmpeg = /usr/local/bin/ffmpeg`
- `ffprobe = /usr/local/bin/ffprobe`
- your desired codec/container settings
- Sonarr/Radarr/Plex sections if needed

For hardware encoders, also set the appropriate `gpu =` and codec values. See [Hardware Acceleration](hardware-acceleration.md).

### Edit `Daemon:` section in `sma-ng.yml`

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

### Create a Compose `.env` File

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

### Host command and compose mounts

Every `sma` command runs in its own throwaway container that reads the same `/opt/sma/config/sma-ng.yml` as the
daemon, so it works alongside a running compose daemon (see [The `sma` host command](#the-sma-host-command) for
install, commands and mounts). Paths no longer have to sit under the daemon's `/mnt`, `/transcode` and `/recycle`
mounts.

The daemon itself still uses the compose mounts above: webhook paths must be visible at the same path inside the
daemon container, so keep media under `/mnt` (or add mounts) for Sonarr, Radarr and other integrations.

#### Migrating from the alias file

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

### Stop grace period

Every daemon service sets `stop_grace_period: ${STOP_GRACE_PERIOD:-2m}`. `docker compose stop`, `restart`, and
`mise run cluster -- stop` / `mise run cluster -- restart` give the daemon that long to drain before Docker sends SIGKILL; jobs still
running at the deadline are requeued. Raise it for long transcodes by setting `STOP_GRACE_PERIOD=10m` in a `.env` file
next to `docker-compose.yml` or in the shell running compose (compose substitution; `daemon.env` does not apply).
The effective drain is the smaller of `daemon.shutdown-grace-seconds` (default `100`) and `STOP_GRACE_PERIOD`: raise
both together and keep `shutdown-grace-seconds` below `STOP_GRACE_PERIOD`.

### Edit `daemon.env`

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

### Start a Profile

From the repo root:

| Profile | Hardware | Database |
| ------------- | ----------------- | ----------------------------------- |
| `software` | CPU only | SQLite (`/opt/sma/data/sma-ng.db`) |
| `software-pg` | CPU only | external PostgreSQL (`db_url`) |
| `intel` | Intel QSV / VAAPI | SQLite |
| `intel-pg` | Intel QSV / VAAPI | external PostgreSQL (`db_url`) |
| `nvidia` | NVIDIA NVENC | SQLite |
| `nvidia-pg` | NVIDIA NVENC | external PostgreSQL (`db_url`) |

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml --profile intel up -d
```

### Verify

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

### Hardware-Specific Notes

#### Intel QSV

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

#### NVIDIA

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

### Submitting a Test Job

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

### Common Customizations

#### Change the published daemon port

In `docker/.env`:

```bash
PORT=8686
```

#### Use an external PostgreSQL instance

Use the matching `-pg` profile and set `daemon.db_url` in `sma-ng.yml`:

```yaml
daemon:
  db_url: postgresql://sma:password@db-host:5432/sma
```

Database URLs in `/opt/sma/config/daemon.env` are ignored.

#### Route different media roots to different profiles

```yaml
Daemon:
  default_config: /config/sma-ng.yml
  path_configs:
    - path: /mnt/unionfs/Media/TV
      profile: rq
    - path: /mnt/unionfs/Media/Movies
      profile: lq
```

### Updating

Pull new code and recreate the selected profile:

```bash
git pull --rebase
docker compose --env-file docker/.env -f docker/docker-compose.yml --profile software-pg up -d --build
```

Replace `software-pg` with whichever profile you use.

### Compose troubleshooting

#### Daemon starts but jobs fail immediately

- check `/opt/sma/config/sma-ng.yml`
- verify media paths inside the container
- verify `ffmpeg` and `ffprobe` paths

#### Jobs are accepted but never run

- check PostgreSQL connectivity for `*-pg` or external DB mode
- check `/health` and `/status`
- check `/opt/sma/logs`

#### Intel profile cannot see `/dev/dri`

- verify host device exists (`ls -l /dev/dri` on the host)
- for SR-IOV guests, verify the guest itself exposes a matching `card*` and `renderD*` pair under `/dev/dri`
- check container startup logs for `granted ubuntu access to /dev/dri/...` lines confirming the entrypoint added the runtime user to the host render/video groups
- verify `vainfo` works inside the container

#### NVIDIA profile starts without GPU

- verify `nvidia-container-toolkit`
- verify host driver installation
- check `docker info` and container runtime configuration
