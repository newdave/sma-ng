#!/usr/bin/env python3
"""Validate the effective SMA-NG configuration and surface misconfigurations.

Same source-detection as ``scripts/show-config.py``: looks at
``config/sma-ng.yml`` → ``.deploy-staging/<host>/config/sma-ng.yml`` →
synthesize-from-local-yml-plus-sample. Validates the rendered config
against the pydantic schema, then runs a suite of operator-facing checks
on top of schema validation:

* Schema validation errors (always fatal).
* Unknown config keys (typo detection).
* Suspicious encoder configuration (QSV-only flags under ``vaapi:``,
  VAAPI-only flags under ``qsv:``, codec-parameters that still contain
  encoder-specific tokens the runtime would filter at job time).
* Routing references to missing services or undefined profiles.
* Services with both a ``path`` and ``_defaults``-only data missing
  ``url``/``apikey``.

Exit codes:

* ``0`` — clean (no warnings, no errors).
* ``1`` — warnings only (suspicious but not necessarily broken).
* ``2`` — schema errors or other fatal issues.

Output: one line per finding, prefixed ``[error]`` / ``[warn]`` /
``[info]``; followed by a summary count.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
  sys.path.insert(0, str(ROOT))

# Reuse the show-config plumbing for source selection and synthesis so we
# never drift between "what would deploy" and "what we validate."
import importlib.util  # noqa: E402

_show_spec = importlib.util.spec_from_file_location("show_config", str(ROOT / "scripts" / "show-config.py"))
assert _show_spec is not None and _show_spec.loader is not None
show_config = importlib.util.module_from_spec(_show_spec)
sys.modules["show_config"] = show_config
_show_spec.loader.exec_module(show_config)


from resources.config_loader import ConfigError, ConfigLoader  # noqa: E402
from resources.config_schema import SmaConfig  # noqa: E402

# ---------------------------------------------------------------------------
# Finding model
# ---------------------------------------------------------------------------


class Finding:
  __slots__ = ("level", "message", "path")

  def __init__(self, level: str, path: str, message: str) -> None:
    self.level = level
    self.path = path
    self.message = message

  def render(self) -> str:
    tag = {"error": "\033[31m[error]\033[0m", "warn": "\033[33m[warn] \033[0m", "info": "\033[36m[info] \033[0m"}.get(self.level, self.level)
    return f"{tag} {self.path}: {self.message}"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


_QSV_ONLY_TOKENS = {
  "-low_power",
  "-async_depth",
  "-extbrc",
  "-b_strategy",
  "-look_ahead",
  "-look_ahead_depth",
  "-adaptive_i",
  "-adaptive_b",
  "-p_strategy",
  "-rdo",
}
_VAAPI_ONLY_TOKENS = {"-rc_mode", "-compression_level", "-qp"}


def _check_encoder_flag_leaks(cfg: SmaConfig, findings: list[Finding]) -> None:
  """Detect QSV-only or VAAPI-only flag tokens lingering in encoder-agnostic
  ``codec-parameters`` strings — the migration shim should have lifted them
  but a stale config or a typo can leave them behind.
  """
  for section_label, section in (("base.video", cfg.base.video), ("base.hdr", cfg.base.hdr)):
    params = (section.codec_parameters or "").split()
    qsv = [t for t in params if t in _QSV_ONLY_TOKENS]
    vaapi = [t for t in params if t in _VAAPI_ONLY_TOKENS]
    if qsv:
      findings.append(
        Finding(
          "warn",
          f"{section_label}.codec-parameters",
          f"contains QSV-only token(s) {qsv!r} — move under {section_label}.qsv.codec-parameters or typed fields",
        )
      )
    if vaapi:
      findings.append(
        Finding(
          "warn",
          f"{section_label}.codec-parameters",
          f"contains VAAPI-only token(s) {vaapi!r} — move under {section_label}.vaapi.codec-parameters",
        )
      )
  for prof_name, prof in cfg.profiles.items():
    if prof.video is not None:
      params = (prof.video.codec_parameters or "").split()
      qsv = [t for t in params if t in _QSV_ONLY_TOKENS]
      vaapi = [t for t in params if t in _VAAPI_ONLY_TOKENS]
      if qsv:
        findings.append(
          Finding(
            "warn",
            f"profiles.{prof_name}.video.codec-parameters",
            f"contains QSV-only token(s) {qsv!r}",
          )
        )
      if vaapi:
        findings.append(
          Finding(
            "warn",
            f"profiles.{prof_name}.video.codec-parameters",
            f"contains VAAPI-only token(s) {vaapi!r}",
          )
        )


def _check_routing_references(cfg: SmaConfig, findings: list[Finding]) -> None:
  """Routing rules must reference profiles and services that exist."""
  known_profiles = set(cfg.profiles)
  known_services = set()
  for stype, instances in cfg.services.model_dump(by_alias=False, exclude_none=False).items():
    if isinstance(instances, dict):
      for inst_name in instances:
        known_services.add(f"{stype}.{inst_name}")
  for i, rule in enumerate(cfg.daemon.routing or []):
    label = f"daemon.routing[{i}] (match={rule.match!r})"
    if rule.profile and rule.profile not in known_profiles:
      findings.append(
        Finding(
          "error",
          label,
          f"references unknown profile {rule.profile!r}; defined profiles: {sorted(known_profiles)!r}",
        )
      )
    for svc in rule.services or []:
      if svc not in known_services:
        findings.append(
          Finding(
            "error",
            label,
            f"references unknown service {svc!r}; defined services: {sorted(known_services)!r}",
          )
        )


def _check_service_completeness(cfg: SmaConfig, findings: list[Finding]) -> None:
  """Services should have non-empty url + (apikey | token | password) for
  the integration to actually fire.
  """
  for stype in ("sonarr", "radarr"):
    insts = getattr(cfg.services, stype, {}) or {}
    for name, inst in insts.items():
      if not inst.url:
        findings.append(Finding("error", f"services.{stype}.{name}", "missing required `url`"))
      if not inst.apikey:
        findings.append(Finding("warn", f"services.{stype}.{name}", "missing `apikey` — integration will not fire"))
  for stype in ("emby", "jellyfin"):
    insts = getattr(cfg.services, stype, {}) or {}
    for name, inst in insts.items():
      if not inst.url:
        findings.append(Finding("error", f"services.{stype}.{name}", "missing required `url`"))
      if not inst.apikey:
        findings.append(Finding("warn", f"services.{stype}.{name}", "missing `apikey` — refresh calls will fail"))
  for name, inst in (cfg.services.plex or {}).items():
    if not inst.url:
      findings.append(Finding("error", f"services.plex.{name}", "missing required `url`"))
    if not inst.token:
      findings.append(Finding("warn", f"services.plex.{name}", "missing `token` — Plex API calls will fail"))


def _check_codec_list_shapes(cfg: SmaConfig, findings: list[Finding]) -> None:
  """Audio/video codec lists should have a valid first entry."""
  if not cfg.base.video.codec:
    findings.append(Finding("warn", "base.video.codec", "empty list; no transcode target defined"))
  audio = cfg.base.audio.codec
  if audio and audio[0] in ("copy",):
    findings.append(
      Finding(
        "error",
        "base.audio.codec",
        "first entry is 'copy' which is NOT a valid encoder target; "
        "set the first entry to a real encoder (e.g. 'eac3', 'aac') and let "
        "source-codec list-membership trigger copy behaviour automatically",
      )
    )
  for prof_name, prof in cfg.profiles.items():
    if prof.audio is not None and prof.audio.codec and prof.audio.codec[0] in ("copy",):
      findings.append(
        Finding(
          "error",
          f"profiles.{prof_name}.audio.codec",
          "first entry is 'copy' which is NOT a valid encoder target",
        )
      )


def _check_subblock_encoder_alignment(cfg: SmaConfig, findings: list[Finding]) -> None:
  """If gpu is set, warn when the OTHER subblock carries operator data —
  those values will be ignored by the runtime since the active encoder
  won't read them.
  """
  gpu = (cfg.base.video.gpu or "").strip().lower()
  if not gpu:
    return
  if gpu == "qsv":
    vaapi = cfg.base.video.vaapi
    has_data = bool(vaapi.codec_parameters) or bool(vaapi.rc_mode) or vaapi.compression_level > 0 or vaapi.preset != ""
    if has_data:
      findings.append(
        Finding(
          "info",
          "base.video.vaapi",
          "gpu is qsv; vaapi subblock will be read only on the hw_alt fallback tier (or ignored entirely under fallback-policy: hw_only)",
        )
      )
  elif gpu == "vaapi":
    qsv = cfg.base.video.qsv
    has_data = bool(qsv.codec_parameters) or qsv.low_power != -1 or qsv.async_depth > 0
    if has_data:
      findings.append(
        Finding(
          "warn",
          "base.video.qsv",
          "gpu is vaapi but qsv subblock has operator data; those flags will be silently ignored at runtime",
        )
      )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
  p = argparse.ArgumentParser(description="Validate the effective SMA-NG configuration.")
  p.add_argument(
    "--config",
    "-c",
    default=None,
    help="Path to sma-ng.yml (default: auto-detect, same as config:show).",
  )
  p.add_argument(
    "--strict",
    action="store_true",
    help="Exit non-zero on warnings, not just errors.",
  )
  p.add_argument(
    "--quiet",
    "-q",
    action="store_true",
    help="Suppress per-finding output; only the summary line is printed.",
  )
  return p.parse_args()


def _load_config(source: str) -> tuple[SmaConfig | None, list[Finding]]:
  findings: list[Finding] = []
  if source == "synthesize":
    try:
      raw = show_config._synthesize_config()
      return SmaConfig.model_validate(raw), findings
    except Exception as exc:
      findings.append(Finding("error", "schema", f"failed to synthesize/validate config: {exc}"))
      return None, findings
  path = Path(source)
  if not path.is_file():
    findings.append(Finding("error", "schema", f"config file not found: {source}"))
    return None, findings
  loader = ConfigLoader(logger=logging.getLogger("validate-config"))

  # Capture unknown-key warnings that ConfigLoader logs as "Unknown config key: …"
  class _CapturingHandler(logging.Handler):
    def __init__(self) -> None:
      super().__init__()
      self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
      self.records.append(record)

  handler = _CapturingHandler()
  loader.logger.addHandler(handler)
  loader.logger.setLevel(logging.WARNING)
  try:
    cfg = loader.load(str(path))
  except ConfigError as exc:
    findings.append(Finding("error", "schema", str(exc)))
    return None, findings
  for rec in handler.records:
    msg = rec.getMessage()
    if msg.startswith("Unknown config key: "):
      key = msg[len("Unknown config key: ") :]
      findings.append(Finding("warn", key, "unknown key — typo or stale schema entry"))
    elif rec.levelno >= logging.WARNING:
      findings.append(Finding("warn", "schema", msg))
  return cfg, findings


def main() -> int:
  args = _parse_args()
  if args.config is None:
    args.config, source_label = show_config._default_source()
  else:
    source_label = args.config

  cfg, findings = _load_config(args.config)

  if cfg is not None:
    _check_encoder_flag_leaks(cfg, findings)
    _check_routing_references(cfg, findings)
    _check_service_completeness(cfg, findings)
    _check_codec_list_shapes(cfg, findings)
    _check_subblock_encoder_alignment(cfg, findings)

  errors = sum(1 for f in findings if f.level == "error")
  warnings = sum(1 for f in findings if f.level == "warn")
  infos = sum(1 for f in findings if f.level == "info")

  if not args.quiet:
    print(f"# validating: {source_label}")
    for f in findings:
      print(f.render())
    print(f"# summary: {errors} error(s), {warnings} warning(s), {infos} info")
  else:
    print(f"errors={errors} warnings={warnings} infos={infos}")

  if errors:
    return 2
  if warnings and args.strict:
    return 1
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
