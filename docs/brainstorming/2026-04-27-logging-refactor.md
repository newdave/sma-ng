# Feature Brainstorming Session: Concise, Single-Line Logging

**Date:** 2026-04-27
**Session Type:** Technical Design / Cross-Cutting Refactor

## 1. Context & Problem Statement

### Problem Description

The daemon's logs are noisy and inconsistent. Several sites emit multi-line records,
unbounded JSON blobs, or write directly to stdout instead of the logger, which makes
both the dashboard's per-config log viewer and the cluster `logs` PostgreSQL table
hard to scan. Specific symptoms:

- **Multi-line records** that wreck line-oriented tooling (`tail -f`, `grep`,
  `/cluster/logs?node_id=…&level=ERROR`):
  - `manual.py:612` — `print(json.dumps(output, indent=4))` bypasses the logger
    entirely. It's captured by the worker's subprocess pipe and flushed line-by-line
    into the per-config log file as a fragmented blob.
  - `resources/postprocess.py:123` — `self.log.debug(json.dumps(env, indent=4))`
    emits a multi-line debug record per post-process invocation.
  - `resources/mediaprocessor.py:683` — `json.dumps(dump, indent=4)` returned
    inline for FFmpeg command logging.
- **Inline JSON blobs** that are technically single-line but unbounded in width
  and routinely truncate badly when viewed in the dashboard:
  - `mediaprocessor.py:274–280, 832, 962, 996` — `Output Data:`, `Preopts:`,
    `Postopts:`, `Subtitle Extracts:`, `Analyzer recommendations:`,
    `Input Data:` — each dumps a stream-options dict with dozens of fields.
  - `worker.py:248` — `Progress:` JSON serialised every progress tick.
  - `webhook_client.py:77` — full webhook payload.
- **No redaction at the logging layer.** Today secrets are stripped only in
  specific code paths (e.g. `_strip_secrets` in `set_cluster_config`). Any
  log call that happens to dump a daemon-section dict can leak api_key /
  db_url. We caught this manually for `/admin/config`; nothing prevents a
  future caller from regressing it.
- **Format drift.** A mix of `%`-formatting, f-strings, and concatenation
  (`"Overriden move-to to " + args["moveto"]`). Some records use `extra=`
  for structured fields; most don't. The PostgreSQL `logs` table receives
  an unstructured `message` column with no parseable shape.
- **Level hygiene.** Several `INFO` lines are operationally noisy (e.g.
  `manual.py:785` "No-delete enabled"); some `DEBUG` lines carry information
  that an operator needs at `INFO` (e.g. routing-rule resolution).

### Target Users

- **Primary Users:** Operators triaging incidents — "why did this conversion
  fail?", "what did the daemon do at 14:32?", "which node claimed this job?"
  They live in the dashboard's `/cluster/logs` page and the per-config log
  viewer, and they `tail -f` `logs/<config>.log` over SSH.
- **Secondary Users:** Developers reading test output and CI runs. The same
  formatter is shared, so noisy logs make test failures harder to read.

### Success Criteria

- **Operator metrics:**
  - **Every log record is exactly one line on disk** (the only exception is
    Python tracebacks emitted by `log.exception()`; those stay multi-line by
    necessity but render with a clear leading marker so they're greppable as
    a group).
  - JSON-bearing log records render compactly (no `indent=`, total record
    width capped — see decision below) and elide oversized values.
  - Secrets (`api_key`, `db_url`, `username`, `password`, `node_id`,
    `apikey`, `token`) are redacted by the logging layer regardless of how
    the record was constructed.
- **Developer metrics:**
  - A pre-commit lint rule rejects new uses of `print(`, `json.dumps(...,
    indent=...)` inside `log.*` calls, and bare `\n` in log messages.
  - All existing tests still pass; new tests pin the formatter contract.
- **Performance:**
  - Formatter cost stays sub-millisecond per record at p99 (we emit O(10²)
    records per conversion; this can't show up in conversion latency).

### Constraints & Assumptions

- **Don't break the per-config log file** consumed by `/logs/<config>?lines=…`
  — that handler reads raw lines, so each record must remain newline-delimited.
- **Don't break the PostgreSQL `logs` table.** `PostgreSQLLogHandler` writes
  a structured row per record; the formatter changes apply to console + file
  handlers, not to the DB handler (which already uses `extra` fields).
- **Don't break `log.exception()` tracebacks.** Tracebacks are intrinsically
  multi-line; the formatter must let them through but tag the leading line
  so they're identifiable. The "single-line" rule applies to the
  application's own message, not the traceback frames the runtime emits.
- **Don't change log call signatures broadly.** A migration that touches
  ~169 call sites is high-risk; prefer a formatter-driven approach that
  makes existing call sites comply automatically.
- **Compatibility:** `manual.py` runs as a subprocess of the worker, so its
  stdout is captured into `logs/<config>.log`. Any `print()` we leave behind
  ends up in the file regardless of formatter. Those need to be migrated
  to the logger explicitly.

## 2. Brainstormed Ideas & Options

### Option A: Targeted offender cleanup only

- **Description:** Find each multi-line/`indent=` site and rewrite it
  individually. No formatter or lint rule.
- **Key Features:**
  - Direct edits to `manual.py:612`, `postprocess.py:123`, `mediaprocessor.py:683`,
    plus the high-fanout `Output Data:` / `Input Data:` / `Progress:` sites.
  - One commit per logical area.
- **Pros:**
  - Minimal moving parts, easiest to review.
  - No formatter bugs can affect well-behaved sites.
- **Cons:**
  - Doesn't prevent regressions — anyone can reintroduce a multi-line log.
  - Doesn't address redaction or width capping.
  - 169 call sites means easy to miss something.
- **Effort Estimate:** S
- **Risk Level:** Low (per change), but high cumulative regression risk.
- **Dependencies:** None.

### Option B: Custom `logging.Formatter` enforcing single-line + JSON pretty→compact

- **Description:** Replace the project's default `Formatter` with one that
  collapses any `\n` in the message to ` ⏎ ` (a visible marker), runs
  any embedded JSON-looking substring through `json.dumps(...,
  separators=(",",":"))`, truncates to a configured width, and emits one
  record per line. Tracebacks (`exc_info`) are formatted separately and
  written *after* the record on their own lines, but the application-level
  message is always exactly one line.
- **Key Features:**
  - Single-line invariant enforced at the formatter, not the call site.
  - Width cap (proposed: 1 KiB per record, configurable via env var).
  - JSON normalisation: detect `{…}` / `[…]` substrings and re-dump compact.
  - Optional smart truncation for long lists/dicts (e.g. show first N items
    with a `…+M more` tail marker).
- **Pros:**
  - Existing call sites become compliant without changes.
  - Single point of enforcement is testable.
- **Cons:**
  - Formatter-level JSON parsing is brittle (regex around braces) and can
    misinterpret messages that *contain* but aren't JSON.
  - Loses information silently if values get truncated (mitigated by also
    forwarding the full record to the DB handler unchanged).
- **Effort Estimate:** M
- **Risk Level:** Medium — formatter correctness is the whole game.
- **Dependencies:** None.

### Option C: Structured logging via `extra=` everywhere, key=value renderer

- **Description:** Convert all log calls to pass a short message + structured
  `extra={…}` keyword. Console formatter renders `msg key=val key=val`
  (logfmt-style); DB handler stores the dict as JSON.
- **Key Features:**
  - Ergonomic for the eye, easy to grep, machine-parseable.
  - Natural place to plug in redaction (`api_key=***`).
  - Maps cleanly to OpenTelemetry / structured backends if we ever migrate.
- **Pros:**
  - Cleanest long-term shape.
  - Redaction is trivial when secrets travel as `extra` keys, not as message text.
- **Cons:**
  - Migration scope is huge (169 call sites) and easy to leave half-done.
  - Many existing messages encode their values in the prose
    (`"Worker %d processing job %d"`); rewriting them mechanically loses
    grep-ability for current operators.
- **Effort Estimate:** XL
- **Risk Level:** High — big diff, many tests touch log assertions.
- **Dependencies:** None.

### Option D (chosen): Formatter + redaction + targeted cleanup + lint rule

- **Description:** Combine **B** as the enforcement mechanism with a small
  set of **A** fixes for the known direct-stdout / `indent=` offenders, plus
  a **redaction filter** layered onto the formatter, plus a lint rule to
  prevent regression. No mass rewrite of call sites.
- **Key Features:**
  - `resources/log.py` gains:
    - `SingleLineFormatter` that collapses newlines, compacts JSON-looking
      substrings, applies width cap, and emits traceback frames with a
      consistent prefix (two spaces + `| <frame>`) on subsequent lines.
    - `RedactingFilter` that walks the record's `args`, `extra`, and the
      final message text against `SECRET_KEYS` ∪ `SERVICE_SECRET_FIELDS`
      and replaces matched values with `***`.
  - Targeted offender cleanup:
    - `manual.py:612` — `print(json.dumps(output, indent=4))` → `log.debug("conversion result: %s", _compact(output))`.
    - `resources/postprocess.py:123` — drop `indent=4`, switch to `extra=` so
      formatter renders compactly.
    - `resources/mediaprocessor.py:683` — return compact JSON; the
      indent-4 form was used for one log site that no longer needs it.
  - Pre-commit lint rule (`.pre-commit-config.yaml` + small Python
    helper):
    - Reject `json.dumps(.*indent=` inside files matching `log\.|logger\.|log =`.
    - Reject literal `\n` inside f-strings/percent-strings passed to
      `log.(info|debug|warning|error|exception|critical)(`.
    - Reject `print(` in `daemon.py`, `manual.py`, `resources/`, `autoprocess/`,
      with an allowlist for top-of-script `print` (e.g. `--help` output).
  - Width cap configurable via `SMA_LOG_MAX_WIDTH` (default 1024).
  - Tests: `tests/test_log_formatter.py` covers single-line invariant,
    JSON compaction, redaction, width cap, and traceback handling.
- **Pros:**
  - Stops the bleed (lint) without a 169-site rewrite.
  - Redaction at the layer makes the cluster_config-leak class of bug
    impossible regardless of caller intent.
  - Rolls out incrementally — formatter ships first, offenders fixed in
    one or two follow-ups, lint rule enabled last so the cleanup commits
    don't trip the lint they introduced.
- **Cons:**
  - The "JSON-looking substring" detector is heuristic; tested cases are
    safe, but exotic messages may render slightly differently than today.
  - Width cap can hide a long stack trace when the trace is encoded as a
    string (rare; `log.exception` uses `exc_info`, which goes through a
    different code path).
- **Effort Estimate:** M
- **Risk Level:** Medium
- **Dependencies:** None — `resources/log.py` is the central logger factory
  that everything in the project uses.

### Additional Ideas Considered

- **Per-config log file rotation/age cap.** Already handled by
  `LogArchiver`; out of scope for this brainstorm.
- **Switch to `structlog`.** Pulls in a dependency for what's effectively
  a custom Formatter; reject for now.
- **Emit JSON-lines by default.** Operationally great for ingestion, hostile
  for humans tailing files. Defer until/if we have a log shipper.
- **Color in the console formatter.** Worth doing eventually; not needed
  for the conciseness goal and risks corrupting the per-config log file
  (which is shared between human and machine readers).

## 3. Decision Outcome

### Chosen Approach

**Selected Solution:** **Option D — Formatter + redaction + targeted cleanup + lint rule.**

### Rationale

**Primary Factors:**

- The single-line invariant has to be enforced somewhere both *cheap* and
  *unmissable*. A formatter is both. A 169-site rewrite is neither.
- Redaction at the formatter layer eliminates an entire class of bug
  (the cluster_config-leak we just fixed by hand) without depending on
  every future caller doing the right thing.
- The lint rule is the cheapest possible regression test: it catches
  `print(json.dumps(..., indent=4))` and friends at commit time, not in
  an incident postmortem.
- The targeted cleanups for `manual.py:612`, `postprocess.py:123`, and
  `mediaprocessor.py:683` are the only sites where the formatter alone
  isn't sufficient (because they bypass the logger entirely or are read
  directly via the function's return value).

**Secondary Factors:**

- The `extra=` keyword path (Option C) remains the natural future
  direction; nothing in Option D blocks it. New code can opt in
  incrementally.
- Effort fits a single sprint; rollout is reviewable in 3–4 small commits.

## 4. Implementation Plan

### Subtasks

1. **`resources/log.py` — formatter + filter** *(M)*
   - Add `SingleLineFormatter(fmt, datefmt, max_width=1024)`:
     - Render the message via the parent formatter.
     - Replace any `\r?\n` in the application message with ` ⏎ `.
     - Detect JSON-looking substrings (regex around balanced `{…}` /
       `[…]`, parsed with `json.loads`, re-dumped with
       `separators=(",",":")`); on parse failure leave the substring
       intact.
     - Truncate the final string to `max_width`; append `…+N` indicating
       trimmed bytes when truncated.
     - When `record.exc_info` is present: emit the message line first,
       then traceback frames each on their own line prefixed with two
       spaces + `|` so they're visually grouped and greppable
       (`grep -v '^  |'` to drop them, `grep '^  |'` to isolate).
   - Add `RedactingFilter()`:
     - Walk `record.args` and `record.__dict__` for keys in `SECRET_KEYS`
       ∪ `SERVICE_SECRET_FIELDS`; replace with `"***"` in a deep copy.
     - Apply a final pass on the rendered message to mask
       `key=value` / `"key": "value"` patterns where `key` ∈ that set
       (catches secrets that traveled as message-formatted text).
   - Wire both into `getLogger()` so every consumer benefits without
     opt-in.

2. **Targeted offender cleanup** *(S)*
   - `manual.py:612` — switch `print(json.dumps(output, indent=4))` to
     `log.debug("conversion result", extra={"output": output})`.
     Update the daemon worker's stdout-capture logic if anything depended
     on parsing that block (a quick grep showed nothing does).
   - `resources/postprocess.py:123` — drop `indent=4`, switch to
     `extra={"env": …}`.
   - `resources/mediaprocessor.py:683` — return compact JSON. Verify the
     callers that use the return value (logging only, per scan).

3. **Pre-commit lint rule** *(S)*
   - Add `scripts/lint-logging.py` (Python, since the rule has to scan
     code blocks not lines):
     - AST-walk; for any `Call` whose attribute name is one of
       `info|debug|warning|error|exception|critical`, recurse into args
       to forbid `json.dumps(..., indent=…)` and reject any string
       constant containing `\n`.
     - Reject `print(` in the files listed earlier, with an allowlist of
       call-with-no-args / docstring-emitting helpers.
   - Add a hook entry in `.pre-commit-config.yaml`. CI already runs
     pre-commit, so this also gates PRs.

4. **Tests** *(S)*
   - `tests/test_log_formatter.py`:
     - Multi-line message → single line with ` ⏎ ` marker.
     - JSON-bearing message → compact JSON in output.
     - Width cap → truncated suffix.
     - `log.exception` → message line + indented frames.
     - Redaction: secret keys in `extra=` and in message text both
       become `***`.
   - `tests/test_lint_logging.py`:
     - Sample fixture that violates each rule fails the linter.
     - Sample fixture that doesn't violate passes.

5. **Docs** *(S)*
   - `docs/troubleshooting.md` — short "How log lines render" section.
   - `CLAUDE.md` — add a one-line rule under the existing rules section:
     "Don't use `print()` for diagnostics; don't pass `indent=` to a log
     call; one record == one line."
   - Wiki mirror.

### Sequencing

```text
PR 1: formatter + redacting filter + tests        (M)
PR 2: targeted offender cleanup                   (S)
PR 3: lint rule + CLAUDE.md/docs/wiki             (S)
```

PR 3 lands last so PR 1 and PR 2 don't have to dodge the rule they
introduce; once it lands, the rule prevents regressions.

## 5. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Formatter heuristic mis-renders a message that contains literal `{…}` text | Medium | Low | Tests cover non-JSON braces; on `json.loads` failure the substring is left untouched. |
| Width cap hides debugging detail mid-incident | Low | Medium | Configurable via `SMA_LOG_MAX_WIDTH`; DB log handler bypasses it (full record stored). |
| Redaction filter under-redacts (new secret-bearing field added later) | Medium | High | Centralise the redaction list in `resources.daemon.constants`; new fields are added there once and picked up everywhere. |
| Redaction filter over-redacts a non-secret field that shares a name | Low | Low | Match on full key, not substring; limit redaction to known service blocks. |
| Existing tests assert on log message text and break | Medium | Low | Run full suite after PR 1; update assertions to use `caplog.records` rather than rendered text where possible. |
| Per-config log file consumers (the dashboard `/logs/<config>` viewer) display the ` ⏎ ` marker oddly | Low | Low | Use a marker that's plain ASCII-friendly if needed (e.g. `␤` or just `\\n`); decide during PR 1. |

## 6. Resolved Decisions

These were the open questions; the implementer should treat the answers
below as binding and not rediscuss them in PR review.

- **Width cap:** 1024 bytes (configurable via `SMA_LOG_MAX_WIDTH`).
- **Multi-line marker glyph:** Unicode `⏎` (with surrounding spaces in
  the rendered string). Tests must include a non-ASCII assertion so
  encoding regressions surface immediately.
- **`manual.py:612`'s indented JSON dump:** nothing depends on it being
  parseable. Replace with a `log.debug("conversion result", extra={...})`
  call and drop the `print()` outright. No machine-mode flag.
- **Lint rule placement:** `pre-commit` + small standalone Python helper,
  not a custom `ruff` rule.

## 7. Next Steps

1. **Owner picks up PR 1** (formatter + redaction filter + tests).
2. **Operator review** of one converted log session in a staging-like run
   to confirm the visual style works.
3. **PR 2** lands the targeted cleanup.
4. **PR 3** turns on the lint rule and updates `CLAUDE.md` + wiki.
5. **Retro after PR 3** — count noisy lines per conversion before/after
   to confirm the operator metric.

## 8. References

- Concrete offender list, line-numbered, lives in §1 above.
- `resources/log.py` — current logger factory (single point of change for the formatter).
- `resources/daemon/db.py:PostgreSQLLogHandler` — DB log path that must keep
  receiving full records, not the truncated formatter output.
- `resources/daemon/constants.py:SECRET_KEYS`, `SERVICE_SECRET_FIELDS` —
  redaction key list, already centralised.
