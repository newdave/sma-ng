"""ConfigLoader — load, validate, and resolve sma-ng.yml.

Public surface:

* ``ConfigError`` — raised for any operator-facing config failure
  (INI pointer, old flat shape, validation error). The message is the
  human-readable error to display at startup.
* ``ConfigLoader.load(path)`` → ``SmaConfig``
* ``ConfigLoader.apply_profile(cfg, profile_name)`` → ``BaseConfig`` with the
  named profile shallow-merged onto base. Mirrors the existing semantic of
  ``readsettings._apply_profile`` (readsettings.py:497-504).
* ``ConfigLoader.resolve_routing(cfg, file_path)`` → ``RoutingResolution``
  with longest-prefix match and bare-base fallback.

Routing precedent: ``resources.daemon.config.PathConfigManager`` already
implements longest-prefix routing for ``path_configs`` and
``path_rewrites`` (config.py:354, 373, 430-475). The same algorithm is
reused here.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from resources import yamlconfig
from resources.config_schema import BaseConfig, RoutingRule, SmaConfig


class ConfigError(Exception):
  """Raised for any operator-facing config failure."""


@dataclass
class RoutingResolution:
  """Result of resolving a single file path against ``daemon.routing``.

  Attributes:
      profile: name of the matched profile, or ``None`` if no rule matched
          or the matched rule used bare base.
      services: list of ``(service_type, instance_name)`` tuples for
          downstream notification. Empty when no rule matched or the
          matched rule explicitly omitted ``services``.
      base: the resolved ``BaseConfig`` — base alone if profile is
          ``None``, otherwise base shallow-merged with the named profile.
  """

  profile: str | None
  services: list[tuple[str, str]]
  base: BaseConfig


class ConfigLoader:
  """Load and validate sma-ng.yml; resolve profiles and routing."""

  _OLD_SHAPE_TOPLEVEL_KEYS = ("converter", "video", "audio", "subtitle", "metadata", "naming", "analyzer", "permissions", "hdr")

  def __init__(self, logger: logging.Logger | None = None) -> None:
    self.logger = logger or logging.getLogger("sma.config")

  # ---------------------------------------------------------------------
  # Loading
  # ---------------------------------------------------------------------

  def load(self, path: str) -> SmaConfig:
    """Load, validate, and return the parsed config.

    Raises ``ConfigError`` for operator-facing failures: an .ini pointer,
    the old flat shape, or any pydantic validation failure. Unknown keys
    are logged at WARNING level and ignored.
    """

    if path.lower().endswith(".ini"):
      raise ConfigError("autoProcess.ini is no longer supported. Convert to sma-ng.yml — see docs/configuration.md.")

    try:
      raw = yamlconfig.load(path)
    except (TypeError, ValueError) as exc:
      raise ConfigError(f"Config file {path!r} is not a YAML mapping at the top level.") from exc

    if not isinstance(raw, dict):
      raise ConfigError(f"Config file {path!r} is not a YAML mapping at the top level.")

    self._reject_old_shape(raw, path)

    try:
      cfg = SmaConfig.model_validate(raw)
    except ValidationError as exc:
      raise ConfigError(f"Config validation failed for {path!r}:\n{exc}") from exc

    self._warn_extras(cfg, prefix="")
    return cfg

  def _reject_old_shape(self, raw: dict, path: str) -> None:
    """Refuse the legacy flat layout with a pointer to the new shape."""
    if "base" in raw:
      return
    legacy_hits = [k for k in self._OLD_SHAPE_TOPLEVEL_KEYS if k in raw]
    if legacy_hits:
      raise ConfigError(
        f"Old flat-shape config detected in {path!r}. "
        f"Found top-level key(s) {legacy_hits!r} that must now live under a `base:` block. "
        "Wrap converter/video/audio/etc. under `base:`, "
        "move sonarr/radarr/plex into `services.<type>.<instance>:`, "
        "and put daemon-only settings under `daemon:`. "
        "See docs/configuration.md for the four-bucket layout."
      )

  def _warn_extras(self, model: BaseModel, prefix: str) -> None:
    """Recursively log a WARNING per unknown key.

    Pydantic captures unknown keys under ``__pydantic_extra__`` when the
    model has ``extra="allow"``. We walk every nested model and dict-of-
    model to surface every dotted path the user might have typo'd.
    """

    extras = getattr(model, "__pydantic_extra__", None) or {}
    for key in extras:
      dotted = f"{prefix}.{key}" if prefix else key
      self.logger.warning("Unknown config key: %s", dotted)

    for name in type(model).model_fields:
      try:
        val = getattr(model, name)
      except AttributeError:
        continue
      child_prefix = f"{prefix}.{name}" if prefix else name
      if isinstance(val, BaseModel):
        self._warn_extras(val, child_prefix)
      elif isinstance(val, dict):
        for k, v in val.items():
          if isinstance(v, BaseModel):
            self._warn_extras(v, f"{child_prefix}.{k}")
      elif isinstance(val, list):
        for i, item in enumerate(val):
          if isinstance(item, BaseModel):
            self._warn_extras(item, f"{child_prefix}[{i}]")

  # ---------------------------------------------------------------------
  # Profile overlay
  # ---------------------------------------------------------------------

  def apply_profile(self, cfg: SmaConfig, profile_name: str | None) -> BaseConfig:
    """Return base shallow-merged with the named profile.

    Mirrors ``readsettings._apply_profile`` (readsettings.py:497-504):
    each section in the profile *replaces* the corresponding section in
    base, but only for the fields the profile actually set. Sections the
    profile doesn't mention pass through unchanged from base.

    A None profile name returns base unchanged (no overlay). An unknown
    name is rejected with ``ConfigError`` — but this should not happen
    because ``SmaConfig._validate_routing_references`` already rejected
    unknown profile names referenced from routing rules; this guard
    catches direct callers (e.g. ``manual.py --profile``).
    """

    if profile_name is None:
      return cfg.base

    if profile_name not in cfg.profiles:
      raise ConfigError(f"Unknown profile: {profile_name!r}. Defined profiles: {sorted(cfg.profiles)!r}")

    overlay = cfg.profiles[profile_name]
    base_data = cfg.base.model_dump(by_alias=True)

    for section_name in BaseConfig.model_fields:
      section_overlay = getattr(overlay, section_name, None)
      if section_overlay is None:
        continue
      overlay_data = section_overlay.model_dump(by_alias=True, exclude_unset=True)
      if not overlay_data:
        continue
      yaml_key = type(cfg.base).model_fields[section_name].alias or section_name
      base_section = base_data.setdefault(yaml_key, {})
      base_section.update(overlay_data)

    return BaseConfig.model_validate(base_data)

  # ---------------------------------------------------------------------
  # Path routing
  # ---------------------------------------------------------------------

  def resolve_routing(self, cfg: SmaConfig, file_path: str) -> RoutingResolution:
    """Resolve a file path through ``daemon.routing``.

    Algorithm (mirrors ``PathConfigManager.get_config_for_path``):

    1. Apply ``daemon.path_rewrites`` longest-prefix to normalise the input
       path.
    2. Walk ``daemon.routing`` rules sorted by match-length descending;
       first prefix match wins.
    3. On match: return the matched rule's profile name (overlaid onto
       base) and parsed service references.
    4. On no match: return bare base and no services.
    """

    normalised = self._normalise(file_path, cfg)
    rules = sorted(cfg.daemon.routing, key=lambda r: len(r.match), reverse=True)
    for rule in rules:
      if self._rule_matches(normalised, rule):
        return RoutingResolution(
          profile=rule.profile,
          services=[self._parse_service_ref(s) for s in rule.services],
          base=self.apply_profile(cfg, rule.profile),
        )
    return RoutingResolution(profile=None, services=[], base=cfg.base)

  def _normalise(self, file_path: str, cfg: SmaConfig) -> str:
    """Apply longest-prefix path_rewrite, then ``os.path.normpath``."""
    rewritten = file_path
    rewrites = sorted(cfg.daemon.path_rewrites, key=lambda r: len(r.from_), reverse=True)
    for r in rewrites:
      if file_path.startswith(r.from_):
        rewritten = r.to + file_path[len(r.from_) :]
        break
    return os.path.normpath(rewritten)

  @staticmethod
  def _rule_matches(path: str, rule: RoutingRule) -> bool:
    """Prefix match against the rule's ``match`` pattern.

    A trailing ``/**`` (or ``/*``) is stripped before matching. A trailing
    slash is appended to the prefix to enforce directory boundaries —
    so a rule for ``/media/tv`` does not match ``/media/tvshow``.
    """
    pattern = rule.match.rstrip("/")
    while pattern.endswith("*"):
      pattern = pattern.rstrip("*").rstrip("/")
    if not pattern:
      return True  # empty rule matches everything (degenerate but legal)
    norm_pattern = os.path.normpath(pattern)
    if path == norm_pattern:
      return True
    return path.startswith(norm_pattern + os.sep)

  @staticmethod
  def _parse_service_ref(ref: str) -> tuple[str, str]:
    """Split ``<type>.<instance>`` — schema validator already enforces shape."""
    stype, sname = ref.split(".", 1)
    return stype, sname


__all__ = ["ConfigError", "ConfigLoader", "RoutingResolution"]
