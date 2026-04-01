# SMA-NG Codebase Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Remove dead code, eliminate duplication across post-processing scripts / GPU codecs / downloader scripts, add missing test coverage, and decompose the oversized mediaprocessor.py.

**Architecture:** Six sequential phases — dead code removal first (reduces noise for later phases), then deduplication of postSonarr/postRadarr into shared mediamanager module, GPU codec base class extraction, downloader helper extraction, new tests, and finally mediaprocessor decomposition. Each phase produces a working, testable commit.

**Tech Stack:** Python 3.12+, pytest, FFmpeg codec abstractions

---

## File Map

### Phase 1: Dead Code Removal

- Delete: `autoprocess/sonarr.py`, `autoprocess/radarr.py`
- Modify: `resources/mediaprocessor.py` (remove `__future__`, decode workarounds)
- Modify: `resources/readsettings.py` (remove Py2 compat imports/checks)
- Modify: `resources/log.py` (remove Py2 ConfigParser import)
- Modify: `manual.py` (remove raw_input, decode, version check)
- Modify: `converter/ffmpeg.py` (remove unused decode line)
- Delete: `__init__.py` (root), `resources/__init__.py`, `autoprocess/__init__.py`, `config/__init__.py`

### Phase 2: Media Manager Deduplication

- Create: `resources/mediamanager.py` (~80 lines — shared API helpers)
- Modify: `postSonarr.py` (use shared helpers, ~60 lines down from ~200)
- Modify: `postRadarr.py` (use shared helpers, ~60 lines down from ~200)
- Modify: `tests/test_integration_scripts.py` (update patches)

### Phase 3: GPU Codec Base Class

- Modify: `converter/avcodecs.py` (extract `HWAccelVideoCodec` mixin, reduce ~400 lines)

### Phase 4: Downloader Helpers

- Modify: `resources/webhook_client.py` (add `check_bypass()`, `submit_path()`)
- Modify: `SABPostProcess.py`, `delugePostProcess.py`, `qBittorrentPostProcess.py`, `uTorrentPostProcess.py`
- Modify: `tests/test_webhook_client.py` (test new helpers)
- Modify: `tests/test_integration_scripts.py` (update patches)

### Phase 5: New Tests

- Create: `tests/test_metadata.py`
- Create: `tests/test_converter.py`
- Create: `tests/test_postprocess.py`
- Modify: `tests/test_mediaprocessor.py` (add generateOptions, isValidSource tests)

### Phase 6: MediaProcessor Decomposition

- Create: `resources/subtitles.py` (~200 lines — SubtitleProcessor class)
- Modify: `resources/mediaprocessor.py` (delegate to SubtitleProcessor, ~400 lines removed)
- Create: `tests/test_subtitles.py`

---

## Task 1: Delete unused autoprocess modules

**Files:**
- Delete: `autoprocess/sonarr.py`
- Delete: `autoprocess/radarr.py`

These modules define `processEpisode()` and `processMovie()` but are never imported anywhere. The project uses the webhook-based `postSonarr.py`/`postRadarr.py` flow instead.

- [x] **Step 1: Verify modules are truly unused**

Run: `grep -r 'autoprocess.sonarr\|autoprocess.radarr\|from autoprocess import sonarr\|from autoprocess import radarr' --include='*.py' .`
Expected: No output (zero matches)

- [x] **Step 2: Delete the files**

```bash
rm autoprocess/sonarr.py autoprocess/radarr.py
```

- [x] **Step 3: Run tests**

Run: `python -m pytest tests/ -v`
Expected: All 278 tests pass

- [x] **Step 4: Commit**

```bash
git add -A
git commit -m "Remove unused autoprocess/sonarr.py and autoprocess/radarr.py"
```

---

## Task 2: Remove Python 2 compatibility code

**Files:**
- Modify: `resources/mediaprocessor.py:1` (remove `from __future__ import unicode_literals`)
- Modify: `resources/mediaprocessor.py` (5 decode workaround blocks — lines ~2029, ~2166, ~2460, ~2506, ~2534)
- Modify: `resources/readsettings.py:6-13` (simplify ConfigParser/reload imports)
- Modify: `resources/readsettings.py:88-90` (simplify getint)
- Modify: `resources/readsettings.py:374-375` (remove Py2 warning)
- Modify: `resources/readsettings.py:411-432` (remove Py2 encoding block)
- Modify: `resources/log.py:7-10` (simplify ConfigParser import)
- Modify: `manual.py:22-23` (remove raw_input alias)
- Modify: `manual.py:68,84,106` (replace raw_input with input)
- Modify: `manual.py:88-91` (remove decode workaround)
- Modify: `converter/ffmpeg.py:842` (remove unused decode statement)

- [x] **Step 1: Clean up resources/readsettings.py imports and Py2 checks**

Replace the try/except ConfigParser import (lines 6-9) with:

```python
from configparser import ConfigParser
```

Replace the try/except importlib.reload (lines 10-13) with:

```python
from importlib import reload
```

Simplify `getint()` (lines 87-90) — remove the `sys.version[0] == '2'` branch, keep only:

```python
def getint(self, section, option, vars=None, fallback=0):
    return super(SMAConfigParser, self).getint(section, option, vars=vars, fallback=fallback)
```

Remove the Python 2 warning (lines 374-375):

```python
if sys.version_info.major == 2:
    self.log.warning("Python 2 is no longer officially supported. Use with caution.")
```

Remove the entire Python 2 encoding setup block (lines 411-432, the `if sys.version[0] == '2':` block with locale/setdefaultencoding).

- [x] **Step 2: Clean up resources/log.py**

Replace the try/except ConfigParser import (lines 7-10) with:

```python
from configparser import RawConfigParser
```

- [x] **Step 3: Clean up resources/mediaprocessor.py**

Remove line 1: `from __future__ import unicode_literals`

For each of these 5 decode workaround blocks, remove the try/except and keep only the except branch (which is the Python 3 code):

Around line 2029 — simplify to: `newoutputfile = os.path.join(input_dir, outputfilename)`
Around line 2166 — simplify to: `outputfile = os.path.join(output_dir, filename + counter + "." + output_extension)`
Around line 2460 — simplify to: `outputfile = inputfile + TEMP_EXT`
Around line 2506 — simplify to: `shutil.copy(inputfile, d)` (keep KeyboardInterrupt raise)
Around line 2534 — remove `.decode(sys.getfilesystemencoding())` from the `shutil.move` call

- [x] **Step 4: Clean up manual.py**

Remove lines 22-23 (the `if sys.version[0] == "3": raw_input = input` block).

Replace all `raw_input(` calls with `input(`:
- Line 68: `result = input("#: ")`
- Line 84: `value = input("#: ").strip(' \"')`
- Line 106: `data = input("# [y/n]: ")`

Remove lines 88-91 (the `try: value = value.decode(...)` block).

- [x] **Step 5: Clean up converter/ffmpeg.py**

Remove the unused standalone `stderr_data.decode(console_encoding)` line (~line 842) that doesn't assign its result.

- [x] **Step 6: Run tests**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [x] **Step 7: Commit**

```bash
git add -A
git commit -m "Remove Python 2 compatibility code (project requires 3.12+)"
```

---

## Task 3: Remove empty __init__.py files

**Files:**
- Delete: `__init__.py` (root)
- Delete: `resources/__init__.py`
- Delete: `autoprocess/__init__.py`
- Delete: `config/__init__.py`

Keep `converter/__init__.py` (it exports the Converter class) and `tests/__init__.py`.

- [x] **Step 1: Delete empty init files**

```bash
rm __init__.py resources/__init__.py autoprocess/__init__.py config/__init__.py
```

- [x] **Step 2: Run tests**

Run: `python -m pytest tests/ -v`
Expected: All tests pass. If any import fails, the __init__.py was needed — restore it.

- [x] **Step 3: Commit**

```bash
git add -A
git commit -m "Remove empty __init__.py files that serve no purpose"
```

---

## Task 4: Extract shared media manager API helpers

**Files:**
- Create: `resources/mediamanager.py`
- Modify: `postSonarr.py`
- Modify: `postRadarr.py`
- Modify: `tests/test_integration_scripts.py`

- [x] **Step 1: Create resources/mediamanager.py with shared API helpers**

```python
"""Shared API helpers for Sonarr/Radarr post-processing scripts."""
import time
import requests


def build_api_url(settings_section):
    """Build base URL from a Sonarr/Radarr settings section dict."""
    protocol = "https://" if settings_section.get('ssl') else "http://"
    return protocol + settings_section['host'] + ":" + str(settings_section['port']) + settings_section.get('webroot', '')


def build_headers(settings_section, user_agent):
    """Build API headers with auth key."""
    return {'X-Api-Key': settings_section['apikey'], 'User-Agent': user_agent}


def api_command(base_url, headers, payload, log):
    """POST a command to /api/v3/command and return the response."""
    url = base_url + "/api/v3/command"
    log.debug("API command: %s" % str(payload))
    r = requests.post(url, json=payload, headers=headers)
    rstate = r.json()
    try:
        rstate = rstate[0]
    except (KeyError, IndexError, TypeError):
        pass
    log.info("API response: ID %s %s." % (rstate.get('id', '?'), rstate.get('status', '?')))
    return rstate


def wait_for_command(base_url, headers, command_id, log, retries=6, delay=10):
    """Poll /api/v3/command/{id} until completed or retries exhausted."""
    url = base_url + "/api/v3/command/" + str(command_id)
    r = requests.get(url, headers=headers)
    command = r.json()
    attempts = 0
    while command['status'].lower() not in ['complete', 'completed'] and attempts < retries:
        time.sleep(delay)
        r = requests.get(url, headers=headers)
        command = r.json()
        attempts += 1
    return command['status'].lower() in ['complete', 'completed']


def api_get(base_url, headers, endpoint, log):
    """GET a resource at /api/v3/{endpoint}."""
    url = base_url + "/api/v3/" + endpoint
    r = requests.get(url, headers=headers)
    return r.json()


def api_put(base_url, headers, endpoint, data, log):
    """PUT a resource at /api/v3/{endpoint}."""
    url = base_url + "/api/v3/" + endpoint
    r = requests.put(url, json=data, headers=headers)
    return r.json()
```

- [x] **Step 2: Rewrite postSonarr.py using shared helpers**

Replace the 7 inline API functions (rescanRequest, waitForCommand, renameRequest, getEpisode, updateEpisode, getEpisodeFile, updateEpisodeFile) with calls to the shared module. The main script logic stays but uses `mediamanager.api_command()`, `mediamanager.wait_for_command()`, `mediamanager.api_get()`, `mediamanager.api_put()`.

Key mappings:
- `rescanRequest(baseURL, headers, seriesid, log)` → `api_command(base_url, headers, {'name': 'RescanSeries', 'seriesId': seriesid}, log)`
- `renameRequest(baseURL, headers, fileid, seriesid, log)` → `api_command(base_url, headers, {'name': 'RenameFiles', 'files': [fileid], 'seriesId': seriesid}, log)`
- `getEpisode(baseURL, headers, episodeid, log)` → `api_get(base_url, headers, 'episode/' + str(episodeid), log)`
- `updateEpisode(baseURL, headers, new, episodeid, log)` → `api_put(base_url, headers, 'episode/' + str(episodeid), new, log)`
- `getEpisodeFile(...)` → `api_get(base_url, headers, 'episodefile/' + str(id), log)`
- `updateEpisodeFile(...)` → `api_put(base_url, headers, 'episodefile/' + str(id), data, log)`

- [x] **Step 3: Rewrite postRadarr.py using shared helpers**

Same approach — replace inline functions with shared helper calls:
- `rescanRequest` → `api_command(base_url, headers, {'name': 'RescanMovie', 'movieId': movieid}, log)`
- `getMovie` → `api_get(base_url, headers, 'movie/' + str(movieid), log)`
- etc.

- [x] **Step 4: Update test patches in test_integration_scripts.py**

The `@patch` decorators reference `resources.readsettings.ReadSettings._validate_binaries` and `resources.webhook_client.submit_and_wait`. These should still work since postSonarr/postRadarr still import from those modules. Verify patch targets still match.

- [x] **Step 5: Run tests**

Run: `python -m pytest tests/test_integration_scripts.py -v`
Expected: All integration tests pass

- [x] **Step 6: Commit**

```bash
git add -A
git commit -m "Extract shared media manager API helpers, deduplicate postSonarr/postRadarr"
```

---

## Task 5: Extract GPU codec base class

**Files:**
- Modify: `converter/avcodecs.py`

This is the highest-complexity refactor. The GPU codec classes (NVEnc, VAAPI, QSV variants) duplicate three patterns:
1. **CRF→quality conversion** with range validation
2. **Width/height→scale variable** conversion
3. **Scale filter construction** with device initialization

- [x] **Step 1: Add HWAccelVideoCodec mixin before the first GPU codec class**

Insert after the base H264Codec class (around line 1110). The mixin provides shared methods that GPU codec subclasses call:

```python
class HWAccelVideoCodec:
    """Mixin for hardware-accelerated video codecs.

    Subclasses set class attributes to configure behavior:
        hw_scale_prefix: str - variable name prefix for scale vars (e.g., 'qsv', 'vaapi', 'nvenc')
        hw_quality_flag: str - FFmpeg flag name ('-qp' or '-global_quality')
        hw_quality_range: tuple - (min, max) valid range for quality value
        hw_default_fmt: str or None - default pixel format (e.g., 'nv12' for VAAPI)
        hw_look_ahead: bool - whether to append -look_ahead 0 (QSV)
    """
    hw_scale_prefix = ''
    hw_quality_flag = '-qp'
    hw_quality_range = (0, 52)
    hw_default_fmt = None
    hw_look_ahead = False

    def _hw_parse_scale(self, safe):
        """Convert width/height to hw-prefixed scale variables."""
        prefix = self.hw_scale_prefix
        if 'width' in safe:
            w = safe['width']
            if w % 2 != 0:
                w += 1
            safe[prefix + '_wscale'] = w
            del safe['width']
        if 'height' in safe:
            h = safe['height']
            if h % 2 != 0:
                h += 1
            safe[prefix + '_hscale'] = h
            del safe['height']

    def _hw_parse_quality(self, safe):
        """Convert CRF to hardware quality parameter."""
        if 'crf' in safe:
            qmin, qmax = self.hw_quality_range
            if safe['crf'] < qmin or safe['crf'] > qmax:
                del safe['crf']
            else:
                if 'bitrate' in safe:
                    del safe['bitrate']

    def _hw_produce_quality_opts(self, safe):
        """Produce quality flag options."""
        optlist = []
        if 'crf' in safe:
            optlist.extend([self.hw_quality_flag, str(safe['crf'])])
            if 'maxrate' in safe:
                optlist.extend(['-maxrate:v', str(safe['maxrate']) + 'k'])
            if 'bufsize' in safe:
                optlist.extend(['-bufsize:v', str(safe['bufsize']) + 'k'])
        return optlist

    def _hw_produce_device_opts(self, safe):
        """Produce device initialization options."""
        optlist = []
        prefix = self.hw_scale_prefix
        if 'device' in safe:
            optlist.extend(['-filter_hw_device', safe['device']])
            if 'decode_device' in safe and safe['decode_device'] != safe['device']:
                safe[prefix + '_hwdownload'] = True
        elif 'decode_device' in safe:
            safe[prefix + '_hwdownload'] = True
        return optlist

    def _hw_produce_scale_opts(self, safe):
        """Produce scale filter options."""
        prefix = self.hw_scale_prefix
        wkey = prefix + '_wscale'
        hkey = prefix + '_hscale'
        if wkey in safe and hkey in safe:
            return ['-vf', '%s=w=%s:h=%s' % (self.scale_filter, safe[wkey], safe[hkey])]
        elif wkey in safe:
            return ['-vf', '%s=w=%s:h=trunc(ow/a/2)*2' % (self.scale_filter, safe[wkey])]
        elif hkey in safe:
            return ['-vf', '%s=w=trunc((oh*a)/2)*2:h=%s' % (self.scale_filter, safe[hkey])]
        return []
```

- [x] **Step 2: Refactor NVEnc H264/H265 codecs to use the mixin**

Update `NVEncH264Codec` to inherit from both `H264Codec` and `HWAccelVideoCodec`:

```python
class NVEncH264Codec(HWAccelVideoCodec, H264Codec):
    codec_name = 'h264_nvenc'
    ffmpeg_codec_name = 'h264_nvenc'
    scale_filter = 'scale_npp'
    max_depth = 8
    hw_scale_prefix = 'nvenc'
    hw_quality_flag = '-qp'
    hw_quality_range = (0, 52)

    encoder_options = H264Codec.encoder_options.copy()
    encoder_options.update({'decode_device': str, 'device': str})

    def _codec_specific_parse_options(self, safe):
        self._hw_parse_scale(safe)
        self._hw_parse_quality(safe)
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = super()._codec_specific_produce_ffmpeg_list(safe, stream)
        optlist.extend(self._hw_produce_quality_opts(safe))
        optlist.extend(self._hw_produce_device_opts(safe))
        optlist.extend(self._hw_produce_scale_opts(safe))
        return optlist
```

Apply the same pattern to NVEncH265Codec, then verify H264VAAPICodec, H265VAAPICodec, H264QSVCodec, H265QSVCodec, AV1QSVCodec, AV1VAAPICodec, Vp9QSVCodec can each use the mixin methods.

**Important:** Some codecs have unique behavior that won't fit the mixin exactly:
- VAAPI codecs have a complex `format=X|vaapi,hwupload,scale_vaapi=...` filter chain — override `_hw_produce_scale_opts()`
- QSV codecs build optlist from scratch (don't call super() at start) — override the full `_codec_specific_produce_ffmpeg_list()`
- The Patched variants (NVEncH265CodecPatched, H265QSVCodecPatched) have `safe_framedata()` — leave these as-is

Focus on extracting the common parts (parse_scale, parse_quality, device_opts) and let unique filter chains remain overridden.

- [x] **Step 3: Run codec tests**

Run: `python -m pytest tests/test_avcodecs.py -v`
Expected: All codec tests pass. This is the critical gate — if any codec test breaks, the mixin logic diverges from the original behavior.

- [x] **Step 4: Commit**

```bash
git add converter/avcodecs.py
git commit -m "Extract HWAccelVideoCodec mixin, reduce GPU codec duplication"
```

---

## Task 6: Extract downloader bypass/submit helpers

**Files:**
- Modify: `resources/webhook_client.py`
- Modify: `SABPostProcess.py`
- Modify: `delugePostProcess.py`
- Modify: `qBittorrentPostProcess.py`
- Modify: `uTorrentPostProcess.py`
- Modify: `tests/test_webhook_client.py`
- Modify: `tests/test_integration_scripts.py`

- [x] **Step 1: Write tests for new helpers in test_webhook_client.py**

Add to the end of `tests/test_webhook_client.py`:

```python
class TestCheckBypass:
    def test_match(self):
        from resources.webhook_client import check_bypass
        assert check_bypass(['sonarr', 'bypass'], 'bypass-movies') is True

    def test_no_match(self):
        from resources.webhook_client import check_bypass
        assert check_bypass(['bypass'], 'sonarr') is False

    def test_empty_list(self):
        from resources.webhook_client import check_bypass
        assert check_bypass([], 'anything') is False

    def test_empty_strings_ignored(self):
        from resources.webhook_client import check_bypass
        assert check_bypass(['', 'bypass'], 'bypass') is True

    def test_prefix_match(self):
        from resources.webhook_client import check_bypass
        assert check_bypass(['tv'], 'tv-sonarr') is True


class TestSubmitPath:
    @patch('resources.webhook_client.submit_job')
    def test_single_file(self, mock_submit, tmp_path):
        from resources.webhook_client import submit_path
        f = tmp_path / "movie.mkv"
        f.touch()
        count = submit_path(str(f))
        assert count == 1
        mock_submit.assert_called_once()

    @patch('resources.webhook_client.submit_job')
    def test_directory(self, mock_submit, tmp_path):
        from resources.webhook_client import submit_path
        (tmp_path / "a.mkv").touch()
        (tmp_path / "b.mkv").touch()
        count = submit_path(str(tmp_path))
        assert count == 2

    @patch('resources.webhook_client.submit_job')
    def test_missing_path_returns_zero(self, mock_submit):
        from resources.webhook_client import submit_path
        count = submit_path("/nonexistent/path")
        assert count == 0
        mock_submit.assert_not_called()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_webhook_client.py::TestCheckBypass -v`
Expected: ImportError — function doesn't exist yet

- [x] **Step 3: Implement helpers in resources/webhook_client.py**

Add to the end of `resources/webhook_client.py`:

```python
def check_bypass(bypass_list, value):
    """Check if value matches any bypass prefix. Returns True if bypassed."""
    for b in bypass_list:
        if b and value.startswith(b):
            return True
    return False


def submit_path(path, logger=None):
    """Submit all files at path (file or directory) to daemon. Returns count of submitted jobs."""
    count = 0
    if os.path.isfile(path):
        if submit_job(path, logger=logger):
            count += 1
    elif os.path.isdir(path):
        for root, _, files in os.walk(path):
            for f in files:
                if submit_job(os.path.join(root, f), logger=logger):
                    count += 1
    else:
        if logger:
            logger.error("Path does not exist: %s" % path)
    return count
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_webhook_client.py -v`
Expected: All pass including new TestCheckBypass and TestSubmitPath

- [x] **Step 5: Refactor downloader scripts to use helpers**

In each of SABPostProcess.py, delugePostProcess.py, qBittorrentPostProcess.py, uTorrentPostProcess.py:

Replace the bypass check block (~10 lines) with:

```python
if webhook.check_bypass(settings.DOWNLOADER.get('bypass', []), label):
    log.info("Bypass label matched, skipping.")
    sys.exit(0)
```

Replace the file walk/submit block (~8 lines) with:

```python
count = webhook.submit_path(path, logger=log)
```

Keep all pre/post action code (qBittorrent pause/resume, Deluge remove, uTorrent WebUI actions) — these are unique to each script.

- [x] **Step 6: Run integration tests**

Run: `python -m pytest tests/test_integration_scripts.py -v`
Expected: All pass

- [x] **Step 7: Commit**

```bash
git add -A
git commit -m "Extract check_bypass() and submit_path() helpers, deduplicate downloader scripts"
```

---

## Task 7: Add test coverage for metadata.py

**Files:**
- Create: `tests/test_metadata.py`

Focus on testable logic that doesn't require real TMDB API calls.

- [x] **Step 1: Write tests for Metadata construction and MediaType enum**

```python
"""Tests for resources/metadata.py."""
import os
import pytest
from unittest.mock import patch, MagicMock
from resources.metadata import Metadata, MediaType


class TestMediaType:
    def test_movie_type(self):
        assert MediaType.Movie.value == 'movie'

    def test_tv_type(self):
        assert MediaType.TV.value == 'tv'


class TestMetadataConstruction:
    @patch('resources.metadata.tmdb')
    def test_movie_metadata_with_tmdbid(self, mock_tmdb):
        mock_movie = MagicMock()
        mock_movie.info.return_value = {
            'title': 'The Matrix',
            'release_date': '1999-03-31',
            'overview': 'A computer hacker...',
            'id': 603,
        }
        mock_tmdb.Movies.return_value = mock_movie
        m = Metadata(MediaType.Movie, tmdbid=603)
        assert m.tmdbid == 603
        assert m.mediatype == MediaType.Movie

    @patch('resources.metadata.tmdb')
    def test_tv_metadata_with_tmdbid(self, mock_tmdb):
        mock_tv = MagicMock()
        mock_tv.info.return_value = {
            'name': 'Breaking Bad',
            'id': 1396,
            'first_air_date': '2008-01-20',
        }
        mock_tv_season = MagicMock()
        mock_tv_season.info.return_value = {
            'episodes': [{'episode_number': 1, 'name': 'Pilot', 'overview': 'test'}],
        }
        mock_tmdb.TV.return_value = mock_tv
        mock_tmdb.TV_Seasons.return_value = mock_tv_season
        m = Metadata(MediaType.TV, tmdbid=1396, season=1, episode=1)
        assert m.mediatype == MediaType.TV
```

- [x] **Step 2: Run tests**

Run: `python -m pytest tests/test_metadata.py -v`
Expected: Tests pass (adjust mocks as needed based on actual Metadata.__init__ behavior)

- [x] **Step 3: Add tests for update_plexmatch**

These can reuse patterns from `tests/test_naming.py::TestPlexmatch` which already tests `update_plexmatch`. Verify that test file already covers the main paths — if so, skip adding duplicates.

- [x] **Step 4: Commit**

```bash
git add tests/test_metadata.py
git commit -m "Add tests for Metadata construction and MediaType"
```

---

## Task 8: Add test coverage for Converter class

**Files:**
- Create: `tests/test_converter.py`

- [x] **Step 1: Write tests for Converter initialization and codec lookups**

```python
"""Tests for converter/__init__.py Converter class."""
from converter import Converter, ConverterError
from converter.avcodecs import video_codec_list, audio_codec_list


class TestConverterInit:
    def test_video_codecs_populated(self):
        c = Converter(ffmpeg_path='ffmpeg', ffprobe_path='ffprobe')
        assert len(c.video_codecs) > 0
        assert 'h264' in c.video_codecs

    def test_audio_codecs_populated(self):
        c = Converter(ffmpeg_path='ffmpeg', ffprobe_path='ffprobe')
        assert 'aac' in c.audio_codecs

    def test_formats_populated(self):
        c = Converter(ffmpeg_path='ffmpeg', ffprobe_path='ffprobe')
        assert 'mp4' in c.formats


class TestCodecLookups:
    def test_codec_name_to_ffmpeg(self):
        result = Converter.codec_name_to_ffmpeg_codec_name('h264')
        assert result == 'libx264'

    def test_codec_name_to_ffprobe(self):
        result = Converter.codec_name_to_ffprobe_codec_name('aac')
        assert result is not None

    def test_unknown_codec_returns_none(self):
        result = Converter.codec_name_to_ffmpeg_codec_name('nonexistent')
        assert result is None

    def test_encoder_lookup(self):
        enc = Converter.encoder('h264')
        assert enc is not None
        assert enc.codec_name == 'h264'


class TestParseOptions:
    def test_missing_source_raises(self):
        c = Converter(ffmpeg_path='ffmpeg', ffprobe_path='ffprobe')
        import pytest
        with pytest.raises(ConverterError):
            c.parse_options({'format': 'mp4', 'audio': {'codec': 'aac'}})

    def test_no_streams_raises(self):
        c = Converter(ffmpeg_path='ffmpeg', ffprobe_path='ffprobe')
        import pytest
        with pytest.raises(ConverterError):
            c.parse_options({'format': 'mp4', 'source': ['/dev/null']})
```

- [x] **Step 2: Run tests**

Run: `python -m pytest tests/test_converter.py -v`
Expected: All pass

- [x] **Step 3: Commit**

```bash
git add tests/test_converter.py
git commit -m "Add tests for Converter class initialization and codec lookups"
```

---

## Task 9: Add test coverage for PostProcessor

**Files:**
- Create: `tests/test_postprocess.py`

- [x] **Step 1: Write tests for PostProcessor**

```python
"""Tests for resources/postprocess.py."""
import os
import pytest
from unittest.mock import patch, MagicMock
from resources.postprocess import PostProcessor
from resources.metadata import MediaType


class TestPostProcessor:
    def test_no_scripts_dir(self, tmp_path):
        """PostProcessor with nonexistent scripts dir gathers nothing."""
        pp = PostProcessor(str(tmp_path / "nonexistent"))
        assert pp.scripts == []

    def test_gathers_py_scripts(self, tmp_path):
        """PostProcessor finds .py files in the post_process dir."""
        scripts_dir = tmp_path / "post_process"
        scripts_dir.mkdir()
        (scripts_dir / "myscript.py").write_text("# test")
        (scripts_dir / "notascript.txt").write_text("nope")
        (scripts_dir / "__init__.py").write_text("")
        pp = PostProcessor(str(scripts_dir))
        script_names = [os.path.basename(s) for s in pp.scripts]
        assert "myscript.py" in script_names
        assert "notascript.txt" not in script_names
        assert "__init__.py" not in script_names
```

- [x] **Step 2: Run tests**

Run: `python -m pytest tests/test_postprocess.py -v`
Expected: Pass (adjust based on actual PostProcessor.__init__ signature)

- [x] **Step 3: Commit**

```bash
git add tests/test_postprocess.py
git commit -m "Add tests for PostProcessor script discovery"
```

---

## Task 10: Add more mediaprocessor.py test coverage

**Files:**
- Modify: `tests/test_mediaprocessor.py`

- [x] **Step 1: Add tests for isValidSource behavior**

```python
class TestIsValidSource:
    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_nonexistent_file_returns_none(self, mock_validate, tmp_ini):
        from resources.mediaprocessor import MediaProcessor
        from resources.readsettings import ReadSettings
        settings = ReadSettings(tmp_ini())
        mp = MediaProcessor(settings)
        result = mp.isValidSource("/nonexistent/file.mkv")
        assert result is None

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_undersized_file_returns_none(self, mock_validate, tmp_ini, tmp_path):
        from resources.mediaprocessor import MediaProcessor
        from resources.readsettings import ReadSettings
        ini = tmp_ini()
        # Set minimum size to 100MB
        with open(ini, 'r') as f:
            content = f.read()
        content = content.replace('minimum-size = 0', 'minimum-size = 100')
        with open(ini, 'w') as f:
            f.write(content)
        settings = ReadSettings(ini)
        mp = MediaProcessor(settings)
        small_file = tmp_path / "tiny.mkv"
        small_file.write_bytes(b'\x00' * 1024)  # 1KB file
        result = mp.isValidSource(str(small_file))
        assert result is None
```

- [x] **Step 2: Run tests**

Run: `python -m pytest tests/test_mediaprocessor.py -v`
Expected: All pass

- [x] **Step 3: Commit**

```bash
git add tests/test_mediaprocessor.py
git commit -m "Add isValidSource tests for mediaprocessor"
```

---

## Task 11: Extract SubtitleProcessor from mediaprocessor.py

**Files:**
- Create: `resources/subtitles.py`
- Modify: `resources/mediaprocessor.py`
- Create: `tests/test_subtitles.py`

This extracts 6 subtitle-related methods into a focused class while keeping the same interface for callers within mediaprocessor.py.

- [x] **Step 1: Create resources/subtitles.py**

Extract these methods from MediaProcessor into a new SubtitleProcessor class:
- `scanForExternalSubs()`
- `processExternalSub()`
- `downloadSubtitles()`
- `custom_scan_video()` (static)
- `burnSubtitleFilter()`
- `syncExternalSub()`

The class takes `settings`, `converter`, and `log` in its constructor (same dependencies these methods use within MediaProcessor).

```python
"""Subtitle scanning, downloading, burning, and syncing."""
import os


class SubtitleProcessor:
    """Handles all subtitle operations for media conversion."""

    def __init__(self, settings, converter, log):
        self.settings = settings
        self.converter = converter
        self.log = log

    # Move the 6 methods here, replacing self.settings/self.converter/self.log
    # references (they're the same).
    # For methods that call other MediaProcessor methods (like self.isValidSubtitleSource,
    # self.isImageBasedSubtitle, self.sortStreams, self.validLanguage, etc.),
    # pass a reference to the MediaProcessor instance or accept callbacks.
```

**Important design decision:** Since subtitle methods call ~10 other MediaProcessor methods (isValidSubtitleSource, isImageBasedSubtitle, sortStreams, validLanguage, checkDisposition, parseFile, getSubExtensionFromCodec, getSubOutputFile, setPermissions), SubtitleProcessor should accept a `media_processor` reference for these calls rather than duplicating them:

```python
class SubtitleProcessor:
    def __init__(self, media_processor):
        self.mp = media_processor
        self.settings = media_processor.settings
        self.converter = media_processor.converter
        self.log = media_processor.log
```

- [x] **Step 2: Wire SubtitleProcessor into MediaProcessor**

In `resources/mediaprocessor.py`:

```python
from resources.subtitles import SubtitleProcessor

class MediaProcessor:
    def __init__(self, settings, logger=None):
        # ... existing init ...
        self.subtitles = SubtitleProcessor(self)
```

Then replace all internal calls:
- `self.scanForExternalSubs(...)` → `self.subtitles.scanForExternalSubs(...)`
- `self.downloadSubtitles(...)` → `self.subtitles.downloadSubtitles(...)`
- `self.burnSubtitleFilter(...)` → `self.subtitles.burnSubtitleFilter(...)`
- `self.ripSubs(...)` → `self.subtitles.ripSubs(...)`
- `self.syncExternalSub(...)` → `self.subtitles.syncExternalSub(...)`
- `self.processExternalSub(...)` → `self.subtitles.processExternalSub(...)`

Keep the original methods as thin delegators if external code calls them directly, or remove them if they're only called internally.

- [x] **Step 3: Write basic tests for SubtitleProcessor**

Create `tests/test_subtitles.py`:

```python
"""Tests for resources/subtitles.py SubtitleProcessor."""
import os
import pytest
from unittest.mock import patch, MagicMock
from resources.subtitles import SubtitleProcessor


class TestProcessExternalSub:
    def test_extracts_language_from_filename(self):
        """movie.en.srt should be detected as English."""
        mp = MagicMock()
        mp.settings.sdl = 'eng'
        sp = SubtitleProcessor(mp)
        # Create mock MediaInfo with subtitle stream
        sub_info = MagicMock()
        sub_stream = MagicMock()
        sub_stream.metadata = {}
        sub_stream.disposition = {}
        sub_info.subtitle = sub_stream
        sub_info.path = '/path/to/movie.en.srt'
        result = sp.processExternalSub(sub_info, '/path/to/movie.mkv')
        # Verify language was extracted
        assert result is not None

    def test_detects_forced_disposition(self):
        """movie.en.forced.srt should have forced disposition."""
        mp = MagicMock()
        mp.settings.sdl = 'eng'
        sp = SubtitleProcessor(mp)
        sub_info = MagicMock()
        sub_stream = MagicMock()
        sub_stream.metadata = {}
        sub_stream.disposition = {}
        sub_info.subtitle = sub_stream
        sub_info.path = '/path/to/movie.en.forced.srt'
        result = sp.processExternalSub(sub_info, '/path/to/movie.mkv')
        assert result is not None
```

- [x] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "Extract SubtitleProcessor from mediaprocessor.py"
```

---

## Verification

After all tasks, run the full test suite:

```bash
python -m pytest tests/ -v --tb=short
```

Expected: All tests pass. The codebase should be noticeably smaller (rough estimates):
- ~210 lines removed from autoprocess/ (dead modules)
- ~50 lines removed from Py2 compat code
- ~130 lines removed from postSonarr/postRadarr dedup
- ~200+ lines removed from GPU codec dedup
- ~30 lines removed from downloader dedup
- ~400 lines moved from mediaprocessor.py to subtitles.py
- ~200+ lines of new test coverage added
