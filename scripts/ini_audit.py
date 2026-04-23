"""Audit autoProcess.ini files against a sample and cross-check with daemon.json.

Exit code 0: no issues found.
Exit code 1: one or more issues found.

Usage:
  python3 scripts/ini_audit.py --sample setup/autoProcess.ini.sample \\
      --ini config/autoProcess.ini [config/autoProcess.lq.ini ...] \\
      [--daemon config/daemon.json] [--json]

Checks performed:
  Per-INI:
    - Sections present in the sample but absent from the live file (warning)
    - Keys present in a sample section but absent from the corresponding live
      section (warning: missing key)
    - Keys present in the live file but absent from the sample (info: deprecated)

  Cross-file (when --daemon is provided):
    - ffmpeg_dir in daemon.json vs [Converter] ffmpeg / ffprobe in the INI
      files.  If ffmpeg_dir is set and an INI contains absolute paths for
      ffmpeg/ffprobe that are NOT inside ffmpeg_dir, that is a conflict.
      Bare names (e.g. "ffmpeg") are always considered consistent.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

# Allow importing parse_keys from the shared deploy lib without installing it.
_LIB_DIR = os.path.join(os.path.dirname(__file__), "..", ".mise", "shared", "deploy", "lib")
sys.path.insert(0, os.path.abspath(_LIB_DIR))
from ini_merge import parse_keys  # noqa: E402

# ── Finding dataclass ────────────────────────────────────────────────────────


@dataclass
class Finding:
  level: str  # 'error' | 'warning' | 'info'
  source: str  # path of the file that triggered the finding
  section: str  # INI section name (or '' for file-level findings)
  key: str  # INI key name (or '' for section-level findings)
  message: str

  def as_dict(self) -> dict:
    return {
      "level": self.level,
      "source": self.source,
      "section": self.section,
      "key": self.key,
      "message": self.message,
    }

  def __str__(self) -> str:
    loc = self.source
    if self.section:
      loc += f"  [{self.section}]"
    if self.key:
      loc += f"  {self.key}"
    return f"{self.level.upper():<8} {loc}\n         {self.message}"


# ── Per-INI audit ────────────────────────────────────────────────────────────


def audit_ini(sample_path: str, live_path: str) -> list[Finding]:
  """Compare a live INI against the sample and return findings."""
  sample_secs = parse_keys(sample_path)
  live_secs = parse_keys(live_path)
  findings: list[Finding] = []

  for sec, sample_keys in sample_secs.items():
    if sec not in live_secs:
      findings.append(
        Finding(
          level="warning",
          source=live_path,
          section=sec,
          key="",
          message=f"Section [{sec}] is present in the sample but missing from this file.",
        )
      )
      continue

    live_keys = live_secs[sec]

    # Missing keys
    for key in sample_keys:
      if key not in live_keys:
        findings.append(
          Finding(
            level="warning",
            source=live_path,
            section=sec,
            key=key,
            message=f'Key "{key}" is in the sample but absent from this file.',
          )
        )

    # Deprecated (live-only) keys
    for key in live_keys:
      if key not in sample_keys:
        findings.append(
          Finding(
            level="info",
            source=live_path,
            section=sec,
            key=key,
            message=f'Key "{key}" is not in the sample (possibly deprecated).',
          )
        )

  # Sections in live but not in sample
  for sec in live_secs:
    if sec not in sample_secs:
      findings.append(
        Finding(
          level="info",
          source=live_path,
          section=sec,
          key="",
          message=f"Section [{sec}] is not in the sample (custom or deprecated section).",
        )
      )

  return findings


# ── Cross-file audit ─────────────────────────────────────────────────────────

# Rules: (daemon_key, ini_section, ini_key)
# When the daemon key is an absolute directory path, the INI key must either
# be a bare name (no path separator) or an absolute path inside that directory.
CROSS_FILE_RULES: list[tuple[str, str, str]] = [
  ("ffmpeg_dir", "Converter", "ffmpeg"),
  ("ffmpeg_dir", "Converter", "ffprobe"),
]


def _is_bare(value: str) -> bool:
  """Return True if the value contains no path separator (bare binary name)."""
  return os.sep not in value and "/" not in value


def _inside_dir(path: str, directory: str) -> bool:
  """Return True if path starts with directory (normalised)."""
  norm_dir = os.path.normpath(directory)
  norm_path = os.path.normpath(path)
  return norm_path.startswith(norm_dir + os.sep) or norm_path == norm_dir


def audit_cross_file(
  daemon_path: str,
  ini_paths: list[str],
) -> list[Finding]:
  """Check consistency between daemon.json ffmpeg_dir and INI ffmpeg/ffprobe paths."""
  findings: list[Finding] = []

  try:
    with open(daemon_path) as f:
      daemon_cfg = json.load(f)
  except (OSError, json.JSONDecodeError) as exc:
    findings.append(
      Finding(
        level="error",
        source=daemon_path,
        section="",
        key="",
        message=f"Could not load daemon.json: {exc}",
      )
    )
    return findings

  for daemon_key, ini_section, ini_key in CROSS_FILE_RULES:
    daemon_val: Optional[str] = daemon_cfg.get(daemon_key)
    if not daemon_val:
      # daemon_dir not set — nothing to cross-check
      continue

    for ini_path in ini_paths:
      ini_secs = parse_keys(ini_path)
      ini_val = ini_secs.get(ini_section, {}).get(ini_key)
      if ini_val is None:
        continue  # key not present — covered by audit_ini

      if _is_bare(ini_val):
        # Bare name is always consistent — the OS PATH will resolve it,
        # and ffmpeg_dir is prepended to PATH by the daemon.
        continue

      if not _inside_dir(ini_val, daemon_val):
        findings.append(
          Finding(
            level="warning",
            source=ini_path,
            section=ini_section,
            key=ini_key,
            message=(f'"{ini_key} = {ini_val}" is an absolute path outside daemon.json {daemon_key} ({daemon_val!r}).  Use a bare name or a path inside {daemon_val!r}.'),
          )
        )

  return findings


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
  import argparse
  import glob as _glob

  parser = argparse.ArgumentParser(
    description="Audit autoProcess.ini files against a sample.",
  )
  parser.add_argument("--sample", required=True, help="Path to autoProcess.ini.sample")
  parser.add_argument("--ini", nargs="+", required=True, help="One or more live INI files (globs are expanded by the shell)")
  parser.add_argument("--daemon", default="", help="Path to daemon.json for cross-file checks")
  parser.add_argument("--json", action="store_true", help="Output findings as a JSON array")
  args = parser.parse_args()

  # Expand any globs that the shell didn't expand (e.g., on Windows)
  ini_paths: list[str] = []
  for pattern in args.ini:
    expanded = _glob.glob(pattern)
    ini_paths.extend(expanded if expanded else [pattern])

  all_findings: list[Finding] = []

  for ini_path in ini_paths:
    all_findings.extend(audit_ini(args.sample, ini_path))

  if args.daemon:
    all_findings.extend(audit_cross_file(args.daemon, ini_paths))

  if args.json:
    import json as _json

    print(_json.dumps([f.as_dict() for f in all_findings], indent=2))
  else:
    if not all_findings:
      print("No issues found.")
    else:
      for finding in all_findings:
        print(finding)

  sys.exit(1 if all_findings else 0)


if __name__ == "__main__":
  main()
