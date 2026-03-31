# Post-Processing Scripts

This directory contains two types of scripts:

1. **Integration scripts** — submit jobs to the SMA-NG daemon from external applications (Sonarr, Radarr, download clients)
2. **Custom post-process scripts** — run automatically by SMA-NG after each conversion completes

---

## Integration Scripts

These scripts are called by external applications and submit conversion jobs to the SMA-NG daemon webhook.

Configure the daemon connection in all scripts via environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `SMA_DAEMON_HOST` | `127.0.0.1` | Daemon hostname or IP |
| `SMA_DAEMON_PORT` | `8585` | Daemon port |
| `SMA_DAEMON_API_KEY` | _(none)_ | API key if auth is enabled |

### Media Managers

#### `sonarr_post_process.sh`

Configure in Sonarr: Settings → Connect → Custom Script

Set the script path and enable **On Import** and **On Upgrade** triggers. No arguments needed — Sonarr passes all required data via environment variables.

#### `radarr_post_process.sh`

Configure in Radarr: Settings → Connect → Custom Script

Set the script path and enable **On Import** and **On Upgrade** triggers. No arguments needed — Radarr passes all required data via environment variables.

### Download Clients

#### `nzbget_post_process.sh`

Configure in NZBGet: Settings → Extension Scripts

Copy the script to your NZBGet scripts directory and enable it. Configurable options (set in NZBGet's script settings UI):

| Option | Default | Description |
| --- | --- | --- |
| `SHOULDCONVERT` | `true` | Enable/disable conversion |
| `BYPASS_CAT` | `bypass` | Category prefix to skip |

#### `sabnzbd_post_process.sh`

Configure in SABnzbd: Config → Categories → Script (per category), or Config → Switches → Post-Processing Script

SABnzbd passes arguments positionally: `$1`=path, `$5`=category, `$7`=status.

Set bypass categories via `SMA_BYPASS_CATS` (comma-separated, e.g. `bypass,skip`).

#### `deluge_post_process.sh`

Configure in Deluge: Preferences → Execute → Event: TorrentComplete

Command:

```text
/path/to/deluge_post_process.sh "%T" "%N" "%L" "%I"
```

Arguments: torrent name, download path, label, info hash.

Set bypass labels via `SMA_BYPASS_LABELS` (comma-separated).

#### `qbittorrent_post_process.sh`

Configure in qBittorrent: Tools → Options → Downloads → Run external program on torrent completion:

```text
/path/to/qbittorrent_post_process.sh "%L" "%T" "%R" "%F" "%N" "%I"
```

Arguments: category, tracker, root path, content path, torrent name, info hash.

Set bypass labels via `SMA_BYPASS_LABELS` (comma-separated).

#### `utorrent_post_process.sh`

Configure in uTorrent: Preferences → Advanced → Run program → On torrent completion:

```text
/path/to/utorrent_post_process.sh "%L" "%T" "%D" "%K" "%F" "%I" "%N"
```

Arguments: label, tracker, directory, kind (single/multi), filename, info hash, torrent name.

Set bypass labels via `SMA_BYPASS_LABELS` (comma-separated).

---

## Custom Post-Process Scripts

SMA-NG can run custom scripts after each conversion completes. Place scripts in this directory and set `post-process = True` in `autoProcess.ini`. Scripts in the `resources/` subdirectory are excluded from auto-execution.

The following environment variables are available to custom scripts:

| Variable | Description |
| --- | --- |
| `SMA_FILES` | JSON array of output files. First entry is the primary file; additional entries are copies created by `copy-to` |
| `SMA_TMDBID` | TMDB ID of the processed file |
| `SMA_SEASON` | Season number (TV only) |
| `SMA_EPISODE` | Episode number (TV only) |
