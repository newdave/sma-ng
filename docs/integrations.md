# Integrations

## Media Managers

### Sonarr

Two integration methods are available:

**Native webhook (recommended):** Sonarr posts directly to SMA-NG's built-in endpoint. No external script required.

1. In Sonarr: **Settings → Connect → Add Webhook**
   - On Download/Import: Yes, On Upgrade: Yes
   - URL: `http://<sma-host>:8585/webhook/sonarr`
   - Method: POST
   - If API key is configured, add header `X-API-Key: YOUR_SECRET_KEY`

SMA-NG extracts `episodeFile.path`, `series.tvdbId`, and episode numbers from the Sonarr payload and queues the job automatically. Test events (from the **Test** button) return a 200 OK without queuing anything.

**Custom script:** Suitable when SMA-NG runs locally alongside Sonarr (not in a container).

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

Two integration methods are available:

**Native webhook (recommended):** Radarr posts directly to SMA-NG's built-in endpoint.

1. In Radarr: **Settings → Connect → Add Webhook**
   - On Download/Import: Yes, On Upgrade: Yes
   - URL: `http://<sma-host>:8585/webhook/radarr`
   - Method: POST
   - If API key is configured, add header `X-API-Key: YOUR_SECRET_KEY`

SMA-NG extracts `movieFile.path` and `movie.tmdbId` (or `imdbId` as fallback) and queues the job. Test events return 200 OK without queuing.

**Custom script:** Suitable when SMA-NG runs locally alongside Radarr.

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

The sample config also includes commented examples directly under the primary `[Sonarr]` and `[Radarr]` sections so the intended grouping is visible in-place.

### Plex

Configure `[Plex]` section. SMA-NG refreshes the matching library section after conversion. Use `path-mapping` if Plex sees files at different mount points.

1. Disable automatic library scanning in Plex to prevent Plex from scanning files mid-conversion
2. Connect directly to the Plex server using its local hostname or IP on port `32400` (or your custom port) and set `token` plus `refresh = true` in `[Plex]`

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
