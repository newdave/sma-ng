# PRP: OpenVINO Analyzer Implementation

## Discovery Summary

### Initial Task Analysis

User requested OpenVINO integration to use Intel GPU/NPU for tuning better quality transcodes
while staying storage-efficient. Preflight analysis found that the entire pipeline architecture
is already scaffolded — `analyzer.py`, `openvino_analyzer.py`, pipeline wiring in `mediaprocessor.py`,
and the full `[Analyzer]` config section in `readsettings.py`. The only gap is the body of
`OpenVINOAnalyzerBackend.analyze()`, which currently returns empty `AnalyzerObservations()`.

### User Clarifications Received

- **Question**: Model strategy — bundle models, user-supplied, or heuristics only?
- **Answer**: Bundle models (ship pre-converted OpenVINO IR models with SMA-NG)
- **Impact**: Requires a `scripts/prepare_models.py` build step and `resources/models/` directory;
  default `model-dir` points to bundled location

- **Question**: Frame extraction — FFmpeg pipe, OpenCV, or OpenVINO preprocessing pipeline?
- **Answer**: OpenVINO preprocessing pipeline (C)
- **Impact**: `PrePostProcessor` bakes resize+normalize into the compiled model graph; FFmpeg raw
  RGB24 subprocess pipe feeds uint8 numpy arrays directly; no OpenCV dependency

### Missing Requirements Identified

- No bundled model files existed; a `scripts/prepare_models.py` conversion script is required
- No-model fallback required for systems that haven't run model preparation

---

## Goal

Implement `OpenVINOAnalyzerBackend.analyze()` in `resources/openvino_analyzer.py` to replace the
stub with real inference that populates all six `AnalyzerObservations` fields, enabling downstream
`build_recommendations()` to produce FFmpeg tuning overrides (codec reorder, bitrate multipliers,
preset, filter injection) that improve quality-per-bit across conversion jobs.

---

## Why

- **Quality improvement**: Analyzer recommendations adjust bitrate multipliers, presets, and
  deinterlace/crop filters per content type — a sports clip gets higher bitrate headroom; animation
  gets a slower preset for better compression; letterboxed content gets the bars stripped
- **Storage efficiency**: Bitrate ratio multipliers and max-bitrate ceilings prevent over-allocation
  for simple content (talking head, animation) while preserving quality for complex scenes
- **Intel hardware utilisation**: GPU/NPU inference on Intel iGPU, Arc dGPU, or NPU runs the
  frame analysis without blocking CPU encoding pipelines
- **Already wired**: `mediaprocessor.py` already calls `analyze()` and feeds results into FFmpeg
  option construction — this PRP only fills the stub; no new integration points needed

---

## What

### User-visible behaviour

1. With `Analyzer.enabled: true` in `sma-ng.yml`, each conversion job runs a pre-encode frame
   analysis pass that emits a log line summarising observations and applied recommendations
2. Conversion options produced by `manual.py -oo` include analyzer recommendation annotations
3. On systems without Intel GPU/NPU the device falls back to CPU automatically
4. If `model-dir` is not set or model files are absent, signals fall back to numpy heuristics and
   `content_type` defaults to `"general_live_action"`
5. A `scripts/prepare_models.py` script converts and saves the bundled EfficientNet-B0 IR model

### Technical requirements

- `analyze()` accepts `inputfile` (str) and `info` (MediaInfo) as keyword args, plus `core`
- Returns fully-populated `AnalyzerObservations` with all six fields set to real values
- Frame extraction via FFmpeg raw RGB24 subprocess pipe (no OpenCV)
- OpenVINO `PrePostProcessor` handles resize and ImageNet normalisation inside the compiled model
- Heuristic fallback for all signals when model is unavailable
- All new code guarded behind the existing `_import_openvino()` lazy-import pattern
- Existing test `test_analyze_returns_placeholder_observations_after_validation` must still pass
  (i.e. `analyze(core=core)` with no `inputfile` returns valid defaults without error)

### Success Criteria

- [ ] `source venv/bin/activate && python -m pytest tests/test_openvino_analyzer.py -v` — all pass
- [ ] `source venv/bin/activate && python -m pytest tests/ -v` — full suite passes
- [ ] `source venv/bin/activate && ruff check resources/openvino_analyzer.py scripts/prepare_models.py` — no errors
- [ ] `source venv/bin/activate && python manual.py -i /path/to/file.mkv -oo` with `Analyzer.enabled: true` logs analyzer observations
- [ ] On Intel GPU system: `core.available_devices` includes `"GPU"` and jobs run on GPU
- [ ] On non-Intel system: analyzer completes on CPU with `AUTO:GPU,NPU,CPU` fallback

---

## All Needed Context

### Research Phase Summary

- **Codebase patterns found**: Full pipeline scaffolding, `OpenVINOBackendConfig` dataclass,
  `_import_openvino()` lazy-import guard, `FFMpeg.thumbnails()` frame pipe pattern, `FakeCore`
  test double, `requirements-openvino.txt` optional-dep file
- **External research needed**: Yes — OpenVINO 2025.x API breaking changes, PrePostProcessor setup,
  model bundling, frame extraction patterns, heuristic formulas
- **Knowledge gaps filled**: `openvino.runtime` is deprecated/gone in 2026.0; `omz_downloader`
  removed from 2025.x pip; `ppp.build()` returns a NEW model that must be compiled; NPU excluded
  from AUTO by default

### Documentation & References

```yaml
- url: https://docs.openvino.ai/2025/api/ie_python_api/_autosummary/openvino.Core.html
  why: read_model, compile_model, set_property, available_devices exact signatures
  critical: Use `import openvino as ov; core = ov.Core()` — NOT `openvino.runtime.Core`

- url: https://docs.openvino.ai/2025/openvino-workflow/running-inference/optimize-inference/optimize-preprocessing.html
  why: PrePostProcessor full pipeline setup — tensor layout, color format, resize, mean/scale
  critical: ppp.build() returns a NEW ov.Model; you MUST compile that returned object, not the original

- url: https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-request.html
  why: create_infer_request(), infer(), get_output_tensor() — correct per-job inference pattern
  critical: compiled_model() reuses one internal InferRequest and is NOT thread-safe

- url: https://docs.openvino.ai/2025/openvino-workflow/running-inference/optimize-inference/optimizing-latency/model-caching-overview.html
  why: cache_dir must be set via set_property BEFORE first compile_model() call
  critical: set_property({props.cache_dir: path}) on Core, not on CompiledModel

- url: https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-devices-and-modes/auto-device-selection.html
  why: AUTO:GPU,NPU,CPU string syntax for graceful hardware fallback
  critical: NPU is NOT included in AUTO by default; must be listed explicitly

- url: https://docs.openvino.ai/2025/openvino-workflow/model-preparation.html
  why: ovc CLI and ov.convert_model + ov.save_model for IR generation in prepare_models.py

- url: https://github.com/openvinotoolkit/open_model_zoo/blob/master/models/public/efficientnet-b0-pytorch/README.md
  why: Input shape [1,3,224,224] NCHW, ImageNet mean/scale constants in RGB order
  critical: Mean=[123.675,116.28,103.53] Scale=[58.395,57.12,57.375] in RGB order after BGR->RGB

- url: https://docs.python.org/3.12/library/importlib.resources.html
  why: files(), joinpath(), as_file() pattern for locating bundled model at runtime

- file: resources/openvino_analyzer.py
  why: Stub to implement — OpenVINOBackendConfig, _import_openvino() pattern, validate_device(),
       existing class structure, OpenVINOAnalyzerError hierarchy, FakeCore test double usage

- file: resources/analyzer.py
  why: AnalyzerObservations fields (all 6), valid content_type strings, build_recommendations() policy

- file: resources/mediaprocessor.py
  why: How analyze() is called (line 883), how recommendations flow into FFmpeg opts (lines 1237-1398)

- file: converter/ffmpeg.py
  why: FFMpeg.thumbnails() at line 866 for frame pipe pattern; FFMpeg._spawn() subprocess conventions

- file: tests/test_openvino_analyzer.py
  why: FakeCore structure, monkeypatching pattern, existing tests that must still pass

- file: setup/requirements-openvino.txt
  why: Pattern for optional dependency file; already contains openvino>=2025.0,<2027
```

### Current Codebase Tree (relevant files)

```bash
resources/
  analyzer.py               # AnalyzerObservations, AnalyzerRecommendations, build_recommendations
  openvino_analyzer.py      # OpenVINOBackendConfig, OpenVINOAnalyzerBackend (stub analyze())
  mediaprocessor.py         # calls analyze() at line 883; consumes recs at lines 1237-1398
  readsettings.py           # [Analyzer] DEFAULTS at lines 105-118; _read_analyzer() at 773-789
converter/
  ffmpeg.py                 # FFMpeg.thumbnails() at line 866; _spawn() pattern
setup/
  requirements-openvino.txt # openvino>=2025.0,<2027
  sma-ng.yml.sample         # Analyzer section documented
tests/
  test_openvino_analyzer.py # 89 lines; FakeCore; covers validate_device; placeholder analyze()
  test_analyzer.py          # 75 lines; covers all build_recommendations() branches
  test_mediaprocessor.py    # patches OpenVINOAnalyzerBackend; asserts analyze() call signature
```

### Desired Codebase Tree

```bash
resources/
  openvino_analyzer.py      # MODIFIED — implement analyze(), _extract_frames(),
                            #            _heuristic_signals(), _classify_content_type(),
                            #            _load_compiled_model()
  models/
    __init__.py             # CREATE — makes models/ a package for importlib.resources
    scene_classifier.xml    # BUILT ARTIFACT (via prepare_models.py) — IR model descriptor
    scene_classifier.bin    # BUILT ARTIFACT — IR model weights
scripts/
  prepare_models.py         # CREATE — downloads EfficientNet-B0, converts to IR, saves to
                            #          resources/models/
setup/
  requirements-openvino.txt # MODIFY — add numpy; add torch + torchvision as [prepare] extras
tests/
  test_openvino_analyzer.py # MODIFY — extend with inference behaviour tests
```

### Known Gotchas

```python
# CRITICAL: openvino.runtime is DEPRECATED in 2025.0, REMOVED in 2026.0
# WRONG:  from openvino.runtime import Core
# CORRECT: import openvino as ov; core = ov.Core()

# CRITICAL: ppp.build() returns a NEW ov.Model — compile THAT, not the original
model = core.read_model(xml_path)
ppp = PrePostProcessor(model)
# ... configure ppp ...
preprocessed_model = ppp.build()            # returns NEW model
compiled = core.compile_model(preprocessed_model, device)   # compile NEW model
# NOT: compiled = core.compile_model(model, device) — preprocessing silently absent

# CRITICAL: cache_dir must be set BEFORE first compile_model() call
core.set_property({props.cache_dir: cache_path})   # do this first
compiled = core.compile_model(...)                  # then compile

# CRITICAL: NPU not in AUTO by default — use explicit list to include it
# AUTO        = tries GPU, CPU (NPU excluded)
# AUTO:GPU,NPU,CPU = tries all three in order
device_str = "AUTO:GPU,NPU,CPU"   # or "AUTO" if NPU opt-in not desired

# CRITICAL: both .xml and .bin must be in the same directory
# core.read_model(xml) auto-loads xml.replace(".xml", ".bin") from same dir

# CRITICAL: existing test calls analyze(core=core) with NO inputfile/info
# The implementation must handle missing kwargs gracefully (return AnalyzerObservations())

# CRITICAL: _import_openvino() lazy pattern — never import openvino at module level
# WRONG:  import openvino as ov  (at top of file)
# CORRECT: ov = self._import_openvino()  (inside method, before use)

# CRITICAL: numpy frombuffer returns read-only view — call .copy() before reshape
arr = np.frombuffer(raw_chunk, dtype=np.uint8).copy().reshape((H, W, 3))

# CRITICAL: FFmpeg raw RGB24 pipe bufsize must be >= one full frame
# bufsize = width * height * 3   (bytes per frame)

# CRITICAL: omz_downloader is GONE from openvino 2025.x pip
# Use: ov.convert_model(pytorch_model, example_input=...) + ov.save_model()
```

---

## Implementation Blueprint

### Data Models

No new data models needed — `AnalyzerObservations` and `OpenVINOBackendConfig` already defined.

```python
# resources/analyzer.py — EXISTING, do not modify
@dataclass(slots=True)
class AnalyzerObservations:
    content_type: str = "general_live_action"    # "animation"|"talking_head"|"sports_high_motion"|"general_live_action"
    noise_score: float = 0.0                     # 0.0-1.0
    motion_score: float = 0.0                    # 0.0-1.0
    interlace_confidence: float = 0.0            # 0.0-1.0
    crop_confidence: float = 0.0                 # 0.0-1.0
    crop_filter: str | None = None               # "crop=W:H:X:Y" or None

# resources/openvino_analyzer.py — EXISTING OpenVINOBackendConfig, do not modify
@dataclass(slots=True)
class OpenVINOBackendConfig:
    device: str = "AUTO"
    model_dir: str | None = None    # None when blank in config (via cfg_getpath)
    cache_dir: str | None = None    # None when blank in config
    max_frames: int = 12
    target_width: int = 960
```

### Tasks

```yaml
Task 1: Update requirements-openvino.txt
MODIFY setup/requirements-openvino.txt:
  - ADD: numpy>=1.26  (needed for frame array ops in analyze())
  - KEEP: openvino>=2025.0,<2027 unchanged
  - ADD comment block: "# Build-time extras for prepare_models.py (not required at runtime):"
  - ADD commented lines: # torch>=2.2; torchvision>=0.17  (user installs manually for model prep)

Task 2: Create resources/models/__init__.py
CREATE resources/models/__init__.py:
  - Empty file — makes models/ a package so importlib.resources.files() works
  - Required for as_file() to locate bundled .xml/.bin at runtime

Task 3: Create scripts/prepare_models.py
CREATE scripts/prepare_models.py:
  - Downloads EfficientNet-B0 from torchvision (pretrained=True, Apache 2.0)
  - Exports to ONNX via torch.onnx.export() with example_input shape [1,3,224,224]
  - Converts ONNX → IR via openvino.convert_model() + openvino.save_model()
  - Saves to resources/models/scene_classifier.xml + .bin
  - Emits progress messages and confirms output paths
  - CLI: python scripts/prepare_models.py [--output-dir resources/models]
  - Add to .gitignore: resources/models/*.bin (large binary artifact)

Task 4: Implement analyze() and private helpers in resources/openvino_analyzer.py
MODIFY resources/openvino_analyzer.py — implement analyze() and four private methods:

  A. _get_bundled_model_dir() -> str | None
     - Use importlib.resources.files("resources.models") to get package path
     - Return str path if scene_classifier.xml exists there, else None

  B. _extract_frames(inputfile, ffmpeg_path, n_frames, target_width) -> list[np.ndarray]
     - FFmpeg raw RGB24 subprocess pipe → numpy arrays
     - Returns list of (H, W, 3) uint8 RGB arrays, length <= n_frames
     - Handles proc.communicate() with bufsize = target_width * scaled_height * 3
     - Returns [] on subprocess error (caller handles gracefully)

  C. _heuristic_signals(frames, field_order) -> dict
     - motion_score: mean absolute diff of grayscale consecutive frame pairs / 50.0, clamped 0-1
     - noise_score: mean Laplacian variance across frames / 500.0, clamped 0-1
     - interlace_confidence: 0.95 if field_order in ("tt","bb") else 0.0 (FFprobe is authoritative)
     - crop_confidence + crop_filter: row/col mean intensity scan on median frame, threshold=16
     - content_type_hint: "sports_high_motion" if motion_score>=0.8, else "general_live_action"
     - Returns dict with all six AnalyzerObservations field names as keys

  D. _load_compiled_model(core, model_xml_path, device, cache_dir) -> CompiledModel | None
     - set_property({props.cache_dir: cache_dir}) if cache_dir is set (BEFORE compile)
     - core.read_model(model_xml_path)
     - Build PrePostProcessor:
         ppp.input(0).tensor().set_element_type(u8).set_layout(NHWC).set_color_format(BGR)
         ppp.input(0).model().set_layout(NCHW)
         ppp.input(0).preprocess().convert_color(RGB).resize(RESIZE_LINEAR)
                                   .convert_element_type(f32)
                                   .mean([123.675,116.28,103.53])
                                   .scale([58.395,57.12,57.375])
     - core.compile_model(ppp.build(), device)  ← compile ppp.build() NOT original model
     - Return compiled_model, or None on any exception (log warning)

  E. analyze(self, *args, core=None, **kwargs) -> AnalyzerObservations  ← REPLACE STUB
     - self.validate_device(core=core)    ← keep existing call
     - inputfile = kwargs.get("inputfile") or None
     - info = kwargs.get("info") or None
     - If inputfile is None: return AnalyzerObservations()   ← preserves existing test
     - Determine ffmpeg_path from self.config (no stored ffmpeg_path in config — use "ffmpeg")
     - frames = self._extract_frames(inputfile, "ffmpeg", self.config.max_frames, self.config.target_width)
     - If not frames: return AnalyzerObservations()
     - field_order = info.video.field_order if (info and info.video) else "progressive"
     - signals = self._heuristic_signals(frames, field_order)
     - content_type = signals["content_type_hint"]   (heuristic default)
     - If model_dir available (self.config.model_dir or self._get_bundled_model_dir()):
         ov = self._import_openvino()
         compiled = self._load_compiled_model(core or create_core(), xml_path, device, cache_dir)
         If compiled:
           req = compiled.create_infer_request()
           logits = []
           for frame in frames:
             bgr = frame[:,:,::-1]   # RGB→BGR for PrePostProcessor ColorFormat.BGR input
             nhwc = bgr[np.newaxis,...]
             req.infer({0: nhwc})
             logits.append(req.get_output_tensor(0).data.copy()[0])  # shape [num_classes]
           mean_logits = np.mean(logits, axis=0)
           content_type = self._classify_content_type(mean_logits)
     - Return AnalyzerObservations(
           content_type=content_type,
           noise_score=signals["noise_score"],
           motion_score=signals["motion_score"],
           interlace_confidence=signals["interlace_confidence"],
           crop_confidence=signals["crop_confidence"],
           crop_filter=signals["crop_filter"],
       )

  F. _classify_content_type(mean_logits) -> str   (static method)
     - EfficientNet-B0 outputs 1000 ImageNet class logits
     - Apply softmax to get probabilities
     - Animation proxy: sum prob of classes 0-11 (cartoon/comic/art classes in ImageNet-1k)
       and classes with high color uniformity proxy — or use logit distribution entropy:
       low entropy (confident single class) + high top-1 prob → animation
     - Sports proxy: sum probs of known sports classes (e.g., 805-850 range in ImageNet-1k)
     - Talking head: softmax entropy in mid range + no sport class dominant
     - Thresholds (tune empirically): animation if top-1 > 0.6 and mean_logit_std < 2.0
     - Default: "general_live_action"
     - Note: This is a heuristic mapping over ImageNet — document that a fine-tuned model
       improves accuracy; the bundled model provides a starting point

Task 5: Extend tests/test_openvino_analyzer.py
MODIFY tests/test_openvino_analyzer.py:
  - Add FakeCompiledModel and FakeInferRequest test doubles
  - Test: _extract_frames() with mocked subprocess returns correct numpy shape
  - Test: _heuristic_signals() with synthetic frames (known solid-color arrays for crop,
    zero-motion frames for motion=0, noisy frames for noise>0)
  - Test: analyze() with inputfile=None returns default AnalyzerObservations (existing test preserved)
  - Test: analyze() with inputfile and no model_dir returns observations from heuristics
  - Test: analyze() with inputfile and model_dir returns observations with model classification
  - Test: analyze() on interlaced input (field_order="tt") sets interlace_confidence=0.95
  - MIRROR: FakeCore pattern already in file; extend same pattern for FakeCompiledModel
```

### Per-Task Pseudocode

```python
# Task 4B: _extract_frames — FFmpeg raw RGB24 pipe
def _extract_frames(self, inputfile, ffmpeg_path, n_frames, target_width):
    # Probe video dimensions first (use info.video.width/height if available, else ffprobe)
    # FFmpeg cmd: evenly-spaced frames using thumbnail filter
    cmd = [
        ffmpeg_path, "-i", inputfile,
        "-vf", f"thumbnail={max(1, 300 // n_frames)},scale={target_width}:-2",
        "-vframes", str(n_frames),
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-an", "-sn", "pipe:1",
    ]
    # scale={target_width}:-2 preserves aspect ratio, rounds height to even number
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            bufsize=target_width * target_width * 3)  # conservative upper bound
    raw, _ = proc.communicate()
    # Width is fixed; height depends on source AR — calculate from total bytes
    # Determine actual (W, H) from raw length and n_frames
    # frame_bytes = len(raw) // n_frames   (approximate — use // to handle short reads)
    # H = frame_bytes // (target_width * 3)
    frames = []
    if not raw:
        return frames
    frame_size_w = target_width
    approx_h = len(raw) // (n_frames * frame_size_w * 3) if n_frames > 0 else 0
    if approx_h == 0:
        return frames
    frame_bytes = frame_size_w * approx_h * 3
    for i in range(min(n_frames, len(raw) // frame_bytes)):
        chunk = raw[i * frame_bytes:(i + 1) * frame_bytes]
        arr = np.frombuffer(chunk, dtype=np.uint8).copy().reshape((approx_h, frame_size_w, 3))
        frames.append(arr)
    return frames

# Task 4C: _heuristic_signals — pure numpy, no OpenCV
def _heuristic_signals(self, frames, field_order):
    # Motion: mean absolute grayscale diff between consecutive frames
    motion = 0.0
    if len(frames) >= 2:
        diffs = []
        for a, b in zip(frames[:-1], frames[1:]):
            ga = 0.299*a[:,:,0].astype(np.float32) + 0.587*a[:,:,1] + 0.114*a[:,:,2]
            gb = 0.299*b[:,:,0].astype(np.float32) + 0.587*b[:,:,1] + 0.114*b[:,:,2]
            diffs.append(float(np.mean(np.abs(ga - gb))))
        motion = min(1.0, float(np.mean(diffs)) / 50.0)

    # Noise: Laplacian variance via manual 3x3 kernel — averaged across frames
    noise_scores = []
    for frame in frames[:4]:   # limit to first 4 for speed
        gray = 0.299*frame[:,:,0].astype(np.float32) + 0.587*frame[:,:,1] + 0.114*frame[:,:,2]
        H, W = gray.shape
        pad = np.pad(gray, 1, mode="reflect")
        lap = (-4*pad[1:H+1,1:W+1] + pad[0:H,1:W+1] + pad[2:H+2,1:W+1]
               + pad[1:H+1,0:W] + pad[1:H+1,2:W+2])
        noise_scores.append(float(np.var(lap)))
    noise = min(1.0, float(np.mean(noise_scores)) / 500.0) if noise_scores else 0.0

    # Interlace: trust FFprobe field_order — it's authoritative
    interlace = 0.95 if field_order in ("tt", "bb") else 0.0

    # Crop: scan middle frame for letterbox bars
    crop_conf, crop_filter = 0.0, None
    if frames:
        frame = frames[len(frames) // 2]
        H, W = frame.shape[:2]
        gray = 0.299*frame[:,:,0].astype(np.float32) + 0.587*frame[:,:,1] + 0.114*frame[:,:,2]
        row_m, col_m = gray.mean(axis=1), gray.mean(axis=0)
        active_rows = np.where(row_m > 16)[0]
        active_cols = np.where(col_m > 16)[0]
        if len(active_rows) > 0 and len(active_cols) > 0:
            top, bottom = int(active_rows[0]), int(active_rows[-1]) + 1
            left, right = int(active_cols[0]), int(active_cols[-1]) + 1
            ch, cw = (bottom - top) & ~1, (right - left) & ~1
            top, left = top & ~1, left & ~1
            bar_frac = 1.0 - (ch * cw) / (H * W)
            if bar_frac >= 0.01:
                crop_conf = min(1.0, bar_frac * 3.0)
                crop_filter = f"crop={cw}:{ch}:{left}:{top}"

    content_hint = "sports_high_motion" if motion >= 0.8 else "general_live_action"

    return {
        "motion_score": motion,
        "noise_score": noise,
        "interlace_confidence": interlace,
        "crop_confidence": crop_conf,
        "crop_filter": crop_filter,
        "content_type_hint": content_hint,
    }
```

### Integration Points

```yaml
EXISTING (no changes needed):
  mediaprocessor.py:
    - Line 883: `observations = OpenVINOAnalyzerBackend(analyzer_config).analyze(inputfile=inputfile, info=info)`
    - Lines 1237-1398: recommendations flow into FFmpeg opts (codec_order, bitrate_ratio_multiplier, etc.)
    - Lines 887-892: OpenVINOAnalyzerError catch → fallback to empty AnalyzerRecommendations

  readsettings.py:
    - Lines 773-789: _read_analyzer() → self.analyzer dict
    - analyzer_config dict keys: enabled, backend, device, model_dir, cache_dir, max_frames,
      target_width, allow_* toggles — all already wired to OpenVINOBackendConfig in __init__

CONFIG (no schema changes needed):
  sma-ng.yml:
    Analyzer:
      enabled: true
      backend: openvino
      device: "AUTO:GPU,NPU,CPU"          # or "AUTO" to exclude NPU
      model-dir: ""                        # blank = use bundled resources/models/
      cache-dir: "/tmp/ov_cache"           # recommended — eliminates GPU kernel compile on restart
      max-frames: 12
      target-width: 960

OPTIONAL DEPS:
  setup/requirements-openvino.txt:
    - openvino>=2025.0,<2027   (already present)
    - numpy>=1.26              (ADD — required for array ops in analyze())
```

---

## Validation Loop

### Level 1: Syntax & Style

```bash
# After each file change:
source venv/bin/activate && ruff check resources/openvino_analyzer.py --fix
source venv/bin/activate && ruff format resources/openvino_analyzer.py
source venv/bin/activate && ruff check scripts/prepare_models.py --fix
source venv/bin/activate && ruff format scripts/prepare_models.py
```

### Level 2: Unit Tests

```bash
# Existing tests must still pass after every change:
source venv/bin/activate && python -m pytest tests/test_openvino_analyzer.py -v

# After Task 5 (new tests):
source venv/bin/activate && python -m pytest tests/test_openvino_analyzer.py tests/test_analyzer.py -v

# Full suite:
source venv/bin/activate && python -m pytest -v
```

### Level 3: Integration Smoke Test

```bash
# Verify OpenVINO import works:
source venv/bin/activate && python -c "import openvino as ov; c = ov.Core(); print(c.available_devices)"

# Verify PrePostProcessor import:
source venv/bin/activate && python -c "from openvino.preprocess import PrePostProcessor, ResizeAlgorithm, ColorFormat; print('OK')"

# Verify prepare_models.py dry-run (without torch installed shows friendly error):
source venv/bin/activate && python scripts/prepare_models.py --help

# Smoke test analyze() with heuristic fallback (no model needed):
source venv/bin/activate && python -c "
from resources.openvino_analyzer import OpenVINOAnalyzerBackend, OpenVINOBackendConfig
cfg = OpenVINOBackendConfig(device='CPU', model_dir=None)
backend = OpenVINOAnalyzerBackend(cfg)
# analyze with no inputfile — must return AnalyzerObservations() without error
obs = backend.analyze()
assert obs.content_type == 'general_live_action'
print('no-inputfile fallback: OK')
"

# Smoke test with a real media file (requires Analyzer.enabled: true in sma-ng.yml):
source venv/bin/activate && python manual.py -i /path/to/test.mkv -oo
# Expected: log lines containing 'Analyzer observations' with populated fields
```

---

## Final Validation Checklist

- [ ] All tests pass: `source venv/bin/activate && python -m pytest -v`
- [ ] No linting errors: `source venv/bin/activate && ruff check resources/openvino_analyzer.py scripts/prepare_models.py`
- [ ] `analyze(core=core)` with no inputfile returns `AnalyzerObservations()` (existing test preserved)
- [ ] `analyze(inputfile=path, info=info)` with no model returns heuristic observations (all 6 fields)
- [ ] `analyze()` with valid model_dir returns model-based content_type classification
- [ ] `interlace_confidence=0.95` when `info.video.field_order in ("tt","bb")`
- [ ] `crop_filter` is a valid FFmpeg crop string or None
- [ ] `OpenVINODependencyError` propagates when openvino not installed (caller catches it)
- [ ] `_load_compiled_model()` returns None on model load failure (no crash)
- [ ] `resources/models/__init__.py` created
- [ ] `setup/requirements-openvino.txt` includes numpy
- [ ] `scripts/prepare_models.py` exits with clear error when torch not installed
- [ ] No `from openvino.runtime import` anywhere in codebase (deprecated API)

---

## Anti-Patterns to Avoid

- ❌ `from openvino.runtime import Core` — deprecated 2025.0, removed 2026.0; use `import openvino as ov`
- ❌ `core.compile_model(model, device)` after `ppp.build()` on the original `model` — preprocessing silently absent; compile `ppp.build()` return value
- ❌ Setting `cache_dir` after the first `compile_model()` call — has no effect; set it first
- ❌ `compiled_model()` in any thread-shared context — not thread-safe; use `create_infer_request()` per call
- ❌ Using `omz_downloader` — removed from openvino 2025.x pip; use `ov.convert_model` + `ov.save_model`
- ❌ Importing numpy or openvino at module level — must use lazy import pattern matching `_import_openvino()`
- ❌ Calling `build_recommendations()` inside `analyze()` — that's done by the caller in mediaprocessor.py; analyze() returns only AnalyzerObservations
- ❌ Raising inside analyze() for recoverable errors — let `OpenVINOAnalyzerError` subclasses propagate; catch only at the heuristic fallback level within the method
- ❌ Hard-coding `"GPU"` as device — use `"AUTO:GPU,NPU,CPU"` for graceful degradation to CPU
- ❌ Forgetting `.copy()` on `np.frombuffer()` result — returns read-only view; reshape will fail

---

## Task Breakdown Reference

See `docs/tasks/openvino-analyzer-implementation.md` for detailed sprint-ready task decomposition.

---

## Confidence Score: 7/10

**Rationale**: The scaffolding, contract, and pipeline integration are all in place — this is purely
implementing the stub body. The heuristic signals are well-defined with clear formulas. The main
uncertainty is `_classify_content_type()`: EfficientNet-B0 is an ImageNet classifier, not a
video content type classifier, so the class-to-content-type mapping is an approximation that will
need empirical calibration. A fine-tuned model would raise confidence to 9/10. The frame extraction
via raw RGB24 pipe and variable-height handling adds moderate implementation risk. All other pieces
have clear patterns to follow from the existing codebase and research.
