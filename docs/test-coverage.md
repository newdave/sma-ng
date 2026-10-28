# Test Coverage Policy

The Python test suite is gated on **≥90% global line coverage** with a
**≥70% per-module floor** for production modules of ≥100 statements.

Current baseline: **91.53%** (13 679 / 14 945 statements).

## Running locally

```bash
mise run test:cov
```

This invokes pytest with `--cov-fail-under=${COV_FAIL_UNDER:-90}` and then runs
`scripts/check-coverage-floor.py --floor ${COV_MODULE_FLOOR:-70}`. The task
exits non-zero when global coverage drops below 90% or any production module
of ≥100 statements drops below the floor, and writes:

- `htmlcov/index.html` — interactive per-file report (open it after a run)
- `coverage.xml` — machine-readable for CI / IDEs
- `cov.json` — JSON used by `scripts/check-coverage-floor.py`

To re-check the per-module floor against an existing `cov.json`:

```bash
source venv/bin/activate
python scripts/check-coverage-floor.py
# OK: all production modules >= 100 statements clear 70%. Repo-wide: 91.53%.
```

The floor helper exits non-zero if any module ≥100 statements falls below
70%. Both gates run in CI; both must pass on a PR before merge.

## Bypassing the gate

Long-running refactors sometimes need to land partial work that
temporarily dips below the threshold. Set `COV_FAIL_UNDER=0` to disable
the global gate for a single run:

```bash
COV_FAIL_UNDER=0 mise run test:cov
```

The per-module floor helper has no equivalent override on purpose: a
module that drops below 70% is louder than a global dip and warrants an
explicit conversation in the PR.

## Exclusions (`.coveragerc`)

One module is excluded from coverage measurement because it requires
hardware that isn't available in CI:

| Module | Why excluded |
|---|---|
| `resources/openvino_analyzer.py` | Requires Intel OpenVINO runtime + GPU; the daemon's integration with this module is exercised via the analyzer-not-available fallback path on every dev box, but unit-testing the analyzer itself needs hardware not available in CI. |

`resources/library_audit/probes.py` used to be excluded; its probes are now unit-tested against real
`MediaInfo` objects and it is measured like any other module.

When adding a new exclusion, document the reason in this table — a
one-liner like "covered indirectly via X" is enough.

## Module-level standings

The 80% target floor is a stretch goal; the enforced floor is 70%. Every production module of ≥100
statements now clears 80%. The lowest are:

| Module | Cover % | Statements | Notes |
|---|---|---|---|
| `resources/mediaprocessor.py` | 82.1% | 2 837 | The transcoding pipeline. Remaining misses are scattered across HDR detection edges, audio downmix matrices, subtitle burn-in fallbacks and codec mixing. **Next target.** |
| `resources/daemon/config.py` | 85.2% | 485 | Path config manager and routing. |
| `resources/daemon/storage.py` | 86.8% | 114 | Storage janitor sweeps. |

Once `mediaprocessor.py` reaches ≥85%, raise the per-module floor from 70% → 80% via
`COV_MODULE_FLOOR` in `.mise/tasks/test/cov` (the default lives in `scripts/check-coverage-floor.py`).

## CI

The repo runs both gates in `.github/workflows/ci.yml` through `mise run test:cov`: pytest's own
`--cov-fail-under=90` flag fails the workflow on a global regression, and the task's follow-up
`scripts/check-coverage-floor.py` step fails on any module below 70%.

## What to do when the gate fails

1. Run `mise run test:cov && open htmlcov/index.html` (Linux:
   `xdg-open`).
2. Click into the file flagged by the report; uncovered lines are
   highlighted red.
3. Add a test that exercises the missing branch. Mirror the style
   of the file's existing tests — every test module documents its
   pattern in the docstring at the top.

If you genuinely cannot test a branch (hardware bound, or it's a
defensive `else` after an exhaustive `if/elif`), add a `# pragma: no
cover` comment and explain why on the same line.

Don't lower the global threshold. Don't add `# pragma: no cover` to
mask uncovered logic.

## Related

- Coverage policy summary in [CLAUDE.md](../CLAUDE.md) under *Validation Matrix*.
