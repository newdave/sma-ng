# `sma` CLI Quick Start

`sma` is a small command you install on the Docker host. It runs SMA-NG's tools (convert, preview, repair, rename,
audit, …) inside the running SMA-NG container, so you never type `docker exec`. It is one POSIX `sh` script that
ships inside the image, which keeps it in step with the image version.

This guide assumes the container is already running from [Docker Compose Quick Start](docker-compose-quickstart.md).

## 1. Install

Copy the script out of the running container (works on any CPU architecture, and always matches the image that is
actually running):

```bash
sudo docker cp sma-ng:/usr/local/bin/sma-host /usr/local/bin/sma
sudo chmod 755 /usr/local/bin/sma
```

Other ways to get it:

```bash
# From an image without running it. `latest` is published for linux/amd64 only; on an arm64 host either add
# --platform linux/amd64 (the script itself is architecture-independent) or use a release tag (amd64 + arm64).
id=$(docker create --platform linux/amd64 ghcr.io/newdave/sma-ng:latest)
sudo docker cp "$id":/usr/local/bin/sma-host /usr/local/bin/sma
docker rm "$id"

# From a repository checkout (also creates the /opt/sma directories):
mise run setup:docker:target
```

Your user needs permission to run `docker` (membership of the `docker` group, or prefix `sma` with `sudo`).

## 2. Check it can see the container

```bash
sma version   # host script and image commits; warns if they differ
sma status    # container=/sma-ng state=running started=...
```

```text
sma host script: c5ae7fc03410f519ddda34edd2af1e2ab008c25d
container image: c5ae7fc03410f519ddda34edd2af1e2ab008c25d
```

## 3. Convert something

Use the same paths you see on the host:

```bash
sma preview /mnt/unionfs/Media/Movies/Film.mkv   # print the conversion plan, change nothing
sma convert /mnt/unionfs/Media/Movies/Film.mkv   # convert, tag and file it like the daemon would
sma convert /mnt/unionfs/Media/TV/Show            # a whole directory
```

`preview` and `convert` run in automatic mode (`-a`) and never stop to ask questions. Extra arguments after the path
are passed to `manual.py`, for example `sma convert FILE -tmdb 603` or `sma convert FILE -p rq` for a profile.

## 4. Everyday commands

| Command                    | What it does                                                     |
| -------------------------- | ---------------------------------------------------------------- |
| `sma preview PATH [ARGS]`  | Conversion plan only (`manual.py -i PATH -oo -a`)                |
| `sma convert PATH [ARGS]`  | Convert a file or directory (`manual.py -i PATH -a`)             |
| `sma repair PATH... [-n]`  | Stream-copy repair, never transcodes; `-n` is a dry run          |
| `sma rename PATH [ARGS]`   | Rename with SMA naming templates (`--dry-run` to preview)        |
| `sma audit PATH`           | Library audit report (`manual.py --audit -i PATH`)               |
| `sma manual ARGS...`       | `manual.py` with your own arguments                              |
| `sma codecs`               | List supported codecs                                            |
| `sma smoke`                | Daemon config smoke test                                         |
| `sma shell`                | Bash inside the container as the daemon user                     |
| `sma logs`                 | Follow the container logs (`docker logs -f`)                     |
| `sma status` / `version`   | Container state / script and image versions                      |
| `sma help`                 | The command list                                                 |

Everything runs as the daemon's user (`ubuntu`) with the daemon's GPU groups and VA-API driver setting, so files
`sma` writes are owned like the daemon's and hardware encoding behaves the same. For a root shell use
`docker exec -u 0 -it sma-ng bash`.

## 5. Paths: one path per file

`sma` passes paths through unchanged, so a path only works if the container sees it at the same location. The
compose file mounts `/mnt`, `/transcode` and `/recycle` at identical paths. `sma` checks every path against the
container's mounts before running anything:

```text
$ sma preview /etc/hosts
sma: /etc/hosts is not visible at the same path inside the container (mounted: /mnt /transcode /recycle)
```

Relative paths work too and are expanded on the host (`cd /mnt/unionfs/Media/Movies && sma preview Film.mkv`).
`/config`, `/logs` and `/data` are container-only paths; look at them with `sma shell`.

## 6. Scripts and cron

`sma` only asks Docker for a terminal when you are at one, so it works from cron, scripts and
`ssh host sma ...`. Exit codes:

| Code  | Meaning                                                          |
| ----- | ---------------------------------------------------------------- |
| `0`   | Success (or the command's own exit code otherwise)               |
| `2`   | Usage error, or a path the container cannot see                  |
| `3`   | No running SMA-NG container found                                |

```cron
# Nightly dry-run repair report of the TV library
0 3 * * * sma repair -n /mnt/unionfs/Media/TV >> /var/log/sma-repair.log 2>&1
```

`sma manual` without `-a` may ask interactive questions; with no terminal attached it exits with code `2` and tells
you to re-run with `-a` instead of waiting forever.

## 7. More than one container

`sma` picks the container in this order:

1. `SMA_CONTAINER` (or `CONTAINER`) if set.
2. The first running service of the compose project in `SMA_COMPOSE_DIR` (default `/opt/sma/docker`), narrowed to
   `SMA_COMPOSE_SERVICE` if set.
3. The container named `sma-ng`.

```bash
SMA_CONTAINER=sma-ng-4k sma convert /mnt/unionfs/Media/Movies-4K/Film.mkv
```

`DOCKER_BIN` overrides the `docker` executable (for example a wrapper script).

## 8. Updating and removing

After `mise run deploy:remote` or `docker compose pull && docker compose up -d`, `sma version` warns when the
host script and the new image differ. Reinstall with the same `docker cp` command from step 1.

To remove it: `sudo rm /usr/local/bin/sma`.

Coming from the old alias file (`source /opt/sma/sma-ng-docker-aliases.sh`)? It is now a deprecated shim that
forwards the `sma-*` names to `sma`; see
[Migrating from the alias file](docker-compose-quickstart.md#migrating-from-the-alias-file).

## Troubleshooting

| Symptom                                               | Fix                                                                                 |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `sma: no running sma-ng container` (exit `3`)         | Start the stack, or set `SMA_CONTAINER` / `SMA_COMPOSE_DIR` to point at yours       |
| `... is not visible at the same path` (exit `2`)      | Use a path under an identity mount (`/mnt`, `/transcode`, `/recycle`) or add a mount |
| `permission denied ... docker.sock`                   | Add your user to the `docker` group (`sudo usermod -aG docker $USER`) or use `sudo`  |
| `no matching manifest for linux/arm64` while installing | Use the `docker cp` install from the running container, or add `--platform linux/amd64` |
| `sma version` warns the versions differ               | Reinstall `sma` from the running container                                          |
| A command waits for input in a script                 | Add `-a` to `sma manual` (automatic mode)                                            |
