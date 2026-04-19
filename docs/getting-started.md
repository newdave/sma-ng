# Getting Started

## Requirements

- Python 3.12+
- FFmpeg (system install or via `ffmpeg_dir` in daemon.json)
- Python packages: `pip install -r setup/requirements.txt`

Optional:
- qBittorrent integration: `pip install -r setup/requirements-qbittorrent.txt`
- Deluge integration: `pip install -r setup/requirements-deluge.txt`

## Quality Profiles

`mise run config` always generates three config files:

| File | Profile | Video | Audio |
| --- | --- | --- | --- |
| `config/autoProcess.ini` | Regular Quality (default) | 3 Mbit/s 1080p · 20 Mbit/s 4K | EAC3, 128 kbps/ch |
| `config/autoProcess.rq.ini` | Regular Quality (explicit) | same as above | same as above |
| `config/autoProcess.lq.ini` | Lower Quality | 2 Mbit/s capped at 1080p (4K downscaled) | AAC, 96 kbps/ch |

Use `config/autoProcess.lq.ini` for bandwidth-limited destinations (mobile devices, remote access). Route files to it via `path_configs` in `daemon.json`:

```json
{
  "path_configs": [
    {"path": "/mnt/media/TV", "config": "config/autoProcess.rq.ini"},
    {"path": "/mnt/media/Mobile", "config": "config/autoProcess.lq.ini"}
  ]
}
```

## Quick Start

### With mise (recommended)

[mise](https://mise.jdx.dev/) is a dev-tool manager. Install it once, then:

```bash
git clone https://github.com/newdave/sma-ng && cd sma-ng

# Install Python 3.12, create venv, install dependencies
mise install
mise run install

# Generate config (auto-detects GPU)
mise run config

# Test a conversion
mise run convert -- /path/to/file.mkv

# Start the daemon
mise run daemon
```

### Without mise

```bash
git clone https://github.com/newdave/sma-ng && cd sma-ng
python3 -m venv venv && source venv/bin/activate
pip install -r setup/requirements.txt

# Generate config with auto GPU detection
make config

# Or copy sample and edit manually
cp setup/autoProcess.ini.sample config/autoProcess.ini
$EDITOR config/autoProcess.ini

# Test a conversion
python manual.py -i /path/to/file.mkv -a

# Start the daemon
python daemon.py --host 0.0.0.0 --port 8585
```

---

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

# Use alternate config file
python manual.py -i /path/to/file.mkv -a -c config/autoProcess.ini-movies4k

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

### All Flags

| Flag | Long | Description |
| --- | --- | --- |
| `-i` | `--input` | Input file or directory |
| `-c` | `--config` | Alternate config file |
| `-a` | `--auto` | Auto mode (no prompts, guesses metadata) |
| `-s` | `--season` | Season number |
| `-e` | `--episode` | Episode number |
| `-tvdb` | `--tvdbid` | TVDB ID |
| `-imdb` | `--imdbid` | IMDB ID |
| `-tmdb` | `--tmdbid` | TMDB ID |
| `-nm` | `--nomove` | Disable move-to and output-directory |
| `-nc` | `--nocopy` | Disable copy-to |
| `-nd` | `--nodelete` | Disable original file deletion |
| `-nt` | `--notag` | Disable metadata tagging |
| `-to` | `--tagonly` | Tag only, no conversion |
| `-np` | `--nopost` | Disable post-process scripts |
| `-pr` | `--preserverelative` | Preserve relative directory structure |
| `-pse` | `--processsameextensions` | Reprocess files already in target format |
| `-fc` | `--forceconvert` | Force conversion + process-same-extensions |
| `-m` | `--moveto` | Override move-to path |
| `-oo` | `--optionsonly` | Show conversion options, don't convert |
| `-cl` | `--codeclist` | List all supported codecs |
| `-o` | `--original` | Specify original filename for guessing |
| `-ms` | `--minsize` | Minimum file size in MB |
| `-pa` | `--processedarchive` | Path to processed files archive JSON |

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

Language rules from `autoProcess.ini` apply — subtitles for non-whitelisted languages are ignored.

---

## Post-Process Scripts

Place executable scripts in `post_process/`. They receive:

| Variable | Description |
| --- | --- |
| `SMA_FILES` | JSON array of output file paths |
| `SMA_TMDBID` | TMDB ID |
| `SMA_SEASON` | Season number (TV only) |
| `SMA_EPISODE` | Episode number (TV only) |

See `setup/post_process/` for examples (Plex, Emby, Jellyfin, iTunes).

---

## Supported Codecs

Run `python manual.py -cl` for the full list. Key codecs:

### Video

| SMA-NG Name | FFmpeg Encoder | Notes |
| --- | --- | --- |
| `h264` | libx264 | Software H.264 |
| `h265` / `hevc` | libx265 | Software HEVC |
| `h264qsv` | h264_qsv | Intel QSV H.264 |
| `h265qsv` | hevc_qsv | Intel QSV HEVC |
| `h264vaapi` | h264_vaapi | Intel VAAPI H.264 |
| `h265vaapi` | hevc_vaapi | Intel VAAPI HEVC |
| `av1qsv` | av1_qsv | Intel QSV AV1 |
| `av1vaapi` | av1_vaapi | Intel VAAPI AV1 |
| `h265_nvenc` | hevc_nvenc | NVIDIA HEVC |
| `av1` | libaom-av1 | Software AV1 |
| `svtav1` | libsvtav1 | SVT-AV1 |
| `vp9` | libvpx-vp9 | Software VP9 |

### Audio

| SMA-NG Name | FFmpeg Encoder |
| --- | --- |
| `aac` | aac / libfdk_aac |
| `ac3` | ac3 |
| `eac3` | eac3 |
| `flac` | flac |
| `opus` | libopus |
| `mp3` | libmp3lame |
| `dts` | dca |
| `truehd` | truehd |
