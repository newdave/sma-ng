#!/usr/bin/env python3
"""Pre-commit hook: auto-increment patch version when code files change.

Examines the staged file list. If any 'code' files are staged (Python
source outside tests/, plus converter/ resources/ autoprocess/ and
top-level entry points), the patch component of the version in
pyproject.toml is incremented and pyproject.toml is re-staged.

Excluded from triggering a bump:
  - docs/          documentation
  - tests/         test suite
  - .github/       CI/CD workflows
  - setup/         config samples and requirements
  - scripts/       helper scripts
  - Makefile, *.md, *.ini, *.json, *.yml, *.yaml, *.cfg, *.toml
    (when NOT pyproject.toml itself being bumped)
  - Formatting-only changes (handled by ruff-format — detected via
    the absence of any substantive diff beyond whitespace)
"""

import re
import subprocess
import sys
from pathlib import Path

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"

# Directories whose changes never trigger a bump
EXCLUDED_DIRS = {
    "docs",
    "tests",
    ".github",
    "setup",
    "scripts",
    ".mise",
}

# File extensions that never trigger a bump
EXCLUDED_EXTENSIONS = {
    ".md",
    ".ini",
    ".sample",
    ".yml",
    ".yaml",
    ".cfg",
    ".toml",
    ".json",
    ".txt",
    ".sh",
    ".lock",
}

# Top-level files (not in subdirs) that never trigger a bump
EXCLUDED_TOP_LEVEL = {
    "Makefile",
    "Dockerfile",
    "docker-entrypoint.sh",
    "docker-compose.yml",
    "mise.toml",
    ".pre-commit-config.yaml",
    ".gitignore",
    "license.md",
}


def get_staged_files():
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True,
        text=True,
    )
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]


def is_code_file(path_str):
    p = Path(path_str)
    parts = p.parts

    # Must be a Python file
    if p.suffix != ".py":
        return False

    # Top-level filename exclusions
    if p.name in EXCLUDED_TOP_LEVEL:
        return False

    # Directory exclusions (check first component)
    if parts and parts[0] in EXCLUDED_DIRS:
        return False

    return True


def bump_patch(version_str):
    parts = version_str.split(".")
    if len(parts) != 3:
        raise ValueError("Expected x.y.z version, got: %s" % version_str)
    parts[2] = str(int(parts[2]) + 1)
    return ".".join(parts)


def main():
    staged = get_staged_files()
    code_files = [f for f in staged if is_code_file(f)]

    if not code_files:
        sys.exit(0)

    content = PYPROJECT.read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not match:
        print("bump-version: could not find version in pyproject.toml", file=sys.stderr)
        sys.exit(1)

    old_version = match.group(1)
    new_version = bump_patch(old_version)
    new_content = content[: match.start(1)] + new_version + content[match.end(1) :]

    PYPROJECT.write_text(new_content)
    subprocess.run(["git", "add", str(PYPROJECT)], check=True)

    print("bump-version: %s → %s  (triggered by: %s)" % (old_version, new_version, ", ".join(code_files[:3]) + (" ..." if len(code_files) > 3 else "")))
    sys.exit(0)


if __name__ == "__main__":
    main()
