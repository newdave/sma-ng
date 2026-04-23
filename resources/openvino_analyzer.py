"""OpenVINO analyzer backend scaffolding with explicit device validation."""

import importlib
from dataclasses import dataclass
from typing import Any

from resources.analyzer import AnalyzerObservations

OPENVINO_DEVICE_MODES = {"AUTO", "HETERO", "MULTI"}
OPENVINO_EXECUTION_DEVICES = {"CPU", "GPU", "NPU"}


class OpenVINOAnalyzerError(RuntimeError):
  """Base error for OpenVINO analyzer failures."""


class OpenVINODependencyError(OpenVINOAnalyzerError):
  """Raised when the optional OpenVINO runtime dependency is unavailable."""


class OpenVINODeviceError(OpenVINOAnalyzerError):
  """Raised when the requested OpenVINO device configuration is invalid."""


@dataclass(slots=True)
class OpenVINOBackendConfig:
  """Typed OpenVINO backend configuration."""

  device: str = "AUTO"
  model_dir: str | None = None
  cache_dir: str | None = None
  max_frames: int = 12
  target_width: int = 960


def normalize_openvino_device(device: str | None) -> str:
  """Normalize a user-facing OpenVINO device string.

  Supports direct devices like ``CPU``, ``GPU``, and ``NPU`` plus composite
  selectors such as ``AUTO:NPU,CPU`` or ``MULTI:NPU,GPU``.
  """

  raw = (device or "AUTO").strip().upper()
  if not raw:
    return "AUTO"

  if ":" not in raw:
    if raw not in OPENVINO_DEVICE_MODES | OPENVINO_EXECUTION_DEVICES:
      raise OpenVINODeviceError("Unsupported OpenVINO device '%s'." % raw)
    return raw

  mode, targets = raw.split(":", 1)
  mode = mode.strip().upper()
  if mode not in OPENVINO_DEVICE_MODES:
    raise OpenVINODeviceError("Unsupported OpenVINO device mode '%s'." % mode)

  normalized_targets = []
  for target in targets.split(","):
    candidate = target.strip().upper()
    if not candidate:
      continue
    if candidate not in OPENVINO_EXECUTION_DEVICES:
      raise OpenVINODeviceError("Unsupported OpenVINO execution device '%s'." % candidate)
    if candidate not in normalized_targets:
      normalized_targets.append(candidate)

  if not normalized_targets:
    raise OpenVINODeviceError("OpenVINO device mode '%s' requires at least one target device." % mode)

  return "%s:%s" % (mode, ",".join(normalized_targets))


def _available_device_families(available_devices: list[str] | tuple[str, ...]) -> set[str]:
  return {device.split(".", 1)[0].upper() for device in available_devices}


def _requested_device_families(device: str) -> set[str]:
  normalized = normalize_openvino_device(device)
  if ":" not in normalized:
    return {normalized} if normalized in OPENVINO_EXECUTION_DEVICES else set()
  return {part for part in normalized.split(":", 1)[1].split(",") if part}


def ensure_requested_devices_available(device: str, available_devices: list[str] | tuple[str, ...]) -> str:
  """Validate that requested OpenVINO execution devices exist on the host."""

  normalized = normalize_openvino_device(device)
  requested = _requested_device_families(normalized)
  if not requested:
    return normalized

  available = _available_device_families(available_devices)
  missing = sorted(requested - available)
  if missing:
    raise OpenVINODeviceError("Requested OpenVINO devices are unavailable: %s." % ", ".join(missing))

  return normalized


class OpenVINOAnalyzerBackend:
  """Optional OpenVINO-backed analyzer runtime wrapper."""

  def __init__(self, analyzer_config: dict[str, Any] | None = None):
    analyzer_config = analyzer_config or {}
    self.config = OpenVINOBackendConfig(
      device=analyzer_config.get("device", "AUTO"),
      model_dir=analyzer_config.get("model_dir"),
      cache_dir=analyzer_config.get("cache_dir"),
      max_frames=analyzer_config.get("max_frames", 12),
      target_width=analyzer_config.get("target_width", 960),
    )
    self.device = normalize_openvino_device(self.config.device)

  def _import_openvino(self):
    try:
      return importlib.import_module("openvino")
    except ImportError as exc:
      raise OpenVINODependencyError("OpenVINO analyzer support requires the optional openvino dependency.") from exc

  def create_core(self):
    """Create an OpenVINO Core instance using either the top-level or runtime API."""

    openvino = self._import_openvino()
    if hasattr(openvino, "Core"):
      core = openvino.Core()
    elif hasattr(openvino, "runtime") and hasattr(openvino.runtime, "Core"):
      core = openvino.runtime.Core()
    else:
      raise OpenVINODependencyError("Installed OpenVINO package does not expose a Core runtime.")

    if self.config.cache_dir and hasattr(core, "set_property"):
      try:
        core.set_property({"CACHE_DIR": self.config.cache_dir})
      except Exception:
        pass

    return core

  def available_devices(self, core=None) -> list[str]:
    """Return the list of available OpenVINO devices."""

    core = core or self.create_core()
    return list(getattr(core, "available_devices", []))

  def validate_device(self, core=None) -> str:
    """Validate the configured OpenVINO device against the runtime."""

    core = core or self.create_core()
    return ensure_requested_devices_available(self.device, self.available_devices(core))

  def analyze(self, *args, core=None, **kwargs) -> AnalyzerObservations:
    """Validate runtime/device availability and return placeholder observations."""

    self.validate_device(core=core)
    return AnalyzerObservations()
