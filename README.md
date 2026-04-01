SMA-NG Conversion/Tagging Automation Script
==============

![SMA-NG](logo.png)

**Automatically converts media files to a standardized format and tags them with metadata from TMDB.**

SMA-NG is a quasi-fork of [Sickbeard MP4 Automator](https://github.com/mdhiggins/sickbeard_mp4_automator). It builds on that project's foundation with a restructured codebase, daemon mode, hardware-accelerated encoding, and deployment tooling.

Works on Windows, macOS, and Linux.

Integration
--------------

### Media Managers

- [Sonarr](#sonarr-setup)
- [Radarr](#radarr-setup)

### Downloaders

- [NZBGet](#nzbget-setup)
- [SABNZBD](#sabnzbd-setup)
- [Deluge Daemon](#deluge-daemon-setup)
- [uTorrent](#utorrent-setup)
- [qBittorrent](#qbittorrent-setup)

Dependencies
--------------

- [Python 3.12+](https://www.python.org/)
- [FFmpeg](https://ffmpeg.org/)

Default Settings
--------------

- Container: MP4
- Video: H265 (with hardware acceleration if detected)
- Audio: AAC 2.0 + AC3 passthrough when source has >2 channels
- Subtitles: mov_text

Getting Started
--------------

```bash
# Clone the repo
git clone https://github.com/newdave/sma-ng.git
cd sma-ng

# Create config with auto-detected GPU (see GPU Configuration below)
make config

# Install dependencies
make install

# Test a conversion
python manual.py -i "/path/to/file.mkv" -a
```

GPU Configuration
--------------

SMA-NG supports hardware-accelerated video encoding via FFmpeg. The `gpu` setting in `[Video]` selects the encoder backend:

| Value | Hardware | Supported codecs |
| --- | --- | --- |
| `nvenc` | NVIDIA GPU | h264, h265, av1 |
| `qsv` | Intel Quick Sync | h264, h265, av1, vp9 |
| `vaapi` | AMD / generic VA-API (Linux) | h264, h265, av1 |
| `videotoolbox` | Apple Silicon / macOS | h264, h265 |
| *(empty)* | Software (CPU) | all codecs |

When `gpu` is set, SMA-NG selects the appropriate FFmpeg encoder for your chosen `codec`. For example:

```text
gpu = nvenc, codec = h265   →  h265_nvenc  (+ software fallback)
gpu = qsv,   codec = h264   →  h264_qsv    (+ software fallback)
gpu = vaapi, codec = h265   →  h265_vaapi  (+ software fallback)
gpu = ,      codec = h265   →  libx265 (software only)
```

**Auto-detection:** `make config` (and `mise run config`) detect your hardware automatically:

- macOS (Apple Silicon) → `videotoolbox`
- NVIDIA GPU (detected via `nvidia-smi`) → `nvenc`
- Intel iGPU (detected via `/sys/module/i915` or `vainfo`) → `qsv`
- Generic VA-API device (`/dev/dri/renderD128`) → `vaapi`
- Fallback → software

To override detection:

```bash
make config GPU=nvenc      # force NVIDIA
make config GPU=vaapi      # force VA-API
make config GPU=           # force software encoding
make detect-gpu            # show what would be detected without creating config
```

In `config/autoProcess.ini`:

```ini
[Video]
gpu = nvenc          # or qsv, vaapi, videotoolbox, or leave empty for software
codec = h265
```

General Configuration
--------------

1. Run `make config` to generate `config/autoProcess.ini` with auto-detected GPU settings
2. Edit `config/autoProcess.ini` to set your desired output format, codecs, and paths
3. Run `manual.py` to test a conversion (see [Manual Script Usage](#manual-script-usage))
4. Configure direct integration using the instructions below

The config file location defaults to `config/autoProcess.ini`. Override it with the `SMA_CONFIG` environment variable.

Sonarr Setup
--------------

1. Set your Sonarr API credentials in the `[Sonarr]` section of `autoProcess.ini`
2. In Sonarr, go to **Settings > Connect > +** and add a **Custom Script** connection:
   - `Name`: postSonarr
   - `On Grab`: No
   - `On Download` / `On Import`: Yes
   - `On Upgrade`: Yes
   - `On Rename`: No
   - `Path`: Full path to your Python executable
   - `Arguments`: Full path to `triggers/media_managers/sonarr.sh`
3. **Optional:** To convert before Sonarr imports the file, disable *Completed Download Handling* in Sonarr and configure your download client to use the included script. The script triggers a path re-scan so conversion completes before Sonarr moves the file.

Radarr Setup
--------------

1. Set your Radarr API credentials in the `[Radarr]` section of `autoProcess.ini`
2. In Radarr, go to **Settings > Connect > +** and add a **Custom Script** connection:
   - `Name`: postRadarr
   - `On Grab`: No
   - `On Download` / `On Import`: Yes
   - `On Upgrade`: Yes
   - `On Rename`: No
   - `Path`: Full path to your Python executable
   - `Arguments`: Full path to `triggers/media_managers/radarr.sh`
3. **Optional:** Same note as Sonarr above — disable *Completed Download Handling* if you want conversion to happen before Radarr imports.

NZBGet Setup
--------------

1. Copy `triggers/usenet/NZBGetPostProcess.py` to NZBGet's scripts folder (default `/opt/nzbget/scripts/`)
2. Restart NZBGet
3. In NZBGet's WebUI (`localhost:6789`), go to **Settings > NZBGETPOSTPROCESS** and configure:
   - `MP4_FOLDER`: Full path to the SMA-NG directory (with trailing slash)
   - `SHOULDCONVERT`: `True` to convert before passing to destination
   - `SONARR_CAT`: Category name for Sonarr downloads (default: `sonarr`)
   - `BYPASS_CAT`: Category for downloads to convert but not forward (default: `bypass`)
4. In NZBGet categories, set `PostScript` to `NZBGetPostProcess.py`

*Not required if using Completed Download Handling with Sonarr/Radarr.*

SABNZBD Setup
--------------

1. Set your SABnzbd settings in the `[SABNZBD]` section of `autoProcess.ini`
2. Point SABnzbd's script directory to the SMA-NG root
3. Configure categories under **Settings > Categories**:
   - Set `name` to match the categories in `autoProcess.ini` (defaults: `tv`, `movies`, `bypass`)
   - Set the script to `SABPostProcess.py` for each category
4. Ensure your media manager is assigning the matching category label

*Not required if using Completed Download Handling with Sonarr/Radarr.*

Deluge Daemon Setup
--------------

1. Create a daemon user in your Deluge config:
   - Open the `auth` file in your Deluge config directory
     - Windows: `%appdata%\Roaming\Deluge`
     - Linux: `/var/lib/deluge/.config/deluge/`
   - Add a line: `username:password:10`
2. Restart `deluged` (not the Deluge GUI)
3. In the Deluge WebUI (default port `8112`), enable the **Execute** plugin
   - Add an event for *Torrent Complete*
   - Set path to `triggers/torrents/delugePostProcess.py`
4. Set your Deluge credentials in the `[Deluge]` section of `autoProcess.ini`

*Not required if using Completed Download Handling with Sonarr/Radarr.*

uTorrent Setup
--------------

1. In uTorrent, go to **Options > Preferences > Advanced > Run Program**
2. Set path to `triggers/torrents/uTorrentPostProcess.py` with arguments: `%L %T %D %K %F %I %N`
3. Set your uTorrent settings in the `[uTorrent]` section of `autoProcess.ini`

*Not required if using Completed Download Handling with Sonarr/Radarr.*

qBittorrent Setup
--------------

1. In qBittorrent, go to **Tools > Options > Run external program on torrent completion**
2. Set path to `triggers/torrents/qBittorrentPostProcess.py` with arguments: `"%L" "%T" "%R" "%F" "%N" "%I"`
3. Set your qBittorrent settings in the `[qBittorrent]` section of `autoProcess.ini`

*Not required if using Completed Download Handling with Sonarr/Radarr.*

Plex Notification
--------------

Send a Plex library refresh as the final step after conversion completes. This prevents Plex from scanning a file while it is still being processed.

1. Disable automatic library refreshing in Plex: **Settings > Server > Library** — disable *Update my library automatically* and *Update my library periodically*
2. Set your Plex token and host in the `[Plex]` section of `autoProcess.ini`

If you have *Secure Connections* set to *Required* in Plex, add your SMA-NG server's local IP to the allowed list under **Plex Server Settings > Network > Advanced**, or change *Secure Connections* to *Preferred*.

Daemon Mode
--------------

The daemon runs an HTTP server that accepts webhook requests to trigger conversions.

```bash
# Start with defaults (127.0.0.1:8585, 1 worker)
python daemon.py

# Listen on all interfaces with multiple workers
python daemon.py --host 0.0.0.0 --port 8585 --workers 4

# With API key authentication
python daemon.py --api-key YOUR_SECRET_KEY
# Or via environment variable:
SMA_DAEMON_API_KEY=YOUR_SECRET_KEY python daemon.py
```

See [CLAUDE.md](CLAUDE.md) for full daemon documentation including endpoints, authentication, path-based config, concurrency, SQLite/PostgreSQL persistence, and cluster mode.

Manual Script Usage
--------------

```bash
# Auto-tag from filename
python manual.py -i "/path/to/Futurama.S03E10.mkv" -a

# Specify TMDB ID (movie)
python manual.py -i "/path/to/The.Matrix.1999.mkv" -tmdb 603

# Specify TVDB ID (TV episode)
python manual.py -i "/path/to/episode.mkv" -tvdb 73871 -s 3 -e 10

# Preview FFmpeg options without converting
python manual.py -i "/path/to/file.mkv" -oo

# List supported codecs
python manual.py -cl

# Process a directory in batch mode
python manual.py -i "/path/to/directory" -a
```

**All options:**

```text
usage: manual.py [-h] [-i INPUT] [--config CONFIG] [-a] [-tmdb TMDBID]
                 [-tvdb TVDBID] [-imdb IMDBID] [-s SEASON] [-e EPISODE]
                 [-nm] [-m MOVETO] [-nc] [-nt] [-to] [-nd] [-pr] [-pse]
                 [-fc] [-oo] [-cl] [-pa ARCHIVE]

  -i, --input           Source file or directory
  --config              Alternate config file path
  -a, --auto            Auto mode: guess metadata from filename (no prompts)
  -tmdb TMDBID          TMDB ID for the media
  -tvdb TVDBID          TVDB ID for the media
  -imdb IMDBID          IMDB ID for the media
  -s, --season          Season number
  -e, --episode         Episode number
  -nm, --nomove         Disable custom file moving (output_dir / move-to)
  -m, --moveto          Override move-to destination
  -nc, --nocopy         Disable custom file copying
  -nt, --notag          Disable tagging
  -to, --tagonly        Tag without converting
  -nd, --nodelete       Disable deletion of original files
  -pr, --preserverelative  Preserve relative directory structure when copying/moving
  -pse, --processsameextensions  Allow reprocessing files with same extension
  -fc, --forceconvert   Force conversion regardless of extension
  -oo, --optionsonly    Show FFmpeg options only, do not convert
  -cl, --codeclist      List supported codecs and their FFmpeg encoders
  -pa, --processedarchive  Path to processed-files archive (skip already-done files)
```

External Cover Art
--------------

Place a `jpg` or `png` image file in the same directory as the input video with the same base name before processing to use it as cover art instead of the TMDB poster.

Import External Subtitles
--------------

Place a `.srt` file in the same directory as the input, named with the same base name plus the language code:

```text
input:    The.Matrix.1999.mkv
subtitle: The.Matrix.1999.eng.srt
```

Language rules from `autoProcess.ini` apply — subtitles for non-whitelisted languages are ignored.

Post Process Scripts
--------------

Custom scripts in `post_process/` run after conversion. See [post_process/post_process.md](post_process/post_process.md) for the API.

Deployment with mise
--------------

SMA-NG includes a deployment system built on [mise](https://mise.jdx.dev/) for syncing code and managing configuration on remote Linux hosts. This is the recommended approach for managing multiple servers.

### Prerequisites

```bash
# Install mise (if not already installed)
make install-mise
```

### Configuration

Copy the sample local config and fill in your details:

```bash
cp setup/.local.ini.sample setup/.local.ini
```

`setup/.local.ini` is gitignored and never committed. It controls:

- **`[deploy]`** — SSH targets, remote directory, SSH key path
- **`[daemon]`** — API key, database URL, FFmpeg directory
- **`[Sonarr]`, `[Radarr]`, `[Plex]`, etc.** — service credentials stamped into remote config files
- **Per-host overrides** — any `[deploy]` key can be overridden for a specific host

Example:

```ini
[deploy]
DEPLOY_HOSTS = user@media1.example.com user@media2.example.com
DEPLOY_DIR   = /opt/sma
SSH_KEY      = ~/.ssh/id_ed25519_sma

[daemon]
api_key  = your_secret_key
db_url   =            # blank = SQLite; set to postgresql://... for multi-node

[Sonarr]
host     = sonarr.example.com
port     = 443
ssl      = true
apikey   = abc123

[Radarr]
host     = radarr.example.com
port     = 443
ssl      = true
apikey   = def456

[user@media1.example.com]
DEPLOY_DIR  = /opt/sma
REMOTE_MAKE = install restart
FFMPEG_DIR  = /opt/ffmpeg/bin
```

### Deployment Workflow

```bash
# 1. First-time: generate SSH key, configure ssh-config, install prerequisites,
#    create DEPLOY_DIR, sync code, and verify ffmpeg on each host
mise run deploy:setup

# 2. Sync code and run post-sync make target (default: install)
mise run deploy:run

# 3. Push config files to remote hosts
#    Creates missing configs from samples, merges new sample keys into existing
#    configs, stamps in service credentials, sets ffmpeg paths, and deploys
#    post-process scripts with correct credentials
mise run deploy:config

# 4. Restart the daemon on all hosts
mise run deploy:restart

# 5. Run an arbitrary make target on all hosts without syncing
REMOTE_MAKE=install mise run deploy:remote-make

# 6. Verify setup/.local.ini and DEPLOY_HOSTS
mise run deploy:check
```

### What deploy:config does

`mise run deploy:config` is the main config management command. For each remote host it:

1. Detects the GPU type remotely and sets `gpu =` in the generated config
2. Creates any missing config files (`autoProcess.ini`, `daemon.json`, `daemon.env`) from the bundled samples
3. Merges new keys from updated samples into existing configs (non-destructive — existing values are preserved)
4. Stamps service credentials from `setup/.local.ini` (`[Sonarr]`, `[Radarr]`, `[Plex]`, etc.) into all `*.ini` files
5. Sets `ffmpeg` and `ffprobe` paths in every `.ini` based on `FFMPEG_DIR`
6. Stamps daemon credentials (`api_key`, `db_url`, `ffmpeg_dir`) into `daemon.json` and `daemon.env`
7. Deploys post-process scripts (`plex.py`, `jellyfin.py`, `emby.py`) with the correct interpreter shebang and credentials filled in

### Systemd Service

The daemon can be installed as a systemd service managed by the deployment tools:

```bash
# Install and enable the service on the local host
make systemd-install

# Or deploy via mise (runs systemd-install on all remote hosts)
mise run deploy:run
```

The service unit is at `setup/sma-daemon.service`. It loads `config/daemon.env` for environment overrides and uses `setup/sma-daemon-start.sh` as the entrypoint (which selects SQLite vs. PostgreSQL based on `SMA_DAEMON_DB_URL`).

Key service settings:

- Runs as `nobody` by default — override with `SERVICE_USER=myuser make systemd-install`
- `TimeoutStopSec=infinity` — waits for active conversions to complete on shutdown
- `ReadWritePaths=/opt/sma/config /opt/sma/logs /transcodes /mnt` — adjust if your paths differ

Docker
--------------

A Docker image is published to GHCR on every release.

```bash
# Pull and run
docker run --rm -p 8585:8585 \
  -v /your/config:/config \
  -v /your/logs:/logs \
  ghcr.io/newdave/sma-ng:latest

# Build locally
make docker-build

# Run locally-built image
make docker-run

# Smoke-test locally-built image
make docker-smoke
```

Tags: `latest`, `1`, `1.2`, `1.2.3` (semver) and `main` (rolling build from main branch).

Credits
--------------

- [FFmpeg](http://www.ffmpeg.org/)
- [Python](http://www.python.org/)
- [SABnzbd](http://sabnzbd.org/)
- [NZBGet](https://nzbget.net/)
- [Deluge](https://www.deluge-torrent.org/)
- [qBittorrent](https://www.qbittorrent.org/)
- [tmdbsimple](https://github.com/celiao/tmdbsimple)
- [mutagen](https://github.com/quodlibet/mutagen)
- [qtfaststart](http://github.com/danielgtaylor/qtfaststart)
- [guessit](http://github.com/wackou/guessit)
- [subliminal](http://github.com/Diaoul/subliminal)
- [cleanit](https://github.com/ratoaq2/cleanit)
- [Sonarr](http://sonarr.tv/)
- [Radarr](http://radarr.video/)
- [mise](https://mise.jdx.dev/)
