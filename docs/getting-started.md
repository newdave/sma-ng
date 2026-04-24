# Getting Started

This guide is the fastest path from a fresh checkout to a working conversion or daemon setup.

It focuses on the minimum you need to understand to get a successful first run. For full option reference, see:

- [Configuration](configuration.md)
- [Daemon Mode](daemon.md)
- [Docker Compose Quick Start](docker-compose-quickstart.md)
- [Hardware Acceleration](hardware-acceleration.md)

## Requirements

- Python 3.12+
- FFmpeg (system install or via `ffmpeg_dir` in `Daemon:` section in `sma-ng.yml`)
- Python packages: `pip install -r setup/requirements.txt`

Optional:

- qBittorrent integration: `pip install -r setup/requirements-qbittorrent.txt`
- Deluge integration: `pip install -r setup/requirements-deluge.txt`
- OpenVINO analyzer runtime: `pip install -r setup/requirements-openvino.txt`

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

`mise run config:generate` and `make config` create one base YAML config with named quality profiles:

| Profile | Video                                    | Audio             |
| ------- | ---------------------------------------- | ----------------- |
| base    | Regular-quality defaults                 | EAC3, 128 kbps/ch |
| `rq`    | Explicit regular-quality override        | EAC3/AAC          |
| `lq`    | Lower bitrate, stereo-focused override   | AAC, stereo       |

Use the `lq` profile for bandwidth-limited destinations. Route files to it via `Daemon.path_configs` in `sma-ng.yml`:

```yaml
Daemon:
  path_configs:
    - path: /mnt/media/TV
      profile: rq
    - path: /mnt/media/Mobile
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

# Start the daemon
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
mise run dev:check
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

# Generate the same config set as `mise run config:generate`
make config

# Or copy sample and edit manually
cp setup/sma-ng.yml.sample config/sma-ng.yml
$EDITOR config/sma-ng.yml

# Test a conversion
python manual.py -i /path/to/file.mkv -a

# Start the daemon
python daemon.py --host 0.0.0.0 --port 8585
```

If FFmpeg is not on `PATH`, either:

- install it system-wide, or
- point `ffmpeg` and `ffprobe` in `config/sma-ng.yml` to the correct binaries

### With Docker Compose

If you want the fastest daemon setup path, see [Docker Compose Quick Start](docker-compose-quickstart.md).

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
- optional analyzer recommendations (when `[Analyzer]` is enabled)

without writing output.

If you enable the analyzer, the preview JSON includes an `analyzer` section showing any bounded recommendations. For OpenVINO, you can target `CPU`, `GPU`, `NPU`, or composite selectors such as `AUTO:NPU,CPU`. Today the backend primarily validates runtime/device availability and planner wiring; richer model-backed inference is reserved for later expansion.

### 4. Test a real conversion

```bash
python manual.py -i /path/to/file.mkv -a
```

Start with a small file so failures are cheap to diagnose.

### 5. If using GPU acceleration, verify it explicitly

Run:

```bash
make detect-gpu
```

or:

```bash
mise run config:gpu
```

Both commands use the same detection script. Then compare the result with your chosen `gpu =` and codec settings. If GPU detection or encoder availability is uncertain, switch to software first and get a clean baseline run before troubleshooting hardware acceleration.

---

## Generated Config Files

The normal bootstrap path generates these files:

| File                        | Purpose                                         |
| --------------------------- | ----------------------------------------------- |
| `config/sma-ng.yml` | Main conversion config, profiles, and daemon settings |
| `config/daemon.env`       | Optional daemon environment variables                |

You can operate successfully with only:

- `config/sma-ng.yml`

Use the `Daemon:` section in `config/sma-ng.yml` when routing different paths, enabling PostgreSQL-backed clustered mode, using path rewrites, or scheduling directory scans.

## Minimum Config to Review

Even if you auto-generate the config, review these sections before production use.

### `[Converter]`

Check:

- `ffmpeg`
- `ffprobe`
- `output-format`
- `copy-to`
- `move-to`
- `delete-original`
- `post-process`

This section determines where the file goes and whether the original is retained.

### `[Video]`

Check:

- `codec`
- `gpu`
- `max-bitrate`
- `crf-profiles`
- `preset`

This section determines whether you are doing software encode, QSV, VAAPI, or NVENC, and what quality/bitrate model is used.

### `[Analyzer]`

Review this section only after the baseline conversion path already works.

Check:

- `enabled`
- `backend`
- `device`
- `max-frames`
- `target-width`
- the `allow-*` toggles

Start with `enabled = false`, get a clean preview/conversion first, then enable it once the rest of the pipeline is stable.

### `[Audio]`

Check:

- `codec`
- `languages`
- `default-language`
- `copy-original`

This section determines which tracks are kept and how they are transcoded.

### `[Subtitle]`

Check:

- `codec`
- `languages`
- `burn-subtitles`
- `embed-subs`
- subtitle download settings if you use Subliminal

### `[Metadata]`

Check:

- tagging enabled/disabled
- artwork download behavior
- language for metadata

### `[Sonarr]`, `[Radarr]`, `[Plex]`

Only configure these once the core conversion path already works.

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

# Force re-encode even if format matches
python manual.py -i /path/to/file.mp4 -a -fc

# Convert without tagging
python manual.py -i /path/to/file.mkv -a -nt

# Tag only (no conversion)
python manual.py -i /path/to/file.mp4 -to

# Skip file operations (no move, no copy, no delete)
python manual.py -i /path/to/file.mkv -a -nm -nc -nd
```

After conversion, `manual.py` automatically triggers a rescan on the matching Sonarr/Radarr instance based on the output file's directory path.

### Safe First Commands

Use these as your first few runs:

```bash
# Show codec support
python manual.py -cl

# Preview without writing output
python manual.py -i /path/to/file.mkv -oo

# Convert without destructive file operations
python manual.py -i /path/to/file.mkv -a -nm -nc -nd

# Force software-style baseline testing by using a software codec config
python manual.py -i /path/to/file.mkv -a -c config/sma-ng.yml
```

### All Flags

| Flag    | Long                      | Description                                |
| ------- | ------------------------- | ------------------------------------------ |
| `-i`    | `--input`                 | Input file or directory                    |
| `-c`    | `--config`                | Alternate config file                      |
| `-a`    | `--auto`                  | Auto mode (no prompts, guesses metadata)   |
| `-s`    | `--season`                | Season number                              |
| `-e`    | `--episode`               | Episode number                             |
| `-tvdb` | `--tvdbid`                | TVDB ID                                    |
| `-imdb` | `--imdbid`                | IMDB ID                                    |
| `-tmdb` | `--tmdbid`                | TMDB ID                                    |
| `-nm`   | `--nomove`                | Disable move-to and output-directory       |
| `-nc`   | `--nocopy`                | Disable copy-to                            |
| `-nd`   | `--nodelete`              | Disable original file deletion             |
| `-nt`   | `--notag`                 | Disable metadata tagging                   |
| `-to`   | `--tagonly`               | Tag only, no conversion                    |
| `-np`   | `--nopost`                | Disable post-process scripts               |
| `-pr`   | `--preserverelative`      | Preserve relative directory structure      |
| `-pse`  | `--processsameextensions` | Reprocess files already in target format   |
| `-fc`   | `--forceconvert`          | Force conversion + process-same-extensions |
| `-m`    | `--moveto`                | Override move-to path                      |
| `-oo`   | `--optionsonly`           | Show conversion options, don't convert     |
| `-cl`   | `--codeclist`             | List all supported codecs                  |
| `-o`    | `--original`              | Specify original filename for guessing     |
| `-ms`   | `--minsize`               | Minimum file size in MB                    |
| `-pa`   | `--processedarchive`      | Path to processed files archive JSON       |

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

Language rules from `sma-ng.yml` apply — subtitles for non-whitelisted languages are ignored.

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
- set explicit paths in `[Converter]`

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

### Sonarr/Radarr/Plex behavior is confusing

Disable those integrations until the standalone conversion path is proven.

### Wrong config chosen in daemon mode

Check:

- `default_config`
- `path_configs`
- `path_rewrites`
- actual container/host-visible path values

## What to Read Next

After you have a first successful run:

- read [Configuration](configuration.md) to tune codecs, languages, and file operations
- read [Hardware Acceleration](hardware-acceleration.md) if you want QSV, VAAPI, NVENC, or VideoToolbox
- read [Daemon Mode](daemon.md) if this will run continuously
- read [Integrations](integrations.md) when wiring Sonarr, Radarr, Plex, or download clients
- read [Multi-Instance Deployment](multi-instance-deployment.md) if you are planning multiple nodes or daemons

## Post-Process Scripts

Place executable scripts in `post_process/`. They receive:

| Variable      | Description                     |
| ------------- | ------------------------------- |
| `SMA_FILES`   | JSON array of output file paths |
| `SMA_TMDBID`  | TMDB ID                         |
| `SMA_SEASON`  | Season number (TV only)         |
| `SMA_EPISODE` | Episode number (TV only)        |

See `setup/post_process/` for examples (Plex, Emby, Jellyfin, iTunes).

---

## Supported Codecs

Run `python manual.py -cl` for the full list. Key codecs:

### Video

| SMA-NG Name     | FFmpeg Encoder | Notes             |
| --------------- | -------------- | ----------------- |
| `h264`          | libx264        | Software H.264    |
| `h265` / `hevc` | libx265        | Software HEVC     |
| `h264qsv`       | h264_qsv       | Intel QSV H.264   |
| `h265qsv`       | hevc_qsv       | Intel QSV HEVC    |
| `h264vaapi`     | h264_vaapi     | Intel VAAPI H.264 |
| `h265vaapi`     | hevc_vaapi     | Intel VAAPI HEVC  |
| `av1qsv`        | av1_qsv        | Intel QSV AV1     |
| `av1vaapi`      | av1_vaapi      | Intel VAAPI AV1   |
| `h265_nvenc`    | hevc_nvenc     | NVIDIA HEVC       |
| `av1`           | libaom-av1     | Software AV1      |
| `svtav1`        | libsvtav1      | SVT-AV1           |
| `vp9`           | libvpx-vp9     | Software VP9      |

### Audio

| SMA-NG Name | FFmpeg Encoder   |
| ----------- | ---------------- |
| `aac`       | aac / libfdk_aac |
| `ac3`       | ac3              |
| `eac3`      | eac3             |
| `flac`      | flac             |
| `opus`      | libopus          |
| `mp3`       | libmp3lame       |
| `dts`       | dca              |
| `truehd`    | truehd           |
