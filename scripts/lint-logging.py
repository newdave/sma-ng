#!/usr/bin/env python3
"""Lint rules for SMA-NG logging conventions.

Enforces invariants documented in
``docs/brainstorming/2026-04-27-logging-refactor.md`` so the
single-line / no-secrets contract for logs survives future changes.

Rules
-----

1. ``json.dumps(..., indent=…)`` is forbidden inside
   ``log.<level>()`` / ``logger.<level>()`` / ``self.log.<level>()`` calls.
   Multi-line JSON in a log record breaks line-oriented tooling. Drop the
   ``indent`` arg; the SingleLineFormatter renders compact JSON regardless.

2. String literals containing ``\\n`` cannot be passed as the message arg
   to a logging call. Multi-line application messages are rendered as
   ``msg ⏎ next-line`` by the formatter — better to emit two separate
   records, or restructure the message.

3. ``print(json.dumps(..., indent=…))`` is forbidden anywhere. The daemon
   worker captures ``manual.py``'s stdout into per-config log files; an
   indented JSON dump becomes a fragmented multi-line blob.

4. Bare ``print()`` is allowed in interactive CLI helpers but rejected in
   ``resources/daemon/`` where it would pollute the worker's stdout
   capture. Per-line opt-out: append ``# noqa: log-print``.

Usage::

    scripts/lint-logging.py [files...]

If no files are given, lints everything under ``resources/``,
``autoprocess/``, plus ``daemon.py`` and ``manual.py``.
"""

from __future__ import annotations

import ast
import os
import sys
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

LOGGING_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "log"})

# Filenames where bare print() is rejected unless explicitly annotated.
PRINT_RESTRICTED_PREFIXES = ("resources/daemon/",)

# `print()` lines may opt out by appending this comment.
NOQA_PRINT_TOKEN = "noqa: log-print"


def _is_logger_call(call: ast.Call) -> bool:
  """Return True when *call* targets one of LOGGING_METHODS on *something*.

  Matches:
      log.info(...)
      self.log.info(...)
      self._log.info(...)
      logger.info(...)
      foo.bar.log.info(...)   (any depth, attribute named 'log' / 'logger' anywhere)
  """
  func = call.func
  if not isinstance(func, ast.Attribute):
    return False
  if func.attr not in LOGGING_METHODS:
    return False
  # Walk up the attribute chain looking for an obvious logger token.
  cur: ast.AST | None = func.value
  while isinstance(cur, ast.Attribute):
    if cur.attr in ("log", "logger", "_log", "_logger"):
      return True
    cur = cur.value
  if isinstance(cur, ast.Name) and cur.id in ("log", "logger", "_log", "_logger"):
    return True
  return False


def _is_json_dumps(call: ast.Call) -> bool:
  func = call.func
  if isinstance(func, ast.Attribute) and func.attr == "dumps":
    if isinstance(func.value, ast.Name) and func.value.id == "json":
      return True
  if isinstance(func, ast.Name) and func.id == "dumps":
    return True
  return False


def _has_indent_kwarg(call: ast.Call) -> bool:
  for kw in call.keywords:
    if kw.arg == "indent":
      # `indent=None` is a no-op; treat that as compact and allow it.
      if isinstance(kw.value, ast.Constant) and kw.value.value is None:
        return False
      return True
  return False


def _walk_calls(node: ast.AST) -> Iterable[ast.Call]:
  for child in ast.walk(node):
    if isinstance(child, ast.Call):
      yield child


def _string_literal_contains_newline(node: ast.AST) -> bool:
  if isinstance(node, ast.Constant) and isinstance(node.value, str):
    return "\n" in node.value
  if isinstance(node, ast.JoinedStr):  # f-string
    return any(_string_literal_contains_newline(v) for v in node.values)
  if isinstance(node, ast.FormattedValue):
    return False  # interpolated expression, can't inspect statically
  if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
    return _string_literal_contains_newline(node.left) or _string_literal_contains_newline(node.right)
  return False


def _line_has_noqa_print(source_lines: list[str], lineno: int) -> bool:
  """True when source line `lineno` (1-indexed) ends with the noqa comment."""
  if not (1 <= lineno <= len(source_lines)):
    return False
  return NOQA_PRINT_TOKEN in source_lines[lineno - 1]


def lint_file(path: Path) -> list[str]:
  """Return a list of human-readable violation strings for *path*."""
  errors: list[str] = []
  try:
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
  except (OSError, SyntaxError) as exc:
    errors.append(f"{path}: parse error — {exc}")
    return errors
  source_lines = source.splitlines()
  try:
    rel = path.relative_to(REPO_ROOT) if path.is_absolute() else path
  except ValueError:
    # path lives outside the repo (e.g. tmp_path in tests); use as-is.
    rel = path
  rel_str = str(rel).replace(os.sep, "/")

  print_restricted = any(rel_str.startswith(p) for p in PRINT_RESTRICTED_PREFIXES)

  for call in _walk_calls(tree):
    func = call.func

    # Rule 4: bare print() in restricted areas.
    if isinstance(func, ast.Name) and func.id == "print":
      if print_restricted and not _line_has_noqa_print(source_lines, call.lineno):
        errors.append(f"{rel_str}:{call.lineno}: print() is forbidden in {PRINT_RESTRICTED_PREFIXES[0]} (append `# {NOQA_PRINT_TOKEN}` to opt out)")
      # Rule 3: print(json.dumps(..., indent=...))
      for arg in call.args:
        if isinstance(arg, ast.Call) and _is_json_dumps(arg) and _has_indent_kwarg(arg):
          errors.append(f"{rel_str}:{call.lineno}: print(json.dumps(..., indent=…)) emits multi-line output; use a logger call without indent= instead")
      continue

    if not _is_logger_call(call):
      continue

    # Rule 1: indent= inside log calls.
    for arg in call.args:
      if isinstance(arg, ast.Call) and _is_json_dumps(arg) and _has_indent_kwarg(arg):
        errors.append(f"{rel_str}:{call.lineno}: json.dumps(..., indent=…) inside a log call produces multi-line records; drop the indent (the formatter renders compact JSON)")

    # Rule 2: literal `\n` in the message argument (first positional).
    if call.args and _string_literal_contains_newline(call.args[0]):
      errors.append(f"{rel_str}:{call.lineno}: log message string contains a literal newline; split into separate log calls or rephrase as one record")

  return errors


def _default_targets() -> list[Path]:
  targets: list[Path] = []
  for d in ("resources", "autoprocess"):
    targets.extend((REPO_ROOT / d).rglob("*.py"))
  for f in ("daemon.py", "manual.py"):
    p = REPO_ROOT / f
    if p.exists():
      targets.append(p)
  return targets


def main(argv: list[str]) -> int:
  raw_paths = argv[1:]
  if raw_paths:
    paths = [Path(p) if Path(p).is_absolute() else (REPO_ROOT / p) for p in raw_paths]
  else:
    paths = _default_targets()

  errors: list[str] = []
  for p in paths:
    if not p.is_file() or p.suffix != ".py":
      continue
    errors.extend(lint_file(p))

  for line in errors:
    print(line, file=sys.stderr)
  return 1 if errors else 0


if __name__ == "__main__":
  sys.exit(main(sys.argv))
