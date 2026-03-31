# Triggers

Trigger scripts bridge 3rd-party download clients and media managers to the SMA-NG daemon.
When a download completes or a file is imported, the relevant trigger script submits it to
the daemon webhook for conversion.

## Directory Structure

```text
triggers/
  cli/
    scan.sh         — Manual CLI trigger for a single file or directory
  media_managers/
    sonarr.sh       — Sonarr On Import / On Upgrade
    radarr.sh       — Radarr On Import / On Upgrade
  usenet/
    nzbget.sh       — NZBGet post-processing script
    sabnzbd.sh      — SABnzbd post-processing script
  torrents/
    deluge.sh       — Deluge Execute plugin (TorrentComplete)
    qbittorrent.sh  — qBittorrent run-on-completion
    utorrent.sh     — uTorrent run-on-completion
```

## Common Configuration

All scripts read daemon connection settings from environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `SMA_DAEMON_HOST` | `127.0.0.1` | Daemon hostname or IP |
| `SMA_DAEMON_PORT` | `8585` | Daemon port |
| `SMA_DAEMON_API_KEY` | _(none)_ | API key if authentication is enabled |

---

## CLI

### `cli/scan.sh`

Submit a file or directory for conversion from the command line.

```bash
# Submit and exit immediately (prints job_id to stdout)
triggers/cli/scan.sh /media/movies/film.mkv

# Submit and wait for completion
triggers/cli/scan.sh --wait /media/movies/film.mkv

# Provide TMDB ID
triggers/cli/scan.sh --wait --tmdb 603 /media/movies/film.mkv

# Provide TVDB ID with season/episode
triggers/cli/scan.sh --wait --tvdb 73871 -s 3 -e 10 /media/tv/show/ep.mkv

# Override config
triggers/cli/scan.sh --wait --config /etc/sma/4k.ini /media/4k/film.mkv

# Directory — queues all matching files in the tree
triggers/cli/scan.sh --wait /media/tv/show/season1/
```

**Options:**

| Flag | Description |
| --- | --- |
| `-w`, `--wait` | Block until the job completes (exit 0 on success, non-zero on failure) |
| `-c`, `--config PATH` | Override autoProcess.ini for this job |
| `-a`, `--args ARGS` | Pass arbitrary extra args to manual.py (quoted string) |
| `--tmdb ID` | Shorthand for `--args "-tmdb ID"` |
| `--tvdb ID` | Shorthand for `--args "-tvdb ID"` |
| `-s`, `--season N` | Season number (used with `--tvdb`) |
| `-e`, `--episode N` | Episode number (used with `--tvdb`) |
| `-t`, `--timeout N` | Max seconds to wait (default: 0 = unlimited) |
| `-i`, `--interval N` | Polling interval in seconds (default: 5) |

Without `--wait`, the script prints the job ID and exits immediately, making it
suitable for fire-and-forget or scripted batch submission.

---

## Media Managers

### `media_managers/sonarr.sh`

Configure in Sonarr: Settings → Connect → Custom Script

Enable **On Import** and **On Upgrade** triggers. No arguments needed — Sonarr supplies
all required data via environment variables (`sonarr_eventtype`, `sonarr_episodefile_path`, etc.).

### `media_managers/radarr.sh`

Configure in Radarr: Settings → Connect → Custom Script

Enable **On Import** and **On Upgrade** triggers. No arguments needed — Radarr supplies
all required data via environment variables (`radarr_eventtype`, `radarr_moviefile_path`, etc.).

---

## Usenet

### `usenet/nzbget.sh`

Configure in NZBGet: Settings → Extension Scripts

Copy the script to your NZBGet scripts directory and enable it. Options are set via
NZBGet's script settings UI:

| Option | Default | Description |
| --- | --- | --- |
| `SHOULDCONVERT` | `true` | Enable/disable submission to daemon |
| `BYPASS_CAT` | `bypass` | Category prefix to skip |

### `usenet/sabnzbd.sh`

Configure in SABnzbd: Config → Categories → Script (per category), or
Config → Switches → Post-Processing Script (global).

SABnzbd passes arguments positionally: `$1`=path, `$5`=category, `$7`=status.

Set bypass categories via `SMA_BYPASS_CATS` (comma-separated, e.g. `bypass,skip`).

---

## Torrents

All torrent triggers support bypass labels via `SMA_BYPASS_LABELS` (comma-separated).

### `torrents/deluge.sh`

Configure in Deluge: Preferences → Execute → Event: TorrentComplete

```text
/path/to/triggers/torrents/deluge.sh "%T" "%N" "%L" "%I"
```

Arguments: torrent name, download path, label, info hash.

### `torrents/qbittorrent.sh`

Configure in qBittorrent: Tools → Options → Downloads → Run external program on torrent completion:

```text
/path/to/triggers/torrents/qbittorrent.sh "%L" "%T" "%R" "%F" "%N" "%I"
```

Arguments: category, tracker, root path, content path, torrent name, info hash.

### `torrents/utorrent.sh`

Configure in uTorrent: Preferences → Advanced → Run program → On torrent completion:

```text
/path/to/triggers/torrents/utorrent.sh "%L" "%T" "%D" "%K" "%F" "%I" "%N"
```

Arguments: label, tracker, directory, kind (single/multi), filename, info hash, torrent name.
