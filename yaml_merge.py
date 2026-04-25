"""Merge/audit YAML config files against a sample reference.

Mirrors ``ini_merge.py``'s interface for YAML files.  Uses ``ruamel.yaml``
(``typ="rt"``) throughout so comments, key order, and formatting are preserved
on round-trip.
"""

from __future__ import annotations

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

# Sections whose keys are never deprecated or backfilled because they contain
# user-defined names (Sonarr-*/Radarr-* instance sections, Profiles, Daemon).
_SKIP_SECTIONS = frozenset({"profiles", "daemon"})
_WILDCARD_PREFIXES = ("sonarr", "radarr")


def _should_skip(section: str) -> bool:
  s = section.lower()
  return section in _SKIP_SECTIONS or any(s.startswith(p) for p in _WILDCARD_PREFIXES)


def _make_yaml() -> YAML:
  y = YAML(typ="rt")
  y.preserve_quotes = True
  y.width = 4096
  return y


def _to_plain(obj) -> dict:
  """Recursively convert CommentedMap / any mapping to a plain dict."""
  if isinstance(obj, dict):
    return {k: _to_plain(v) for k, v in obj.items()}
  return obj


def _load(path: str) -> CommentedMap:
  y = _make_yaml()
  with open(path) as fh:
    data = y.load(fh)
  return data if data is not None else CommentedMap()


def parse_keys(path: str) -> dict:
  """Load YAML file and return ``{section: {key: value}}`` as a plain dict."""
  data = _load(path)
  return _to_plain(data)


def add_missing(live_path: str, sample_path: str, dry_run: bool = False) -> None:
  """Add keys present in sample but absent in live.

  Preserves live values and comments.  Skip sections defined in
  ``_SKIP_SECTIONS`` and sections whose names start with a wildcard prefix.
  """
  live = _load(live_path)
  sample = _load(sample_path)
  changed = False

  for section, s_keys in sample.items():
    if _should_skip(section):
      continue
    if not isinstance(s_keys, dict):
      # Top-level scalar key — handle directly.
      if section not in live:
        print(f"  + {section}: {s_keys!r}")
        live[section] = s_keys
        changed = True
      continue

    if section not in live or not isinstance(live[section], dict):
      live[section] = CommentedMap()

    for key, value in s_keys.items():
      if key not in live[section]:
        print(f"  + [{section}] {key}: {value!r}")
        live[section][key] = value
        changed = True

  if changed and not dry_run:
    y = _make_yaml()
    with open(live_path, "w") as fh:
      y.dump(live, fh)


def deprecate_removed(live_path: str, sample_path: str, dry_run: bool = False) -> None:
  """Comment out keys in live that are not in sample.

  Uses ruamel.yaml comment attributes to prepend ``# deprecated:`` before the
  key.  Skip sections in ``_SKIP_SECTIONS`` and wildcard-prefix sections.
  """
  live = _load(live_path)
  sample = _load(sample_path)

  # Collect (section, key) pairs to deprecate before mutating the map.
  to_deprecate: list[tuple[str, str]] = []
  for section, l_keys in live.items():
    if _should_skip(section):
      continue
    if not isinstance(l_keys, dict):
      continue
    s_section = sample.get(section, {}) if isinstance(sample.get(section), dict) else {}
    for key in l_keys:
      if key not in s_section:
        to_deprecate.append((section, key))

  if not to_deprecate:
    return

  for section, key in to_deprecate:
    print(f"  ! [{section}] {key}: deprecated")
    # Attach a comment above the key so it reads as "# deprecated: key: value"
    # when dumped.  ruamel.yaml stores before-key comments in ca.items[key][1].
    live[section].yaml_set_comment_before_after_key(
      key,
      before=f"deprecated: {key}: {live[section][key]}",
    )
    del live[section][key]

  if not dry_run:
    y = _make_yaml()
    with open(live_path, "w") as fh:
      y.dump(live, fh)


def sort_keys(live_path: str, sample_path: str, dry_run: bool = False) -> None:
  """Reorder live keys/sections to match sample order.

  Sections absent from sample go at the end; within each section, keys absent
  from sample go at the end (alphabetically stable among themselves).
  """
  live = _load(live_path)
  sample = _load(sample_path)

  sample_section_order = {s: i for i, s in enumerate(sample)}

  def _section_sort_key(sec: str) -> tuple:
    return (sample_section_order.get(sec, len(sample_section_order)), sec)

  sorted_sections = sorted(live.keys(), key=_section_sort_key)

  reordered: CommentedMap = CommentedMap()
  for section in sorted_sections:
    l_val = live[section]
    if not isinstance(l_val, dict) or section not in sample or not isinstance(sample[section], dict):
      reordered[section] = l_val
      continue

    s_keys_order = {k: i for i, k in enumerate(sample[section])}

    def _key_sort_key(k: str) -> tuple:
      return (s_keys_order.get(k, len(s_keys_order)), k)

    sorted_section_keys = sorted(l_val.keys(), key=_key_sort_key)
    new_section: CommentedMap = CommentedMap()
    for k in sorted_section_keys:
      new_section[k] = l_val[k]
    reordered[section] = new_section

  if not dry_run:
    y = _make_yaml()
    with open(live_path, "w") as fh:
      y.dump(reordered, fh)


def _diff_summary(live_path: str, sample_path: str) -> None:
  """Print a summary of keys missing from live and keys extra in live."""
  live = parse_keys(live_path)
  sample = parse_keys(sample_path)

  missing: list[str] = []
  extra: list[str] = []

  for section, s_keys in sample.items():
    if _should_skip(section):
      continue
    if not isinstance(s_keys, dict):
      if section not in live:
        missing.append(f"  missing: {section}")
      continue
    for key in s_keys:
      if key not in (live.get(section) or {}):
        missing.append(f"  missing: [{section}] {key}")

  for section, l_keys in live.items():
    if _should_skip(section):
      continue
    if not isinstance(l_keys, dict):
      if section not in sample:
        extra.append(f"  extra:   {section}")
      continue
    for key in l_keys:
      if key not in (sample.get(section) or {}):
        extra.append(f"  extra:   [{section}] {key}")

  if missing or extra:
    for line in missing + extra:
      print(line)
  else:
    print("  (no differences)")


if __name__ == "__main__":
  import argparse

  parser = argparse.ArgumentParser(
    description="Merge/audit YAML config files against a sample reference.",
  )
  parser.add_argument("live", help="Path to the live config file (modified in place)")
  parser.add_argument("sample", help="Path to the sample config file (read-only reference)")
  parser.add_argument("-n", "--dry-run", action="store_true", help="Print what would change without writing")
  parser.add_argument("--additions", action="store_true", help="Add missing keys from sample to live")
  parser.add_argument("--deprecate", action="store_true", help="Comment out live-only keys not in sample")
  parser.add_argument("--sort", action="store_true", help="Reorder live keys to match sample order")
  args = parser.parse_args()

  if not any([args.additions, args.deprecate, args.sort]):
    _diff_summary(args.live, args.sample)
  else:
    if args.additions:
      add_missing(args.live, args.sample, dry_run=args.dry_run)
    if args.deprecate:
      deprecate_removed(args.live, args.sample, dry_run=args.dry_run)
    if args.sort:
      sort_keys(args.live, args.sample, dry_run=args.dry_run)
