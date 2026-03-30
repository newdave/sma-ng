#!/opt/sma/venv/bin/python3
"""
SMA-NG qBittorrent Post-Processing Script

Submits conversion job to daemon via webhook on torrent completion.
Handles pre/post actions (pause, resume, delete) on the torrent.

Args: "%L" "%T" "%R" "%F" "%N" "%I"
      Category, Tracker, RootPath, ContentPath, TorrentName, InfoHash
"""

import sys

import resources.webhook_client as webhook
from resources.log import getLogger
from resources.readsettings import ReadSettings

log = getLogger("qBittorrentPostProcess")
log.info("qBittorrent post-processing started.")

try:
    settings = ReadSettings()

    if len(sys.argv) < 6:
        log.error("Not enough arguments. Expected: category tracker root_path [content_path] torrent_name info_hash")
        sys.exit(1)

    # Parse arguments (6 or 7 args depending on qBittorrent version)
    if len(sys.argv) >= 7:
        label = sys.argv[1].lower().strip()
        root_path = sys.argv[3]
        content_path = sys.argv[4]
        torrent_name = sys.argv[5]
        info_hash = sys.argv[6]
    else:
        label = sys.argv[1].lower().strip()
        root_path = sys.argv[3]
        content_path = sys.argv[3]
        torrent_name = sys.argv[4]
        info_hash = sys.argv[5]

    log.info("Label: %s" % label)
    log.info("Content path: %s" % content_path)
    log.info("Torrent: %s (%s)" % (torrent_name, info_hash))

    if webhook.check_bypass(settings.qBittorrent.get("bypass", []), label):
        log.info("Bypass label matched, skipping.")
        sys.exit(0)

    # Connect to qBittorrent for pre/post actions
    qbt_client = None
    actionbefore = settings.qBittorrent.get("actionbefore", "").lower()
    actionafter = settings.qBittorrent.get("actionafter", "").lower()

    if actionbefore or actionafter:
        try:
            from qbittorrent import Client

            host = settings.qBittorrent.get("host", "localhost")
            port = settings.qBittorrent.get("port", 8080)
            ssl = settings.qBittorrent.get("ssl", False)
            protocol = "https://" if ssl else "http://"
            qbt_client = Client("%s%s:%s/" % (protocol, host, port))
            qbt_client.login(settings.qBittorrent.get("user", ""), settings.qBittorrent.get("pass", ""))
        except:
            log.exception("Failed to connect to qBittorrent WebUI.")

    # Pre-action
    if qbt_client and actionbefore == "pause":
        try:
            qbt_client.pause(info_hash)
            log.info("Paused torrent %s." % info_hash)
        except:
            log.exception("Failed to pause torrent.")

    # Submit files to daemon
    if not webhook.submit_path(content_path, logger=log):
        sys.exit(1)

    # Post-action
    if qbt_client and actionafter:
        try:
            if actionafter == "resume":
                qbt_client.resume(info_hash)
                log.info("Resumed torrent %s." % info_hash)
            elif actionafter == "delete":
                qbt_client.delete(info_hash)
                log.info("Deleted torrent %s (kept data)." % info_hash)
            elif actionafter == "deletedata":
                qbt_client.delete_permanently(info_hash)
                log.info("Deleted torrent %s and data." % info_hash)
        except:
            log.exception("Failed to perform post-action on torrent.")

except:
    log.exception("Error in qBittorrent post-processing.")
    sys.exit(1)
