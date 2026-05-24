# PRP: Weighted Concurrency Budget

STATUS: IN-FLIGHT

## Discovery Summary

### Initial Task Analysis

Refactor the per-profile concurrency cap shipped in `ab2ad73` so that
profiles compete for a shared per-node *encoder-capacity budget* instead
of independent hard slot counts. Operator intent expressed as
"1 hq = 3 rq = 6 lq" maps directly onto per-profile integer costs and a
per-node integer budget. Identical-to-today behaviour when costs and
budget are left at defaults.

### User Clarifications Received

- **Q**: Weighted budget (option A) vs. mutually-exclusive class slots
  (B) vs. token bucket (C)?
  **A**: Option A.
  **Impact**: Drives the data model — integer costs + integer budget +
  cost-sum check inside the existing advisory-lock transaction.
- **Q**: Budget default — `workers` (byte-identical fallback) or
  `workers × 2` (encoder headroom)?
  **A**: `workers`.
  **Impact**: Zero-config installs see no behaviour change; opt-in only.
- **Q**: Keep `profiles.<name>.max-concurrent` alongside the new budget?
  **A**: Yes — as a hard secondary ceiling. Composes; doesn't replace.

### Missing Requirements Identified

- Startup validation: refuse to start if any profile's
  `concurrency-cost > concurrency-budget` (would be unclaimable forever).
- Skipped-claim diagnostic log so operators can tell which gate
  (`budget` vs `max-concurrent`) fired when a job isn't claiming.

## Goal

Generalise today's per-profile `max-concurrent` cap into a shared
weighted-budget scheduler so:

- A node's encoder bandwidth is expressed once as `daemon.concurrency-budget`.
- Each profile carries a `concurrency-cost` integer; the claim path skips
  a candidate when accepting it would push `Σ running.cost > budget`.
- Operators can keep the existing per-profile hard ceiling for cases
  where encoder bandwidth isn't the binding constraint (e.g.
  output-disk saturation on hq).
- Zero-config installs (no costs/budget set) behave byte-identically to
  today's `max-concurrent`-only model.

## Why

- The current per-profile cap has an *idle-worker pathology*: with three
  workers and `lq.max-concurrent: 4`, the node can still only run 3 lq
  jobs concurrently, but during an hq run rq and lq workers stay
  uncoordinated with the encoder bandwidth actually consumed.
- Operators already think in weight-classes. "1 hq = 3 rq = 6 lq" is a
  sentence they say out loud; the config should mirror that sentence.
- Cluster-mode story stays clean: profile costs are global config (every
  node agrees on the relative weight of each profile), budgets are
  per-node (every node sets its own ceiling based on its own iGPU).
- The infrastructure landed earlier today (`d419084` advisory lock,
  `461fbb2` args-derived profile, `ba16bfc` requeue backfill) is already
  the right shape for the cost-sum check — no new primitives needed.

## What

### User-visible behaviour

**New config keys:**

```yaml
daemon:
  concurrency-budget: 6        # per-node; null/unset → workers

profiles:
  hq:
    concurrency-cost: 6        # 1 hq saturates a budget=6 node
    max-concurrent: 1          # keep — belt + suspenders
  rq:
    concurrency-cost: 2        # 3 rq fills the budget
  lq:
    concurrency-cost: 1        # 6 lq fills the budget
```

**Claim semantics (the tightest cap wins):**

1. `count(running jobs) ≤ daemon.workers` (today; unchanged).
2. `count(running jobs with profile P) ≤ profiles.P.max-concurrent`
   (today; from `ab2ad73`).
3. `Σ(running.concurrency-cost) + this_job.concurrency-cost ≤ daemon.concurrency-budget`
   (new).

**Diagnostic log on a skipped candidate** — emitted at INFO inside the
claim transaction, single-line structured:

```json
{"event":"claim.skipped","reason":"budget","profile":"hq","cost":6,"budget":6,"in_use":2}
{"event":"claim.skipped","reason":"max_concurrent","profile":"hq","running":1,"cap":1}
```

**Startup validation:** refuse to start with a clear ERROR if any
profile has `concurrency-cost > concurrency-budget`:

```text
ERROR Profile 'hq' has concurrency-cost=6 but daemon.concurrency-budget=4;
      this profile would be unclaimable forever. Either lower the cost
      or raise the budget.
```

### Success Criteria

- [ ] With `hq.concurrency-cost: 6` + `daemon.concurrency-budget: 6` and
      one hq running, *no other job of any profile* gets claimed until
      hq completes (proven by SQLite test using `_profiles_at_cap`-style
      assertions).
- [ ] With `budget=6` and only lq queued (`cost=1`), up to 6 lq claimed
      concurrently — bounded only by `workers` and `lq.max-concurrent`.
- [ ] Zero-config install (no costs/budget set) shows byte-identical
      claim behaviour to today's `max-concurrent`-only model.
- [ ] The `pg_advisory_xact_lock` from `d419084` wraps the cost-sum
      computation; concurrent claims can't both pass the budget check
      on the same available slot (proven by Postgres mock test asserting
      the lock is taken before the SELECT).
- [ ] Startup refuses to boot on `cost > budget`; structured ERROR log
      names the offending profile + both values; daemon exit code is
      non-zero (proven by `tests/test_daemon.py` schema-validation test).
- [ ] `pytest -q` stays green at the current count (3569+ tests passing).
- [ ] `mise run test:lint` clean.

## All Needed Context

### Research Phase Summary

Already explored in `docs/brainstorming/2026-05-24-weighted-concurrency-budget.md`.
Key findings:

- The cost-sum check is the same shape of query as the existing
  `_profiles_at_cap()` — `SELECT request_profile, args FROM jobs WHERE status='running'`
  followed by a Python-side aggregation. Already runs inside the
  advisory-lock transaction.
- Profile resolution via `_effective_profile()` (returns
  `request_profile` column or args-parsed `--profile`) already
  handles legacy NULL rows. The cost lookup uses the same accessor.
- Config-side: `ProfileOverlay.max_concurrent` is the precedent for
  the new `concurrency_cost` field. `DaemonConfig.storage_janitor_*`
  is the precedent for the new `concurrency_budget` field with `None`
  semantics.

### Files to Touch

| File | Change |
| ---- | ------ |
| `resources/config_schema.py` | Add `ProfileOverlay.concurrency_cost: int = 1`. Add `DaemonConfig.concurrency_budget: int \| None = None`. Add a `model_validator(mode='after')` on `SmaConfig` that raises if any profile's cost exceeds the resolved budget (use `workers` from the validated DaemonConfig when budget is None). |
| `resources/daemon/config.py` | Add `PathConfigManager.profile_concurrency_costs() -> dict[str, int]` (mirror of `profile_concurrency_caps`) and `PathConfigManager.concurrency_budget -> int` property (returns `_cfg.daemon.concurrency_budget or _cfg.daemon.workers`). |
| `resources/daemon/db.py` | Extend `claim_next_job` (both SQLite + Postgres backends) with `profile_costs: dict[str, int] \| None = None` and `concurrency_budget: int \| None = None` kwargs. New helper `_budget_exhausted_profiles(conn, profile_costs, budget, *, is_sqlite)` returns the set of profile names whose cost would push the cluster-wide running cost-sum over `budget`. Union that set with `_profiles_at_cap`'s output before the SELECT-FOR-UPDATE filter. The advisory-lock block already in place serialises the count + claim. |
| `resources/daemon/worker.py` | Thread `profile_costs` + `concurrency_budget` through the `claim_next_job` call site (same shape as today's `profile_caps`). On a skipped candidate, log the structured `{"event":"claim.skipped",...}` line — gate the log behind `_skipped_log_throttle` so a backed-up queue doesn't flood logs. |
| `setup/sma-ng.yml.sample` | Regenerate via `mise run config:sample`. The illustrative `hq` profile in `scripts/generate_sma_ng_sample.py` carries `concurrency-cost: 6` so future operators see the pattern. |
| `setup/local.yml` | Operator-side (gitignored). Stamp `hq.cost=6 / rq.cost=2 / lq.cost=1 / budget=6` on sma-master. |
| `docs/configuration.md` | New `daemon.concurrency-budget` and `profiles.<name>.concurrency-cost` subsections. Document the interaction with `max-concurrent` (tightest cap wins) and the cost > budget validation. |
| `docs/daemon.md` | Extend the existing "Profile concurrency caps and the claim-time advisory lock" subsection with a "Weighted budget" paragraph and a "Why a job isn't claiming" diagnostic-log reference. |
| `tests/test_sqlite_db.py` | Six new cases: cost-sum blocks a second claim; mixed-profile cost-sum (1 rq + 1 lq within budget passes, 1 hq blocks all); zero-config identity (no cost/budget set behaves like today); legacy NULL rows count via `_effective_profile`; budget=None resolves to workers; `max-concurrent` fires before budget when both would gate (tightest wins). |
| `tests/test_daemon.py` | Two new Postgres mock cases: advisory lock taken before the cost-sum SELECT; cost > budget at config load raises `ConfigError`. |
| `tests/test_config_sample.py` | Updated automatically by sample regen — sample-sync drift guard. |

### Codebase References

- `resources/daemon/db.py:95` — `_CAP_ADVISORY_LOCK_KEY` (the lock the
  new check reuses).
- `resources/daemon/db.py:_profiles_at_cap` — pattern to mirror for
  `_budget_exhausted_profiles`.
- `resources/daemon/db.py:_effective_profile` — profile resolution
  helper that handles legacy NULL rows.
- `resources/daemon/config.py:profile_concurrency_caps` — pattern to
  mirror for `profile_concurrency_costs` + `concurrency_budget`.
- `resources/daemon/worker.py` — claim site that already threads
  `profile_caps` through; mirror for `profile_costs` + budget.
- `tests/test_sqlite_db.py:test_profile_cap_skips_pending_when_running_count_reached`
  — pattern to mirror for the budget tests.

### Related Commits (Today's Session)

- `ab2ad73` — initial per-profile `max-concurrent` cap (the thing this
  refactor generalises; stays in place as the hard secondary ceiling).
- `d419084` — `pg_advisory_xact_lock` wrapping the count + claim (the
  race-safety guard the new cost-sum check rides on).
- `461fbb2` — derive profile from args (lets legacy NULL-profile rows
  participate in the cost-sum the same way they participate in the cap).
- `ba16bfc` — backfill `request_profile` on every requeue path (so
  retried jobs carry their cost correctly).

## Implementation Blueprint

### Step 1 — Schema

1. Add to `ProfileOverlay` (`resources/config_schema.py`, near
   `max_concurrent`):

   ```python
   concurrency_cost: int = 1
   ```

   Comment: explain "weight in the per-node concurrency budget; default
   1 means every profile counts equally; pair with
   `daemon.concurrency-budget`."

2. Add to `DaemonConfig`:

   ```python
   concurrency_budget: int | None = None
   ```

   Comment: explain "per-node encoder-capacity ceiling; None defaults
   to `workers` at runtime; refuse to start if any profile's
   `concurrency-cost` exceeds it."

3. Add a `model_validator(mode='after')` on `SmaConfig` that resolves
   `effective_budget = daemon.concurrency_budget or daemon.workers` and
   raises `ValueError` if any `overlay.concurrency_cost > effective_budget`.
   Message: `Profile %r has concurrency-cost=%d but the effective budget is %d; lower the cost or raise daemon.concurrency-budget.`

**Validate:** `source venv/bin/activate && python -m pytest tests/test_config_sample.py tests/test_fallback_policy.py -q`

### Step 2 — PathConfigManager projection

Mirror the existing `profile_concurrency_caps()`:

```python
def profile_concurrency_costs(self) -> dict[str, int]:
  if self._cfg is None or self._cfg.profiles is None:
    return {}
  return {name: int(getattr(o, "concurrency_cost", 1) or 1)
          for name, o in self._cfg.profiles.items()}

@property
def concurrency_budget(self) -> int:
  if self._cfg is None:
    return 0
  return int(self._cfg.daemon.concurrency_budget or self._cfg.daemon.workers or 0)
```

**Validate:** `source venv/bin/activate && python -m pytest tests/test_daemon.py -q`

### Step 3 — db.py cost-sum helper + claim integration

1. Add module-level helper near `_profiles_at_cap`:

   ```python
   def _budget_exhausted_profiles(conn, profile_costs, budget, *, is_sqlite):
     """Return {profile} whose cost would push Σ running cost > budget."""
     if not profile_costs or not budget or budget <= 0:
       return set()
     # SELECT request_profile, args FROM jobs WHERE status = running
     # ... reuse _effective_profile to resolve profile per row ...
     # sum the costs of currently-running jobs (default cost=1 for
     # profiles not in profile_costs).
     # Return {p for p, cost in profile_costs.items()
     #         if running_total + cost > budget}.
   ```

2. Extend both `SQLiteJobDatabase.claim_next_job` and
   `PostgreSQLJobDatabase.claim_next_job` to accept
   `profile_costs=None, concurrency_budget=None`. Compute the union
   `over_capped = _profiles_at_cap(...) | _budget_exhausted_profiles(...)`
   and apply that to the existing skip filter — no other SQL changes
   needed.

3. On the Postgres side, the existing
   `if profile_caps: cur.execute("SELECT pg_advisory_xact_lock(%s)", ...)`
   block becomes:

   ```python
   if profile_caps or profile_costs:
     cur.execute("SELECT pg_advisory_xact_lock(%s)", (_CAP_ADVISORY_LOCK_KEY,))
   ```

**Validate:** `source venv/bin/activate && python -m pytest tests/test_sqlite_db.py tests/test_daemon.py -q`

### Step 4 — Worker plumbing + diagnostic log

1. In `resources/daemon/worker.py` `run()` loop, replace:

   ```python
   profile_caps = self.path_config_manager.profile_concurrency_caps()
   ```

   with:

   ```python
   try:
     profile_caps = self.path_config_manager.profile_concurrency_caps()
     profile_costs = self.path_config_manager.profile_concurrency_costs()
     budget = self.path_config_manager.concurrency_budget
   except Exception:
     self.log.debug("profile_caps/costs unavailable; ignoring caps", exc_info=True)
     profile_caps = profile_costs = None
     budget = None

   job = self.job_db.claim_next_job(
     self.worker_id, self.node_id,
     exclude_configs=locked or None,
     profile_caps=profile_caps or None,
     profile_costs=profile_costs or None,
     concurrency_budget=budget or None,
   )
   ```

2. Add a throttled skip-claim diagnostic in db.py
   `claim_next_job` when `over_capped` is non-empty AND the returned
   row is None (i.e. work was queued but capped). One INFO log per
   30 seconds per (reason, profile) tuple. Throttle state lives on
   the db instance to keep claim_next_job stateless.

**Validate:** `source venv/bin/activate && python -m pytest tests/test_worker.py tests/test_daemon.py -q`

### Step 5 — Sample + operator config + docs

1. `scripts/generate_sma_ng_sample.py` — illustrative `hq` profile
   carries `concurrency-cost: 6`. Run `mise run config:sample`.
2. `setup/local.yml` (gitignored) on sma-master gets
   `daemon.concurrency-budget: 6` + `hq.concurrency-cost: 6`,
   `rq.concurrency-cost: 2`, `lq.concurrency-cost: 1`.
3. `docs/configuration.md` — new subsections under `daemon` and
   `profiles.<name>` with the example table from the brainstorming doc.
4. `docs/daemon.md` — extend the "Profile concurrency caps" section
   with the budget paragraph + the diagnostic-log reference.

**Validate:** `source venv/bin/activate && python -m pytest -q`

### Step 6 — Final pass

```bash
source venv/bin/activate && python -m pytest -q
mise run test:lint
python daemon.py --smoke-test
```

Commit + push. Wait for CI Docker build. `mise run deploy:config && mise run deploy:reload` (hot-reload — no container recreate needed because nothing in the running container's behaviour changes without the config carrying new keys).

## Validation Matrix

| Test surface | Command |
| ------------ | ------- |
| Schema + validator | `venv/bin/python -m pytest tests/test_config_sample.py tests/test_fallback_policy.py -q` |
| SQLite claim | `venv/bin/python -m pytest tests/test_sqlite_db.py -q` |
| Postgres claim (mocked) | `venv/bin/python -m pytest tests/test_daemon.py -q` |
| Worker plumbing | `venv/bin/python -m pytest tests/test_worker.py -q` |
| Broad pass | `venv/bin/python -m pytest -q` |
| Lint | `mise run test:lint` |
| Smoke | `python daemon.py --smoke-test` |

Coverage policy: per CLAUDE.md, global line coverage stays at ≥90% and
no touched production module ≥100 stmts drops below 70%. The new
helpers are <40 lines combined; existing test patterns extend cleanly.

## Risks & Mitigations

- **Extra SQL per claim** — Mitigation: early-out when
  `not profile_costs or budget <= 0`. Same `jobs WHERE status='running'`
  scan the cap query already hits; index reuse is automatic.
- **Operator confusion between two cap mechanisms** — Mitigation:
  structured skip-claim log names which gate fired; docs include a
  decision table; per-profile `max-concurrent` and `concurrency-cost`
  live next to each other in `setup/local.yml` with comments.
- **Misconfigured cost > budget renders a profile unclaimable** —
  Mitigation: startup validator refuses to boot with a clear ERROR.

## Out of Scope (Phase 2+)

- Auto-detected budget from iGPU probe at startup (defer until we have
  a second node type to compare against).
- Priority-weighted claim ordering (separate from capacity; new ticket
  when SLA-style routing becomes a need).
- Prometheus gauges (`sma_concurrency_budget_total`,
  `sma_concurrency_budget_in_use`) — small additive change but only
  worth doing if real traffic shows the budget saturating.
- `GET /jobs?summary=concurrency` admin endpoint — same reason; build
  the gauges first, build the endpoint if anyone asks.
