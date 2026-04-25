"""OpenVINO analyzer backend with frame-level heuristic and model-based analysis."""

import importlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from resources.analyzer import AnalyzerObservations

OPENVINO_DEVICE_MODES = {"AUTO", "HETERO", "MULTI"}
OPENVINO_EXECUTION_DEVICES = {"CPU", "GPU", "NPU"}

# ImageNet-1k class indices that correspond to sports equipment and scenes.
# EfficientNet-B0 is trained on ImageNet; these are approximate proxies.
_SPORTS_CLASS_INDICES = [
  400,  # tennis ball
  401,  # basketball
  402,  # croquet ball
  404,  # golf ball
  406,  # volleyball
  408,  # rugby ball
  422,  # bobsled
  430,  # baseball
  446,  # bicycle-built-for-two
  473,  # football helmet
  508,  # parachute
  509,  # parallel bars
  530,  # ski
  531,  # ski mask
  532,  # sled
  533,  # sleeping bag
  534,  # slot
  535,  # soccer ball
  536,  # soccer jersey
  537,  # soccer shoe
  538,  # soccer stadium
  539,  # speedboat
  540,  # spider web
  541,  # spindle
  542,  # sports car
  543,  # spotlight
  544,  # stage
  545,  # steel arch bridge
  546,  # stethoscope
  547,  # stingray
  548,  # stretcher
  549,  # studio couch
  550,  # stupa
]

_BUNDLED_MODEL_NAME = "scene_classifier"


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
  """OpenVINO-backed analyzer: heuristic frame analysis with optional model inference."""

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

  def _import_numpy(self):
    try:
      return importlib.import_module("numpy")
    except ImportError as exc:
      raise OpenVINODependencyError("OpenVINO analyzer requires numpy. Install via: pip install -r setup/requirements-openvino.txt") from exc

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

  def _get_bundled_model_xml(self) -> str | None:
    """Return path to bundled scene_classifier.xml if it has been built."""

    model_dir = Path(__file__).parent / "models"
    xml_path = model_dir / ("%s.xml" % _BUNDLED_MODEL_NAME)
    return str(xml_path) if xml_path.is_file() else None

  def _extract_frames(self, inputfile: str, ffmpeg_path: str, n_frames: int, target_width: int) -> list:
    """Extract evenly-spaced frames as RGB numpy arrays via FFmpeg raw pipe.

    Uses the ``thumbnail`` filter to pick representative frames, scales to
    ``target_width`` preserving aspect ratio (height rounded to even), then
    pipes raw RGB24 bytes. Height is inferred from total byte count.
    """

    np = self._import_numpy()

    cmd = [
      ffmpeg_path,
      "-i",
      inputfile,
      "-vf",
      "thumbnail=%d,scale=%d:-2" % (max(1, 300 // n_frames), target_width),
      "-vframes",
      str(n_frames),
      "-f",
      "rawvideo",
      "-pix_fmt",
      "rgb24",
      "-an",
      "-sn",
      "pipe:1",
    ]
    try:
      proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=target_width * target_width * 3,
      )
      raw, _ = proc.communicate()
    except Exception:
      return []

    if not raw:
      return []

    # Height is variable (depends on source aspect ratio); infer from total bytes.
    w = target_width
    total_pixels = len(raw) // 3
    pixels_per_frame = total_pixels // n_frames if n_frames > 0 else 0
    h = pixels_per_frame // w
    if h == 0:
      return []

    frame_bytes = w * h * 3
    frames = []
    for i in range(min(n_frames, len(raw) // frame_bytes)):
      chunk = raw[i * frame_bytes : (i + 1) * frame_bytes]
      arr = np.frombuffer(chunk, dtype=np.uint8).copy().reshape((h, w, 3))
      frames.append(arr)
    return frames

  def _heuristic_signals(self, frames: list, field_order: str) -> dict:
    """Compute content signals from sampled frames using pure numpy.

    Returns a dict matching the six ``AnalyzerObservations`` field names plus
    a ``content_type_hint`` key used as the default content type when no model
    is available.
    """

    np = self._import_numpy()

    # Motion: mean absolute grayscale difference between consecutive frame pairs.
    motion = 0.0
    if len(frames) >= 2:
      diffs = []
      for a, b in zip(frames[:-1], frames[1:]):
        ga = 0.299 * a[:, :, 0].astype(np.float32) + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
        gb = 0.299 * b[:, :, 0].astype(np.float32) + 0.587 * b[:, :, 1] + 0.114 * b[:, :, 2]
        diffs.append(float(np.mean(np.abs(ga - gb))))
      motion = min(1.0, float(np.mean(diffs)) / 50.0)

    # Noise: Laplacian variance via 3×3 kernel (first 4 frames for speed).
    noise_scores = []
    for frame in frames[:4]:
      gray = 0.299 * frame[:, :, 0].astype(np.float32) + 0.587 * frame[:, :, 1] + 0.114 * frame[:, :, 2]
      h_f, w_f = gray.shape
      pad = np.pad(gray, 1, mode="reflect")
      lap = -4.0 * pad[1 : h_f + 1, 1 : w_f + 1] + pad[0:h_f, 1 : w_f + 1] + pad[2 : h_f + 2, 1 : w_f + 1] + pad[1 : h_f + 1, 0:w_f] + pad[1 : h_f + 1, 2 : w_f + 2]
      noise_scores.append(float(np.var(lap)))
    noise = min(1.0, float(np.mean(noise_scores)) / 500.0) if noise_scores else 0.0

    # Interlace: FFprobe field_order is authoritative — no frame-level heuristic needed.
    interlace = 0.95 if field_order in ("tt", "bb") else 0.0

    # Crop: letterbox detection via row/column mean intensity threshold.
    crop_conf, crop_filter = 0.0, None
    if frames:
      mid = frames[len(frames) // 2]
      h_m, w_m = mid.shape[:2]
      gray_m = 0.299 * mid[:, :, 0].astype(np.float32) + 0.587 * mid[:, :, 1] + 0.114 * mid[:, :, 2]
      active_rows = np.where(gray_m.mean(axis=1) > 16)[0]
      active_cols = np.where(gray_m.mean(axis=0) > 16)[0]
      if len(active_rows) > 0 and len(active_cols) > 0:
        top = int(active_rows[0])
        bottom = int(active_rows[-1]) + 1
        left = int(active_cols[0])
        right = int(active_cols[-1]) + 1
        crop_h = (bottom - top) & ~1
        crop_w = (right - left) & ~1
        top = top & ~1
        left = left & ~1
        bar_frac = 1.0 - (crop_h * crop_w) / (h_m * w_m)
        if bar_frac >= 0.01 and crop_h > 0 and crop_w > 0:
          crop_conf = min(1.0, bar_frac * 3.0)
          crop_filter = "crop=%d:%d:%d:%d" % (crop_w, crop_h, left, top)

    content_hint = "sports_high_motion" if motion >= 0.8 else "general_live_action"

    return {
      "motion_score": motion,
      "noise_score": noise,
      "interlace_confidence": interlace,
      "crop_confidence": crop_conf,
      "crop_filter": crop_filter,
      "content_type_hint": content_hint,
    }

  def _load_compiled_model(self, core, model_xml_path: str, device: str):
    """Load an IR model with PrePostProcessor baking ImageNet normalisation into the graph.

    The PrePostProcessor is configured to accept raw uint8 BGR NHWC numpy arrays and
    internally handles BGR→RGB conversion, bilinear resize to the model's fixed input
    shape, float32 conversion, and ImageNet mean/scale normalisation.

    Returns the compiled model, or ``None`` if loading fails (caller falls back to
    heuristic content_type).
    """

    try:
      ov = self._import_openvino()
      preprocess_mod = importlib.import_module("openvino.preprocess")
      PrePostProcessor = preprocess_mod.PrePostProcessor
      ResizeAlgorithm = preprocess_mod.ResizeAlgorithm
      ColorFormat = preprocess_mod.ColorFormat

      model = core.read_model(model_xml_path)

      ppp = PrePostProcessor(model)
      inp = ppp.input(0)
      (inp.tensor().set_element_type(ov.Type.u8).set_layout(ov.Layout("NHWC")).set_color_format(ColorFormat.BGR))
      inp.model().set_layout(ov.Layout("NCHW"))
      (inp.preprocess().convert_color(ColorFormat.RGB).resize(ResizeAlgorithm.RESIZE_LINEAR).convert_element_type(ov.Type.f32).mean([123.675, 116.28, 103.53]).scale([58.395, 57.12, 57.375]))

      # CRITICAL: compile ppp.build() result, NOT the original model.
      preprocessed_model = ppp.build()
      return core.compile_model(preprocessed_model, device)
    except Exception:
      return None

  def _classify_content_type(self, mean_logits) -> str:
    """Map mean EfficientNet-B0 ImageNet logits to a content type string.

    EfficientNet-B0 is trained on ImageNet-1k (1000 object classes), not on
    video content types, so this mapping is a heuristic approximation. Replace
    the bundled model with a fine-tuned content-type classifier and update this
    method for production-quality classification.
    """

    np = self._import_numpy()

    # Numerically stable softmax.
    shifted = mean_logits - mean_logits.max()
    exp_vals = np.exp(shifted)
    probs = exp_vals / exp_vals.sum()

    sports_indices = [i for i in _SPORTS_CLASS_INDICES if i < len(probs)]
    sports_prob = float(probs[sports_indices].sum()) if sports_indices else 0.0
    top1_prob = float(probs.max())
    entropy_norm = float(-(probs * np.log(probs + 1e-10)).sum()) / np.log(len(probs))

    # Very high confidence on one class with low entropy → animation-like
    # (animated frames produce distinctive, unambiguous patterns for ImageNet classifiers).
    if top1_prob > 0.5 and entropy_norm < 0.3:
      return "animation"

    # Strong cumulative signal across sports equipment/scene classes.
    if sports_prob > 0.15:
      return "sports_high_motion"

    # Low top-1 confidence in a moderately narrow distribution → talking-head
    # (consistent, simple foreground with low visual complexity).
    if top1_prob < 0.05 and 0.5 < entropy_norm < 0.75:
      return "talking_head"

    return "general_live_action"

  def analyze(self, *_args, core=None, **kwargs) -> AnalyzerObservations:
    """Run frame-level analysis and return populated observations.

    Accepts ``inputfile`` (str) and ``info`` (MediaInfo) as keyword arguments.
    When ``inputfile`` is absent the method returns default observations after
    device validation — this preserves backward compatibility with tests that
    call ``analyze(core=core)`` to verify device availability only.
    """

    self.validate_device(core=core)

    inputfile = kwargs.get("inputfile") or None
    if inputfile is None:
      return AnalyzerObservations()

    info = kwargs.get("info") or None
    field_order = (info.video.field_order if (info and info.video) else None) or "progressive"

    frames = self._extract_frames(inputfile, "ffmpeg", self.config.max_frames, self.config.target_width)
    if not frames:
      return AnalyzerObservations()

    signals = self._heuristic_signals(frames, field_order)
    content_type = signals["content_type_hint"]

    # Locate IR model: explicit model_dir first, then bundled fallback.
    model_xml = None
    if self.config.model_dir:
      candidate = os.path.join(self.config.model_dir, "%s.xml" % _BUNDLED_MODEL_NAME)
      if os.path.isfile(candidate):
        model_xml = candidate

    if model_xml is None:
      model_xml = self._get_bundled_model_xml()

    if model_xml is not None:
      np = self._import_numpy()
      active_core = core or self.create_core()
      compiled = self._load_compiled_model(active_core, model_xml, self.device)
      if compiled is not None:
        try:
          req = compiled.create_infer_request()
          logits_list = []
          for frame in frames:
            # PrePostProcessor expects BGR NHWC uint8 input.
            bgr = frame[:, :, ::-1].copy()
            nhwc = bgr[np.newaxis, ...]
            req.infer({0: nhwc})
            logits_list.append(req.get_output_tensor(0).data.copy()[0])
          mean_logits = np.mean(logits_list, axis=0)
          content_type = self._classify_content_type(mean_logits)
        except Exception:
          pass  # fall back to heuristic content_type on inference failure

    return AnalyzerObservations(
      content_type=content_type,
      noise_score=signals["noise_score"],
      motion_score=signals["motion_score"],
      interlace_confidence=signals["interlace_confidence"],
      crop_confidence=signals["crop_confidence"],
      crop_filter=signals["crop_filter"],
    )
