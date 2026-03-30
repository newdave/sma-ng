#!/opt/sma/venv/bin/python3
"""
SMA-NG Deluge Post-Processing Script

Submits conversion job to daemon via webhook on torrent completion.
Optionally removes torrent from Deluge after submission.
"""

import os
import sys

import resources.webhook_client as webhook
from resources.log import getLogger
from resources.readsettings import ReadSettings

log = getLogger("DelugePostProcess")
log.info("Deluge post-processing started.")

try:
    settings = ReadSettings()

    if len(sys.argv) < 4:
        log.error("Not enough arguments. Usage: delugePostProcess.py <torrent_id> <torrent_name> <path> [forcepath]")
        sys.exit(1)

    torrent_id = sys.argv[1]
    torrent_name = sys.argv[2]
    path = sys.argv[3]

    log.info("Torrent: %s" % torrent_name)
    log.info("Path: %s" % path)

    # Check label via Deluge RPC (if available and not forcepath mode)
    label = ""
    forcepath = len(sys.argv) > 4 and sys.argv[4] == "forcepath"

    if not forcepath:
        try:
            from deluge_client import DelugeRPCClient

            deluge_host = settings.deluge.get("host", "localhost")
            deluge_port = int(settings.deluge.get("port", 58846))
            deluge_user = settings.deluge.get("user", "")
            deluge_pass = settings.deluge.get("pass", "")

            client = DelugeRPCClient(deluge_host, deluge_port, deluge_user, deluge_pass)
            client.connect()
            torrent_data = client.call("core.get_torrent_status", torrent_id, ["label", "save_path", "files"])
            label = (torrent_data.get(b"label") or torrent_data.get("label", b"")).decode("utf-8").lower()
            log.info("Torrent label: %s" % label)
        except:
            log.exception("Could not connect to Deluge RPC, proceeding without label check.")

    if webhook.check_bypass(settings.deluge.get("bypass", []), label):
        log.info("Bypass label matched, skipping conversion.")
        sys.exit(0)

    # Submit files to daemon — try path first, then path + torrent_name
    if not webhook.submit_path(path, logger=log):
        combined = os.path.join(path, torrent_name)
        if not webhook.submit_path(combined, logger=log):
            log.error("Path does not exist: %s" % path)
            sys.exit(1)

    # Remove torrent if configured
    if not forcepath and settings.deluge.get("remove", False):
        try:
            client.call("core.remove_torrent", torrent_id, False)
            log.info("Removed torrent %s from Deluge." % torrent_id)
        except:
            log.exception("Failed to remove torrent from Deluge.")

except:
    log.exception("Error in Deluge post-processing.")
    sys.exit(1)
