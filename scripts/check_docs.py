#!/usr/bin/env python3
"""Check operator docs for broken references.

- Every relative markdown link resolves to a file in the repository.
- Every ``mise run <task>`` names a real task or alias (from ``.mise/tasks``).

Fenced code blocks are skipped for links but included for ``mise run``
references, since copy-pasted commands are what operators actually run.

Usage:
    python scripts/check_docs.py
"""

from __future__ import annotations

import posixpath
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wiki_sync import ROOT, task_names  # noqa: E402

DOC_GLOBS = ["README.md", "CONTRIBUTING.md", "docs/*.md", "post_process/*.md"]

_LINK_RE = re.compile(r"!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_MISE_RUN_RE = re.compile(r"mise run ([a-z][a-z0-9:_-]*)(\*|\{[^}]*\})?")


def _task_exists(name: str, pattern: str, tasks: set[str]) -> bool:
  """``group:*`` needs some task in the group; ``group:{a,b}`` needs every expansion."""
  if pattern == "*":
    return any(t.startswith(name) for t in tasks)
  if pattern:
    return all(name + part.strip() in tasks for part in pattern[1:-1].split(","))
  return name in tasks


def doc_files(root: Path = ROOT) -> list[Path]:
  files: list[Path] = []
  for pattern in DOC_GLOBS:
    files.extend(sorted(root.glob(pattern)))
  return files


def check_file(path: Path, root: Path, tasks: set[str]) -> list[str]:
  rel = str(path.relative_to(root))
  base_dir = posixpath.dirname(rel)
  problems = []
  in_fence = False
  for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
    if _FENCE_RE.match(line):
      in_fence = not in_fence
    for task, pattern in _MISE_RUN_RE.findall(line):
      if not _task_exists(task, pattern, tasks):
        problems.append("%s:%d: unknown mise task %r" % (rel, number, task + pattern))
    if in_fence:
      continue
    for target in _LINK_RE.findall(line):
      if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
        continue
      resolved = posixpath.normpath(posixpath.join(base_dir, target.partition("#")[0]))
      if resolved.startswith("..") or not (root / resolved).exists():
        problems.append("%s:%d: broken link %s" % (rel, number, target))
  return problems


def main(root: Path = ROOT) -> int:
  tasks = task_names(root)
  problems = []
  files = doc_files(root)
  for path in files:
    problems.extend(check_file(path, root, tasks))
  for problem in problems:
    print(problem, file=sys.stderr)
  if not problems:
    print("docs check: %d files, all links and mise task references resolve" % len(files))
  return 1 if problems else 0


if __name__ == "__main__":
  sys.exit(main())
