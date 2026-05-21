#!/usr/bin/env python3
"""Render the effective resolved SMA-NG configuration.

Resolves the four-bucket sma-ng.yml (daemon / base / profiles / services)
into the *effective* base block that the daemon would use after the named
profile's overlay is applied — equivalent to ``ConfigLoader.apply_profile``
which the runtime calls per job. Useful for answering "what would actually
happen if a file hit profile `rq`?" without reading the daemon's logs.

Usage::

    mise run config:show                     # raw base, no profile applied
    mise run config:show -- --profile rq     # base + rq profile overlay
    mise run config:show -- --profile rq --section video
    mise run config:show -- --profile rq --format json
    mise run config:show -- --profile rq --diff   # only fields the profile changes
    mise run config:show -- --config /opt/sma/config/sma-ng.yml --profile hq

When ``--diff`` is set, only fields where the profile overlay differs from
the base block are shown (skipping every field that passed through
unchanged). Useful for confirming a profile carries only the deltas you
expect.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Allow running as a script from anywhere — pretend we're at repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
  sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from resources.config_loader import ConfigError, ConfigLoader  # noqa: E402
from resources.config_schema import SmaConfig  # noqa: E402


def _parse_args() -> argparse.Namespace:
  p = argparse.ArgumentParser(
    description="Render the effective SMA-NG config for a given profile.",
  )
  default_config = ROOT / "config" / "sma-ng.yml"
  p.add_argument(
    "--config",
    "-c",
    default=str(default_config),
    help="Path to sma-ng.yml (default: config/sma-ng.yml under the repo root).",
  )
  p.add_argument(
    "--profile",
    "-p",
    default=None,
    help="Named profile to overlay onto base. Omit for the raw base block.",
  )
  p.add_argument(
    "--section",
    "-s",
    default=None,
    help="Narrow output to one base section (e.g. 'video', 'hdr', 'audio').",
  )
  p.add_argument(
    "--format",
    "-f",
    choices=("yaml", "json"),
    default="yaml",
    help="Output format (default: yaml).",
  )
  p.add_argument(
    "--diff",
    action="store_true",
    help="With --profile, show only the fields the profile overlay changes relative to the raw base block. Useful for confirming a profile carries only deltas.",
  )
  return p.parse_args()


def _load(config_path: str) -> SmaConfig:
  path = Path(config_path)
  if not path.is_file():
    sys.stderr.write(f"error: config not found: {config_path}\n")
    sys.exit(1)
  loader = ConfigLoader(logger=logging.getLogger("show-config"))
  try:
    return loader.load(str(path))
  except ConfigError as exc:
    sys.stderr.write(f"error: {exc}\n")
    sys.exit(1)


def _resolve(cfg: SmaConfig, profile: str | None) -> dict[str, Any]:
  """Return the resolved base block as a kebab-cased dict."""
  loader = ConfigLoader(logger=logging.getLogger("show-config"))
  base = loader.apply_profile(cfg, profile)
  return base.model_dump(by_alias=True, exclude_none=False)


def _diff(base: dict[str, Any], resolved: dict[str, Any]) -> dict[str, Any]:
  """Recursive deep-diff: keep keys where ``resolved`` differs from ``base``.

  Dicts recurse; lists and scalars are compared by value. Keys absent from
  one side appear with their present value.
  """
  out: dict[str, Any] = {}
  all_keys = set(base) | set(resolved)
  for key in all_keys:
    bv = base.get(key)
    rv = resolved.get(key)
    if isinstance(bv, dict) and isinstance(rv, dict):
      sub = _diff(bv, rv)
      if sub:
        out[key] = sub
    elif bv != rv:
      out[key] = rv
  return out


def _filter_section(data: dict[str, Any], section: str | None) -> Any:
  if section is None:
    return data
  if section not in data:
    sys.stderr.write(f"error: no such section '{section}' (available: {sorted(data)})\n")
    sys.exit(1)
  return {section: data[section]}


def _emit(data: Any, fmt: str) -> None:
  if fmt == "json":
    json.dump(data, sys.stdout, indent=2, sort_keys=False, default=str)
    sys.stdout.write("\n")
  else:
    yaml.safe_dump(data, sys.stdout, sort_keys=False, default_flow_style=False)


def main() -> int:
  args = _parse_args()
  cfg = _load(args.config)

  if args.diff and args.profile is None:
    sys.stderr.write("error: --diff requires --profile\n")
    return 1

  if args.profile and args.profile not in cfg.profiles:
    sys.stderr.write(
      f"error: unknown profile '{args.profile}' (available: {sorted(cfg.profiles)})\n",
    )
    return 1

  if args.diff:
    base_raw = _resolve(cfg, None)
    resolved = _resolve(cfg, args.profile)
    data = _diff(base_raw, resolved)
    header = f"# diff: base vs profile '{args.profile}' (only fields the overlay changes)\n"
  else:
    data = _resolve(cfg, args.profile)
    header = f"# resolved config: profile={args.profile or '(none, raw base)'}\n"

  data = _filter_section(data, args.section)

  if args.format == "yaml":
    sys.stdout.write(header)
  _emit(data, args.format)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
