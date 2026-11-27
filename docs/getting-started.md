# Getting Started

This guide is the fastest path from a fresh checkout to a working conversion or daemon setup.

It focuses on the minimum you need to understand to get a successful first run. For full option reference, see:

- [Configuration](configuration.md)
- [Daemon Mode](daemon.md)
- [Docker Quick Start](docker-quickstart.md)
- [Hardware Acceleration](hardware-acceleration.md)

## Requirements

- Python 3.12 or newer (`mise install` provides 3.12)
- FFmpeg and FFprobe (system install, or set `daemon.ffmpeg-dir` / `base.converter.ffmpeg`)
- Python packages: `pyproject.toml` declares them. `mise run setup:deps` creates `venv/` and installs
  `setup/requirements.txt`; without mise, run `pip install -r setup/requirements.txt` inside your own virtualenv.

Optional extras (`pip install ".[extra]"`, or `mise run setup:deps:all` for the dev, postgres and openvino extras):

| Extra | Adds |
| ---------- | ------------------------------------------------------------------------ |
| `postgres` | `psycopg2-binary` for PostgreSQL job persistence and cluster mode |
| `openvino` | OpenVINO runtime for the analyzer |
| `dev` | pytest, ruff, pre-commit, and other lint/test tooling (`setup:deps:dev`) |

`setup/requirements.txt` mirrors the base dependencies plus the `postgres` extra, and a test keeps it in sync with
`pyproject.toml`.

## What SMA-NG Does

At a high level, SMA-NG:

1. probes the input with FFprobe
2. decides whether streams can be copied or must be transcoded
3. runs FFmpeg with your selected software or hardware encoder
4. optionally tags the output with TMDB metadata
5. optionally moves, copies, renames, and post-processes the result

You can use it in two ways:

- `manual.py` for one-off or batch conversions from the CLI
- `daemon.py` for webhook-driven and always-on operation

## Choose a Starting Path

Use `manual.py` first if:

- you want to validate FFmpeg and codec behavior quickly
- you are still tuning `sma-ng.yml`
- you want the smallest possible first success

Use `daemon.py` first if:

- you already know the paths and config layout you want
- you are wiring this into Sonarr, Radarr, or download clients
- you want a dashboard and persistent job queue immediately

Use Docker if:

- you want fast deployment and repeatability
- your host already has the media mounts arranged
- you prefer containerized FFmpeg/Python dependencies

## Before You Start

Make sure these questions are answered:

- where will your media files live?
- will output overwrite in place, copy elsewhere, or move elsewhere?
- do you want software encoding or GPU encoding?
- are you doing one-off conversions only, or daemon/webhook automation?
- do Sonarr/Radarr/Plex need to be notified after processing?

If you do not know the answers yet, start with:

- software encoding
- in-place output
- `manual.py`
- tagging enabled
- post-processing disabled

## Quality Profiles

`mise run config:generate` copies `setup/sma-ng.yml.sample` to `config/sma-ng.yml`. The file has one `base:` block
with the defaults and three named profiles that override it:

| Profile | Video | Audio |
| ------- | ----------------------------------------------- | -------------------------------- |
| base | HEVC (`h265`), no bitrate cap | AC3, 128 kbps per channel |
| `rq` | HEVC, capped at 8000 kbps | AC3, then AAC |
| `lq` | H.264, capped at 3000 kbps, `fast` preset | AAC, stereo |
| `hq` | HEVC, capped at 18000 kbps, `slow` preset, one job at a time | E-AC3, then AC3, up to 8 channels |

Use the `lq` profile for bandwidth-limited destinations. Route files to it through `daemon.routing` in `sma-ng.yml`,
or choose it for a single run with `--profile lq`:

```yaml
daemon:
  routing:
    - match: /mnt/unionfs/Media/TV
      profile: rq
    - match: /mnt/unionfs/Media/Mobile
      profile: lq
```

## Quick Start

### With mise (recommended)

[mise](https://mise.jdx.dev/) is a dev-tool manager and task runner. Install it once with the one-liner below,
then use `mise run <task>` anywhere in the project. See the
[mise installation docs](https://mise.jdx.dev/getting-started.html) for alternative methods (Homebrew, package
managers, Windows).

```bash
curl https://mise.run | sh
```

Then clone and set up the project:

```bash
git clone https://github.com/newdave/sma-ng && cd sma-ng

# Install Python 3.12, create venv, install dependencies
mise install
mise run setup:deps

# Generate config (auto-detects GPU)
mise run config:generate

# Test a conversion
mise run media:convert -- /path/to/file.mkv

# Start the daemon (bind address, port and workers come from daemon: in sma-ng.yml)
mise run daemon:start
```

What this gives you:

- Python installed via `mise`
- project virtualenv created
- dependencies installed
- config generated with GPU auto-detection
- a working CLI and daemon entrypoint

Useful follow-up commands:

```bash
mise run dev:precommit
mise run config:gpu
mise run test:openapi
mise run media:preview -- /path/to/file.mkv
mise run media:codecs
```

### Without mise

```bash
git clone https://github.com/newdave/sma-ng && cd sma-ng
python3 -m venv venv && source venv/bin/activate
pip install -r setup/requirements.txt

# Create the config from the sample, with GPU auto-detection
./scripts/generate-configs.sh

# Or copy the sample and edit it manually
cp setup/sma-ng.yml.sample config/sma-ng.yml
$EDITOR config/sma-ng.yml

# Test a conversion
python manual.py -i /path/to/file.mkv -a

# Start the daemon
python daemon.py --host 0.0.0.0 --port 8585
```

If FFmpeg is not on `PATH`, either:

- install it system-wide, or
- point `base.converter.ffmpeg` and `base.converter.ffprobe` in `config/sma-ng.yml` (or `daemon.ffmpeg-dir`) to the
  correct binaries

### With Docker Compose

If you want the fastest daemon setup path, see [Docker Quick Start](docker-quickstart.md).

---

## First-Time Verification

Before you start tuning quality settings, verify the basics.

### 1. Confirm FFmpeg is available

```bash
ffmpeg -version
ffprobe -version
```

If either command fails, SMA-NG will not be able to process media until you fix the binary path.

### 2. Confirm the project installs cleanly

```bash
python manual.py -cl
```

This should print the supported codec list.

### 3. Preview options without converting

```bash
python manual.py -i /path/to/file.mkv -oo
```

This is the safest first functional test because it exercises:

- config loading
- source probing
- stream selection logic
- encoder selection
- optional analyzer recommendations (when `base.analyzer.enabled` is true)

without writing output.

If you enable the analyzer, the preview JSON includes an `analyzer` section showing any bounded recommendations. For
OpenVINO, you can target `CPU`, `GPU`, `NPU`, or composite selectors such as `AUTO:NPU,CPU`. See
[Configuration](configuration.md) for the analyzer settings.

### 4. Test a real conversion

```bash
python manual.py -i /path/to/file.mkv -a
```

Start with a small file so failures are cheap to diagnose.

### 5. If using GPU acceleration, verify it explicitly

Run:

```bash
mise run config:gpu
```

This runs the detection script. Compare the result with your chosen `base.video.gpu` and `base.video.codec` settings.
If GPU detection or encoder availability is uncertain, switch to software first (leave `gpu` empty) and get a clean
baseline run before you troubleshoot hardware acceleration.

---

## Generated Config Files

The normal bootstrap path generates these files:

| File | Purpose |
| ------------------- | ----------------------------------------------------- |
| `config/sma-ng.yml` | Main conversion config, profiles, and daemon settings |

`config:generate` skips the file if it already exists; delete it first to regenerate. That one file is all you need.
Docker deployments can additionally use `config/daemon.env`, copied from `setup/daemon.env.sample`. Compose loads
it for container-level settings only, such as `SMA_WORKERS`, `RENDER_GID` and `LIBVA_DRIVER_NAME`.

Use the `daemon:` block in `config/sma-ng.yml` when routing different paths (`daemon.routing`), enabling
PostgreSQL-backed clustered mode (`daemon.db-url`), rewriting paths (`daemon.path-rewrites`), or scheduling
directory scans (`daemon.scan-paths`).

## Minimum Config to Review

`sma-ng.yml` has four top-level buckets: `daemon:`, `base:` (defaults for every conversion), `profiles:` (named
overlays that deep-merge onto `base:`), and `services:` (named Sonarr/Radarr/Plex/... instances). Keys are
kebab-case. Even if you auto-generate the config, review these `base:` blocks before production use.

### `base.converter`

Check:

- `ffmpeg`
- `ffprobe`
- `output-format`
- `copy-to`
- `move-to`
- `delete-original` and `recycle-bin`
- `post-process`

This section determines where the file goes and whether the original is retained.

### `base.video`

Check:

- `codec`
- `gpu`
- `max-bitrate`
- `crf-profiles`
- `preset`
- `global-quality`

This section determines whether you are doing a software encode, QSV, VAAPI, NVENC or VideoToolbox, and which
quality and bitrate model is used.

### `base.analyzer`

Review this section only after the baseline conversion path already works.

Check:

- `enabled`
- `backend`
- `device`
- `max-frames`
- `target-width`
- the `allow-*` toggles

Start with `enabled: false`, get a clean preview/conversion first, then enable it once the rest of the pipeline is stable.

### `base.audio`

Check:

- `codec`
- `languages`
- `default-language`
- `copy-original`

This section determines which tracks are kept and how they are transcoded.

### `base.subtitle`

Check:

- `codec`
- `languages`
- `burn-subtitles`
- `embed-subs`
- subtitle download settings (`subliminal`) if you use them

### `base.metadata`

Check:

- tagging enabled/disabled
- language for metadata

### `services.*`

These are named instances (for example `services.sonarr.main` with `url` and `apikey`) for Sonarr, Radarr, Plex,
Emby, Jellyfin and Autoscan. Only configure them once the core conversion path already works. See
[Integrations](integrations.md).

Do not debug media-manager callbacks and FFmpeg problems at the same time if you can avoid it.

## Recommended First Conversion Workflow

This is the safest progression:

1. Generate config.
2. Run `manual.py -cl`.
3. Run `manual.py -oo` on a known-good sample file.
4. Run one real conversion with `manual.py -a`.
5. Inspect the output file manually.
6. Only then enable `move-to`, `copy-to`, deletions, or post-process scripts.

For the first real conversion, it is often useful to temporarily avoid destructive file operations:

```bash
python manual.py -i /path/to/file.mkv -a -nm -nc -nd
```

That disables:

- move
- copy
- delete original

and keeps debugging focused on conversion behavior.

## CLI Usage (manual.py)

```bash
# Auto-tag from filename
python manual.py -i /path/to/file.mkv -a

# Specify TMDB ID (movie)
python manual.py -i /path/to/movie.mkv -tmdb 603

# TV episode with TVDB ID
python manual.py -i /path/to/episode.mkv -tvdb 73871 -s 3 -e 10

# Batch process a directory
python manual.py -i /path/to/directory/ -a

# Preview conversion options (no conversion)
python manual.py -i /path/to/file.mkv -oo

# List supported codecs
python manual.py -cl

# Use a named profile from the config file
python manual.py -i /path/to/file.mkv -a -c config/sma-ng.yml --profile rq

# Force the file to be processed even if it already matches the target format
# (note: the video stream may still be COPIED if its codec is already accepted)
python manual.py -i /path/to/file.mp4 -a -fc

# Force the video stream to be re-encoded rather than copied — rebuilds the
# GOP/keyframe grid. Works on a whole directory tree; recurses automatically.
python manual.py -i "/path/to/Show Name" -a --tv -fr

# Convert without tagging
python manual.py -i /path/to/file.mkv -a -nt

# Tag only (no conversion); also strips any embedded cover art
python manual.py -i /path/to/file.mp4 -to

# Skip file operations (no move, no copy, no delete)
python manual.py -i /path/to/file.mkv -a -nm -nc -nd
```

After a conversion, `manual.py` asks the Sonarr or Radarr instance that matches the output file's directory (through
`daemon.routing`) to rescan and import the file, and refreshes Plex, Emby, Jellyfin and Autoscan when they are
configured. See [Integrations](integrations.md).

### Safe First Commands

Use these as your first few runs:

```bash
# Show codec support
python manual.py -cl

# Preview without writing output
python manual.py -i /path/to/file.mkv -oo

# Convert without destructive file operations
python manual.py -i /path/to/file.mkv -a -nm -nc -nd

# Use a named profile from the config (here the lower-bitrate one)
python manual.py -i /path/to/file.mkv -a -p lq
```

### All Flags

| Flag | Long | Description |
| ------- | ------------------------- | ------------------------------------------ |
| `-i` | `--input` | Input file or directory |
| `-c` | `--config` | Alternate config file |
| `-p` | `--profile` | Apply a named profile from the config; overrides the profile that `daemon.routing` would pick |
| `-a` | `--auto` | Auto mode (no prompts, guesses metadata) |
| `-s` | `--season` | Season number |
| `-e` | `--episode` | Episode number (repeat for a multi-episode file, for example `-e 1 -e 2`) |
| `-tvdb` | `--tvdbid` | TVDB ID |
| `-imdb` | `--imdbid` | IMDB ID |
| `-tmdb` | `--tmdbid` | TMDB ID |
| `-nm` | `--nomove` | Disable `move-to` and the move out of `output-directory` |
| `-nc` | `--nocopy` | Disable `copy-to` |
| `-nd` | `--nodelete` | Disable original file deletion |
| `-nt` | `--notag` | Disable metadata tagging |
| `-to` | `--tagonly` | Tag only, no conversion |
| `-np` | `--nopost` | Disable post-process scripts |
| `-pr` | `--preserverelative` | Preserve relative directory structure |
| `-pse` | `--processsameextensions` | Reprocess files already in target format |
| `-fc` | `--forceconvert` | Force conversion + process-same-extensions |
| `-fr` | `--forcereencode` | Re-encode the video even when its codec is already accepted; also enables process-same-extensions |
| `-m` | `--moveto` | Override move-to path |
| | `--tv` | Make guessit treat the input as a TV episode |
| | `--movie` | Make guessit treat the input as a movie |
| `-oo` | `--optionsonly` | Show conversion options, don't convert |
| `-cl` | `--codeclist` | List all supported codecs |
| `-o` | `--original` | Specify original filename for guessing |
| `-ms` | `--minsize` | Minimum file size in MB |
| `-pa` | `--processedarchive` | Path to a processed-files archive (JSON); listed files are skipped |
| | `--job-id` | Daemon job id (set by the daemon worker) |
| | `--audit` | Audit the input path, print findings |
| | `--recycle-failed` | With `--audit`: move media that fails ffprobe to the configured recycle-bin |
| | `--repair-hybrid` | Repair stretched (hybrid-aspect) MP4s: `remux` (default, lossless aspect fix) or `reencode` (rebuild at true dims); with `--audit`, remuxes hybrid findings during the audit |
| | `--version` | Print version and exit |

`-nm` and `-m` cannot be combined, and neither can `-nt` and `-to`; `--tv` and `--movie` are mutually exclusive.

### repair.py

`repair.py` fixes stream-level damage by stream copy and never transcodes. It takes one or more files or directories:

```bash
python repair.py -n /path/to/library       # dry run
python repair.py /path/to/file.mp4         # repair in place
```

| Flag | Long | Description |
| ---- | ------------- | ---------------------------------------------------------- |
| `-n` | `--dry-run` | Report what would be repaired; change nothing |
| `-c` | `--config` | Alternate `sma-ng.yml` path (disables routing) |
| | `--no-plex` | Skip the Plex refresh after repairs |
| `-v` | `--verbose` | Enable debug logging |

See [Troubleshooting](troubleshooting.md#repairing-damaged-media-without-transcoding) for what it fixes.

---

## External Assets

### Cover Art

Place a `jpg` or `png` image in the same directory as the input video with the same base name to use it as cover art instead of the TMDB poster.

### External Subtitles

Place a `.srt` file in the same directory as the input, named with the same base name plus the language code:

```text
input:    The.Matrix.1999.mkv
subtitle: The.Matrix.1999.eng.srt
```

Language rules from `sma-ng.yml` apply: subtitles in languages that are not allowed are ignored.

---

## First Daemon Setup

Once `manual.py` works, moving to daemon mode is straightforward.

### Minimal daemon start

```bash
python daemon.py --host 0.0.0.0 --port 8585
```

### Better production-style start

```bash
python daemon.py \
  --host 0.0.0.0 \
  --port 8585 \
  --workers 2 \
  --api-key YOUR_SECRET_KEY \
  --daemon-config config/sma-ng.yml \
  --logs-dir logs
```

Each daemon setting resolves in this order: command-line flag, then `daemon:` in `sma-ng.yml`, then the default.
Without `--host`, the command line binds to `127.0.0.1`; the sample config binds to `0.0.0.0`. See
[Daemon Mode](daemon.md) for all flags.

### Health check

```bash
curl http://localhost:8585/health
```

### Submit a first job

```bash
curl -X POST http://localhost:8585/webhook/generic \
  -H "X-API-Key: YOUR_SECRET_KEY" \
  -d "/path/to/file.mkv"
```

The path must lie under a configured media root (a `daemon.routing` match, scan path or path rewrite), or the daemon
answers `403`. See [Troubleshooting](troubleshooting.md).

### Open the dashboard

```text
http://localhost:8585/dashboard
```

If daemon mode is your real target, still prove that `manual.py` works first. The daemon ultimately runs the same conversion pipeline, so CLI validation removes a lot of ambiguity.

## Suggested Early Config Progression

Do not turn everything on at once. A clean progression is:

1. basic conversion only
2. metadata tagging
3. copy/move/delete behavior
4. Sonarr/Radarr rescan integration
5. Plex refresh
6. daemon path routing
7. PostgreSQL clustering
8. post-process scripts

That ordering narrows failures much faster.

## Common First-Time Problems

### FFmpeg or FFprobe not found

Fix:

- install them on `PATH`, or
- set explicit `ffmpeg` / `ffprobe` paths in `base.converter` (or `daemon.ffmpeg-dir`)

### GPU encoder selected but unavailable

Fix:

- verify host/device access
- confirm the encoder exists in `ffmpeg -encoders`
- temporarily switch to software encoding

### File probes but no output is written

Usually caused by:

- unsupported encoder selection
- invalid destination path
- file-operation settings (`move-to`, `copy-to`, delete behavior)
- permission problems on the output path

SMA-NG never overwrites an existing file. If the output name is taken, the new file is numbered (`name.2.mp4`).

### Sonarr/Radarr/Plex behavior is confusing

Disable those integrations until the standalone conversion path is proven.

### Wrong config chosen in daemon mode

Check:

- `daemon.routing` rules (`match` / `profile` / `services`; longest matching prefix wins)
- `daemon.path-rewrites`
- actual container/host-visible path values

## What to Read Next

After you have a first successful run:

- read [Configuration](configuration.md) to tune codecs, languages, and file operations
- read [Hardware Acceleration](hardware-acceleration.md) if you want QSV, VAAPI, NVENC, or VideoToolbox
- read [Daemon Mode](daemon.md) if this will run continuously
- read [Integrations](integrations.md) when wiring Sonarr, Radarr, Plex, or download clients
- read [Cluster Operations](cluster-operations.md#deployment-patterns) if you are planning multiple nodes or daemons

## Post-Process Scripts

Set `post-process: true` under `base.converter`, then create a `post_process/` directory in the SMA-NG install
directory (next to `manual.py`; it is not shipped in the repository) and place executable hook scripts in it. If the
directory is absent, no hooks run. Scripts run after each conversion completes, in sorted filename order, and
`wait-post-process: true` makes SMA-NG wait for each one to finish. A file is skipped (never run as a hook) when it:

- starts with `_` (private helpers, for example `_helpers.py`)
- has the extension `.txt`, `.log`, `.pyc` or `.md`, or is named `resources` or `.DS_Store`
- is a directory

Put shared helper code in `_`-prefixed files. Hooks receive:

| Variable | Description |
| -------------- | ----------------------------------------------------------------------------------------------------------------- |
| `SMA_FILES` | JSON array of output files. First entry is the primary file; additional entries are copies created by `copy-to` |
| `SMA_TMDBID` | TMDB ID of the processed file |
| `SMA_SEASON` | Season number (TV only) |
| `SMA_EPISODE` | Episode number (TV only). For a multi-episode file, the first episode |
| `SMA_EPISODES` | Comma-separated list of every episode number (TV only, for example `1,2`; a single episode yields just that one) |

`setup/post_process/` is different: it holds sample download-client
and media-manager submitter scripts (`*_post_process.sh`) that POST `{path, args}` to the daemon's
`/webhook/generic` endpoint. They are not conversion hooks. See [Integrations](integrations.md).

---

## Supported Codecs

Run `python manual.py -cl` for the full list, including the subtitle codecs (`mov_text`, `srt`, `ass`, `webvtt`,
and others). Key codecs:

### Video

| SMA-NG Name | FFmpeg Encoder | Notes |
| --------------- | ------------------ | ----------------- |
| `h264` | libx264 | Software H.264 |
| `h265` / `hevc` | libx265 | Software HEVC |
| `av1` | libaom-av1 | Software AV1 |
| `svtav1` | libsvtav1 | SVT-AV1 |
| `vp9` | libvpx-vp9 | Software VP9 |
| `h264qsv` | h264_qsv | Intel QSV H.264 |
| `h265qsv` | hevc_qsv | Intel QSV HEVC |
| `av1qsv` | av1_qsv | Intel QSV AV1 |
| `vp9qsv` | vp9_qsv | Intel QSV VP9 |
| `h264vaapi` | h264_vaapi | VAAPI H.264 |
| `h265vaapi` | hevc_vaapi | VAAPI HEVC |
| `av1vaapi` | av1_vaapi | VAAPI AV1 |
| `h264_nvenc` | h264_nvenc | NVIDIA H.264 |
| `h265_nvenc` | hevc_nvenc | NVIDIA HEVC |
| `av1nvenc` | av1_nvenc | NVIDIA AV1 |
| `h264_videotoolbox` | h264_videotoolbox | Apple H.264 |
| `h265_videotoolbox` | hevc_videotoolbox | Apple HEVC |

With `base.video.gpu` set, the generic names `h264`, `h265` and `av1` map to the encoder of that backend. See
[Hardware Acceleration](hardware-acceleration.md).

### Audio

| SMA-NG Name | FFmpeg Encoder |
| ----------- | -------------- |
| `aac` | aac |
| `libfdk_aac` | libfdk_aac |
| `ac3` | ac3 |
| `eac3` | eac3 |
| `dts` | dts |
| `flac` | flac |
| `opus` | libopus |
| `mp3` | libmp3lame |
| `vorbis` | libvorbis |
| `truehd` | truehd |
