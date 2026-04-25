# OpenVINO Analyzer Implementation — Sprint-Ready Task Breakdown

**Source PRP**: docs/prps/openvino-analyzer-implementation.md
**Feature**: Implement `OpenVINOAnalyzerBackend.analyze()` in `resources/openvino_analyzer.py`
**Overall complexity**: Moderate-complex (5 tasks, sequential with some parallelism)

---

## T-001: Add numpy to requirements-openvino.txt

**Task ID**: T-001
**Task Name**: Add numpy dependency and build-time extras to requirements-openvino.txt
**Priority**: High
**Effort**: S

### Context & Background

#### Source PRP Document

**Reference**: docs/prps/openvino-analyzer-implementation.md — Task 1

#### Feature Overview

The OpenVINO analyzer backend performs per-frame numpy array operations (frame differencing, Laplacian
variance, row/col mean scan). These operations require numpy, which is not yet listed in the optional
dependency file. This task pins numpy and documents the build-time extras needed for `prepare_models.py`
without making them hard runtime requirements.

#### Task Purpose

**As a** developer installing the OpenVINO optional dependency set
**I need** numpy pinned in `requirements-openvino.txt`
**So that** `pip install -r setup/requirements-openvino.txt` installs everything `analyze()` needs to run

#### Dependencies

- **Prerequisite Tasks**: None — can be done first, standalone
- **Parallel Tasks**: T-002 can be started simultaneously
- **Integration Points**: None — file-only change

### Technical Requirements

#### Functional Requirements

- **REQ-1**: When `requirements-openvino.txt` is installed, `import numpy` must succeed
- **REQ-2**: The existing `openvino>=2025.0,<2027` pin must remain unchanged
- **REQ-3**: Build-time extras (torch, torchvision) must be documented as comments so they are
  not installed by default but are easy to find

#### Technical Constraints

- **Technology Stack**: pip requirements file format
- **Code Standards**: Comment lines use `#`; no blank lines between entries; comment block precedes
  the extras it describes

### Implementation Details

#### Files to Modify/Create

```text
└── setup/requirements-openvino.txt  - Add numpy>=1.26; add commented build-time extras block
```

#### Key Implementation Steps

1. **Step 1**: Open `setup/requirements-openvino.txt` (currently one line: `openvino>=2025.0,<2027`)
   → Add `numpy>=1.26` on its own line below the openvino pin
2. **Step 2**: Add a blank comment block below numpy explaining build-time extras for
   `scripts/prepare_models.py` → developers know what to install before running model conversion

#### Final File Content

```text
openvino>=2025.0,<2027
numpy>=1.26

# Build-time extras for scripts/prepare_models.py (not required at runtime):
# torch>=2.2
# torchvision>=0.17
```

### Acceptance Criteria

```gherkin
Scenario 1: numpy is installed by requirements-openvino.txt
  Given a clean venv with no numpy installed
  When pip install -r setup/requirements-openvino.txt runs
  Then numpy>=1.26 is installed and importable

Scenario 2: openvino pin unchanged
  Given requirements-openvino.txt has been edited
  When the file is read
  Then openvino>=2025.0,<2027 is present verbatim

Scenario 3: build-time extras visible but not installed
  Given requirements-openvino.txt has been edited
  When pip install -r setup/requirements-openvino.txt runs
  Then torch is NOT installed (commented out)
  And the comment block above the commented lines explains their purpose
```

#### Rule-Based Criteria (Checklist)

- [ ] `numpy>=1.26` is a non-commented line in the file
- [ ] `openvino>=2025.0,<2027` is preserved exactly
- [ ] `torch` and `torchvision` appear only as comments, not as live requirements
- [ ] Comment block uses `#` prefix on every comment line (no bare comment-only section headers)

### Validation & Quality Gates

```bash
# Verify file contents
cat setup/requirements-openvino.txt

# Verify numpy is now resolvable
source venv/bin/activate && pip install -r setup/requirements-openvino.txt --dry-run
```

### Definition of Done

- [ ] File saved with correct content
- [ ] `git diff setup/requirements-openvino.txt` shows only additive changes

---

## T-002: Create resources/models/__init__.py

**Task ID**: T-002
**Task Name**: Create models package init to enable importlib.resources lookup
**Priority**: High
**Effort**: S

### Context & Background

#### Source PRP Document

**Reference**: docs/prps/openvino-analyzer-implementation.md — Task 2

#### Feature Overview

`_get_bundled_model_dir()` in T-004 uses `importlib.resources.files("resources.models")` to locate
the bundled IR model at runtime. Python's `importlib.resources` requires the directory to be a proper
package (i.e., contain `__init__.py`). Without this file the lookup raises `ModuleNotFoundError`.

#### Task Purpose

**As a** runtime model locator in `_get_bundled_model_dir()`
**I need** `resources/models/` to be a Python package
**So that** `importlib.resources.files("resources.models")` resolves to the correct directory

#### Dependencies

- **Prerequisite Tasks**: None — can be done first, standalone
- **Parallel Tasks**: T-001 can be done simultaneously

### Technical Requirements

#### Functional Requirements

- **REQ-1**: `resources/models/__init__.py` must exist (content: empty or a single docstring)
- **REQ-2**: The file must not import anything (it is only a package marker)

#### Technical Constraints

- **Technology Stack**: Python 3.12+
- **Code Standards**: Empty `__init__.py` is acceptable; a one-line docstring is preferred per project
  convention

### Implementation Details

#### Files to Modify/Create

```text
└── resources/models/__init__.py  - CREATE — empty package marker for importlib.resources
```

#### Key Implementation Steps

1. **Step 1**: Create the `resources/models/` directory if it does not exist →
   `mkdir -p resources/models`
2. **Step 2**: Write `resources/models/__init__.py` with a single docstring line →
   the directory becomes a Python package

#### File Content

```python
"""Bundled OpenVINO IR model artifacts for the analyzer backend."""
```

### Acceptance Criteria

```gherkin
Scenario 1: Package is importable
  Given resources/models/__init__.py exists
  When python -c "import resources.models" is run
  Then it completes without ImportError

Scenario 2: importlib.resources resolves the package
  Given resources/models/__init__.py exists
  When python -c "import importlib.resources; print(importlib.resources.files('resources.models'))" is run
  Then the output is a path pointing to resources/models/
```

#### Rule-Based Criteria (Checklist)

- [ ] File exists at `resources/models/__init__.py`
- [ ] File does not contain any imports
- [ ] `python -c "import resources.models"` exits 0

### Validation & Quality Gates

```bash
source venv/bin/activate && python -c "import resources.models; print('OK')"
source venv/bin/activate && python -c "import importlib.resources; p = importlib.resources.files('resources.models'); print(p)"
```

### Definition of Done

- [ ] File created and committed
- [ ] Import check command above exits 0

---

## T-003: Create scripts/prepare_models.py

**Task ID**: T-003
**Task Name**: Create model download and IR conversion script
**Priority**: Medium
**Effort**: M

### Context & Background

#### Source PRP Document

**Reference**: docs/prps/openvino-analyzer-implementation.md — Task 3

#### Feature Overview

`analyze()` can use a bundled EfficientNet-B0 OpenVINO IR model for content-type classification.
Those model files (`scene_classifier.xml` / `scene_classifier.bin`) must be generated once and
committed. This script automates the download + conversion. It is a developer/build-time tool —
not a runtime dependency — so `torch` and `torchvision` are only needed when running this script.

#### Task Purpose

**As a** developer preparing a release of SMA-NG
**I need** a script that downloads EfficientNet-B0 and converts it to OpenVINO IR
**So that** `resources/models/scene_classifier.xml` and `.bin` are produced and can be committed

#### Dependencies

- **Prerequisite Tasks**: T-002 (resources/models/ must exist as a package)
- **Parallel Tasks**: T-004 can begin implementation in parallel; T-003 produces artifacts used at
  runtime by T-004 but T-004's heuristic path runs without them
- **Integration Points**: `torchvision` (build-time only), `openvino.convert_model`, `openvino.save_model`

### Technical Requirements

#### Functional Requirements

- **REQ-1**: When run with `--output-dir resources/models`, the script produces
  `scene_classifier.xml` and `scene_classifier.bin` in that directory
- **REQ-2**: When `torch` is not installed, the script exits with a clear human-readable error
  message (not a traceback) directing the user to install the build extras
- **REQ-3**: The script accepts `--output-dir` as an optional CLI argument; it defaults to
  `resources/models/` relative to the project root

#### Non-Functional Requirements

- **Performance**: Must complete in under 5 minutes on a typical dev machine (downloads ~21 MB)
- **Compatibility**: Python 3.12+; `openvino>=2025.0`; `torch>=2.2`; `torchvision>=0.17`

#### Technical Constraints

- **Technology Stack**: Python `argparse`, `torch`, `torchvision`, `openvino`
- **Code Standards**: ruff-clean; no inline shell in Python; ShellCheck does not apply (pure Python)
- **Critical API note**: Use `import openvino as ov; ov.convert_model(); ov.save_model()` —
  NOT the removed `omz_downloader`; NOT `from openvino.runtime import ...`

### Implementation Details

#### Files to Modify/Create

```text
└── scripts/prepare_models.py  - CREATE — download + convert EfficientNet-B0 to IR
```

#### Key Implementation Steps

1. **Step 1**: Parse `--output-dir` arg → resolve to absolute path, create if needed
2. **Step 2**: Guard-import `torch` and `torchvision`; print clear error + `sys.exit(1)` if missing
3. **Step 3**: Load EfficientNet-B0 with `torchvision.models.efficientnet_b0(weights="DEFAULT")`
   in `eval()` mode → pretrained ImageNet weights; Apache 2.0 license
4. **Step 4**: Export to ONNX via `torch.onnx.export()` with example input `torch.zeros(1,3,224,224)`
   to a temp file
5. **Step 5**: Convert ONNX to IR via `ov.convert_model(onnx_path)` then
   `ov.save_model(ir_model, output_dir / "scene_classifier.xml")`; OpenVINO auto-writes `.bin`
6. **Step 6**: Print output paths and file sizes; assert both files exist

#### Code Patterns to Follow

- **Error handling for missing optional deps**: `resources/openvino_analyzer.py:114-118` —
  `_import_openvino()` lazy-import pattern; mirror the try/except ImportError → sys.exit(1) pattern
- **argparse CLI**: `daemon.py` main() for argument parser conventions used in this codebase

#### Known Gotchas

- `omz_downloader` is removed from openvino 2025.x pip — do NOT use it
- `ov.convert_model()` accepts a PyTorch model directly (no ONNX temp file needed in newer versions),
  but using an explicit ONNX intermediate is more portable across openvino 2025/2026
- Both `.xml` and `.bin` must end up in the same directory; `ov.save_model()` handles this automatically
  when the target path ends in `.xml`
- EfficientNet-B0 input shape is `[1, 3, 224, 224]` NCHW; the example_input tensor must match

### Acceptance Criteria

```gherkin
Scenario 1: Successful model conversion
  Given torch and torchvision and openvino are installed
  When python scripts/prepare_models.py --output-dir /tmp/test_models is run
  Then scene_classifier.xml exists in /tmp/test_models/
  And scene_classifier.bin exists in /tmp/test_models/
  And both files are non-zero bytes

Scenario 2: Missing torch dependency
  Given torch is NOT installed in the active venv
  When python scripts/prepare_models.py is run
  Then the script exits with code 1
  And stderr/stdout contains a message directing the user to install torch>=2.2

Scenario 3: --help flag
  Given the script is invoked with --help
  When python scripts/prepare_models.py --help runs
  Then it exits 0 and prints usage including --output-dir description
```

#### Rule-Based Criteria (Checklist)

- [ ] `ruff check scripts/prepare_models.py` passes with no errors
- [ ] `python scripts/prepare_models.py --help` exits 0
- [ ] Script produces `.xml` and `.bin` when prerequisites are installed
- [ ] Missing-torch case exits 1 with a readable message (no bare traceback)
- [ ] No `from openvino.runtime import` anywhere in the file
- [ ] `resources/models/*.bin` added to `.gitignore` (large binary; xml is small enough to commit)

### Validation & Quality Gates

```bash
source venv/bin/activate && ruff check scripts/prepare_models.py --fix
source venv/bin/activate && ruff format scripts/prepare_models.py
source venv/bin/activate && python scripts/prepare_models.py --help
```

### Definition of Done

- [ ] Script file exists at `scripts/prepare_models.py`
- [ ] ruff passes with no errors
- [ ] `--help` exits 0
- [ ] `.gitignore` updated to exclude `resources/models/*.bin`

---

## T-004: Implement analyze() and private helpers in openvino_analyzer.py

**Task ID**: T-004
**Task Name**: Implement OpenVINOAnalyzerBackend.analyze() and five private helper methods
**Priority**: Critical
**Effort**: L

### Context & Background

#### Source PRP Document

**Reference**: docs/prps/openvino-analyzer-implementation.md — Task 4 (A–F)

#### Feature Overview

`resources/openvino_analyzer.py` currently has a stub `analyze()` that only calls `validate_device()`
and returns empty `AnalyzerObservations()`. This task replaces the stub with a full implementation:
FFmpeg-piped frame extraction, pure-numpy heuristic signals, optional OpenVINO model inference, and
the orchestrating `analyze()` method that ties them together.

`mediaprocessor.py` already calls `analyze(inputfile=inputfile, info=info)` at line 883 and consumes
the returned `AnalyzerObservations` through `build_recommendations()`. No changes to those call sites
are needed — only the stub body changes.

#### Task Purpose

**As a** conversion job processed by mediaprocessor.py
**I need** `analyze()` to return real observations (motion, noise, interlace, crop, content_type)
**So that** `build_recommendations()` can produce meaningful FFmpeg tuning overrides

#### Dependencies

- **Prerequisite Tasks**: T-001 (numpy in requirements), T-002 (models package for importlib lookup)
- **Parallel Tasks**: T-003 (model artifacts) — T-004 heuristic path runs without model files;
  the model path in `analyze()` falls back gracefully when `_get_bundled_model_dir()` returns None
- **Integration Points**:
  - `resources/analyzer.py` — `AnalyzerObservations` dataclass (all 6 fields)
  - `converter/ffmpeg.py:866` — `FFMpeg.thumbnails()` as frame pipe pattern reference
  - `resources/mediaprocessor.py:883` — call site; `887-892` — exception catch pattern
  - `tests/test_openvino_analyzer.py` — `FakeCore` test double; existing test must still pass

### Technical Requirements

#### Functional Requirements

- **REQ-1**: When `analyze(core=core)` is called with no `inputfile`, it returns
  `AnalyzerObservations()` without error (preserves existing test at line 82)
- **REQ-2**: When `analyze(inputfile=path, info=info)` is called and FFmpeg extraction succeeds,
  it returns observations with all 6 fields populated from numpy heuristics
- **REQ-3**: When a valid model_dir with `scene_classifier.xml` is available, `content_type` is
  determined by `_classify_content_type()` over EfficientNet-B0 logits averaged across frames
- **REQ-4**: When `info.video.field_order` is `"tt"` or `"bb"`, `interlace_confidence` is `0.95`
- **REQ-5**: When `_load_compiled_model()` fails for any reason, it returns `None` and analysis
  continues with the heuristic `content_type_hint`
- **REQ-6**: `_extract_frames()` returns `[]` on subprocess error; `analyze()` returns
  `AnalyzerObservations()` when frames list is empty

#### Non-Functional Requirements

- **Performance**: Frame extraction limited to `config.max_frames` (default 12); heuristic noise
  scan limited to first 4 frames
- **Compatibility**: Must not import `openvino` at module level — lazy import via
  `self._import_openvino()` throughout

#### Technical Constraints

- **Technology Stack**: Python 3.12+, `subprocess`, `numpy>=1.26`, `openvino>=2025.0`
- **Architecture Patterns**: Lazy import pattern (`_import_openvino()`); no module-level numpy import
- **Code Standards**: ruff-clean; 2-space indent (matches existing file); `slots=True` dataclasses
- **Critical API notes** (must not deviate):
  1. `import openvino as ov` — NOT `from openvino.runtime import Core`
  2. `ppp.build()` returns a NEW model — compile that, not the original
  3. `core.set_property({"CACHE_DIR": path})` BEFORE first `compile_model()` call
  4. Use `create_infer_request()` per inference call — `compiled_model()` is NOT thread-safe
  5. `np.frombuffer(...).copy()` — frombuffer returns read-only view; `.copy()` required before reshape

### Implementation Details

#### Files to Modify/Create

```text
└── resources/openvino_analyzer.py  - MODIFY: add 5 private methods + replace analyze() stub body
```

#### Key Implementation Steps

1. **Step 1: Add `import subprocess` and lazy numpy helper** → `_import_numpy()` following the same
   pattern as `_import_openvino()`, or import numpy inside each method that uses it using a local
   `import numpy as np` (acceptable since numpy is in requirements, not optional at method level)
2. **Step 2: Implement `_get_bundled_model_dir()`** → use
   `importlib.resources.files("resources.models")` with `as_file()` context; return `str(path)` if
   `scene_classifier.xml` exists there, else `None`
3. **Step 3: Implement `_extract_frames()`** → build FFmpeg `rawvideo` subprocess command;
   call `proc.communicate()`; parse raw bytes into list of `(H, W, 3)` uint8 numpy arrays
4. **Step 4: Implement `_heuristic_signals()`** → pure numpy: motion (grayscale diff),
   noise (Laplacian variance), interlace (field_order string check), crop (row/col mean scan)
5. **Step 5: Implement `_load_compiled_model()`** → set cache_dir → read model → build PPP with
   ImageNet normalization baked in → compile `ppp.build()` result → return compiled or None
6. **Step 6: Implement `_classify_content_type()`** → softmax over 1000 ImageNet logits →
   apply class-range heuristic thresholds → return content_type string
7. **Step 7: Replace `analyze()` stub body** → orchestrate all helpers; respect the no-inputfile
   early-return; wire model path resolution and inference loop

#### Code Patterns to Follow

- **Lazy import guard**: `resources/openvino_analyzer.py:114-118` — `_import_openvino()` pattern
- **Subprocess frame pipe**: `converter/ffmpeg.py:866` — `FFMpeg.thumbnails()` for command
  structure and `DEVNULL` stderr convention
- **Exception catch in caller**: `resources/mediaprocessor.py:887-892` — `OpenVINOAnalyzerError`
  is caught there; `analyze()` may raise `OpenVINOAnalyzerError` subclasses for fatal errors but
  must NOT raise for recoverable model-load failures (return None from `_load_compiled_model()`)

#### Method Signatures

```python
def _get_bundled_model_dir(self) -> str | None: ...

def _extract_frames(
    self,
    inputfile: str,
    ffmpeg_path: str,
    n_frames: int,
    target_width: int,
) -> list: ...  # list[np.ndarray]

def _heuristic_signals(
    self,
    frames: list,  # list[np.ndarray]
    field_order: str,
) -> dict: ...  # keys match AnalyzerObservations fields + "content_type_hint"

def _load_compiled_model(
    self,
    core,
    model_xml_path: str,
    device: str,
    cache_dir: str | None,
): ...  # returns CompiledModel or None

@staticmethod
def _classify_content_type(mean_logits) -> str: ...  # -> one of the 4 content_type strings

def analyze(self, *args, core=None, **kwargs) -> AnalyzerObservations: ...
```

#### Heuristic Formulas (from PRP)

The PRP provides complete pseudocode for `_extract_frames()` and `_heuristic_signals()` in the
"Per-Task Pseudocode" section. Implement exactly as specified there — the formulas are load-bearing
(tests in T-005 will verify boundary values).

Key constants:
- Motion normalization divisor: `50.0`
- Noise (Laplacian variance) normalization divisor: `500.0`
- Interlace: `0.95` if `field_order in ("tt", "bb")` else `0.0`
- Crop threshold: row/col mean intensity `> 16`
- Crop filter string format: `"crop={cw}:{ch}:{left}:{top}"`
- ImageNet mean (RGB order): `[123.675, 116.28, 103.53]`
- ImageNet scale (RGB order): `[58.395, 57.12, 57.375]`
- Sports motion threshold for heuristic content_type_hint: `motion_score >= 0.8`

#### _load_compiled_model() PrePostProcessor Setup (exact sequence)

```python
# Inside _load_compiled_model():
ov = self._import_openvino()
from openvino.preprocess import PrePostProcessor, ResizeAlgorithm, ColorFormat

# 1. set cache before compile
if cache_dir:
    core.set_property({"CACHE_DIR": cache_dir})

# 2. read model
model = core.read_model(model_xml_path)

# 3. build PrePostProcessor
ppp = PrePostProcessor(model)
ppp.input(0).tensor() \
    .set_element_type(ov.Type.u8) \
    .set_layout(ov.Layout("NHWC")) \
    .set_color_format(ColorFormat.BGR)
ppp.input(0).model().set_layout(ov.Layout("NCHW"))
ppp.input(0).preprocess() \
    .convert_color(ColorFormat.RGB) \
    .resize(ResizeAlgorithm.RESIZE_LINEAR) \
    .convert_element_type(ov.Type.f32) \
    .mean([123.675, 116.28, 103.53]) \
    .scale([58.395, 57.12, 57.375])

# 4. CRITICAL: compile ppp.build(), NOT the original model
preprocessed_model = ppp.build()
compiled = core.compile_model(preprocessed_model, device)
return compiled
```

#### analyze() Inference Loop (exact sequence)

```python
# Inside analyze(), after getting compiled model:
req = compiled.create_infer_request()   # NOT compiled_model() — thread safety
logits = []
for frame in frames:
    bgr = frame[:, :, ::-1]            # RGB -> BGR for ColorFormat.BGR input
    nhwc = bgr[np.newaxis, ...]        # add batch dim: (1, H, W, 3)
    req.infer({0: nhwc})
    logits.append(req.get_output_tensor(0).data.copy()[0])  # shape [1000]
mean_logits = np.mean(logits, axis=0)
content_type = self._classify_content_type(mean_logits)
```

### Acceptance Criteria

```gherkin
Scenario 1: No inputfile — returns default observations (existing test preserved)
  Given an OpenVINOAnalyzerBackend with device="NPU"
  And a FakeCore with available_devices=["NPU"]
  When analyze(core=core) is called with no inputfile kwarg
  Then the return value is AnalyzerObservations()
  And observations.content_type == "general_live_action"
  And observations.noise_score == 0.0

Scenario 2: Heuristic path — model absent, FFmpeg extraction succeeds
  Given an OpenVINOAnalyzerBackend with model_dir=None
  And FFmpeg subprocess is mocked to return synthetic raw RGB24 bytes for 4 frames
  When analyze(inputfile="/fake/video.mkv", info=None, core=fake_core) is called
  Then it returns AnalyzerObservations with all 6 fields set (not all zeros)
  And no OpenVINO inference is attempted

Scenario 3: Interlaced input sets interlace_confidence
  Given info.video.field_order == "tt"
  And frames are available from _extract_frames()
  When _heuristic_signals(frames, "tt") is called
  Then result["interlace_confidence"] == 0.95

Scenario 4: Zero-motion frames produce motion_score == 0.0
  Given a list of identical frames (no pixel difference)
  When _heuristic_signals(frames, "progressive") is called
  Then result["motion_score"] == 0.0

Scenario 5: Model load failure falls back to heuristic content_type
  Given model_dir is set but core.read_model raises an exception
  When analyze(inputfile=path, info=info, core=fake_core) is called
  Then it returns AnalyzerObservations with content_type from heuristic
  And no exception is raised from analyze()

Scenario 6: model classification path executes
  Given a FakeCompiledModel returning known logits
  And model_dir points to a directory with a scene_classifier.xml marker file
  When analyze(inputfile=path, info=info, core=fake_core) runs the inference loop
  Then content_type is set to the return value of _classify_content_type(mean_logits)

Scenario 7: FFmpeg pipe failure returns empty observations
  Given subprocess.Popen raises an OSError (ffmpeg not found)
  When analyze(inputfile=path, info=info, core=fake_core) is called
  Then it returns AnalyzerObservations() (all defaults)
  And no exception propagates
```

#### Rule-Based Criteria (Checklist)

- [ ] `ruff check resources/openvino_analyzer.py` passes with no errors
- [ ] No `from openvino.runtime import` anywhere in the file
- [ ] No module-level `import numpy` (lazy import inside methods only)
- [ ] `_load_compiled_model()` compiles `ppp.build()` result, not the original model
- [ ] `cache_dir` property set before `compile_model()` call
- [ ] `create_infer_request()` used (not `compiled_model()`)
- [ ] `np.frombuffer(...).copy()` used before reshape in `_extract_frames()`
- [ ] `analyze(core=core)` with no inputfile returns `AnalyzerObservations()` (existing test passes)
- [ ] `_classify_content_type()` is a `@staticmethod`
- [ ] All four valid `content_type` strings used: `"animation"`, `"talking_head"`,
  `"sports_high_motion"`, `"general_live_action"`

### Validation & Quality Gates

```bash
# Lint
source venv/bin/activate && ruff check resources/openvino_analyzer.py --fix
source venv/bin/activate && ruff format resources/openvino_analyzer.py

# Existing tests still pass
source venv/bin/activate && python -m pytest tests/test_openvino_analyzer.py -v

# Smoke test: no-inputfile fallback
source venv/bin/activate && python -c "
from resources.openvino_analyzer import OpenVINOAnalyzerBackend, OpenVINOBackendConfig
from types import SimpleNamespace
class FakeCore:
    available_devices = ['CPU']
cfg = {'device': 'CPU', 'model_dir': None}
backend = OpenVINOAnalyzerBackend(cfg)
obs = backend.analyze(core=FakeCore())
assert obs.content_type == 'general_live_action', obs
print('no-inputfile fallback: OK')
"

# Verify deprecated import not present
source venv/bin/activate && grep -n "openvino.runtime" resources/openvino_analyzer.py && echo "FAIL: deprecated import found" || echo "OK: no deprecated import"
```

### Definition of Done

- [ ] `analyze()` stub body replaced with full implementation
- [ ] All 5 private methods implemented (`_get_bundled_model_dir`, `_extract_frames`,
  `_heuristic_signals`, `_load_compiled_model`, `_classify_content_type`)
- [ ] Existing test `test_analyze_returns_placeholder_observations_after_validation` still passes
- [ ] ruff passes with no errors
- [ ] No `openvino.runtime` import anywhere in the file

---

## T-005: Extend tests/test_openvino_analyzer.py

**Task ID**: T-005
**Task Name**: Add unit tests for analyze() helpers and inference branches
**Priority**: High
**Effort**: M

### Context & Background

#### Source PRP Document

**Reference**: docs/prps/openvino-analyzer-implementation.md — Task 5

#### Feature Overview

The existing test file covers `normalize_openvino_device`, `ensure_requested_devices_available`,
`validate_device`, `create_core`, and the stub `analyze()`. T-004 adds five new private methods
and replaces the stub. This task adds test coverage for all new behavior: frame extraction,
heuristic signal formulas, model inference path, and all `analyze()` branches.

#### Task Purpose

**As a** CI pipeline
**I need** comprehensive tests for every `analyze()` code path
**So that** regressions in heuristic formulas or inference wiring are caught automatically

#### Dependencies

- **Prerequisite Tasks**: T-004 — tests cover the new implementation
- **Parallel Tasks**: None; must test the actual implementation

### Technical Requirements

#### Functional Requirements

- **REQ-1**: All existing tests must continue to pass without modification
- **REQ-2**: New tests must cover: `_extract_frames()`, `_heuristic_signals()` boundary values,
  `analyze()` with no inputfile, `analyze()` with mocked FFmpeg, interlaced input, model path
- **REQ-3**: Subprocess calls in `_extract_frames()` tests must be mocked — no actual FFmpeg needed

#### Technical Constraints

- **Technology Stack**: `pytest`, `unittest.mock` or `monkeypatch`
- **Code Standards**: Mirror existing `FakeCore` pattern for `FakeCompiledModel` and
  `FakeInferRequest`; 2-space indent; class-based test grouping matching existing structure

### Implementation Details

#### Files to Modify/Create

```text
└── tests/test_openvino_analyzer.py  - MODIFY: add test classes for new methods and branches
```

#### Key Implementation Steps

1. **Step 1**: Add `FakeCompiledModel` and `FakeInferRequest` test doubles following the `FakeCore`
   pattern at lines 16-23 of the existing file
2. **Step 2**: Add `TestExtractFrames` class — mock `subprocess.Popen`; construct synthetic raw
   bytes for a known (W, H) and verify array shape and dtype
3. **Step 3**: Add `TestHeuristicSignals` class — use `numpy.zeros` / `numpy.ones` arrays for
   boundary cases; verify exact float values for zero-motion, known-noise, interlace flags
4. **Step 4**: Add `TestAnalyzeOrchestration` class — covers all `analyze()` branches:
   - No inputfile → default observations
   - Empty frames → default observations
   - Heuristic-only path (model_dir=None)
   - Interlaced field_order → interlace_confidence=0.95
   - Model path with FakeCompiledModel → content_type from `_classify_content_type()`
   - Model load failure (exception from read_model) → falls back to heuristic

#### Test Double Designs

```python
class FakeInferRequest:
    def __init__(self, output_data):
        self._output = output_data  # np.ndarray shape [1000]

    def infer(self, inputs):
        pass  # no-op

    def get_output_tensor(self, index):
        return SimpleNamespace(data=self._output)


class FakeCompiledModel:
    def __init__(self, output_logits):
        self._logits = output_logits

    def create_infer_request(self):
        return FakeInferRequest(self._logits)
```

#### Frame Byte Construction for _extract_frames() Tests

```python
import numpy as np

# Build synthetic raw RGB24 bytes for 2 frames of size W=64, H=36
W, H, N = 64, 36, 2
fake_frame = np.full((H, W, 3), 128, dtype=np.uint8)
raw_bytes = (fake_frame.tobytes() * N)

# In test: mock subprocess.Popen to return raw_bytes from communicate()
```

#### Heuristic Boundary Test Cases

| Test scenario | Input | Expected field | Expected value |
|---|---|---|---|
| Zero-motion (identical frames) | `[np.full((36,64,3),128,uint8)] * 4` | `motion_score` | `0.0` |
| Max-motion (alternating black/white) | `[zeros, ones*255, zeros, ones*255]` | `motion_score` | `1.0` (clamped) |
| Interlaced tt | `field_order="tt"` | `interlace_confidence` | `0.95` |
| Progressive | `field_order="progressive"` | `interlace_confidence` | `0.0` |
| Full black frame | uniform black array | `crop_confidence` | `0.0` |
| Letterboxed frame (black rows top+bottom) | array with black top/bottom rows | `crop_filter` | `"crop=..."` string |

### Acceptance Criteria

```gherkin
Scenario 1: _extract_frames returns correct shape
  Given subprocess.Popen is mocked to return 2 frames of 64x36 raw RGB24 bytes
  When _extract_frames("fake.mkv", "ffmpeg", 2, 64) is called
  Then it returns a list of 2 numpy arrays each with shape (36, 64, 3) and dtype uint8

Scenario 2: Zero-motion frames produce motion_score=0.0
  Given a list of 4 identical uint8 arrays (all pixels=128)
  When _heuristic_signals(frames, "progressive") is called
  Then result["motion_score"] == 0.0

Scenario 3: Interlaced field_order sets interlace_confidence=0.95
  Given any non-empty frames list
  When _heuristic_signals(frames, "tt") is called
  Then result["interlace_confidence"] == 0.95
  And _heuristic_signals(frames, "progressive")["interlace_confidence"] == 0.0

Scenario 4: Existing test still passes after T-004 implementation
  Given the full test file including new tests
  When pytest tests/test_openvino_analyzer.py -v runs
  Then test_analyze_returns_placeholder_observations_after_validation passes

Scenario 5: Model inference path executed with FakeCompiledModel
  Given a FakeCompiledModel returning logits that _classify_content_type maps to "animation"
  And model_dir is patched to return a valid directory
  When analyze(inputfile=path, info=info, core=fake_core) is called
  Then observations.content_type == "animation"

Scenario 6: _extract_frames returns [] on Popen OSError
  Given subprocess.Popen is patched to raise OSError
  When _extract_frames("fake.mkv", "ffmpeg", 4, 960) is called
  Then it returns []
```

#### Rule-Based Criteria (Checklist)

- [ ] All existing tests still pass (zero regressions)
- [ ] `_extract_frames()` tested with mocked subprocess — no real FFmpeg needed
- [ ] `_heuristic_signals()` boundary values verified numerically
- [ ] `interlace_confidence=0.95` for `"tt"` and `"bb"` covered explicitly
- [ ] `analyze()` no-inputfile branch covered
- [ ] `analyze()` model-load-failure fallback covered
- [ ] `FakeCompiledModel` and `FakeInferRequest` test doubles added
- [ ] No test imports `openvino` directly (tests must run without openvino installed)

### Validation & Quality Gates

```bash
# Run full analyzer test file
source venv/bin/activate && python -m pytest tests/test_openvino_analyzer.py -v

# Run related test files to check for regressions
source venv/bin/activate && python -m pytest tests/test_openvino_analyzer.py tests/test_analyzer.py -v

# Run full suite
source venv/bin/activate && python -m pytest -v

# Lint
source venv/bin/activate && ruff check tests/test_openvino_analyzer.py --fix
```

### Definition of Done

- [ ] All new test cases pass
- [ ] All existing test cases still pass
- [ ] Coverage includes all branches of `analyze()` (no-inputfile, no-frames, heuristic, model)
- [ ] No test requires real FFmpeg or real OpenVINO installation
- [ ] ruff passes with no errors on the test file

---

## Implementation Recommendations

### Suggested Task Sequencing

The critical path is T-001 → T-004 → T-005. Tasks T-002 and T-003 can proceed in parallel with
T-001, but T-004 depends on T-001 and T-002 being done first.

```text
T-001 ─┐
T-002 ─┼─> T-004 ─> T-005
T-003 ─┘ (optional for T-004 runtime; needed for T-005 model path tests)
```

**Recommended sprint order for a single developer**:

1. T-001 (5 min) — trivial, unblocks T-004 numpy use
2. T-002 (5 min) — trivial, unblocks importlib lookup in T-004
3. T-004 (3–4 hours) — core implementation; heuristic path can be verified without T-003 artifacts
4. T-005 (2 hours) — write tests; T-003 artifacts are not needed since the model path is mocked
5. T-003 (1 hour) — conversion script; can be deferred to a separate commit

### Team Structure (if parallel)

- **Developer A**: T-001 + T-002 + T-004 (core implementation)
- **Developer B**: T-003 (model conversion script; independent Python script work)
- **QA / Developer A**: T-005 (tests written after T-004 is complete)

### Parallelization Opportunities

- T-001 and T-002 are two-minute tasks; do them together in a single commit before starting T-004
- T-003 is fully independent of T-004/T-005 at the code level — the model path in `analyze()`
  degrades gracefully to heuristics when model files are absent
- T-005 can be partially written (heuristic tests) before T-004's model path is implemented

### Critical Path Analysis

**Tasks on critical path**: T-001 → T-004 → T-005

**Potential bottlenecks**:

1. **_extract_frames() height calculation** — raw RGB24 pipe produces variable-height frames
   depending on source aspect ratio. The height is inferred from `len(raw) // (n_frames * W * 3)`.
   If FFmpeg writes fewer bytes than expected (short read), the frame list will be shorter than
   `n_frames`. T-005 must test this short-read case explicitly.

2. **ppp.build() compilation** — the PrePostProcessor API changed between openvino 2025.0 and
   2026.0. The implementation must follow the exact API from the PRP documentation references.
   Test against the installed version in venv.

3. **_classify_content_type() thresholds** — the EfficientNet-B0 logit-to-content-type mapping
   is a heuristic approximation. The thresholds in the PRP are starting points and will require
   empirical calibration against real content. Document this clearly in the method docstring.

**Schedule optimization**: T-001 and T-002 are zero-risk file-creation tasks. Complete them before
anything else so T-004 is never blocked on them.

---

## Anti-Pattern Reference

The following are hard failures documented in the PRP — do not introduce them:

| Anti-pattern | Why it breaks | Correct pattern |
|---|---|---|
| `from openvino.runtime import Core` | Removed in 2026.0 | `import openvino as ov; ov.Core()` |
| `core.compile_model(model, device)` after ppp setup | Preprocessing silently absent | `core.compile_model(ppp.build(), device)` |
| `set_property` after `compile_model` | No effect | Set cache_dir before first compile |
| `compiled_model()` in shared context | Not thread-safe | `create_infer_request()` per call |
| `omz_downloader` | Removed from 2025.x | `ov.convert_model` + `ov.save_model` |
| Module-level `import numpy` | Breaks lazy-import contract | `import numpy as np` inside methods |
| `np.frombuffer(raw).reshape(...)` | Read-only view fails reshape | `.copy()` before reshape |
