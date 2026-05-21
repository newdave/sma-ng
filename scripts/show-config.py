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
import copy
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


def _default_source() -> tuple[str, str]:
  """Pick the most operator-useful default config source.

  Returns ``(path, label)`` where ``label`` is a short human-readable
  description.

  **Default is the synthesize path** — operators iterating on
  ``setup/local.yml`` want to see/validate what *would* deploy, not
  whatever stale file may have been left behind from an earlier
  deploy:config run or a previous daemon start. Synthesize mirrors
  exactly what ``stamp_daemon`` would produce on the next deploy
  (minus credential stamping, which only matters when pushing).

  The other source paths are still selectable via ``--config <path>``
  when an operator wants to inspect a specific file (the live deployed
  one, a staged file, or an arbitrary path).
  """
  if (ROOT / "setup" / "local.yml").is_file() and (ROOT / "setup" / "sma-ng.yml.sample").is_file():
    return "synthesize", "synthesized from setup/local.yml + setup/sma-ng.yml.sample"
  # Fallbacks when setup/local.yml isn't present (e.g. fresh clone before
  # operator config exists, or running on the deployed host where there's
  # no local.yml). Try the live and staged files in order.
  candidates = [
    (ROOT / "config" / "sma-ng.yml", "config/sma-ng.yml"),
  ]
  staging = ROOT / ".deploy-staging"
  if staging.is_dir():
    for host_dir in sorted(staging.iterdir()):
      candidate = host_dir / "config" / "sma-ng.yml"
      if candidate.is_file():
        candidates.append((candidate, f".deploy-staging/{host_dir.name}/config/sma-ng.yml"))
        break
  for path, label in candidates:
    if path.is_file():
      return str(path), label
  return "synthesize", "synthesized from setup/local.yml + setup/sma-ng.yml.sample"


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
  """Recursive merge: dicts merge, everything else in src overwrites dst."""
  for key, value in src.items():
    if isinstance(value, dict) and isinstance(dst.get(key), dict):
      _deep_merge(dst[key], value)
    else:
      dst[key] = value
  return dst


def _synthesize_config() -> dict[str, Any]:
  """Build the four-bucket sma-ng.yml dict by merging local.yml onto sample.

  Mirrors what ``stamp_daemon.py`` does at deploy time, minus credential
  stamping (which only matters when pushing to a host).

  **Authoritative buckets:** ``services`` and ``profiles`` in the
  rendered output come entirely from ``setup/local.yml``. Any service
  instances or profile definitions seeded by ``setup/sma-ng.yml.sample``
  are discarded before the merge. Mirrors the stamp_daemon
  authoritative-mode fix (commit 97a682f) so:

    * Sample-seeded ``services.<type>.main`` placeholders with no
      credentials don't bleed into the validated output.
    * Sample profile fields (e.g. ``profiles.rq.video.max-bitrate: 8000``)
      don't override operator intent when the operator's local.yml
      profile relies on inheriting from ``base:``.

  ``base`` and ``daemon`` keep the additive deep-merge: the sample's
  base defaults are useful starting values and the operator usually
  only overrides a handful of fields.
  """
  sample = ROOT / "setup" / "sma-ng.yml.sample"
  local = ROOT / "setup" / "local.yml"
  if not sample.is_file():
    sys.stderr.write(f"error: missing schema sample: {sample}\n")
    sys.exit(1)
  with open(sample) as f:
    merged = yaml.safe_load(f) or {}
  # Wipe sample-seeded services and profiles — local.yml is authoritative
  # for both. This matches stamp_daemon's behaviour for services and
  # extends the same guarantee to profiles.
  merged.pop("services", None)
  merged.pop("profiles", None)
  if local.is_file():
    with open(local) as f:
      local_data = yaml.safe_load(f) or {}
    for bucket in ("daemon", "base"):
      block = local_data.get(bucket)
      if isinstance(block, dict):
        merged.setdefault(bucket, {})
        _deep_merge(merged[bucket], copy.deepcopy(block))
    # Authoritative buckets — replace, don't merge.
    for bucket in ("profiles", "services"):
      block = local_data.get(bucket)
      if isinstance(block, dict):
        merged[bucket] = copy.deepcopy(block)
  # Strip routing-only metadata from services. `path` and `profile` are
  # consumed by stamp_daemon to build `daemon.routing` and are not part
  # of any service-instance schema. Leaving them would surface as
  # "Unknown config key" warnings on load. Mirrors stamp_daemon's
  # ROUTING_ONLY_KEYS filter.
  routing_only = {"path", "profile"}
  for instances in (merged.get("services") or {}).values():
    if not isinstance(instances, dict):
      continue
    for fields in instances.values():
      if not isinstance(fields, dict):
        continue
      for k in list(fields):
        if k in routing_only:
          del fields[k]
  return merged


def _parse_args() -> argparse.Namespace:
  p = argparse.ArgumentParser(
    description="Render the effective SMA-NG config for a given profile.",
  )
  p.add_argument(
    "--config",
    "-c",
    default=None,
    help="Path to sma-ng.yml (default: auto-detect from config/, .deploy-staging/, or synthesize from setup/local.yml + setup/sma-ng.yml.sample).",
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
  p.add_argument(
    "--input",
    "-i",
    default=None,
    help="Path to a real media file. Probes the file, runs generateOptions, and prints the full FFmpeg command that would be executed under the chosen profile — without actually transcoding. Pairs naturally with --profile.",
  )
  return p.parse_args()


def _load(config_path: str) -> SmaConfig:
  if config_path == "synthesize":
    try:
      raw = _synthesize_config()
      return SmaConfig.model_validate(raw)
    except Exception as exc:
      sys.stderr.write(f"error synthesizing config: {exc}\n")
      sys.exit(1)
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
  """Return the resolved base block as a kebab-cased dict.

  Uses ``mode="json"`` so enum fields (``FallbackPolicy``) serialise to
  their string values rather than the native Python enum object — PyYAML
  can't represent the latter and would raise ``RepresenterError``.
  """
  loader = ConfigLoader(logger=logging.getLogger("show-config"))
  base = loader.apply_profile(cfg, profile)
  return base.model_dump(by_alias=True, exclude_none=False, mode="json")


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
    # Distinguish "section never existed" from "section exists but the
    # current view filtered it to empty" (e.g. --diff with a profile
    # that doesn't override that section). The latter is not an error.
    sys.stdout.write(f"# section '{section}' has no entries in this view (available: {sorted(data) or 'none'})\n")
    return {}
  return {section: data[section]}


def _emit(data: Any, fmt: str) -> None:
  if fmt == "json":
    json.dump(data, sys.stdout, indent=2, sort_keys=False, default=str)
    sys.stdout.write("\n")
  else:
    yaml.safe_dump(data, sys.stdout, sort_keys=False, default_flow_style=False)


def _render_ffmpeg_preview(input_path: str, profile: str | None, config_path: str | None, fmt: str) -> int:
  """Probe ``input_path`` and print the FFmpeg command that would run.

  Calls ``MediaProcessor.jsonDump`` directly — same call-path
  ``manual.py -oo`` ultimately reaches, but bypassing manual.py's
  logger lets us emit the full untruncated JSON and pretty-print it.

  When ``config_path`` is ``"synthesize"`` (the default), the
  synthesized config is written to a temp file first so ``ReadSettings``
  can load it like any other config. The temp file is cleaned up on
  exit.
  """
  import json
  import tempfile

  input_file = Path(input_path)
  if not input_file.exists():
    sys.stderr.write(f"error: input file not found: {input_path}\n")
    return 1

  temp_cfg: Path | None = None
  try:
    if config_path == "synthesize" or config_path is None:
      synthesized = _synthesize_config()
      # The synthesized config carries the deploy host's ffmpeg path
      # (e.g. /usr/local/bin). If that path doesn't exist on the
      # current host (running this preview locally on macOS while the
      # daemon runs on Linux), MediaProcessor.isValidSource silently
      # returns None and the dump crashes downstream. Detect and
      # override with what's actually on PATH so the preview works
      # regardless of where it's invoked.
      import shutil

      local_ffmpeg = shutil.which("ffmpeg")
      local_ffprobe = shutil.which("ffprobe")
      configured_ffmpeg = ((synthesized.get("base") or {}).get("converter") or {}).get("ffmpeg") or ""
      configured_exists = bool(configured_ffmpeg) and Path(configured_ffmpeg).is_file()
      if local_ffmpeg and not configured_exists:
        synthesized.setdefault("base", {}).setdefault("converter", {})
        synthesized["base"]["converter"]["ffmpeg"] = local_ffmpeg
        if local_ffprobe:
          synthesized["base"]["converter"]["ffprobe"] = local_ffprobe
        synthesized.setdefault("daemon", {})["ffmpeg-dir"] = str(Path(local_ffmpeg).parent)
        sys.stdout.write(f"# note: configured ffmpeg path missing on this host; using {local_ffmpeg} for the preview\n")
      with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", prefix="sma-ng.show.", delete=False) as tmp:
        yaml.safe_dump(synthesized, tmp, sort_keys=False, default_flow_style=False)
        temp_cfg = Path(tmp.name)
      cfg_path = str(temp_cfg)
    else:
      cfg_path = config_path

    # Lazy imports — heavy modules. Keep them out of the validator's
    # hot path.
    from resources.mediaprocessor import MediaProcessor  # noqa: E402
    from resources.readsettings import ReadSettings  # noqa: E402

    settings = ReadSettings(configFile=cfg_path, profile=profile)
    mp = MediaProcessor(settings)
    payload_json = mp.jsonDump(str(input_file), tagdata=None)
    try:
      payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError):
      # jsonDump may already return a dict on some paths; or the dump
      # may be non-JSON for an unsupported source. Pass through.
      sys.stdout.write(str(payload_json) + "\n")
      return 0

    # If the source was rejected (no audio, blacklisted extension, etc.)
    # the input dict only carries an "error" placeholder. ffprobe the
    # file inline to tell the operator *why* the source was rejected.
    if isinstance(payload.get("input"), dict) and payload["input"].get("error"):
      sys.stderr.write("\n# input rejected by isValidSource — diagnosing with ffprobe:\n")
      ffprobe = settings.ffmpeg.replace("ffmpeg", "ffprobe") if hasattr(settings, "ffmpeg") else "ffprobe"
      try:
        import subprocess

        probe = subprocess.run(
          [ffprobe, "-v", "error", "-show_entries", "stream=index,codec_type,codec_name", "-of", "default=noprint_wrappers=1", str(input_file)],
          check=False,
          capture_output=True,
          text=True,
        )
        sys.stderr.write(probe.stdout)
        if probe.stderr:
          sys.stderr.write(probe.stderr)
      except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"# (ffprobe call failed: {exc})\n")
      sys.stderr.write(
        "# Likely cause: SMA-NG requires at least 1 video AND 1 audio stream.\n"
        "# Files missing audio (typical for camera/drone clips and animated GIFs)\n"
        "# are rejected before option-generation can run. Add an audio track or\n"
        "# point --input at a regular media file.\n"
      )

    if fmt == "json":
      sys.stdout.write(json.dumps(payload, indent=2, sort_keys=False))
      sys.stdout.write("\n")
    else:
      sys.stdout.write(yaml.safe_dump(payload, sort_keys=False, default_flow_style=False))
    return 0
  except Exception as exc:  # noqa: BLE001 — surface any failure as a clean error
    sys.stderr.write(f"error generating preview: {exc}\n")
    return 1
  finally:
    if temp_cfg is not None and temp_cfg.exists():
      try:
        temp_cfg.unlink()
      except OSError:
        pass


def main() -> int:
  args = _parse_args()
  if args.config is None:
    args.config, source_label = _default_source()
  else:
    source_label = args.config

  if args.input:
    if not Path(args.input).exists():
      sys.stderr.write(f"error: input file not found: {args.input}\n")
      return 1
    sys.stdout.write(f"# source: {source_label}\n")
    sys.stdout.write(f"# input:  {args.input}\n")
    sys.stdout.write(f"# profile: {args.profile or '(none)'}\n")
    sys.stdout.write("# generating FFmpeg command via manual.py -oo (no transcode performed)\n")
    sys.stdout.write("# " + "-" * 70 + "\n")
    sys.stdout.flush()
    return _render_ffmpeg_preview(args.input, args.profile, args.config, args.format)

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
    header = f"# source: {source_label}\n# diff: base vs profile '{args.profile}' (only fields the overlay changes)\n"
  else:
    data = _resolve(cfg, args.profile)
    header = f"# source: {source_label}\n# resolved config: profile={args.profile or '(none, raw base)'}\n"

  data = _filter_section(data, args.section)

  if args.format == "yaml":
    sys.stdout.write(header)
  _emit(data, args.format)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
