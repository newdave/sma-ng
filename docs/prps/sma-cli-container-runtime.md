# PRP: `sma` CLI as a Container Runtime and Daemon Client

STATUS: COMPLETE

Source design: [`docs/brainstorming/2026-10-28-sma-cli-container-runtime.md`](../brainstorming/2026-10-28-sma-cli-container-runtime.md).
Task breakdown: [`docs/tasks/sma-cli-container-runtime.md`](../tasks/sma-cli-container-runtime.md).

## Discovery Summary

### Initial Task Analysis

The operator wants a local `sma` command that drives SMA-NG without a source checkout.

- With no daemon, it starts a short-lived container with access to exactly the paths named, runs the tool, and
  removes the container.
- With a daemon, local or remote, it talks to it over HTTP.

Today `setup/sma-host.sh` only `docker exec`s into a running compose daemon and rejects paths outside its identity
mounts. Installation assumes a checkout.

### User Clarifications Received

Brainstorming session (2026-10-28):

- **Q:** Where does `sma` run? **A:** On the Docker host, and as a client of remote daemons.
- **Q:** Remote paths? **A:** Translate via per-target prefix mappings when one matches, otherwise pass through.
- **Q:** Daemon running and a command is typed? **A:** Submit to the daemon by default; `--local` forces a
  throwaway container.
- **Q:** CLI form? **A:** A POSIX shell bootstrap on the host; all logic in Python inside the image.
- **Q:** How does the in-image client drive Docker? **A:** Plan, then execute. The client writes a plan and the
  bootstrap runs it. No Docker socket goes into any container.
- **Q:** `sma daemon` vs Compose? **A:** Detect and adopt. A compose-managed daemon is driven through compose;
  otherwise a plain `docker run -d`. Clusters keep their existing deploy tooling.
- **Q:** Scope? **A:** Daemon interaction is HTTP only, so local and remote targets share one code path. Remote is
  a Must. No `--bg` mode.

PRP preflight:

- **Q:** Drop the legacy `sma manual` / `smoke`, `SMA_CONTAINER` / `SMA_COMPOSE_*`, and exit 3 = "no container"?
  - **A:** Drop them.
  - **Impact:** Clean replacement of `setup/sma-host.sh`. `sma logs` becomes an alias of `sma daemon logs`, and
    exit 3 means "target unreachable".
- **Q:** What does `sma daemon up` mount on a plain (non-compose) host?
  - **A:** Explicit roots only.
  - **Impact:** It needs `--media PATH` (repeatable) or `daemon.media-roots` in client config, and exits 2
    otherwise. The roots are remembered.
- **Q:** Job output retention?
  - **A:** 1 MiB per job with a truncation marker, purged with the job.
  - **Impact:** Mirrors the `ffmpeg_stderr` cap.

### Missing Requirements Identified

Found in research and resolved in this PRP:

- **Client containers cannot see host paths.** The bootstrap records each argument's type on the host before the
  client runs (the `argstat` file). Config-derived directories are mounted only if they exist on the host (the
  `optmount` plan line). Plain `-v SRC:DST` auto-creates missing sources as root-owned directories, so every
  mount source must be checked first.
- **The image has no `/app/config`.** Tools default to `/app/config/sma-ng.yml`, and the daemon passes
  `--daemon-config /config/sma-ng.yml`. Throwaway runs mount the host config directory at `/app/config:ro`.
  Passing `-c` instead would disable `repair.py` routing (`-c` turns routing off).
- **Job dedupe is by path only** (`add_job`, `find_active_job`, both backends). It must include `kind`.
- **Convert-only logic in shared paths must be skipped for non-convert kinds:**
  - `_merge_args` default args;
  - the same-extension skip;
  - `_preflight_disk_pressure`;
  - per-file directory expansion;
  - `input_size` / `output_size`.
- **Exit semantics differ per tool.** `manual.py --audit` exits non-zero when it finds problems, which is a
  successful audit.
- **Mixed-version clusters.** Old nodes would claim non-convert rows and run them as conversions. A capability
  column on `cluster_nodes` gates submission.
- **`GET /jobs` returns `SELECT *`.** The new `output` column must be excluded from list and detail responses.
- **The `/jobs/` GET prefix dispatcher drops `query`.** `?offset=` needs it.
- **`repair.py` is not in the Dockerfile shebang rewrite.** Always invoke tools as `python <tool>.py`.
- **`sma-ng` / `sma-ng-daemon` console scripts only work from `/app`.** The client runs as
  `python -m resources.cli` with `--workdir /app`, never as a console script.

## Goal

Ship a single `sma` command plus a curl installer such that:

1. `curl -fsSL https://raw.githubusercontent.com/newdave/sma-ng/main/install-smang.sh | sh` gives a working `sma`
   with only Docker installed. There is no checkout, no Compose, no mise, and no host Python.
2. `sma repair|convert|audit|preview|rename PATH` runs in a throwaway container when no daemon is reachable:
   - it mounts exactly the named paths plus the directories the config needs;
   - it runs as the caller's uid/gid with GPU passthrough;
   - it exits with the tool's status;
   - it leaves nothing behind.
3. The same commands are submitted over HTTP to a daemon when one is reachable, or to an explicit remote target.
   Progress and output are streamed, and the exit status follows the job.
4. `sma daemon up|down|restart|status|logs|upgrade` manages the long-lived daemon, through compose if it is
   compose-managed, otherwise with plain `docker run -d`.
5. `sma upgrade` moves the bootstrap and the image together.

## Why

- Using SMA-NG today needs a checkout or a hand-built compose stack. The "image is the product" model removes
  both.
- Identity-mount rules ("path must be under /mnt") disappear, because the container mounts what the user names.
- A single HTTP path for local and remote daemons lets an operator drive a NAS or cluster node from a laptop.
- Submitting to a running daemon avoids two transcoders contending for the GPU, files and database.
- It replaces `setup/sma-host.sh`, `setup/install-docker-target.sh`, the alias shim, and most Docker install docs.

## What

### Phases

| Phase | Scope |
|-------|-------|
| 1 | Bootstrap + plan protocol, `resources/cli/` client, throwaway commands, `sma upgrade`/`version`, installer, docs |
| 2 | Daemon job kinds, `POST /jobs`, `GET /jobs/{id}/output`, `GET /paths`, capability gate; client HTTP routing, targets, mappings, auth, progress, `--json`, `sma jobs` |
| 3 | `sma daemon up/down/restart/status/logs/upgrade` (adopt compose or plain), retire legacy installer/shim/docs |

Each phase is independently releasable. Phase 1 alone must be useful: a throwaway runtime with no daemon
features.

### Command surface (final)

```text
sma convert PATH... [TOOL ARGS]     # manual.py -a -i PATH
sma preview PATH [TOOL ARGS]        # manual.py -a -i PATH -oo
sma repair PATH... [-n] [ARGS]      # repair.py PATH...
sma audit PATH [ARGS]               # manual.py --audit -i PATH
sma rename PATH [ARGS]              # rename.py PATH
sma codecs                          # manual.py -cl (throwaway only)
sma shell                           # bash in a throwaway container, cwd mounted (throwaway only)
sma jobs [list|show ID|logs ID|cancel ID]       # Phase 2
sma daemon up|down|restart|status|logs|upgrade  # Phase 3 (status works in Phase 2)
sma logs                            # alias: sma daemon logs
sma target add NAME URL [--api-key-stdin] | list | use NAME | rm NAME   # Phase 2
sma map add TARGET LOCAL REMOTE | list [TARGET] | rm TARGET LOCAL       # Phase 2
sma upgrade [TAG]                   # bootstrap-native
sma version | help

Global flags: --target NAME, --local, --no-wait, --json, --mount PATH (repeatable), -q/--quiet
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Job/tool failed (tool's own non-zero code is passed through for throwaway runs) |
| 2 | Usage error, bad path, path not under target roots |
| 3 | Target unreachable (explicit target, or `--target`) |
| 4 | Authentication failed (401 from daemon) |
| 5 | Plan/protocol error (version skew → suggests `sma upgrade`) |
| 130 | Interrupted (Ctrl-C while waiting on a daemon job detaches; prints job id) |

### Success Criteria

- [x] `install-smang.sh` installs `sma` with only Docker present. It is idempotent, needs no checkout, works when
  piped to `sh`, and picks a release tag on arm64.
- [x] `sma repair /any/dir` with no daemon:
  - [x] runs `repair.py /any/dir` in a container with `/any/dir` mounted at the same path;
  - [x] runs as the caller's uid/gid with `--rm`;
  - [x] passes through the tool's exit code;
  - [x] leaves no container.
- [x] A missing input path gives exit 2 with a one-line error, and no Docker mount auto-creates directories.
- [x] Config-derived directories (output-directory, move-to, copy-to, recycle-bin) are mounted only if they exist
  on the host.
- [x] GPU passthrough:
  - [x] `/dev/dri` gets `--device /dev/dri` plus `--group-add` for each device GID;
  - [x] a working `nvidia-smi` gives `--gpus all`;
  - [x] rootless Docker skips `--user`;
  - [x] SELinux enforcing adds `:z`.
- [x] With a reachable daemon, the command is submitted to `POST /jobs` with the right `kind`. Output is streamed
  from `GET /jobs/{id}/output`, and the exit code follows the job status.
- [x] Remote target paths go through the longest matching prefix mapping and are validated against
  `GET /paths`.
- [x] The daemon runs `repair`, `preview`, `audit` and `rename` jobs with per-kind argv and exit semantics:
  - [x] it stores the last 1 MiB of output per job;
  - [x] dedupe is per path + kind;
  - [x] a PostgreSQL cluster refuses non-convert kinds with 409 while any online node lacks the `job-kinds-v1`
    capability.
- [x] `sma daemon up/down/status/logs/upgrade` works for compose-managed and plain daemons. Plain `up` without
  media roots exits 2.
- [x] `sma upgrade` replaces the bootstrap from the image and rewrites the pinned image. A plan-version mismatch
  gives exit 5 with an upgrade hint.
- [x] All tests pass, coverage gates hold (global ≥90%, every module ≥100 statements ≥70%), ShellCheck is clean,
  and docs and OpenAPI are updated.

## All Needed Context

### Research Phase Summary

- **Codebase patterns found:**
  - `setup/sma-host.sh` and `tests/test_sma_host.py` (POSIX launcher plus a fake-docker test harness);
  - `docker/docker-entrypoint.sh` `run` mode (privilege drop, groups, VA-API);
  - `resources/daemon/worker.py` (subprocess job runner, watchdog);
  - `resources/daemon/db.py` (twin SQLite/PostgreSQL backends with additive migrations);
  - `resources/daemon/routes.py` + `handler.py` (route tables, auth, webhook queueing);
  - `/jobs/{id}/ffmpeg-stderr` (capped text output pattern);
  - `_get_browse` (valid roots).
- **External research needed:** No. Everything uses the Docker CLI, POSIX `sh` and the Python stdlib
  (`urllib.request`, `json`, `argparse`). Behaviours relied on:
  - `docker run --rm`, `--user`, `--group-add`, `--device`, `--gpus all`, `--label`, `-w`;
  - `-v SRC:DST` auto-creates a missing SRC, which is why every SRC is pre-checked;
  - `docker inspect` compose labels: `com.docker.compose.project`, `com.docker.compose.project.working_dir`,
    `com.docker.compose.project.config_files`, `com.docker.compose.service`;
  - rootless detection: `docker info --format '{{.SecurityOptions}}'` contains `name=rootless`;
  - SELinux: `docker info --format '{{.SecurityOptions}}'` contains `name=selinux`.
- **Knowledge gaps:** none blocking. Python dependencies: the client must use only the stdlib plus modules
  already in the image (`ruamel.yaml`, `pydantic` via `ConfigLoader`). Do not add `requests` usage to the client.

### Documentation & References

```yaml
- file: setup/sma-host.sh
  why: Current launcher being replaced. Reuse its TTY-flag logic (run_in), normalize()/abs_path() helpers,
       @SMA_GIT_SHA@ placeholder (SMA_HOST_VERSION), POSIX style. Exit codes change per this PRP.
- file: tests/test_sma_host.py
  why: Fake-docker harness (FAKE_DOCKER script logging "$*" to $FAKE_LOG, env fixture, _run/_calls helpers).
       Extend the fake to emulate the client container by writing $FAKE_PLAN into the mounted /plan dir.
- file: tests/test_install_smang.py, install-smang.sh (untracked draft)
  why: Fake curl/docker installer tests, DESTDIR/SUDO test hooks, `sh -s --` piped-run test. The draft is
       daemon-first; rewrite to CLI-first per Task 7 but keep the harness.
- file: docker/docker-entrypoint.sh
  why: `entrypoint.sh run CMD...` (lines ~56-64) - set_runtime_env + drop_privileges; with a non-root --user it
       just execs. No seeding in run mode, so config must be mounted.
- file: docker/Dockerfile
  why: Lines ~304-317: ARG/ENV SMA_GIT_SHA and the sma-host install + sed substitution. WORKDIR /app, PATH has
       /venv/bin, ENTRYPOINT tini -> entrypoint.sh, CMD daemon.py --daemon-config /config/sma-ng.yml --logs-dir /logs.
       There is NO /app/config in the image.
- file: resources/daemon/worker.py
  why: process_job (~394) pre-checks/outcome mapping; _run_conversion_inner (~674) argv at ~695
       `[sys.executable, self.script_path, "-a", "-i", path, "-c", config_file, "--job-id", str(job_id)] + extra_args`,
       Popen stdout+stderr merged (~708), line parsing (~730-765, _SMA_FINAL_OUTPUT_RE, _FFMPEG_PROGRESS_RE),
       _JobWatchdog (~109) and kill_process_tree (~62), _ingest_ffmpeg_stderr_sidecars (~621),
       _preflight_disk_pressure (~470/577).
- file: resources/daemon/db.py
  why: Twin backends, no shared base. SQLite jobs DDL (~416) + PRAGMA-guarded ALTERs (~457-465);
       _METRICS_EXPANSION_COLUMNS tuple pattern (~82); PG DDL (~1045) + ADD COLUMN IF NOT EXISTS (~1129-1195);
       add_job (512/1298) path-only dedupe; find_active_job (530/1317); claim_next_job (535/1325);
       update_job_ffmpeg_stderr (634/1498) with _FFMPEG_STDERR_MAX_BYTES tail cap (70); cleanup_old_jobs (749/1839);
       heartbeat (766/1870) and cluster_nodes ALTERs (~1144).
- file: resources/daemon/routes.py
  why: _get_routes / _post_routes exact tables, prefix tables, _dispatch_get_job (/ffmpeg-stderr suffix pattern),
       dispatch_get/dispatch_post run check_auth first. Prefix lambda drops `query`.
- file: resources/daemon/handler.py
  why: PUBLIC_ENDPOINTS (115), check_auth (141, X-API-Key / Bearer / Basic, hmac.compare_digest),
       _get_job (354), _get_job_ffmpeg_stderr (367-397, text/plain response pattern), _get_browse (399-456, roots),
       _queue_directory (1383), _queue_file (1456), _dispatch_path (1608), _handle_webhook (1662),
       _parse_webhook_body (1256), _BadRequest + _guarded (228).
- file: resources/daemon/webhook_parsing.py
  why: parse_generic_webhook_body - JSON path/args/config/max_retries parsing; add parse_jobs_body beside it.
- file: resources/daemon/config.py
  why: PathConfigManager (269): routing_match_paths (696), scan_paths, audit_paths, path_rewrites,
       rewrite_path, is_recycle_bin_path, get_profile_for_path, default_config.
- file: resources/readsettings.py, resources/config_loader.py, resources/config_schema.py
  why: ConfigLoader().load(path) -> SmaConfig (raises ConfigError; ReadSettings sys.exit()s - avoid it in the client).
       Path-typed converter fields: output_directory, move_to, copy_to (list), recycle_bin (config_schema ~109-125);
       analyzer model_dir/cache_dir (~498).
- file: repair.py, rename.py, manual.py
  why: repair.py paths(+) -n -c --no-plex -v, exit 0/1, -c disables routing. rename.py path --dry-run etc., exit 0/1.
       manual.py -a -i -oo -cl --audit (non-zero rc when findings; 2 usage), -c, --job-id, exit 75 = no output space.
- file: tests/test_handler.py
  why: _make_server/_make_handler/_get_response_body helpers; copy TestGetJobFfmpegStderr, TestHandleWebhook,
       TestQueueFile, TestDoGetRouting, TestDoPostRouting, TestGetBrowse, TestCheckAuth.
- file: tests/test_worker.py, tests/test_worker_reliability.py
  why: _make_worker, _make_fake_process(stdout_lines, returncode) + mock.patch("subprocess.Popen");
       reliability tests replace worker.script_path with a real fake script.
- file: tests/test_sqlite_db.py (~70-185), tests/test_daemon.py (~926+, _make_fake_psycopg2 / _make_db_with_mock_pool)
  why: Legacy-DB upgrade test pattern; PostgreSQL is mocked (assert executed SQL). Real PG only with TEST_DB_URL.
- file: docs/openapi.yaml
  why: components.schemas.Job (~29), /jobs/{id}/ffmpeg-stderr (~392) text/plain pattern. /convert, / and
       /webhook/generic are undocumented - document them alongside POST /jobs.
- docfile: docs/daemon.md (## API Endpoints ~59, ffmpeg-stderr section ~346), docs/sma-cli-quickstart.md,
           docs/docker-compose-quickstart.md, docs/deployment.md, README.md
  why: Must be updated in the same commits as the behaviour (CLAUDE.md doc rule).
- docfile: /tmp/sma-phase1-drafts/ (if still present)
  why: Prior installer-first doc drafts (compose quickstart, CLI quickstart); reuse wording, but they predate
       this design (daemon-first) - do not copy their install flow.
```

### Current codebase tree (relevant parts)

```bash
setup/sma-host.sh                 # docker-exec launcher (to be replaced)
setup/install-docker-target.sh    # checkout-based installer (retire in Phase 3)
setup/sma-ng-docker-aliases.sh    # deprecated alias shim (retire in Phase 3)
docker/Dockerfile, docker/docker-entrypoint.sh, docker/docker-compose.yml
resources/daemon/{handler,routes,worker,db,config,webhook_parsing}.py
resources/{readsettings,config_loader,config_schema,media_repair}.py
manual.py repair.py rename.py daemon.py
tests/test_sma_host.py tests/test_handler.py tests/test_worker.py tests/test_sqlite_db.py tests/test_daemon.py
install-smang.sh tests/test_install_smang.py   # untracked daemon-first draft
```

### Desired codebase tree

```bash
install-smang.sh                  # CLI-first curl installer (rewritten draft)
setup/sma-host.sh                 # bootstrap: host facts, argstat, run client, execute plan, `upgrade`
resources/cli/__init__.py         # main(argv) entry; `python -m resources.cli`
resources/cli/__main__.py         # calls main()
resources/cli/hostfacts.py        # parse SMA_HOST_* env + /plan/argstat + /plan/daemon.json into HostFacts
resources/cli/clientconfig.py     # ~/.config/sma/config.yml load/save (0600 check), targets, mappings, media roots
resources/cli/plan.py             # Plan/Step model + v1 serializer (sma-plan 1 / step / arg / optmount / end)
resources/cli/throwaway.py        # command -> worker container plan (mounts, user, GPU, tool argv)
resources/cli/mounts.py           # mount set: arg paths (argstat), config-derived dirs (optmount), dedupe, refusals
resources/cli/http.py             # urllib client: health probe, POST /jobs, poll job + output, auth errors
resources/cli/routing.py          # target resolution, path mapping, local-vs-daemon decision
resources/cli/daemonctl.py        # Phase 3: daemon up/down/... plans (compose adopt vs plain)
resources/cli/commands.py         # argparse surface, dispatch, exit codes, --json output
resources/daemon/job_kinds.py     # Phase 2: kind registry: argv builder, exit-code semantics, flags per kind
tests/test_cli_*.py               # one test module per cli module
tests/test_job_kinds.py           # kind registry tests
```

### Known gotchas of our codebase and library quirks

```python
# CRITICAL: Python in this repo uses 2-space indentation, %-formatting in log calls, single-line log records.
# CRITICAL: No print() in resources/daemon/ (tests/test_lint_logging.py). resources/cli/ is a human CLI and may
#           write to stdout/stderr, but MUST NEVER print or log API keys/tokens.
# CRITICAL: Shell must be POSIX sh + ShellCheck clean (`mise run test:shell`); no inline Python in shell.
# CRITICAL: Both DB backends must get every schema/method change (no shared base class). PG tests are mocked;
#           assert on the executed SQL strings.
# CRITICAL: `docker run -v SRC:DST` CREATES a missing SRC as a root-owned directory. Every SRC must be a path the
#           bootstrap verified on the host (argstat for args, optmount for config dirs). Reject ':' in mount paths.
# CRITICAL: The client container cannot see host paths or the host Docker. It only sees: /plan (rw),
#           /home-config (= ~/.config/sma, rw), /app/config (= $SMA_HOME/config, ro) and SMA_HOST_* env.
# CRITICAL: No /app/config exists in the image. Throwaway workers mount $SMA_HOME/config at /app/config:ro so
#           tool defaults resolve; do NOT pass -c (repair.py -c disables routing).
# CRITICAL: Tools must be invoked as `python <tool>.py` (repair.py lacks the shebang rewrite); workdir /app.
# CRITICAL: Run the client as `--entrypoint python IMAGE -m resources.cli` with -w /app; console scripts built
#           from pyproject only work from /app and resources.cli is not in [tool.setuptools] packages.
# CRITICAL: LOGS_DIR is /app/logs (SCRIPT_DIR/logs). With --user UID:GID the worker may not be able to write
#           /app/logs: mount $SMA_HOME/logs at /app/logs if it is a directory writable by the caller (bootstrap
#           reports this in SMA_HOST_LOGS_WRITABLE), else `--tmpfs /app/logs`.
# GOTCHA: manual.py prompts interactively without -a/-i; always pass both. Exit 75 = insufficient output space.
# GOTCHA: manual.py --audit returns non-zero when findings exist: treat rc 1 as success-with-findings (kind=audit).
# GOTCHA: /health is public; auth errors only surface on the first authenticated call (POST /jobs, GET /paths).
# GOTCHA: get_job is SELECT * - pop "output"/"output_dropped" before serialising job dicts in GET /jobs and
#         GET /jobs/{id}.
# GOTCHA: _dispatch_get_job is called from a prefix lambda that drops `query`; thread query through.
# GOTCHA: POST "/jobs" exact route coexists with prefix "/jobs/"; exact routes are matched first.
# GOTCHA: Docker Desktop (macOS) has no host networking by default: the bootstrap reports SMA_HOST_OS=Darwin and
#         the client rewrites 127.0.0.1/localhost target URLs to host.docker.internal; on Linux the client
#         container runs with --network host.
# GOTCHA: CLAUDE.md memory: routing may be rebuilt from service instances at deploy time; GET /paths must include
#         routing_match_paths(), scan_paths[].path, audit_paths[].path and path_rewrites[].from.
```

## Implementation Blueprint

### Data models and structure

```python
# resources/cli/hostfacts.py
@dataclass(frozen=True)
class ArgStat:
  index: int          # position in the original argv passed to sma
  kind: str           # "d" directory, "f" file/other existing, "-" missing
  abspath: str        # lexically absolutised against the host PWD (bootstrap)

@dataclass(frozen=True)
class HostFacts:
  pwd: str; uid: int; gid: int; os: str            # SMA_HOST_PWD/UID/GID/OS ("Linux"/"Darwin")
  home: str                                        # SMA_HOST_HOME (host $HOME, for messages)
  sma_home: str                                    # SMA_HOME (default /opt/sma)
  image: str                                       # SMA_IMAGE (pinned ref the bootstrap used)
  bootstrap_version: str                           # SMA_HOST_VERSION (git sha or "dev")
  tty: bool                                        # SMA_HOST_TTY=1 when stdin+stdout are TTYs
  dri_gids: tuple[int, ...]                        # SMA_HOST_DRI_GIDS "44,993" ("" = no /dev/dri)
  nvidia: bool                                     # SMA_HOST_NVIDIA=1
  rootless: bool; selinux: bool                    # SMA_HOST_ROOTLESS / SMA_HOST_SELINUX
  logs_writable: bool                              # SMA_HOST_LOGS_WRITABLE
  argstat: tuple[ArgStat, ...]                     # /plan/argstat
  daemon_container: dict | None                    # /plan/daemon.json (docker inspect sma-ng [0]) or None

# resources/cli/plan.py  - protocol v1 (line based, UTF-8, LF; args may not contain "\n")
#   sma-plan 1
#   step docker            # allowed docker subcommands: run pull rm stop logs compose exec create cp inspect
#   arg run
#   arg --rm
#   optmount /transcode    # bootstrap appends `-v /transcode:/transcode[:z]` only if [ -d /transcode ]
#   arg ghcr.io/newdave/sma-ng:3.2.22
#   end
# The bootstrap runs every step in order (non-zero aborts with that code) and `exec`s the final one.
@dataclass
class Step:
  argv: list[str]                 # docker argv after "docker"
  optmounts: list[str]            # inserted (as -v pairs) where the optmount lines appear -> keep order: store
                                  # items as ("arg", value) | ("optmount", path) tuples instead
@dataclass
class Plan:
  steps: list[Step]
  def serialize(self) -> str: ...

# resources/cli/clientconfig.py  (~/.config/sma/config.yml, mode 0600 required if any api-key present)
# default-target: local
# targets:
#   local: {url: "http://127.0.0.1:8585"}
#   nas: {url: "http://nas.lan:8585", api-key: "...", mappings: [{local: /Volumes/Media, remote: /mnt/unionfs/Media}]}
# extra-mounts: [/srv/scratch]
# daemon: {name: sma-ng, port: 8585, media-roots: [/mnt/media]}

# Daemon (Phase 2) - jobs table additions (both backends):
#   kind TEXT DEFAULT 'convert'      output TEXT      output_dropped INTEGER DEFAULT 0
# cluster_nodes addition: capabilities TEXT   (comma list; this release writes "job-kinds-v1")

# resources/daemon/job_kinds.py
@dataclass(frozen=True)
class JobKind:
  name: str                       # convert | repair | preview | audit | rename
  script: str                     # manual.py | repair.py | rename.py
  directory_ok: bool              # repair/audit/rename True; preview False; convert expands per file (existing path)
  success_codes: frozenset[int]   # convert/preview/repair/rename {0}; audit {0, 1}
  forbidden_args: frozenset[str]  # {"-c","--config","-i","--input","--job-id"} for all kinds
  def argv(self, python, script_dir, path, config, job_id, args) -> list[str]: ...
#   convert: [py, manual.py, "-a", "-i", path, "-c", config, "--job-id", id] + args   (unchanged)
#   preview: [py, manual.py, "-a", "-i", path, "-c", config, "-oo"] + args
#   audit:   [py, manual.py, "--audit", "-i", path, "-c", config] + args
#   repair:  [py, repair.py, path] + args            # no -c: keeps routing; daemon cwd/app config resolution
#   rename:  [py, rename.py, path] + args
```

API shapes (Phase 2):

```text
POST /jobs   {"kind":"repair","path":"/mnt/tv/Show","args":["-n"],"profile":null}
  202 {"status":"queued","job_id":12,"kind":"repair","path":"/mnt/tv/Show"}
  200 {"status":"duplicate","job_id":9,"kind":"repair","path":"..."}
  convert of a directory -> existing _queue_directory response shape (202 with queued/duplicates/skipped)
  400 {"error": "..."}  unknown kind | forbidden arg | path does not exist | directory not allowed for kind
  409 {"error":"cluster node <name> does not support job kinds; upgrade all nodes","node":"..."}  (PG only)
GET /jobs/{id}/output?offset=N   text/plain; headers X-Next-Offset: <abs byte offset>, X-Output-Dropped: <bytes>
  offset < dropped -> body starts at the oldest retained byte; 404 unknown job; 400 bad id/offset
GET /paths   {"roots": ["/mnt/unionfs/Media", ...]}   (auth required)
GET /jobs/{id}   unchanged shape + "kind"; never includes output/output_dropped
```

### List of tasks in order

```yaml
# ---------------- Phase 1: throwaway runtime ----------------
Task 1 CREATE resources/cli/plan.py + tests/test_cli_plan.py:
  - Step/Plan model storing ordered ("arg"|"optmount", value) items; serialize() -> protocol v1 text.
  - Reject values containing "\n" or "\0" (raise PlanError -> exit 5). Allowed step subcommands only.
  - Tests: round-trip text, spaces/quotes/globs/leading dashes preserved verbatim, newline rejection.

Task 2 CREATE resources/cli/hostfacts.py + tests/test_cli_hostfacts.py:
  - Parse env + /plan/argstat ("<index>\t<d|f|->\t<abspath>" per line) + optional /plan/daemon.json.
  - Missing/malformed facts -> PlanError (exit 5) naming the variable.

Task 3 CREATE resources/cli/mounts.py + tests/test_cli_mounts.py:
  - required mounts from path args: kind "d" -> the dir; "f" -> its parent dir; "-" -> UsageError exit 2
    "sma: <arg>: no such file or directory". Refuse "/" and any path containing ":".
  - config-derived optional mounts: load $SMA_HOME/config/sma-ng.yml via ConfigLoader().load (catch ConfigError ->
    exit 2 with the message); collect converter.output_directory, move_to, copy_to[], recycle_bin, analyzer
    model_dir/cache_dir when absolute; plus clientconfig extra-mounts and --mount PATH (both optional mounts).
  - dedupe: drop exact duplicates and any path nested under another mounted path (required wins).
  - Test enumerating path-like ConverterConfig fields (names matching dir|directory|path|move|copy|recycle) so a
    new path field fails the test until added to the collector or an explicit ignore list.

Task 4 CREATE resources/cli/throwaway.py + tests/test_cli_throwaway.py:
  - build_plan(command, tool_args, facts, cfg) -> Plan with one final `docker run` step:
    run --rm [-it|-i] --label sma.throwaway=1 -w /app
        [--user UID:GID -e HOME=/tmp]           (omit when facts.rootless or uid == 0)
        -v $SMA_HOME/config:/app/config:ro      (only if facts say it exists: pass via argstat-like SMA_HOST_CONFIG=1)
        (-v $SMA_HOME/logs:/app/logs | --tmpfs /app/logs)
        -v P:P[:z] for required mounts; optmount lines for optional mounts
        [--device /dev/dri --group-add G ...] [--gpus all]
        IMAGE run python <tool>.py <tool argv>
  - command -> tool argv: convert: manual.py -a -i P (one docker step per extra PATH, final exec'd);
    preview: manual.py -a -i P -oo; audit: manual.py --audit -i P; repair: repair.py P...; rename: rename.py P;
    codecs: manual.py -cl; shell: bash (mount facts.pwd, -w facts.pwd).
  - -i only when stdin is piped/tty per facts; -t only when facts.tty.

Task 5 CREATE resources/cli/commands.py, __init__.py, __main__.py + tests/test_cli_commands.py:
  - argparse surface (Phase 1 subset: convert preview repair audit rename codecs shell version help, --local,
    --mount, -q). Unknown/Phase-2 commands -> exit 2 "not available in this version".
  - main(): build facts, decide (Phase 1: always throwaway), write /plan/plan, return exit code.
  - Error classes -> exit codes: UsageError 2, Unreachable 3, AuthError 4, PlanError 5.

Task 6 REWRITE setup/sma-host.sh (bootstrap) + REWRITE tests/test_sma_host.py:
  - Reads image from ${SMA_IMAGE:-$(cat ~/.config/sma/image)} (fallback ghcr.io/newdave/sma-ng:latest),
    SMA_HOME (default /opt/sma), DOCKER_BIN.
  - mktemp -d plan dir (chmod 700); write argstat for every arg (case /*) or $PWD/arg; [ -d ] d, [ -e ] f, else -);
    `docker inspect sma-ng > daemon.json` when it exists; compute DRI GIDs via `ls -ln /dev/dri` (awk $4, sort -u),
    nvidia via `nvidia-smi -L`, rootless/selinux via `docker info --format '{{.SecurityOptions}}'`,
    logs writability via [ -d "$SMA_HOME/logs" ] && [ -w ... ].
  - Run client: docker run --rm [-it|-i] [--network host on Linux] [--user UID:GID unless rootless] -e HOME=/tmp
      -v "$plan:/plan" -v "$HOME/.config/sma:/home-config" [-v "$SMA_HOME/config:/app/config:ro"]
      -e SMA_HOST_* ... --entrypoint python -w /app "$IMAGE" -m resources.cli "$@"
    Client exit != 0 -> rm plan dir, exit with that code.
  - Parse plan: first line must be "sma-plan 1" else exit 5 "plan version X unsupported; run sma upgrade".
    Build each step with `set --` appends (never eval); optmount P -> append -v "P:P" (+":z" if selinux) only if
    [ -d P ]; allow-listed docker subcommands only. Read the whole plan into steps before running anything,
    rm the plan dir, run steps 1..n-1, `exec` step n.
  - `sma upgrade [TAG]` native: pull IMAGE (or repo:TAG), `docker create` + `docker cp :/usr/local/bin/sma-host`
    + `docker rm`, replace "$0" (if not writable print `sudo sma upgrade` hint, exit 2), rewrite ~/.config/sma/image.
  - `sma version` prints bootstrap version then the client's (client handles `version`).
  - Tests: extend FAKE_DOCKER so `run ... -m resources.cli` writes $FAKE_PLAN into the -v <dir>:/plan dir and exits
    $FAKE_CLIENT_RC; assert argstat contents, executed steps, optmount presence/absence, exec of last step,
    exit-code passthrough, version mismatch -> 5, no eval (args with spaces/$()/;/* survive verbatim).

Task 7 REWRITE install-smang.sh + tests/test_install_smang.py (CLI-first):
  - Checks docker + `docker info`; resolves tag (arm64 -> latest release via GitHub API; else latest; --tag/SMA_IMAGE_TAG);
    `docker pull`; installs sma from the image via docker create/cp/rm to /usr/local/bin (sudo) or ~/.local/bin
    fallback (warn if not on PATH); writes ~/.config/sma/image for the invoking user (SUDO_USER aware, chown);
    creates $SMA_HOME/{config,logs,data} owned by the invoking user; seeds $SMA_HOME/config/sma-ng.yml from the
    image (`docker run --rm --entrypoint cat IMAGE /app/setup/sma-ng.yml.sample`) if missing.
  - `--daemon [--media PATH]...` runs `sma daemon up ...` (Phase 3; before Phase 3 prints "not yet available").
  - No compose, no daemon.env, no raw downloads besides the installer itself.

Task 8 UPDATE docker/Dockerfile only if needed (resources/cli ships via COPY . .; verify) and the smoke test:
  - .mise/tasks/build/smoke: add `docker run --rm --entrypoint python "$TAG" -m resources.cli version`.
  - scripts/docker_smoke_imports.py: import resources.cli.

Task 9 DOCS Phase 1 (same commits as the behaviour):
  - docs/sma-cli-quickstart.md rewrite (install, throwaway model, mounts, GPU, exit codes, upgrade, troubleshooting);
    docs/docker-compose-quickstart.md install section -> installer; README quick start; docs/deployment.md mention.
  - Remove identity-mount wording that no longer applies to throwaway runs.

# ---------------- Phase 2: daemon targets ----------------
Task 10 CREATE resources/daemon/job_kinds.py + tests/test_job_kinds.py (registry per data model above).

Task 11 MODIFY resources/daemon/db.py (both backends) + tests:
  - New tuple _JOB_KIND_COLUMNS = (("kind","TEXT DEFAULT 'convert'","TEXT DEFAULT 'convert'"),
    ("output","TEXT","TEXT"), ("output_dropped","INTEGER DEFAULT 0","INTEGER DEFAULT 0")) migrated like
    _METRICS_EXPANSION_COLUMNS (keep that tuple untouched).
  - add_job(..., kind="convert", priority=None): dedupe + find_active_job filter on kind; INSERT kind (+priority).
  - claim_next_job(..., kinds=None): AND kind IN (...) when given.
  - append_job_output(job_id, text): keep tail <= 1 MiB (reuse the ffmpeg_stderr tail logic), add dropped bytes to
    output_dropped. get_job_output(job_id) -> (text, dropped).
  - cleanup_old_jobs unchanged (output purged with the row).
  - cluster_nodes.capabilities column; heartbeat(..., capabilities=None) writes it; nodes_missing_capability(cap,
    online_within_s) -> list of node names (PG); SQLite returns [].
  - Tests: legacy-DB upgrade adds columns; dedupe per kind; claim filters kinds; output cap/tail/dropped math;
    PG mocked SQL contains ADD COLUMN IF NOT EXISTS kind/output/output_dropped/capabilities.

Task 12 MODIFY resources/daemon/worker.py + tests:
  - process_job: kind = job.get("kind") or "convert"; for non-convert skip _preflight_disk_pressure, input/output
    size bookkeeping for directories, same-extension logic; require os.path.exists(path) for all.
  - _run_conversion_inner: argv from JOB_KINDS[kind].argv(...); capture every stdout line into a bounded buffer
    and flush via job_db.append_job_output every 2 s and at exit (all kinds); success = rc in kind.success_codes.
  - Worker claims with kinds=SUPPORTED_KINDS; heartbeat sends capabilities="job-kinds-v1".
  - Tests: per-kind argv, audit rc 1 completes, repair rc 1 fails, output flushed, convert path byte-identical argv.

Task 13 MODIFY handler.py, routes.py, webhook_parsing.py + tests:
  - parse_jobs_body (kind default "convert", path, args list[str] or shlex string, profile, priority int).
  - POST /jobs -> _post_jobs: validate kind/args (forbidden args -> 400); path exists (400); recycle-bin path (400);
    convert -> existing _dispatch_path flow unchanged; other kinds -> single job for the path (no default args,
    no same-extension skip, no directory expansion; preview requires a file); PG capability gate -> 409.
  - GET /jobs/{id}/output (thread query through _dispatch_get_job); GET /paths (roots as listed in gotchas);
    GET /jobs and GET /jobs/{id} add "kind" and drop output fields.
  - Tests mirroring TestQueueFile/TestGetJobFfmpegStderr/TestGetBrowse/TestDoPostRouting.

Task 14 DOCS Phase 2: docs/daemon.md endpoints (POST /jobs, GET /jobs/{id}/output, GET /paths, kinds, 409 gate,
  document POST /convert, / and /webhook/generic) + docs/openapi.yaml (Job.kind, new paths, JobSubmit schema);
  `mise run test:openapi`.

Task 15 CREATE resources/cli/clientconfig.py, http.py, routing.py + tests; MODIFY commands.py:
  - Target resolution (--target > SMA_TARGET > default-target > implicit local); Darwin loopback rewrite.
  - Decision: probe GET /health (1 s). Reachable and not --local -> HTTP; unreachable implicit local -> throwaway;
    unreachable explicit -> exit 3. --local with a reachable daemon prints a one-line contention warning.
  - Path mapping: argstat abspath -> longest matching `local` prefix -> `remote`; else unchanged. Validate against
    GET /paths (prefix match on path boundaries) -> exit 2 naming the target.
  - Submit POST /jobs (X-API-Key), 401 -> exit 4; then unless --no-wait poll GET /jobs/{id} + /output every 1 s,
    writing new output to stdout; Ctrl-C -> print "detached: job <id> (sma jobs show <id>)", exit 130.
    Final: completed -> 0, failed -> 1 (print error), cancelled -> 1.
  - sma jobs list|show|logs|cancel; target add/list/use/rm (api key from stdin, never argv); map add/list/rm;
    config file written 0600; refuse to read a config with api keys that is group/world-readable (exit 2).
  - --json: one JSON object per command result (job dict without output) on stdout.

Task 16 DOCS Phase 2: CLI quickstart sections for targets, mappings, daemon submission, --json, exit codes.

# ---------------- Phase 3: daemon lifecycle ----------------
Task 17 CREATE resources/cli/daemonctl.py + tests; MODIFY commands.py:
  - Detect compose from daemon.json labels (project, working_dir, config_files, service).
  - compose plans: docker compose -p P --project-directory W -f F... <pull SERVICE | up -d SERVICE |
    stop SERVICE | restart SERVICE | logs -f --tail 200 SERVICE>.
  - plain plans: up = [rm -f NAME if exists and not compose] + run -d --name NAME --restart unless-stopped
    -p PORT:8585 -v $SMA_HOME/{config,logs,data}:/{config,logs,data} -v R:R per media root (+/transcode,/recycle via
    optmount) + GPU flags IMAGE; then `docker exec NAME python -m resources.cli _wait-health http://127.0.0.1:8585/health 60`.
    down = stop + rm. restart = docker restart. logs = docker logs -f --tail 200. upgrade = pull + up.
  - up without media roots (no --media, none saved) -> exit 2; save --media roots to clientconfig daemon.media-roots.
  - status: HTTP /health of the local target + container state from daemon.json; --json.
  - `_wait-health URL SECS` hidden command (polls, exit 0/3).

Task 18 RETIRE + DOCS Phase 3: remove setup/install-docker-target.sh, setup/sma-ng-docker-aliases.sh and their
  mise task/doc references (`mise run setup:docker:target`), update docs/deployment.md task table, compose quickstart
  (compose optional; `sma daemon up` primary), cluster docs (clusters unchanged), wiki PAGES if docs added/removed.
```

### Per-task pseudocode (critical parts)

```sh
# setup/sma-host.sh - plan execution (POSIX; no eval)
run_plan() {  # $1 = plan file already read into $plan_text
  first=1; n=0
  # Pass 1: count steps and validate header (exit 5 on mismatch).
  # Pass 2: for each step build "$@" with `set --`, then either run or exec the last one.
  while IFS= read -r line; do
    case "$line" in
      "step docker") set -- ;;
      "arg "*) set -- "$@" "${line#arg }" ;;
      "optmount "*) p="${line#optmount }"; [ -d "$p" ] && set -- "$@" -v "$p:$p${SMA_SEL:+:z}" ;;
      "end") n=$((n + 1)); if [ "$n" -eq "$total" ]; then exec "$DOCKER_BIN" "$@"; fi
             "$DOCKER_BIN" "$@" || exit $? ;;
    esac
  done <<EOF
$plan_body
EOF
}
# The first arg of each step must be in: run pull rm stop logs compose exec create cp inspect restart.
```

```python
# resources/cli/routing.py
def decide(cmd, facts, cfg, flags) -> Route:
  target = resolve_target(flags.target, os.environ.get("SMA_TARGET"), cfg)  # Target(name, url, api_key, mappings, explicit)
  if cmd in THROWAWAY_ONLY or flags.local:
    if flags.local and http.healthy(target.url): warn("a daemon is running at %s; --local runs alongside it" % target.url)
    return Route.throwaway()
  if http.healthy(target.url, timeout=1.0):
    return Route.daemon(target)
  if target.explicit:
    raise Unreachable("sma: target %s (%s) is not reachable" % (target.name, target.url))   # exit 3
  return Route.throwaway()
```

```python
# resources/daemon/db.py - output tail cap (mirror update_job_ffmpeg_stderr)
def append_job_output(self, job_id, text):
  # SELECT output, output_dropped; combined = (old or "") + text; data = combined.encode("utf-8")
  # if len(data) > _JOB_OUTPUT_MAX_BYTES: drop = len(data) - MAX; data = data[drop:]; dropped += drop
  # UPDATE jobs SET output=?, output_dropped=? WHERE id=?   (decode errors="replace")
```

### Integration Points

```yaml
DATABASE:
  - migration: jobs.kind TEXT DEFAULT 'convert', jobs.output TEXT, jobs.output_dropped INTEGER DEFAULT 0;
               cluster_nodes.capabilities TEXT (SQLite PRAGMA-guarded ALTER; PG ADD COLUMN IF NOT EXISTS)
  - index: none required (claim already filters by status; kind is low-cardinality)
API/ROUTES:
  - add to: resources/daemon/routes.py _post_routes "/jobs", _get_routes "/paths"; _dispatch_get_job "/output"
  - middleware: existing check_auth (none of the new routes are public)
CONFIG:
  - daemon sma-ng.yml: no new keys
  - client: ~/.config/sma/config.yml (new, client-only) and ~/.config/sma/image (one line)
  - env (bootstrap): SMA_HOME, SMA_IMAGE, SMA_TARGET, DOCKER_BIN
IMAGE:
  - resources/cli ships via COPY . . ; smoke test runs `python -m resources.cli version`
INSTALL:
  - install-smang.sh at repo root, served from raw.githubusercontent.com/newdave/sma-ng/main/install-smang.sh
```

## Validation Loop

### Level 1: Syntax and style

```bash
source venv/bin/activate
ruff check resources/cli resources/daemon tests --fix && ruff format resources/cli resources/daemon tests
mise run test:shell                      # ShellCheck: setup/sma-host.sh, install-smang.sh
mise run test:lint                       # ruff + bandit (resources/daemon, daemon.py, manual.py, rename.py)
```

### Level 2: Unit and integration tests

```bash
source venv/bin/activate
python -m pytest tests/test_cli_*.py tests/test_sma_host.py tests/test_install_smang.py -q      # Phase 1
python -m pytest tests/test_job_kinds.py tests/test_sqlite_db.py tests/test_daemon.py \
  tests/test_handler.py tests/test_worker.py tests/test_worker_reliability.py -q               # Phase 2
python -m pytest tests/test_docker.py tests/test_entrypoint.py -q
mise run test:cov                        # global >= 90%, per-module (>=100 stmts) >= 70%
```

### Level 3: Docs and API contract

```bash
mise run test:openapi                    # docs/openapi.yaml valid
mise run docs:check                      # links, mise refs, wiki pages, markdownlint
```

### Level 4: Image and end-to-end (ask the operator before running containers on their machine)

```bash
mise run build:smoke                     # includes python -m resources.cli version
# Manual, on a Docker host with the operator's consent:
sh install-smang.sh --tag <built tag>
sma repair -n /path/to/dir; echo $?      # throwaway: no container left (docker ps -a --filter label=sma.throwaway=1)
sma --target local convert /path/file.mkv    # with a daemon running (Phase 2)
```

## Final validation checklist

- [x] All tests pass: `mise run test`
- [x] Coverage gates: `mise run test:cov`
- [x] Lint: `mise run test:lint`, `mise run test:shell`
- [x] OpenAPI valid: `mise run test:openapi`
- [x] Docs: `mise run docs:check`; `mise run wiki:publish` after pushing doc changes (hook does it for `git push`)
- [x] Image smoke: `mise run build:smoke` (CI runs it in docker.yml)
- [x] Manual throwaway run leaves no container; missing path exits 2; GPU flags present on a GPU host
- [x] Error cases: unreachable target 3, auth 4, plan version 5, Ctrl-C detach 130
- [x] No API key ever printed or logged; client config 0600 enforced
- [x] Docs updated in the same commits as the behaviour; conventional commits; no AI attribution

## Anti-Patterns to Avoid

- ❌ Mounting the Docker socket into any container, or running `eval` on plan contents.
- ❌ `docker run -v` with an unchecked source path (auto-creates root-owned directories).
- ❌ Passing `-c` to `repair.py` in throwaway runs (disables routing); relying on `/app/config` existing.
- ❌ Changing one DB backend without its twin; adding `kind` to `_METRICS_EXPANSION_COLUMNS`.
- ❌ Routing non-convert jobs through `_merge_args` / same-extension skip / per-file directory expansion.
- ❌ Returning `output` in `GET /jobs` lists.
- ❌ Adding `requests` or other new dependencies to the client; the stdlib `urllib` is enough.
- ❌ Printing API keys, or accepting them as CLI arguments (they would land in shell history).
- ❌ Running containers on the operator's machine during development without asking.
