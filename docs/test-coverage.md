# Test Coverage Policy

The Python test suite is gated on two thresholds, and both must pass before a pull request can merge:

- **90% global line coverage** across `resources/`, `converter/` and `autoprocess/`.
- **70% per-module floor** for every production module with at least 100 statements. The floor stops a single weak
  module from hiding behind well-covered ones.

This page explains the policy and how to run it. It deliberately contains no coverage figures, because they change
with every commit; run the commands below to see current numbers.

## Running the Gates Locally

```bash
mise run test:cov
```

The task runs pytest with `--cov-fail-under=${COV_FAIL_UNDER:-90}`, then runs
`scripts/check-coverage-floor.py --floor ${COV_MODULE_FLOOR:-70}`. It exits non-zero when either gate fails, and it
writes these reports:

- `htmlcov/index.html`: an interactive per-file report with uncovered lines highlighted.
- `coverage.xml`: a machine-readable report for CI and IDEs.
- `cov.json`: the JSON that `scripts/check-coverage-floor.py` reads.

To re-check the per-module floor against an existing `cov.json` without running the tests again:

```bash
venv/bin/python scripts/check-coverage-floor.py
```

CI runs the same `mise run test:cov` task in `.github/workflows/ci.yml`.

## Exclusions

Coverage is configured in `.coveragerc`. Test files and virtual environments are omitted, and so is
`resources/openvino_analyzer.py`, which needs the Intel OpenVINO runtime and a GPU that CI does not have. The daemon
exercises the fallback path for a missing analyzer, so the integration is still covered.

When you add an exclusion, add a comment in `.coveragerc` that explains why. Hardware-bound code is a valid reason;
code that is merely awkward to test is not.

## When a Gate Fails

1. Run `mise run test:cov` and open `htmlcov/index.html`.
2. Open the file the report flagged and find the red (uncovered) lines.
3. Add a test that exercises the missing branch, following the style of the existing tests for that module.

Follow these rules when you fix a failing gate:

- Do not lower the thresholds.
- Do not use `# pragma: no cover` to hide logic. It is acceptable only for a branch that cannot be tested, such as a
  defensive `else` after an exhaustive `if` chain, and the reason must be on the same line.
- For a temporary dip during a large refactor, `COV_FAIL_UNDER=0 mise run test:cov` disables the global gate for one
  run. The per-module floor has no override on purpose, because a module that falls below 70% deserves an explicit
  discussion in the pull request.

## Related

- [CONTRIBUTING.md](../CONTRIBUTING.md) describes the full development workflow.
- The *Validation Matrix* in [CLAUDE.md](../CLAUDE.md) lists the smallest useful test set for each area of the code.
