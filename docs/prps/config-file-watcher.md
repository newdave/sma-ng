name: "Auto-reload sma-ng.yml on file change"
description: |
  Add a daemon-side watcher that detects changes to the active
  ``sma-ng.yml`` and triggers the existing in-place reload. Today the
  operator has to ``curl -X POST /reload`` (or send `SIGHUP` for a
  full restart) to pick up edits; this PRP wires that up automatically
  so changes made via ``mise run config:roll``, an editor save, or a
  remote rsync take effect within a few seconds with no operator
  action.

---

## Discovery Summary

### Initial Task Analysis

User request: "Add dynamic reloading of config based on file change
for sma-ng.yml". The infrastructure is already in place — the daemon
has a working in-place reload (`DaemonServer.reload_config` at
`resources/daemon/server.py:202`), exposed as `POST /reload`. The
missing piece is the automatic trigger.

### Existing reload behavior (the keeper)

`DaemonServer.reload_config()` already:

- Re-reads `sma-ng.yml` via `PathConfigManager.load_config()`.
- Re-applies API key / basic auth / ffmpeg_dir with CLI > env > config
  priority intact.
- Pushes new ffmpeg_dir / job_timeout_seconds / progress_log_interval
  onto every worker.
- Restarts the `ScannerThread` and `RecycleBinCleanerThread` so
  changed `scan_paths` and recycle-bin policy take effect.
- On failure, logs the exception and **keeps the previous runtime
  settings** — no crash, no half-applied state.

This work doesn't touch any of that. We only add a new auto-trigger.

### Codebase mapping

```text
resources/daemon/threads.py       — _StoppableThread base, plus
                                    HeartbeatThread, ScannerThread,
                                    RecycleBinCleanerThread to mirror.
resources/daemon/server.py:202    — reload_config() (the trigger).
resources/daemon/server.py        — DaemonServer.__init__ starts
                                    other threads; new watcher goes
                                    here.
resources/daemon/config.py:282    — PathConfigManager._config_file
                                    holds os.path.realpath of the
                                    active config (set on first
                                    load_config call). This is what
                                    the watcher should stat.
resources/config_schema.py        — DaemonSettings; add a new
                                    ConfigWatchSettings nested model.
setup/sma-ng.yml.sample           — regenerate via mise run
                                    config:sample.
docs/configuration.md, docs/daemon.md, /tmp/sma-wiki/
                                  — three-place doc rule.
```

### Design choice

**Polling thread, not inotify/watchdog.** Reasons:

- Project deps stay minimal (`setup/requirements.txt` has no
  filesystem-watcher today). Adding `watchdog` adds a wheel and a
  per-platform backend (kqueue on macOS, inotify on Linux). Not worth
  it for a config file that changes once in a blue moon.
- The codebase already has three `_StoppableThread` patterns; one more
  is mechanical.
- Polling at 5s is invisible in CPU/IO terms (one `os.stat` per tick).
- Cross-platform without conditional code paths.
- Container-friendly: no inotify watch limit issues on small
  filesystems.

**Trade-offs accepted:**

- Up to `interval` seconds of latency between save and reload.
  Acceptable for a daemon config; we are not optimizing latency-
  critical hot paths.
- Doesn't catch atomic-rename edits (e.g. editor saves to `.swp`,
  renames over the original) any earlier than mtime polling does.
  The existing `os.path.realpath` resolution + statting the
  destination handles this.

### Behavior contract

1. The watcher polls the active config file every `interval`
   seconds (default 5).
2. On every poll, `os.stat()` the realpath; record `(mtime_ns, size)`.
3. When the tuple differs from the last-seen tuple, start a
   debounce timer of `debounce` seconds (default 2). Reset the timer
   if another change is observed during the window.
4. After the debounce timer expires with no further change, call
   `server.reload_config()`. The existing `reload_config` does its
   own validation and rolls back on failure.
5. If `os.stat` raises `FileNotFoundError` (the config was deleted
   or is mid-rename), do **not** trigger a reload. Log a one-line
   debug note and keep polling. Resume normal operation when the
   file reappears.
6. If `reload_config()` returns `False` (validation failed), log a
   warning and keep polling. Do not retry the same `(mtime, size)`
   tuple — only retry when the tuple changes again.
7. Watcher is opt-out via `daemon.config_watch.enabled: false` (or
   by setting `interval: 0`).
8. The watcher logs:
   - INFO at startup: `Config watcher started: file=... interval=Xs`
   - INFO on triggered reload: `Config change detected at file=... — reloading`
   - WARNING on reload failure: `Config reload failed; will retry on next change.`
   - DEBUG on transient stat errors.

### Why not SIGHUP?

The daemon already binds `SIGHUP` to `graceful_restart` (full
process re-exec). Repurposing it for in-place reload would change
existing operator behavior in a confusing way. The new auto-watcher
is purely additive and doesn't touch signal handling.

### User Clarifications Received

None. Auto-mode. The defaults below match this codebase's other
poll-based threads (heartbeat, scanner, recycle cleaner) — all
opt-out via the schema, all stoppable, all log at INFO when they
do meaningful work.

## Goal

A small, opt-out polling thread that detects mtime/size changes to
`sma-ng.yml` and triggers the existing `DaemonServer.reload_config()`
within a few seconds.

## Why

- Operator running `mise run config:roll` against three nodes today
  has to either restart each daemon or POST `/reload` to each one.
  The watcher closes that loop.
- Editor-save iteration on a single host is an obvious developer-
  experience win.
- The reload code already exists and is well-tested; the watcher is
  a thin trigger, low risk.

## What

A new `ConfigWatcherThread` in `resources/daemon/threads.py`,
launched by `DaemonServer.__init__` alongside the other daemon
threads, plus schema knobs and tests.

### Success Criteria

- [ ] `daemon.config_watch` block exists in the schema with
      `enabled` (bool, default `true`), `interval_seconds`
      (int, default `5`), `debounce_seconds` (int, default `2`).
- [ ] `DaemonServer` starts a `ConfigWatcherThread` when the
      daemon is launched with a config file path AND
      `config_watch.enabled` is true AND `interval_seconds > 0`.
- [ ] When `sma-ng.yml` is modified, `reload_config()` is called
      within `interval + debounce` seconds.
- [ ] When `sma-ng.yml` is deleted then re-created (atomic rename
      / `mv tmp config`), the watcher does not crash and triggers
      one reload after the new file settles.
- [ ] When `reload_config()` raises or returns False, the watcher
      logs a WARNING and keeps polling.
- [ ] Setting `config_watch.enabled: false` (or `interval_seconds:
      0`) skips the watcher entirely.
- [ ] `graceful_restart` and `shutdown` stop the watcher cleanly
      (mirrors how scanner_thread / recycle_cleaner_thread are
      stopped).
- [ ] Tests cover: change-detected→reload, no-change→no-reload,
      debounce, missing-file path, reload-failure path, opt-out.
- [ ] All existing tests pass.

## All Needed Context

### Research Phase Summary

- **Codebase patterns found**: Three production poll-based daemon
  threads (`HeartbeatThread`, `ScannerThread`,
  `RecycleBinCleanerThread`) all subclass `_StoppableThread` and
  drive their schedule with `self._stop_event.wait(timeout=...)`.
  The watcher mirrors them.
- **External research needed**: No.
- **Knowledge gaps**: None.

### Documentation & References

```yaml
- file: resources/daemon/server.py
  why: |
    DaemonServer.__init__ wires up the existing daemon threads; the
    watcher slots in alongside them. reload_config() at line 202 is
    the existing trigger we call.

- file: resources/daemon/threads.py
  why: |
    Pattern source for the new ConfigWatcherThread. Mirror the
    _StoppableThread cooperation contract (self.running flag plus
    self._stop_event.wait for cancellable sleep) used by every
    other daemon thread.

- file: resources/daemon/config.py
  why: |
    PathConfigManager._config_file (line 282) is the realpath of the
    active config — what the watcher should stat. PathConfigManager
    is the input to reload_config so we don't need extra plumbing.

- file: resources/config_schema.py
  why: |
    Add ConfigWatchSettings as a nested model under DaemonSettings.

- file: setup/sma-ng.yml.sample
  why: |
    Regenerate via mise run config:sample after schema changes.

- file: docs/configuration.md
  why: |
    Document the new daemon.config_watch block.

- file: docs/daemon.md
  why: |
    Mention auto-reload alongside the existing POST /reload
    endpoint section.
```

### Known Gotchas

```python
# CRITICAL: Do not call reload_config() while it's already running.
#   DaemonServer.reload_config restarts the scanner_thread and
#   recycle_cleaner_thread. If two reloads run concurrently they'll
#   race on those handles. Guard with a threading.Lock; if the lock
#   is already held, skip this tick and try again next interval.

# CRITICAL: PathConfigManager._config_file is set to None when the
#   daemon was launched without a resolvable config path. Skip
#   starting the watcher in that case (matches the existing guard
#   at server.py:204).

# CRITICAL: The watched file's realpath may change if the operator
#   atomically rewrites it via mise run config:roll's tmp+rename
#   pattern. The realpath returned by os.path.realpath() at startup
#   will resolve to the post-rename inode for the same path string
#   on every stat, so polling the original *path string* (not a
#   captured fd or inode) is what we want. Confirm by re-reading
#   _config_file every tick rather than caching the value.

# CRITICAL: Avoid stat-storming on slow filesystems. One os.stat()
#   every interval is fine; do NOT also fsync/read the file unless
#   the (mtime, size) tuple changed.

# CRITICAL: When _config_file is on a docker bind-mount, mtime
#   resolution can be 1s. Use mtime_ns where available
#   (os.stat().st_mtime_ns) so 1s-quick saves don't get coalesced
#   into a single tick. Fall back to mtime if st_mtime_ns is 0.

# CRITICAL: Tests must NOT rely on real-clock sleeps. Use a
#   monkeypatched _StoppableThread._stop_event or inject a fake
#   clock. Mirror the test pattern from tests/test_daemon.py for
#   ScannerThread / HeartbeatThread.
```

## Implementation Blueprint

### Schema

```python
# resources/config_schema.py
class ConfigWatchSettings(_Base):
    enabled: bool = True
    interval_seconds: int = 5
    debounce_seconds: int = 2

class DaemonSettings(_Base):
    ...
    config_watch: ConfigWatchSettings = Field(default_factory=ConfigWatchSettings)
```

### Tasks (in order)

```yaml
Task 1 — Schema knob:
MODIFY resources/config_schema.py:
  - Add ConfigWatchSettings model (enabled, interval_seconds,
    debounce_seconds).
  - Add `config_watch` field to DaemonSettings with the new model
    as default.

Task 2 — PathConfigManager surface:
MODIFY resources/daemon/config.py:
  - Add a public `config_watch` property that returns the parsed
    ConfigWatchSettings (mirroring how scan_paths /
    recycle_bin_max_age_days are exposed). Default to enabled=True
    interval_seconds=5 debounce_seconds=2 when the daemon block is
    absent.

Task 3 — ConfigWatcherThread:
ADD class to resources/daemon/threads.py:
  - Subclass _StoppableThread.
  - __init__(self, server, path_config_manager, settings, logger):
      - server: DaemonServer (for reload_config())
      - path_config_manager: PathConfigManager (for _config_file)
      - settings: ConfigWatchSettings (interval_seconds,
                  debounce_seconds)
  - run():
      - Capture initial (mtime_ns, size) of the realpath.
      - While running:
          * sleep interval seconds via _stop_event.wait
          * os.stat the realpath; on FileNotFoundError, debug-log
            once and skip
          * if (mtime_ns, size) unchanged, continue
          * else log INFO "Config change detected — reloading after
            debounce", then enter a debounce loop: keep stat'ing
            every 0.5s until debounce_seconds pass with no further
            change
          * call server.reload_config() under server's
            _reload_lock (added in Task 4)
          * record the post-reload (mtime_ns, size) regardless of
            success so we don't busy-retry the same change.

Task 4 — Wire into DaemonServer:
MODIFY resources/daemon/server.py:
  - Add self._reload_lock = threading.Lock() to __init__.
  - Wrap the body of reload_config() in `with self._reload_lock:`
    (so manual POST /reload and the auto-watcher don't race).
  - In __init__, after scanner_thread / recycle_cleaner_thread
    setup, start a ConfigWatcherThread if:
      path_config_manager._config_file is not None
      AND path_config_manager.config_watch.enabled
      AND path_config_manager.config_watch.interval_seconds > 0
    Store on self.config_watcher_thread. Otherwise log INFO
    "Config watcher disabled."
  - In graceful_restart() and shutdown(), call
    self.config_watcher_thread.stop() and join with the same
    timeouts as the other threads.
  - In reload_config()'s thread-restart block, **don't** restart
    the watcher (settings haven't changed shape; let it keep
    running).

Task 5 — Sample regen:
RUN: source venv/bin/activate && mise run config:sample
VERIFY: setup/sma-ng.yml.sample now has
  daemon:
    ...
    config-watch:
      enabled: true
      interval-seconds: 5
      debounce-seconds: 2

Task 6 — Tests:
ADD tests/test_config_watcher.py:
  - test_change_detected_triggers_reload (touch the file → reload
    called once after debounce expires).
  - test_no_change_no_reload (no stat change → reload never
    called).
  - test_debounce_coalesces_rapid_changes (multiple touches
    within debounce window → exactly one reload call).
  - test_missing_file_does_not_crash (delete the file mid-loop;
    watcher logs and continues; on re-create, triggers reload).
  - test_reload_failure_does_not_busy_loop (reload_config
    returns False; watcher waits for next mtime change before
    retrying).
  - test_disabled_config_watch_skips_thread (enabled=False →
    DaemonServer doesn't start the thread).
  - test_zero_interval_skips_thread (interval_seconds=0 → same).
  - test_lock_serializes_with_manual_reload (manual POST /reload
    holding the lock → watcher waits, doesn't double-trigger).

Test pattern: mirror tests/test_daemon.py's existing
ScannerThread/HeartbeatThread tests — inject a real tmp file,
drive the thread by calling .run() in a worker thread, signal
.stop() and join. Use very small interval/debounce values
(e.g. 0.05s / 0.05s) to keep test runtime sub-second.

Task 7 — Documentation (three-place rule):
MODIFY docs/configuration.md:
  - Add daemon.config-watch row to the daemon-settings table with
    the three sub-fields and behavior summary.
MODIFY docs/daemon.md:
  - In the existing "POST /reload" section, add a paragraph noting
    that auto-reload is on by default and how to disable it.
MIRROR same edits to /tmp/sma-wiki/Configuration.md and
       /tmp/sma-wiki/Daemon-Mode.md.
GREP resources/docs.html for matching prose; mirror if present.

Task 8 — Commit + push (logical commits per CLAUDE.md):
  - Commit 1: schema + PathConfigManager surface + sample regen
       feat(daemon): config-watch schema knob
  - Commit 2: ConfigWatcherThread + DaemonServer wiring + lock
       feat(daemon): auto-reload sma-ng.yml on file change
  - Commit 3: tests
       test(daemon): cover config watcher behavior
  - Commit 4: docs
       docs(daemon): document config auto-reload
  - After each: git pull --rebase && git push.
  - No AI attribution.
```

### Per-task pseudocode

```python
# resources/daemon/threads.py — ConfigWatcherThread

class ConfigWatcherThread(_StoppableThread):
    """Polls the active sma-ng.yml and triggers DaemonServer.reload_config()
    on detected changes. Tunable via daemon.config_watch.
    """

    def __init__(self, server, path_config_manager, settings, logger):
        super().__init__()
        self.server = server
        self.pcm = path_config_manager
        self.interval = max(1, int(settings.interval_seconds))
        self.debounce = max(0, int(settings.debounce_seconds))
        self.log = logger

    def _stat_tuple(self):
        path = self.pcm._config_file
        if not path:
            return None
        try:
            st = os.stat(path)
            return (st.st_mtime_ns or st.st_mtime, st.st_size)
        except FileNotFoundError:
            return None

    def run(self):
        last = self._stat_tuple()
        path = self.pcm._config_file
        self.log.info("Config watcher started: file=%s interval=%ds debounce=%ds",
                      path, self.interval, self.debounce)
        while self.running:
            self._stop_event.wait(timeout=self.interval)
            if not self.running:
                return
            current = self._stat_tuple()
            if current is None or current == last:
                continue
            self.log.info("Config change detected at %s — reloading after %ds debounce", path, self.debounce)
            # debounce: wait until the file stops changing for `debounce` seconds
            stable = current
            settle_deadline = time.monotonic() + self.debounce
            while self.running and time.monotonic() < settle_deadline:
                self._stop_event.wait(timeout=0.5)
                latest = self._stat_tuple()
                if latest is None:
                    continue
                if latest != stable:
                    stable = latest
                    settle_deadline = time.monotonic() + self.debounce
            try:
                ok = self.server.reload_config()
                if not ok:
                    self.log.warning("Config reload failed; will retry on next change.")
            except Exception:
                self.log.exception("Config reload raised; will retry on next change.")
            # record post-reload tuple regardless of success so we don't
            # spin on the same change
            last = self._stat_tuple() or stable
```

```python
# resources/daemon/server.py — wiring

def __init__(self, ...):
    ...
    self._reload_lock = threading.Lock()
    ...
    cw = self.path_config_manager.config_watch
    if (self.path_config_manager._config_file
            and cw.enabled
            and cw.interval_seconds > 0):
        self.config_watcher_thread = ConfigWatcherThread(
            server=self,
            path_config_manager=self.path_config_manager,
            settings=cw,
            logger=logger,
        )
        self.config_watcher_thread.start()
    else:
        self.config_watcher_thread = None
        logger.info("Config watcher disabled.")

def reload_config(self):
    with self._reload_lock:
        return self._reload_config_locked()

def _reload_config_locked(self):
    # body of the existing reload_config moves here verbatim
    ...

def graceful_restart(self):
    ...
    if self.config_watcher_thread:
        self.config_watcher_thread.stop()
    ...
```

### Integration Points

```yaml
SCHEMA:
  - DaemonSettings.config_watch (new ConfigWatchSettings nested model).
DAEMON:
  - DaemonServer holds a _reload_lock and an optional
    config_watcher_thread.
  - graceful_restart / shutdown stop the watcher cleanly.
  - reload_config() body wrapped in the lock so manual POST /reload
    and the watcher serialize.
DOCS:
  - docs/configuration.md, docs/daemon.md, wiki mirrors.
TESTS:
  - tests/test_config_watcher.py (new), test patterns mirrored
    from tests/test_daemon.py existing thread tests.
```

## Validation Loop

### Level 1: Lints

```bash
source venv/bin/activate
ruff check resources/ tests/
ruff format --check resources/ tests/
python scripts/lint-logging.py
markdownlint docs/configuration.md docs/daemon.md /tmp/sma-wiki/Configuration.md /tmp/sma-wiki/Daemon-Mode.md
```

### Level 2: Unit + integration tests

```bash
source venv/bin/activate
pytest tests/test_config_watcher.py -v
pytest tests/test_daemon.py -v -k "reload"   # ensure existing reload tests still pass
pytest -q                                    # full suite
```

### Level 3: Live smoke

```bash
# In one terminal:
source venv/bin/activate
python daemon.py --host 127.0.0.1 --port 8585 -d config/sma-ng.yml --workers 1

# In another:
# touch the config to bump mtime; expect within ~7s a daemon log line:
#   "Config change detected at config/sma-ng.yml — reloading after 2s debounce"
#   "Configuration reloaded."
touch config/sma-ng.yml

# break the config to verify failure path:
echo 'NOT YAML AT ALL' >> config/sma-ng.yml
# expect:
#   "Config reload failed; will retry on next change."
# fix and retouch:
git checkout config/sma-ng.yml
touch config/sma-ng.yml
# expect another successful reload.
```

## Final validation Checklist

- [ ] `pytest` passes
- [ ] `ruff check` / `ruff format --check` clean
- [ ] `markdownlint` clean for edited markdown
- [ ] `python scripts/lint-logging.py` clean
- [ ] Live smoke shows reload within `interval + debounce` seconds
- [ ] Setting `config_watch.enabled: false` suppresses the watcher
- [ ] Three-place doc rule honored
- [ ] One commit per logical area; no AI attribution

---

## Anti-Patterns to Avoid

- ❌ Don't add `watchdog` (or any inotify/kqueue dep). Polling is
  enough; a new dep isn't worth the latency improvement.
- ❌ Don't reload on every single mtime tick — debounce.
- ❌ Don't busy-retry after a reload failure; wait for the next
  change.
- ❌ Don't repurpose SIGHUP. SIGHUP already triggers
  `graceful_restart`; auto-reload is a separate concern.
- ❌ Don't skip the lock around `reload_config`. POST /reload and
  the watcher must serialize to avoid racing on
  `scanner_thread` / `recycle_cleaner_thread` reassignment.
- ❌ Don't crash the watcher thread on `FileNotFoundError`; the
  config disappears momentarily during atomic-rename writes.
- ❌ Don't hardcode the watch path. Re-read
  `path_config_manager._config_file` every tick so a future
  change to the active config path is picked up.

## Task Breakdown

A companion task breakdown lives at
[docs/tasks/config-file-watcher.md](../tasks/config-file-watcher.md).

## Confidence Score

**8 / 10** for one-pass implementation success.

Why not higher: the watcher interacts with three other daemon
threads (scanner / recycle cleaner are restarted by `reload_config`;
heartbeat is left alone). Test isolation around real tmp files +
real timing requires care to keep flake-free. Why not lower: the
trigger (`reload_config`) already exists and is well-tested, the
threading pattern has three working precedents in the same file,
and the schema + lock pieces are mechanical.
