# Test Coverage Policy

The Python test suite is gated on **≥90% global line coverage** with a
**≥70% per-module floor** for production modules of ≥100 statements.

Current baseline: **90.03%** (10 218 / 11 349 statements).

## Running locally

```bash
mise run test:cov
```

This invokes pytest with `--cov-fail-under=${COV_FAIL_UNDER:-90}`. The task
exits non-zero when global coverage drops below 90%, and writes:

- `htmlcov/index.html` — interactive per-file report (open it after a run)
- `coverage.xml` — machine-readable for CI / IDEs
- `cov.json` — JSON used by `scripts/check-coverage-floor.py`

To check the per-module floor:

```bash
source venv/bin/activate
python scripts/check-coverage-floor.py
# OK: all production modules >= 100 statements clear 70%. Repo-wide: 90.03%.
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

Two modules are excluded from coverage measurement because they require
hardware or binaries that aren't available in CI:

| Module | Why excluded |
|---|---|
| `resources/openvino_analyzer.py` | Requires Intel OpenVINO runtime + GPU; the daemon's integration with this module is exercised via the analyzer-not-available fallback path on every dev box, but unit-testing the analyzer itself needs hardware not available in CI. |
| `resources/library_audit/probes.py` | Calls `ffprobe` against real files. The decision logic is covered indirectly via the library-audit engine tests that mock its module-level FFMpeg reference. The remaining missed lines are the real-FFprobe path used in production but not in CI. |

When adding a new exclusion, document the reason in this table — a
one-liner like "covered indirectly via X" is enough.

## Module-level standings

The 80% target floor is a stretch goal; the enforced floor is 70%
because `resources/mediaprocessor.py` is the one production module
currently sitting between the two:

| Module | Cover % | Notes |
|---|---|---|
| `resources/mediaprocessor.py` | 75.8% | The transcoding pipeline. Largest module by far (2 055 statements). The remaining 498 missed lines are scattered across HDR detection edges, audio downmix matrices, subtitle burn-in fallbacks, and codec mixing. **Next target.** |

A separate task is on the roadmap to drive `mediaprocessor.py` to
≥85%; once it lands, raise the per-module floor from 70% → 80% in
`scripts/check-coverage-floor.py`.

Every other production module of ≥100 statements is at ≥85%.

## CI

The repo runs the gate in `.github/workflows/ci.yml` as part of the
test job: pytest's own `--cov-fail-under=90` flag fails the workflow
on a regression, and `python scripts/check-coverage-floor.py` runs as
a follow-up step that fails on any module below 70%.

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

- [PRP: docs/prps/test-coverage-90.md](prps/test-coverage-90.md)
- [Task breakdown: docs/tasks/test-coverage-90.md](tasks/test-coverage-90.md)
