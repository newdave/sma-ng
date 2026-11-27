# Migrating from sickbeard_mp4_automator to SMA-NG

SMA-NG is a quasi-fork of [sickbeard_mp4_automator](https://github.com/mdhiggins/sickbeard_mp4_automator) (the
original SMA). Both convert media to MP4 with FFmpeg and tag it with metadata, but SMA-NG is a different program:
it runs as an HTTP daemon with a job queue, reads a YAML configuration file, and is triggered by webhooks or shell
scripts instead of Python post-processing scripts.

There is no automatic converter for old configuration. SMA-NG does not read `autoProcess.ini` or any other INI file:
pointing it at one fails at startup with an error that refers to the
[Configuration Reference](configuration.md). Build a new `sma-ng.yml` from the sample and port your settings by hand.

## What changed

| Area | Original SMA | SMA-NG |
| ------------------- | ------------------------------------------------ | -------------------------------------------------------- |
| Python | Any Python 3 | Python 3.12 or later |
| Execution model | Synchronous script run by Sonarr or Radarr | Asynchronous daemon with a persistent job queue |
| Configuration | `autoProcess.ini` | `config/sma-ng.yml` (YAML, kebab-case keys) |
| Sonarr and Radarr | Custom Script (`postSonarr.py`, `postRadarr.py`) | Native webhook endpoints or the `triggers/` shell scripts |
| Download clients | Python post-process scripts | Shell scripts under `triggers/` that call the daemon |
| Several instances | One `[Sonarr]` and one `[Radarr]` section | Named instances under `services.sonarr` and `services.radarr` |
| Sickbeard, Sickrage | Supported | Not supported |
| Docker | `sonarr-sma` and `radarr-sma` images | One standalone image, `ghcr.io/newdave/sma-ng` |

## Port the configuration

1. Copy the sample and edit the copy:

   ```bash
   cp setup/sma-ng.yml.sample config/sma-ng.yml
   ```

2. Move each old INI section into the matching bucket. The full key list is in the
   [Configuration Reference](configuration.md).

   | Old INI section | New location |
   | ---------------------------------------- | ---------------------------------------------------------------- |
   | `[Converter]` | `base.converter` |
   | `[Video]`, `[HDR]` | `base.video`, `base.hdr` |
   | `[Audio]`, `[Audio.Sorting]`, `[Universal Audio]` | `base.audio`, `base.audio.sorting`, `base.audio.universal` |
   | `[Subtitle]`, `[Subtitle.Sorting]` | `base.subtitle`, `base.subtitle.sorting` |
   | `[Metadata]`, `[Permissions]` | `base.metadata`, `base.permissions` |
   | `[Sonarr]`, `[Radarr]`, `[Plex]` | `services.sonarr.<name>`, `services.radarr.<name>`, `services.plex.<name>` |
   | `[SABNZBD]`, `[Deluge]`, `[qBittorrent]`, `[uTorrent]` | Removed. Configure the scripts under `triggers/` instead |
   | `[Sickbeard]`, `[Sickrage]` | Removed. Neither is supported |

3. Rename keys that changed meaning:

   | Old setting | SMA-NG setting |
   | -------------------------------------------- | -------------------------------------------------------------------------------- |
   | `video-codec` | `base.video.codec` |
   | `crf = 21` | `base.video.crf-profiles: '0:21:0:0'` (see the tiered format in the reference) |
   | `prefer-more-channels`, `default-more-channels` | Put `channels.d` in `base.audio.sorting.sorting` and `default-sorting` |
   | `copy-original-before` | Reorder the entries in `base.audio.sorting` |
   | `[Universal Audio]` always on | Set `base.audio.universal.enabled: true` (it is off by default) |
   | Several Sonarr or Radarr sections | One named instance each, selected by a `daemon.routing` rule |

4. Check the result. `mise run config:validate` reports unknown keys, typos and encoder flags in the wrong block:

   ```bash
   mise run config:validate
   ```

For hardware encoding, run `mise run config:gpu` to detect the available encoders and set `base.video.gpu`
(see [Hardware Acceleration](hardware-acceleration.md)).

## Route paths to profiles and instances

The original SMA chose a Sonarr or Radarr section by the longest matching `path` prefix. SMA-NG makes that explicit
with `daemon.routing` rules. Each rule maps a directory to an optional profile and to the service instances to notify.
The longest matching prefix wins.

```yaml
daemon:
  routing:
    - match: /mnt/media/TV
      profile: rq
      services: [sonarr.main]
    - match: /mnt/media/TV/Kids
      profile: lq
      services: [sonarr.kids]
services:
  sonarr:
    main:
      url: http://sonarr.example.com:8989
      apikey: abc123
    kids:
      url: http://sonarr-kids.example.com:8989
      apikey: def456
```

See [Path routing](daemon.md#path-routing) for the matching rules.

## Switch the triggers

Start the daemon before any trigger fires. It keeps running and queues each request:

```bash
python daemon.py --host 0.0.0.0 --port 8585 --workers 4 --api-key YOUR_SECRET_KEY
```

Sonarr and Radarr: replace the Custom Script connection with a Webhook connection (Settings, Connect, Add, Webhook)
that posts to `http://<daemon-host>:8585/webhook/sonarr` or `/webhook/radarr` on Download and Upgrade events. If you
prefer a Custom Script connection, point it at `triggers/media_managers/sonarr.sh` or `radarr.sh`.

Download clients: replace the Python script with the matching shell script. The scripts submit a job and return
immediately.

| Download client | Original script | SMA-NG script |
| --------------- | --------------------------- | ---------------------------------- |
| SABnzbd | `SABPostProcess.py` | `triggers/usenet/sabnzbd.sh` |
| NZBGet | `NZBGetPostProcess.py` | `triggers/usenet/nzbget.sh` |
| qBittorrent | `qBittorrentPostProcess.py` | `triggers/torrents/qbittorrent.sh` |
| Deluge | `delugePostProcess.py` | `triggers/torrents/deluge.sh` |
| uTorrent | `uTorrentPostProcess.py` | `triggers/torrents/utorrent.sh` |

The scripts need the daemon URL and API key. See [Integrations](integrations.md) for their setup.

Finally, send a test job and confirm that it appears at `http://<daemon-host>:8585/jobs`.

## Upgrading an older sma-ng.yml

Some older SMA-NG settings are still accepted and rewritten at load time, so an existing `sma-ng.yml` keeps working:

| Older setting | What happens at load |
| ------------------------------------------------------------ | ------------------------------------------------------------------ |
| `base.converter.software-fallback: true` or `false` | Becomes `fallback-policy: aggressive` or `hw_only`, with a warning |
| QSV-only or VAAPI-only flags in `base.video.codec-parameters` | Moved into `base.video.qsv` or `base.video.vaapi` `codec-parameters` |
| Same flags in `base.hdr.codec-parameters` | Moved into `base.hdr.qsv` or `base.hdr.vaapi` `codec-parameters` |

Rewrite these in the current form when convenient. Two kinds of file are rejected instead of migrated: INI files and
the old flat YAML layout (top-level `converter:`, `video:` and so on). Wrap the old sections in the four buckets
(`daemon`, `base`, `profiles`, `services`) instead.

## Further reading

- [Getting Started](getting-started.md): installation and first-run walkthrough
- [Configuration Reference](configuration.md): every `sma-ng.yml` key
- [Daemon Mode](daemon.md): daemon options, API and clustering
- [Integrations](integrations.md): Sonarr, Radarr and download client setup
- [Hardware Acceleration](hardware-acceleration.md): GPU configuration
