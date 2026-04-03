# Integrations

## Media Managers

### Sonarr

1. Set API credentials in `[Sonarr]` section of `autoProcess.ini`
2. In Sonarr: **Settings → Connect → Add Custom Script**
   - On Download/Import: Yes, On Upgrade: Yes
   - Path: `/bin/bash`
   - Arguments: full path to `triggers/media_managers/sonarr.sh`

**Per-instance config override:** Set `SMA_CONFIG` in Sonarr's environment (Settings → General → Environment Variables) to force a specific config file, useful when Sonarr imports to a staging path that doesn't match `path_configs` prefixes in `daemon.json`:

```bash
SMA_CONFIG=/opt/sma/config/autoProcess.tv.ini
```

### Radarr

1. Set API credentials in `[Radarr]` section of `autoProcess.ini`
2. In Radarr: **Settings → Connect → Add Custom Script**
   - On Download/Import: Yes, On Upgrade: Yes
   - Path: `/bin/bash`
   - Arguments: full path to `triggers/media_managers/radarr.sh`

**Per-instance config override:** Same as Sonarr — set `SMA_CONFIG` in Radarr's environment.

### Multiple Instances

Any config section starting with `Sonarr` or `Radarr` is discovered automatically. Each instance needs a `path` field for directory-based matching:

```ini
[Sonarr]
path = /mnt/media/TV
host = sonarr.example.com
apikey = abc123

[Sonarr-Kids]
path = /mnt/media/TV-Kids
host = sonarr-kids.example.com
apikey = def456

[Radarr]
path = /mnt/media/Movies
host = radarr.example.com
apikey = ghi789

[Radarr-4K]
path = /mnt/media/Movies/4K
host = radarr-4k.example.com
apikey = jkl012
```

When `manual.py` processes `/mnt/media/Movies/4K/film.mp4`, it matches `Radarr-4K` (longest prefix) and triggers a rescan on that instance.

### Plex

Configure `[Plex]` section. SMA-NG refreshes the matching library section after conversion. Use `path-mapping` if Plex sees files at different mount points.

1. Disable automatic library scanning in Plex to prevent Plex from scanning files mid-conversion
2. Set host, token, and `refresh = true` in `[Plex]`

---

## Download Clients

All download client integrations use bash scripts in `triggers/` that submit jobs to the daemon via webhook. These are not required if you are using Completed Download Handling with Sonarr/Radarr.

### NZBGet

In **Settings → Extension Scripts**, add `triggers/usenet/nzbget.sh`. Configure categories under the script settings.

### SABnzbd

In **Settings → Folders → Scripts Folder**, point to `triggers/usenet/`. Set `sabnzbd.sh` as the category script. Configure `[SABNZBD]` section in `autoProcess.ini`.

### qBittorrent

In **Tools → Options → Downloads → Run external program on torrent completion**:

```bash
bash /path/to/triggers/torrents/qbittorrent.sh "%L" "%T" "%R" "%F" "%N" "%I"
```

Configure `[qBittorrent]` section with host, credentials, and label mappings.

### Deluge

Enable the **Execute** plugin in Deluge WebUI. Set `triggers/torrents/deluge.sh` as the Torrent Complete handler. Configure `[Deluge]` section with daemon host and credentials.

### uTorrent

In **Options → Preferences → Advanced → Run Program**:

```bash
bash /path/to/triggers/torrents/utorrent.sh %L %T %D %K %F %I %N
```
