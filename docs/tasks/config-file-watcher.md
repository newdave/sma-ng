# Task Breakdown — Auto-reload sma-ng.yml on file change

Companion to [docs/prps/config-file-watcher.md](../prps/config-file-watcher.md).

Add a polling watcher that calls the existing
`DaemonServer.reload_config()` when `sma-ng.yml` is modified. No new
dependencies; mirrors the three existing daemon threads
(`HeartbeatThread`, `ScannerThread`, `RecycleBinCleanerThread`).

## Conventions

- All Python work runs from an activated venv.
- One logical commit per task per `CLAUDE.md` rules.
- After each commit: `git pull --rebase && git push`.
- No `Co-Authored-By` / AI attribution.

## Critical path

```text
T1 (schema + sample) ─┐
                      ├── T3 (watcher thread + wiring + lock) ── T4 (tests) ── T5 (docs) ── T6 (commits)
T2 (PCM surface)   ───┘
```

T1 and T2 are independent; T3 depends on both.

---

## T1 — Schema knob + sample regen

**Files**

- `resources/config_schema.py`
- `setup/sma-ng.yml.sample` (regenerated)

**Steps**

1. Add `ConfigWatchSettings` model with three fields:
   `enabled: bool = True`, `interval_seconds: int = 5`,
   `debounce_seconds: int = 2`.
2. Add `config_watch: ConfigWatchSettings = Field(default_factory=ConfigWatchSettings)`
   to `DaemonSettings`.
3. `mise run config:sample` and verify the diff adds:

   ```yaml
   daemon:
     ...
     config-watch:
       enabled: true
       interval-seconds: 5
       debounce-seconds: 2
   ```

**Acceptance**

- **Given** a fresh `sma-ng.yml`,
  **When** `ConfigLoader().load(path)` runs,
  **Then** `cfg.daemon.config_watch.enabled` is `True`,
  `interval_seconds` is `5`, `debounce_seconds` is `2`.
- **Given** `daemon.config-watch.enabled: false` in YAML,
  **When** loaded,
  **Then** the field is `False`.
- **Given** the regenerated sample,
  **When** `pytest tests/test_config_sample_consistency.py` runs,
  **Then** it passes.

**Suggested commit**: bundled with T2.

---

## T2 — PathConfigManager surface

**File**: `resources/daemon/config.py`

**Steps**

1. In `PathConfigManager.load_config`, project the new schema field
   onto `self.config_watch` (a `ConfigWatchSettings` instance).
2. When the daemon block is missing, fall back to the schema
   defaults (`ConfigWatchSettings()`).

**Acceptance**

- **Given** a config with no `daemon.config-watch` block,
  **When** the daemon starts,
  **Then** `path_config_manager.config_watch.enabled` is `True` and
  `interval_seconds` is `5`.

**Suggested commit (T1+T2)**: `feat(daemon): config-watch schema knob`

---

## T3 — `ConfigWatcherThread` + DaemonServer wiring + reload lock

**Files**

- `resources/daemon/threads.py` (new class)
- `resources/daemon/server.py` (start/stop wiring + lock)

**Steps**

1. Add `ConfigWatcherThread(_StoppableThread)` to `threads.py`:

   ```python
   class ConfigWatcherThread(_StoppableThread):
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
           # see PRP pseudocode for full body — debounce loop included
           ...
   ```

2. In `DaemonServer.__init__`:
   - Add `self._reload_lock = threading.Lock()`.
   - After scanner/recycle threads are wired, conditionally start the
     watcher:

     ```python
     cw = self.path_config_manager.config_watch
     if (self.path_config_manager._config_file
             and cw.enabled
             and cw.interval_seconds > 0):
         self.config_watcher_thread = ConfigWatcherThread(
             server=self, path_config_manager=self.path_config_manager,
             settings=cw, logger=logger,
         )
         self.config_watcher_thread.start()
     else:
         self.config_watcher_thread = None
         logger.info("Config watcher disabled.")
     ```

3. Wrap `reload_config` body in `with self._reload_lock:`.
4. In `graceful_restart` and `shutdown`, stop the watcher if set:

   ```python
   if self.config_watcher_thread:
       self.config_watcher_thread.stop()
       self.config_watcher_thread.join(timeout=5)
   ```

**Acceptance**

- **Given** a daemon launched with a config file path AND
  `daemon.config-watch.enabled: true`,
  **When** the daemon starts,
  **Then** `server.config_watcher_thread.is_alive()` is `True`.
- **Given** `daemon.config-watch.enabled: false`,
  **When** the daemon starts,
  **Then** `server.config_watcher_thread is None`.
- **Given** `daemon.config-watch.interval-seconds: 0`,
  **When** the daemon starts,
  **Then** `server.config_watcher_thread is None`.
- **Given** the watcher is running and the config mtime changes,
  **When** `interval + debounce` seconds elapse,
  **Then** `server.reload_config()` is called exactly once.

**Suggested commit**: `feat(daemon): auto-reload sma-ng.yml on file change`

---

## T4 — Tests

**File**: `tests/test_config_watcher.py` (new)

**Steps**

Add fast tests with sub-second intervals (`interval_seconds=0.05`,
`debounce_seconds=0.05`), driving the thread by calling `start()`
and `stop()` directly. Use a real tmpfile (`tmp_path / "sma-ng.yml"`)
so `os.stat` returns truthful mtimes.

Test list:

- `test_change_detected_triggers_reload` — touch the file → reload
  called once.
- `test_no_change_no_reload` — no change for 2x interval → reload
  never called.
- `test_debounce_coalesces_rapid_changes` — three touches inside the
  debounce window → exactly one reload.
- `test_missing_file_does_not_crash` — delete file, sleep
  3 × interval, recreate, expect one reload after recreate.
- `test_reload_failure_does_not_busy_loop` — make
  `server.reload_config` return `False`; subsequent ticks without
  mtime change must NOT call reload again.
- `test_disabled_skips_thread` — DaemonServer init with
  `enabled=False` leaves `config_watcher_thread is None`.
- `test_zero_interval_skips_thread` — same with `interval_seconds=0`.
- `test_lock_serializes_with_manual_reload` — pre-acquire
  `server._reload_lock`; trigger watcher; verify it blocks until
  released and only reloads once.

Use a small `FakeServer` stub (mock for `reload_config`) and a
`FakePcm` stub exposing `_config_file` and `config_watch`. Mirror
the harness patterns in `tests/test_daemon.py` for
`ScannerThread` / `HeartbeatThread`.

**Acceptance**

- All new tests pass.
- The full suite still passes.
- No real-clock `time.sleep` longer than 0.5s in any test.

**Suggested commit**: `test(daemon): cover config watcher behavior`

---

## T5 — Documentation

**Files**

- `docs/configuration.md`
- `docs/daemon.md`
- `/tmp/sma-wiki/Configuration.md`
- `/tmp/sma-wiki/Daemon-Mode.md`
- `resources/docs.html` (only if grep matches)

**Steps**

1. Add a row to the daemon-settings table describing
   `config-watch.enabled`, `config-watch.interval-seconds`, and
   `config-watch.debounce-seconds`. Include the behaviour summary:
   "polls the active sma-ng.yml every `interval-seconds`; when the
   mtime/size changes and stays stable for `debounce-seconds`,
   triggers an in-place reload (same code path as POST /reload)".
2. In `docs/daemon.md`'s existing "POST /reload" / reload section,
   add a paragraph: "Auto-reload is on by default. The daemon
   watches `sma-ng.yml` and triggers the same reload when it
   changes; disable via `daemon.config-watch.enabled: false` if you
   prefer manual control."
3. Mirror to wiki and (if applicable) `resources/docs.html`.
4. `markdownlint` clean.

**Acceptance**

- `markdownlint` returns 0 on edited files.
- `docs/configuration.md` and `/tmp/sma-wiki/Configuration.md`
  document the three sub-fields.
- `docs/daemon.md` references the auto-reload alongside the
  manual `/reload` endpoint.

**Suggested commit**: `docs(daemon): document config auto-reload`

---

## T6 — Commits + push

**Steps**

```bash
# Commit 1: schema + PCM + sample
git add resources/config_schema.py resources/daemon/config.py setup/sma-ng.yml.sample
git commit -m "feat(daemon): config-watch schema knob

Add daemon.config-watch (enabled, interval-seconds, debounce-seconds)
to the schema and project it onto PathConfigManager.config_watch.
Sample regenerated."
git pull --rebase && git push

# Commit 2: watcher + wiring + lock
git add resources/daemon/threads.py resources/daemon/server.py
git commit -m "feat(daemon): auto-reload sma-ng.yml on file change

Adds ConfigWatcherThread (mtime/size polling, debounced) that calls
DaemonServer.reload_config() when the active sma-ng.yml changes.
Reload is now serialized via DaemonServer._reload_lock so the
auto-watcher and POST /reload don't race on scanner_thread /
recycle_cleaner_thread reassignment."
git pull --rebase && git push

# Commit 3: tests
git add tests/test_config_watcher.py
git commit -m "test(daemon): cover config watcher behavior"
git pull --rebase && git push

# Commit 4: docs
git add docs/configuration.md docs/daemon.md
git commit -m "docs(daemon): document config auto-reload"
git pull --rebase && git push

# Wiki
cd /tmp/sma-wiki-fresh
# (apply edits from /tmp/sma-wiki/, or edit directly)
git add Configuration.md Daemon-Mode.md
git commit -m "docs: document config auto-reload"
git push origin HEAD:master
```

**Acceptance**

- `git log --oneline` shows four small commits in the order above.
- Wiki has a matching commit on `master`.
- No `Co-Authored-By`.

---

## Out of scope

- inotify / watchdog dependency — explicitly rejected (PRP
  Anti-Patterns). Polling at 5s is sufficient.
- SIGHUP repurposing — already bound to `graceful_restart`.
- Reloading anything other than `sma-ng.yml`. Per-job env files,
  daemon.env, etc. are out of scope for this PRP.
- Changing the body of `reload_config` itself (only wrapping it
  with a lock). Behavior preserved.

## Escalation triggers

Stop and ask the user if:

1. Tests are persistently flaky despite the sub-second timing
   (signals a fundamental race between the polling loop and the
   `_stop_event` cooperation that the existing thread base may not
   support cleanly — solvable but warrants a check-in).
2. Live smoke shows the watcher firing on every tick without an
   apparent change — the docker-mounted filesystem is reporting
   spurious mtime updates and we need a content hash instead of
   mtime+size.
3. The reload lock surfaces a deadlock with another daemon
   subsystem (the scanner/recycle threads should not hold any lock
   that `reload_config` needs to acquire — but verify under load).
