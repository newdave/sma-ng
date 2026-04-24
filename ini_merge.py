"""Add or update keys in a live INI file from a sample INI file.

This repo-root module keeps ``import ini_merge`` working in tests and scripts
without depending on untracked ``.mise`` helper paths.
"""

import re
import sys


def parse_keys(path):
  """Return {section: {key: value}} for all non-comment key=value lines."""
  sections = {}
  cur = None
  with open(path) as f:
    for line in f:
      s = line.strip()
      m = re.match(r"^\[(.+)\]", s)
      if m:
        cur = m.group(1)
        sections.setdefault(cur, {})
      elif cur and re.match(r"^[^#;].*=", s):
        k, _, v = s.partition("=")
        sections[cur][k.strip()] = v.strip()
  return sections


def parse_force_keys(arg):
  """Parse --force-keys value into a {section: set(keys)} dict."""
  forced = {}
  for item in arg.split(","):
    item = item.strip()
    if "." not in item:
      continue
    sec, _, key = item.partition(".")
    forced.setdefault(sec.strip(), set()).add(key.strip())
  return forced


def _key_from_line(line):
  """Extract the key name from a key=value line, or '' if not a key line."""
  m = re.match(r"^(\S[^=]*?)\s*=", line)
  return m.group(1).strip() if m else ""


def sort_section_keys(lines, sample_secs):
  """Reorder key=value lines within each section to match sample key order."""
  spans = []
  cur_name = None
  cur_start = None
  for i, line in enumerate(lines):
    m = re.match(r"^\[(.+)\]", line.strip())
    if m:
      if cur_name is not None:
        spans.append((cur_name, cur_start, i))
      cur_name = m.group(1)
      cur_start = i + 1
  if cur_name is not None:
    spans.append((cur_name, cur_start, len(lines)))

  out = list(lines)
  for name, start, end in spans:
    sample_keys = list(sample_secs.get(name, {}).keys())
    sample_order = {k: i for i, k in enumerate(sample_keys)}
    key_indices = [i for i in range(start, end) if re.match(r"^[^#;].*=", out[i].strip())]
    if not key_indices:
      continue

    key_lines = [out[i] for i in key_indices]
    key_lines.sort(
      key=lambda l: (
        sample_order.get(_key_from_line(l), len(sample_keys)),
        _key_from_line(l),
      )
    )
    for i, idx in enumerate(key_indices):
      out[idx] = key_lines[i]

  return out


# Sections whose names start with these prefixes are wildcard-matched at
# runtime (any [Sonarr-*] / [Radarr-*] name is valid).  Never deprecate keys
# inside them just because the section isn't in the sample.
_WILDCARD_SECTION_PREFIXES = ("sonarr", "radarr")


def deprecate_removed_keys(lines, sample_secs):
  """Prepend '# deprecated: ' to key=value lines absent from the sample.

  Keys inside Sonarr*/Radarr* sections are never deprecated because those
  section names are auto-discovered wildcards, not fixed sample sections.
  """
  out = []
  cur = None
  for line in lines:
    m = re.match(r"^\[(.+)\]", line.strip())
    if m:
      cur = m.group(1)
      out.append(line)
      continue
    if cur and re.match(r"^[^#;].*=", line.strip()):
      # Skip wildcard-prefix sections entirely — they have no sample equivalent.
      if not cur.lower().startswith(_WILDCARD_SECTION_PREFIXES):
        key = _key_from_line(line.strip())
        if key and key not in sample_secs.get(cur, {}):
          print(f"  ! [{cur}] {key}: deprecated")
          line = f"# deprecated: {line.lstrip()}"
    out.append(line)
  return out


def remove_blank_values(lines):
  """Remove key=value lines where the value is the empty string."""
  out = []
  for line in lines:
    if re.match(r"^[^#;\s].*=\s*$", line):
      continue
    out.append(line)
  return out


def main():
  import argparse
  import shutil

  parser = argparse.ArgumentParser(
    description="Merge keys from a sample INI into a live INI file.",
  )
  parser.add_argument("sample")
  parser.add_argument("live")
  parser.add_argument("--force-keys", default="", help="Comma-separated Section.key pairs to always overwrite")
  parser.add_argument("--sort", action="store_true", help="Reorder keys within sections to match sample order")
  parser.add_argument("--deprecate", action="store_true", help="Comment out live-only keys absent from the sample")
  parser.add_argument("--backup", default="", help="Copy live file to this path before modifying")
  parser.add_argument("--remove-blank", action="store_true", help="Remove key=value lines with empty values")
  args = parser.parse_args()

  sample_secs = parse_keys(args.sample)
  live_secs = parse_keys(args.live)
  forced = parse_force_keys(args.force_keys) if args.force_keys else {}

  additions = {}
  for sec, keys in sample_secs.items():
    for k, v in keys.items():
      if k not in live_secs.get(sec, {}):
        additions.setdefault(sec, []).append((k, v))

  updates = {}
  for sec, keys in forced.items():
    for k in keys:
      sample_val = sample_secs.get(sec, {}).get(k)
      live_val = live_secs.get(sec, {}).get(k)
      if sample_val is None:
        continue
      if live_val != sample_val:
        updates.setdefault(sec, {})[k] = sample_val

  needs_write = bool(additions or updates or args.sort or args.deprecate or args.remove_blank)
  if not needs_write:
    sys.exit(0)

  with open(args.live) as f:
    lines = f.readlines()

  out = []
  cur = None
  inserted = set()

  for line in lines:
    m = re.match(r"^\[(.+)\]", line.strip())
    if m:
      cur = m.group(1)
      out.append(line)
      if cur in additions and cur not in inserted:
        for k, v in additions[cur]:
          out.append(f"{k} = {v}\n")
          print(f"  + [{cur}] {k} = {v}")
        inserted.add(cur)
      continue

    if cur and cur in updates:
      m2 = re.match(r"^(\s*)(\S[^=]*?)\s*=\s*(.*)", line)
      if m2:
        indent, key, old_val = m2.groups()
        key = key.strip()
        if key in updates[cur]:
          new_val = updates[cur][key]
          print(f"  ~ [{cur}] {key}: {old_val.strip()!r} -> {new_val!r}")
          line = f"{indent}{key} = {new_val}\n"

    out.append(line)

  for sec, pairs in additions.items():
    if sec not in inserted:
      out.append(f"\n[{sec}]\n")
      for k, v in pairs:
        out.append(f"{k} = {v}\n")
        print(f"  + [{sec}] {k} = {v}")

  if args.sort:
    out = sort_section_keys(out, sample_secs)

  if args.deprecate:
    out = deprecate_removed_keys(out, sample_secs)

  if args.remove_blank:
    out = remove_blank_values(out)

  if args.backup:
    shutil.copy2(args.live, args.backup)

  with open(args.live, "w") as f:
    f.writelines(out)


if __name__ == "__main__":
  main()
