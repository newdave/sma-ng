# `sma` CLI Quick Start

`sma` runs SMA-NG's tools (convert, preview, repair, audit, rename) on a Docker host without a source checkout,
Compose, or Python. When an SMA-NG daemon answers, on this host or a remote one you configure, `sma` sends the work
to it as a job and shows its output. Otherwise it starts a throwaway container from the SMA-NG image, mounts exactly
the paths you name, runs the tool as your own user, and removes the container when it finishes.

`sma` itself is a small POSIX `sh` launcher. The logic lives in the image, so `sma` and the image are always upgraded
together.

## 1. Install

Docker is the only requirement:

```bash
curl -fsSL https://raw.githubusercontent.com/newdave/sma-ng/main/install-smang.sh | sh
```

The installer:

- pulls `ghcr.io/newdave/sma-ng:latest`; on arm64 hosts it picks the latest release instead, because `latest` is
  published for linux/amd64 only;
- installs `sma` to `/usr/local/bin` (using `sudo` if needed), or `~/.local/bin` when it cannot;
- records the image in `~/.config/sma/image`;
- creates `/opt/sma/config`, `/opt/sma/logs` and `/opt/sma/data`, owned by you, plus a sample
  `/opt/sma/config/sma-ng.yml` if none exists.

Re-running it is safe; existing config is never overwritten.

| Option | Environment | Default | Meaning |
| ------ | ----------- | ------- | ------- |
| `--tag TAG` | `SMA_IMAGE_TAG` | `latest` (release on arm64) | Image tag to install |
| `--bin-dir DIR` | `SMA_BIN_DIR` | `/usr/local/bin` | Where `sma` goes |
| | `SMA_HOME` | `/opt/sma` | Directory holding `config/`, `logs/`, `data/` |

Pass options through the pipe with `sh -s --`:

```bash
curl -fsSL https://raw.githubusercontent.com/newdave/sma-ng/main/install-smang.sh | sh -s -- --tag 3.2.23
```

Edit `/opt/sma/config/sma-ng.yml` before converting (see [Configuration](configuration.md)). Every `sma` command
reads it.

## 2. Check it works

```bash
sma version
```

```text
sma host command: 6807857c...
sma image: 3.2.23 (commit 6807857c...)
```

## 3. Run something

```bash
sma preview /mnt/media/Movies/Film.mkv        # print the conversion plan, change nothing
sma convert /mnt/media/Movies/Film.mkv        # convert, tag and file it
sma convert ~/Downloads/a.mkv ~/Downloads/b.mkv
sma repair -n /mnt/media/TV                   # dry-run repair report for a whole directory
sma audit /mnt/media/Movies                   # library audit report
```

Paths can be anywhere on the host, absolute or relative to the current directory. Arguments after the path are passed
to the tool, for example `sma convert Film.mkv -tmdb 603`.

## 4. Commands

| Command | Runs |
| ------- | ---- |
| `sma convert PATH... [ARGS]` | `manual.py -a -i PATH`, one container per path; stops at the first failure |
| `sma preview PATH [ARGS]` | `manual.py -a -i PATH -oo` (plan only) |
| `sma repair PATH... [-n] [ARGS]` | `repair.py PATH...`: stream-copy repair, never transcodes; `-n` is a dry run |
| `sma audit PATH [ARGS]` | `manual.py --audit -i PATH` |
| `sma rename PATH [ARGS]` | `rename.py PATH` (`--dry-run` to preview) |
| `sma codecs` | List supported codecs (always a throwaway container) |
| `sma shell [ARGS]` | `bash` in a throwaway container with the current directory mounted |
| `sma jobs [list\|show ID\|logs ID [-f]\|cancel ID]` | Daemon jobs (see [Daemon targets](#10-daemon-targets)) |
| `sma target add\|list\|use\|rm ...` | Daemons `sma` can send work to |
| `sma map add\|list\|rm ...` | Translate local paths to a daemon's paths |
| `sma upgrade [TAG]` | Pull the image (or switch to `TAG`) and replace `sma` from it |
| `sma version` / `sma help` | Versions / command list |

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

## 5. What gets mounted

Each run mounts, at the same path inside the container:

- **every path you name**: a directory itself, or a file through its parent directory. A path that does not exist
  stops `sma` with exit code `2` before anything runs.
- **directories from `sma-ng.yml`**, but only those that exist on the host: `output-directory`, `move-to`,
  `copy-to`, `recycle-bin` and the analyzer model and cache directories, from the base config and every profile;
- **`/opt/sma/config`** read-only, where the tools find `sma-ng.yml`;
- **`/opt/sma/logs`** for the tools' logs, if you can write to it;
- **`extra-mounts`** from `~/.config/sma/config.yml` and any `--mount PATH`.

```yaml
# ~/.config/sma/config.yml
extra-mounts:
  - /srv/scratch
```

Paths containing `:` cannot be mounted, and `/` is never mounted.

## 6. Users and hardware

The container runs as your user and group, so new files are owned by you. Under rootless Docker, `sma` leaves the
user to Docker's own mapping. On SELinux hosts the mounts get the `:z` label.

GPU access is detected on the host for every run:

- `/dev/dri` (Intel, AMD): `--device /dev/dri` plus the device nodes' groups, so VA-API and QSV work as your user;
- a working `nvidia-smi`: `--gpus all` (needs the NVIDIA container toolkit).

## 7. Scripts and cron

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

## 8. Upgrading

```bash
sma upgrade            # pull the current tag again (latest moves; a version tag does not)
sma upgrade 3.2.23     # switch to a specific release
```

`sma upgrade` replaces the `sma` script from the new image and updates `~/.config/sma/image`. If `sma` is installed
somewhere you cannot write, it tells you to run `sudo sma upgrade`.

## 9. Settings

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `SMA_TARGET` | `default-target` | Daemon target for this command |
| `SMA_IMAGE` | `~/.config/sma/image` | Image to run for this command |
| `SMA_HOME` | `/opt/sma` | Directory holding `config/`, `logs/`, `data/` |
| `SMA_CONFIG_HOME` | `~/.config/sma` | Client settings |
| `DOCKER_BIN` | `docker` | Docker executable |

## 10. Daemon targets

### Where a command runs

For `convert`, `preview`, `repair`, `audit` and `rename`, `sma` picks the daemon first:

1. `--target NAME`, else the `SMA_TARGET` variable, else `default-target` from `~/.config/sma/config.yml`;
2. with none of those, a daemon on this host at `http://127.0.0.1:8585`, if one answers.

If that daemon answers `/health` within a second, the command becomes a job on it. A chosen target that does not
answer is an error (exit `3`); with no target chosen, `sma` falls back to a throwaway container. `--local` always
uses a throwaway container, with a warning when a daemon is running, because both would compete for the GPU and the
same files.

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

### Remote daemons

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

### Scripting

`--json` prints one JSON object per job on stdout. With `--no-wait` that is the queued job; otherwise it is the final
state, and the job's output is not streamed:

```bash
sma --json repair -n /mnt/media/TV
```

```json
{"job_id": 42, "kind": "repair", "path": "/mnt/media/TV", "status": "completed", "error": null}
```

The long-running daemon itself (webhooks from Sonarr and Radarr, the queue, the dashboard) is still set up with
Docker Compose; see [Docker Compose Quick Start](docker-compose-quickstart.md).

Coming from the old `docker exec` version of `sma` or the alias file: `sma manual`, `sma smoke`, `sma logs`,
`sma status` and the `SMA_CONTAINER` / `SMA_COMPOSE_*` variables are gone. Use `docker logs -f sma-ng` for daemon logs.

## Troubleshooting

| Symptom | Fix |
| ------- | --- |
| `sma: cannot talk to Docker` (exit `3`) | Start Docker, or add yourself to the `docker` group |
| `target ... is not reachable` (exit `3`) | Check the URL with `sma target list`; is the daemon running? |
| `rejected the API key` (exit `4`) | `sma target add NAME URL --api-key-stdin` with the daemon's `daemon.api-key` |
| `... is not under any path target X serves` (exit `2`) | Add a mapping: `sma map add X LOCAL REMOTE` |
| `does not support repair jobs; upgrade all nodes` | A cluster node runs an older release: upgrade every node |
| `sma: X: no such file or directory` (exit `2`) | Check the path; it is resolved on the host before anything runs |
| `plan version ... unsupported; run sma upgrade` (exit `5`) | `sma` and the image are from different releases: `sma upgrade` |
| A tool cannot find or write a directory | It is not one `sma` mounts; add it with `--mount PATH` or `extra-mounts` |
| Output files owned by root | Older `sma` versions ran as root: `sudo chown -R "$USER" <path>` once |
| `no matching manifest for linux/arm64` | Install or upgrade with a release tag: `sma upgrade 3.2.23` |
