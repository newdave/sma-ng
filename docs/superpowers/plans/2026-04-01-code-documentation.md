# SMA-NG Code Documentation Plan

> **For agentic workers:** Work through tasks sequentially. Each task ends with a test run and commit. Update checkboxes as you progress. After completing each commit, update this plan file to reflect completed steps.

**Goal:** Add comprehensive inline documentation (docstrings) to all Python files in the codebase that currently lack it. Every public module, class, and function should have a plain-style docstring explaining what it does, its parameters, and its return value where non-obvious.

**Architecture:** Eight sequential tasks ordered by priority — highest-impact underdocumented files first, then medium, then low. Each task documents one or two related files, runs tests, and commits.

**Docstring Style:** Plain triple-quoted docstrings (matching existing codebase style). No Google/NumPy/RST format. No type annotations (project has none). One-line summary for simple items; multi-line with blank line + detail for complex ones.

**Tech Stack:** Python 3.12+, pytest

---

## Baseline Coverage

| Area | Before | Target |
|------|--------|--------|
| resources/mediaprocessor.py | 1.4% | 100% public methods |
| resources/metadata.py | ~0% | 100% public methods |
| converter/avcodecs.py | 10% (classes only) | 100% base/mixin methods |
| converter/ffmpeg.py | 39% | 100% public methods |
| resources/readsettings.py | 16% | 100% public methods |
| manual.py | 9.5% | 100% public functions |
| resources/postprocess.py | 0% | 100% |
| resources/lang.py | 0% | 100% |
| autoprocess/plex.py | 0% | 100% |
| converter/__init__.py | 61% | 100% public methods |

---

## File Map

### Task 1: resources/mediaprocessor.py
- Modify: `resources/mediaprocessor.py` — add module docstring + docstrings to all methods on `MediaProcessor`

### Task 2: resources/metadata.py
- Modify: `resources/metadata.py` — add module docstring + docstrings to `TMDBIDError`, `MediaType`, `Metadata` class and all its methods

### Task 3: converter/avcodecs.py
- Modify: `converter/avcodecs.py` — add docstrings to all methods on `BaseCodec`, `VideoCodec`, `AudioCodec`, `SubtitleCodec` base classes and `HWAccelVideoCodec` mixin; add brief docstring to `parse_options()` on each concrete codec class

### Task 4: converter/ffmpeg.py + converter/__init__.py
- Modify: `converter/ffmpeg.py` — add module docstring + docstrings to undocumented methods on `MediaFormatInfo`, `MediaStreamInfo`, `MediaInfo`, `FFMpeg`
- Modify: `converter/__init__.py` — add module docstring + docstrings to `parse_options()`, `convert()`, `tag()`

### Task 5: resources/readsettings.py
- Modify: `resources/readsettings.py` — add module docstring + docstrings to all methods on `SMAConfigParser` and `ReadSettings`

### Task 6: manual.py
- Modify: `manual.py` — add module docstring + docstrings to all functions and enum/exception classes

### Task 7: Small modules
- Modify: `resources/postprocess.py` — add module docstring + docstrings to `PostProcessor` methods
- Modify: `resources/lang.py` — add module docstring + docstrings to `getAlpha3TCode()`, `getAlpha2BCode()`
- Modify: `autoprocess/plex.py` — add module docstring + docstrings to `refreshPlex()`, `getPlexServer()`

### Task 8: Remaining gaps
- Modify: `resources/log.py` — add docstring to `rotator()`
- Modify: `update.py` — add module docstring + docstring to `main()`
- Modify: `resources/extensions.py` — add module docstring

---

## Task 1: Document resources/mediaprocessor.py

**Files:** `resources/mediaprocessor.py`

This is the largest and most critical file (2,607 lines). `MediaProcessor` is the core conversion orchestrator — nearly every method is undocumented. Priority: get module docstring + all public methods covered.

- [x] **Step 1: Add module docstring**

  At the top of the file (before imports), add:
  ```python
  """
  Core media processing pipeline for SMA-NG.

  Provides the MediaProcessor class which orchestrates the full conversion
  workflow: source validation, FFmpeg option generation, conversion, metadata
  tagging, file placement, and post-processing notifications.
  """
  ```

- [x] **Step 2: Document MediaProcessor.__init__**

  ```python
  """
  Initialize a MediaProcessor for the given input file or directory.

  Reads settings from config, sets up logging, and prepares the conversion
  environment. Does not begin processing until process() is called.
  """
  ```

- [x] **Step 3: Document isValidSource() and related validation methods**

  `isValidSource()`:
  ```python
  """
  Check whether the input file is a valid media source FFprobe can read.

  Returns True if the file has at least one recognized stream, False otherwise.
  Logs a warning on failure.
  """
  ```

- [x] **Step 4: Document process() and fullprocess()**

  `process()`:
  ```python
  """
  Build FFmpeg options for the input file and run the conversion.

  Reads stream info via FFprobe, applies codec/bitrate/language settings from
  config, invokes the FFmpeg converter, and places the output file. Returns
  the path to the output file, or None on failure.
  """
  ```

  `fullprocess()`:
  ```python
  """
  Run the complete processing pipeline for a single input file.

  Calls process(), then handles tagging, file replication, post-processing,
  and media manager notifications. Returns True on success, False on failure.
  """
  ```

- [x] **Step 5: Document post(), replicate(), and file-handling methods**

  Document each method with a one-liner explaining what it does, what it takes, and what it returns or side-effects.

- [x] **Step 6: Document all remaining public methods**

  Work through the full class. For each undocumented public method (no leading `_`), add at minimum a one-line docstring. For complex methods (>30 lines), add a short paragraph.

- [x] **Step 7: Run tests**

  ```bash
  source venv/bin/activate && python -m pytest tests/ -v
  ```
  Expected: all tests pass

- [x] **Step 8: Commit**

  ```bash
  git add resources/mediaprocessor.py
  git commit -m "docs: add docstrings to MediaProcessor (mediaprocessor.py)"
  ```

---

## Task 2: Document resources/metadata.py

**Files:** `resources/metadata.py`

`Metadata` class handles all TMDB lookups and MP4 tag writing. Zero method docstrings currently (plexmatch functions at module level are already documented — leave those alone).

- [x] **Step 1: Add module docstring**

  ```python
  """
  TMDB metadata lookup and MP4/MKV tag writing for SMA-NG.

  Provides the Metadata class which fetches movie/TV metadata from TMDB and
  writes tags (title, year, description, artwork, etc.) into the output file
  using mutagen. Also handles .plexmatch sidecar file generation.
  """
  ```

- [x] **Step 2: Document TMDBIDError and MediaType**

  ```python
  class TMDBIDError(Exception):
      """Raised when a TMDB ID cannot be found or resolved for the given input."""

  class MediaType(Enum):
      """Media type classification used to select the correct TMDB search endpoint."""
  ```

- [x] **Step 3: Document Metadata class and __init__**

  Class:
  ```python
  """
  Fetches metadata from TMDB and writes tags to a converted media file.

  Accepts movie or TV episode identifiers (TMDB ID, IMDB ID, TVDB ID) and
  resolves them against the TMDB API. Call writeTags() to embed the retrieved
  metadata into the output file.
  """
  ```

- [x] **Step 4: Document all Metadata methods**

  Key methods to cover:
  - `writeTags()` — describe what tags are written, what file types are supported
  - `getArtwork()` — describe artwork download/embed behavior
  - `setHDVideo()`, `setContentRating()` — brief one-liners
  - `_tmdb_*` private helpers — one-liners acceptable

- [x] **Step 5: Run tests**

  ```bash
  source venv/bin/activate && python -m pytest tests/test_metadata.py -v
  ```
  Expected: all tests pass

- [x] **Step 6: Commit**

  ```bash
  git add resources/metadata.py
  git commit -m "docs: add docstrings to Metadata class (metadata.py)"
  ```

---

## Task 3: Document converter/avcodecs.py

**Files:** `converter/avcodecs.py`

83 codec classes. Class-level docstrings exist. The gap is: base class methods and `parse_options()` / `_codec_specific_parse_options()` / `_codec_specific_produce_ffmpeg_list()` have no docstrings. Do NOT add docstrings to every concrete override — only to the base/mixin definitions where the contract is established.

- [x] **Step 1: Document BaseCodec methods**

  All methods on `BaseCodec`: `parse_options()`, `safe_options()`, `ffmpeg_codec_name`, class attributes — one-liner each.

- [x] **Step 2: Document VideoCodec, AudioCodec, SubtitleCodec base methods**

  These define the `parse_options()` contract. Document the parameters and return shape once on the base class method.

- [x] **Step 3: Document HWAccelVideoCodec mixin methods**

  Already partially documented. Fill any gaps: `_hw_parse_scale()`, `_hw_parse_quality()`, `_hw_produce_quality_opts()`, `_hw_produce_device_opts()`, `_hw_produce_scale_opts()`.

- [x] **Step 4: Document module-level docstring if missing**

  Confirm/update the existing module docstring to mention the codec hierarchy.

- [x] **Step 5: Run tests**

  ```bash
  source venv/bin/activate && python -m pytest tests/test_avcodecs.py -v
  ```
  Expected: all tests pass

- [x] **Step 6: Commit**

  ```bash
  git add converter/avcodecs.py
  git commit -m "docs: add docstrings to codec base classes and HWAccelVideoCodec mixin"
  ```

---

## Task 4: Document converter/ffmpeg.py and converter/__init__.py

**Files:** `converter/ffmpeg.py`, `converter/__init__.py`

`ffmpeg.py` is 39% documented. Key gaps: `MediaStreamInfo` methods, `MediaInfo.posters_url()` / `audio_streams` / `video_streams`, and `FFMpeg.convert()` / `FFMpeg.probe()`. `__init__.py` is missing docstrings on `parse_options()`, `convert()`, `tag()`.

- [x] **Step 1: Add module docstring to ffmpeg.py**

  ```python
  """
  FFprobe/FFmpeg wrapper for SMA-NG.

  Provides MediaFormatInfo, MediaStreamInfo, and MediaInfo data classes for
  parsing FFprobe output, and the FFMpeg class for invoking FFmpeg conversions
  with progress reporting.
  """
  ```

- [x] **Step 2: Document undocumented methods in MediaStreamInfo**

  Each property/method: one-liner explaining what stream attribute it exposes.

- [x] **Step 3: Document undocumented methods in MediaInfo and FFMpeg**

  `FFMpeg.convert()` is the most important — document accepted option dict shape and yield behavior for progress.

- [x] **Step 4: Add module docstring to converter/__init__.py**

  ```python
  """
  High-level converter interface for SMA-NG.

  Wraps FFMpeg and codec definitions into the Converter class, which translates
  structured option dicts into FFmpeg command-line arguments and runs conversions.
  """
  ```

- [x] **Step 5: Document Converter.parse_options(), convert(), tag()**

  `parse_options()`:
  ```python
  """
  Translate a structured options dict into FFmpeg command-line argument lists.

  Accepts a dict with 'format', 'audio', 'video', 'subtitle', 'map' keys and
  returns a list of ffmpeg argument strings ready to pass to FFMpeg.convert().
  """
  ```

- [x] **Step 6: Run tests**

  ```bash
  source venv/bin/activate && python -m pytest tests/test_converter.py tests/ -v
  ```
  Expected: all tests pass

- [x] **Step 7: Commit**

  ```bash
  git add converter/ffmpeg.py converter/__init__.py
  git commit -m "docs: add docstrings to converter/ffmpeg.py and converter/__init__.py"
  ```

---

## Task 5: Document resources/readsettings.py

**Files:** `resources/readsettings.py`

`SMAConfigParser` has 0 method docstrings. `ReadSettings` is 26% documented. Key gaps: `__init__()`, `readConfig()`, all `_apply_*` helpers that lack docstrings.

- [x] **Step 1: Add module docstring**

  ```python
  """
  Configuration file parser for SMA-NG.

  Reads autoProcess.ini (or the file at $SMA_CONFIG) using SMAConfigParser,
  a thin ConfigParser subclass, and exposes all settings as attributes on the
  ReadSettings instance.
  """
  ```

- [x] **Step 2: Document SMAConfigParser methods**

  All 9 methods — one-liners. `getint()` / `getboolean()` overrides: note what default they apply.

- [x] **Step 3: Document ReadSettings.__init__ and readConfig()**

  `readConfig()` should describe the section-by-section parsing and where settings end up.

- [x] **Step 4: Document all remaining ReadSettings methods**

  Each `_apply_*` helper: one line saying which config section it processes and what attribute(s) it sets.

- [x] **Step 5: Run tests**

  ```bash
  source venv/bin/activate && python -m pytest tests/test_config.py -v
  ```
  Expected: all tests pass

- [x] **Step 6: Commit**

  ```bash
  git add resources/readsettings.py
  git commit -m "docs: add docstrings to SMAConfigParser and ReadSettings (readsettings.py)"
  ```

---

## Task 6: Document manual.py

**Files:** `manual.py`

User-facing CLI tool. 16 functions with 2 documented. Add module docstring and document all functions.

- [x] **Step 1: Add module docstring**

  ```python
  """
  CLI tool for manual media conversion and tagging.

  Run a single file or directory through the SMA-NG conversion pipeline,
  optionally auto-detecting metadata from the filename or accepting explicit
  TMDB/TVDB IDs. Use -oo to preview FFmpeg options without converting.

  Usage:
      python manual.py -i /path/to/file.mkv -a
      python manual.py -i /path/to/file.mkv -tmdb 603
      python manual.py -i /path/to/dir -a
  """
  ```

- [x] **Step 2: Document MediaTypes and SkipFileException**

  ```python
  class MediaTypes(Enum):
      """Media type enum used to route files to movie or TV processing."""

  class SkipFileException(Exception):
      """Raised to skip a file during batch processing without aborting the run."""
  ```

- [x] **Step 3: Document all functions**

  Key functions:
  - `getInfo()` — describe the interactive metadata resolution flow
  - `getValue()`, `getYesNo()` — one-liners
  - `mediatype()` — describe how it detects media type from filename
  - `main()` — describe argument parsing and batch vs single-file flow

- [x] **Step 4: Run tests**

  ```bash
  source venv/bin/activate && python -m pytest tests/ -v
  ```
  Expected: all tests pass

- [x] **Step 5: Commit**

  ```bash
  git add manual.py
  git commit -m "docs: add docstrings to manual.py"
  ```

---

## Task 7: Document small modules

**Files:** `resources/postprocess.py`, `resources/lang.py`, `autoprocess/plex.py`

Small files with zero documentation.

- [x] **Step 1: Document resources/postprocess.py**

  Module docstring + `PostProcessor` class docstring + all 8 methods. Focus on `run()` (what scripts it calls, cwd, environment) and `__init__()` (what it reads from settings).

- [x] **Step 2: Document resources/lang.py**

  Module docstring:
  ```python
  """
  Language code conversion utilities for SMA-NG.

  Converts between ISO 639-2/T, 639-2/B, and 639-1 language codes used
  in FFprobe stream metadata and configuration files.
  """
  ```

  `getAlpha3TCode()` and `getAlpha2BCode()`: document input/output code formats.

- [x] **Step 3: Document autoprocess/plex.py**

  Module docstring + `refreshPlex()` + `getPlexServer()`. Note what settings they read, what they call, and what they return.

- [x] **Step 4: Run tests**

  ```bash
  source venv/bin/activate && python -m pytest tests/ -v
  ```
  Expected: all tests pass

- [x] **Step 5: Commit**

  ```bash
  git add resources/postprocess.py resources/lang.py autoprocess/plex.py
  git commit -m "docs: add docstrings to postprocess.py, lang.py, plex.py"
  ```

---

## Task 8: Remaining gaps

**Files:** `resources/log.py`, `update.py`, `resources/extensions.py`

Final cleanup pass on files that are mostly documented already or very small.

- [x] **Step 1: Document rotator() in resources/log.py**

  Add a one-liner docstring explaining it's a log rotation callback and what it does with the source file.

- [x] **Step 2: Document update.py**

  Module docstring (what the update script does) + `main()` docstring.

- [x] **Step 3: Document resources/extensions.py**

  Module docstring explaining the constants (API key, valid input/output extensions).

- [x] **Step 4: Run full test suite**

  ```bash
  source venv/bin/activate && python -m pytest tests/ -v
  ```
  Expected: all tests pass

- [x] **Step 5: Commit**

  ```bash
  git add resources/log.py update.py resources/extensions.py
  git commit -m "docs: fill remaining docstring gaps (log.py, update.py, extensions.py)"
  ```

---

## Verification

After all tasks are complete:

```bash
source venv/bin/activate && python -m pytest tests/ -v
```

Expected: all tests pass with no regressions.

Manual spot-check:
```bash
source venv/bin/activate && python -c "
import pydoc
import resources.mediaprocessor
import resources.metadata
import converter.ffmpeg
help(resources.mediaprocessor.MediaProcessor.process)
help(resources.metadata.Metadata.writeTags)
"
```

Expected: each `help()` call shows a meaningful docstring, not just `None`.
