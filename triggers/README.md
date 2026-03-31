# Triggers

Trigger scripts bridge 3rd-party download clients and media managers to the SMA-NG daemon.
When a download completes or a file is imported, the relevant trigger script submits it to
the daemon webhook for conversion.

## Directory Structure

```text
triggers/
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
