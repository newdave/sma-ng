# Triggers

![SMA-NG](../logo.png)

Trigger scripts connect download clients and media managers to the SMA-NG daemon. When a download completes or a file
is imported, the relevant script submits the path to the daemon webhook, and the daemon queues the conversion. The
scripts only submit work and, where noted, wait for it; the daemon does the conversion. For the daemon side of each
integration, see [Integrations](../docs/integrations.md).

## Directory Structure

```text
triggers/
  cli/
    scan.sh         — Manual CLI trigger for a single file or directory
  lib/
    common.sh       — Shared Bash helpers for daemon auth, polling, and payload calls
    json_tools.py   — Shared JSON parser/payload builder used by trigger scripts
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
| `DAEMON_HOST` | `127.0.0.1` | Daemon hostname or IP |
| `DAEMON_PORT` | `8585` | Daemon port |
| `DAEMON_API_KEY` | _(none)_ | API key if authentication is enabled |
| `DAEMON_USERNAME`, `DAEMON_PASSWORD` | _(none)_ | HTTP Basic credentials, used when no API key is set |
| `SMA_MEDIA_EXTS` | `mkv mp4 m4v avi mov wmv ts m2ts mpg mpeg flv webm` | Space-separated extensions treated as media when a script submits a directory |

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

# Override config (the daemon only accepts its own configured sma-ng.yml path)
triggers/cli/scan.sh --wait --config /config/sma-ng.yml /media/4k/film.mkv

# Directory — queues all matching files in the tree
triggers/cli/scan.sh --wait /media/tv/show/season1/
```

**Options:**

| Flag | Description |
| --- | --- |
| `-w`, `--wait` | Block until the job completes (exit 0 on success, non-zero on failure) |
| `-c`, `--config PATH` | Override sma-ng.yml for this job |
| `-a`, `--args ARGS` | Pass arbitrary extra args to manual.py (quoted string) |
| `--tmdb ID` | Shorthand for `--args "-tmdb ID"` |
| `--tvdb ID` | Shorthand for `--args "-tvdb ID"` |
| `-s`, `--season N` | Season number (used with `--tvdb`) |
| `-e`, `--episode N` | Episode number (used with `--tvdb`) |
| `-t`, `--timeout N` | Max seconds to wait (default: 0 = unlimited) |
| `-i`, `--interval N` | Polling interval in seconds (default: 5) |

Without `--wait`, the script prints the job ID and exits immediately, which suits fire-and-forget or scripted batch
submission.

---

## Media Managers

### `media_managers/sonarr.sh`

Configure in Sonarr: Settings → Connect → Custom Script

Enable the **On Import** and **On Upgrade** triggers. The script needs no arguments, because Sonarr passes the event
through environment variables (`sonarr_eventtype`, `sonarr_episodefile_path` and so on). It submits the file to
`/webhook/sonarr` and waits for the job to finish. Optional `POLL_INTERVAL` (default 5) and `TIMEOUT` (default 0,
unlimited) control the wait.

### `media_managers/radarr.sh`

Configure in Radarr: Settings → Connect → Custom Script

Enable the **On Import** and **On Upgrade** triggers. The script needs no arguments, because Radarr passes the event
through environment variables (`radarr_eventtype`, `radarr_moviefile_path` and so on). It submits the file to
`/webhook/radarr` and waits for the job to finish, with the same `POLL_INTERVAL` and `TIMEOUT` options.

---

## Usenet

### `usenet/nzbget.sh`

Configure in NZBGet: Settings → Extension Scripts

Copy the script to your NZBGet scripts directory and enable it. Set these options in NZBGet's script settings UI:

| Option | Default | Description |
| --- | --- | --- |
| `SHOULDCONVERT` | `true` | Enable/disable submission to daemon |
| `BYPASS_CAT` | `bypass` | Category prefix to skip |

The daemon connection variables above are read from the environment of the NZBGet process.

### `usenet/sabnzbd.sh`

Configure in SABnzbd: Config → Categories → Script (per category), or
Config → Switches → Post-Processing Script (global).

SABnzbd passes arguments positionally: `$1` is the job directory, `$5` the category and `$7` the status.

Set the categories to skip with `BYPASS_CATS`, a comma-separated list of prefixes (default `bypass`, for example
`bypass,skip`).

---

## Torrents

All torrent triggers skip torrents whose label starts with one of the comma-separated prefixes in `BYPASS_LABELS`
(default `bypass`).

### `torrents/deluge.sh`

Configure in Deluge: Preferences → Execute → Event: TorrentComplete

```text
/path/to/triggers/torrents/deluge.sh "%T" "%N" "%L" "%I"
```

Arguments: torrent name, download path, label and info hash (the info hash is optional).

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
