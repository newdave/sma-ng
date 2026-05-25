# Task Breakdown: Per-Profile Priority Weight

STATUS: COMPLETE
PRP: [docs/prps/per-profile-priority-weight.md](../prps/per-profile-priority-weight.md)

## Overview

Six sequential tasks. Each is small (1–2 file touches max), additive,
and validated by an existing pytest target. Total estimated effort:
one focused 90-minute session.

## Critical Path

```text
T1 (schema) → T2 (PCM accessor) → T3 (db.py builder + claim integration)
                                      ↓
                                    T4 (worker plumbing)
                                      ↓
                                    T5 (sample + docs)
                                      ↓
                                    T6 (broad validation + commit + push + deploy)
```

T1–T5 are strictly ordered (each step's tests depend on the prior
step's code being present). T6 is the final gate.

---

## T1 — Schema field

### Files

- `resources/config_schema.py`

### Change

Add to `ProfileOverlay` next to `concurrency_cost`:

```python
priority_weight: int = 0
```

Inline comment explains additive semantics and zero-default identity.
No model validator needed — any integer is legal.

### Acceptance criteria (Given/When/Then)

- **Given** a profile config with `priority-weight: -10` in YAML
  **When** the schema parses it
  **Then** the resulting `ProfileOverlay.priority_weight == -10`.
- **Given** a profile config with no `priority-weight` key
  **When** the schema parses it
  **Then** `ProfileOverlay.priority_weight == 0`.
- **Given** any integer (positive, negative, zero)
  **When** the schema parses it
  **Then** no `ValidationError` is raised.

### Validate

```bash
venv/bin/python -m pytest tests/test_config_sample.py tests/test_fallback_policy.py -q
```

**Estimated effort**: 5 minutes.

---

## T2 — PathConfigManager accessor

### Files

- `resources/daemon/config.py`

### Change

Add `profile_priority_weights() -> dict[str, int]` immediately after
`profile_concurrency_costs()`. Mirror that helper's shape: always
include every defined profile (default 0), so callers don't need to
fall back to 0 for missing keys.

### Acceptance criteria

- **Given** three profiles (`hq=-10`, `rq=0`, `lq=5`)
  **When** `profile_priority_weights()` is called
  **Then** the returned dict is `{"hq": -10, "rq": 0, "lq": 5}`.
- **Given** a config with no `profiles:` block
  **When** the accessor is called
  **Then** an empty dict is returned (no exception).

### Validate

```bash
venv/bin/python -m pytest tests/test_daemon.py -q
```

**Estimated effort**: 10 minutes.

---

## T3 — db.py SQL builder + claim integration

### Files

- `resources/daemon/db.py`

### Change

1. New module-level helper `_priority_weight_sql_clause(profile_weights, *, is_sqlite)`
   that returns `(expression_string, [bound_params])`. When no
   non-zero weights are configured, returns `("priority", [])` —
   byte-identical to today's ORDER BY clause.
2. Extend both `SQLiteJobDatabase.claim_next_job` and
   `PostgreSQLJobDatabase.claim_next_job` signatures with
   `profile_weights: dict[str,int] | None = None`.
3. Substitute the builder's expression into the existing
   `ORDER BY priority DESC, created_at ASC` so it becomes
   `ORDER BY {expr} DESC, created_at ASC`. Prepend the builder's
   params at the correct position (before LIMIT/OFFSET; for Postgres,
   before the SELECT FOR UPDATE).
4. Extend the Postgres advisory-lock guard:

   ```python
   if profile_caps or profile_costs or profile_weights:
       cur.execute("SELECT pg_advisory_xact_lock(%s)", (_CAP_ADVISORY_LOCK_KEY,))
   ```

### Acceptance criteria

- **Given** `profile_weights={"hq": -10, "lq": 5}` and three pending
  jobs with equal row-priority and same `created_at` (one per profile)
  **When** the worker calls `claim_next_job` three times
  **Then** the order claimed is `lq` → `rq` → `hq`.
- **Given** `hq.priority_weight=-10` AND one hq job whose row
  `jobs.priority` was manually set to `+20`
  **When** competing against an lq job (priority=0, weight=5)
  **Then** the hq job claims first (effective 10 > effective 5).
- **Given** `profile_weights=None` (or every weight is 0)
  **When** the ORDER BY is built
  **Then** the emitted SQL contains `ORDER BY priority DESC,
  created_at ASC` and NO `CASE` expression — byte-identical to today.
- **Given** legacy NULL `request_profile` rows whose `args` carry
  `--profile hq`
  **When** ordering is applied with `hq.priority_weight=-10`
  **Then** those rows sort as if they were hq (the `CASE
  request_profile` matches the parsed-from-args profile via
  `_effective_profile`-style logic, OR — simpler — the SQL CASE
  matches against the `request_profile` column only and NULL-row
  ordering is treated as the default 0; the four ordering tests
  validate whichever choice is made).
- **Given** any of `profile_caps` / `profile_costs` / `profile_weights`
  is non-empty in the Postgres path
  **When** `claim_next_job` runs
  **Then** the cursor executes `SELECT pg_advisory_xact_lock(...)`
  before the candidate SELECT.

### Validate

```bash
venv/bin/python -m pytest tests/test_sqlite_db.py tests/test_daemon.py -q
```

**Estimated effort**: 30–40 minutes.

---

## T4 — Worker plumbing

### Files

- `resources/daemon/worker.py`

### Change

In the existing `try/except` block that fetches `profile_caps` /
`profile_costs` / `budget` from `path_config_manager`, also fetch
`profile_priority_weights()` and pass it through to `claim_next_job`
as `profile_weights=`. Keep the broad `except Exception` defensive
guard (preflight must never wedge the queue).

### Acceptance criteria

- **Given** `path_config_manager.profile_priority_weights()` returns
  `{"hq": -10, "lq": 5}`
  **When** the worker calls `claim_next_job`
  **Then** the kwarg `profile_weights={"hq": -10, "lq": 5}` is
  observable in the mock.
- **Given** `path_config_manager.profile_priority_weights()` raises
  **When** the worker tries to claim
  **Then** the claim still happens, `profile_weights` is `None`, and
  a debug-level log line is emitted.

### Validate

```bash
venv/bin/python -m pytest tests/test_worker.py -q
```

**Estimated effort**: 5 minutes.

---

## T5 — Sample + docs

### Files

- `scripts/generate_sma_ng_sample.py`
- `setup/sma-ng.yml.sample` (regenerated)
- `docs/configuration.md`
- `docs/daemon.md`

### Change

1. Generator's illustrative `hq` gets `priority-weight: -10`; `lq`
   gets `priority-weight: 5`. Regenerate the sample.
2. `docs/configuration.md`: new `profiles.<name>.priority-weight`
   subsection right after `concurrency-cost`. Worked example showing
   composition with `jobs.priority`:

   ```text
   Effective claim ordering = (jobs.priority + profile-weight) DESC,
                              created_at ASC

   With profiles.hq.priority-weight: -10 and a per-row priority bump
   of +15 on a specific hq job, the effective priority is +5 — beats
   any rq/lq job that has not had its row priority touched.
   ```

3. `docs/daemon.md`: extend the "Profile concurrency caps and the
   claim-time advisory lock" subsection's lead-in clause to mention
   weights too (`When any profile carries max-concurrent: N or
   concurrency-cost > 1 or priority-weight ≠ 0, ...`).

### Acceptance criteria

- **Given** the regenerated `setup/sma-ng.yml.sample`
  **When** `tests/test_config_sample.py` runs
  **Then** the sample is byte-identical to the generator output.
- **Given** `docs/configuration.md`
  **When** an operator searches for `priority-weight`
  **Then** they find the worked example with the composition
  formula.

### Validate

```bash
venv/bin/python -m pytest tests/test_config_sample.py tests/test_fallback_policy.py -q
mise run config:sample
```

**Estimated effort**: 15 minutes.

---

## T6 — Broad validation, commit, push, deploy

### Files

- (none — meta-task)

### Change

```bash
source venv/bin/activate && python -m pytest -q          # 3580+ green
mise run test:lint                                       # clean
python daemon.py --smoke-test                            # exit 0
```

If green:

1. `git add -u && git commit -m "feat(claim): per-profile priority weight..."`
2. `git push`
3. Wait for the GHA Docker workflow to finish.
4. Stamp `setup/local.yml` with the operator-side values:

   ```yaml
   profiles:
     hq:
       priority-weight: -10
     lq:
       priority-weight: 5
   ```

5. `mise run deploy:remote` — the fast-path verifier in deploy:docker
   will detect the new image digest and pull+recreate.
6. After deploy, watch the queue for ~10 minutes to confirm lq is
   claimed ahead of rq when both are pending.

### Acceptance criteria

- **Given** the full broad `pytest -q` run
  **When** it completes
  **Then** it reports `passed` with no `failed` (skipped is fine).
- **Given** the daemon is freshly deployed with the new image and
  `setup/local.yml` priority weights set
  **When** the queue contains one pending job of each profile
  **Then** the worker's `claim.skipped`-style log lines (or the
  resulting `/jobs?status=running` snapshot) show lq claimed first.

### Validate

```bash
source venv/bin/activate && python -m pytest -q
mise run test:lint
python daemon.py --smoke-test
```

**Estimated effort**: 10–15 minutes including the deploy.

---

## Total

- Six sequential tasks
- ~75–90 minutes end-to-end
- No parallelism opportunities (each step builds on the previous)
- Zero new external dependencies
- Zero database migrations (priority-weight lives entirely in YAML)
