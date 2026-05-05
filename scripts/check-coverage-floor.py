#!/usr/bin/env python3
"""Per-module coverage-floor gate.

Reads ``coverage.json`` (produced by ``pytest --cov --cov-report=json:cov.json``
or by ``mise run test:cov``) and exits non-zero when any production module of
``--min-statements`` statements or more falls below ``--floor``%.

Default floor is 80%. The repo-wide ≥90% gate is enforced separately by
pytest's ``--cov-fail-under``. This script catches the case where the global
% passes only because well-covered modules outweigh a single weak one.

Excludes the same hardware-bound modules as ``.coveragerc``: any module
NOT present in the JSON's ``files`` map is implicitly accepted (it was
omitted at measurement time).

Usage:
    python scripts/check-coverage-floor.py              # default: cov.json, 80%, 100 stmts
    python scripts/check-coverage-floor.py --floor 70   # lower the bar
    python scripts/check-coverage-floor.py --json other.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument("--json", default="cov.json", help="path to coverage JSON file")
  p.add_argument("--floor", type=float, default=70.0, help="minimum percent per module")
  p.add_argument(
    "--min-statements",
    type=int,
    default=100,
    help="only enforce floor on modules with at least this many statements",
  )
  return p.parse_args()


def main() -> int:
  args = _parse_args()
  cov_path = Path(args.json)
  if not cov_path.is_file():
    print(f"ERROR: coverage file not found: {cov_path}", file=sys.stderr)
    print("Run `mise run test:cov` first.", file=sys.stderr)
    return 2

  data = json.loads(cov_path.read_text())
  files = data.get("files", {})
  offenders: list[tuple[str, float, int]] = []
  for path, file_data in files.items():
    summary = file_data.get("summary", {})
    stmts = summary.get("num_statements", 0)
    pct = summary.get("percent_covered", 100.0)
    if stmts < args.min_statements:
      continue
    if pct < args.floor:
      offenders.append((path, pct, stmts))

  if offenders:
    print(
      f"FAILED: {len(offenders)} module(s) below {args.floor:.0f}% floor (min statements: {args.min_statements}):",
      file=sys.stderr,
    )
    offenders.sort(key=lambda r: r[1])
    for path, pct, stmts in offenders:
      print(f"  {pct:5.1f}% ({stmts:>4} stmts)  {path}", file=sys.stderr)
    return 1

  total = data.get("totals", {}).get("percent_covered", 0.0)
  print(f"OK: all production modules >= {args.min_statements} statements clear {args.floor:.0f}%. Repo-wide: {total:.2f}%.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
