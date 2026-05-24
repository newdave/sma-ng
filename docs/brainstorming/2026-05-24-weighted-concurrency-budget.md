# Feature Brainstorming Session: Weighted Concurrency Budget

**Date:** 2026-05-24
**Session Type:** Technical Design (refactor of existing per-profile cap)

## 1. Context & Problem Statement

### Problem Description

The per-profile concurrency cap shipped earlier today (`profiles.<name>.max-concurrent`)
expresses scheduling intent as hard, *mutually-independent* slot counts:

| profile | current cap | actual GPU/encoder load                                    |
| ------- | ----------- | ---------------------------------------------------------- |
| `hq`    | 1           | 4K HEVC, lookahead=60, async=8 — saturates the Xe-LPG iGPU |
| `rq`    | 3           | 1080p HEVC, lookahead=40, async=6 — ~⅓ of an hq job        |
| `lq`    | 4           | 1080p HEVC VDENC speed-first — ~⅙ of an hq job             |

The flaw: the caps are independent. With three workers on sma-master, an
idle node with no hq queued and 50 rq jobs queued will only ever run 3
rq simultaneously, even though the encoder could handle more. Worse,
when an hq job *is* running, the rq/lq caps don't reduce — they can
still launch jobs that contend for the same encoder bandwidth the hq
job already monopolises.

Operationally:

- Today the live numbers ratio out to roughly **1 hq ≈ 3 rq ≈ 6 lq** in
  encoder cost.
- Operators want one knob ("budget") that expresses node capacity, and
  per-profile "cost" weights that map work to that budget.
- The current cap should remain available as a hard secondary ceiling
  (e.g. "never more than one hq even if budget allows", for output-disk
  saturation reasons), but not be the only mechanism.

### Target Users

- **Primary Users:** Daemon operators tuning a single node (sma-master)
  whose hardware mix (Meteor Lake Xe-LPG, 3 worker threads, shared
  `/transcodes/sma`) doesn't fit a one-cap-per-profile model.
- **Secondary Users:** Future multi-node cluster operators where each
  node has different encoder capacity (Arc dGPU vs iGPU vs CPU-only) —
  per-node budgets, global profile costs.

### Success Criteria

- **Technical Metrics:**
  - A node running only lq jobs claims up to `budget / lq.cost` of them
    concurrently (vs. the current hard `lq.max-concurrent`).
  - A running hq job blocks any other claim that would push
    `Σ running.cost > budget`.
  - `claim_next_job` race-safety preserved (existing `pg_advisory_xact_lock`
    extends to cost-sum check).
  - Default config (no costs/budget set) behaves identically to today.
- **Operator Metrics:**
  - Single tunable per node (`daemon.concurrency-budget`) plus per-profile
    `concurrency-cost` integers expresses the policy.
  - Misconfiguration that would make a profile unclaimable (cost > budget)
    surfaces at daemon startup, not at first claim attempt.

### Constraints & Assumptions

- **Technical Constraints:**
  - Postgres + SQLite backends must both implement; the cost-sum query
    runs inside the existing claim transaction.
  - Integer arithmetic only (no floats; the queue ordering is already
    integer-priority).
  - Must compose with the existing `profiles.<name>.max-concurrent` (the
    "hard ceiling" semantic) and with the cluster-wide pg-advisory lock.
- **Business Constraints:**
  - Zero-config installs must keep working — the schema defaults must
    produce identical-to-today behaviour.
  - sma-master's current `local.yml` should migrate to the new model in
    the same change so we don't ship dead code.
- **Assumptions Made:**
  - Worker count remains an upper bound separate from the budget — the
    budget is an *encoder-capacity* cap, the worker count is a *parallel-
    process* cap. Both apply; the tighter wins.

## 2. Brainstormed Ideas & Options

### Option A: Weighted Capacity Budget (CHOSEN)

- **Description:** Each profile declares an integer `concurrency-cost`.
  Each node declares an integer `concurrency-budget`. A pending job is
  claimable iff `Σ(cost of running jobs) + this_job.cost ≤ budget`.
- **Key Features:**
  - Generalises today's per-profile cap into a shared budget.
  - Profiles compose: 1 hq saturates the node, *or* 3 rq, *or* 6 lq,
    *or* 1 rq + 4 lq.
  - Existing `max-concurrent` keeps working as a hard secondary ceiling
    (belt + suspenders for cases like "never two hq even if budget allows").
- **Pros:**
  - One number per profile, one number per node — clean operator model.
  - Default cost=1 + default budget=workers → identical behaviour to a
    pre-cap installation.
  - Cluster-mode-friendly: profile costs are global, budgets are per-node.
- **Cons:**
  - Two interacting cap mechanisms (budget + max-concurrent) — must
    document carefully or operators get confused about which fires.
  - Adds one more SQL query inside the claim transaction (cost-sum).
- **Effort Estimate:** M
- **Risk Level:** Low (additive; gated by config; race-safety re-uses
  the lock we already ship).
- **Dependencies:** Per-profile `max-concurrent` (`ab2ad73`), Postgres
  advisory lock (`d419084`), args-derived profile (`461fbb2`).

### Option B: Mutually-Exclusive Class Slots

- **Description:** Each profile has its own slot count AND running an hq
  exclusively locks the node from any other class.
- **Pros:** Simpler reasoning ("hq means stop everything else").
- **Cons:** Idle-worker problem: an hq running on a slow source blocks
  *all* lq throughput, which is the opposite of the current operator
  intent (lq Kids content should drain fast, even during 4K work).
- **Effort Estimate:** S
- **Risk Level:** Medium (operator-visible regression vs. today's
  independent caps).

### Option C: Token Bucket / Rate Limiter

- **Description:** Tokens accumulate over time at a node-specific rate;
  starting a job consumes tokens proportional to expected cost.
- **Pros:** Smooth handling of burst arrivals.
- **Cons:** Token-bucket semantics are foreign to today's claim model;
  introduces wall-clock timing into a queue that's currently purely
  state-driven; debuggability suffers.
- **Effort Estimate:** L
- **Risk Level:** High (new behaviour, hard to introspect from `/jobs`).

### Additional Ideas Considered

- **Auto-detected budget from iGPU capability** — defer until Option A
  ships; can derive a sensible default later from `vainfo` output if
  needed.
- **Per-profile priority weights to influence claim ordering** —
  separate concern from capacity; punt to a future session.

## 3. Decision Outcome

### Chosen Approach

**Selected Solution:** Option A — Weighted Capacity Budget.

### Rationale

**Primary Factors in Decision:**

- *Composability with today's code.* `claim_next_job` already takes
  `profile_caps`; extending to a cost-sum check is the same shape of
  query inside the same advisory-lock transaction.
- *Identical-to-today default behaviour.* `concurrency-cost = 1` for
  every profile and `concurrency-budget = workers` produces exactly
  the current claim semantics, so existing operators see no change.
- *Operator mental model.* Operators already think of jobs in
  weight-classes; "1 hq = 3 rq = 6 lq" is a sentence they say out
  loud. The config keys mirror that sentence directly.

### Trade-offs Accepted

- **What We're Gaining:**
  - A single tunable expresses node capacity instead of N independent caps.
  - Idle-worker problem dissolves: lq fans out to fill the budget when
    nothing heavier is queued.
  - Cluster-mode story stays clean (profile costs global, budgets per-node).
- **What We're Sacrificing:**
  - Two interacting cap mechanisms (`max-concurrent` + budget). Mitigated
    by docs and by the fact that `max-concurrent` is genuinely useful as
    a hard ceiling (output-disk saturation reasons that have nothing to
    do with encoder bandwidth).
- **Future Considerations:**
  - Auto-derive a default budget from probed iGPU capability.
  - Priority-weighted claim ordering (separate from capacity) for SLA-style work.

## 4. Implementation Plan

### MVP Scope (Phase 1)

**Core Features for Initial Release:**

- [ ] Schema: add `profiles.<name>.concurrency-cost: int = 1` (alias
      `concurrency-cost`).
- [ ] Schema: add `daemon.concurrency-budget: int | None = None`,
      where None resolves to `workers` at PathConfigManager time.
- [ ] PathConfigManager: expose `profile_concurrency_costs() -> dict[str, int]`
      and `concurrency_budget -> int` accessors mirroring the existing
      `profile_concurrency_caps()` pattern.
- [ ] db.py: extend `_profiles_at_cap()` (or add `_budget_exhausted_profiles()`)
      to compute, for each profile, the highest cost a *new* job of that
      profile could carry without pushing `Σ running.cost` over budget.
      Profiles whose minimum-cost-job would exceed remaining budget go
      into the over-capped set already consumed by `claim_next_job`.
- [ ] worker.py: thread `profile_costs` + `concurrency_budget` through
      `claim_next_job` the same way `profile_caps` already flows.
- [ ] Startup validation: refuse to start if any profile's
      `concurrency-cost > concurrency-budget` (would be unclaimable forever);
      structured single-line ERROR log naming the offending profile.
- [ ] `setup/local.yml` (operator-side, gitignored): stamp the
      `hq=6 / rq=2 / lq=1 / budget=6` values; update generator's
      illustrative profiles to show the pattern.

**Acceptance Criteria:**

- As an operator with `concurrency-cost: 6` on hq, when one hq is
  running on a node with `concurrency-budget: 6`, I see *no other job
  of any profile* get claimed until that hq completes.
- As an operator with budget=6 and only lq queued (cost=1), I see up
  to 6 lq jobs claimed concurrently — bounded only by `workers` and
  `lq.max-concurrent` (if set).
- As an operator with no costs/budget configured, claim behaviour is
  byte-identical to today's `max-concurrent`-only model.
- The new `pg_advisory_xact_lock` (`d419084`) wraps the cost-sum
  computation, so two concurrent claims can't both pass the budget
  check on the same available slot.

**Definition of Done:**

- [ ] Schema + projection + db logic + worker plumbing
- [ ] Targeted tests in `tests/test_sqlite_db.py` for cost-sum
      enforcement (single-profile, mixed-profile, budget-exhausted,
      zero-config-identical-to-today)
- [ ] Mocked-pool tests in `tests/test_daemon.py` for the Postgres
      claim path
- [ ] Startup-validation test for cost > budget
- [ ] Sample regenerated; `docs/configuration.md` documents the new
      keys + the interaction with `max-concurrent`; `docs/daemon.md`
      gets a "Concurrency budgeting" section
- [ ] `setup/local.yml` updated on sma-master via `deploy:config`
- [ ] CI passes (3569+ tests stay green)

### Future Enhancements (Phase 2+)

**Features for Later Iterations:**

- *Auto-detected budget* from iGPU probe at startup (Xe-LPG → 6,
  Arc dGPU → 12, CPU-only → 1, etc.). Deferred because the
  operator-stamped value is sufficient and the auto-detection
  rules need real-world measurement.
- *Priority-weighted claim ordering* — a separate concern from
  capacity; if SLA-style "always claim arr-webhook jobs ahead of
  scanner jobs" becomes a need, that's a new ticket.

**Nice-to-Have Improvements:**

- Per-node concurrency-budget surfaced as a Prom gauge
  (`sma_concurrency_budget_total`, `sma_concurrency_budget_in_use`)
  so operators can alert on sustained saturation.
- `GET /jobs?summary=concurrency` admin endpoint returning the
  per-profile cost map + current sum.

## 5. Action Items & Next Steps

### Immediate Actions (This Week)

- [ ] **Author PRP `docs/prps/weighted-concurrency-budget.md`**
  - **Dependencies:** This brainstorm doc.
  - **Success Criteria:** PRP carries `STATUS: IN-FLIGHT`, lists the
    files to touch, includes a 4-row validation matrix, and writes
    out the exact schema-default behaviour table.

- [ ] **Implement Phase 1 in one focused commit series**
  - **Dependencies:** PRP merged.
  - **Success Criteria:** All Definition of Done items above ticked,
    `pytest -q` stays green at 3569+ tests, `mise run test:lint` clean.

### Short-term Actions (Next Sprint)

- [ ] Deploy to sma-master via `mise run deploy:config && mise run deploy:reload`
- [ ] Observe `/jobs?status=running` over 24h to confirm the
      budget-bounded claim behaviour is what we expect
- [ ] Optionally add the Phase 2 Prometheus gauges if the budget
      saturation is actually load-bearing in real traffic

## 6. Risks & Dependencies

### Technical Risks

- **Risk:** Cost-sum query inside the claim transaction adds latency
  to every claim, even when no profile carries a custom cost.
  - **Impact:** Low
  - **Probability:** Low
  - **Mitigation Strategy:** Early-out when all profile costs are 1
    AND budget ≥ workers — the budget is structurally non-binding,
    skip the query. Measured cost is one extra `COUNT/SUM` against
    `jobs WHERE status='running'`, the same table the existing cap
    query hits; index reuse is automatic.

- **Risk:** Two cap mechanisms (`max-concurrent` + budget) confuse
  operators about which one fired in a "why isn't this claiming?"
  scenario.
  - **Impact:** Medium
  - **Probability:** Medium
  - **Mitigation Strategy:** When a candidate job is skipped, log a
    single-line structured event naming the gate that fired
    (`{"event":"claim.skipped","reason":"budget","cost":6,"budget_remaining":2}`
    or `"reason":"max_concurrent","profile":"hq","running":1,"cap":1`).
    Surface in `docs/daemon.md` under "Why a job isn't claiming".

- **Risk:** A misconfigured `concurrency-cost > concurrency-budget`
  silently makes a profile unclaimable.
  - **Impact:** High (data flow stalls without an obvious error)
  - **Probability:** Medium (likely on first operator tuning)
  - **Mitigation Strategy:** Refuse to start the daemon if validation
    fails; structured ERROR log naming the profile and both values.

## 7. Resources & References

### Codebase References

- `resources/daemon/db.py` — `_profiles_at_cap()`, the
  `claim_next_job()` advisory-lock blocks (both backends),
  `_backfill_one_request_profile()`.
- `resources/daemon/config.py` — `profile_concurrency_caps()` (pattern
  to mirror for `profile_concurrency_costs()` + `concurrency_budget`).
- `resources/daemon/worker.py` — claim site that threads `profile_caps`
  through.
- `resources/config_schema.py` — `ProfileOverlay.max_concurrent`
  (location of the new `concurrency_cost` field) and `DaemonConfig`
  (location of the new `concurrency_budget` field).
- `tests/test_sqlite_db.py` — `test_profile_cap_skips_pending_when_running_count_reached`
  (pattern to mirror for budget tests).

### Related Commits (Today's Session)

- `ab2ad73` — initial per-profile `max-concurrent` cap (the thing
  this refactor generalises).
- `d419084` — `pg_advisory_xact_lock` wrapping the count + claim
  (the race-safety guard that the new cost-sum check rides on).
- `461fbb2` — derive profile from args (lets legacy NULL-profile rows
  participate in the cost-sum the same way they participate in the cap).
- `ba16bfc` — backfill `request_profile` on every requeue path (so
  retried jobs carry the cost correctly).

## 8. Session Notes & Insights

### Key Insights Discovered

- The current `max-concurrent` model has an idle-worker pathology:
  on a 3-worker node with `hq.max-concurrent: 1` and a long hq
  running, the other two workers can't *boost* lq throughput
  beyond `lq.max-concurrent` even if encoder bandwidth allows it.
  Weighted budgets fix this elegantly.
- `max-concurrent` retains real value as a *non-encoder* cap (e.g.
  "never two hq because the output-disk janitor can't keep up with
  two 4K writes" is a disk-bandwidth concern, not an encoder-bandwidth
  one). Keep both mechanisms — they answer different questions.
- The model maps cleanly to the existing args-derived profile
  - advisory-lock infrastructure landed earlier today; no new
  primitives needed.

### Questions Raised (For Future Investigation)

- Does the iGPU-specific budget value warrant auto-detection from
  `vainfo`-reported encoder unit count? Probably not until we have
  a second node type to compare.
- Should `concurrency-budget: null` mean "unbounded" or "workers"?
  Decided: `None` resolves to `workers` (today-identical default).
  An explicit `concurrency-budget: 0` could be reserved for
  "unbounded" if a future need arises; not in MVP.

### Team Feedback

- Single-operator project; no team feedback to record.
- Operator preference is explicit: keep the migration trivial
  (config-only changes), keep the validation loud (refuse to start
  on misconfiguration), keep the docs in `docs/daemon.md` next to
  the existing storage-management content.
