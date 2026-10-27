# Docker Host CLI: One `sma` Command, Daemon Identity, One Path Per File

> **STATUS: COMPLETE**

name: "Docker host CLI simplification (items 1–3)"
description: |
  Close the gap between the SMA-NG Docker image and a human operating it from the Docker host:
  replace the sourced Bash alias file with one POSIX `sma` host command shipped in the image,
  run every host-initiated command with the same identity and GPU groups as the daemon, and make
  every container path identical to its host path so operators never translate paths.

## Discovery Summary

### Current State (verified in the tree)

- `setup/sma-ng-docker-aliases.sh` is a Bash-only file the operator must `source`. It defines
  `sma-manual`, `sma-convert`, `sma-preview`, `sma-codecs`, `sma-smoke`, `sma-rename`, `sma-repair`,
  `sma-logs` and `sma-shell`, all as `docker exec -it "$CONTAINER" …` with `CONTAINER=sma-ng` hard-coded.
- `setup/install-docker-target.sh:85` installs that file to `/opt/sma/sma-ng-docker-aliases.sh`.
- `docker exec` does not run the image `ENTRYPOINT`, and the aliases pass no `-u`. Every CLI command
  therefore runs as **root** with no `LIBVA_DRIVER_NAME`. The daemon (PID 1) goes through
  `docker/docker-entrypoint.sh`. That script sets `LIBVA_DRIVER_NAME=iHD` on Intel, optionally fixes
  render GIDs (`SMA_ENTRYPOINT_FIX_GIDS`), and drops to `ubuntu` with an explicit supplementary group
  list via `setpriv --reuid=ubuntu --regid=ubuntu --groups "$keep_groups"`.
- `-it` is unconditional, so the aliases fail without a TTY (cron, scripts, `ssh host cmd`).
- `docker/docker-compose.yml` has six services (`sma-software`, `sma-software-pg`, `sma-nvidia`,
  `sma-nvidia-pg`, `sma-intel`, `sma-intel-pg`), selected by compose profile, all with
  `container_name: sma-ng`. Each mounts:
  - `/opt/sma/config:/config`, `/opt/sma/logs:/logs`, `/opt/sma/data:/data` (container-only paths)
  - `/mnt:/mnt`, `/transcode:/transcode`, `/recycle:/recycle` (host path == container path)
  - `/mnt/unionfs/downloads:/downloads` — the **same files reachable under a second name**, since
    `/mnt` is already mounted.
- Docs that describe the alias workflow or `/downloads`:
  - `docs/docker-compose-quickstart.md:60,138-160`
  - `docs/deployment.md:137-138`
  - `docs/troubleshooting.md:211` ("sma-repair inside Docker")
  - `docs/multi-instance-deployment.md:96,296-297` (a `/downloads` → `/mnt/downloads` rewrite example)
- `daemon.path-rewrites` (`setup/sma-ng.yml.sample:30`) already rewrites incoming webhook paths.

### Problems Being Solved

1. **Root-owned output, GPU mismatch:** CLI runs as root without the daemon's groups or
   `LIBVA_DRIVER_NAME`. Converted files are root-owned, and VAAPI/QSV can behave differently from
   daemon jobs.
2. **Fragile host UX:** a Bash-only sourced file, a hard-coded container name, and a forced TTY.
3. **Path translation:** a file under `/mnt/unionfs/downloads` has two container paths. Routing and
   rewrites keyed on one don't match the other, and the usage text tells operators to pass
   "container paths".

### Out of Scope

Items 4–8 of the original suggestion list: defaulting `convert`/`repair` to the daemon API,
`[project.scripts]` entry points, collapsing the compose services, a single config surface, and a
dedicated operator doc page. This PRP leaves room for item 4 by keeping subcommand names stable.

## Goal

An operator on the Docker host:

- installs one command (`sma`), which always matches the image version;
- runs `sma convert /mnt/unionfs/Media/Movie.mkv` with the same path they see on the host;
- gets output owned by the daemon's user, using the daemon's GPU groups and VAAPI driver;
- can use it from cron and scripts (no TTY needed).

## Success Criteria

- [x] `sma <subcommand>` works from `sh`, `bash` and `zsh`, with and without a TTY, and never needs sourcing.
- [x] Files written by `sma convert`/`repair`/`rename` are owned by `ubuntu` (the daemon's UID/GID),
      not root.
- [x] Inside `sma shell`, and for every subcommand, `id -G` matches the daemon process's groups, and on
      Intel hosts `LIBVA_DRIVER_NAME` is set the same way.
- [x] `/downloads` is no longer mounted. Webhooks from download clients that report `/downloads/...`
      still resolve, via a shipped `path-rewrites` entry.
- [x] `sma` rejects a host path outside any identity mount with a single-line error and exit code 2,
      before running anything in the container.
- [x] `setup/sma-ng-docker-aliases.sh` is replaced by a deprecation shim. The docs describe only `sma`.
- [x] ShellCheck clean (`shellcheck -x`), with no inline Python in any shell file.

## Design

### 1. `sma` host command (`setup/sma-host.sh` → `/usr/local/bin/sma`)

A single POSIX `sh` script, committed as `setup/sma-host.sh` and copied into the image as
`/usr/local/bin/sma-host` (mode 755).

**Install** (documented, plus done by `setup/install-docker-target.sh`):

```sh
docker run --rm --entrypoint cat ghcr.io/newdave/sma-ng:latest /usr/local/bin/sma-host > /usr/local/bin/sma
chmod 755 /usr/local/bin/sma
```

`sma version` prints the script's embedded image version (from `SMA_GIT_SHA`, substituted at build time)
next to the running container's `SMA_GIT_SHA`, and warns on mismatch.

**Container resolution**, in order:

1. `$SMA_CONTAINER` if set.
2. `docker compose ps -q` for the first running service whose name starts with `sma-`, in the
   project dir `$SMA_COMPOSE_DIR` (default `/opt/sma/docker`).
3. The container named `sma-ng` (the current `container_name`).

If nothing is running, it fails with `sma: no running sma-ng container (set SMA_CONTAINER)`.
`DOCKER_BIN` (default `docker`) is kept for parity with the alias file.

**TTY handling:** add `-i` when stdin is not `/dev/null`, and `-t` only when `[ -t 0 ] && [ -t 1 ]`.

**Subcommands.** Arguments pass through, and each runs via item 2's `entrypoint.sh run`:

| Subcommand              | Runs in container                       | Notes                                        |
| ----------------------- | --------------------------------------- | -------------------------------------------- |
| `sma convert PATH …`    | `python manual.py -i PATH -a …`         | path check (item 3)                          |
| `sma preview PATH …`    | `python manual.py -i PATH -oo …`        | path check                                   |
| `sma manual …`          | `python manual.py …`                    | escape hatch; path check on `-i` value       |
| `sma repair PATH… …`    | `python repair.py …`                    | path check on every positional path          |
| `sma rename PATH …`     | `python rename.py …`                    | path check                                   |
| `sma audit PATH …`      | `python manual.py --audit -i PATH …`    | path check                                   |
| `sma codecs`            | `python manual.py -cl`                  |                                              |
| `sma smoke`             | `python daemon.py --smoke-test`         |                                              |
| `sma shell`             | `bash`                                  | always TTY when available                    |
| `sma logs …`            | host `docker logs -f CONTAINER …`       | no exec                                      |
| `sma status`            | `docker inspect` state + `/health` curl | host side; no secrets printed                |
| `sma version` / `help`  | host side                               |                                              |

Exit codes: the container command's exit code is passed through. Usage errors and bad paths exit 2,
and no container found exits 3.

**Alias file:** `setup/sma-ng-docker-aliases.sh` becomes a shim. It defines the old alias names as
calls to `sma <sub>` and prints one deprecation line to stderr when sourced. It is removed in a later
release.

### 2. Daemon identity for host commands (`docker/docker-entrypoint.sh run`)

Add a `run` mode to the entrypoint. The script's existing structure stays: seeding, then
GPU/LIBVA setup, then the privilege drop.

```sh
docker exec [-i] [-t] "$CONTAINER" /usr/local/bin/entrypoint.sh run python manual.py …
```

- When `$1 = run`, `shift` and **skip first-run config seeding, defaults refresh, `chown -R` of
  `CONFIG_DIR`, `detect-gpu.sh`, and the `SMA_ENTRYPOINT_FIX_GIDS` mutation of `/etc/group`**. Those
  belong to container start, and the daemon's run already applied the GID fix-up to `/etc/group`.
- **Do** re-apply the environment-only setup that `docker exec` does not inherit. Currently that is
  the `LIBVA_DRIVER_NAME=iHD` export when unset and `/sys/module/i915` exists. Move it into a
  `set_runtime_env()` function called by both modes, so the two cannot drift.
- Reuse the existing privilege drop unchanged. Move it into a `drop_privileges "$@"` function used by
  both modes:
  - if the process is root, compute `keep_groups` from `id -G ubuntu` plus `id -G` minus `0`, then run
    `exec setpriv --reuid=ubuntu --regid=ubuntu --groups "$keep_groups" -- "$@"`;
  - otherwise `exec "$@"`.

  Under `docker exec` as root, `id -G` includes the compose `group_add` GIDs, so `keep_groups` matches
  the daemon's.
- `WORKDIR` is `/app` for `docker exec`, so relative `manual.py` paths keep working. `run` must not
  `cd`.

### 3. One path per file

- **Compose:** remove `"/mnt/unionfs/downloads:/downloads:rslave"` from all six services in
  `docker/docker-compose.yml`. The same tree is already reachable at the identical path through
  `/mnt:/mnt`.
- **Webhook compatibility:** download clients in other containers often report `/downloads/...`. The
  `path-rewrites` entry `{from: /downloads, to: /mnt/unionfs/downloads}` keeps those requests working.
  - Commented in `setup/sma-ng.yml.sample` (via the schema/sample generator, not a hand edit).
  - Shown in `docs/docker-compose-quickstart.md` and `docs/multi-instance-deployment.md`.

  Relies on the separator-aware rewrite-boundary fix in `resources/config_loader.py` (in progress
  in this tree). Without it, `/downloads` would also rewrite `/downloadsX`.
- **Host path check in `sma`:**
  - Read the container's mounts once per invocation:
    `docker inspect -f '{{range .Mounts}}{{.Source}}|{{.Destination}}{{"\n"}}{{end}}' CONTAINER`.
  - Resolve the argument to an absolute host path. Use `realpath` when available, else
    `cd "$(dirname p)" && pwd -P`; the target may not exist yet for `rename`.
  - Accept it only if it equals or is under (separator-aware) a mount whose `Source == Destination`.
  - Otherwise exit 2 with `sma: PATH is not visible at the same path inside the container (mounted:
    /mnt /transcode /recycle)`.
  - The check validates only and never rewrites. Operator config, logs and routing then all show one
    path.
- **Docs:** replace "must be container-visible paths, such as `/mnt/...`, `/downloads/...`"
  (`docs/docker-compose-quickstart.md:159`) with the identity-mount rule. List `/config`, `/logs` and
  `/data` as container-only (use `sma shell` to inspect them).

## Implementation Tasks (in order)

> Sequencing: `docker/`, `setup/` and `docs/deployment.md` are being edited by another in-flight
> change (build/CI hardening, including the entrypoint `--init-groups` → `--groups` change shown
> above). Start only after that lands, and rebase onto its result.

1. **Entrypoint refactor (no behaviour change):** in `docker/docker-entrypoint.sh`, extract
   `set_runtime_env` (the LIBVA export) and `drop_privileges` (the existing `setpriv` block). The
   daemon path must produce byte-identical behaviour.
2. **Entrypoint `run` mode:** branch on `$1 = run` before seeding. Call `set_runtime_env`, then
   `drop_privileges "$@"`.
3. **`setup/sma-host.sh`:** implement the resolution, TTY, subcommand table, path check, exit codes,
   `help` and `version`. No bashisms (`local` is allowed only if the repo's shell style already uses
   it in POSIX scripts; otherwise use prefixed globals).
4. **Dockerfile:** `COPY setup/sma-host.sh /usr/local/bin/sma-host` with `chmod 755`, and substitute
   `SMA_GIT_SHA` into the script at build time (a `sed` placeholder at build is fine). Keep the existing
   `ENTRYPOINT`/`CMD` unchanged.
5. **Compose:** remove the `/downloads` mount from all six services.
6. **Sample/schema:** add the commented `path-rewrites` example through the sample generator
   (`mise run config:sample`). If the sample's comments are hand-maintained, follow that file's
   existing convention.
7. **Installer:** `setup/install-docker-target.sh` installs `/usr/local/bin/sma` (from the image, per
   the command above, or from the repo copy when running from a checkout). Keep writing the alias shim
   for one release.
8. **Alias shim:** rewrite `setup/sma-ng-docker-aliases.sh` as a thin deprecation shim over `sma`.
9. **Docs** (same change, per CLAUDE.md):
   - `docs/docker-compose-quickstart.md`: replace the alias section with `sma` install and usage, the
     mounts list, and the `/downloads` rewrite.
   - `docs/deployment.md`: replace the alias paragraph.
   - `docs/troubleshooting.md:211`: "sma-repair inside Docker" becomes `sma repair`.
   - `docs/multi-instance-deployment.md`: update the `/downloads` example.
   - Add a "Migrating from the alias file" note: `source` is no longer needed, `/downloads/...`
     arguments become `/mnt/unionfs/downloads/...`, and output is now `ubuntu`-owned.
10. **Tests** (see Validation).

## Validation

### Static

```sh
shellcheck -x setup/sma-host.sh setup/sma-ng-docker-aliases.sh docker/docker-entrypoint.sh
source venv/bin/activate && python -m pytest -q tests/test_docker.py
```

### Unit tests (`tests/test_docker.py` or a new `tests/test_sma_host.py`)

Run the script with a stub `docker` on `PATH` (a fixture writes a tiny POSIX stub that records its argv
and prints canned `inspect`/`compose ps` output) and assert:

- each subcommand maps to the exact `docker exec … /usr/local/bin/entrypoint.sh run python …` argv;
- `-t` is present only with a TTY, and `-i` only when stdin is not `/dev/null`;
- container resolution order: `SMA_CONTAINER`, then compose, then `sma-ng`;
- the path check accepts `/mnt/x` and `/mnt` itself;
- the path check rejects `/mntx/y`, `/downloads/x`, `/opt/sma/config/x`, and relative paths that
  resolve outside the mounts, each with exit 2 and one stderr line;
- the exit code of the stubbed exec is passed through;
- the compose file has no `/downloads` mount in any service, and every non-`/config|/logs|/data` mount
  has `Source == Destination` (parse YAML in the test);
- the entrypoint contains a `run` branch that runs before seeding and calls `drop_privileges`, and
  `drop_privileges` does not use `--init-groups`.

### Manual (Docker host)

```sh
sma version
sma codecs                                  # no TTY needed: also run via `sma codecs < /dev/null`
sma shell -c 'id; echo "$LIBVA_DRIVER_NAME"'  # groups and LIBVA match `docker top`/PID 1 env
sma preview /mnt/unionfs/Media/test.mkv
sma convert /mnt/unionfs/Media/test.mkv && stat -c '%U:%G' /mnt/unionfs/Media/test.mp4   # ubuntu:ubuntu
sma convert /downloads/x.mkv                # exit 2, clear message
curl -X POST … -d '{"path":"/downloads/complete/x.mkv"}' /webhook   # rewritten to /mnt/unionfs/downloads
```

## Known Gotchas

- `docker exec` skips the `ENTRYPOINT` and starts with no env from PID 1's runtime exports. Anything
  set with `export` in the entrypoint must go into `set_runtime_env`. Image `ENV` and compose
  `env_file` values are already present.
- `SMA_ENTRYPOINT_FIX_GIDS` edits `/etc/group` only at container start. `run` relies on that having
  happened and must not re-run it, since that would race the daemon and require root group edits per
  command.
- `sma shell` as `ubuntu` cannot install packages. Document `docker exec -u 0 -it sma-ng bash` for
  root debugging.
- On hosts where `/mnt/unionfs` is a FUSE mount, `rslave` propagation is still needed. Removing
  `/downloads` doesn't change that for `/mnt`.
- Path-rewrite matching must be separator-aware; see the in-flight `config_loader` fix.
- macOS hosts: `realpath` may be missing, so use the `cd … && pwd -P` fallback.
- Don't log or echo the API key in `sma status`. Use `/health`, which is unauthenticated, and container
  state only.

## Confidence

8/10 for one-pass implementation. The main risks are interactions with the in-flight entrypoint and
compose changes (hence the sequencing note) and download-client path conventions that differ per
deployment. The `path-rewrites` entry covers the common `/downloads` case.

## Completion Notes

- Landed with static and fake-docker tests (`tests/test_sma_host.py`, `tests/test_docker.py`). The "Manual (Docker host)"
  checks (file ownership, `id -G` parity, live webhook rewrite) were not run in this change and need a Docker host.
- The `/downloads` rewrite example lives in `docs/daemon.md`, `docs/deployment.md`, `docs/docker-compose-quickstart.md`
  and `docs/multi-instance-deployment.md`; the schema default and sample stay `path-rewrites: []`.
- `sma status` reports container state only; the image has no curl, so no `/health` probe.
