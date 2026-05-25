# PRP: Per-Profile Priority Weight

STATUS: IN-FLIGHT

## Discovery Summary

### Initial Task Analysis

Add a per-profile *priority weight* knob so that when two pending jobs
of different profiles are eligible for claim, the worker picks the
higher-weighted profile first. Today `claim_next_job` orders strictly
by `(jobs.priority DESC, jobs.created_at ASC)` — re-ordering the
dashboard's display via the new `?sort=` does not influence claim
order (confirmed in commit `8606e79`'s scope review). This PRP makes
the operator-visible "1 hq = 3 rq = 6 lq" intent extend from
*capacity* (already shipped: per-profile caps + weighted budget) to
*ordering*.

### User Clarifications (not asked — derivable from prior work)

- **Naming**: `profiles.<name>.priority-weight` (schema field
  `priority_weight`). Avoids collision with the per-row `jobs.priority`
  column that operators tune via the ▲/▼ buttons.
- **Semantics**: weight is **added** to the row's per-job priority at
  ORDER BY time. Effective ordering becomes
  `(jobs.priority + profile_weight(request_profile)) DESC, created_at ASC`.
  Operators can still bump an individual job up/down by adjusting its
  row priority — the profile weight is a default bias, not a hard
  override.
- **Defaults**: every profile defaults to `priority-weight: 0`, which
  reproduces today's ordering exactly (additive identity). Zero-config
  installs see no behaviour change.
- **Composition with caps/budget**: the weight only affects *ordering*
  among already-claimable candidates. The cap and budget filters
  (`profile_caps`, `profile_costs`, `concurrency_budget`) still gate
  what's claimable. So `hq.priority-weight: 100` does **not** override
  `hq.max-concurrent: 1` — it just means "when hq is claimable, it
  outranks every other profile in the ordering tie-breaker".

### Missing Requirements (none — additive, identity-default)

## Goal

Let operators set `profiles.<name>.priority-weight: int` so that
`claim_next_job` picks higher-weighted profiles ahead of lower-weighted
ones when both are claimable, while preserving:

- today's `(jobs.priority DESC, created_at ASC)` ordering when no
  weights are configured;
- the existing cap/budget skip semantics (weight ordering only matters
  among rows that pass those filters);
- the per-row `jobs.priority` column as the *finer* operator knob (a
  single job with priority=5 still wins against a profile-weighted
  peer with priority=0 unless the weight difference exceeds 5).

## Why

- Today operators can express "limit 4K to one at a time" (caps) and
  "4K saturates the encoder budget" (cost). They can't yet express
  "prefer to drain Kids quickly so the family can watch tonight" or
  "rip 4K HDR Movies as the LAST thing in the queue". Both are
  legitimate scheduling intents that map cleanly onto a per-profile
  weight.
- The infrastructure is already in place: `claim_next_job` already
  threads `profile_caps` and `profile_costs` dicts from
  `PathConfigManager`; the Postgres advisory lock already guards
  per-claim ordering decisions; the args-derived effective profile
  (`_effective_profile`) lets legacy NULL-column rows participate
  identically.
- The dashboard's existing per-row priority controls keep working
  unchanged — weight is just a tunable default that shifts the curve
  for everything in a profile.

## What

### User-visible behaviour

**New config key:**

```yaml
profiles:
  hq:
    priority-weight: -10        # de-prioritise 4K transcodes
  lq:
    priority-weight: 5          # claim kids content first
```

**Claim semantics (unchanged where weights are zero):**

```text
ORDER BY (jobs.priority + COALESCE(profile_weight, 0)) DESC,
         jobs.created_at ASC
```

`profile_weight` is resolved via a CASE expression built from the
config-time `{profile_name: weight}` dict. Profiles not in the dict
contribute 0. The expression is bound via parameters, never string-
interpolated — same pattern as `_profiles_at_cap`'s IN-list.

**Compose with everything that already exists:**

1. `workers` cap (today; unchanged).
2. `profiles.<name>.max-concurrent` (today; unchanged).
3. `concurrency-budget` + `concurrency-cost` (today; unchanged).
4. **`priority-weight` shifts the ORDER BY among rows that pass 1-3.**

A row can be the highest-weighted pending profile and still not claim
if its profile is at-cap or over-budget. Caps win over weight; weight
only matters when the candidate is already eligible.

### Success Criteria

- [ ] With `hq.priority-weight: -10`, `lq.priority-weight: 5`, and
      three pending jobs (one of each profile, all `jobs.priority=0`,
      same `created_at`), the lq job claims first, then rq, then hq.
- [ ] With `hq.priority-weight: -10` AND one hq job whose
      `jobs.priority` was manually bumped to `+20`, that hq job still
      outranks both rq (priority=0, weight=0 → effective 0) and lq
      (priority=0, weight=5 → effective 5), because effective is 10.
      The per-row override still beats the profile default.
- [ ] Zero-config (no `priority-weight` set anywhere) produces
      byte-identical claim ordering to today's
      `(priority DESC, created_at ASC)`.
- [ ] Legacy NULL `request_profile` rows fall back to args-parsing via
      `_effective_profile`; if a NULL row's args don't yield a profile
      it counts as weight 0.
- [ ] The Postgres claim path takes the existing
      `pg_advisory_xact_lock` when *any* of caps / costs / weights are
      configured, so the ORDER BY's view of running state can't race a
      concurrent claim.
- [ ] `pytest -q` stays green at the current count; new tests cover
      the four ordering cases above.
- [ ] `mise run test:lint` clean.

## All Needed Context

### Files to Touch

| File | Change |
| ---- | ------ |
| `resources/config_schema.py` | Add `ProfileOverlay.priority_weight: int = 0`. No model validator needed — any integer is meaningful (negative de-prioritises). |
| `resources/daemon/config.py` | New `PathConfigManager.profile_priority_weights() -> dict[str, int]`, mirror of `profile_concurrency_costs`. Returns `{name: weight}` for every defined profile (default 0); empty dict when no `profiles:` block. |
| `resources/daemon/db.py` | (1) New `_priority_weight_sql_clause(profile_weights, is_sqlite)` builder that returns a `(sql_fragment, params)` pair where the fragment is e.g. `(priority + CASE request_profile WHEN ? THEN ? WHEN ? THEN ? ELSE 0 END)`; (2) Both `SQLiteJobDatabase.claim_next_job` and `PostgreSQLJobDatabase.claim_next_job` accept `profile_weights: dict[str,int] \| None = None`, build the ORDER BY clause from the helper, and prepend it to the existing `created_at ASC`. Default ORDER BY (when weights dict is empty or all zero) remains `priority DESC, created_at ASC` — byte-identical. (3) Postgres advisory-lock guard extended to fire when `profile_weights` is non-empty too (so the ordering decision races safely). |
| `resources/daemon/worker.py` | Thread `profile_weights` through alongside `profile_caps` / `profile_costs` in the `claim_next_job` invocation. Tolerant `try/except` already in place. |
| `setup/sma-ng.yml.sample` | Generator's illustrative `hq` profile gets `priority-weight: -10` and `lq` gets `+5` so future operators see the pattern. Regenerate via `mise run config:sample`. |
| `docs/configuration.md` | Extend the existing `profiles.<name>.max-concurrent` / `concurrency-cost` section with a `priority-weight` subsection. Worked example showing how weight composes with row-priority. |
| `docs/daemon.md` | Extend "Profile concurrency caps and the claim-time advisory lock" subsection: note that the advisory lock now also fires for weight ordering, and that weights only affect ordering among claimable rows. |
| `tests/test_sqlite_db.py` | Five new cases: (a) zero-weight identity (today's order preserved); (b) negative weight pushes a profile to the back; (c) positive weight pulls a profile forward; (d) row-priority overrides profile weight when difference > weight; (e) legacy NULL `request_profile` counted via args parsing. |
| `tests/test_daemon.py` | Two new Postgres-mock cases mirroring the SQLite ones: advisory lock fires when only weights configured; ORDER BY contains the CASE expression. |

### Codebase References (mirror these patterns exactly)

- `resources/daemon/db.py:_CAP_ADVISORY_LOCK_KEY` and the
  `pg_advisory_xact_lock(_CAP_ADVISORY_LOCK_KEY)` invocations inside
  both `claim_next_job` paths — extend the existing `if profile_caps
  or profile_costs:` guard to `or profile_weights`.
- `resources/daemon/db.py:_profiles_at_cap` — parametrised SQL
  builder that operates on a `{profile: int}` dict. The new
  `_priority_weight_sql_clause` mirrors its IN-list construction.
- `resources/daemon/db.py:_effective_profile` — already returns the
  request_profile column or args-parsed `--profile`; reuse.
- `resources/daemon/config.py:profile_concurrency_caps` and
  `profile_concurrency_costs` — pattern to copy verbatim for
  `profile_priority_weights`.
- `tests/test_sqlite_db.py::test_profile_cap_skips_pending_when_running_count_reached`
  and `test_budget_blocks_second_claim_when_cost_exhausts` — pattern
  to copy for the new ordering tests.

### Related Commits (already-landed infra this PRP rides on)

- `ab2ad73` — per-profile `max-concurrent` cap.
- `d419084` — `pg_advisory_xact_lock` wrapping count + claim.
- `461fbb2` — derive profile from args (so legacy NULL rows count).
- `ba16bfc` — backfill `request_profile` on every requeue path.
- `69a4772` — weighted-budget scheduler (the `profile_costs` and
  `concurrency_budget` plumbing this PRP follows).
- `8606e79` — display-only queue sort (the work this PRP complements
  by extending sorting to the claim path).

## Implementation Blueprint

### Step 1 — Schema

Add to `ProfileOverlay` next to `max_concurrent` / `concurrency_cost`:

```python
# Additive bias applied to every job in this profile at claim ORDER BY
# time. Effective claim ordering is
#   (jobs.priority + profile_weight) DESC, created_at ASC
# so a positive weight pulls the profile forward and a negative weight
# pushes it back. Default 0 reproduces the historical claim order.
# Composes with the per-row `jobs.priority` column (the ▲/▼ controls in
# the dashboard); whichever sum wins. No effect on caps/budget — a
# profile that is at-cap or over-budget is skipped regardless of weight.
priority_weight: int = 0
```

**Validate:** `venv/bin/python -m pytest tests/test_config_sample.py tests/test_fallback_policy.py -q`

### Step 2 — PathConfigManager

Mirror `profile_concurrency_costs`:

```python
def profile_priority_weights(self) -> dict[str, int]:
    if self._cfg is None or self._cfg.profiles is None:
        return {}
    return {
        name: int(getattr(o, "priority_weight", 0) or 0)
        for name, o in self._cfg.profiles.items()
    }
```

**Validate:** `venv/bin/python -m pytest tests/test_daemon.py -q`

### Step 3 — db.py helpers + ORDER BY composition

1. Add the SQL builder near `_profiles_at_cap`:

   ```python
   def _priority_weight_sql_clause(profile_weights, *, is_sqlite):
       """Return (clause, params) for the additive priority-weight expr.

       clause is either '(priority + 0)' (when no non-zero weights are
       configured — byte-identical to today's `priority` column alone)
       or a CASE expression that adds each profile's weight to the row
       priority. Parameters are bound — profile names + weights never
       reach the SQL planner as literals.
       """
       if not profile_weights:
           return "priority", []
       non_zero = {k: int(v) for k, v in profile_weights.items() if v}
       if not non_zero:
           return "priority", []
       qmark = "?" if is_sqlite else "%s"
       whens = " ".join(
           "WHEN " + qmark + " THEN " + qmark for _ in non_zero
       )
       expr = "(priority + CASE request_profile " + whens + " ELSE 0 END)"
       params = []
       for name, weight in non_zero.items():
           params.append(name)
           params.append(weight)
       return expr, params
   ```

2. Apply in both `claim_next_job` paths:

   - Build the priority-clause + params first.
   - Substitute into the existing `ORDER BY priority DESC, created_at ASC`
     → `ORDER BY {priority_clause} DESC, created_at ASC`.
   - Prepend the params to the existing param list at the right
     position (before LIMIT / OFFSET).

3. Postgres advisory-lock guard:

   ```python
   if profile_caps or profile_costs or profile_weights:
       cur.execute("SELECT pg_advisory_xact_lock(%s)", (_CAP_ADVISORY_LOCK_KEY,))
   ```

**Validate:** `venv/bin/python -m pytest tests/test_sqlite_db.py tests/test_daemon.py -q`

### Step 4 — Worker plumbing

```python
profile_weights = None
try:
    profile_caps = self.path_config_manager.profile_concurrency_caps()
    profile_costs = self.path_config_manager.profile_concurrency_costs()
    profile_weights = self.path_config_manager.profile_priority_weights()
    budget = self.path_config_manager.concurrency_budget
except Exception:
    self.log.debug("profile cap/cost/weight/budget unavailable; ignoring", exc_info=True)
    profile_caps = profile_costs = profile_weights = None
    budget = None
job = self.job_db.claim_next_job(
    self.worker_id,
    self.node_id,
    exclude_configs=locked or None,
    profile_caps=profile_caps or None,
    profile_costs=profile_costs or None,
    profile_weights=profile_weights or None,
    concurrency_budget=budget or None,
)
```

**Validate:** `venv/bin/python -m pytest tests/test_worker.py -q`

### Step 5 — Sample + docs

Generator's illustrative profiles:

```python
"hq": {..., "priority-weight": -10, ...},
"lq": {..., "priority-weight": 5, ...},
```

`docs/configuration.md` adds a `profiles.<name>.priority-weight`
subsection right after the existing `concurrency-cost` one, with a
worked example showing the composition with `jobs.priority`.
`docs/daemon.md`'s advisory-lock subsection notes that weights also
serialise through the same lock.

**Validate:** `venv/bin/python -m pytest -q`

### Step 6 — Final pass

```bash
source venv/bin/activate && python -m pytest -q
mise run test:lint
python daemon.py --smoke-test
```

Commit, push, wait for CI Docker build, deploy via
`mise run deploy:remote` (the fast-path will detect that the image
digest has moved and pull + recreate; the deploy:config that runs
first stamps the new `priority-weight` keys into the live yml).

## Validation Matrix

| Test surface | Command |
| ------------ | ------- |
| Schema | `venv/bin/python -m pytest tests/test_config_sample.py tests/test_fallback_policy.py -q` |
| SQLite claim | `venv/bin/python -m pytest tests/test_sqlite_db.py -q` |
| Postgres claim (mocked) | `venv/bin/python -m pytest tests/test_daemon.py -q` |
| Worker plumbing | `venv/bin/python -m pytest tests/test_worker.py -q` |
| Broad pass | `venv/bin/python -m pytest -q` |
| Lint | `mise run test:lint` |
| Smoke | `python daemon.py --smoke-test` |

Coverage policy: per `CLAUDE.md`, global line coverage stays at ≥90%
and no touched production module ≥100 stmts drops below 70%. The new
helper is <40 lines; existing patterns extend cleanly.

## Risks & Mitigations

- **CASE-WHEN cost at scale.** For a small profile set (the 3 we have)
  the CASE evaluates in constant time per row; the additional ORDER BY
  cost is negligible against the existing index on `(status, created_at)`.
  Mitigation: only emit the CASE when at least one weight is non-zero
  (zero-weight identity short-circuits to plain `priority`).
- **Operator confusion: two priority knobs.** "Why didn't my +5 row
  priority win against `hq.priority-weight: -10`?" Mitigation: docs
  spell out the composition (effective = row + profile-weight). The
  dashboard's existing priority badge tooltip is extended to show
  effective when a profile weight is configured (out-of-scope phase 2).
- **Negative weights causing perpetual starvation.** If
  `hq.priority-weight: -1000` is set and any other profile is always
  queued, hq could never claim. Mitigation: weight is *additive*, not
  multiplicative; a single hq job with `jobs.priority: 1001` still
  claims. Documented as expected behaviour, not a bug.

## Out of Scope (phase 2+)

- Dashboard rendering of effective priority (badge tooltip extension).
- Time-decay weights (e.g. "boost the weight of jobs older than X").
- Per-routing-rule weight overrides (right now weight is profile-wide;
  a future operator might want "TV Kids gets +5 but Movie Kids gets 0").

## Task Breakdown

See `docs/tasks/per-profile-priority-weight.md` for the actionable
sprint breakdown.

## PRP Confidence Score

**9/10.** The infrastructure landed today (`ab2ad73`, `d419084`,
`461fbb2`, `ba16bfc`, `69a4772`, `8606e79`) makes this a near-mechanical
addition: the helper-and-kwarg pattern is already established three
times over, the advisory-lock guard already exists, and the
`_effective_profile` legacy-row handler is reusable as-is. The only
risk vector is the SQL CASE-WHEN composition, which is well-bounded
by the parameterised builder and covered by the five new ordering
tests. One point reserved for any surprise from the Postgres ORDER BY

- FOR UPDATE SKIP LOCKED interaction at scale, which can only be
fully validated by deploying and watching the live queue.
