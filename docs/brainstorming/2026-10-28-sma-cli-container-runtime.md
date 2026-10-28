# Feature Brainstorming Session: `sma` CLI as a Container Runtime and Daemon Client

STATUS: IN-FLIGHT

**Date:** 2026-10-28
**Session Type:** Product and architecture design (redesign of the Docker host CLI and installer)

## 1. Context & Problem Statement

### Problem Description

Using SMA-NG on a Docker host currently requires a source checkout or a hand-built compose setup, and the
`sma` host command (`setup/sma-host.sh`) can only `docker exec` into an already-running daemon container.
Consequences:

- A user who wants to repair or convert one directory must first deploy the long-lived daemon.
- Paths only work if they sit under the daemon's identity mounts (`/mnt`, `/transcode`, `/recycle`); anything
  else is rejected with "not visible at the same path".
- Docker Compose is required even when no daemon is wanted.
- The daemon is only reachable from the CLI on the same host; there is no way to drive a NAS or cluster
  daemon from a laptop.
- Installation docs assume a checkout (`mise run setup:docker:target`).

The redesign makes **the image the product** and **`sma` the only thing a user installs**. For each command,
`sma` either submits a job to a daemon over HTTP or runs the image as a short-lived container that mounts
exactly the paths named, then removes it.

### Target Users

- **Primary:** an operator on a Docker host running `sma repair /some/dir` or `sma convert file.mkv`
  interactively or from cron, with or without a daemon.
- **Secondary:** an operator on a laptop driving a remote daemon (NAS, cluster node) with local paths that
  map to the daemon's paths.
- **Tertiary:** scripts on other machines feeding a daemon (`--json`, stable exit codes).

### Success Criteria

- One-line install (`curl -fsSL …/install-smang.sh | sh`) needs only Docker; no checkout, no Compose, no
  mise, no Python on the host.
- `sma repair /any/dir` works on a host with no daemon: it mounts `/any/dir`, runs as the caller's uid/gid
  with GPU passthrough, exits with the tool's exit code, and leaves no container behind.
- With a reachable daemon (local or remote), the same command is submitted over HTTP, and progress and
  result are shown.
- `sma daemon up/down/status/logs/upgrade` works on hosts with and without Compose.
- `sma upgrade` moves the CLI and the image together; the CLI never drifts from the image it runs.

### Constraints & Assumptions

- Host dependencies: POSIX `sh`, `docker`, `mktemp`. `curl` is needed for the installer only.
- Linux and macOS hosts (Docker Desktop on macOS: no GPU, `host.docker.internal` networking). Windows is
  out of scope.
- `latest` is linux/amd64 only; release tags are amd64 + arm64. The installer pins a release tag on arm64.
- Clusters keep their existing deployment tooling (`deploy:remote`, `cluster_compose.sh`, compose
  profiles). `sma` treats cluster nodes as remote HTTP targets only.
- Daemon auth is the existing API key (`X-API-Key`, Bearer, or Basic).

## 2. Brainstormed Ideas & Options

Decisions taken in the session, in order:

| # | Question | Decision |
| - | -------- | -------- |
| 1 | Where does `sma` run? | On the Docker host, **and** as a client of remote daemons |
| 2 | Paths for remote daemons | Translate through per-target prefix mappings if one matches, else pass through |
| 3 | Daemon running and a command is typed | Submit to the daemon by default; `--local` forces a throwaway container |
| 4 | Form of the CLI | Thin POSIX shell bootstrap on the host; all logic in Python inside the image |
| 5 | How the in-image client drives Docker | Plan, then execute: the client writes a plan, the bootstrap executes it |
| 6 | `sma daemon` vs Compose | Detect and adopt: compose-managed daemons via compose, otherwise plain `docker run -d` |
| 7 | Scope | Daemon interaction is HTTP-only, so local and remote targets are the same code path; no `--bg` |

### Option A: Plan-then-execute bootstrap (CHOSEN)

- **Description:** `sma` (shell) runs the client container with the user's config mounted and a temporary
  plan directory. The client decides everything and writes a plan file of Docker command lines. The
  bootstrap executes them, running the last one with `exec`.
- **Pros:** no Docker socket inside any container; Ctrl-C, TTY and exit codes go straight to the worker; no
  orphaned containers; the bootstrap stays a small, auditable interpreter.
- **Cons:** two container starts per throwaway command (about 1–2 s); the plan format must be parseable by
  `sh`.
- **Effort:** M

### Option B: Client drives Docker via a mounted socket

- **Description:** the client container gets `/var/run/docker.sock` and starts sibling containers itself.
- **Pros:** one entry point; mid-flight decisions in Python.
- **Cons:** socket in a container; Docker CLI/SDK added to the image; two-layer signal forwarding; orphaned
  workers when the client dies; rootless and Docker Desktop socket differences.
- **Effort:** M–L. Rejected.

### Option C: Python zipapp or static Go binary on the host

- **Pros:** a richer client with no container start for HTTP-only commands.
- **Cons:** needs Python on the host (zipapp) or a second language and toolchain (Go).
- **Effort:** L. Rejected in favour of keeping a single language and an image-only product.

### Additional Ideas Considered

- `--bg` / `sma jobs` / `sma attach` for detached throwaway runs: dropped. Long work goes to a daemon.
- `sma` managing clusters: out of scope; existing tooling stays.
- Running every command throwaway even when a daemon exists: rejected because it competes with the daemon
  for the GPU, files and the database.

## 3. Decision Outcome

### Chosen Approach

A shell bootstrap with a plan-then-execute protocol, plus an in-image Python client that routes each
command to either an HTTP daemon target or a throwaway container plan.

### Rationale

- One language (Python) for all decisions; tests run in the existing suite, and the logic versions with
  the image.
- The host script stays small enough to audit and keep ShellCheck-clean.
- HTTP is the only daemon interface, so a local daemon is just the target `http://127.0.0.1:8585`.

### Trade-offs Accepted

- Docker is required even on a laptop that only talks to remote daemons (the client runs in a container).
- Commands pay one extra container start.
- The daemon must grow non-convert job kinds before every command can be submitted.

## 4. Implementation Plan

### Architecture

```text
host                                   image (sma-ng)
────                                   ──────────────
sma (sh bootstrap)
  ├─ collect host facts ─────────────▶ docker run --rm [-it] sma-ng sma-client ARGS
  │   PWD, uid/gid, OS, /dev/dri GIDs,      ├─ load ~/.config/sma/config.yml (targets, mappings)
  │   nvidia, daemon container inspect      ├─ resolve target → probe /health
  │                                         ├─ daemon reachable: HTTP submit, stream, exit code
  │                                         └─ else: write plan → /plan/plan
  └─ read plan, run steps, exec last ◀──────┘
        docker run --rm -v /x:/x --user U:G --device … sma-ng run python repair.py /x
```

### Bootstrap (`setup/sma-host.sh`, installed as `sma`)

- Reads the pinned image from `~/.config/sma/image` (one line, written by the installer and `sma upgrade`).
- Collects host facts and passes them as environment variables:
  - `SMA_HOST_PWD`, `SMA_HOST_UID`, `SMA_HOST_GID`, `SMA_HOST_OS`;
  - `SMA_HOST_DRI_GIDS` (from `ls -ln /dev/dri`), `SMA_HOST_NVIDIA`;
  - `SMA_IMAGE` (its own pinned ref).
- Writes `docker inspect sma-ng` output (if any) to the plan directory, so the client can detect a
  compose-managed daemon.
- Runs the client:
  - `docker run --rm` with `-it` only when stdin and stdout are TTYs;
  - mounts `~/.config/sma` read-only, `/opt/sma/config` read-only, and the plan directory;
  - networking: `--network host` on Linux, `host.docker.internal` on Docker Desktop.
- Executes the plan file:

  ```text
  step docker      # begins a docker invocation
  arg run
  arg --rm
  arg -v
  arg /media/My Show:/media/My Show
  end              # runs it; non-zero aborts; the final step is exec'd
  ```

  Arguments containing a newline are rejected by the client. No step kind runs arbitrary shell.
- Implements `sma upgrade` itself:
  1. Pull the new tag.
  2. Copy `/usr/local/bin/sma-host` out with `docker create` + `docker cp`.
  3. Replace itself and rewrite `~/.config/sma/image`.

### Client (`resources/cli/`, console script `sma-client` in the image)

- **Commands:**
  - work: `convert`, `repair`, `audit`, `preview`, `rename`, `codecs`, `shell` (throwaway only);
  - jobs: `jobs list|show|logs|cancel`;
  - daemon: `daemon up|down|restart|status|logs|upgrade`;
  - client config: `target add|list|use|rm`, `map add|list|rm`;
  - other: `version`, `help`.
- **Target resolution:** `--target` > `SMA_TARGET` > `default-target` in config > implicit `local`
  (`http://127.0.0.1:8585`).
- **Routing:**
  - Probe `GET /health` with a 1 s timeout.
  - Reachable and no `--local`: submit over HTTP.
  - Unreachable implicit `local`: throwaway plan.
  - Unreachable explicit target: exit 3.
- **Throwaway plan:**
  - Path arguments are made absolute against `SMA_HOST_PWD` and must exist.
  - Mount each one: a directory as itself, a file via its parent directory.
  - Add directories the config needs, read from `/opt/sma/config/sma-ng.yml` with the existing settings
    loader: output and temp directory, move-to/copy-to, recycle bin.
  - Add `/opt/sma/config`, logs and data, plus `extra-mounts` from client config and `--mount PATH`.
  - De-duplicate nested mounts, and refuse to mount `/`.
  - Run as `--user UID:GID -e HOME=/tmp`; add `--device /dev/dri --group-add GID…` and/or `--gpus all`.
  - Command: entrypoint `run python <tool> …`, so the worker has the daemon's identity and VA-API setup.
- **HTTP targets:**
  - Apply the target's longest matching path mapping, otherwise pass through.
  - Validate against the daemon's advertised path roots.
  - Submit, then poll job state and output: about 1 s interval, printing new output.
  - Ctrl-C detaches and prints the job id; `--no-wait` returns immediately.
  - Exit code follows the job result.
- **Output:** human-readable by default; `--json` for scripts.
- **Exit codes:** `0` ok, `1` job failed, `2` usage or path, `3` target unreachable, `4` auth,
  `5` plan or protocol error, `130` interrupted.
- **Client config** (`~/.config/sma/config.yml`, must be mode `0600` when it holds API keys):

  ```yaml
  default-target: local
  targets:
    local: {url: "http://127.0.0.1:8585"}
    nas:
      url: "http://nas.lan:8585"
      api-key: "..."
      mappings:
        - {local: /Volumes/Media, remote: /mnt/unionfs/Media}
  extra-mounts: [/srv/scratch]
  ```

### Daemon changes

- **Job kinds:** add `kind` (`convert` default, `repair`, `preview`, `audit`, `rename`) to the jobs table
  (SQLite and PostgreSQL migrations). The worker dispatches by kind. `preview` jobs get top priority.
- **Submission:** `POST /jobs` with `{kind, path, args, profile}` is the canonical endpoint.
  `POST /convert`, `/` and `/webhook/generic` remain aliases for `kind=convert`.
- **Output:** `GET /jobs/{id}/output?offset=N` returns captured tool output (repair report, preview plan,
  audit findings) incrementally. The existing `/jobs/{id}/ffmpeg-stderr` stays.
- **Path roots:** `GET /paths` returns the roots the daemon serves (from its service routing), for client
  validation. It requires auth.
- **Docs:** `docs/daemon.md` and `docs/openapi.yaml` for all new and changed endpoints.

### `sma daemon` lifecycle

- **Detection:** the bootstrap-supplied `docker inspect` shows `com.docker.compose.project`, the working
  directory and the config files.
  - Compose-managed: plans run `docker compose -p P --project-directory W -f F… <pull|up -d|down|logs>`.
  - Otherwise, plain container:
    1. `docker pull`;
    2. `docker rm -f sma-ng`;
    3. `docker run -d --name sma-ng --restart unless-stopped -p PORT:8585`, with
       `/opt/sma/{config,logs,data}`, the media roots from client config, and GPU flags.
- **Health:** a plan step re-invokes the client (`sma-client _wait-health URL`) instead of the bootstrap
  polling.
- **Clusters:** untouched; nodes are HTTP targets.

### Installer (`install-smang.sh`, repo root)

- Checks Docker, pulls the image (a release tag on arm64), installs `sma` from the image, writes
  `~/.config/sma/image`, and seeds `/opt/sma/config/sma-ng.yml` and `daemon.env` from raw GitHub URLs if
  missing.
- `--daemon` additionally runs `sma daemon up`. No Compose unless the user already has a compose-managed
  daemon.

### MVP Scope (Phase 1: throwaway runtime)

- [ ] Plan protocol spec plus the bootstrap rewrite, with fake-docker tests (the `tests/test_sma_host.py`
  style).
- [ ] `resources/cli/` client: argument parsing, host facts, throwaway plan builder, config-derived mounts.
- [ ] Throwaway `convert/repair/audit/preview/rename/codecs/shell`.
- [ ] `sma upgrade`, `sma version`.
- [ ] Installer rewrite: CLI-first, `--daemon` optional.
- [ ] Docs: `docs/sma-cli-quickstart.md`, `docs/docker-compose-quickstart.md`, README.

### Phase 2: daemon targets

- [ ] Daemon job kinds, `POST /jobs`, `GET /jobs/{id}/output`, `GET /paths`, DB migrations, OpenAPI.
- [ ] Client HTTP routing, targets, mappings, auth, progress, `--json`, `sma jobs …`.
- [ ] Auto-submit to the local daemon when reachable; `--local` override.

### Phase 3: daemon lifecycle

- [ ] `sma daemon up/down/restart/status/logs/upgrade` (adopt compose, or plain `docker run`).
- [ ] Retire `setup/install-docker-target.sh`, the alias shim and the identity-mount docs once replaced.

### Future Enhancements

- Server-sent events for progress instead of polling.
- Shell completion generated by the client.
- `sma doctor` (host facts, GPU access, target reachability, version skew).

## 5. Action Items & Next Steps

### Immediate Actions

- [ ] **Land the standalone fixes from the paused work** in separate commits:
  - `docker/.env.example` uses the variables compose actually reads;
  - remove the false "bundled PostgreSQL" / `PGSQL_*` claims from the docs.
- [ ] **Park the draft `install-smang.sh`:** it is daemon-first and will be rewritten in Phase 1.
- [ ] **Tighten workflow timeouts** (independent of this feature). Measured: CI test 2 min, CodeQL 2 min,
  image amd64 2–3 min, arm64 6.6 min cold. Proposed limits:
  - CI test 10, lint/checks 5, CodeQL 10;
  - image build 20, plan/merge 5, release jobs 10.
- [ ] **Write a PRP** from this document (`/bp:generate-prp`) and break it into tasks.

### Short-term Actions

- [ ] Phase 1 implementation and release; dogfood `sma repair` on the operator's host.
- [ ] Phase 2 daemon API design review (job kinds, output storage size limits, migrations).

## 6. Risks & Dependencies

### Technical Risks

- **Config-derived mounts miss a path** (post-process scripts, custom move targets): the job fails inside
  the container.
  - Impact: medium. Probability: medium.
  - Mitigation: the plan builder covers all path-typed settings in `config_schema.py` (a test enumerates
    path fields); `--mount` and `extra-mounts` as escape hatches; a clear error naming the unmounted path.
- **Rootless Docker / user namespaces:** `--user UID:GID` maps differently, and file ownership may surprise.
  - Impact: medium. Probability: low.
  - Mitigation: detect rootless (`docker info` security options) and skip `--user`; document it.
- **SELinux hosts** need `:z` on bind mounts.
  - Impact: medium. Probability: low.
  - Mitigation: detect via host facts and add the mount option.
- **Docker Desktop networking:** `--network host` support differs.
  - Impact: low. Probability: medium.
  - Mitigation: use `host.docker.internal` for local targets on macOS.
- **A throwaway run and the daemon compete** when the daemon is down or `--local` is used.
  - Impact: medium. Probability: low.
  - Mitigation: warn when `--local` is used while a daemon is reachable.
- **Daemon job kinds and migrations** across SQLite and PostgreSQL clusters.
  - Impact: high. Probability: medium.
  - Mitigation: an additive column with a default of `convert`; old nodes ignore non-convert kinds (the
    claim query filters by supported kinds); covered by migration tests.
- **Plan protocol injection or quoting bugs.**
  - Impact: high. Probability: low.
  - Mitigation: a line-based format; arguments are never shell-evaluated; the bootstrap only builds
    `"$@"`; fuzz tests with spaces, quotes, globs and leading dashes.
- **Version skew** between the bootstrap and the image.
  - Impact: low. Probability: medium.
  - Mitigation: the plan header carries a protocol version; the bootstrap refuses unknown versions and
    suggests `sma upgrade`.

### Dependencies

- The daemon job-kind work (Phase 2) blocks submitting non-convert commands to daemons.
- Release images must exist for arm64 installs. The first one is 3.2.21, built on native runners.

## 7. Resources & References

### Codebase References

- `setup/sma-host.sh`: the current `docker exec` host command, to be replaced by the bootstrap.
- `docker/docker-entrypoint.sh`: `run` mode (privilege drop, supplementary groups, VA-API environment).
- `resources/daemon/routes.py`: GET/POST route tables (`/convert`, `/jobs/…`, `/health`).
- `resources/daemon/handler.py`: auth (`X-API-Key`, Bearer, Basic), job handlers.
- `resources/daemon/db.py`: `add_job(path, config, args, …)` for the SQLite and PostgreSQL backends.
- `resources/config_schema.py`, `resources/readsettings.py`: path-typed settings for derived mounts.
- `tests/test_sma_host.py`, `tests/test_install_smang.py`: fake-docker test pattern.

### Related Work This Session

- `c8d2a58`: native-runner multi-arch image build (makes arm64 release images possible).
- `5f5f5a7`: Docker smoke test fix.
- Uncommitted draft: `install-smang.sh` (daemon-first), docs rewrite of the compose quickstart.

## 8. Session Notes & Insights

### Key Insights Discovered

- The image is the product; the host CLI is a launcher plus HTTP client.
- Mounting exactly what the user names removes the identity-mount restriction and most of the Docker docs.
- Treating every daemon as an HTTP target removes the local/remote distinction.
- Keeping decisions inside the image (plan-then-execute) gives a dependency-free host with all logic in
  tested Python.

### Questions Raised (For Future Investigation)

- How long should job output be retained in the database, and with what size cap?
- Should `preview` on a daemon target bypass the queue entirely (synchronous endpoint)?
- Should the installer also offer a per-user install (`~/.local/bin/sma`) without sudo?
- Can `sma daemon up` on a plain host import an existing compose `.env` for a smooth migration?
