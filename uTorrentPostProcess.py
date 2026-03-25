#!/opt/sma/venv/bin/python3
"""
SMA-NG uTorrent Post-Processing Script

Submits conversion job to daemon via webhook on torrent completion.
Optionally performs pre/post actions via uTorrent WebUI.

Args: %L %T %D %K %F %I %N
      Label, Tracker, Directory, single|multi, Filename, InfoHash, Name
"""
import os
import sys
from resources.log import getLogger
from resources.readsettings import ReadSettings
from resources.webhook_client import submit_job

log = getLogger("uTorrentPostProcess")
log.info("uTorrent post-processing started.")

try:
    settings = ReadSettings()

    if len(sys.argv) < 7:
        log.error("Not enough arguments. Expected: label tracker directory kind filename info_hash [name]")
        sys.exit(1)

    label = sys.argv[1].lower().strip()
    path = sys.argv[3]
    kind = sys.argv[4].lower()
    filename = sys.argv[5]
    info_hash = sys.argv[6]
    torrent_name = sys.argv[7] if len(sys.argv) > 7 else info_hash

    log.info("Label: %s" % label)
    log.info("Path: %s" % path)
    log.info("Kind: %s" % kind)
    log.info("Torrent: %s (%s)" % (torrent_name, info_hash))

    # Check bypass
    bypass = settings.uTorrent.get('bypass', '').lower()
    if bypass and label.startswith(bypass):
        log.info("Bypass label matched, skipping.")
        sys.exit(0)

    # WebUI actions
    webui = settings.uTorrent.get('webui', False)
    actionbefore = settings.uTorrent.get('actionbefore', '').lower()
    actionafter = settings.uTorrent.get('actionafter', '').lower()

    def utorrent_action(action_name):
        if not webui or not action_name:
            return
        try:
            import requests
            host = settings.uTorrent.get('host', 'localhost')
            port = settings.uTorrent.get('port', 8080)
            ssl = settings.uTorrent.get('ssl', False)
            protocol = "https://" if ssl else "http://"
            user = settings.uTorrent.get('user', '')
            passwd = settings.uTorrent.get('pass', '')

            base_url = "%s%s:%s/gui/" % (protocol, host, port)
            # Get auth token
            r = requests.get(base_url + "token.html", auth=(user, passwd))
            token = r.text.split("'")[1] if "'" in r.text else ''

            url = "%s?action=%s&hash=%s&token=%s" % (base_url, action_name, info_hash, token)
            requests.get(url, auth=(user, passwd))
            log.info("uTorrent action '%s' sent for %s." % (action_name, info_hash))
        except:
            log.exception("Failed to send uTorrent action '%s'." % action_name)

    # Pre-action
    utorrent_action(actionbefore)

    # Submit files to daemon
    if kind == 'single' and filename:
        filepath = os.path.join(path, filename)
        if os.path.isfile(filepath):
            submit_job(filepath, logger=log)
        else:
            log.error("File does not exist: %s" % filepath)
    elif os.path.isdir(path):
        for root, _, files in os.walk(path):
            for f in files:
                submit_job(os.path.join(root, f), logger=log)
    else:
        log.error("Path does not exist: %s" % path)
        sys.exit(1)

    # Post-action
    utorrent_action(actionafter)

except:
    log.exception("Error in uTorrent post-processing.")
    sys.exit(1)
