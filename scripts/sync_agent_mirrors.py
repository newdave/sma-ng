#!/usr/bin/env python3
"""Regenerate the Codex mirrors of the Claude agent configuration.

Sources of truth:
  .claude/skills/<name>/...  -> .agents/skills/<name>/...   (verbatim copy)
  .claude/agents/<name>.md   -> .codex/agents/<name>.toml   (frontmatter name/description + body)

Usage: sync_agent_mirrors.py [--check]
  --check  write nothing; list drifted paths and exit 1 if any.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_SRC = ROOT / ".claude" / "skills"
SKILLS_DST = ROOT / ".agents" / "skills"
AGENTS_SRC = ROOT / ".claude" / "agents"
AGENTS_DST = ROOT / ".codex" / "agents"

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


def _tree(base):
  """Map relative path -> bytes for every file below base."""
  if not base.is_dir():
    return {}
  return {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}


def _toml_string(value):
  return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_agent(text):
  """Render a Claude agent markdown file as a Codex agent TOML document."""
  match = _FRONTMATTER.match(text)
  if not match:
    raise ValueError("missing frontmatter")
  fields = {}
  for line in match.group(1).splitlines():
    key, _, value = line.partition(":")
    fields[key.strip()] = value.strip()
  body = match.group(2).lstrip("\n").rstrip("\n")
  if '"""' in body or "\\" in body:
    raise ValueError("agent body contains characters that need TOML escaping")
  return f'name = {_toml_string(fields["name"])}\ndescription = {_toml_string(fields["description"])}\ndeveloper_instructions = """\n{body}"""\n'


def expected():
  """Return {absolute destination path: bytes} for every generated file."""
  files = {}
  for rel, data in _tree(SKILLS_SRC).items():
    files[SKILLS_DST / rel] = data
  for src in sorted(AGENTS_SRC.glob("*.md")):
    files[AGENTS_DST / f"{src.stem}.toml"] = render_agent(src.read_text()).encode()
  return files


def stale():
  """Generated-area files that have no source (to be deleted)."""
  want = set(expected())
  found = {SKILLS_DST / r for r in _tree(SKILLS_DST)}
  found |= {AGENTS_DST / r for r in _tree(AGENTS_DST) if r.endswith(".toml")}
  return sorted(found - want)


def drift():
  """Return (missing_or_different, stale) path lists."""
  changed = [p for p, data in expected().items() if not p.is_file() or p.read_bytes() != data]
  return sorted(changed), stale()


def sync():
  for path, data in expected().items():
    if not path.is_file() or path.read_bytes() != data:
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_bytes(data)
  for path in stale():
    path.unlink()
  if SKILLS_DST.is_dir():
    for d in sorted((p for p in SKILLS_DST.rglob("*") if p.is_dir()), reverse=True):
      if not any(d.iterdir()):
        d.rmdir()


def main(argv=None):
  parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
  parser.add_argument("--check", action="store_true", help="report drift without writing")
  args = parser.parse_args(argv)
  changed, extra = drift()
  if args.check:
    for p in changed:
      print(f"drift: {p.relative_to(ROOT)}")
    for p in extra:
      print(f"stale: {p.relative_to(ROOT)}")
    return 1 if changed or extra else 0
  sync()
  print(f"synced {len(changed)} file(s), removed {len(extra)} stale file(s)")
  return 0


if __name__ == "__main__":
  sys.exit(main())
