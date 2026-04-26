#!/usr/bin/env python3
"""Schema-driven generator for ``setup/sma-ng.yml.sample``.

Default invocation rewrites ``setup/sma-ng.yml.sample`` from the pydantic
schema in ``resources/config_schema.py``, including illustrative entries
for ``profiles.rq`` / ``profiles.lq``, ``services.{sonarr,radarr,plex}``
instances, and ``daemon.routing`` rules.

``--check`` regenerates into memory and exits non-zero (printing a
unified diff) if the committed sample differs. CI uses this mode to
prevent hand-edits from drifting away from the schema.
"""

from __future__ import annotations

import argparse
import difflib
import io
import os
import sys
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from resources.config_schema import SmaConfig  # noqa: E402

SAMPLE_PATH = REPO_ROOT / "setup" / "sma-ng.yml.sample"


def _to_commented(obj):
  if isinstance(obj, dict):
    cm = CommentedMap()
    for k, v in obj.items():
      cm[k] = _to_commented(v)
    return cm
  if isinstance(obj, list):
    return [_to_commented(x) for x in obj]
  return obj


def _illustrative_profiles() -> dict:
  return {
    "rq": {
      "video": {"codec": ["h265"], "max-bitrate": 8000},
      "audio": {"codec": ["ac3", "aac"]},
    },
    "lq": {
      "video": {"codec": ["h264"], "max-bitrate": 3000, "preset": "fast"},
      "audio": {"codec": ["aac"], "max-channels": 2},
    },
  }


def _illustrative_services() -> dict:
  return {
    "sonarr": {
      "main": {
        "url": "http://localhost:8989",
        "apikey": "",
        "rescan": True,
        "force-rename": False,
        "in-progress-check": True,
        "block-reprocess": False,
      },
      "kids": {
        "url": "http://localhost:8990",
        "apikey": "",
      },
    },
    "radarr": {
      "main": {
        "url": "http://localhost:7878",
        "apikey": "",
        "rescan": True,
      },
    },
    "plex": {
      "main": {
        "url": "http://localhost:32400",
        "token": "",
        "refresh": True,
        "ignore-certs": False,
        "path-mapping": "",
        "plexmatch": True,
      },
    },
  }


def _illustrative_routing() -> list[dict]:
  return [
    {"match": "/mnt/media/TV", "profile": "rq", "services": ["sonarr.main"]},
    {"match": "/mnt/media/TV/Kids", "profile": "lq", "services": ["sonarr.kids"]},
    {"match": "/mnt/media/Movies", "profile": "rq", "services": ["radarr.main"]},
  ]


_SECTION_COMMENTS: dict[str, str] = {
  "daemon": (
    "── Daemon settings ──────────────────────────────────────────────────\n"
    "Only used when running daemon.py. manual.py ignores this section.\n"
    "Per-flag precedence: CLI > env (SMA_DAEMON_*) > sma-ng.yml > default."
  ),
  "base": ("── Base media-conversion settings ───────────────────────────────────\nDefaults applied to every conversion. Profiles below shallow-merge on top\nof these per-section."),
  "profiles": (
    "── Profiles ─────────────────────────────────────────────────────────\n"
    "Named overlays referenced from daemon.routing[].profile or via\n"
    "`manual.py --profile <name>`. Only the sections/keys listed here\n"
    "override base; everything else passes through unchanged."
  ),
  "services": (
    "── Services ─────────────────────────────────────────────────────────\n"
    "Sonarr / Radarr / Plex instances. Reference each one from\n"
    "daemon.routing[].services as `<type>.<instance>` (e.g. `sonarr.kids`).\n"
    "Downloader integrations (SAB/Deluge/qBittorrent/uTorrent) are now\n"
    "shell-trigger-only and live in triggers/, not here."
  ),
}


def build_sample_yaml() -> bytes:
  """Render the committed sample bytes from the schema."""

  cfg = SmaConfig()
  data = cfg.model_dump(by_alias=True, mode="python")

  # ScanPath / PathRewrite / RoutingRule lists default to [] — drop them
  # from the daemon block so the sample only carries illustrative entries.
  data["daemon"]["routing"] = _illustrative_routing()
  data["profiles"] = _illustrative_profiles()
  data["services"] = _illustrative_services()

  # Force the canonical four-bucket order regardless of dict insertion.
  ordered = CommentedMap()
  for key in ("daemon", "base", "profiles", "services"):
    ordered[key] = _to_commented(data[key])
    ordered.yaml_set_comment_before_after_key(key, before=_SECTION_COMMENTS[key])

  yaml = YAML(typ="rt")
  yaml.default_flow_style = False
  yaml.width = 120
  yaml.indent(mapping=2, sequence=4, offset=2)

  buf = io.BytesIO()
  yaml.dump(ordered, buf)
  return buf.getvalue()


def _read_committed() -> bytes:
  if not SAMPLE_PATH.exists():
    return b""
  return SAMPLE_PATH.read_bytes()


def _diff(expected: bytes, actual: bytes) -> str:
  return "".join(
    difflib.unified_diff(
      actual.decode("utf-8").splitlines(keepends=True),
      expected.decode("utf-8").splitlines(keepends=True),
      fromfile=str(SAMPLE_PATH.relative_to(REPO_ROOT)) + " (committed)",
      tofile=str(SAMPLE_PATH.relative_to(REPO_ROOT)) + " (generated)",
    )
  )


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--check",
    action="store_true",
    help="exit non-zero (printing a unified diff) if the committed sample differs from the schema-generated output",
  )
  args = parser.parse_args()

  generated = build_sample_yaml()

  if args.check:
    committed = _read_committed()
    if committed == generated:
      return 0
    sys.stdout.write(_diff(generated, committed))
    sys.stderr.write("\nsetup/sma-ng.yml.sample is out of sync with the schema. Run `mise run config:sample` to regenerate.\n")
    return 1

  os.makedirs(SAMPLE_PATH.parent, exist_ok=True)
  SAMPLE_PATH.write_bytes(generated)
  return 0


if __name__ == "__main__":
  sys.exit(main())
